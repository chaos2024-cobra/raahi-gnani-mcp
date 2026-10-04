"""Environment-driven configuration. No credentials are ever hardcoded."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Mapping

DEFAULT_STT_URL = "https://api.vachana.ai/stt/v3"
DEFAULT_TTS_URL = "https://api.vachana.ai/api/v1/tts/inference"
DEFAULT_TRIGGER_CALL_URL = "https://api.inya.ai/platform/v1/agents/{bot_id}/trigger_call"
DEFAULT_CALL_OUTCOME_URL = "https://api.inya.ai/platform/v1/conversations/{conversation_id}/stats"

DEFAULT_SPEECH_AUTH_HEADER = "X-API-Key-ID"
DEFAULT_PLATFORM_AUTH_HEADER = "x-api-key"

LOOPBACK_HOSTS = ("127.0.0.1:*", "localhost:*", "[::1]:*")
LOOPBACK_ORIGINS = ("http://127.0.0.1:*", "http://localhost:*", "http://[::1]:*")

RENDER_HOST = "raahi-gnani-mcp-1.onrender.com"
RENDER_HOSTS = (RENDER_HOST, f"{RENDER_HOST}:*")
RENDER_ORIGINS = (f"https://{RENDER_HOST}",)


def _get(env: Mapping[str, str], key: str, default: str = "") -> str:
    value = env.get(key)
    if value is None:
        return default
    return value.strip()


def _get_optional(env: Mapping[str, str], key: str) -> str | None:
    value = _get(env, key)
    return value or None


def _get_float(env: Mapping[str, str], key: str, default: float) -> float:
    raw = _get(env, key)
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def _get_int(env: Mapping[str, str], key: str, default: int) -> int:
    raw = _get(env, key)
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _get_bool(env: Mapping[str, str], key: str, default: bool) -> bool:
    raw = _get(env, key).lower()
    if not raw:
        return default
    return raw in ("1", "true", "yes", "on")


def _get_tuple(env: Mapping[str, str], key: str) -> tuple[str, ...]:
    raw = _get(env, key)
    if not raw:
        return ()
    return tuple(part.strip() for part in raw.split(",") if part.strip())


@dataclass(frozen=True)
class Settings:
    api_key: str | None = None
    stt_url: str = DEFAULT_STT_URL
    tts_url: str = DEFAULT_TTS_URL
    tts_model: str = "timbre-v2.5"
    tts_container: str = "wav"
    speech_auth_header: str = DEFAULT_SPEECH_AUTH_HEADER
    trigger_call_url: str = DEFAULT_TRIGGER_CALL_URL
    call_outcome_url: str = DEFAULT_CALL_OUTCOME_URL
    platform_auth_header: str = DEFAULT_PLATFORM_AUTH_HEADER
    bot_id: str | None = None
    call_environment: str = "development"
    dtmf_url: str | None = None
    case_status_url: str | None = None
    case_status_token: str | None = None
    case_status_source: str = "local"
    case_status_timeout_seconds: float = 3.0
    allowed_numbers: tuple[str, ...] = field(default_factory=tuple)
    http_timeout_seconds: float = 10.0
    max_retries: int = 2
    retry_backoff_seconds: float = 0.5
    max_audio_bytes: int = 10 * 1024 * 1024
    max_tts_text_chars: int = 5000
    outcome_poll_max_attempts: int = 3
    outcome_poll_interval_seconds: float = 1.0
    dns_rebinding_protection: bool = True
    allowed_hosts: tuple[str, ...] = field(default_factory=tuple)
    allowed_origins: tuple[str, ...] = field(default_factory=tuple)
    log_level: str = "INFO"

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> Settings:
        env = os.environ if env is None else env
        source = _get(env, "GNANI_CASE_STATUS_URL") and "http" or "local"
        return cls(
            api_key=_get_optional(env, "GNANI_API_KEY"),
            stt_url=_get(env, "GNANI_STT_URL", DEFAULT_STT_URL),
            tts_url=_get(env, "GNANI_TTS_URL", DEFAULT_TTS_URL),
            tts_model=_get(env, "GNANI_TTS_MODEL", "timbre-v2.5"),
            tts_container=_get(env, "GNANI_TTS_CONTAINER", "wav"),
            speech_auth_header=_get(env, "GNANI_SPEECH_AUTH_HEADER", DEFAULT_SPEECH_AUTH_HEADER),
            trigger_call_url=_get(env, "GNANI_TRIGGER_CALL_URL", DEFAULT_TRIGGER_CALL_URL),
            call_outcome_url=_get(env, "GNANI_CALL_OUTCOME_URL", DEFAULT_CALL_OUTCOME_URL),
            platform_auth_header=_get(env, "GNANI_PLATFORM_AUTH_HEADER", DEFAULT_PLATFORM_AUTH_HEADER),
            bot_id=_get_optional(env, "GNANI_BOT_ID"),
            call_environment=_get(env, "GNANI_CALL_ENVIRONMENT", "development"),
            dtmf_url=_get_optional(env, "GNANI_DTMF_URL"),
            case_status_url=_get_optional(env, "GNANI_CASE_STATUS_URL"),
            case_status_token=_get_optional(env, "GNANI_CASE_STATUS_TOKEN"),
            case_status_source=_get(env, "GNANI_CASE_STATUS_SOURCE", source),
            case_status_timeout_seconds=_get_float(env, "GNANI_CASE_STATUS_TIMEOUT_SECONDS", 3.0),
            allowed_numbers=_get_tuple(env, "GNANI_ALLOWED_NUMBERS"),
            http_timeout_seconds=_get_float(env, "GNANI_HTTP_TIMEOUT_SECONDS", 10.0),
            max_retries=max(0, _get_int(env, "GNANI_MAX_RETRIES", 2)),
            retry_backoff_seconds=_get_float(env, "GNANI_RETRY_BACKOFF_SECONDS", 0.5),
            max_audio_bytes=max(1024, _get_int(env, "GNANI_MAX_AUDIO_BYTES", 10 * 1024 * 1024)),
            max_tts_text_chars=max(1, _get_int(env, "GNANI_MAX_TTS_TEXT_CHARS", 5000)),
            outcome_poll_max_attempts=max(1, _get_int(env, "GNANI_OUTCOME_POLL_MAX_ATTEMPTS", 3)),
            outcome_poll_interval_seconds=_get_float(env, "GNANI_OUTCOME_POLL_INTERVAL_SECONDS", 1.0),
            dns_rebinding_protection=_get_bool(env, "MCP_ENABLE_DNS_REBINDING_PROTECTION", True),
            allowed_hosts=_get_tuple(env, "MCP_ALLOWED_HOSTS"),
            allowed_origins=_get_tuple(env, "MCP_ALLOWED_ORIGINS"),
            log_level=_get(env, "LOG_LEVEL", "INFO"),
        )

    @property
    def mcp_max_body_bytes(self) -> int:
        return int(self.max_audio_bytes * 4 / 3) + 65536

    def transport_security(self):
        from mcp.server.transport_security import TransportSecuritySettings

        if not self.dns_rebinding_protection:
            return TransportSecuritySettings(enable_dns_rebinding_protection=False)
        hosts = list(self.allowed_hosts) or [*LOOPBACK_HOSTS, *RENDER_HOSTS]
        origins = list(self.allowed_origins) or [*LOOPBACK_ORIGINS, *RENDER_ORIGINS]
        return TransportSecuritySettings(
            enable_dns_rebinding_protection=True,
            allowed_hosts=hosts,
            allowed_origins=origins,
        )

    def require_api_key(self, tool: str) -> str:
        from src.errors import auth_error

        if not self.api_key:
            raise auth_error(
                "GNANI_API_KEY is not configured; cannot authenticate to Gnani.",
                tool=tool,
            )
        return self.api_key


def get_settings(env: Mapping[str, str] | None = None) -> Settings:
    return Settings.from_env(env)
