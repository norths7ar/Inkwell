"""Pure-Python async Agent Loop, context builder, mode prompts, and unified tools."""

from backend.agent.context import GLOBAL_RULES_SETTING_KEY, build_context
from backend.agent.events import (
    AgentEvent,
    AttemptResetEvent,
    EventSink,
    PendingChangeCreatedEvent,
    ReasoningDeltaEvent,
    RunFinishedEvent,
    RunStartedEvent,
    TextDeltaEvent,
    ToolFinishedEvent,
    ToolStartedEvent,
    WaitingUserEvent,
    emit_event,
)
from backend.agent.loop import AgentRunner
from backend.agent.prompts import DEFAULT_MODE_PROMPTS, mode_prompt_setting_key, resolve_mode_prompt
from backend.agent.serialization import deserialize_blocks, message_to_llm, serialize_blocks
from backend.agent.tools import (
    BoundTool,
    ToolContext,
    ToolExecutionResult,
    ToolFailure,
    tools_for_mode,
)

__all__ = [
    "AgentEvent",
    "AgentRunner",
    "AttemptResetEvent",
    "BoundTool",
    "DEFAULT_MODE_PROMPTS",
    "EventSink",
    "GLOBAL_RULES_SETTING_KEY",
    "PendingChangeCreatedEvent",
    "ReasoningDeltaEvent",
    "RunFinishedEvent",
    "RunStartedEvent",
    "TextDeltaEvent",
    "ToolContext",
    "ToolExecutionResult",
    "ToolFailure",
    "ToolFinishedEvent",
    "ToolStartedEvent",
    "WaitingUserEvent",
    "build_context",
    "deserialize_blocks",
    "emit_event",
    "message_to_llm",
    "mode_prompt_setting_key",
    "resolve_mode_prompt",
    "serialize_blocks",
    "tools_for_mode",
]
