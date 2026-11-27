"""Anthropic Messages (/messages) wire protocol driver."""

from __future__ import annotations

from collections.abc import AsyncIterator

import httpx

from backend.llm._json import JsonObject, JsonValue, as_object, get_int, get_str, parse_json_object
from backend.llm.errors import ErrorCategory, LLMError
from backend.llm.protocols._shared import (
    build_auth_headers,
    join_endpoint_path,
    merge_extra_body,
    parse_sse_json,
)
from backend.llm.sse import iter_sse_frames
from backend.llm.types import (
    DoneEvent,
    EndpointConfig,
    GenerateRequest,
    ImageBlock,
    LLMMessage,
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

ANTHROPIC_VERSION = "2023-06-01"
DEFAULT_MAX_TOKENS = 4096


def _parse_tool_input(arguments_json: str) -> JsonObject:
    raw = arguments_json.strip()
    if not raw:
        return {}
    try:
        return parse_json_object(raw)
    except ValueError:
        return {}


def _encode_image_block(block: ImageBlock) -> JsonObject | None:
    if block.data_base64:
        return {
            "type": "image",
            "source": {
                "type": "base64",
                "media_type": block.media_type,
                "data": block.data_base64,
            },
        }
    if block.url:
        return {
            "type": "image",
            "source": {
                "type": "url",
                "url": block.url,
            },
        }
    return None


def _encode_messages(messages: tuple[LLMMessage, ...]) -> tuple[str | None, list[JsonValue]]:
    system_parts: list[str] = []
    turns: list[tuple[str, list[JsonValue]]] = []

    for msg in messages:
        if msg.role == Role.SYSTEM:
            for block in msg.blocks:
                if isinstance(block, TextBlock) and block.text:
                    system_parts.append(block.text)
            continue

        if msg.role == Role.USER:
            blocks: list[JsonValue] = []
            for block in msg.blocks:
                if isinstance(block, TextBlock) and block.text:
                    blocks.append({"type": "text", "text": block.text})
                elif isinstance(block, ImageBlock):
                    img = _encode_image_block(block)
                    if img is not None:
                        blocks.append(img)
            if blocks:
                turns.append(("user", blocks))
            continue

        if msg.role == Role.ASSISTANT:
            blocks = []
            for block in msg.blocks:
                if isinstance(block, ReasoningBlock):
                    if block.signature and block.signature_protocol != WireProtocol.GOOGLE_GENAI:
                        blocks.append(
                            {
                                "type": "thinking",
                                "thinking": block.text,
                                "signature": block.signature,
                            }
                        )
                elif isinstance(block, TextBlock) and block.text:
                    blocks.append({"type": "text", "text": block.text})
                elif isinstance(block, ToolCallBlock):
                    blocks.append(
                        {
                            "type": "tool_use",
                            "id": block.call_id,
                            "name": block.name,
                            "input": _parse_tool_input(block.arguments_json),
                        }
                    )
            if blocks:
                turns.append(("assistant", blocks))
            continue

        tool_results: list[JsonValue] = [
            {
                "type": "tool_result",
                "tool_use_id": block.call_id,
                "content": block.content,
                "is_error": block.is_error,
            }
            for block in msg.blocks
            if isinstance(block, ToolResultBlock)
        ]
        if tool_results:
            turns.append(("user", tool_results))

    merged: list[JsonValue] = []
    merged_roles: list[str] = []
    merged_contents: list[list[JsonValue]] = []
    for role_str, content_list in turns:
        if merged_roles and merged_roles[-1] == role_str:
            merged_contents[-1].extend(content_list)
        else:
            merged_roles.append(role_str)
            merged_contents.append(list(content_list))
    for role_str, content_list in zip(merged_roles, merged_contents, strict=True):
        merged.append({"role": role_str, "content": content_list})

    system_text = "\n\n".join(system_parts) if system_parts else None
    return system_text, merged


def build_request(
    endpoint: EndpointConfig,
    request: GenerateRequest,
) -> tuple[str, dict[str, str], JsonObject]:
    """Build URL, headers, and JSON body for Anthropic Messages streaming."""
    url = join_endpoint_path(endpoint.base_url, "/messages")
    headers = build_auth_headers(endpoint)
    headers.setdefault("anthropic-version", ANTHROPIC_VERSION)

    system_text, messages_payload = _encode_messages(request.messages)
    payload: JsonObject = {
        "model": request.model,
        "messages": messages_payload,
        "max_tokens": request.max_output_tokens or DEFAULT_MAX_TOKENS,
        "stream": True,
    }
    if system_text is not None:
        payload["system"] = system_text
    if request.tools:
        payload["tools"] = [
            {
                "name": tool.name,
                "description": tool.description,
                "input_schema": tool.input_schema,
            }
            for tool in request.tools
        ]
    if request.temperature is not None:
        payload["temperature"] = request.temperature
    if request.reasoning_effort is not None:
        payload["output_config"] = {"effort": request.reasoning_effort}

    merge_extra_body(payload, request.extra_body)
    return url, headers, payload


async def decode_stream(response: httpx.Response) -> AsyncIterator[StreamEvent]:
    """Decode an Anthropic Messages SSE stream into normalized StreamEvents."""
    input_tokens = 0
    output_tokens = 0
    cache_read_tokens = 0
    cache_write_tokens = 0
    stop_reason: str | None = None
    emitted_done = False

    block_to_tool_index: dict[int, int] = {}
    next_tool_index = 0

    async for frame in iter_sse_frames(response):
        obj = parse_sse_json(frame.data)
        event_type = get_str(obj, "type") or frame.event or ""

        if event_type == "message_start":
            message_obj = as_object(obj.get("message"))
            usage_obj = as_object(message_obj.get("usage")) if message_obj is not None else None
            if usage_obj is not None:
                input_tokens = get_int(usage_obj, "input_tokens") or 0
                cache_read_tokens = get_int(usage_obj, "cache_read_input_tokens") or 0
                cache_write_tokens = get_int(usage_obj, "cache_creation_input_tokens") or 0
            continue

        if event_type == "content_block_start":
            block_idx = get_int(obj, "index") or 0
            block_obj = as_object(obj.get("content_block"))
            if block_obj is not None and get_str(block_obj, "type") == "tool_use":
                tool_idx = next_tool_index
                next_tool_index += 1
                block_to_tool_index[block_idx] = tool_idx
                yield ToolCallDeltaEvent(
                    index=tool_idx,
                    call_id=get_str(block_obj, "id"),
                    name=get_str(block_obj, "name"),
                )
            continue

        if event_type == "content_block_delta":
            block_idx = get_int(obj, "index") or 0
            delta = as_object(obj.get("delta"))
            if delta is None:
                continue
            delta_type = get_str(delta, "type")
            if delta_type == "text_delta":
                text = get_str(delta, "text")
                if text:
                    yield TextDeltaEvent(text)
            elif delta_type == "thinking_delta":
                thinking = get_str(delta, "thinking")
                if thinking:
                    yield ReasoningDeltaEvent(thinking)
            elif delta_type == "signature_delta":
                signature = get_str(delta, "signature")
                if signature:
                    yield ReasoningDeltaEvent("", signature_delta=signature)
            elif delta_type == "input_json_delta":
                partial = get_str(delta, "partial_json") or ""
                if partial:
                    tool_idx = block_to_tool_index.get(block_idx, 0)
                    yield ToolCallDeltaEvent(index=tool_idx, arguments_delta=partial)
            continue

        if event_type == "message_delta":
            delta = as_object(obj.get("delta"))
            if delta is not None:
                reason = get_str(delta, "stop_reason")
                if reason:
                    stop_reason = reason
            usage_obj = as_object(obj.get("usage"))
            if usage_obj is not None:
                out_val = get_int(usage_obj, "output_tokens")
                if out_val is not None:
                    output_tokens = out_val
                in_val = get_int(usage_obj, "input_tokens")
                if in_val is not None:
                    input_tokens = in_val
                cr_val = get_int(usage_obj, "cache_read_input_tokens")
                if cr_val is not None:
                    cache_read_tokens = cr_val
                cw_val = get_int(usage_obj, "cache_creation_input_tokens")
                if cw_val is not None:
                    cache_write_tokens = cw_val
            continue

        if event_type == "message_stop":
            yield UsageEvent(
                TokenUsage(
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                    cache_read_tokens=cache_read_tokens,
                    cache_write_tokens=cache_write_tokens,
                )
            )
            yield DoneEvent(finish_reason=stop_reason)
            emitted_done = True
            continue

        if event_type == "error":
            err_obj = as_object(obj.get("error"))
            msg = (
                (get_str(err_obj, "message") if err_obj is not None else None)
                or get_str(obj, "message")
                or "Upstream Anthropic stream error"
            )
            raise LLMError(ErrorCategory.SERVER, msg, retryable=True, raw_body=frame.data)

    if not emitted_done:
        yield UsageEvent(
            TokenUsage(
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                cache_read_tokens=cache_read_tokens,
                cache_write_tokens=cache_write_tokens,
            )
        )
        yield DoneEvent(finish_reason=stop_reason)
