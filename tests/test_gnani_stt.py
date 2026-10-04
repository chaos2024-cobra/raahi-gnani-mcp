import asyncio

from src.errors import ErrorCategory
from src.gnani_tools import transcribe_speech
from tests.fixtures import FakeSender, json_response, stt_final, stt_interim_final, stt_partial_error, timeout_error

import base64

AUDIO_B64 = base64.b64encode(b"RIFF" + b"\x00" * 256).decode("ascii")


async def test_stt_validation_empty_audio(sender):
    result = await transcribe_speech(audio="", language="hi-IN")
    assert result.success is False
    assert result.error_category is ErrorCategory.VALIDATION_ERROR
    assert result.error is not None
    assert result.error.retryable is False
    assert sender.requests == []


async def test_stt_validation_invalid_base64(sender):
    result = await transcribe_speech(audio="not valid base64 !!!", language="hi-IN")
    assert result.error_category is ErrorCategory.VALIDATION_ERROR
    assert "base64" in result.error.message
    assert sender.requests == []


async def test_stt_validation_bad_language(sender):
    result = await transcribe_speech(audio=AUDIO_B64, language="12345")
    assert result.error_category is ErrorCategory.VALIDATION_ERROR
    assert sender.requests == []


async def test_stt_validation_oversize_audio(sender, monkeypatch):
    monkeypatch.setenv("GNANI_MAX_AUDIO_BYTES", "1024")
    oversize = base64.b64encode(b"RIFF" + b"\x00" * 4096).decode("ascii")
    result = await transcribe_speech(audio=oversize, language="hi-IN")
    assert result.error_category is ErrorCategory.VALIDATION_ERROR
    assert "maximum size" in result.error.message
    assert sender.requests == []


async def test_stt_validation_path_traversal_filename(sender):
    result = await transcribe_speech(
        audio=AUDIO_B64, language="hi-IN", filename="../../etc/passwd"
    )
    assert result.error_category is ErrorCategory.VALIDATION_ERROR
    assert sender.requests == []


async def test_stt_success_final_transcript(sender):
    sender.push(stt_final("jaipur jana hai", request_id="req_final_1"))
    result = await transcribe_speech(audio=AUDIO_B64, language="hi-IN")
    assert result.success is True
    assert result.error_category is ErrorCategory.SUCCESS
    assert result.final_transcript == "jaipur jana hai"
    assert result.interim_transcript is None
    assert result.interim_available is False
    assert result.provider_request_id == "req_final_1"
    request = sender.requests[0]
    assert request.method == "POST"
    assert request.url.endswith("/stt/v3")
    assert request.headers.get("X-API-Key-ID") == "unit-test-gnani-key-not-real"
    assert request.data == {"language_code": "hi-IN"}
    assert request.files["audio_file"][0] == "audio.wav"


async def test_stt_interim_and_final_preserved(sender):
    sender.push(stt_interim_final("jaipur jana", "jaipur jana hai"))
    result = await transcribe_speech(audio=AUDIO_B64, language="hi-IN")
    assert result.success is True
    assert result.interim_transcript == "jaipur jana"
    assert result.final_transcript == "jaipur jana hai"
    assert result.interim_available is True


async def test_stt_partial_transcript_with_error(sender):
    sender.push(stt_partial_error("मुझे जाना है ..."))
    result = await transcribe_speech(audio=AUDIO_B64, language="hi-IN")
    assert result.success is False
    assert result.error_category is ErrorCategory.VALIDATION_ERROR
    assert result.error.http_status == 400
    assert result.partial is True
    assert result.partial_transcript == "मुझे जाना है ..."


async def test_stt_provider_timeout(sender, fast_retries):
    sender.set_default(timeout_error())
    result = await transcribe_speech(audio=AUDIO_B64, language="hi-IN")
    assert result.error_category is ErrorCategory.TIMEOUT
    assert result.error.retryable is True
    assert len(sender.requests) == 3


async def test_stt_provider_error_401(sender):
    sender.push(json_response({"success": False, "error": {"type": "AUTH", "message": "bad key"}}, status=401))
    result = await transcribe_speech(audio=AUDIO_B64, language="hi-IN")
    assert result.error_category is ErrorCategory.AUTH_ERROR
    assert result.error.http_status == 401
    assert len(sender.requests) == 1


async def test_stt_provider_5xx_retried_bounded(sender, fast_retries):
    sender.set_default(json_response({"error": {"message": "boom"}}, status=503))
    result = await transcribe_speech(audio=AUDIO_B64, language="hi-IN")
    assert result.error_category is ErrorCategory.PROVIDER_ERROR
    assert result.error.http_status == 503
    assert result.error.retryable is True
    assert len(sender.requests) == 3


async def test_stt_malformed_response(sender):
    sender.push(json_response({"success": True, "request_id": "r1"}))
    result = await transcribe_speech(audio=AUDIO_B64, language="hi-IN")
    assert result.error_category is ErrorCategory.MALFORMED_RESPONSE
    assert result.final_transcript is None


async def test_stt_never_invents_content(sender):
    sender.push(stt_final("delhi"))
    result = await transcribe_speech(audio=AUDIO_B64, language="en-IN")
    assert result.final_transcript == "delhi"
    provider_content = " ".join(
        part
        for part in (result.final_transcript, result.interim_transcript, result.partial_transcript)
        if part
    )
    for forbidden in ("2025", "amount", "₹", "date", "phone", "raipur"):
        assert forbidden not in provider_content


async def test_stt_missing_api_key(monkeypatch, sender):
    monkeypatch.delenv("GNANI_API_KEY", raising=False)
    result = await transcribe_speech(audio=AUDIO_B64, language="hi-IN")
    assert result.error_category is ErrorCategory.AUTH_ERROR
    assert "GNANI_API_KEY" in result.error.message
    assert sender.requests == []
