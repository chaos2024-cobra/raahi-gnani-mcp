from src.errors import ErrorCategory
from src.gnani_tools import call_bank_rm_or_desk, read_call_outcome, transcribe_speech
from tests.fixtures import (
    json_response,
    stats_answered,
    stats_pending,
    timeout_error,
    trigger_400,
    trigger_success,
)

import base64

AUDIO_B64 = base64.b64encode(b"RIFF" + b"\x00" * 32).decode("ascii")


async def test_retry_limit_timeout_is_bounded(sender, fast_retries):
    sender.set_default(timeout_error())
    result = await transcribe_speech(audio=AUDIO_B64, language="en-IN")
    assert result.error_category is ErrorCategory.TIMEOUT
    assert len(sender.requests) == 3


async def test_retry_limit_configurable_to_zero(sender, monkeypatch):
    monkeypatch.setenv("GNANI_MAX_RETRIES", "0")
    sender.set_default(timeout_error())
    result = await transcribe_speech(audio=AUDIO_B64, language="en-IN")
    assert result.error_category is ErrorCategory.TIMEOUT
    assert len(sender.requests) == 1


async def test_retry_limit_recovers_after_transient_failures(sender, fast_retries):
    sender.push(timeout_error())
    sender.push(json_response({"error": {"message": "hiccup"}}, status=503))
    sender.push(json_response({"success": True, "request_id": "r_ok", "transcript": "done"}))
    result = await transcribe_speech(audio=AUDIO_B64, language="en-IN")
    assert result.success is True
    assert result.final_transcript == "done"
    assert len(sender.requests) == 3


async def test_no_retry_on_400(sender, fast_retries):
    sender.set_default(trigger_400())
    result = await call_bank_rm_or_desk(
        phone="9876543210",
        country_code="+91",
        name="RM",
        checklist_item_id="item-1",
    )
    assert result.error_category is ErrorCategory.VALIDATION_ERROR
    assert len(sender.requests) == 1


async def test_no_retry_on_403(sender, fast_retries):
    sender.push(json_response({"message": "forbidden"}, status=403))
    sender.set_default(trigger_success())
    result = await call_bank_rm_or_desk(
        phone="9876543210",
        country_code="+91",
        name="RM",
        checklist_item_id="item-1",
    )
    assert result.error_category is ErrorCategory.FORBIDDEN
    assert len(sender.requests) == 1


async def test_no_retry_on_429(sender, fast_retries):
    sender.set_default(json_response({"error": {"message": "slow down"}}, status=429))
    result = await transcribe_speech(audio=AUDIO_B64, language="en-IN")
    assert result.error_category is ErrorCategory.RATE_LIMITED
    assert len(sender.requests) == 1


async def test_analytics_polling_is_bounded(sender, monkeypatch):
    monkeypatch.setenv("GNANI_OUTCOME_POLL_MAX_ATTEMPTS", "2")
    monkeypatch.setenv("GNANI_OUTCOME_POLL_INTERVAL_SECONDS", "0")
    sender.push(stats_pending())
    sender.push(stats_pending())
    result = await read_call_outcome(conversation_id="conv_1")
    assert result.error_category is ErrorCategory.ANALYTICS_PENDING
    assert result.polls_attempted == 2
    assert len(sender.requests) == 2


async def test_analytics_polling_never_exceeds_configured_attempts(sender, monkeypatch):
    monkeypatch.setenv("GNANI_OUTCOME_POLL_MAX_ATTEMPTS", "1")
    monkeypatch.setenv("GNANI_OUTCOME_POLL_INTERVAL_SECONDS", "0")
    sender.set_default(stats_pending())
    result = await read_call_outcome(conversation_id="conv_1")
    assert result.error_category is ErrorCategory.ANALYTICS_PENDING
    assert len(sender.requests) == 1
