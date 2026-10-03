from src.errors import ErrorCategory
from src.gnani_tools import call_bank_rm_or_desk, read_call_outcome
from tests.fixtures import (
    json_response,
    stats_answered,
    stats_empty,
    stats_no_answer,
    stats_not_found,
    stats_pending,
    timeout_error,
    trigger_400,
    trigger_403,
    trigger_success,
)

VALID_ARGS = {
    "phone": "9876543210",
    "country_code": "+91",
    "name": "Bank RM Desk",
    "checklist_item_id": "bank-letter-1",
}


async def test_trigger_validation_empty_phone(sender):
    result = await call_bank_rm_or_desk(**{**VALID_ARGS, "phone": ""})
    assert result.error_category is ErrorCategory.VALIDATION_ERROR
    assert result.error.retryable is False
    assert sender.requests == []


async def test_trigger_validation_letters_in_phone(sender):
    result = await call_bank_rm_or_desk(**{**VALID_ARGS, "phone": "CALL-ME-NOW"})
    assert result.error_category is ErrorCategory.VALIDATION_ERROR
    assert sender.requests == []


async def test_trigger_validation_bad_country_code(sender):
    result = await call_bank_rm_or_desk(**{**VALID_ARGS, "country_code": "91"})
    assert result.error_category is ErrorCategory.VALIDATION_ERROR
    assert sender.requests == []


async def test_trigger_validation_missing_name(sender):
    result = await call_bank_rm_or_desk(**{**VALID_ARGS, "name": "   "})
    assert result.error_category is ErrorCategory.VALIDATION_ERROR
    assert sender.requests == []


async def test_trigger_validation_bad_checklist_id(sender):
    result = await call_bank_rm_or_desk(**{**VALID_ARGS, "checklist_item_id": "item with spaces!"})
    assert result.error_category is ErrorCategory.VALIDATION_ERROR
    assert sender.requests == []


async def test_trigger_rejects_unapproved_number_locally(sender, monkeypatch):
    monkeypatch.setenv("GNANI_ALLOWED_NUMBERS", "+919800000000")
    result = await call_bank_rm_or_desk(**VALID_ARGS)
    assert result.error_category is ErrorCategory.FORBIDDEN
    assert "GNANI_ALLOWED_NUMBERS" in result.error.message
    assert result.error.retryable is False
    assert sender.requests == []


async def test_trigger_accepts_approved_number(sender, monkeypatch):
    monkeypatch.setenv("GNANI_ALLOWED_NUMBERS", "+919876543210")
    sender.push(trigger_success())
    result = await call_bank_rm_or_desk(**VALID_ARGS)
    assert result.success is True
    assert len(sender.requests) == 1


async def test_trigger_success_reports_trigger_only(sender):
    sender.push(trigger_success(client_reference_id="bank-letter-1"))
    result = await call_bank_rm_or_desk(**VALID_ARGS)
    assert result.success is True
    assert result.trigger_status == "success"
    assert result.current_status == "TRIGGERED"
    assert result.disposition is None
    assert result.transcript is None
    assert result.conversation_id is None
    assert result.client_reference_id == "bank-letter-1"
    assert "does not resolve any checklist item" in result.note
    request = sender.requests[0]
    body = request.json_body
    assert body["phone"] == "9876543210"
    assert body["countryCode"] == "+91"
    assert body["name"] == "Bank RM Desk"
    assert body["clientReferenceId"] == "bank-letter-1"
    assert request.headers.get("x-api-key") == "unit-test-gnani-key-not-real"
    assert "environment=" in request.url
    assert "test-bot-id" in request.url


async def test_trigger_phone_is_normalised(sender):
    sender.push(trigger_success())
    result = await call_bank_rm_or_desk(
        **{**VALID_ARGS, "phone": "+91 98765 43210"}
    )
    assert result.success is True
    assert sender.requests[0].json_body["phone"] == "919876543210"


async def test_trigger_provider_400_unwhitelisted(sender):
    sender.push(trigger_400())
    result = await call_bank_rm_or_desk(**VALID_ARGS)
    assert result.error_category is ErrorCategory.VALIDATION_ERROR
    assert result.error.http_status == 400
    assert "not registered for outbound calls" in result.error.message
    assert result.error.retryable is False
    assert len(sender.requests) == 1


async def test_trigger_provider_403(sender):
    sender.push(trigger_403())
    result = await call_bank_rm_or_desk(**VALID_ARGS)
    assert result.error_category is ErrorCategory.FORBIDDEN
    assert result.error.http_status == 403
    assert len(sender.requests) == 1


async def test_trigger_500_retried_bounded(sender, fast_retries):
    sender.set_default(json_response({"message": "internal"}, status=500))
    result = await call_bank_rm_or_desk(**VALID_ARGS)
    assert result.error_category is ErrorCategory.PROVIDER_ERROR
    assert len(sender.requests) == 3


async def test_trigger_missing_bot_id_is_not_configured(sender, monkeypatch):
    import pytest

    from src.config import get_settings
    from src.errors import GnaniError
    from src.gnani_client import GnaniClient

    monkeypatch.delenv("GNANI_BOT_ID", raising=False)
    client = GnaniClient(settings=get_settings(), sender=sender)
    with pytest.raises(GnaniError) as excinfo:
        await client.trigger_call(
            phone="9876543210",
            country_code="+91",
            name="x",
            checklist_item_id="item",
        )
    assert excinfo.value.category is ErrorCategory.NOT_CONFIGURED
    assert sender.requests == []


async def test_outcome_success(sender):
    sender.push(stats_answered(disposition="PTP", summary={"disposition": "resolved"}))
    result = await read_call_outcome(conversation_id="conv_123")
    assert result.success is True
    assert result.call_status == "ANSWERED"
    assert result.connected is True
    assert result.disposition == "PTP"
    assert result.disposition_label == "resolved"
    assert result.analytics_ready is True
    assert result.analytics_status == "READY"
    assert result.resolved is False
    assert result.resolution_evidence is None
    assert len(result.transcript) == 2
    assert result.transcript[1].detected_language == "hindi"
    assert "not proof that a checklist item was resolved" in result.note
    assert sender.requests[0].method == "GET"
    assert sender.requests[0].url.endswith("/conversations/conv_123/stats")
    assert sender.requests[0].headers.get("x-api-key") == "unit-test-gnani-key-not-real"


async def test_outcome_connected_call_is_never_resolved(sender):
    sender.push(stats_answered(disposition="PTP", summary={"disposition": "resolved"}))
    result = await read_call_outcome(conversation_id="conv_123")
    assert result.connected is True
    assert result.resolved is False


async def test_outcome_with_explicit_resolution_evidence(sender):
    payload = stats_answered().json()
    payload["response"][0]["resolution_evidence"] = {"checklist_item": "bank_letter", "verified_by": "bank"}
    sender.push(json_response(payload))
    result = await read_call_outcome(conversation_id="conv_123")
    assert result.resolved is True
    assert result.resolution_evidence == {"checklist_item": "bank_letter", "verified_by": "bank"}


async def test_outcome_analytics_pending(sender, monkeypatch):
    monkeypatch.setenv("GNANI_OUTCOME_POLL_MAX_ATTEMPTS", "1")
    sender.push(stats_pending())
    result = await read_call_outcome(conversation_id="conv_123")
    assert result.success is False
    assert result.error_category is ErrorCategory.ANALYTICS_PENDING
    assert result.error.retryable is True
    assert result.analytics_ready is False
    assert result.resolved is False
    assert result.polls_attempted == 1
    assert len(sender.requests) == 1


async def test_outcome_polls_until_analytics_ready(sender, monkeypatch):
    monkeypatch.setenv("GNANI_OUTCOME_POLL_MAX_ATTEMPTS", "3")
    monkeypatch.setenv("GNANI_OUTCOME_POLL_INTERVAL_SECONDS", "0")
    sender.push(stats_pending())
    sender.push(stats_answered())
    result = await read_call_outcome(conversation_id="conv_123")
    assert result.success is True
    assert result.polls_attempted == 2
    assert len(sender.requests) == 2


async def test_outcome_timeout(sender, fast_retries):
    sender.set_default(timeout_error())
    result = await read_call_outcome(conversation_id="conv_123")
    assert result.error_category is ErrorCategory.TIMEOUT
    assert len(sender.requests) == 3


async def test_outcome_not_found(sender):
    sender.push(stats_not_found())
    result = await read_call_outcome(conversation_id="conv_missing")
    assert result.error_category is ErrorCategory.NOT_FOUND
    assert len(sender.requests) == 1


async def test_outcome_empty_response_is_not_found(sender):
    sender.push(stats_empty())
    result = await read_call_outcome(conversation_id="conv_empty")
    assert result.error_category is ErrorCategory.NOT_FOUND


async def test_outcome_no_answer(sender):
    sender.push(stats_no_answer())
    result = await read_call_outcome(conversation_id="conv_na")
    assert result.success is True
    assert result.connected is False
    assert result.call_status == "NO ANSWER"
    assert result.disposition == "RNR"


async def test_outcome_validation_bad_conversation_id(sender):
    result = await read_call_outcome(conversation_id="conv 123 !")
    assert result.error_category is ErrorCategory.VALIDATION_ERROR
    assert sender.requests == []
