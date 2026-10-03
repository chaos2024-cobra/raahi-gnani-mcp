"""
Gnani MCP server — the Voice rail for Raahi (capabilities 1-6 in KEN.pdf).

  1. transcribe_speech            Gnani Vachana STT (REST)            VERIFIED endpoint
  2. speak_reply                  Gnani Timbre TTS (REST)             VERIFIED endpoint
  3. call_bank_rm_or_desk         Gnani/Inya Trigger Call API         ASSUMED endpoint (env-configurable)
  4. read_call_outcome            Gnani/Inya Conversation Statistics  ASSUMED endpoint (env-configurable)
  5. navigate_ivr                 Gnani DTMF                          ASSUMED endpoint (env-configurable)
  6. pull_case_status_into_call   YOUR backend (CASE_STATUS_URL)      you build this endpoint

"VERIFIED" = taken from Gnani's official `gnani-vachana` Python SDK.
"ASSUMED"  = Gnani's call-trigger / statistics / DTMF APIs are not publicly documented;
             confirm paths and payloads with your Gnani account manager and set the env vars.

Guardrails from the "What it must never do" column are enforced in code, not just in prompts.
"""

from __future__ import annotations

import base64
import json
import os
import re
import uuid
from pathlib import Path
from typing import Any

import httpx
from mcp.server.fastmcp import FastMCP

# --------------------------------------------------------------------------- config
API_KEY = os.getenv("GNANI_API_KEY", "")
VACHANA_BASE = os.getenv("GNANI_BASE_URL", "https://api.vachana.ai").rstrip("/")

# Call platform (Inya = Gnani's agent/telephony platform). ASSUMED — set from your account docs.
CALL_BASE = os.getenv("GNANI_CALL_BASE_URL", "https://api.inya.ai").rstrip("/")
CALL_TRIGGER_PATH = os.getenv("GNANI_CALL_TRIGGER_PATH", "/v1/calls/trigger")
CALL_STATS_PATH = os.getenv("GNANI_CALL_STATS_PATH", "/v1/conversations/{conversation_id}/statistics")
CALL_DTMF_PATH = os.getenv("GNANI_CALL_DTMF_PATH", "/v1/calls/{conversation_id}/dtmf")
CALL_AUTH_HEADER = os.getenv("GNANI_CALL_AUTH_HEADER", "X-API-Key-ID")  # some Inya setups use Authorization
EXTRA_CALL_HEADERS = json.loads(os.getenv("GNANI_CALL_EXTRA_HEADERS", "{}"))  # e.g. org / user IDs

# Guardrail data
ALLOWED_NUMBERS = {n.strip() for n in os.getenv("GNANI_ALLOWED_NUMBERS", "").split(",") if n.strip()}
IVR_MENUS_FILE = os.getenv("GNANI_IVR_MENUS_FILE", "")  # JSON: {"+9180...": {"menu_id": {"1": "...", "2": "..."}}}
CASE_STATUS_URL = os.getenv("CASE_STATUS_URL", "")  # e.g. https://api.raahi.app/cases/{case_id}/checklist
CASE_STATUS_TIMEOUT = float(os.getenv("CASE_STATUS_TIMEOUT_SECONDS", "3"))
CASE_STATUS_TOKEN = os.getenv("CASE_STATUS_TOKEN", "")

AUDIO_OUT_DIR = Path(os.getenv("GNANI_AUDIO_OUT_DIR", "./audio_out"))

STT_LANGS = {
    "bn-IN", "en-IN", "gu-IN", "hi-IN", "kn-IN",
    "ml-IN", "mr-IN", "pa-IN", "ta-IN", "te-IN",
}
STT_EXTS = {".wav", ".mp3", ".flac", ".ogg", ".m4a", ".aac"}
TTS_MODELS = {"timbre-v2.0", "timbre-v2.5"}
TTS_V20_VOICES = {"Pranav", "Kaveri", "Shubhra", "Deepak"}

mcp = FastMCP("gnani", host="0.0.0.0", stateless_http=True)


# --------------------------------------------------------------------------- helpers
def _err(code: str, message: str, **extra: Any) -> dict[str, Any]:
    """Uniform failure shape: tools return errors, never guesses."""
    return {"ok": False, "error_code": code, "error": message, **extra}


def _need_key() -> dict[str, Any] | None:
    if not API_KEY:
        return _err("NO_API_KEY", "GNANI_API_KEY is not set.")
    return None


def _vachana_headers() -> dict[str, str]:
    return {"X-API-Key-ID": API_KEY, "X-API-Request-ID": str(uuid.uuid4())}


def _call_headers() -> dict[str, str]:
    return {
        CALL_AUTH_HEADER: API_KEY,
        "Content-Type": "application/json",
        "X-API-Request-ID": str(uuid.uuid4()),
        **EXTRA_CALL_HEADERS,
    }


def _digits(s: str) -> str:
    return re.sub(r"\D", "", s)


def _e164(country_code: str, phone: str) -> str:
    return f"+{_digits(country_code)}{_digits(phone)}"


def _number_allowed(e164: str) -> bool:
    return e164 in ALLOWED_NUMBERS or e164.lstrip("+") in {n.lstrip("+") for n in ALLOWED_NUMBERS}


def _load_menus() -> dict[str, Any]:
    if not IVR_MENUS_FILE:
        return {}
    try:
        return json.loads(Path(IVR_MENUS_FILE).read_text())
    except Exception:
        return {}


# --------------------------------------------------------------------------- 1. transcribe_speech
@mcp.tool()
async def transcribe_speech(
    audio_path: str | None = None,
    audio_base64: str | None = None,
    filename: str = "audio.wav",
    language_code: str = "en-IN",
    format: str = "transcribe",
) -> dict[str, Any]:
    """Transcribe a recorded audio clip (Intake). Provide audio_path OR audio_base64.

    Max ~60 s per clip on the REST endpoint; audio is auto-converted to 16 kHz mono.
    language_code: one of bn-IN en-IN gu-IN hi-IN kn-IN ml-IN mr-IN pa-IN ta-IN te-IN.
    format: 'transcribe' (numbers/dates/currency normalised, hi-IN & en-IN only) or 'verbatim'.

    Returns the transcript plus `needs_confirmation=True`. Callers must read destination,
    dates and amounts back to the applicant and get a yes before using them; this tool
    never infers or fills in unclear values.
    """
    if (e := _need_key()):
        return e
    if language_code not in STT_LANGS:
        return _err("BAD_LANGUAGE", f"language_code must be one of {sorted(STT_LANGS)}")
    if format not in ("verbatim", "transcribe"):
        return _err("BAD_FORMAT", "format must be 'verbatim' or 'transcribe'")
    if format == "transcribe" and language_code not in ("hi-IN", "en-IN"):
        format = "verbatim"  # ITN only supported for hi-IN / en-IN

    if audio_path:
        p = Path(audio_path)
        if not p.exists():
            return _err("FILE_NOT_FOUND", f"No such file: {audio_path}")
        if p.suffix.lower() not in STT_EXTS:
            return _err("BAD_AUDIO", f"Unsupported extension {p.suffix}; use {sorted(STT_EXTS)}")
        data, filename = p.read_bytes(), p.name
    elif audio_base64:
        try:
            data = base64.b64decode(audio_base64, validate=True)
        except Exception:
            return _err("BAD_AUDIO", "audio_base64 is not valid base64")
    else:
        return _err("NO_AUDIO", "Provide audio_path or audio_base64")

    try:
        async with httpx.AsyncClient(timeout=60) as c:
            r = await c.post(
                f"{VACHANA_BASE}/stt/v3",
                headers=_vachana_headers(),
                files={"audio_file": (filename, data)},
                data={"language_code": language_code, "format": format},
            )
    except httpx.HTTPError as ex:
        return _err("NETWORK", str(ex))

    if r.status_code != 200:
        return _err(f"HTTP_{r.status_code}", r.text[:500])
    body = r.json()
    transcript = (body.get("transcript") or "").strip()
    if not transcript:
        return _err("EMPTY_TRANSCRIPT", "No speech recognised; ask the applicant to repeat.", partial=body)
    return {
        "ok": True,
        "transcript": transcript,
        "request_id": body.get("request_id"),
        "language_code": language_code,
        "needs_confirmation": True,
    }


# --------------------------------------------------------------------------- 2. speak_reply
_FACT_PATTERN = re.compile(r"[\d₹$€£]|\b(rupees?|dollars?|euros?|lakh|crore)\b", re.I)


@mcp.tool()
async def speak_reply(
    text: str,
    voice: str = "Pranav",
    model: str = "timbre-v2.0",
    language: str | None = None,
    speed: float | None = None,
    sample_rate: int = 8000,
    container: str = "wav",
    facts_verified: bool = False,
    output_path: str | None = None,
) -> dict[str, Any]:
    """Synthesise a spoken reply with Gnani Timbre TTS and save it to a file.

    Guardrail: if `text` contains any number, date or amount, `facts_verified` must be True
    (i.e. every figure came from a verified source such as DigiLocker, AA, or a call outcome).
    Otherwise the call errors instead of speaking possibly stale/unverified data.

    model 'timbre-v2.0' voices: Pranav, Kaveri, Shubhra, Deepak.
    model 'timbre-v2.5': larger voice catalogue, supports `language` (e.g. hi-IN) and `speed` (0.85-1.15).
    container: wav | mp3 | raw | mulaw | alaw. Use mulaw/alaw at 8000 Hz for telephony.
    """
    if (e := _need_key()):
        return e
    if not text.strip():
        return _err("EMPTY_TEXT", "text is empty")
    if _FACT_PATTERN.search(text) and not facts_verified:
        return _err(
            "UNVERIFIED_FACTS",
            "Text contains a number/date/amount but facts_verified=False. "
            "Refusing to speak unverified document, date or amount details.",
        )
    if model not in TTS_MODELS:
        return _err("BAD_MODEL", f"model must be one of {sorted(TTS_MODELS)}")
    if model == "timbre-v2.0" and voice not in TTS_V20_VOICES:
        return _err("BAD_VOICE", f"timbre-v2.0 voices: {sorted(TTS_V20_VOICES)}")

    body: dict[str, Any] = {
        "text": text,
        "model": model,
        "voice": voice,
        "audio_config": {
            "sample_rate": sample_rate,
            "encoding": "linear_pcm",
            "num_channels": 1,
            "sample_width": 2,
            "container": container,
        },
    }
    if model == "timbre-v2.5":
        if language:
            body["language"] = language
        body["speed"] = speed if speed is not None else 1.0

    headers = {**_vachana_headers(), "Content-Type": "application/json"}
    try:
        async with httpx.AsyncClient(timeout=60) as c:
            r = await c.post(f"{VACHANA_BASE}/api/v1/tts/inference", headers=headers, json=body)
    except httpx.HTTPError as ex:
        return _err("NETWORK", str(ex))
    if r.status_code != 200:
        return _err(f"HTTP_{r.status_code}", r.text[:500])  # error, never stale/cached audio

    ext = {"mp3": "mp3", "wav": "wav", "raw": "pcm", "mulaw": "ulaw", "alaw": "alaw"}.get(container, "bin")
    out = Path(output_path) if output_path else AUDIO_OUT_DIR / f"reply_{uuid.uuid4().hex[:8]}.{ext}"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(r.content)
    return {"ok": True, "audio_path": str(out.resolve()), "bytes": len(r.content), "container": container}


@mcp.tool()
def list_tts_voices(model: str = "timbre-v2.0") -> dict[str, Any]:
    """List known TTS voice IDs. For timbre-v2.5 see https://docs.gnani.ai/api/TTS/tts-sse#available-voices"""
    if model == "timbre-v2.0":
        return {"ok": True, "voices": sorted(TTS_V20_VOICES)}
    return {"ok": True, "voices": [], "note": "See Gnani docs for the full timbre-v2.5 catalogue."}


# --------------------------------------------------------------------------- 3. call_bank_rm_or_desk
@mcp.tool()
async def call_bank_rm_or_desk(
    phone: str,
    country_code: str,
    name: str,
    checklist_item_id: str,
    extra_variables: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Trigger an outbound AI call to a bank RM / desk (GapResolution -> VoiceChase).

    Guardrail: the number must be in GNANI_ALLOWED_NUMBERS (applicant-approved / confirmed
    numbers). Anything else is refused locally, before any API call is made.
    Returns a conversation_id; use read_call_outcome later. Connecting != resolved.
    """
    if (e := _need_key()):
        return e
    e164 = _e164(country_code, phone)
    if not _number_allowed(e164):
        return _err("NUMBER_NOT_APPROVED", f"{e164} is not on the approved list; hand back to applicant.")

    payload = {
        "phone": _digits(phone),
        "countryCode": f"+{_digits(country_code)}",
        "name": name,
        "variables": {"checklist_item_id": checklist_item_id, **(extra_variables or {})},
    }
    try:
        async with httpx.AsyncClient(timeout=30) as c:
            r = await c.post(f"{CALL_BASE}{CALL_TRIGGER_PATH}", headers=_call_headers(), json=payload)
    except httpx.HTTPError as ex:
        return _err("NETWORK", str(ex))

    if r.status_code in (400, 403):
        return _err(f"HTTP_{r.status_code}", "Invalid or unwhitelisted number at Gnani.", detail=r.text[:500])
    if r.status_code >= 300:
        return _err(f"HTTP_{r.status_code}", r.text[:500])
    body = r.json() if r.content else {}
    return {"ok": True, "triggered": True, "checklist_item_id": checklist_item_id, "gnani_response": body}


# --------------------------------------------------------------------------- 4. read_call_outcome
@mcp.tool()
async def read_call_outcome(conversation_id: str) -> dict[str, Any]:
    """Fetch status, disposition and transcript for a finished call (after VoiceChase).

    If analytics are not complete the result has pending=True: poll again or use a webhook.
    `item_resolved` is ALWAYS False here: a connected call never marks a checklist item
    resolved. Decide that from the disposition + transcript, with the applicant if unclear.
    """
    if (e := _need_key()):
        return e
    path = CALL_STATS_PATH.format(conversation_id=conversation_id)
    try:
        async with httpx.AsyncClient(timeout=30) as c:
            r = await c.get(f"{CALL_BASE}{path}", headers=_call_headers())
    except httpx.HTTPError as ex:
        return _err("NETWORK", str(ex))
    if r.status_code == 404:
        return {"ok": True, "pending": True, "item_resolved": False, "note": "Not available yet; retry."}
    if r.status_code != 200:
        return _err(f"HTTP_{r.status_code}", r.text[:500])

    b = r.json()
    status = b.get("status") or b.get("callStatus")
    disposition = b.get("disposition")
    transcript = b.get("transcript")
    if not disposition and not transcript:
        return {"ok": True, "pending": True, "item_resolved": False, "status": status,
                "note": "Analytics incomplete; poll again."}
    return {
        "ok": True, "pending": False, "status": status, "disposition": disposition,
        "transcript": transcript, "item_resolved": False, "raw": b,
    }


# --------------------------------------------------------------------------- 5. navigate_ivr
@mcp.tool()
async def navigate_ivr(
    conversation_id: str,
    dtmf: str,
    phone: str,
    menu_id: str,
) -> dict[str, Any]:
    """Send a DTMF sequence on a live IVR call.

    Guardrail: every key must be mapped for (phone, menu_id) in GNANI_IVR_MENUS_FILE.
    Unmapped options are refused, and if the menu_id is unknown (menu changed) it stops.
    """
    if (e := _need_key()):
        return e
    if not re.fullmatch(r"[0-9*#]+", dtmf or ""):
        return _err("BAD_DTMF", "dtmf may contain only 0-9, * and #")
    menus = _load_menus()
    menu = menus.get(phone, {}).get(menu_id) or menus.get(_digits(phone), {}).get(menu_id)
    if not menu:
        return _err("MENU_UNMAPPED", f"No mapped menu '{menu_id}' for {phone}; stopping.")
    unmapped = [k for k in dtmf if k not in menu]
    if unmapped:
        return _err("OPTION_UNMAPPED", f"Keys {unmapped} are not mapped in menu '{menu_id}'; stopping.")

    path = CALL_DTMF_PATH.format(conversation_id=conversation_id)
    try:
        async with httpx.AsyncClient(timeout=15) as c:
            r = await c.post(f"{CALL_BASE}{path}", headers=_call_headers(), json={"digits": dtmf})
    except httpx.HTTPError as ex:
        return _err("NETWORK", str(ex))
    if r.status_code >= 300:
        return _err(f"HTTP_{r.status_code}", r.text[:500])
    return {"ok": True, "sent": dtmf, "menu_id": menu_id, "gnani_response": r.json() if r.content else {}}


# --------------------------------------------------------------------------- 6. pull_case_status_into_call
@mcp.tool()
async def pull_case_status_into_call(case_id: str) -> dict[str, Any]:
    """Fetch live checklist status from OUR backend for use mid-call (VoiceChase).

    Hard timeout (CASE_STATUS_TIMEOUT_SECONDS, default 3s). On timeout/failure it returns
    degraded=True so the call carries on rather than leaving the caller waiting.
    Requires CASE_STATUS_URL, e.g. https://api.yourapp.com/cases/{case_id}/checklist
    """
    if not CASE_STATUS_URL:
        return {"ok": False, "degraded": True, "error_code": "NOT_CONFIGURED",
                "error": "CASE_STATUS_URL not set; continue the call without live status."}
    headers = {"Authorization": f"Bearer {CASE_STATUS_TOKEN}"} if CASE_STATUS_TOKEN else {}
    try:
        async with httpx.AsyncClient(timeout=CASE_STATUS_TIMEOUT) as c:
            r = await c.get(CASE_STATUS_URL.format(case_id=case_id), headers=headers)
        if r.status_code != 200:
            return {"ok": False, "degraded": True, "error_code": f"HTTP_{r.status_code}",
                    "error": "Backend error; continue the call."}
        return {"ok": True, "degraded": False, "checklist": r.json()}
    except httpx.TimeoutException:
        return {"ok": False, "degraded": True, "error_code": "TIMEOUT",
                "error": "Backend timed out; continue the call without live status."}
    except httpx.HTTPError as ex:
        return {"ok": False, "degraded": True, "error_code": "NETWORK", "error": str(ex)}


# --------------------------------------------------------------------------- HTTP app (Render / any host)
# Run:  uvicorn server:app --host 0.0.0.0 --port $PORT
# MCP endpoint:  https://<your-service>.onrender.com/mcp
# Set MCP_AUTH_TOKEN so only your clients can use it (this server can place calls and spend credits).
MCP_AUTH_TOKEN = os.getenv("MCP_AUTH_TOKEN", "")


class _Guard:
    """Bearer-token check + /health endpoint in front of the MCP app."""

    def __init__(self, inner):
        self.inner = inner

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http":
            if scope["path"] == "/health":
                await send({"type": "http.response.start", "status": 200,
                            "headers": [(b"content-type", b"text/plain")]})
                await send({"type": "http.response.body", "body": b"ok"})
                return
            if MCP_AUTH_TOKEN:
                hdrs = dict(scope["headers"])
                if hdrs.get(b"authorization", b"").decode() != f"Bearer {MCP_AUTH_TOKEN}":
                    await send({"type": "http.response.start", "status": 401,
                                "headers": [(b"content-type", b"text/plain")]})
                    await send({"type": "http.response.body", "body": b"unauthorized"})
                    return
        await self.inner(scope, receive, send)


app = _Guard(mcp.streamable_http_app())

if __name__ == "__main__":
    mcp.run()  # stdio transport (local use, e.g. Claude Desktop)
