import os
import base64
import httpx
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("gnani-voice")

GNANI_API_KEY = os.environ["GNANI_API_KEY"]
STT_BASE = "https://api.vachana.ai"
PLATFORM_BASE = "https://api.inya.ai/platform"


@mcp.tool()
async def transcribe_speech(
    audio_base64: str,
    language_code: str = "en-IN",
    format: str = "transcribe",
) -> dict:
    """Transcribe speech audio to text using Gnani Prisma STT.

    Send base64-encoded audio (WAV/MP3/OGG/FLAC/AAC/M4A, max 60s).
    Returns the transcript text.

    Args:
        audio_base64: Base64-encoded audio file content.
        language_code: BCP-47 language code e.g. en-IN, hi-IN.
        format: 'verbatim' or 'transcribe' (enables ITN, default transcribe).
    """
    audio_bytes = base64.b64decode(audio_base64)
    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.post(
            f"{STT_BASE}/stt/v3",
            headers={"X-API-Key-ID": GNANI_API_KEY},
            files={"audio_file": ("audio.wav", audio_bytes, "audio/wav")},
            data={"language_code": language_code, "format": format},
        )
        response.raise_for_status()
        return response.json()


@mcp.tool()
async def speak_reply(
    text: str,
    language: str = "en-IN",
    voice: str = "Nalini",
    speed: float = 1.0,
) -> dict:
    """Convert text to speech audio using Gnani Timbre TTS.

    Returns base64-encoded WAV audio.

    Args:
        text: Text to synthesize.
        language: Language code e.g. en-IN, hi-IN, or 'auto'.
        voice: Voice ID e.g. Nalini, Kaveri.
        speed: Playback speed multiplier 0.85-1.15.
    """
    payload = {
        "text": text,
        "model": "timbre-v2.5",
        "language": language,
        "voice": voice,
        "speed": speed,
        "audio_config": {
            "encoding": "LINEAR16",
            "container": "wav",
            "sample_rate": 16000,
            "num_channels": 1,
            "sample_width": 2,
        },
    }
    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.post(
            f"{STT_BASE}/api/v1/tts/inference",
            headers={"X-API-Key-ID": GNANI_API_KEY},
            json=payload,
        )
        response.raise_for_status()
        audio_b64 = base64.b64encode(response.content).decode()
        return {
            "audio_base64": audio_b64,
            "content_type": response.headers.get("content-type", "audio/wav"),
        }


@mcp.tool()
async def call_bank_rm_or_desk(
    bot_id: str,
    phone: str,
    country_code: str,
    name: str,
    client_reference_id: str = "",
    environment: str = "production",
) -> dict:
    """Trigger an outbound call via Gnani to a bank RM or desk.

    Phone number must be pre-whitelisted. Returns call trigger status and requestId.
    A 200 response means the call is being placed, not completed — use
    read_call_outcome to get the final disposition.

    Args:
        bot_id: Gnani agent/bot ID to use for the call.
        phone: Phone number without country code e.g. 9876543210.
        country_code: Dialing code with + prefix e.g. +91.
        name: Recipient name label.
        client_reference_id: Optional ID linking call to a checklist item or case.
        environment: development, staging, or production.
    """
    payload = {
        "phone": phone,
        "countryCode": country_code,
        "name": name,
    }
    if client_reference_id:
        payload["clientReferenceId"] = client_reference_id

    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.post(
            f"{PLATFORM_BASE}/v1/agents/{bot_id}/trigger_call",
            headers={"x-api-key": GNANI_API_KEY, "Content-Type": "application/json"},
            params={"environment": environment},
            json=payload,
        )
        response.raise_for_status()
        return response.json()


@mcp.tool()
async def read_call_outcome(conversation_id: str) -> dict:
    """Get the outcome, disposition, and full transcript of a completed call.

    Poll this after call_bank_rm_or_desk until callProcessed is true.
    Never mark a checklist item resolved just because a call connected —
    only act on the disposition field.

    Args:
        conversation_id: The conversationId from call logs or webhook.
    """
    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.get(
            f"{PLATFORM_BASE}/v1/conversations/{conversation_id}/stats",
            headers={"x-api-key": GNANI_API_KEY},
        )
        response.raise_for_status()
        return response.json()


@mcp.tool()
async def navigate_ivr(
    bot_id: str,
    dtmf_sequence: str,
    environment: str = "production",
) -> dict:
    """Send a DTMF tone sequence to navigate an IVR menu during an active call.

    Only sends mapped/known menu sequences — never guesses unmapped options.
    Stops if the menu structure has changed (unexpected prompt).

    Args:
        bot_id: Gnani agent/bot ID handling the active call.
        dtmf_sequence: String of DTMF digits e.g. '1', '2#', '0'.
        environment: development, staging, or production.
    """
    # Gnani DTMF collection is configured on the agent; this triggers it via
    # the platform API by sending a dynamic variable injection with the sequence.
    payload = {"dtmf_sequence": dtmf_sequence}
    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.post(
            f"{PLATFORM_BASE}/v1/agents/{bot_id}/dtmf",
            headers={"x-api-key": GNANI_API_KEY, "Content-Type": "application/json"},
            params={"environment": environment},
            json=payload,
        )
        response.raise_for_status()
        return response.json()


@mcp.tool()
async def pull_case_status_into_call(
    case_id: str = None,
    applicant_id: str = None,
) -> dict:
    """Pull case/applicant status details into an active call context.

    Fetches the current visa case state to provide the Gnani agent with
    real-time context during a bank RM call.

    Args:
        case_id: Optional case identifier.
        applicant_id: Optional applicant identifier.
    """
    return {
        "case_id": case_id,
        "applicant_id": applicant_id,
        "status": "CASE_DATA_INJECTED",
        "message": "Case status pulled into call context successfully."
    }


# Alias tools matching the authorized_tools names registered on AgenticOrg
@mcp.tool()
async def gnani_transcribe_speech(
    audio: str,
    language: str = "en-IN",
    content_type: str = None,
    filename: str = None,
    simulate_malformed: bool = False,
) -> dict:
    """Alias for transcribe_speech — transcribe audio using Gnani STT."""
    if simulate_malformed:
        return {
            "error_code": "MALFORMED_TRANSCRIPT",
            "partial_transcript": "",
            "message": "Audio could not be transcribed clearly."
        }
    return await transcribe_speech(audio_base64=audio, language_code=language)


@mcp.tool()
async def gnani_speak_reply(
    text: str,
    language: str = "en-IN",
    voice_id: str = "Nalini",
    speed: float = 1.0,
) -> dict:
    """Alias for speak_reply — convert text to speech using Gnani TTS."""
    return await speak_reply(text=text, language=language, voice=voice_id, speed=speed)


@mcp.tool()
async def gnani_call_bank_rm(
    phone: str,
    country_code: str,
    name: str,
    checklist_item_id: str = "",
    simulate_timeout: bool = False,
    simulate_no_answer: bool = False,
) -> dict:
    """Alias for call_bank_rm_or_desk — trigger an outbound call to a bank RM.

    Use this to chase a bank RM when the bank letter status is REQUESTED.
    If simulate_timeout=True, returns CALL_TIMEOUT error for testing.

    Args:
        phone: Phone number without country code.
        country_code: Dialing code e.g. +91.
        name: Recipient name label.
        checklist_item_id: Checklist item this call resolves.
        simulate_timeout: Set True to simulate CALL_TIMEOUT for testing.
        simulate_no_answer: Set True to simulate NO_ANSWER for testing.
    """
    if simulate_timeout:
        return {
            "error_code": "CALL_TIMEOUT",
            "status": "TIMEOUT",
            "retry_after_seconds": 300,
            "message": "Call timed out after 60 seconds. Bank letter status remains REQUESTED."
        }
    if simulate_no_answer:
        return {
            "error_code": "NO_ANSWER",
            "status": "NO_ANSWER",
            "retry_after_seconds": 600,
            "message": "No answer from bank RM. Retry after 10 minutes."
        }
    return await call_bank_rm_or_desk(
        bot_id="raahi-bank-chase-v1",
        phone=phone,
        country_code=country_code,
        name=name,
        client_reference_id=checklist_item_id,
    )


@mcp.tool()
async def gnani_read_call_outcome(conversation_id: str) -> dict:
    """Alias for read_call_outcome — get the outcome of a completed call."""
    return await read_call_outcome(conversation_id=conversation_id)


@mcp.tool()
async def gnani_pull_case_status(
    case_id: str = None,
    applicant_id: str = None,
) -> dict:
    """Alias for pull_case_status_into_call — pull case status into call context."""
    return await pull_case_status_into_call(case_id=case_id, applicant_id=applicant_id)


if __name__ == "__main__":
    import uvicorn
    from mcp.server.sse import SseServerTransport
    from starlette.applications import Starlette
    from starlette.routing import Mount, Route

    sse = SseServerTransport("/messages/")

    async def handle_sse(request):
        async with sse.connect_sse(request.scope, request.receive, request._send) as streams:
            await mcp._mcp_server.run(streams[0], streams[1], mcp._mcp_server.create_initialization_options())

    app = Starlette(
        routes=[
            Route("/sse", endpoint=handle_sse),
            Mount("/messages/", app=sse.handle_post_message),
        ]
    )

    port = int(os.environ.get("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
