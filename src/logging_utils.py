"""Structured, redacting logger for tool invocations.

Logged fields: timestamp, tool_name, request_id, duration_ms, success,
error_category, provider. Secrets (API keys, auth headers), full phone
numbers, PAN/Aadhaar-like digits and audio payloads are never logged.
"""

from __future__ import annotations

import json
import logging
import re
import uuid
from datetime import datetime, timezone
from typing import Any

LOGGER_NAME = "raahi.gnani"

_configured = False

_KEY_PATTERN = re.compile(
    r"(?i)\b(authorization|api[-_]?key|x-api-key-id|x-api-key|token|secret|password)\b\s*[:=]\s*\S+"
)
_BEARER_PATTERN = re.compile(r"(?i)\b(bearer)\s+\S+")
_LONG_DIGITS_PATTERN = re.compile(r"\d{6,}")


def setup_logging(level: str = "INFO") -> None:
    global _configured
    logger = logging.getLogger(LOGGER_NAME)
    numeric = getattr(logging, level.upper(), logging.INFO)
    logger.setLevel(numeric)
    if not _configured:
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter("%(message)s"))
        logger.addHandler(handler)
        logger.propagate = False
        _configured = True
    for handler in logger.handlers:
        handler.setLevel(numeric)


def new_request_id() -> str:
    return uuid.uuid4().hex[:16]


def redact(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    text = _KEY_PATTERN.sub(lambda m: f"{m.group(1)}=[REDACTED]", value)
    text = _BEARER_PATTERN.sub("Bearer [REDACTED]", text)
    text = _LONG_DIGITS_PATTERN.sub(lambda m: "*" * len(m.group(0)), text)
    return text


def mask_phone(phone: str | None) -> str | None:
    if not phone:
        return phone
    digits = re.sub(r"\D", "", phone)
    if len(digits) <= 4:
        return "*" * len(digits)
    return f"{digits[:3]}{'*' * (len(digits) - 5)}{digits[-2:]}"


def log_tool_event(
    *,
    tool_name: str,
    request_id: str,
    duration_ms: float,
    success: bool,
    error_category: str,
    provider: str = "gnani",
    **extra: Any,
) -> None:
    payload: dict[str, Any] = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "event": "tool_call",
        "tool_name": tool_name,
        "request_id": request_id,
        "duration_ms": round(duration_ms, 2),
        "success": success,
        "error_category": error_category,
        "provider": provider,
    }
    for key, value in extra.items():
        if value is None:
            continue
        payload[key] = redact(value)
    logging.getLogger(LOGGER_NAME).info(json.dumps(payload, default=str))
