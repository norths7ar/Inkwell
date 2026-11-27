"""Protocol-agnostic internal representation (IR) for LLM requests and streams."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from backend.llm._json import JsonObject


class WireProtocol(StrEnum):
    OPENAI_CHAT = "openai-chat"
    OPENAI_RESPONSES = "openai-responses"
    ANTHROPIC_MESSAGES = "anthropic-messages"
    GOOGLE_GENAI = "google-genai"


class AuthStyle(StrEnum):
    BEARER = "bearer"
    X_API_KEY = "x-api-key"
    X_GOOG_API_KEY = "x-goog-api-key"
    NONE = "none"


class Role(StrEnum):
    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"
    TOOL = "tool"


@dataclass(frozen=True, slots=True)
class TextBlock:
    text: str
    signature: str | None = None


@dataclass(frozen=True, slots=True)
class ReasoningBlock:
    text: str
    signature: str | None = None
    duration_ms: int | None = None
    signature_protocol: WireProtocol | None = None


@dataclass(frozen=True, slots=True)
class ToolCallBlock:
    call_id: str
    name: str
    arguments_json: str
    signature: str | None = None


@dataclass(frozen=True, slots=True)
class ToolResultBlock:
    call_id: str
    name: str
    content: str
    is_error: bool = False


@dataclass(frozen=True, slots=True)
class ImageBlock:
    media_type: str
    data_base64: str | None = None
    url: str | None = None


type ContentBlock = TextBlock | ReasoningBlock | ToolCallBlock | ToolResultBlock | ImageBlock


@dataclass(frozen=True, slots=True)
class LLMMessage:
    role: Role
    blocks: tuple[ContentBlock, ...]

    @classmethod
    def text(cls, role: Role, content: str) -> LLMMessage:
        return cls(role=role, blocks=(TextBlock(content),))


@dataclass(frozen=True, slots=True)
class ToolSpec:
    name: str
    description: str
    input_schema: JsonObject


@dataclass(frozen=True, slots=True)
class TokenUsage:
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0


@dataclass(frozen=True, slots=True)
class ProviderQuirks:
    replay_reasoning_content: bool = False
    reasoning_effort_param: str = "reasoning_effort"
    extra_headers: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True, slots=True)
class EndpointConfig:
    base_url: str
    default_protocol: WireProtocol
    api_key: str = ""
    auth_style: AuthStyle = AuthStyle.BEARER
    discovery_url: str = ""
    headers: tuple[tuple[str, str], ...] = ()
    quirks: ProviderQuirks = ProviderQuirks()


@dataclass(frozen=True, slots=True)
class GenerateRequest:
    model: str
    messages: tuple[LLMMessage, ...]
    tools: tuple[ToolSpec, ...] = ()
    protocol_override: WireProtocol | None = None
    temperature: float | None = None
    max_output_tokens: int | None = None
    reasoning_effort: str | None = None
    extra_body: JsonObject | None = None


@dataclass(frozen=True, slots=True)
class TextDeltaEvent:
    delta: str
    signature_delta: str | None = None
    part_start: bool = False


@dataclass(frozen=True, slots=True)
class ReasoningDeltaEvent:
    delta: str
    signature_delta: str | None = None
    part_start: bool = False
    signature_protocol: WireProtocol | None = None


@dataclass(frozen=True, slots=True)
class ToolCallDeltaEvent:
    index: int
    call_id: str | None = None
    name: str | None = None
    arguments_delta: str = ""
    signature_delta: str | None = None


@dataclass(frozen=True, slots=True)
class UsageEvent:
    usage: TokenUsage


@dataclass(frozen=True, slots=True)
class DoneEvent:
    finish_reason: str | None = None


type StreamEvent = (
    TextDeltaEvent | ReasoningDeltaEvent | ToolCallDeltaEvent | UsageEvent | DoneEvent
)


@dataclass(frozen=True, slots=True)
class GenerateResponse:
    blocks: tuple[ContentBlock, ...]
    usage: TokenUsage
    finish_reason: str | None = None


@dataclass(frozen=True, slots=True)
class DiscoveredModel:
    model_id: str
    display_name: str
    owned_by: str = ""
    context_window: int | None = None
    max_output_tokens: int | None = None
    recommended_protocol: WireProtocol | None = None
    supported: bool = True
    unsupported_reason: str = ""
    catalog_match: str = "none"
    catalog_provider: str | None = None
    catalog_stale: bool = False
