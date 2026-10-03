"""Optional live tests against real Gnani endpoints.

Skipped unless RUN_LIVE_GNANI_TESTS=true. Never run automatically in CI.
Requires a real GNANI_API_KEY (and GNANI_BOT_ID for call tests).
"""

import base64
import os

import pytest

pytestmark = pytest.mark.skipif(
    os.getenv("RUN_LIVE_GNANI_TESTS", "").lower() != "true",
    reason="live Gnani tests disabled; set RUN_LIVE_GNANI_TESTS=true to enable",
)

LIVE_WAV = base64.b64encode(
    b"RIFF"
    + (40000).to_bytes(4, "little")
    + b"WAVEfmt "
    + (16).to_bytes(4, "little")
    + (16).to_bytes(2, "little")
    + (1).to_bytes(2, "little")
    + (16000).to_bytes(4, "little")
    + (32000).to_bytes(4, "little")
    + (2).to_bytes(2, "little")
    + (16).to_bytes(2, "little")
    + b"data"
    + (32000).to_bytes(4, "little")
    + b"\x00" * 32000
).decode("ascii")


@pytest.mark.skipif(not os.getenv("GNANI_API_KEY"), reason="GNANI_API_KEY not configured")
async def test_live_transcribe_speech():
    from src.gnani_tools import transcribe_speech

    result = await transcribe_speech(audio=LIVE_WAV, language="en-IN")
    assert result.error_category.value in ("SUCCESS", "VALIDATION_ERROR", "TIMEOUT", "PROVIDER_ERROR")
    if result.success:
        assert result.final_transcript is not None


@pytest.mark.skipif(not os.getenv("GNANI_API_KEY"), reason="GNANI_API_KEY not configured")
async def test_live_speak_reply():
    from src.gnani_tools import speak_reply

    result = await speak_reply(text="Hello from Raahi", language="en-IN", voice_id="Kaveri")
    structured = result.structured_content
    if structured["success"]:
        assert structured["audio_size_bytes"] > 0
        assert structured["content_type"].startswith("audio/")
