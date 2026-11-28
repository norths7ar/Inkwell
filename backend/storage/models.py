"""Domain dataclasses, enums, and metadata validators for SQLite storage."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from backend.errors import ErrorInfo


class EntryKind(StrEnum):
    CHARACTER = "character"
    WORLD = "world"
    OUTLINE = "outline"
    NOTE = "note"


class Visibility(StrEnum):
    LOCAL = "local"
    GLOBAL = "global"


class SessionMode(StrEnum):
    DISCUSS = "discuss"
    WRITE = "write"
    REVIEW = "review"


class RunStatus(StrEnum):
    RUNNING = "running"
    WAITING_USER = "waiting_user"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    FAILED = "failed"


class PendingStatus(StrEnum):
    PENDING = "pending"
    APPLIED = "applied"
    REJECTED = "rejected"


class PendingAction(StrEnum):
    CREATE = "create"
    UPDATE = "update"
    DELETE = "delete"


class TargetType(StrEnum):
    CHAPTER = "chapter"
    ENTRY = "entry"


class RevisionSource(StrEnum):
    USER = "user"
    PENDING_APPLY = "pending_apply"
    RESTORE = "restore"


class ProtocolMode(StrEnum):
    AUTO = "auto"
    INHERIT = "inherit"
    OVERRIDE = "override"


class ProtocolName(StrEnum):
    OPENAI_CHAT = "openai-chat"
    OPENAI_RESPONSES = "openai-responses"
    ANTHROPIC_MESSAGES = "anthropic-messages"
    GOOGLE_GENAI = "google-genai"


class MessageRole(StrEnum):
    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"
    TOOL = "tool"


class CharacterMeta(BaseModel):
    """Structured metadata for character entries."""

    model_config = ConfigDict(extra="allow")

    aliases: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)


class WorldMeta(BaseModel):
    """Structured metadata for world-building entries."""

    model_config = ConfigDict(extra="allow")

    aliases: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)


class NoteMeta(BaseModel):
    """Structured metadata for free-form notes."""

    model_config = ConfigDict(extra="allow")

    tags: list[str] = Field(default_factory=list)


_ENTRY_META_MODELS: dict[EntryKind, type[BaseModel]] = {
    EntryKind.CHARACTER: CharacterMeta,
    EntryKind.WORLD: WorldMeta,
    EntryKind.OUTLINE: NoteMeta,
    EntryKind.NOTE: NoteMeta,
}


def validate_entry_meta(kind: EntryKind, meta_json: str) -> str:
    """Validate and normalize entry metadata JSON according to its kind."""
    raw = meta_json.strip() or "{}"
    model_cls = _ENTRY_META_MODELS[kind]
    validated = model_cls.model_validate_json(raw)
    return validated.model_dump_json()


@dataclass(frozen=True, slots=True)
class Book:
    id: str
    title: str
    description: str
    rules_markdown: str
    default_visibility: Visibility
    created_at: str
    updated_at: str
    cover_path: str = ""
    chapter_count: int = 0
    word_count: int = 0


@dataclass(frozen=True, slots=True)
class Volume:
    id: str
    book_id: str
    title: str
    description: str
    sort_order: int
    created_at: str
    updated_at: str


@dataclass(frozen=True, slots=True)
class Chapter:
    id: str
    book_id: str
    volume_id: str | None
    title: str
    content: str
    summary: str
    word_count: int
    sort_order: int
    created_at: str
    updated_at: str
    deleted_at: str | None = None


@dataclass(frozen=True, slots=True)
class Entry:
    id: str
    book_id: str
    kind: EntryKind
    title: str
    category: str
    content: str
    visibility: Visibility
    meta_json: str
    sort_order: int
    created_at: str
    updated_at: str
    deleted_at: str | None = None


@dataclass(frozen=True, slots=True)
class EntryFolder:
    id: str
    book_id: str
    kind: EntryKind
    title: str
    description: str
    sort_order: int
    created_at: str
    updated_at: str


@dataclass(frozen=True, slots=True)
class PendingChange:
    id: str
    book_id: str
    session_id: str | None
    run_id: str | None
    target_type: TargetType
    target_id: str | None
    action: PendingAction
    title: str
    before_content: str
    after_content: str
    payload_json: str
    status: PendingStatus
    created_at: str
    resolved_at: str | None


@dataclass(frozen=True, slots=True)
class Revision:
    id: str
    book_id: str
    target_type: TargetType
    target_id: str
    source: RevisionSource
    pending_change_id: str | None
    title: str
    content: str
    snapshot_json: str
    created_at: str


@dataclass(frozen=True, slots=True)
class Session:
    id: str
    book_id: str
    title: str
    mode: SessionMode
    visibility_scope: Visibility
    created_at: str
    updated_at: str
    pinned: bool = False


@dataclass(frozen=True, slots=True)
class Run:
    id: str
    session_id: str
    book_id: str
    status: RunStatus
    model_id: str | None
    pending_question_json: str | None
    error_message: str | None
    input_tokens: int
    output_tokens: int
    cache_read_tokens: int
    cache_write_tokens: int
    started_at: str
    finished_at: str | None
    error: ErrorInfo | None = None
    reasoning_effort: str | None = None
    references_json: str = "[]"
    reasoning_duration_ms: int = 0


@dataclass(frozen=True, slots=True)
class Message:
    id: str
    session_id: str
    book_id: str
    run_id: str | None
    seq: int
    role: MessageRole
    blocks_json: str
    created_at: str


@dataclass(frozen=True, slots=True)
class Provider:
    id: str
    name: str
    preset: str
    base_url: str
    discovery_url: str
    default_protocol: ProtocolName
    api_key_encrypted: str
    headers_encrypted: str
    enabled: bool
    created_at: str
    updated_at: str


@dataclass(frozen=True, slots=True)
class ModelRecord:
    id: str
    provider_id: str
    model_id: str
    display_name: str
    protocol_override: ProtocolName | None
    context_window: int | None
    max_output_tokens: int | None
    capabilities_json: str
    enabled: bool
    created_at: str
    updated_at: str
    discovered_json: str = "{}"
    acked_upstream_json: str = "{}"
    protocol_mode: ProtocolMode = ProtocolMode.INHERIT


@dataclass(frozen=True, slots=True)
class SearchHit:
    target_type: TargetType
    target_id: str
    book_id: str
    title: str
    snippet: str
