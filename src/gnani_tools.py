"""The six Gnani MCP tools (KEN.pdf Round 2 capabilities) plus server routes.

Safety boundary enforced here:
  * inputs are validated before any provider request is made
  * provider failures become typed structured errors, never fake success
  * unclear speech, unmapped menus and unapproved numbers are never guessed
  * a connected call is never reported as a resolved checklist item
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import re
import time
from typing import Annotated, Any

from mcp.server.mcpserver import MCPServer, Audio
from mcp.types import CallToolResult, TextContent
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from src.case_status import CaseLookup, CaseLookupStatus, get_case_status
from src.config import Settings, get_settings
from src.errors import ErrorCategory, GnaniError, build_error_detail, validation_error
from src.gnani_client import GnaniClient, ProviderSender
from src.logging_utils import log_tool_event, mask_phone, new_request_id
from src.models import (
    CallOutcomeResult,
    CallTriggerResult,
    CaseStatusResult,
    ChecklistItem,
    DtmfResult,
    SpeakReplyResult,
    TranscribeSpeechResult,
    TranscriptTurn,
    ToolResultBase,
)

SERVER_NAME = "raahi-gnani-mcp"
SERVER_VERSION = "1.0.0"
PROVIDER = "gnani"

TOOL_NAMES = (
    "transcribe_speech",
    "speak_reply",
    "call_bank_rm_or_desk",
    "read_call_outcome",
    "navigate_ivr",
    "pull_case_status_into_call",
)

TRANSCRIBE_DESCRIPTION = (
    "Transcribe applicant speech using Gnani Prisma v2.5 STT (KEN.pdf Capability 1, Voice rail, "
    "Intake). Send 16kHz mic audio and a language hint; returns the final transcript, and an "
    "interim transcript only when the configured endpoint actually supplies one. When speech is "
    "unclear the verbatim transcript is returned with an uncertainty/partial flag - never guess "
    "an unclear destination, date or amount."
)

SPEAK_DESCRIPTION = (
    "Speak the supplied reply with Gnani Timbre TTS (KEN.pdf Capability 2, Voice rail, voice "
    "states) and return the generated audio as MCP audio content plus metadata. Provider "
    "failures are returned as errors instead of reusing stale audio. Never read an unverified "
    "document, date or amount: only the exact text passed to this tool is spoken."
)

CALL_DESCRIPTION = (
    "Trigger an authorized Gnani call to an approved bank/RM number (KEN.pdf Capability 3, "
    "Gnani Trigger Call API, GapResolution -> VoiceChase). Never call an unapproved number: "
    "empty/invalid numbers are rejected locally and unwhitelisted numbers are rejected by Gnani "
    "with 400/403. KEN.pdf marks this capability PARTIAL - production scale requires commercial "
    "access. A triggered or connected call is never reported as a resolved checklist item."
)

OUTCOME_DESCRIPTION = (
    "Retrieve the actual result of a Gnani call via the Gnani Conversation Statistics API "
    "(KEN.pdf Capability 4, after VoiceChase): status, disposition and transcript. A connected "
    "call is not itself proof that a checklist item was resolved. While analytics are still "
    "processing the tool returns ANALYTICS_PENDING after bounded polling - it never polls "
    "forever and never invents an outcome."
)

IVR_DESCRIPTION = (
    "Send an explicitly supplied DTMF sequence on a Gnani call (KEN.pdf Capability 5, Gnani DTMF "
    "Collection, VoiceChase IVR calls) and return the tone-registration confirmation. Never "
    "guess unmapped menu options: the full sequence must come from the caller, and a changed or "
    "invalid menu yields an explicit failure. Until GNANI_DTMF_URL is configured the tool fails "
    "with NOT_CONFIGURED, because Gnani publishes no public DTMF endpoint."
)

CASE_DESCRIPTION = (
    "Retrieve the current checklist status for a case/applicant with a bounded timeout so the "
    "external caller is never left waiting on our backend (KEN.pdf Capability 6, Gnani Custom "
    "Integrations, VoiceChase). KEN.pdf marks this PARTIAL: the Gnani capability exists but our "
    "endpoint must be built. This tool reads the isolated case-status adapter (local demo store "
    "or GNANI_CASE_STATUS_URL) and returns a TIMEOUT result immediately when the backend is slow."
)

STT_NOTE = (
    "Final transcript from the configured Gnani STT endpoint (Prisma v2.5). The synchronous REST "
    "endpoint returns only the final transcript, so interim_transcript stays null unless the "
    "provider supplies it; interim data is never fabricated. Unclear destinations, dates and "
    "amounts are never guessed from this transcript."
)

TTS_NOTE = (
    "Audio synthesized by the configured Gnani Timbre endpoint. The synchronous inference "
    "endpoint returns the complete audio in one response, so no streamed chunks are available "
    "here; streaming SSE/WebSocket variants are not used by this adapter. Only the exact text "
    "supplied to the tool was spoken."
)

CALL_NOTE = (
    "Gnani accepted the trigger and is placing the call. conversationId is not returned by the "
    "trigger endpoint - read it later via conversation logs or a post-call webhook, then call "
    "read_call_outcome. Triggering or connecting a call does not resolve any checklist item."
)

CASE_TIMEOUT_NOTE = (
    "Case-status lookup exceeded its bounded timeout; the tool returned immediately so an "
    "in-progress external call can continue without waiting on our backend."
)

provider_sender: ProviderSender | None = None


def build_client(settings: Settings) -> GnaniClient:
    return GnaniClient(settings=settings, sender=provider_sender)


def _elapsed_ms(started: float) -> float:
    return (time.perf_counter() - started) * 1000.0


def _success_kwargs(tool: str, request_id: str, started: float) -> dict[str, Any]:
    return {
        "success": True,
        "error_category": ErrorCategory.SUCCESS,
        "error": None,
        "request_id": request_id,
        "provider": PROVIDER,
        "duration_ms": _elapsed_ms(started),
    }


def _error_kwargs(err: GnaniError, tool: str, request_id: str, started: float) -> dict[str, Any]:
    detail = err.to_detail(tool)
    return {
        "success": False,
        "error_category": detail.error_category,
        "error": detail,
        "request_id": request_id,
        "provider": PROVIDER,
        "duration_ms": _elapsed_ms(started),
    }


def _unexpected_kwargs(exc: Exception, tool: str, request_id: str, started: float) -> dict[str, Any]:
    detail = build_error_detail(
        tool,
        ErrorCategory.PROVIDER_ERROR,
        f"Unexpected internal error while running {tool} ({type(exc).__name__})",
        retryable=False,
    )
    return {
        "success": False,
        "error_category": detail.error_category,
        "error": detail,
        "request_id": request_id,
        "provider": PROVIDER,
        "duration_ms": _elapsed_ms(started),
    }


def _emit(result: ToolResultBase, tool: str, request_id: str, **extra: Any) -> None:
    log_tool_event(
        tool_name=tool,
        request_id=request_id,
        duration_ms=result.duration_ms,
        success=result.success,
        error_category=result.error_category.value,
        provider=PROVIDER,
        **extra,
    )


_LANGUAGE_RE = re.compile(r"^[A-Za-z]{2,3}(-[A-Za-z]{2,4})?$")
_CONVERSATION_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_DTMF_RE = re.compile(r"^[0-9*#]{1,32}$")
_COUNTRY_CODE_RE = re.compile(r"^\+[1-9]\d{0,2}$")


def _validate_language(value: str, tool: str, *, allow_auto: bool = False) -> None:
    if not value or not value.strip():
        raise validation_error("language is required (e.g. 'hi-IN' or 'en-IN')", tool=tool)
    normalized = value.strip()
    if allow_auto and normalized.lower() == "auto":
        return
    if not _LANGUAGE_RE.match(normalized):
        raise validation_error(
            f"language '{normalized}' must be a BCP-47 style code such as 'hi-IN'"
            + (" or 'auto'" if allow_auto else ""),
            tool=tool,
        )


def _safe_filename(filename: str | None) -> str | None:
    if filename is None:
        return None
    cleaned = filename.strip()
    if not cleaned:
        return None
    if any(token in cleaned for token in ("/", "\\", "..", "\x00")):
        raise validation_error(
            "filename must not contain path separators or traversal sequences",
            tool="transcribe_speech",
        )
    if len(cleaned) > 128:
        raise validation_error("filename must be at most 128 characters", tool="transcribe_speech")
    return cleaned


def _decode_audio(audio: str | None, settings: Settings, tool: str) -> bytes:
    if not audio or not audio.strip():
        raise validation_error("audio must be provided as a base64 string", tool=tool)
    max_b64 = int(settings.max_audio_bytes * 4 / 3) + 4096
    if len(audio) > max_b64:
        raise validation_error(
            f"audio exceeds the maximum size of {settings.max_audio_bytes} bytes",
            tool=tool,
        )
    try:
        raw = base64.b64decode(audio, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise validation_error("audio is not valid base64", tool=tool) from exc
    if not raw:
        raise validation_error("audio decoded to zero bytes", tool=tool)
    if len(raw) > settings.max_audio_bytes:
        raise validation_error(
            f"audio exceeds the maximum size of {settings.max_audio_bytes} bytes",
            tool=tool,
        )
    return raw


def _validate_phone(phone: str | None, tool: str) -> str:
    if phone is None or not str(phone).strip():
        raise validation_error("phone is required and must not be empty", tool=tool)
    cleaned = re.sub(r"[\s\-().]", "", str(phone))
    if cleaned.startswith("+"):
        cleaned = cleaned[1:]
    if not cleaned or not cleaned.isdigit():
        raise validation_error(
            "phone must contain digits only (no letters or symbols) and is sent without the country code",
            tool=tool,
        )
    if not 6 <= len(cleaned) <= 15:
        raise validation_error(
            "phone must be between 6 and 15 digits (national number, without country code)",
            tool=tool,
        )
    return cleaned


def _validate_country_code(country_code: str | None, tool: str) -> str:
    if country_code is None or not str(country_code).strip():
        raise validation_error("country_code is required (e.g. '+91')", tool=tool)
    cleaned = str(country_code).strip()
    if not _COUNTRY_CODE_RE.match(cleaned):
        raise validation_error("country_code must be a dialing code with '+' prefix, e.g. '+91'", tool=tool)
    return cleaned


def _ensure_number_allowed(phone_digits: str, country_code: str, settings: Settings, tool: str) -> None:
    if not settings.allowed_numbers:
        return
    allowed_digits = {re.sub(r"\D", "", entry) for entry in settings.allowed_numbers}
    if phone_digits in allowed_digits or f"{re.sub(r'\D', '', country_code)}{phone_digits}" in allowed_digits:
        return
    raise GnaniError(
        ErrorCategory.FORBIDDEN,
        "number is not listed in GNANI_ALLOWED_NUMBERS; unapproved numbers are never dialed",
        tool=tool,
        retryable=False,
    )


def _validate_identifier(value: str | None, field_name: str, tool: str) -> str | None:
    if value is None:
        return None
    cleaned = value.strip()
    if not cleaned:
        raise validation_error(f"{field_name} must not be empty", tool=tool)
    if not _IDENTIFIER_RE.match(cleaned):
        raise validation_error(
            f"{field_name} may only contain letters, digits, '_' and '-'",
            tool=tool,
        )
    return cleaned


def _analytics_ready(record: dict[str, Any]) -> bool:
    processed = record.get("callProcessed")
    if processed is True:
        return True
    if processed is False:
        return False
    return record.get("callSummary") is not None


def _extract_resolution_evidence(record: dict[str, Any]) -> tuple[dict | None, bool]:
    evidence = record.get("resolution_evidence")
    if isinstance(evidence, dict) and evidence:
        return evidence, True
    if record.get("checklist_resolved") is True:
        return {"checklist_resolved": True}, True
    return None, False


def _build_transcript(record: dict[str, Any]) -> list[TranscriptTurn]:
    turns: list[TranscriptTurn] = []
    raw = record.get("utteranceAnalytics")
    if isinstance(raw, list):
        for entry in raw:
            if not isinstance(entry, dict):
                continue
            turns.append(
                TranscriptTurn(
                    role=entry.get("role"),
                    content=entry.get("content"),
                    timestamp=entry.get("timestamp"),
                    detected_language=entry.get("detected_language") or entry.get("detectedLanguage"),
                )
            )
    return turns


def _audio_format_for(content_type: str) -> str:
    if content_type.startswith("audio/"):
        return content_type[len("audio/") :]
    return "wav"


mcp = MCPServer(
    name=SERVER_NAME,
    title="Raahi Gnani MCP",
    description=(
        "MCP adapter that exposes the six Gnani voice capabilities from the Raahi "
        "KEN.pdf Round 2 design as MCP tools for AgenticOrg."
    ),
    instructions=(
        "Raahi Gnani MCP is an adapter, not the agent brain. It exposes exactly six Gnani "
        "tools and performs no visa decisions. It never guesses unclear dates, destinations "
        "or amounts, never dials unapproved numbers, never guesses IVR menu options, and "
        "never converts a connected call into a resolved checklist item. Provider failures "
        "are returned as typed structured errors."
    ),
    version=SERVER_VERSION,
)


@mcp.custom_route("/health", methods=["GET"])
async def health(request: Request) -> Response:
    return JSONResponse({"ok": True, "service": SERVER_NAME})


def _case_json(lookup: CaseLookup, applicant_id: str | None, case_id: str | None) -> dict[str, Any]:
    record = lookup.record
    return {
        "case_id": (record.case_id if record else case_id) or None,
        "applicant_id": (record.applicant_id if record else applicant_id) or None,
        "checklist": [
            {"item": item.item, "status": item.status, "detail": item.detail}
            for item in (record.checklist if record else [])
        ],
        "source": lookup.source,
        "fetched_at": lookup.fetched_at,
    }


async def _case_status_response(request: Request, applicant_id: str | None, case_id: str | None) -> Response:
    settings = get_settings()
    if settings.case_status_token:
        auth = request.headers.get("authorization", "")
        if auth != f"Bearer {settings.case_status_token}":
            return JSONResponse({"ok": False, "error": "unauthorized"}, status_code=401)
    lookup = await get_case_status(applicant_id=applicant_id, case_id=case_id, settings=settings)
    if lookup.status is CaseLookupStatus.FOUND:
        return JSONResponse(_case_json(lookup, applicant_id, case_id))
    if lookup.status is CaseLookupStatus.NOT_FOUND:
        return JSONResponse({"ok": False, "error": "NOT_FOUND", "message": lookup.message}, status_code=404)
    if lookup.status is CaseLookupStatus.TIMEOUT:
        return JSONResponse({"ok": False, "error": "TIMEOUT", "message": lookup.message}, status_code=504)
    return JSONResponse({"ok": False, "error": "ERROR", "message": lookup.message}, status_code=502)


@mcp.custom_route("/case-status", methods=["GET"])
async def case_status_by_applicant(request: Request) -> Response:
    applicant_id = request.query_params.get("applicant_id")
    case_id = request.query_params.get("case_id")
    if not applicant_id and not case_id:
        return JSONResponse(
            {"ok": False, "error": "VALIDATION_ERROR", "message": "case_id or applicant_id is required"},
            status_code=400,
        )
    return await _case_status_response(request, applicant_id=applicant_id, case_id=case_id)


@mcp.custom_route("/case-status/{case_id}", methods=["GET"])
async def case_status_by_id(request: Request) -> Response:
    return await _case_status_response(request, applicant_id=None, case_id=request.path_params["case_id"])


@mcp.tool(name="transcribe_speech", description=TRANSCRIBE_DESCRIPTION)
async def transcribe_speech(
    audio: str,
    language: str,
    filename: str | None = None,
    content_type: str | None = None,
) -> TranscribeSpeechResult:
    tool = "transcribe_speech"
    started = time.perf_counter()
    request_id = new_request_id()
    try:
        settings = get_settings()
        raw_audio = _decode_audio(audio, settings, tool)
        _validate_language(language, tool)
        safe_name = _safe_filename(filename)
        payload = await build_client(settings).transcribe_speech(
            audio=raw_audio,
            language=language,
            filename=safe_name,
            content_type=content_type,
            tool=tool,
        )
    except GnaniError as err:
        result = TranscribeSpeechResult(**_error_kwargs(err, tool, request_id, started))
        details = err.details or {}
        partial = details.get("partial_transcript")
        interim = details.get("interim_transcript")
        if isinstance(partial, str) and partial:
            result.partial_transcript = partial
            result.partial = True
        if isinstance(interim, str) and interim:
            result.interim_transcript = interim
            result.interim_available = True
        _emit(result, tool, request_id, http_status=err.http_status)
        return result
    except Exception as exc:
        result = TranscribeSpeechResult(**_unexpected_kwargs(exc, tool, request_id, started))
        _emit(result, tool, request_id)
        return result

    interim = payload.get("interim_transcript")
    confidence = payload.get("confidence")
    result = TranscribeSpeechResult(
        **_success_kwargs(tool, request_id, started),
        language=language,
        final_transcript=payload.get("transcript"),
        interim_transcript=interim if isinstance(interim, str) else None,
        interim_available=bool(isinstance(interim, str) and interim),
        provider_request_id=payload.get("request_id"),
        provider_model=payload.get("model"),
        uncertainty=(float(confidence) < 0.5) if isinstance(confidence, (int, float)) else None,
        note=STT_NOTE,
    )
    _emit(result, tool, request_id, provider_request_id=result.provider_request_id)
    return result


@mcp.tool(name="speak_reply", description=SPEAK_DESCRIPTION)
async def speak_reply(
    text: str,
    language: str,
    voice_id: str,
    speed: float = 1.0,
) -> Annotated[CallToolResult, SpeakReplyResult]:
    tool = "speak_reply"
    started = time.perf_counter()
    request_id = new_request_id()
    try:
        settings = get_settings()
        if not text or not text.strip():
            raise validation_error("text must not be empty", tool=tool)
        if len(text) > settings.max_tts_text_chars:
            raise validation_error(
                f"text exceeds the maximum length of {settings.max_tts_text_chars} characters",
                tool=tool,
            )
        _validate_language(language, tool, allow_auto=True)
        if not voice_id or not voice_id.strip():
            raise validation_error("voice_id must not be empty", tool=tool)
        if len(voice_id) > 64:
            raise validation_error("voice_id must be at most 64 characters", tool=tool)
        if not isinstance(speed, (int, float)) or not 0.85 <= float(speed) <= 1.15:
            raise validation_error("speed must be between 0.85 and 1.15", tool=tool)
        audio_bytes, content_type, _ = await build_client(settings).speak_reply(
            text=text,
            language=language,
            voice_id=voice_id.strip(),
            speed=float(speed),
            tool=tool,
        )
    except GnaniError as err:
        result = SpeakReplyResult(**_error_kwargs(err, tool, request_id, started))
        _emit(result, tool, request_id, http_status=err.http_status)
        return _as_tool_result(result)
    except Exception as exc:
        result = SpeakReplyResult(**_unexpected_kwargs(exc, tool, request_id, started))
        _emit(result, tool, request_id)
        return _as_tool_result(result)

    result = SpeakReplyResult(
        **_success_kwargs(tool, request_id, started),
        content_type=content_type,
        container=(
            _audio_format_for(content_type)
            if content_type.startswith("audio/")
            else settings.tts_container
        ),
        sample_rate=48000,
        audio_size_bytes=len(audio_bytes),
        audio_base64=base64.b64encode(audio_bytes).decode("ascii"),
        streaming=False,
        chunk_count=None,
        voice_id=voice_id.strip(),
        language=language,
        model=settings.tts_model,
        note=TTS_NOTE,
    )
    _emit(result, tool, request_id)
    return _as_tool_result(result, audio_bytes=audio_bytes)


def _as_tool_result(result: SpeakReplyResult, audio_bytes: bytes | None = None) -> CallToolResult:
    if audio_bytes:
        audio_block = Audio(data=audio_bytes, format=_audio_format_for(result.content_type or "audio/wav"))
        content = [audio_block.to_audio_content(), TextContent(type="text", text=result.model_dump_json())]
    else:
        content = [TextContent(type="text", text=result.model_dump_json())]
    return CallToolResult(content=content, structured_content=result.model_dump(mode="json"))


@mcp.tool(name="call_bank_rm_or_desk", description=CALL_DESCRIPTION)
async def call_bank_rm_or_desk(
    phone: str,
    country_code: str,
    name: str,
    checklist_item_id: str,
) -> CallTriggerResult:
    tool = "call_bank_rm_or_desk"
    started = time.perf_counter()
    request_id = new_request_id()
    try:
        settings = get_settings()
        phone_digits = _validate_phone(phone, tool)
        dial_code = _validate_country_code(country_code, tool)
        if not name or not name.strip():
            raise validation_error("name must not be empty", tool=tool)
        if len(name) > 120:
            raise validation_error("name must be at most 120 characters", tool=tool)
        item_id = _validate_identifier(checklist_item_id, "checklist_item_id", tool)
        assert item_id is not None
        _ensure_number_allowed(phone_digits, dial_code, settings, tool)
        payload = await build_client(settings).trigger_call(
            phone=phone_digits,
            country_code=dial_code,
            name=name.strip(),
            checklist_item_id=item_id,
            tool=tool,
        )
    except GnaniError as err:
        result = CallTriggerResult(**_error_kwargs(err, tool, request_id, started))
        _emit(
            result,
            tool,
            request_id,
            http_status=err.http_status,
            phone_masked=mask_phone(phone),
        )
        return result
    except Exception as exc:
        result = CallTriggerResult(**_unexpected_kwargs(exc, tool, request_id, started))
        _emit(result, tool, request_id)
        return result

    response_obj = payload.get("response")
    client_ref = None
    if isinstance(response_obj, dict):
        client_ref = response_obj.get("clientReferenceId")
    result = CallTriggerResult(
        **_success_kwargs(tool, request_id, started),
        checklist_item_id=checklist_item_id,
        conversation_id=None,
        trigger_status=str(payload.get("status") or "success"),
        current_status="TRIGGERED",
        disposition=None,
        transcript=None,
        provider_message=payload.get("message"),
        provider_request_id=payload.get("requestId"),
        client_reference_id=client_ref,
        note=CALL_NOTE,
    )
    _emit(
        result,
        tool,
        request_id,
        phone_masked=mask_phone(phone),
        provider_request_id=result.provider_request_id,
    )
    return result


@mcp.tool(name="read_call_outcome", description=OUTCOME_DESCRIPTION)
async def read_call_outcome(conversation_id: str) -> CallOutcomeResult:
    tool = "read_call_outcome"
    started = time.perf_counter()
    request_id = new_request_id()
    record: dict[str, Any] | None = None
    polls = 0
    try:
        settings = get_settings()
        if not conversation_id or not str(conversation_id).strip():
            raise validation_error("conversation_id is required", tool=tool)
        conversation_id = _validate_identifier(conversation_id, "conversation_id", tool)
        assert conversation_id is not None
        client = build_client(settings)
        attempts = settings.outcome_poll_max_attempts
        for attempt in range(1, attempts + 1):
            polls = attempt
            record = await client.get_call_outcome(conversation_id=conversation_id, tool=tool)
            if _analytics_ready(record):
                break
            if attempt < attempts and settings.outcome_poll_interval_seconds > 0:
                await asyncio.sleep(settings.outcome_poll_interval_seconds)
    except GnaniError as err:
        result = CallOutcomeResult(**_error_kwargs(err, tool, request_id, started), polls_attempted=polls)
        _emit(result, tool, request_id, http_status=err.http_status)
        return result
    except Exception as exc:
        result = CallOutcomeResult(**_unexpected_kwargs(exc, tool, request_id, started), polls_attempted=polls)
        _emit(result, tool, request_id)
        return result

    assert record is not None
    call_status = record.get("callStatus")
    connected = isinstance(call_status, str) and call_status.strip().upper() == "ANSWERED"
    summary = record.get("callSummary")
    disposition_label = summary.get("disposition") if isinstance(summary, dict) else None
    evidence, resolved = _extract_resolution_evidence(record)
    ready = _analytics_ready(record)

    if not ready:
        detail = build_error_detail(
            tool,
            ErrorCategory.ANALYTICS_PENDING,
            "Gnani analytics for this conversation are still processing; poll again later",
            retryable=True,
        )
        result = CallOutcomeResult(
            success=False,
            error_category=ErrorCategory.ANALYTICS_PENDING,
            error=detail,
            request_id=request_id,
            duration_ms=_elapsed_ms(started),
            conversation_id=conversation_id,
            call_status=call_status,
            connected=connected,
            disposition=record.get("overallCallDisposition"),
            disposition_label=disposition_label,
            transcript=_build_transcript(record),
            analytics_ready=False,
            analytics_status="ANALYTICS_PENDING",
            resolved=False,
            polls_attempted=polls,
            note="ANALYTICS_PENDING: outcome is not final yet. A connected call is never treated as resolved.",
        )
        _emit(result, tool, request_id)
        return result

    result = CallOutcomeResult(
        **_success_kwargs(tool, request_id, started),
        conversation_id=conversation_id,
        call_status=call_status,
        connected=connected,
        disposition=record.get("overallCallDisposition"),
        disposition_label=disposition_label,
        transcript=_build_transcript(record),
        analytics_ready=True,
        analytics_status="READY",
        resolved=resolved,
        resolution_evidence=evidence,
        polls_attempted=polls,
        note=(
            "Status, disposition and transcript are reported exactly as Gnani provides them. "
            "A connected call is not proof that a checklist item was resolved; resolved=true "
            "only appears when the provider supplies explicit resolution evidence."
        ),
    )
    _emit(result, tool, request_id)
    return result


@mcp.tool(name="navigate_ivr", description=IVR_DESCRIPTION)
async def navigate_ivr(
    dtmf_sequence: str,
    conversation_id: str | None = None,
) -> DtmfResult:
    tool = "navigate_ivr"
    started = time.perf_counter()
    request_id = new_request_id()
    try:
        settings = get_settings()
        if dtmf_sequence is None or not str(dtmf_sequence).strip():
            raise validation_error("dtmf_sequence is required", tool=tool)
        sequence = str(dtmf_sequence).strip()
        if not _DTMF_RE.match(sequence):
            raise validation_error(
                "dtmf_sequence may only contain digits, '*' and '#' (1-32 characters); "
                "menu options are never guessed",
                tool=tool,
            )
        conversation = _validate_identifier(conversation_id, "conversation_id", tool)
        payload = await build_client(settings).send_dtmf(
            conversation_id=conversation,
            dtmf_sequence=sequence,
            tool=tool,
        )
    except GnaniError as err:
        result = DtmfResult(**_error_kwargs(err, tool, request_id, started))
        _emit(result, tool, request_id, http_status=err.http_status)
        return result
    except Exception as exc:
        result = DtmfResult(**_unexpected_kwargs(exc, tool, request_id, started))
        _emit(result, tool, request_id)
        return result

    status_value = str(payload.get("status", "")).lower()
    accepted = payload.get("accepted")
    if isinstance(accepted, bool):
        is_accepted = accepted
    else:
        is_accepted = status_value not in ("failure", "failed", "error", "rejected", "invalid")
    if not is_accepted:
        detail = build_error_detail(
            tool,
            ErrorCategory.VALIDATION_ERROR,
            str(payload.get("message") or payload.get("reason") or "Gnani rejected the DTMF sequence"),
            retryable=False,
        )
        result = DtmfResult(
            success=False,
            error_category=ErrorCategory.VALIDATION_ERROR,
            error=detail,
            request_id=request_id,
            duration_ms=_elapsed_ms(started),
            conversation_id=conversation,
            accepted=False,
            dtmf_sequence_masked="*" * len(sequence),
            provider_status=str(payload.get("status")) if payload.get("status") else None,
            provider_response=payload,
            note="Provider rejected the sequence (for example a changed IVR menu). Menu options are never guessed.",
        )
        _emit(result, tool, request_id)
        return result

    result = DtmfResult(
        **_success_kwargs(tool, request_id, started),
        conversation_id=conversation,
        accepted=True,
        dtmf_sequence_masked="*" * len(sequence),
        provider_status=str(payload.get("status")) if payload.get("status") else "accepted",
        provider_response=payload,
        note=(
            "DTMF tones were submitted exactly as supplied. No menu mapping was consulted and "
            "no option was guessed."
        ),
    )
    _emit(result, tool, request_id)
    return result


@mcp.tool(name="pull_case_status_into_call", description=CASE_DESCRIPTION)
async def pull_case_status_into_call(
    case_id: str | None = None,
    applicant_id: str | None = None,
) -> CaseStatusResult:
    tool = "pull_case_status_into_call"
    started = time.perf_counter()
    request_id = new_request_id()
    try:
        settings = get_settings()
        if not case_id and not applicant_id:
            raise validation_error("case_id or applicant_id is required", tool=tool)
        case_id = _validate_identifier(case_id, "case_id", tool)
        applicant_id = _validate_identifier(applicant_id, "applicant_id", tool)
        lookup = await get_case_status(
            applicant_id=applicant_id,
            case_id=case_id,
            settings=settings,
            sender=provider_sender,
            tool=tool,
        )
    except GnaniError as err:
        result = CaseStatusResult(**_error_kwargs(err, tool, request_id, started))
        _emit(result, tool, request_id, http_status=err.http_status)
        return result
    except Exception as exc:
        result = CaseStatusResult(**_unexpected_kwargs(exc, tool, request_id, started))
        _emit(result, tool, request_id)
        return result

    if lookup.status is CaseLookupStatus.FOUND:
        record = lookup.record
        assert record is not None
        result = CaseStatusResult(
            **_success_kwargs(tool, request_id, started),
            case_id=record.case_id or case_id,
            applicant_id=record.applicant_id or applicant_id,
            checklist=[
                ChecklistItem(item=item.item, status=item.status, detail=item.detail)
                for item in record.checklist
            ],
            source=lookup.source,
            fetched_at=lookup.fetched_at,
            note="Live checklist state from the case-status adapter; agent state is never modified here.",
        )
        _emit(result, tool, request_id)
        return result

    if lookup.status is CaseLookupStatus.NOT_FOUND:
        category = ErrorCategory.NOT_FOUND
    elif lookup.status is CaseLookupStatus.TIMEOUT:
        category = ErrorCategory.TIMEOUT
    else:
        try:
            category = ErrorCategory(lookup.error_category) if lookup.error_category else ErrorCategory.PROVIDER_ERROR
        except ValueError:
            category = ErrorCategory.PROVIDER_ERROR
    detail = build_error_detail(
        tool,
        category,
        lookup.message or "case-status lookup failed",
        http_status=lookup.http_status,
        retryable=category is ErrorCategory.TIMEOUT,
    )
    note = CASE_TIMEOUT_NOTE if category is ErrorCategory.TIMEOUT else None
    result = CaseStatusResult(
        success=False,
        error_category=category,
        error=detail,
        request_id=request_id,
        duration_ms=_elapsed_ms(started),
        case_id=case_id,
        applicant_id=applicant_id,
        source=lookup.source,
        fetched_at=lookup.fetched_at,
        note=note,
    )
    _emit(result, tool, request_id, http_status=lookup.http_status)
    return result
