"""Typed error categories and structured error objects for every Gnani tool."""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel


class ErrorCategory(str, Enum):
    SUCCESS = "SUCCESS"
    VALIDATION_ERROR = "VALIDATION_ERROR"
    AUTH_ERROR = "AUTH_ERROR"
    FORBIDDEN = "FORBIDDEN"
    NOT_FOUND = "NOT_FOUND"
    TIMEOUT = "TIMEOUT"
    RATE_LIMITED = "RATE_LIMITED"
    PROVIDER_ERROR = "PROVIDER_ERROR"
    MALFORMED_RESPONSE = "MALFORMED_RESPONSE"
    ANALYTICS_PENDING = "ANALYTICS_PENDING"
    NOT_CONFIGURED = "NOT_CONFIGURED"


RETRYABLE_CATEGORIES = frozenset(
    {
        ErrorCategory.TIMEOUT,
        ErrorCategory.RATE_LIMITED,
        ErrorCategory.PROVIDER_ERROR,
        ErrorCategory.ANALYTICS_PENDING,
    }
)


class ErrorDetail(BaseModel):
    tool: str
    provider: str = "gnani"
    error_category: ErrorCategory
    http_status: int | None = None
    provider_error_code: str | None = None
    message: str
    retryable: bool = False


def category_for_http_status(status: int) -> ErrorCategory:
    if status in (400, 409, 422):
        return ErrorCategory.VALIDATION_ERROR
    if status == 401:
        return ErrorCategory.AUTH_ERROR
    if status == 403:
        return ErrorCategory.FORBIDDEN
    if status == 404:
        return ErrorCategory.NOT_FOUND
    if status in (408, 425, 504):
        return ErrorCategory.TIMEOUT
    if status == 429:
        return ErrorCategory.RATE_LIMITED
    return ErrorCategory.PROVIDER_ERROR


def default_retryable(category: ErrorCategory) -> bool:
    return category in RETRYABLE_CATEGORIES


class GnaniError(Exception):
    """Raised by the Gnani client layer; tools convert it into structured output."""

    def __init__(
        self,
        category: ErrorCategory,
        message: str,
        *,
        tool: str = "",
        http_status: int | None = None,
        provider_error_code: str | None = None,
        retryable: bool | None = None,
        provider: str = "gnani",
        details: dict | None = None,
    ) -> None:
        super().__init__(message)
        self.category = category
        self.message = message
        self.tool = tool
        self.http_status = http_status
        self.provider_error_code = provider_error_code
        self.provider = provider
        self.retryable = default_retryable(category) if retryable is None else retryable
        self.details = details

    def to_detail(self, tool: str | None = None) -> ErrorDetail:
        return ErrorDetail(
            tool=tool or self.tool or "unknown",
            provider=self.provider,
            error_category=self.category,
            http_status=self.http_status,
            provider_error_code=self.provider_error_code,
            message=self.message,
            retryable=self.retryable,
        )


def validation_error(message: str, *, tool: str = "") -> GnaniError:
    return GnaniError(ErrorCategory.VALIDATION_ERROR, message, tool=tool, retryable=False)


def not_configured(message: str, *, tool: str = "") -> GnaniError:
    return GnaniError(ErrorCategory.NOT_CONFIGURED, message, tool=tool, retryable=False)


def auth_error(message: str, *, tool: str = "") -> GnaniError:
    return GnaniError(ErrorCategory.AUTH_ERROR, message, tool=tool, retryable=False)


def build_error_detail(
    tool: str,
    category: ErrorCategory,
    message: str,
    *,
    http_status: int | None = None,
    provider_error_code: str | None = None,
    retryable: bool | None = None,
    provider: str = "gnani",
) -> ErrorDetail:
    return ErrorDetail(
        tool=tool,
        provider=provider,
        error_category=category,
        http_status=http_status,
        provider_error_code=provider_error_code,
        message=message,
        retryable=default_retryable(category) if retryable is None else retryable,
    )
