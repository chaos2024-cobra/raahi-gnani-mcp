"""Result models shared by all six MCP tools."""

from __future__ import annotations

from pydantic import BaseModel, Field

from src.errors import ErrorCategory, ErrorDetail

PROVIDER = "gnani"


class ToolResultBase(BaseModel):
    success: bool
    error_category: ErrorCategory
    error: ErrorDetail | None = None
    request_id: str
    provider: str = PROVIDER
    duration_ms: float = 0.0


class TranscriptTurn(BaseModel):
    role: str | None = None
    content: str | None = None
    timestamp: float | None = None
    detected_language: str | None = None


class TranscribeSpeechResult(ToolResultBase):
    language: str | None = None
    final_transcript: str | None = None
    interim_transcript: str | None = None
    interim_available: bool = False
    partial_transcript: str | None = None
    partial: bool = False
    uncertainty: bool | None = None
    provider_request_id: str | None = None
    provider_model: str | None = None
    note: str | None = None


class SpeakReplyResult(ToolResultBase):
    content_type: str | None = None
    container: str | None = None
    sample_rate: int | None = None
    audio_size_bytes: int | None = None
    audio_base64: str | None = None
    streaming: bool = False
    chunk_count: int | None = None
    voice_id: str | None = None
    language: str | None = None
    model: str | None = None
    note: str | None = None


class CallTriggerResult(ToolResultBase):
    checklist_item_id: str | None = None
    conversation_id: str | None = None
    trigger_status: str | None = None
    current_status: str | None = None
    disposition: str | None = None
    transcript: str | None = None
    provider_message: str | None = None
    provider_request_id: str | None = None
    client_reference_id: str | None = None
    note: str | None = None


class CallOutcomeResult(ToolResultBase):
    conversation_id: str | None = None
    call_status: str | None = None
    connected: bool = False
    disposition: str | None = None
    disposition_label: str | None = None
    transcript: list[TranscriptTurn] = Field(default_factory=list)
    analytics_ready: bool = False
    analytics_status: str | None = None
    resolved: bool = False
    resolution_evidence: dict | None = None
    polls_attempted: int = 0
    provider_request_id: str | None = None
    note: str | None = None


class DtmfResult(ToolResultBase):
    conversation_id: str | None = None
    accepted: bool = False
    dtmf_sequence_masked: str | None = None
    provider_status: str | None = None
    provider_response: dict | None = None
    note: str | None = None


class ChecklistItem(BaseModel):
    item: str
    status: str
    detail: str | None = None


class CaseStatusResult(ToolResultBase):
    case_id: str | None = None
    applicant_id: str | None = None
    checklist: list[ChecklistItem] = Field(default_factory=list)
    source: str | None = None
    fetched_at: str | None = None
    note: str | None = None
