"""Strict JSON serialization between persisted SQLite messages and LLMMessage IR."""

from __future__ import annotations

import json

from backend.llm._json import (
    JsonObject,
    JsonValue,
    as_list,
    as_object,
    get_str,
    parse_json_value,
)
from backend.llm.types import (
    ContentBlock,
    ImageBlock,
    LLMMessage,
    ReasoningBlock,
    Role,
    TextBlock,
    ToolCallBlock,
    ToolResultBlock,
    WireProtocol,
)
from backend.storage.models import Message, MessageRole


def _encode_block(block: ContentBlock) -> JsonObject:
    if isinstance(block, TextBlock):
        text_obj: JsonObject = {"type": "text", "text": block.text}
        if block.signature is not None:
            text_obj["signature"] = block.signature
        return text_obj
    if isinstance(block, ReasoningBlock):
        obj: JsonObject = {"type": "reasoning", "text": block.text}
        if block.signature is not None:
            obj["signature"] = block.signature
        if block.duration_ms is not None:
            obj["duration_ms"] = block.duration_ms
        if block.signature_protocol is not None:
            obj["signature_protocol"] = block.signature_protocol.value
        return obj
    if isinstance(block, ToolCallBlock):
        call_obj: JsonObject = {
            "type": "tool_call",
            "call_id": block.call_id,
            "name": block.name,
            "arguments_json": block.arguments_json,
        }
        if block.signature is not None:
            call_obj["signature"] = block.signature
        return call_obj
    if isinstance(block, ToolResultBlock):
        return {
            "type": "tool_result",
            "call_id": block.call_id,
            "name": block.name,
            "content": block.content,
            "is_error": block.is_error,
        }
    img: JsonObject = {"type": "image", "media_type": block.media_type}
    if block.data_base64 is not None:
        img["data_base64"] = block.data_base64
    if block.url is not None:
        img["url"] = block.url
    return img


def serialize_blocks(blocks: tuple[ContentBlock, ...]) -> str:
    """Serialize structured content blocks to JSON for SQLite message storage."""
    payload: list[JsonValue] = [_encode_block(block) for block in blocks]
    return json.dumps(payload, ensure_ascii=False)


def _decode_block(obj: JsonObject) -> ContentBlock | None:
    kind = get_str(obj, "type")
    if kind == "text":
        return TextBlock(text=get_str(obj, "text") or "", signature=get_str(obj, "signature"))
    if kind == "reasoning":
        protocol = get_str(obj, "signature_protocol")
        duration = obj.get("duration_ms")
        return ReasoningBlock(
            text=get_str(obj, "text") or "",
            signature=get_str(obj, "signature"),
            duration_ms=duration
            if isinstance(duration, int) and not isinstance(duration, bool)
            else None,
            signature_protocol=WireProtocol(protocol) if protocol in WireProtocol else None,
        )
    if kind == "tool_call":
        return ToolCallBlock(
            call_id=get_str(obj, "call_id") or "",
            name=get_str(obj, "name") or "",
            arguments_json=get_str(obj, "arguments_json") or "{}",
            signature=get_str(obj, "signature"),
        )
    if kind == "tool_result":
        raw_err = obj.get("is_error")
        return ToolResultBlock(
            call_id=get_str(obj, "call_id") or "",
            name=get_str(obj, "name") or "",
            content=get_str(obj, "content") or "",
            is_error=raw_err if isinstance(raw_err, bool) else False,
        )
    if kind == "image":
        return ImageBlock(
            media_type=get_str(obj, "media_type") or "image/png",
            data_base64=get_str(obj, "data_base64"),
            url=get_str(obj, "url"),
        )
    return None


def deserialize_blocks(blocks_json: str) -> tuple[ContentBlock, ...]:
    """Deserialize JSON blocks from SQLite back into typed ContentBlock items."""
    raw = blocks_json.strip()
    if not raw:
        return ()
    items = as_list(parse_json_value(raw))
    if items is None:
        raise ValueError("Expected JSON array for message blocks")
    blocks: list[ContentBlock] = []
    for item in items:
        obj = as_object(item)
        if obj is None:
            continue
        decoded = _decode_block(obj)
        if decoded is not None:
            blocks.append(decoded)
    return tuple(blocks)


_ROLE_MAP: dict[MessageRole, Role] = {
    MessageRole.SYSTEM: Role.SYSTEM,
    MessageRole.USER: Role.USER,
    MessageRole.ASSISTANT: Role.ASSISTANT,
    MessageRole.TOOL: Role.TOOL,
}


def message_to_llm(message: Message) -> LLMMessage:
    """Convert a persisted SQLite Message into an LLMMessage IR."""
    return LLMMessage(
        role=_ROLE_MAP[message.role],
        blocks=deserialize_blocks(message.blocks_json),
    )
