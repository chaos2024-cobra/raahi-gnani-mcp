import json

from src.gnani_tools import (
    call_bank_rm_or_desk,
    navigate_ivr,
    pull_case_status_into_call,
    read_call_outcome,
    speak_reply,
    transcribe_speech,
)
from tests.fixtures import (
    case_payload,
    dtmf_accepted,
    json_response,
    stats_answered,
    trigger_success,
    tts_audio,
)

import base64

API_KEY = "unit-test-gnani-key-not-real"
AUDIO_B64 = base64.b64encode(b"RIFF" + b"\x00" * 64).decode("ascii")


def _assert_no_secret(payload_text: str) -> None:
    assert API_KEY not in payload_text
    assert "X-API-Key-ID: " + API_KEY not in payload_text
    assert "Bearer " + API_KEY not in payload_text


async def test_no_api_key_in_transcribe_output(sender):
    from tests.fixtures import stt_final

    sender.push(stt_final("hello"))
    result = await transcribe_speech(audio=AUDIO_B64, language="en-IN")
    _assert_no_secret(result.model_dump_json())


async def test_no_api_key_in_trigger_output(sender):
    sender.push(trigger_success())
    result = await call_bank_rm_or_desk(
        phone="9876543210",
        country_code="+91",
        name="RM",
        checklist_item_id="item-1",
    )
    _assert_no_secret(result.model_dump_json())


async def test_no_api_key_in_tts_output(sender):
    sender.push(tts_audio(b"RIFFdata"))
    result = await speak_reply(text="hello", language="en-IN", voice_id="Kaveri")
    _assert_no_secret(json.dumps(result.structured_content))
    for block in result.content:
        _assert_no_secret(block.model_dump_json())


async def test_no_api_key_in_outcome_output(sender):
    sender.push(stats_answered())
    result = await read_call_outcome(conversation_id="conv_1")
    _assert_no_secret(result.model_dump_json())


async def test_no_api_key_in_dtmf_output(sender, monkeypatch):
    monkeypatch.setenv("GNANI_DTMF_URL", "https://gnani.example.test/dtmf")
    sender.push(dtmf_accepted())
    result = await navigate_ivr(dtmf_sequence="1", conversation_id="conv_1")
    _assert_no_secret(result.model_dump_json())


async def test_no_api_key_in_case_status_output(sender):
    result = await pull_case_status_into_call(case_id="RAAHI-DEMO-001")
    _assert_no_secret(result.model_dump_json())


async def test_error_output_never_contains_api_key(sender):
    sender.set_default(json_response({"error": {"message": "nope"}}, status=401))
    result = await transcribe_speech(audio=AUDIO_B64, language="en-IN")
    _assert_no_secret(result.model_dump_json())


async def test_no_secrets_in_structured_logs(sender, raahi_logs):
    from tests.fixtures import stt_final

    sender.push(stt_final("hello world"))
    await transcribe_speech(audio=AUDIO_B64, language="en-IN")
    sender.push(trigger_success(phone="9876543210"))
    await call_bank_rm_or_desk(
        phone="9876543210",
        country_code="+91",
        name="RM Desk",
        checklist_item_id="item-1",
    )
    text = raahi_logs.text
    assert text.strip(), "expected tool logs"
    assert API_KEY not in text
    assert "X-API-Key-ID" not in text
    assert "9876543210" not in text
    assert "Authorization" not in text
    lines = [line[line.index("{") :] for line in text.splitlines() if "{" in line]
    assert lines
    entry = json.loads(lines[0])
    for field in ("timestamp", "tool_name", "request_id", "duration_ms", "success", "error_category", "provider"):
        assert field in entry
    assert entry["tool_name"] == "transcribe_speech"
    assert entry["provider"] == "gnani"


async def test_audio_is_never_logged(sender, raahi_logs):
    from tests.fixtures import stt_final

    sender.push(stt_final("hello"))
    await transcribe_speech(audio=AUDIO_B64, language="en-IN")
    assert AUDIO_B64 not in raahi_logs.text
    assert "RIFF" not in raahi_logs.text
