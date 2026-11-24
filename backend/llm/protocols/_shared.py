"""Shared helpers for wire protocol drivers."""

from __future__ import annotations

from backend.llm._json import JsonObject, parse_json_object
from backend.llm.errors import ErrorCategory, LLMError
from backend.llm.types import AuthStyle, EndpointConfig, ImageBlock


def join_endpoint_path(base_url: str, path: str) -> str:
    """Append an endpoint path to base_url without duplicating a trailing suffix."""
    clean_base = base_url.rstrip("/")
    clean_path = "/" + path.lstrip("/")
    if clean_base.endswith(clean_path):
        return clean_base
    return f"{clean_base}{clean_path}"


def build_auth_headers(
    endpoint: EndpointConfig,
    *,
    include_content_type: bool = True,
) -> dict[str, str]:
    """Assemble HTTP headers from auth style, preset quirks, and endpoint overrides."""
    headers: dict[str, str] = {}
    if include_content_type:
        headers["Content-Type"] = "application/json"

    if endpoint.auth_style == AuthStyle.BEARER and endpoint.api_key:
        headers["Authorization"] = f"Bearer {endpoint.api_key}"
    elif endpoint.auth_style == AuthStyle.X_API_KEY and endpoint.api_key:
        headers["x-api-key"] = endpoint.api_key

    for key, value in endpoint.quirks.extra_headers:
        headers[key] = value
    for key, value in endpoint.headers:
        headers[key] = value
    return headers


def merge_extra_body(payload: JsonObject, extra_body: JsonObject | None) -> JsonObject:
    """Apply optional provider/request extra_body fields onto payload."""
    if not extra_body:
        return payload
    for key, value in extra_body.items():
        payload[key] = value
    return payload


def parse_sse_json(data: str) -> JsonObject:
    """Parse one SSE data frame as a JSON object or raise a protocol LLMError."""
    try:
        return parse_json_object(data)
    except ValueError as exc:
        raise LLMError(
            ErrorCategory.PROTOCOL,
            f"Malformed JSON in SSE frame: {exc}",
            retryable=False,
            raw_body=data,
        ) from exc


def image_url_value(block: ImageBlock) -> str | None:
    """Return an image URL or base64 data URI for OpenAI-style multimodal payloads."""
    if block.url:
        return block.url
    if block.data_base64:
        return f"data:{block.media_type};base64,{block.data_base64}"
    return None
