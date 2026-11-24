"""Unified tool contract, concise validation errors, and M0 core novel-writing tools."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable
from contextlib import closing
from dataclasses import dataclass

from pydantic import BaseModel, Field, ValidationError

from backend.llm._json import JsonObject, coerce_json_value
from backend.llm.types import ToolSpec
from backend.storage.db import Database
from backend.storage.models import (
    EntryKind,
    PendingAction,
    PendingChange,
    SessionMode,
    TargetType,
    Visibility,
    validate_entry_meta,
)
from backend.storage.repos.changes import create_pending_change
from backend.storage.repos.content import (
    get_chapter,
    get_entry,
    get_volume,
    list_chapters,
    list_entries,
    list_volumes,
    search_book_content,
)

_MAX_VALIDATION_INPUT_PREVIEW = 120


@dataclass(frozen=True, slots=True)
class ToolFailure:
    code: str
    message: str

    def to_json(self) -> str:
        return json.dumps(
            {"ok": False, "error": {"code": self.code, "message": self.message}},
            ensure_ascii=False,
        )


def _format_input_preview(value: object) -> str:
    if isinstance(value, dict):
        keys = ", ".join(sorted(str(k) for k in value))
        return f"<object keys=[{keys}]>"
    if isinstance(value, (list, tuple, set)):
        return f"<{type(value).__name__} length={len(value)}>"
    if isinstance(value, str) and len(value) > _MAX_VALIDATION_INPUT_PREVIEW:
        return f"<string length={len(value)} preview={value[:_MAX_VALIDATION_INPUT_PREVIEW]!r}...>"
    text = repr(value)
    if len(text) > _MAX_VALIDATION_INPUT_PREVIEW:
        return text[:_MAX_VALIDATION_INPUT_PREVIEW] + "..."
    return text


def _format_validation_error(exc: ValidationError) -> str:
    details: list[str] = []
    for item in exc.errors():
        loc = ".".join(str(part) for part in item.get("loc", ())) or "<root>"
        msg = " ".join(str(item.get("msg", "Validation error")).split())
        detail = f"{loc}: {msg}"
        if "input" in item:
            raw_input: object = item["input"]
            detail += f" (got {_format_input_preview(raw_input)})"
        details.append(detail)
    if not details:
        return "参数校验失败"
    return f"参数校验失败（{len(details)} 处）:\n" + "\n".join(f"- {d}" for d in details)


@dataclass(frozen=True, slots=True)
class ToolContext:
    db: Database
    book_id: str
    session_id: str
    run_id: str
    visibility_scope: Visibility


@dataclass(frozen=True, slots=True)
class ToolExecutionResult:
    content: str
    is_error: bool = False
    created_change: PendingChange | None = None
    suspend_question: tuple[str, tuple[str, ...]] | None = None


@dataclass(frozen=True, slots=True)
class BoundTool:
    name: str
    description: str
    input_schema: JsonObject
    read_only: bool
    runner: Callable[[ToolContext, str], ToolExecutionResult]

    def execute(self, ctx: ToolContext, raw_arguments_json: str) -> ToolExecutionResult:
        return self.runner(ctx, raw_arguments_json)

    def to_spec(self) -> ToolSpec:
        return ToolSpec(
            name=self.name,
            description=self.description,
            input_schema=self.input_schema,
        )


def _bind_tool[ArgsT: BaseModel](
    *,
    name: str,
    description: str,
    args_model: type[ArgsT],
    read_only: bool,
    handler: Callable[[ToolContext, ArgsT], ToolExecutionResult],
) -> BoundTool:
    raw_schema: object = args_model.model_json_schema()
    schema_val = coerce_json_value(raw_schema)
    if not isinstance(schema_val, dict):
        raise TypeError(f"Expected dict JSON schema for tool {name}")

    def run(ctx: ToolContext, raw_arguments_json: str) -> ToolExecutionResult:
        raw_text = raw_arguments_json.strip() or "{}"
        try:
            parsed = args_model.model_validate_json(raw_text)
        except ValidationError as exc:
            failure = ToolFailure("validation_error", _format_validation_error(exc))
            return ToolExecutionResult(content=failure.to_json(), is_error=True)

        try:
            return handler(ctx, parsed)
        except Exception as exc:
            failure = ToolFailure("execution_error", str(exc) or type(exc).__name__)
            return ToolExecutionResult(content=failure.to_json(), is_error=True)

    return BoundTool(
        name=name,
        description=description,
        input_schema=schema_val,
        read_only=read_only,
        runner=run,
    )


def _ok(payload: object) -> ToolExecutionResult:
    return ToolExecutionResult(
        content=json.dumps({"ok": True, "data": payload}, ensure_ascii=False)
    )


def _err(code: str, message: str) -> ToolExecutionResult:
    return ToolExecutionResult(content=ToolFailure(code, message).to_json(), is_error=True)


# ---------------------------------------------------------------------------
# Argument schemas
# ---------------------------------------------------------------------------


class _ListChaptersArgs(BaseModel):
    volume_id: str | None = Field(
        default=None, description="可选分卷 ID；省略时返回全书分卷与章节目录。"
    )


class _ReadChapterArgs(BaseModel):
    chapter_id: str = Field(min_length=1, description="要读取的章节 ID。")


class _ReadSummariesArgs(BaseModel):
    chapter_ids: list[str] | None = Field(
        default=None,
        description="可选章节 ID 列表；省略时返回全书已有摘要的章节概况。",
    )


class _ReadEntriesArgs(BaseModel):
    kind: EntryKind | None = Field(
        default=None,
        description="可选条目类型：character（人物）、world（世界观）或 note（笔记）。",
    )
    entry_id: str | None = Field(default=None, description="可选条目 ID，用于读取单条完整内容。")


class _SearchContentArgs(BaseModel):
    query: str = Field(min_length=1, description="检索关键词（1 字以上，支持中文与英文）。")
    limit: int = Field(default=10, ge=1, le=50, description="最多返回条数，范围 1 到 50。")


class _ProposeEntryEditArgs(BaseModel):
    action: PendingAction = Field(description="操作类型：create、update 或 delete。")
    target_id: str | None = Field(default=None, description="update 或 delete 时必填的条目 ID。")
    kind: EntryKind = Field(
        default=EntryKind.NOTE, description="条目类型：character、world 或 note。"
    )
    title: str = Field(default="", description="条目标题。")
    category: str = Field(default="", description="可选分组或标签。")
    content: str = Field(default="", description="条目 Markdown 正文。")
    visibility: Visibility = Field(
        default=Visibility.LOCAL, description="可见性：local 或 global。"
    )
    meta_json: str = Field(default="{}", description="JSON 格式扩展元数据（aliases、tags 等）。")
    sort_order: int = Field(default=0, description="可选排序值。")


class _ProposeChapterEditArgs(BaseModel):
    action: PendingAction = Field(description="操作类型：create、update 或 delete。")
    target_id: str | None = Field(default=None, description="update 或 delete 时必填的章节 ID。")
    volume_id: str | None = Field(default=None, description="可选所属分卷 ID。")
    title: str = Field(default="", description="章节标题。")
    content: str = Field(default="", description="章节正文。")
    summary: str | None = Field(default=None, description="可选章节摘要。")
    sort_order: int | None = Field(default=None, description="可选排序值。")


class _AskUserArgs(BaseModel):
    question: str = Field(min_length=1, description="向作者提出的明确问题。")
    options: list[str] = Field(default_factory=list, description="可选建议选项列表。")


# ---------------------------------------------------------------------------
# Tool handlers
# ---------------------------------------------------------------------------


def _handle_list_chapters(ctx: ToolContext, args: _ListChaptersArgs) -> ToolExecutionResult:
    with closing(ctx.db.connect()) as conn:
        volumes = list_volumes(conn, ctx.book_id)
        chapters = list_chapters(conn, ctx.book_id, volume_id=args.volume_id)
    return _ok(
        {
            "volumes": [
                {"id": v.id, "title": v.title, "sort_order": v.sort_order} for v in volumes
            ],
            "chapters": [
                {
                    "id": ch.id,
                    "volume_id": ch.volume_id,
                    "title": ch.title,
                    "word_count": ch.word_count,
                    "sort_order": ch.sort_order,
                    "has_summary": bool(ch.summary.strip()),
                }
                for ch in chapters
            ],
        }
    )


def _handle_read_chapter(ctx: ToolContext, args: _ReadChapterArgs) -> ToolExecutionResult:
    with closing(ctx.db.connect()) as conn:
        ch = get_chapter(conn, args.chapter_id)
    if ch is None or ch.book_id != ctx.book_id:
        return _err("not_found", f"Chapter not found in current book: {args.chapter_id}")
    return _ok(
        {
            "id": ch.id,
            "volume_id": ch.volume_id,
            "title": ch.title,
            "summary": ch.summary,
            "word_count": ch.word_count,
            "content": ch.content,
            "updated_at": ch.updated_at,
        }
    )


def _handle_read_summaries(ctx: ToolContext, args: _ReadSummariesArgs) -> ToolExecutionResult:
    wanted = set(args.chapter_ids) if args.chapter_ids is not None else None
    with closing(ctx.db.connect()) as conn:
        chapters = list_chapters(conn, ctx.book_id)
    items = [
        {
            "id": ch.id,
            "volume_id": ch.volume_id,
            "title": ch.title,
            "summary": ch.summary,
            "word_count": ch.word_count,
        }
        for ch in chapters
        if (wanted is None or ch.id in wanted) and (wanted is not None or ch.summary.strip())
    ]
    return _ok({"summaries": items})


def _entry_visible(entry_visibility: Visibility, scope: Visibility) -> bool:
    if scope == Visibility.GLOBAL:
        return True
    return entry_visibility == Visibility.LOCAL


def _handle_read_entries(ctx: ToolContext, args: _ReadEntriesArgs) -> ToolExecutionResult:
    with closing(ctx.db.connect()) as conn:
        if args.entry_id is not None:
            entry = get_entry(conn, args.entry_id)
            if entry is None or entry.book_id != ctx.book_id:
                return _err("not_found", f"Entry not found in current book: {args.entry_id}")
            if not _entry_visible(entry.visibility, ctx.visibility_scope):
                return _err("forbidden", "Entry is outside local visibility scope")
            return _ok(
                {
                    "id": entry.id,
                    "kind": entry.kind.value,
                    "title": entry.title,
                    "category": entry.category,
                    "visibility": entry.visibility.value,
                    "meta_json": entry.meta_json,
                    "content": entry.content,
                }
            )

        visibility_filter = Visibility.LOCAL if ctx.visibility_scope == Visibility.LOCAL else None
        entries = list_entries(conn, ctx.book_id, kind=args.kind, visibility=visibility_filter)
    return _ok(
        {
            "entries": [
                {
                    "id": e.id,
                    "kind": e.kind.value,
                    "title": e.title,
                    "category": e.category,
                    "visibility": e.visibility.value,
                    "meta_json": e.meta_json,
                    "content": e.content,
                }
                for e in entries
            ]
        }
    )


def _filter_visible_hits(
    conn: sqlite3.Connection,
    scope: Visibility,
    hits_raw: list[tuple[TargetType, str, str, str]],
) -> list[dict[str, str]]:
    results: list[dict[str, str]] = []
    for target_type, target_id, title, snippet in hits_raw:
        if scope == Visibility.LOCAL and target_type == TargetType.ENTRY:
            entry = get_entry(conn, target_id)
            if entry is None or entry.visibility != Visibility.LOCAL:
                continue
        results.append(
            {
                "target_type": target_type.value,
                "target_id": target_id,
                "title": title,
                "snippet": snippet,
            }
        )
    return results


def _handle_search_content(ctx: ToolContext, args: _SearchContentArgs) -> ToolExecutionResult:
    with closing(ctx.db.connect()) as conn:
        hits = search_book_content(conn, ctx.book_id, args.query, limit=args.limit)
        filtered = _filter_visible_hits(
            conn,
            ctx.visibility_scope,
            [(h.target_type, h.target_id, h.title, h.snippet) for h in hits],
        )
    return _ok({"hits": filtered})


def _handle_propose_entry_edit(
    ctx: ToolContext,
    args: _ProposeEntryEditArgs,
) -> ToolExecutionResult:
    if (
        args.action == PendingAction.CREATE
        and ctx.visibility_scope == Visibility.LOCAL
        and args.visibility == Visibility.GLOBAL
    ):
        return _err("forbidden", "Cannot create global entry inside local visibility scope")

    try:
        normalized_meta = validate_entry_meta(args.kind, args.meta_json)
    except Exception as exc:
        return _err("validation_error", f"Invalid meta_json: {exc}")

    with ctx.db.transaction() as conn:
        before_content = ""
        resolved_title = args.title
        resolved_kind = args.kind
        if args.action != PendingAction.CREATE:
            if not args.target_id:
                return _err("validation_error", "target_id is required for update or delete")
            existing = get_entry(conn, args.target_id)
            if existing is None or existing.book_id != ctx.book_id:
                return _err("not_found", f"Entry not found in current book: {args.target_id}")
            if not _entry_visible(existing.visibility, ctx.visibility_scope):
                return _err("forbidden", "Entry is outside local visibility scope")
            before_content = existing.content
            if not resolved_title:
                resolved_title = existing.title
            resolved_kind = existing.kind

        payload = json.dumps(
            {
                "kind": resolved_kind.value,
                "category": args.category,
                "visibility": args.visibility.value,
                "meta_json": normalized_meta,
                "sort_order": args.sort_order,
            },
            ensure_ascii=False,
        )
        change = create_pending_change(
            conn,
            book_id=ctx.book_id,
            session_id=ctx.session_id,
            run_id=ctx.run_id,
            target_type=TargetType.ENTRY,
            target_id=args.target_id,
            action=args.action,
            title=resolved_title,
            before_content=before_content,
            after_content=args.content,
            payload_json=payload,
        )
    return ToolExecutionResult(
        content=json.dumps(
            {
                "ok": True,
                "pending_change_id": change.id,
                "status": change.status.value,
                "target_type": "entry",
                "action": change.action.value,
            },
            ensure_ascii=False,
        ),
        created_change=change,
    )


def _handle_propose_chapter_edit(
    ctx: ToolContext,
    args: _ProposeChapterEditArgs,
) -> ToolExecutionResult:
    with ctx.db.transaction() as conn:
        if args.volume_id is not None:
            vol = get_volume(conn, args.volume_id)
            if vol is None or vol.book_id != ctx.book_id:
                return _err("not_found", f"Volume not found in current book: {args.volume_id}")

        before_content = ""
        resolved_title = args.title
        if args.action != PendingAction.CREATE:
            if not args.target_id:
                return _err("validation_error", "target_id is required for update or delete")
            existing = get_chapter(conn, args.target_id)
            if existing is None or existing.book_id != ctx.book_id:
                return _err("not_found", f"Chapter not found in current book: {args.target_id}")
            before_content = existing.content
            if not resolved_title:
                resolved_title = existing.title

        payload_dict: dict[str, str | int | None] = {}
        if args.volume_id is not None:
            payload_dict["volume_id"] = args.volume_id
        if args.summary is not None:
            payload_dict["summary"] = args.summary
        if args.sort_order is not None:
            payload_dict["sort_order"] = args.sort_order

        change = create_pending_change(
            conn,
            book_id=ctx.book_id,
            session_id=ctx.session_id,
            run_id=ctx.run_id,
            target_type=TargetType.CHAPTER,
            target_id=args.target_id,
            action=args.action,
            title=resolved_title,
            before_content=before_content,
            after_content=args.content,
            payload_json=json.dumps(payload_dict, ensure_ascii=False),
        )
    return ToolExecutionResult(
        content=json.dumps(
            {
                "ok": True,
                "pending_change_id": change.id,
                "status": change.status.value,
                "target_type": "chapter",
                "action": change.action.value,
            },
            ensure_ascii=False,
        ),
        created_change=change,
    )


def _handle_ask_user(_ctx: ToolContext, args: _AskUserArgs) -> ToolExecutionResult:
    return ToolExecutionResult(
        content=json.dumps(
            {"ok": True, "status": "waiting_user", "question": args.question},
            ensure_ascii=False,
        ),
        suspend_question=(args.question, tuple(args.options)),
    )


# ---------------------------------------------------------------------------
# Tool registry & mode binding
# ---------------------------------------------------------------------------

LIST_CHAPTERS_TOOL = _bind_tool(
    name="list_chapters",
    description="列出当前书籍的分卷与章节目录（不含正文），用于定位章节 ID 与字数。",
    args_model=_ListChaptersArgs,
    read_only=True,
    handler=_handle_list_chapters,
)

READ_CHAPTER_TOOL = _bind_tool(
    name="read_chapter",
    description="读取当前书籍指定章节的完整正文与摘要；需先通过 list_chapters 获取 chapter_id。",
    args_model=_ReadChapterArgs,
    read_only=True,
    handler=_handle_read_chapter,
)

READ_SUMMARIES_TOOL = _bind_tool(
    name="read_summaries",
    description="批量读取当前书籍各章节摘要；只读，用于快速回顾前情。",
    args_model=_ReadSummariesArgs,
    read_only=True,
    handler=_handle_read_summaries,
)

READ_ENTRIES_TOOL = _bind_tool(
    name="read_entries",
    description="读取当前可见范围内的人物、世界观或笔记条目；只读。",
    args_model=_ReadEntriesArgs,
    read_only=True,
    handler=_handle_read_entries,
)

SEARCH_CONTENT_TOOL = _bind_tool(
    name="search_content",
    description="在当前书籍可见章节与设定中按关键词检索匹配片段；只读。",
    args_model=_SearchContentArgs,
    read_only=True,
    handler=_handle_search_content,
)

PROPOSE_ENTRY_EDIT_TOOL = _bind_tool(
    name="propose_entry_edit",
    description="提交人物、世界观或笔记的新建/修改/删除待审草案，不直接覆盖正式资料。",
    args_model=_ProposeEntryEditArgs,
    read_only=False,
    handler=_handle_propose_entry_edit,
)

PROPOSE_CHAPTER_EDIT_TOOL = _bind_tool(
    name="propose_chapter_edit",
    description="提交章节的新建/修改/删除待审草案，待作者确认后应用到正文，不阻塞后续对话。",
    args_model=_ProposeChapterEditArgs,
    read_only=False,
    handler=_handle_propose_chapter_edit,
)

ASK_USER_TOOL = _bind_tool(
    name="ask_user",
    description="存在创作分歧或缺失关键前提时向作者提问，调用后会暂停本轮等待回答。",
    args_model=_AskUserArgs,
    read_only=False,
    handler=_handle_ask_user,
)

_READ_ONLY_TOOLS: tuple[BoundTool, ...] = (
    LIST_CHAPTERS_TOOL,
    READ_CHAPTER_TOOL,
    READ_SUMMARIES_TOOL,
    READ_ENTRIES_TOOL,
    SEARCH_CONTENT_TOOL,
)

_MODE_TOOLS: dict[SessionMode, tuple[BoundTool, ...]] = {
    SessionMode.REVIEW: _READ_ONLY_TOOLS,
    SessionMode.DISCUSS: (*_READ_ONLY_TOOLS, PROPOSE_ENTRY_EDIT_TOOL, ASK_USER_TOOL),
    SessionMode.WRITE: (
        *_READ_ONLY_TOOLS,
        PROPOSE_ENTRY_EDIT_TOOL,
        PROPOSE_CHAPTER_EDIT_TOOL,
        ASK_USER_TOOL,
    ),
}


def tools_for_mode(mode: SessionMode) -> tuple[BoundTool, ...]:
    """Return the fixed set of tools permitted for a SessionMode."""
    return _MODE_TOOLS[mode]
