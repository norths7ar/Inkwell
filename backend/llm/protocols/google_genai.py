"""Native Gemini generateContent REST/SSE, without the Google SDK."""

import json
from collections.abc import AsyncIterator, Mapping
from contextlib import suppress
from dataclasses import replace
from urllib.parse import quote
from uuid import uuid4

import httpx

from backend.llm._json import (
    JsonObject,
    JsonValue,
    as_list,
    as_object,
    get_int,
    get_str,
    parse_json_object,
    parse_json_value,
)
from backend.llm.errors import ErrorCategory, LLMError, classify_http_error
from backend.llm.protocols._shared import build_auth_headers, merge_extra_body, parse_sse_json
from backend.llm.sse import iter_sse_frames
from backend.llm.types import (
    AuthStyle,
    DoneEvent,
    EndpointConfig,
    GenerateRequest,
    ImageBlock,
    ReasoningBlock,
    ReasoningDeltaEvent,
    Role,
    StreamEvent,
    TextBlock,
    TextDeltaEvent,
    TokenUsage,
    ToolCallBlock,
    ToolCallDeltaEvent,
    ToolResultBlock,
    UsageEvent,
    WireProtocol,
)


def auth_headers(endpoint: EndpointConfig, *, include_content_type: bool = True) -> dict[str, str]:
    # A per-model native protocol override must not accidentally send a Bearer API key.
    style = AuthStyle.NONE if endpoint.auth_style == AuthStyle.NONE else AuthStyle.X_GOOG_API_KEY
    return build_auth_headers(
        replace(endpoint, auth_style=style), include_content_type=include_content_type
    )


def _thinking_config(model: str, effort: str | None) -> JsonObject | None:
    name = model.removeprefix("models/").lower()
    if effort is None:
        # Older non-thinking models reject thinkingConfig entirely.
        return (
            {"includeThoughts": True}
            if name.startswith(("gemini-2.5", "gemini-3", "gemini-4"))
            else None
        )
    level = effort.lower()
    allowed = {"none", "minimal", "low", "medium", "high", "xhigh", "max", "auto"}
    if level not in allowed:
        raise LLMError(ErrorCategory.INVALID_REQUEST, "Unsupported Gemini reasoning_effort")
    config: JsonObject = {"includeThoughts": level != "none"}
    if name.startswith("gemini-2.5"):
        budgets = {
            "none": 0,
            "minimal": 128,
            "low": 1024,
            "medium": 8192,
            "high": 24576,
            "xhigh": 24576,
            "max": 24576,
            "auto": -1,
        }
        budget = budgets[level]
        # 2.5 Pro cannot disable thinking and requires at least 128 budget tokens.
        if "pro" in name and budget == 0:
            budget = 128
        config["thinkingBudget"] = budget
    elif level != "auto":
        mapped = {"none": "MINIMAL", "max": "HIGH", "xhigh": "HIGH"}.get(level, level.upper())
        # Gemini 3 Pro supports LOW/HIGH; Flash supports MINIMAL/MEDIUM as well.
        if name.startswith("gemini-3") and "pro" in name:
            mapped = "LOW" if mapped in ("MINIMAL", "LOW") else "HIGH"
        if name.startswith("gemini-4") and mapped == "MINIMAL":
            mapped = "LOW"
        config["thinkingLevel"] = mapped
    return config


def _parts(request: GenerateRequest) -> tuple[list[JsonValue], list[JsonValue]]:
    system: list[JsonValue] = []
    turns: list[JsonValue] = []
    last_role = ""
    last_parts: list[JsonValue] = []
    for message in request.messages:
        if message.role == Role.SYSTEM:
            system.extend(
                {"text": block.text} for block in message.blocks if isinstance(block, TextBlock)
            )
            continue
        parts: list[JsonValue] = []
        for block in message.blocks:
            part: JsonObject
            if isinstance(block, (TextBlock, ReasoningBlock)):
                if (
                    isinstance(block, ReasoningBlock)
                    and block.signature_protocol != WireProtocol.GOOGLE_GENAI
                ):
                    # Opaque reasoning state is provider-specific; never replay another protocol's signature.
                    continue
                part = {"text": block.text}
                if isinstance(block, ReasoningBlock):
                    part["thought"] = True
                if block.signature:
                    part["thoughtSignature"] = block.signature
            elif isinstance(block, ToolCallBlock):
                try:
                    arguments = parse_json_object(block.arguments_json)
                except ValueError as exc:
                    raise LLMError(
                        ErrorCategory.INVALID_REQUEST, "Invalid function call arguments"
                    ) from exc
                call: JsonObject = {"name": block.name, "args": arguments}
                if block.call_id:
                    call["id"] = block.call_id
                part = {"functionCall": call}
                if block.signature:
                    part["thoughtSignature"] = block.signature
            elif isinstance(block, ToolResultBlock):
                try:
                    result = parse_json_value(block.content)
                except ValueError:
                    result = block.content
                result_object: JsonObject = {"error" if block.is_error else "output": result}
                part = {
                    "functionResponse": {
                        "id": block.call_id,
                        "name": block.name,
                        "response": result_object,
                    }
                }
            elif isinstance(block, ImageBlock):
                if block.data_base64:
                    part = {"inlineData": {"mimeType": block.media_type, "data": block.data_base64}}
                elif block.url:
                    part = {"fileData": {"mimeType": block.media_type, "fileUri": block.url}}
                else:
                    continue
            else:
                continue
            parts.append(part)
        if not parts:
            continue
        role = "model" if message.role == Role.ASSISTANT else "user"
        if role == last_role:
            last_parts.extend(parts)
        else:
            last_parts = parts
            turns.append({"role": role, "parts": last_parts})
            last_role = role
    return system, turns


def build_request(
    endpoint: EndpointConfig, request: GenerateRequest
) -> tuple[str, dict[str, str], JsonObject]:
    model = request.model.removeprefix("models/")
    url = f"{endpoint.base_url.rstrip('/')}/models/{quote(model, safe='')}:streamGenerateContent?alt=sse"
    system, turns = _parts(request)
    payload: JsonObject = {"contents": turns}
    if system:
        payload["systemInstruction"] = {"parts": system}
    if request.tools:
        payload["tools"] = [
            {
                "functionDeclarations": [
                    {
                        "name": tool.name,
                        "description": tool.description,
                        "parametersJsonSchema": tool.input_schema,
                    }
                    for tool in request.tools
                ]
            }
        ]
    generation: JsonObject = {}
    if request.temperature is not None:
        generation["temperature"] = request.temperature
    if request.max_output_tokens is not None:
        generation["maxOutputTokens"] = request.max_output_tokens
    thinking = _thinking_config(request.model, request.reasoning_effort)
    if thinking is not None:
        budget = get_int(thinking, "thinkingBudget")
        if budget is not None and budget > 0 and request.max_output_tokens is not None:
            # Thinking and visible output share maxOutputTokens; reserve room for the answer.
            thinking["thinkingBudget"] = min(budget, max(128, request.max_output_tokens // 2))
        generation["thinkingConfig"] = thinking
    if generation:
        payload["generationConfig"] = generation
    merge_extra_body(payload, request.extra_body)
    return url, auth_headers(endpoint), payload


def classify_error(status_code: int, body: str, headers: Mapping[str, str]) -> LLMError:
    """Use Google's structured status/reason and RetryInfo before generic HTTP fallback."""
    fallback = classify_http_error(status_code, body, headers)
    try:
        error = as_object(parse_json_object(body).get("error")) or {}
    except ValueError:
        return fallback
    status = get_str(error, "status") or ""
    reasons: list[str] = []
    daily_quota = False
    retry = fallback.retry_after_seconds
    for raw in as_list(error.get("details")) or []:
        detail = as_object(raw) or {}
        reasons.append(get_str(detail, "reason") or "")
        for raw_violation in as_list(detail.get("violations")) or []:
            violation = as_object(raw_violation) or {}
            quota_id = (get_str(violation, "quotaId") or "").lower()
            daily_quota = daily_quota or "perday" in quota_id or "daily" in quota_id
        delay = get_str(detail, "retryDelay")
        if delay and delay.endswith("s"):
            with suppress(ValueError):
                retry = max(0.0, float(delay[:-1]))
    category = fallback.category
    retryable = fallback.retryable
    if status in ("UNAUTHENTICATED", "PERMISSION_DENIED") or any(
        reason.startswith("API_KEY_") for reason in reasons
    ):
        category, retryable = ErrorCategory.AUTH, False
    elif status == "RESOURCE_EXHAUSTED":
        # Per-minute Gemini quota exhaustion is a retryable rate limit, not depleted credits.
        category, retryable = (
            (ErrorCategory.QUOTA, False) if daily_quota else (ErrorCategory.RATE_LIMIT, True)
        )
    elif status == "DEADLINE_EXCEEDED":
        category, retryable = ErrorCategory.TIMEOUT, True
    elif status in ("INTERNAL", "UNAVAILABLE"):
        category, retryable = ErrorCategory.SERVER, True
    return LLMError(
        category,
        fallback.message,
        retryable=retryable,
        status_code=status_code,
        retry_after_seconds=retry,
        raw_body=body,
    )


async def decode_stream(response: httpx.Response) -> AsyncIterator[StreamEvent]:
    next_call = 0
    last_kind = ""
    last_signed = False
    finish: str | None = None
    saw_frame = False
    async for frame in iter_sse_frames(response):
        saw_frame = True
        obj = parse_sse_json(frame.data)
        error = as_object(obj.get("error"))
        if error is not None:
            raise classify_error(get_int(error, "code") or 500, frame.data, response.headers)
        usage = as_object(obj.get("usageMetadata"))
        if usage is not None:
            yield UsageEvent(
                TokenUsage(
                    input_tokens=get_int(usage, "promptTokenCount") or 0,
                    output_tokens=(get_int(usage, "candidatesTokenCount") or 0)
                    + (get_int(usage, "thoughtsTokenCount") or 0),
                    cache_read_tokens=get_int(usage, "cachedContentTokenCount") or 0,
                )
            )
        feedback = as_object(obj.get("promptFeedback")) or {}
        blocked = get_str(feedback, "blockReason")
        if blocked and blocked != "BLOCK_REASON_UNSPECIFIED":
            raise LLMError(ErrorCategory.INVALID_REQUEST, f"Gemini blocked the prompt: {blocked}")
        for raw_candidate in as_list(obj.get("candidates")) or []:
            candidate = as_object(raw_candidate) or {}
            if (get_int(candidate, "index") or 0) != 0:
                continue
            content = as_object(candidate.get("content")) or {}
            for position, raw_part in enumerate(as_list(content.get("parts")) or []):
                part = as_object(raw_part) or {}
                signature = get_str(part, "thoughtSignature")
                call = as_object(part.get("functionCall"))
                if (
                    signature
                    and "text" not in part
                    and "thought" not in part
                    and call is None
                    and last_kind == "tool"
                ):
                    yield ToolCallDeltaEvent(index=next_call - 1, signature_delta=signature)
                elif call is not None:
                    name = get_str(call, "name")
                    arguments = as_object(call.get("args"))
                    if not name or ("args" in call and arguments is None):
                        raise LLMError(ErrorCategory.PROTOCOL, "Malformed Gemini function call")
                    yield ToolCallDeltaEvent(
                        index=next_call,
                        call_id=get_str(call, "id") or f"gemini-{uuid4().hex}",
                        name=name,
                        arguments_delta=json.dumps(arguments or {}, ensure_ascii=False),
                        signature_delta=signature,
                    )
                    next_call += 1
                    last_kind = "tool"
                elif "text" in part or signature:
                    # A signature-only final chunk belongs to the preceding part.
                    kind = "reasoning" if part.get("thought") is True else "text"
                    if (
                        "text" not in part
                        and "thought" not in part
                        and signature
                        and last_kind in ("text", "reasoning")
                    ):
                        kind = last_kind
                    start = position > 0 or last_kind != kind or last_signed
                    text = get_str(part, "text") or ""
                    if kind == "reasoning":
                        yield ReasoningDeltaEvent(
                            text,
                            signature_delta=signature,
                            part_start=start,
                            signature_protocol=WireProtocol.GOOGLE_GENAI,
                        )
                    else:
                        yield TextDeltaEvent(text, signature_delta=signature, part_start=start)
                    last_kind = kind
                last_signed = bool(signature)
            reason = get_str(candidate, "finishReason")
            if reason:
                if reason in ("MALFORMED_FUNCTION_CALL", "UNEXPECTED_TOOL_CALL"):
                    raise LLMError(ErrorCategory.PROTOCOL, f"Gemini stopped with {reason}")
                if reason not in ("STOP", "MAX_TOKENS", "FINISH_REASON_UNSPECIFIED"):
                    raise LLMError(ErrorCategory.INVALID_REQUEST, f"Gemini stopped with {reason}")
                finish = (
                    "length" if reason == "MAX_TOKENS" else "tool_calls" if next_call else "stop"
                )
    if not saw_frame or finish is None:
        raise LLMError(
            ErrorCategory.PROTOCOL, "Gemini stream ended without a finish reason", retryable=True
        )
    yield DoneEvent(finish)
