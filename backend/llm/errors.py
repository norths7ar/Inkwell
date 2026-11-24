"""Structured error taxonomy and HTTP/transport classifiers for LLM calls."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from enum import StrEnum

import httpx

from backend.llm._json import as_object, get_str, parse_json_object


class ErrorCategory(StrEnum):
    AUTH = "auth"
    QUOTA = "quota"
    RATE_LIMIT = "rate_limit"
    CONTEXT_OVERFLOW = "context_overflow"
    INVALID_REQUEST = "invalid_request"
    SERVER = "server"
    TIMEOUT = "timeout"
    NETWORK = "network"
    PROTOCOL = "protocol"
    CANCELLED = "cancelled"


class LLMError(Exception):
    """Normalized error raised by protocol drivers and HTTP transport."""

    def __init__(
        self,
        category: ErrorCategory,
        message: str,
        *,
        retryable: bool = False,
        status_code: int | None = None,
        retry_after_seconds: float | None = None,
        raw_body: str = "",
    ) -> None:
        super().__init__(message)
        self.category = category
        self.message = message
        self.retryable = retryable
        self.status_code = status_code
        self.retry_after_seconds = retry_after_seconds
        self.raw_body = raw_body


_CONTEXT_PATTERNS = (
    "context length",
    "context_length",
    "maximum context",
    "context window",
    "token limit",
    "prompt is too long",
    "too many tokens",
    "input is too long",
    "exceeds the maximum",
)
_QUOTA_PATTERNS = ("insufficient_quota", "quota", "billing", "credit balance")
_RATE_LIMIT_PATTERNS = ("rate limit", "rate_limit", "too many requests", "overloaded")


def _extract_error_message(body: str) -> str:
    trimmed = body.strip()
    if not trimmed:
        return "Empty error response"
    try:
        obj = parse_json_object(trimmed)
    except ValueError:
        return trimmed[:400]

    err_obj = as_object(obj.get("error"))
    if err_obj is not None:
        msg = get_str(err_obj, "message")
        if msg:
            return msg
    msg = get_str(obj, "message") or get_str(obj, "detail")
    if msg:
        return msg
    return trimmed[:400]


def _parse_retry_after(headers: Mapping[str, str]) -> float | None:
    lowered = {k.lower(): v for k, v in headers.items()}
    raw = lowered.get("retry-after")
    if not raw:
        return None
    text = raw.strip()
    try:
        seconds = float(text)
        return max(0.0, seconds)
    except ValueError:
        pass
    try:
        target = parsedate_to_datetime(text)
    except TypeError, ValueError:
        return None
    if target.tzinfo is None:
        target = target.replace(tzinfo=UTC)
    delta = (target - datetime.now(UTC)).total_seconds()
    return max(0.0, delta)


def _contains_any(text: str, patterns: tuple[str, ...]) -> bool:
    return any(pattern in text for pattern in patterns)


def classify_http_error(
    status_code: int,
    body: str,
    headers: Mapping[str, str],
) -> LLMError:
    """Classify an HTTP >= 400 upstream response into an LLMError."""
    message = _extract_error_message(body)
    lowered_msg = f"{message} {body[:500]}".lower()
    retry_after = _parse_retry_after(headers)

    if status_code in (401, 403):
        return LLMError(
            ErrorCategory.AUTH,
            message,
            retryable=False,
            status_code=status_code,
            raw_body=body,
        )
    if status_code == 402 or _contains_any(lowered_msg, _QUOTA_PATTERNS):
        return LLMError(
            ErrorCategory.QUOTA,
            message,
            retryable=False,
            status_code=status_code,
            raw_body=body,
        )
    if status_code == 429 or _contains_any(lowered_msg, _RATE_LIMIT_PATTERNS):
        return LLMError(
            ErrorCategory.RATE_LIMIT,
            message,
            retryable=True,
            status_code=status_code,
            retry_after_seconds=retry_after,
            raw_body=body,
        )
    if status_code == 408:
        return LLMError(
            ErrorCategory.TIMEOUT,
            message,
            retryable=True,
            status_code=status_code,
            raw_body=body,
        )
    if status_code in (400, 413, 422):
        category = (
            ErrorCategory.CONTEXT_OVERFLOW
            if status_code == 413 or _contains_any(lowered_msg, _CONTEXT_PATTERNS)
            else ErrorCategory.INVALID_REQUEST
        )
        return LLMError(
            category,
            message,
            retryable=False,
            status_code=status_code,
            raw_body=body,
        )
    if status_code >= 500:
        return LLMError(
            ErrorCategory.SERVER,
            message,
            retryable=True,
            status_code=status_code,
            retry_after_seconds=retry_after,
            raw_body=body,
        )
    return LLMError(
        ErrorCategory.INVALID_REQUEST,
        message,
        retryable=False,
        status_code=status_code,
        raw_body=body,
    )


def classify_transport_error(exc: httpx.HTTPError) -> LLMError:
    """Map httpx transport or decoding exceptions into an LLMError."""
    if isinstance(exc, httpx.TimeoutException):
        return LLMError(ErrorCategory.TIMEOUT, str(exc) or "Request timed out", retryable=True)
    if isinstance(exc, httpx.RemoteProtocolError):
        return LLMError(
            ErrorCategory.PROTOCOL,
            str(exc) or "Upstream closed connection unexpectedly",
            retryable=True,
        )
    if isinstance(exc, httpx.DecodingError):
        return LLMError(
            ErrorCategory.PROTOCOL,
            str(exc) or "Failed to decode response payload",
            retryable=False,
        )
    return LLMError(ErrorCategory.NETWORK, str(exc) or "Network transport error", retryable=True)
