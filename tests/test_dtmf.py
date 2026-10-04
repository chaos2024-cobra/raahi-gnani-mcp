import pytest

from src.errors import ErrorCategory
from src.gnani_tools import navigate_ivr
from tests.fixtures import dtmf_accepted, dtmf_invalid_sequence, dtmf_menu_changed, timeout_error


async def test_dtmf_not_configured_by_default(sender, no_dtmf_url):
    result = await navigate_ivr(dtmf_sequence="12")
    assert result.success is False
    assert result.error_category is ErrorCategory.NOT_CONFIGURED
    assert "GNANI_DTMF_URL" in result.error.message
    assert result.error.retryable is False
    assert sender.requests == []


async def test_dtmf_valid_sequence_accepted(sender, monkeypatch):
    monkeypatch.setenv("GNANI_DTMF_URL", "https://gnani.example.test/dtmf")
    sender.push(dtmf_accepted(conversation_id="conv_123"))
    result = await navigate_ivr(dtmf_sequence="120", conversation_id="conv_123")
    assert result.success is True
    assert result.accepted is True
    assert result.conversation_id == "conv_123"
    assert result.provider_response is not None
    assert result.dtmf_sequence_masked == "***"
    request = sender.requests[0]
    assert request.url == "https://gnani.example.test/dtmf"
    assert request.json_body == {"conversation_id": "conv_123", "dtmf": "120"}
    assert request.headers.get("x-api-key") == "unit-test-gnani-key-not-real"


async def test_dtmf_star_and_hash_allowed(sender, monkeypatch):
    monkeypatch.setenv("GNANI_DTMF_URL", "https://gnani.example.test/dtmf")
    sender.push(dtmf_accepted())
    result = await navigate_ivr(dtmf_sequence="*0#", conversation_id="conv_1")
    assert result.success is True


async def test_dtmf_validation_letters_rejected(sender, monkeypatch):
    monkeypatch.setenv("GNANI_DTMF_URL", "https://gnani.example.test/dtmf")
    result = await navigate_ivr(dtmf_sequence="abc")
    assert result.error_category is ErrorCategory.VALIDATION_ERROR
    assert "never guessed" in result.error.message
    assert sender.requests == []


async def test_dtmf_validation_empty_sequence(sender, monkeypatch):
    monkeypatch.setenv("GNANI_DTMF_URL", "https://gnani.example.test/dtmf")
    result = await navigate_ivr(dtmf_sequence="  ")
    assert result.error_category is ErrorCategory.VALIDATION_ERROR
    assert sender.requests == []


async def test_dtmf_validation_bad_conversation_id(sender, monkeypatch):
    monkeypatch.setenv("GNANI_DTMF_URL", "https://gnani.example.test/dtmf")
    result = await navigate_ivr(dtmf_sequence="1", conversation_id="conv id!")
    assert result.error_category is ErrorCategory.VALIDATION_ERROR
    assert sender.requests == []


async def test_dtmf_menu_changed_is_explicit_failure(sender, monkeypatch):
    monkeypatch.setenv("GNANI_DTMF_URL", "https://gnani.example.test/dtmf")
    sender.push(dtmf_menu_changed())
    result = await navigate_ivr(dtmf_sequence="123", conversation_id="conv_123")
    assert result.success is False
    assert result.accepted is False
    assert result.error_category is ErrorCategory.VALIDATION_ERROR
    assert "menu structure changed" in result.error.message
    assert result.error.retryable is False
    assert len(sender.requests) == 1


async def test_dtmf_invalid_sequence_from_provider(sender, monkeypatch):
    monkeypatch.setenv("GNANI_DTMF_URL", "https://gnani.example.test/dtmf")
    sender.push(dtmf_invalid_sequence())
    result = await navigate_ivr(dtmf_sequence="999", conversation_id="conv_123")
    assert result.error_category is ErrorCategory.VALIDATION_ERROR
    assert result.error.http_status == 422


async def test_dtmf_timeout(sender, monkeypatch, fast_retries):
    monkeypatch.setenv("GNANI_DTMF_URL", "https://gnani.example.test/dtmf")
    sender.set_default(timeout_error())
    result = await navigate_ivr(dtmf_sequence="1", conversation_id="conv_1")
    assert result.error_category is ErrorCategory.TIMEOUT
    assert len(sender.requests) == 3
