"""OpenAI Responses API (/responses) wire protocol driver."""

from __future__ import annotations

from collections.abc import AsyncIterator

import httpx

from backend.llm._json import JsonObject, JsonValue, as_object, get_int, get_str
from backend.llm.errors import ErrorCategory, LLMError
from backend.llm.protocols._shared import (
    build_auth_headers,
    image_url_value,
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
)


def _encode_input(messages: tuple[LLMMessage, ...]) -> tuple[str | None, list[JsonValue]]:
    instructions_parts: list[str] = []
    input_items: list[JsonValue] = []

    for msg in messages:
        if msg.role == Role.SYSTEM:
            for block in msg.blocks:
                if isinstance(block, TextBlock) and block.text:
                    instructions_parts.append(block.text)
            continue

        if msg.role == Role.USER:
            parts: list[JsonValue] = []
            for block in msg.blocks:
                if isinstance(block, TextBlock) and block.text:
                    parts.append({"type": "input_text", "text": block.text})
                elif isinstance(block, ImageBlock):
                    url = image_url_value(block)
                    if url:
                        parts.append({"type": "input_image", "image_url": url})
            if parts:
                input_items.append({"type": "message", "role": "user", "content": parts})
            continue

        if msg.role == Role.ASSISTANT:
            text_parts: list[JsonValue] = []
            tool_items: list[JsonValue] = []
            for block in msg.blocks:
                if isinstance(block, TextBlock) and block.text:
                    text_parts.append({"type": "output_text", "text": block.text})
                elif isinstance(block, ToolCallBlock):
                    tool_items.append(
                        {
                            "type": "function_call",
                            "call_id": block.call_id,
                            "name": block.name,
                            "arguments": block.arguments_json,
                        }
                    )
            if text_parts:
                input_items.append({"type": "message", "role": "assistant", "content": text_parts})
            input_items.extend(tool_items)
            continue

        for block in msg.blocks:
            if isinstance(block, ToolResultBlock):
                input_items.append(
                    {
                        "type": "function_call_output",
                        "call_id": block.call_id,
                        "output": block.content,
                    }
                )

    instructions = "\n\n".join(instructions_parts) if instructions_parts else None
    return instructions, input_items


def build_request(
    endpoint: EndpointConfig,
    request: GenerateRequest,
) -> tuple[str, dict[str, str], JsonObject]:
    """Build URL, headers, and JSON body for Responses API streaming."""
    url = join_endpoint_path(endpoint.base_url, "/responses")
    headers = build_auth_headers(endpoint)
    instructions, input_items = _encode_input(request.messages)

    payload: JsonObject = {
        "model": request.model,
        "input": input_items,
        "stream": True,
    }
    if instructions is not None:
        payload["instructions"] = instructions
    if request.tools:
        payload["tools"] = [
            {
                "type": "function",
                "name": tool.name,
                "description": tool.description,
                "parameters": tool.input_schema,
            }
            for tool in request.tools
        ]
    if request.temperature is not None:
        payload["temperature"] = request.temperature
    if request.max_output_tokens is not None:
        payload["max_output_tokens"] = request.max_output_tokens
    if request.reasoning_effort is not None:
        payload["reasoning"] = {"effort": request.reasoning_effort}

    merge_extra_body(payload, request.extra_body)
    return url, headers, payload


def _parse_responses_usage(usage_obj: JsonObject) -> TokenUsage:
    input_tokens = get_int(usage_obj, "input_tokens") or 0
    output_tokens = get_int(usage_obj, "output_tokens") or 0
    cache_read = 0
    input_details = as_object(usage_obj.get("input_tokens_details"))
    if input_details is not None:
        cache_read = get_int(input_details, "cached_tokens") or 0
    return TokenUsage(
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cache_read_tokens=cache_read,
    )


async def decode_stream(response: httpx.Response) -> AsyncIterator[StreamEvent]:
    """Decode an OpenAI Responses SSE stream into normalized StreamEvents."""
    finish_reason: str | None = None
    emitted_done = False
    item_id_to_tool_index: dict[str, int] = {}
    output_idx_to_tool_index: dict[int, int] = {}
    next_tool_index = 0

    async for frame in iter_sse_frames(response):
        obj = parse_sse_json(frame.data)
        event_type = get_str(obj, "type") or frame.event or ""

        if event_type == "response.output_text.delta":
            delta = get_str(obj, "delta")
            if delta:
                yield TextDeltaEvent(delta)
            continue

        if event_type in (
            "response.reasoning_summary_text.delta",
            "response.reasoning_text.delta",
        ):
            delta = get_str(obj, "delta")
            if delta:
                yield ReasoningDeltaEvent(delta)
            continue

        if event_type == "response.output_item.added":
            item = as_object(obj.get("item"))
            if item is not None and get_str(item, "type") == "function_call":
                tool_idx = next_tool_index
                next_tool_index += 1
                item_id = get_str(item, "id")
                if item_id:
                    item_id_to_tool_index[item_id] = tool_idx
                output_idx = get_int(obj, "output_index")
                if output_idx is not None:
                    output_idx_to_tool_index[output_idx] = tool_idx
                yield ToolCallDeltaEvent(
                    index=tool_idx,
                    call_id=get_str(item, "call_id"),
                    name=get_str(item, "name"),
                    arguments_delta=get_str(item, "arguments") or "",
                )
            continue

        if event_type == "response.function_call_arguments.delta":
            item_id = get_str(obj, "item_id")
            output_idx = get_int(obj, "output_index")
            tool_idx = item_id_to_tool_index.get(item_id) if item_id is not None else None
            if tool_idx is None and output_idx is not None:
                tool_idx = output_idx_to_tool_index.get(output_idx)
            if tool_idx is None:
                tool_idx = 0
            delta = get_str(obj, "delta") or ""
            if delta:
                yield ToolCallDeltaEvent(index=tool_idx, arguments_delta=delta)
            continue

        if event_type in ("response.completed", "response.incomplete"):
            resp_obj = as_object(obj.get("response"))
            if resp_obj is not None:
                usage_obj = as_object(resp_obj.get("usage"))
                if usage_obj is not None:
                    yield UsageEvent(_parse_responses_usage(usage_obj))
                incomplete = as_object(resp_obj.get("incomplete_details"))
                finish_reason = (
                    get_str(incomplete, "reason") if incomplete is not None else None
                ) or get_str(resp_obj, "status")
            yield DoneEvent(finish_reason=finish_reason)
            emitted_done = True
            continue

        if event_type in ("response.failed", "error"):
            err_obj = as_object(obj.get("error"))
            resp_obj = as_object(obj.get("response"))
            if err_obj is None and resp_obj is not None:
                err_obj = as_object(resp_obj.get("error"))
            msg = (
                (get_str(err_obj, "message") if err_obj is not None else None)
                or get_str(obj, "message")
                or "Upstream Responses stream error"
            )
            raise LLMError(ErrorCategory.SERVER, msg, retryable=True, raw_body=frame.data)

    if not emitted_done:
        yield DoneEvent(finish_reason=finish_reason)
