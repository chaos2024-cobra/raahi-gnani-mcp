import base64
import json

from src.errors import ErrorCategory
from src.gnani_tools import speak_reply
from tests.fixtures import FakeSender, json_response, timeout_error, tts_audio, tts_error

WAV_BYTES = b"RIFF" + b"\x00" * 128


def _structured(result) -> dict:
    return result.structured_content


async def test_tts_validation_empty_text(sender):
    result = await speak_reply(text="   ", language="hi-IN", voice_id="Nalini")
    assert _structured(result)["success"] is False
    assert _structured(result)["error_category"] == "VALIDATION_ERROR"
    assert sender.requests == []


async def test_tts_validation_missing_voice(sender):
    result = await speak_reply(text="namaste", language="hi-IN", voice_id="")
    assert _structured(result)["error_category"] == "VALIDATION_ERROR"
    assert sender.requests == []


async def test_tts_validation_bad_speed(sender):
    result = await speak_reply(text="namaste", language="hi-IN", voice_id="Nalini", speed=3.5)
    assert _structured(result)["error_category"] == "VALIDATION_ERROR"
    assert sender.requests == []


async def test_tts_validation_bad_language(sender):
    result = await speak_reply(text="namaste", language="!!", voice_id="Nalini")
    assert _structured(result)["error_category"] == "VALIDATION_ERROR"
    assert sender.requests == []


async def test_tts_validation_overlong_text(sender, monkeypatch):
    monkeypatch.setenv("GNANI_MAX_TTS_TEXT_CHARS", "5")
    result = await speak_reply(text="this text is far too long", language="hi-IN", voice_id="Nalini")
    assert _structured(result)["error_category"] == "VALIDATION_ERROR"
    assert sender.requests == []


async def test_tts_success_returns_audio(sender):
    sender.push(tts_audio(WAV_BYTES))
    result = await speak_reply(text="namaste doston", language="hi-IN", voice_id="Nalini")
    structured = _structured(result)
    assert structured["success"] is True
    assert structured["content_type"] == "audio/wav"
    assert structured["audio_size_bytes"] == len(WAV_BYTES)
    assert base64.b64decode(structured["audio_base64"]) == WAV_BYTES
    assert structured["streaming"] is False
    assert structured["model"] == "timbre-v2.5"
    assert [block.type for block in result.content] == ["audio", "text"]
    audio_block = result.content[0]
    assert audio_block.mime_type == "audio/wav"
    assert base64.b64decode(audio_block.data) == WAV_BYTES
    request = sender.requests[0]
    assert request.url.endswith("/api/v1/tts/inference")
    assert request.headers.get("X-API-Key-ID") == "unit-test-gnani-key-not-real"
    body = request.json_body
    assert body["text"] == "namaste doston"
    assert body["voice"] == "Nalini"
    assert body["language"] == "hi-IN"
    assert body["model"] == "timbre-v2.5"


async def test_tts_provider_error(sender):
    sender.push(tts_error())
    result = await speak_reply(text="namaste", language="hi-IN", voice_id="Nalini")
    structured = _structured(result)
    assert structured["success"] is False
    assert structured["error_category"] == "VALIDATION_ERROR"
    assert structured["audio_base64"] is None
    assert structured["error"]["http_status"] == 400


async def test_tts_provider_timeout(sender, fast_retries):
    sender.set_default(timeout_error())
    result = await speak_reply(text="namaste", language="hi-IN", voice_id="Nalini")
    structured = _structured(result)
    assert structured["error_category"] == "TIMEOUT"
    assert structured["error"]["retryable"] is True
    assert len(sender.requests) == 3


async def test_tts_rate_limited_no_retry(sender):
    sender.push(json_response({"error": {"type": "RATE_LIMIT_ERROR", "message": "slow down"}}, status=429))
    result = await speak_reply(text="namaste", language="hi-IN", voice_id="Nalini")
    structured = _structured(result)
    assert structured["error_category"] == "RATE_LIMITED"
    assert len(sender.requests) == 1


async def test_tts_forbidden(sender):
    sender.push(json_response({"error": {"type": "FORBIDDEN", "message": "no credits"}}, status=403))
    result = await speak_reply(text="namaste", language="hi-IN", voice_id="Nalini")
    assert _structured(result)["error_category"] == "FORBIDDEN"


async def test_tts_empty_body_is_malformed(sender):
    from tests.fixtures import raw_response

    sender.push(raw_response(b"", content_type="audio/wav"))
    result = await speak_reply(text="namaste", language="hi-IN", voice_id="Nalini")
    assert _structured(result)["error_category"] == "MALFORMED_RESPONSE"
