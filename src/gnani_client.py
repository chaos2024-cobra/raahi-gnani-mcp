"""Centralized Gnani HTTP client: auth, retries, timeouts, response parsing.

Every provider call in this server goes through GnaniClient so that
authentication, retry policy and error classification live in exactly one
place. Auth headers are built here and are never logged or returned.

Endpoint sources (official docs at https://docs.gnani.ai):
  STT           POST https://api.vachana.ai/stt/v3                      (X-API-Key-ID)
  TTS           POST https://api.vachana.ai/api/v1/tts/inference        (X-API-Key-ID)
  Trigger call  POST https://api.inya.ai/platform/v1/agents/{botId}/trigger_call  (x-api-key)
  Call stats    GET  https://api.inya.ai/platform/v1/conversations/{id}/stats     (x-api-key)
  DTMF          NOT publicly documented - isolated behind GNANI_DTMF_URL.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any, Protocol

import httpx2

from src.config import Settings, get_settings
from src.errors import (
    ErrorCategory,
    GnaniError,
    category_for_http_status,
    not_configured,
)

logger = logging.getLogger(__name__)

_JSON_ERROR_KEYS = ("message", "detail", "error_description", "error")


@dataclass
class HttpRequest:
    method: str
    url: str
    headers: dict[str, str] = field(default_factory=dict)
    timeout: float = 10.0
    json_body: Any | None = None
    data: dict[str, str] | None = None
    files: dict[str, Any] | None = None


@dataclass
class HttpResponse:
    status_code: int
    headers: dict[str, str]
    content: bytes

    def json(self) -> Any:
        import json as _json

        return _json.loads(self.content.decode("utf-8"))

    @property
    def content_type(self) -> str:
        for key, value in self.headers.items():
            if key.lower() == "content-type":
                return value.split(";")[0].strip().lower()
        return ""


class ProviderSender(Protocol):
    async def send(self, request: HttpRequest) -> HttpResponse: ...


class HttpxSender:
    def __init__(self) -> None:
        self._client: httpx2.AsyncClient | None = None

    async def send(self, request: HttpRequest) -> HttpResponse:
        if self._client is None:
            self._client = httpx2.AsyncClient()
        response = await self._client.request(
            request.method,
            request.url,
            headers=request.headers,
            json=request.json_body,
            data=request.data,
            files=request.files,
            timeout=httpx2.Timeout(request.timeout),
        )
        return HttpResponse(
            status_code=response.status_code,
            headers=dict(response.headers),
            content=response.content,
        )

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None


@dataclass
class ProviderErrorInfo:
    message: str
    code: str | None = None


def extract_provider_error(response: HttpResponse) -> ProviderErrorInfo:
    try:
        payload = response.json()
    except Exception:
        return ProviderErrorInfo(message=f"Gnani returned HTTP {response.status_code}")
    if isinstance(payload, dict):
        error = payload.get("error")
        if isinstance(error, dict):
            code = error.get("type") or error.get("code")
            message = error.get("message")
            if message:
                return ProviderErrorInfo(message=str(message), code=str(code) if code else None)
        if isinstance(error, str) and error:
            return ProviderErrorInfo(message=error)
        for key in _JSON_ERROR_KEYS:
            value = payload.get(key)
            if isinstance(value, str) and value:
                code = payload.get("code") or payload.get("status")
                return ProviderErrorInfo(message=value, code=str(code) if code else None)
    return ProviderErrorInfo(message=f"Gnani returned HTTP {response.status_code}")


class GnaniClient:
    def __init__(self, settings: Settings | None = None, sender: ProviderSender | None = None) -> None:
        self.settings = settings or get_settings()
        self._sender: ProviderSender = sender or HttpxSender()

    async def _request(self, request: HttpRequest, *, tool: str) -> HttpResponse:
        attempts = self.settings.max_retries + 1
        last_error: GnaniError | None = None
        for attempt in range(attempts):
            try:
                response = await self._sender.send(request)
            except httpx2.TimeoutException:
                last_error = GnaniError(
                    ErrorCategory.TIMEOUT,
                    f"{tool}: Gnani request timed out",
                    tool=tool,
                    retryable=True,
                )
            except httpx2.TransportError as exc:
                last_error = GnaniError(
                    ErrorCategory.PROVIDER_ERROR,
                    f"{tool}: could not reach Gnani ({type(exc).__name__})",
                    tool=tool,
                    retryable=True,
                )
            else:
                if 200 <= response.status_code < 300:
                    return response
                info = extract_provider_error(response)
                category = category_for_http_status(response.status_code)
                details: dict[str, Any] | None = None
                try:
                    parsed = response.json()
                    if isinstance(parsed, dict):
                        details = parsed
                except Exception:
                    details = None
                error = GnaniError(
                    category,
                    info.message,
                    tool=tool,
                    http_status=response.status_code,
                    provider_error_code=info.code,
                    details=details,
                )
                if response.status_code < 500:
                    raise error
                last_error = error
            if attempt < attempts - 1:
                delay = self.settings.retry_backoff_seconds * (2**attempt)
                await asyncio.sleep(delay)
        assert last_error is not None
        raise last_error

    def _speech_headers(self) -> dict[str, str]:
        key = self.settings.require_api_key("gnani_client")
        return {self.settings.speech_auth_header: key}

    def _platform_headers(self) -> dict[str, str]:
        key = self.settings.require_api_key("gnani_client")
        return {self.settings.platform_auth_header: key}

    async def transcribe_speech(
        self,
        *,
        audio: bytes,
        language: str,
        filename: str | None = None,
        content_type: str | None = None,
        tool: str = "transcribe_speech",
    ) -> dict[str, Any]:
        request = HttpRequest(
            method="POST",
            url=self.settings.stt_url,
            headers=self._speech_headers(),
            timeout=self.settings.http_timeout_seconds,
            data={"language_code": language},
            files={
                "audio_file": (
                    filename or "audio.wav",
                    audio,
                    content_type or "application/octet-stream",
                )
            },
        )
        response = await self._request(request, tool=tool)
        payload = self._parse_json_object(response, tool=tool, what="STT transcript")
        if payload.get("success") is False:
            error = payload.get("error") if isinstance(payload.get("error"), dict) else {}
            raise GnaniError(
                ErrorCategory.PROVIDER_ERROR,
                str(error.get("message") or payload.get("message") or "Gnani STT reported failure"),
                tool=tool,
                http_status=response.status_code,
                provider_error_code=str(error.get("type")) if error.get("type") else None,
                retryable=False,
                details=payload,
            )
        if not any(key in payload for key in ("transcript", "partial_transcript", "interim_transcript")):
            raise GnaniError(
                ErrorCategory.MALFORMED_RESPONSE,
                "Gnani STT response did not contain a transcript field",
                tool=tool,
                retryable=False,
            )
        return payload

    async def speak_reply(
        self,
        *,
        text: str,
        language: str,
        voice_id: str,
        speed: float = 1.0,
        tool: str = "speak_reply",
    ) -> tuple[bytes, str, dict[str, Any]]:
        settings = self.settings
        body = {
            "text": text,
            "model": settings.tts_model,
            "voice": voice_id,
            "language": language,
            "speed": speed,
            "audio_config": {
                "sample_rate": 48000,
                "num_channels": 1,
                "sample_width": 2,
                "encoding": "linear_pcm",
                "container": settings.tts_container,
            },
        }
        request = HttpRequest(
            method="POST",
            url=settings.tts_url,
            headers=self._speech_headers(),
            timeout=settings.http_timeout_seconds,
            json_body=body,
        )
        response = await self._request(request, tool=tool)
        content_type = response.content_type
        if content_type.startswith("application/json"):
            payload = self._parse_json_object(response, tool=tool, what="TTS result")
            error = payload.get("error") if isinstance(payload.get("error"), dict) else {}
            raise GnaniError(
                ErrorCategory.PROVIDER_ERROR,
                str(error.get("message") or payload.get("message") or "Gnani TTS reported failure"),
                tool=tool,
                http_status=response.status_code,
                provider_error_code=str(error.get("type")) if error.get("type") else None,
                retryable=False,
            )
        if not response.content:
            raise GnaniError(
                ErrorCategory.MALFORMED_RESPONSE,
                "Gnani TTS returned an empty audio body",
                tool=tool,
                retryable=False,
            )
        if not content_type:
            content_type = "audio/wav"
        return response.content, content_type, {}

    async def trigger_call(
        self,
        *,
        phone: str,
        country_code: str,
        name: str,
        checklist_item_id: str,
        tool: str = "call_bank_rm_or_desk",
    ) -> dict[str, Any]:
        settings = self.settings
        url = settings.trigger_call_url
        if "{bot_id}" in url:
            if not settings.bot_id:
                raise not_configured(
                    "GNANI_BOT_ID is not configured; trigger-call URL template cannot be built.",
                    tool=tool,
                )
            url = url.replace("{bot_id}", settings.bot_id)
        if "?" not in url and settings.call_environment:
            url = f"{url}?environment={settings.call_environment}"
        body = {
            "phone": phone,
            "countryCode": country_code,
            "name": name,
            "clientReferenceId": checklist_item_id,
        }
        request = HttpRequest(
            method="POST",
            url=url,
            headers=self._platform_headers(),
            timeout=settings.http_timeout_seconds,
            json_body=body,
        )
        response = await self._request(request, tool=tool)
        payload = self._parse_json_object(response, tool=tool, what="trigger-call result")
        if str(payload.get("status", "")).lower() != "success":
            raise GnaniError(
                ErrorCategory.PROVIDER_ERROR,
                str(payload.get("message") or "Gnani trigger call did not confirm success"),
                tool=tool,
                http_status=response.status_code,
                provider_error_code=str(payload.get("status")) if payload.get("status") else None,
                retryable=False,
            )
        return payload

    async def get_call_outcome(
        self,
        *,
        conversation_id: str,
        tool: str = "read_call_outcome",
    ) -> dict[str, Any]:
        settings = self.settings
        url = settings.call_outcome_url.replace("{conversation_id}", conversation_id)
        request = HttpRequest(
            method="GET",
            url=url,
            headers=self._platform_headers(),
            timeout=settings.http_timeout_seconds,
        )
        response = await self._request(request, tool=tool)
        payload = self._parse_json_object(response, tool=tool, what="conversation statistics")
        if str(payload.get("status", "")).lower() not in ("success", ""):
            raise GnaniError(
                ErrorCategory.PROVIDER_ERROR,
                str(payload.get("message") or "Gnani conversation statistics request failed"),
                tool=tool,
                http_status=response.status_code,
                provider_error_code=str(payload.get("status")),
                retryable=False,
            )
        records = payload.get("response")
        if not isinstance(records, list) or not records:
            raise GnaniError(
                ErrorCategory.NOT_FOUND,
                f"Gnani has no statistics for conversation {conversation_id}",
                tool=tool,
                http_status=response.status_code,
                retryable=False,
            )
        record = records[0]
        if not isinstance(record, dict):
            raise GnaniError(
                ErrorCategory.MALFORMED_RESPONSE,
                "Gnani conversation statistics record was malformed",
                tool=tool,
                retryable=False,
            )
        return record

    async def send_dtmf(
        self,
        *,
        conversation_id: str | None,
        dtmf_sequence: str,
        tool: str = "navigate_ivr",
    ) -> dict[str, Any]:
        settings = self.settings
        if not settings.dtmf_url:
            raise not_configured(
                "GNANI_DTMF_URL is not set. Gnani does not publicly document a DTMF "
                "endpoint; obtain the URL from Gnani before using navigate_ivr.",
                tool=tool,
            )
        request = HttpRequest(
            method="POST",
            url=settings.dtmf_url,
            headers=self._platform_headers(),
            timeout=settings.http_timeout_seconds,
            json_body={"conversation_id": conversation_id, "dtmf": dtmf_sequence},
        )
        response = await self._request(request, tool=tool)
        if not response.content:
            return {"status": "accepted", "conversation_id": conversation_id}
        try:
            payload = response.json()
        except Exception as exc:
            raise GnaniError(
                ErrorCategory.MALFORMED_RESPONSE,
                "Gnani DTMF endpoint returned a non-JSON body",
                tool=tool,
                retryable=False,
            ) from exc
        if isinstance(payload, dict):
            return payload
        raise GnaniError(
            ErrorCategory.MALFORMED_RESPONSE,
            "Gnani DTMF endpoint returned an unexpected JSON shape",
            tool=tool,
            retryable=False,
        )

    async def get_case_status(
        self,
        *,
        applicant_id: str | None = None,
        case_id: str | None = None,
        tool: str = "pull_case_status_into_call",
    ):
        from src.case_status import get_case_status

        return await get_case_status(
            applicant_id=applicant_id,
            case_id=case_id,
            settings=self.settings,
            sender=self._sender,
            tool=tool,
        )

    @staticmethod
    def _parse_json_object(response: HttpResponse, *, tool: str, what: str) -> dict[str, Any]:
        try:
            payload = response.json()
        except Exception as exc:
            raise GnaniError(
                ErrorCategory.MALFORMED_RESPONSE,
                f"Gnani {what} response was not valid JSON",
                tool=tool,
                http_status=response.status_code,
                retryable=False,
            ) from exc
        if not isinstance(payload, dict):
            raise GnaniError(
                ErrorCategory.MALFORMED_RESPONSE,
                f"Gnani {what} response was not a JSON object",
                tool=tool,
                http_status=response.status_code,
                retryable=False,
            )
        return payload
