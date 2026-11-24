"""Server-Sent Events (SSE) frame decoder for streaming LLM responses."""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass

import httpx


@dataclass(frozen=True, slots=True)
class SSEFrame:
    event: str | None
    data: str


def _strip_single_leading_space(value: str) -> str:
    return value[1:] if value.startswith(" ") else value


async def iter_sse_frames(response: httpx.Response) -> AsyncIterator[SSEFrame]:
    """Decode an httpx streaming response into SSEFrame items until EOF or [DONE]."""
    current_event: str | None = None
    data_lines: list[str] = []

    async for line in response.aiter_lines():
        if not line:
            if data_lines:
                data = "\n".join(data_lines)
                event_name = current_event
                current_event = None
                data_lines = []
                if data == "[DONE]":
                    return
                yield SSEFrame(event=event_name, data=data)
            else:
                current_event = None
            continue

        if line.startswith(":"):
            continue
        if line.startswith("event:"):
            current_event = _strip_single_leading_space(line[6:])
        elif line.startswith("data:"):
            data_lines.append(_strip_single_leading_space(line[5:]))

    if data_lines:
        data = "\n".join(data_lines)
        if data != "[DONE]":
            yield SSEFrame(event=current_event, data=data)
