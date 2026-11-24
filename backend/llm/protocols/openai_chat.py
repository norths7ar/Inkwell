"""OpenAI Chat Completions (/chat/completions) wire protocol driver."""

from __future__ import annotations

from collections.abc import AsyncIterator

import httpx

from backend.llm._json import JsonObject, JsonValue, as_list, as_object, get_int, get_str
from backend.llm.errors import classify_http_error
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
)


def _encode_user_or_system_message(msg: LLMMessage) -> JsonObject:
    role_str = "system" if msg.role == Role.SYSTEM else "user"
    has_images = any(isinstance(block, ImageBlock) for block in msg.blocks)
    if not has_images or msg.role == Role.SYSTEM:
        text = "\n\n".join(
            block.text for block in msg.blocks if isinstance(block, TextBlock) and block.text
        )
        return {"role": role_str, "content": text}

    parts: list[JsonValue] = []
    for block in msg.blocks:
        if isinstance(block, TextBlock) and block.text:
            parts.append({"type": "text", "text": block.text})
        elif isinstance(block, ImageBlock):
            url = image_url_value(block)
            if url:
                parts.append({"type": "image_url", "image_url": {"url": url}})
    return {"role": role_str, "content": parts}


def _encode_messages(
    messages: tuple[LLMMessage, ...],
    *,
    replay_reasoning_content: bool,
) -> list[JsonValue]:
    encoded: list[JsonValue] = []
    for msg in messages:
        if msg.role in (Role.SYSTEM, Role.USER):
            encoded.append(_encode_user_or_system_message(msg))
            continue

        if msg.role == Role.ASSISTANT:
            text_parts: list[str] = []
            reasoning_parts: list[str] = []
            tool_calls: list[JsonValue] = []
            for block in msg.blocks:
                if isinstance(block, TextBlock) and block.text:
                    text_parts.append(block.text)
                elif isinstance(block, ReasoningBlock) and block.text:
                    reasoning_parts.append(block.text)
                elif isinstance(block, ToolCallBlock):
                    tool_calls.append(
                        {
                            "id": block.call_id,
                            "type": "function",
                            "function": {
                                "name": block.name,
                                "arguments": block.arguments_json,
                            },
                        }
                    )
            item: JsonObject = {
                "role": "assistant",
                "content": "".join(text_parts) if text_parts else None,
            }
            if replay_reasoning_content and reasoning_parts:
                item["reasoning_content"] = "".join(reasoning_parts)
            if tool_calls:
                item["tool_calls"] = tool_calls
            encoded.append(item)
            continue

        for block in msg.blocks:
            if isinstance(block, ToolResultBlock):
                encoded.append(
                    {
                        "role": "tool",
                        "tool_call_id": block.call_id,
                        "content": block.content,
                    }
                )
    return encoded


def build_request(
    endpoint: EndpointConfig,
    request: GenerateRequest,
) -> tuple[str, dict[str, str], JsonObject]:
    """Build URL, headers, and JSON body for Chat Completions streaming."""
    url = join_endpoint_path(endpoint.base_url, "/chat/completions")
    headers = build_auth_headers(endpoint)
    payload: JsonObject = {
        "model": request.model,
        "messages": _encode_messages(
            request.messages,
            replay_reasoning_content=endpoint.quirks.replay_reasoning_content,
        ),
        "stream": True,
        "stream_options": {"include_usage": True},
    }
    if request.tools:
        payload["tools"] = [
            {
                "type": "function",
                "function": {
                    "name": tool.name,
                    "description": tool.description,
                    "parameters": tool.input_schema,
                },
            }
            for tool in request.tools
        ]
    if request.temperature is not None:
        payload["temperature"] = request.temperature
    if request.max_output_tokens is not None:
        payload["max_tokens"] = request.max_output_tokens
    if request.reasoning_effort is not None:
        payload[endpoint.quirks.reasoning_effort_param] = request.reasoning_effort

    merge_extra_body(payload, request.extra_body)
    return url, headers, payload


def _parse_usage(usage_obj: JsonObject) -> TokenUsage:
    input_tokens = get_int(usage_obj, "prompt_tokens") or get_int(usage_obj, "input_tokens") or 0
    output_tokens = (
        get_int(usage_obj, "completion_tokens") or get_int(usage_obj, "output_tokens") or 0
    )
    cache_read = get_int(usage_obj, "cache_read_input_tokens") or 0
    prompt_details = as_object(usage_obj.get("prompt_tokens_details"))
    if prompt_details is not None:
        cached = get_int(prompt_details, "cached_tokens")
        if cached is not None:
            cache_read = cached
    cache_write = get_int(usage_obj, "cache_creation_input_tokens") or 0
    return TokenUsage(
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cache_read_tokens=cache_read,
        cache_write_tokens=cache_write,
    )


async def decode_stream(response: httpx.Response) -> AsyncIterator[StreamEvent]:
    """Decode a Chat Completions SSE response into normalized StreamEvents."""
    finish_reason: str | None = None
    seen_tool_names: dict[int, str] = {}

    async for frame in iter_sse_frames(response):
        obj = parse_sse_json(frame.data)
        err_obj = as_object(obj.get("error"))
        if err_obj is not None:
            status = get_int(err_obj, "code") or 502
            raise classify_http_error(status, frame.data, response.headers)

        usage_obj = as_object(obj.get("usage"))
        if usage_obj is not None:
            yield UsageEvent(_parse_usage(usage_obj))

        choices = as_list(obj.get("choices"))
        if not choices:
            continue
        choice = as_object(choices[0])
        if choice is None:
            continue

        choice_finish = get_str(choice, "finish_reason")
        if choice_finish:
            finish_reason = choice_finish

        delta = as_object(choice.get("delta"))
        if delta is None:
            continue

        reasoning = get_str(delta, "reasoning_content") or get_str(delta, "reasoning")
        if reasoning:
            yield ReasoningDeltaEvent(reasoning)

        tool_calls = as_list(delta.get("tool_calls"))
        if tool_calls:
            for item in tool_calls:
                tc_obj = as_object(item)
                if tc_obj is None:
                    continue
                index = get_int(tc_obj, "index") or 0
                call_id = get_str(tc_obj, "id")
                fn_obj = as_object(tc_obj.get("function"))
                raw_name = get_str(fn_obj, "name") if fn_obj is not None else None
                emitted_name: str | None = None
                if raw_name and seen_tool_names.get(index) != raw_name:
                    seen_tool_names[index] = raw_name
                    emitted_name = raw_name
                args_delta = (get_str(fn_obj, "arguments") if fn_obj is not None else None) or ""
                if call_id is not None or emitted_name is not None or args_delta:
                    yield ToolCallDeltaEvent(
                        index=index,
                        call_id=call_id,
                        name=emitted_name,
                        arguments_delta=args_delta,
                    )

        content = get_str(delta, "content")
        if content:
            yield TextDeltaEvent(content)

    yield DoneEvent(finish_reason=finish_reason)
