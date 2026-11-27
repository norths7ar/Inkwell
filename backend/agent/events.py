"""Domain events emitted by the pure-Python Agent Loop."""

from __future__ import annotations

import inspect
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from backend.errors import ErrorInfo
from backend.llm.types import TokenUsage
from backend.storage.models import PendingChange, RunStatus


@dataclass(frozen=True, slots=True)
class RunStartedEvent:
    run_id: str
    session_id: str
    book_id: str


@dataclass(frozen=True, slots=True)
class ReasoningDeltaEvent:
    run_id: str
    delta: str
    iteration: int = 0
    block_index: int = 0
    duration_ms: int = 0


@dataclass(frozen=True, slots=True)
class ReasoningCompletedEvent:
    run_id: str
    iteration: int
    block_index: int
    duration_ms: int


@dataclass(frozen=True, slots=True)
class TextDeltaEvent:
    run_id: str
    delta: str


@dataclass(frozen=True, slots=True)
class AttemptResetEvent:
    run_id: str
    attempt: int
    reason: str
    error: ErrorInfo | None = None


@dataclass(frozen=True, slots=True)
class ToolStartedEvent:
    run_id: str
    call_id: str
    name: str
    arguments_json: str


@dataclass(frozen=True, slots=True)
class ToolFinishedEvent:
    run_id: str
    call_id: str
    name: str
    content: str
    is_error: bool


@dataclass(frozen=True, slots=True)
class PendingChangeCreatedEvent:
    run_id: str
    change: PendingChange


@dataclass(frozen=True, slots=True)
class PendingChangeUpdatedEvent:
    run_id: str
    change: PendingChange


@dataclass(frozen=True, slots=True)
class WaitingUserEvent:
    run_id: str
    question: str
    options: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class RunFinishedEvent:
    run_id: str
    status: RunStatus
    error_message: str | None = None
    usage: TokenUsage = TokenUsage()
    error: ErrorInfo | None = None


type AgentEvent = (
    RunStartedEvent
    | ReasoningDeltaEvent
    | ReasoningCompletedEvent
    | TextDeltaEvent
    | AttemptResetEvent
    | ToolStartedEvent
    | ToolFinishedEvent
    | PendingChangeCreatedEvent
    | PendingChangeUpdatedEvent
    | WaitingUserEvent
    | RunFinishedEvent
)

type EventSink = Callable[[AgentEvent], Awaitable[None] | None]


async def emit_event(sink: EventSink | None, event: AgentEvent) -> None:
    """Deliver an AgentEvent to a sync or async sink."""
    if sink is None:
        return
    result = sink(event)
    if inspect.isawaitable(result):
        await result
