"""Bounded, process-local SSE replay with no per-client unbounded queues."""

import asyncio
import json
import time
from collections import deque
from collections.abc import AsyncIterator
from dataclasses import dataclass

from pydantic import JsonValue


@dataclass(frozen=True)
class Event:
    id: int
    name: str
    data: dict[str, JsonValue]

    def encode(self) -> str:
        return f"id: {self.id}\nevent: {self.name}\ndata: {json.dumps(self.data, ensure_ascii=False)}\n\n"


class EventBus:
    def __init__(self, capacity: int = 2048, heartbeat: float = 15.0) -> None:
        self._events: deque[Event] = deque(maxlen=capacity)
        self._sequence = time.time_ns()
        self._initial_sequence = self._sequence
        self._changed = asyncio.Event()
        self._closed = False
        self.heartbeat = heartbeat

    def publish(self, event: str, data: dict[str, JsonValue]) -> Event:
        self._sequence += 1
        item = Event(self._sequence, event, data)
        self._events.append(item)
        self._changed.set()
        self._changed = asyncio.Event()
        return item

    def close(self) -> None:
        self._closed = True
        self._changed.set()

    async def stream(self, last_id: int | None = None) -> AsyncIterator[str]:
        cursor = self._sequence if last_id is None else last_id
        yield ": connected\n\n"
        while not self._closed:
            changed = self._changed
            oldest_cursor = self._events[0].id - 1 if self._events else self._initial_sequence
            if cursor > self._sequence or cursor < oldest_cursor:
                # A stale/restarted cursor must trigger a REST refresh, never silent loss.
                yield Event(
                    self._sequence, "resync_required", {"reason": "replay_unavailable"}
                ).encode()
                cursor = self._sequence
            for event in tuple(self._events):
                if event.id > cursor:
                    yield event.encode()
                    cursor = event.id
            try:
                await asyncio.wait_for(changed.wait(), self.heartbeat)
            except TimeoutError:
                yield ": heartbeat\n\n"
