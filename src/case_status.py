"""Isolated case-status backend for Capability 6 (pull_case_status_into_call).

KEN.pdf marks Gnani Custom Integrations as PARTIAL: the Gnani capability
exists, but *our* endpoint must be built. This module is that endpoint's
data source, kept deliberately separate from any agent state:

  * local mode (default): deterministic demo case store, no network.
  * http mode (GNANI_CASE_STATUS_URL set): calls your AgenticOrg/backend
    case API with a bounded timeout so the external caller is never blocked.

Gnani's documented "Custom Integrations" (docs.gnani.ai/D05_Custom) is an
outbound mechanism where Gnani calls a URL you configure in the dashboard
(method, headers, params/body with {{variables}}, timeout). The HTTP route
exposed by this server at GET /case-status is the endpoint to plug into
that configuration. Gnani does not publish a fixed request/response schema
for it, so this implementation uses a plain JSON checklist contract and
does not claim it as an official Gnani schema.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any

import httpx2

from src.config import Settings, get_settings
from src.gnani_client import HttpRequest, ProviderSender
from src.models import ChecklistItem


class CaseLookupStatus(str, Enum):
    FOUND = "FOUND"
    NOT_FOUND = "NOT_FOUND"
    TIMEOUT = "TIMEOUT"
    ERROR = "ERROR"


@dataclass
class CaseRecord:
    case_id: str
    applicant_id: str
    checklist: list[ChecklistItem]
    applicant_name: str = ""


@dataclass
class CaseLookup:
    status: CaseLookupStatus
    record: CaseRecord | None = None
    source: str = "local"
    message: str = ""
    error_category: str | None = None
    http_status: int | None = None
    fetched_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )


LOCAL_CASES: dict[str, CaseRecord] = {
    "RAAHI-DEMO-001": CaseRecord(
        case_id="RAAHI-DEMO-001",
        applicant_id="APPLICANT-DEMO-001",
        applicant_name="Demo Applicant",
        checklist=[
            ChecklistItem(item="passport", status="verified"),
            ChecklistItem(item="funds_proof", status="pending"),
            ChecklistItem(item="bank_letter", status="pending"),
            ChecklistItem(item="insurance", status="verified"),
        ],
    ),
    "RAAHI-DEMO-002": CaseRecord(
        case_id="RAAHI-DEMO-002",
        applicant_id="APPLICANT-DEMO-002",
        applicant_name="Second Demo Applicant",
        checklist=[
            ChecklistItem(item="passport", status="verified"),
            ChecklistItem(item="funds_proof", status="verified"),
            ChecklistItem(item="bank_letter", status="verified"),
            ChecklistItem(item="insurance", status="pending"),
        ],
    ),
}

_APPLICANT_INDEX: dict[str, str] = {
    record.applicant_id: case_id for case_id, record in LOCAL_CASES.items()
}


def local_lookup(*, applicant_id: str | None, case_id: str | None) -> CaseLookup:
    resolved_case_id = case_id
    if not resolved_case_id and applicant_id:
        resolved_case_id = _APPLICANT_INDEX.get(applicant_id)
    if resolved_case_id and resolved_case_id in LOCAL_CASES:
        return CaseLookup(status=CaseLookupStatus.FOUND, record=LOCAL_CASES[resolved_case_id])
    identifier = case_id or applicant_id or ""
    return CaseLookup(
        status=CaseLookupStatus.NOT_FOUND,
        message=f"No local case matches {identifier}",
    )


def _parse_remote_payload(payload: Any) -> CaseLookup:
    if not isinstance(payload, dict):
        return CaseLookup(
            status=CaseLookupStatus.ERROR,
            message="Case-status backend returned a non-object JSON body",
            error_category="MALFORMED_RESPONSE",
        )
    raw_checklist = payload.get("checklist")
    if not isinstance(raw_checklist, list):
        return CaseLookup(
            status=CaseLookupStatus.ERROR,
            message="Case-status backend response was missing a checklist array",
            error_category="MALFORMED_RESPONSE",
        )
    items: list[ChecklistItem] = []
    for entry in raw_checklist:
        if not isinstance(entry, dict):
            return CaseLookup(
                status=CaseLookupStatus.ERROR,
                message="Case-status backend returned a malformed checklist entry",
                error_category="MALFORMED_RESPONSE",
            )
        name = entry.get("item") or entry.get("name") or entry.get("id")
        status = entry.get("status")
        if not isinstance(name, str) or not isinstance(status, str):
            return CaseLookup(
                status=CaseLookupStatus.ERROR,
                message="Case-status backend checklist entries need item and status strings",
                error_category="MALFORMED_RESPONSE",
            )
        items.append(
            ChecklistItem(item=name, status=status, detail=entry.get("detail"))
        )
    record = CaseRecord(
        case_id=str(payload.get("case_id") or ""),
        applicant_id=str(payload.get("applicant_id") or ""),
        checklist=items,
        applicant_name=str(payload.get("applicant_name") or ""),
    )
    return CaseLookup(status=CaseLookupStatus.FOUND, record=record)


def _build_remote_url(settings: Settings, applicant_id: str | None, case_id: str | None) -> str:
    url = settings.case_status_url or ""
    if "{case_id}" in url or "{applicant_id}" in url:
        return url.replace("{case_id}", case_id or "").replace("{applicant_id}", applicant_id or "")
    from urllib.parse import urlencode

    params = {key: value for key, value in (("case_id", case_id), ("applicant_id", applicant_id)) if value}
    separator = "&" if "?" in url else "?"
    return f"{url}{separator}{urlencode(params)}" if params else url


async def get_case_status(
    applicant_id: str | None = None,
    case_id: str | None = None,
    *,
    settings: Settings | None = None,
    sender: ProviderSender | None = None,
    tool: str = "pull_case_status_into_call",
) -> CaseLookup:
    """Fetch live checklist state with a bounded timeout; never blocks forever."""
    settings = settings or get_settings()
    if not case_id and not applicant_id:
        return CaseLookup(
            status=CaseLookupStatus.ERROR,
            message="case_id or applicant_id is required",
            error_category="VALIDATION_ERROR",
        )
    if not settings.case_status_url or settings.case_status_source != "http":
        return local_lookup(applicant_id=applicant_id, case_id=case_id)

    from src.gnani_client import HttpxSender

    headers = {"Accept": "application/json"}
    if settings.case_status_token:
        headers["Authorization"] = f"Bearer {settings.case_status_token}"
    request = HttpRequest(
        method="GET",
        url=_build_remote_url(settings, applicant_id, case_id),
        headers=headers,
        timeout=settings.case_status_timeout_seconds,
    )
    try:
        response = await (sender or HttpxSender()).send(request)
    except httpx2.TimeoutException:
        return CaseLookup(
            status=CaseLookupStatus.TIMEOUT,
            source="http",
            message=(
                f"Case-status backend timed out after "
                f"{settings.case_status_timeout_seconds}s; continuing without it"
            ),
            error_category="TIMEOUT",
        )
    except httpx2.TransportError as exc:
        return CaseLookup(
            status=CaseLookupStatus.ERROR,
            source="http",
            message=f"Case-status backend unreachable ({type(exc).__name__})",
            error_category="PROVIDER_ERROR",
        )
    if response.status_code == 404:
        return CaseLookup(
            status=CaseLookupStatus.NOT_FOUND,
            source="http",
            message="Case-status backend has no record for this identifier",
            http_status=404,
        )
    if response.status_code < 200 or response.status_code >= 300:
        return CaseLookup(
            status=CaseLookupStatus.ERROR,
            source="http",
            message=f"Case-status backend returned HTTP {response.status_code}",
            error_category="PROVIDER_ERROR",
            http_status=response.status_code,
        )
    try:
        payload = response.json()
    except Exception:
        return CaseLookup(
            status=CaseLookupStatus.ERROR,
            source="http",
            message="Case-status backend returned invalid JSON",
            error_category="MALFORMED_RESPONSE",
            http_status=response.status_code,
        )
    lookup = _parse_remote_payload(payload)
    lookup.source = "http"
    lookup.http_status = response.status_code
    return lookup
