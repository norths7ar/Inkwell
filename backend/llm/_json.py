"""Strict JSON types and coercion helpers avoiding Any across protocol boundaries."""

from __future__ import annotations

import json

type JsonScalar = str | int | float | bool | None
type JsonValue = JsonScalar | list["JsonValue"] | dict[str, "JsonValue"]
type JsonObject = dict[str, JsonValue]


def coerce_json_value(raw: object) -> JsonValue:
    """Validate and convert an arbitrary decoded Python value into JsonValue."""
    if raw is None or isinstance(raw, (bool, int, float, str)):
        return raw
    if isinstance(raw, list):
        return [coerce_json_value(item) for item in raw]
    if isinstance(raw, dict):
        return {str(key): coerce_json_value(val) for key, val in raw.items()}
    raise ValueError(f"Unsupported JSON value type: {type(raw).__name__}")


def parse_json_value(text: str) -> JsonValue:
    """Parse a JSON string into a strictly typed JsonValue."""
    raw: object = json.loads(text)
    return coerce_json_value(raw)


def parse_json_object(text: str) -> JsonObject:
    """Parse a JSON string expected to be a JSON object."""
    parsed = parse_json_value(text)
    if not isinstance(parsed, dict):
        raise ValueError(f"Expected JSON object, got {type(parsed).__name__}")
    return parsed


def as_object(value: JsonValue | None) -> JsonObject | None:
    return value if isinstance(value, dict) else None


def as_list(value: JsonValue | None) -> list[JsonValue] | None:
    return value if isinstance(value, list) else None


def get_str(obj: JsonObject, key: str) -> str | None:
    val = obj.get(key)
    return val if isinstance(val, str) else None


def get_int(obj: JsonObject, key: str) -> int | None:
    val = obj.get(key)
    if isinstance(val, bool):
        return None
    return val if isinstance(val, int) else None
