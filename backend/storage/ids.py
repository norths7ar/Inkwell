"""Identifier and timestamp helpers."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4


def new_id() -> str:
    """Return a compact, URL-safe unique identifier."""
    return uuid4().hex


def utc_now() -> str:
    """Return the current UTC time in ISO-8601 format."""
    return datetime.now(UTC).isoformat()
