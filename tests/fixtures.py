"""Reusable fake Gnani responses and an injectable fake HTTP sender."""

from __future__ import annotations

import json
from typing import Any

import httpx

from src.gnani_client import HttpRequest, HttpResponse


def json_response(payload: Any, status: int = 200) -> HttpResponse:
    return HttpResponse(
        status_code=status,
        headers={"content-type": "application/json"},
        content=json.dumps(payload).encode("utf-8"),
    )


def raw_response(body: bytes, content_type: str = "application/json", status: int = 200) -> HttpResponse:
    return HttpResponse(status_code=status, headers={"content-type": content_type}, content=body)


class FakeSender:
    """Queue-driven stand-in for the real HTTP sender.

    Responses are consumed in order; once the queue is empty the optional
    default (set via set_default) is reused forever. Exceptions in the queue
    are raised as-is, which is how timeouts are simulated.
    """

    def __init__(self, *responses: HttpResponse | Exception) -> None:
        self.queue: list[HttpResponse | Exception] = list(responses)
        self.default: HttpResponse | Exception | None = None
        self.requests: list[HttpRequest] = []

    def push(self, item: HttpResponse | Exception) -> "FakeSender":
        self.queue.append(item)
        return self

    def set_default(self, item: HttpResponse | Exception) -> "FakeSender":
        self.default = item
        return self

    async def send(self, request: HttpRequest) -> HttpResponse:
        self.requests.append(request)
        item = self.queue.pop(0) if self.queue else self.default
        if item is None:
            raise AssertionError("FakeSender received a request but had no response queued")
        if isinstance(item, Exception):
            raise item
        return item


def timeout_error(message: str = "simulated timeout") -> httpx.TimeoutException:
    return httpx.TimeoutException(message)


def stt_final(text: str, request_id: str = "req_stt_final", model: str | None = None) -> HttpResponse:
    payload: dict[str, Any] = {
        "success": True,
        "request_id": request_id,
        "timestamp": "20251226_143052.123",
        "transcript": text,
    }
    if model:
        payload["model"] = model
    return json_response(payload)


def stt_interim_final(interim: str, final: str, request_id: str = "req_stt_interim") -> HttpResponse:
    return json_response(
        {
            "success": True,
            "request_id": request_id,
            "timestamp": "20251226_143052.456",
            "interim_transcript": interim,
            "transcript": final,
        }
    )


def stt_partial_error(
    partial: str,
    message: str = "Audio duration exceeds maximum limit",
    code: str = "INVALID_REQUEST_ERROR",
    status: int = 400,
) -> HttpResponse:
    return json_response(
        {
            "success": False,
            "error": {"type": code, "message": message},
            "partial_transcript": partial,
        },
        status=status,
    )


def tts_audio(data: bytes = b"RIFF-fake-wav-bytes", content_type: str = "audio/wav") -> HttpResponse:
    return raw_response(data, content_type=content_type)


def tts_error(message: str = "Invalid text or audio configuration.", status: int = 400) -> HttpResponse:
    return json_response(
        {"success": False, "error": {"type": "INVALID_REQUEST_ERROR", "message": message}},
        status=status,
    )


def trigger_success(
    phone: str = "9876543210",
    request_id: str = "req_trigger_1",
    client_reference_id: str | None = "bank-letter-1",
) -> HttpResponse:
    response_obj: dict[str, Any] | None = None
    if client_reference_id is not None:
        response_obj = {"clientReferenceId": client_reference_id}
    return json_response(
        {
            "status": "success",
            "message": f"Call is being triggered to {phone}",
            "response": response_obj,
            "requestId": request_id,
        }
    )


def trigger_400(message: str = "Phone number is not registered for outbound calls") -> HttpResponse:
    return json_response(
        {"status": "failure", "requestId": "req_trigger_400", "message": message},
        status=400,
    )


def trigger_403(
    message: str = "Developer can only trigger calls in the development environment",
) -> HttpResponse:
    return json_response(
        {"status": "failure", "requestId": "req_trigger_403", "message": message},
        status=403,
    )


def _stats_record(
    *,
    conversation_id: str = "conv_123",
    call_status: str = "ANSWERED",
    disposition: str | None = "PTP",
    summary: dict | None = None,
    call_processed: bool | None = True,
    utterances: list[dict] | None = None,
) -> dict[str, Any]:
    record: dict[str, Any] = {
        "conversationId": conversation_id,
        "botId": "bot_1",
        "botName": "Raahi Chase Agent",
        "callStatus": call_status,
        "callDuration": 81 if call_status == "ANSWERED" else 0,
        "overallCallDisposition": disposition,
        "utteranceAnalytics": utterances
        if utterances is not None
        else [
            {"role": "assistant", "content": "Hello", "timestamp": 1786433683.0},
            {"role": "user", "content": "Haan ji", "timestamp": 1786433699.0, "detectedLanguage": "hindi"},
        ],
    }
    if summary is not None:
        record["callSummary"] = summary
    if call_processed is not None:
        record["callProcessed"] = call_processed
    return record


def stats_answered(
    disposition: str = "PTP",
    summary: dict | None = None,
    call_processed: bool | None = True,
) -> HttpResponse:
    return json_response(
        {
            "status": "success",
            "message": "Conversation details fetched successfully",
            "requestId": "req_stats_1",
            "response": [
                _stats_record(disposition=disposition, summary=summary, call_processed=call_processed)
            ],
        }
    )


def stats_pending() -> HttpResponse:
    return json_response(
        {
            "status": "success",
            "message": "Conversation details fetched successfully",
            "requestId": "req_stats_pending",
            "response": [
                _stats_record(
                    disposition="PTP",
                    summary=None,
                    call_processed=False,
                )
            ],
        }
    )


def stats_no_answer() -> HttpResponse:
    return json_response(
        {
            "status": "success",
            "requestId": "req_stats_na",
            "response": [_stats_record(call_status="NO ANSWER", disposition="RNR", call_processed=True)],
        }
    )


def stats_empty() -> HttpResponse:
    return json_response(
        {"status": "success", "message": "No data", "requestId": "req_stats_empty", "response": []}
    )


def stats_not_found() -> HttpResponse:
    return json_response(
        {"status": "failure", "requestId": "req_stats_404", "message": "Conversation not found"},
        status=404,
    )


def dtmf_accepted(conversation_id: str = "conv_123") -> HttpResponse:
    return json_response(
        {"status": "success", "accepted": True, "conversation_id": conversation_id, "message": "tones registered"}
    )


def dtmf_menu_changed(message: str = "IVR menu structure changed; sequence no longer maps to any option") -> HttpResponse:
    return json_response(
        {"status": "failure", "accepted": False, "message": message},
        status=400,
    )


def dtmf_invalid_sequence(message: str = "invalid DTMF sequence") -> HttpResponse:
    return json_response(
        {"status": "failure", "accepted": False, "message": message},
        status=422,
    )


def case_payload(
    case_id: str = "RAAHI-DEMO-001",
    applicant_id: str = "APPLICANT-DEMO-001",
    checklist: list[dict] | None = None,
) -> dict[str, Any]:
    return {
        "case_id": case_id,
        "applicant_id": applicant_id,
        "applicant_name": "Demo Applicant",
        "checklist": checklist
        if checklist is not None
        else [
            {"item": "passport", "status": "verified"},
            {"item": "funds_proof", "status": "pending"},
            {"item": "bank_letter", "status": "pending"},
            {"item": "insurance", "status": "verified"},
        ],
    }
