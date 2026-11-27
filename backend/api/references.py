"""Resolve explicit per-turn references using the same book/scope boundary as tools."""

import sqlite3

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field, model_validator

from backend.storage.models import Chapter, Entry, Session, TargetType, Visibility
from backend.storage.repos import content


class FocusReference(BaseModel):
    model_config = ConfigDict(extra="forbid")
    target_type: TargetType
    target_id: str = Field(min_length=1)


class EntityReference(FocusReference):
    selected_text: str | None = Field(default=None, min_length=1, max_length=12000)
    start_line: int | None = Field(default=None, ge=1)
    end_line: int | None = Field(default=None, ge=1)

    @model_validator(mode="after")
    def validate_lines(self) -> EntityReference:
        if self.selected_text is None and (
            self.start_line is not None or self.end_line is not None
        ):
            raise ValueError("Line numbers require selected_text")
        if self.end_line is not None and (
            self.start_line is None or self.end_line < self.start_line
        ):
            raise ValueError("end_line requires start_line and must not precede it")
        return self


def _target(
    conn: sqlite3.Connection, session: Session, reference: FocusReference
) -> Chapter | Entry:
    target = (
        content.get_chapter(conn, reference.target_id)
        if reference.target_type == TargetType.CHAPTER
        else content.get_entry(conn, reference.target_id)
    )
    if target is None or target.book_id != session.book_id:
        raise HTTPException(404, "Reference not found in this book")
    return target


def _hidden(session: Session, target: Chapter | Entry) -> bool:
    return (
        isinstance(target, Entry)
        and session.visibility_scope == Visibility.LOCAL
        and target.visibility == Visibility.GLOBAL
    )


def _source(target: Chapter | Entry) -> str:
    if isinstance(target, Chapter):
        return f"章节：{target.title}\nID：{target.id}"
    return (
        f"设定：{target.title}\nID：{target.id}\n类型：{target.kind.value}\n分类：{target.category}"
    )


def resolve_focus(
    conn: sqlite3.Connection, session: Session, focus: FocusReference | None
) -> tuple[str, ...]:
    if focus is None:
        return ()
    target = _target(conn, session, focus)
    if _hidden(session, target):
        return ()
    return (f"当前查看焦点（仅位置提示，正文请按需使用工具读取）：\n{_source(target)}",)


def resolve_references(
    conn: sqlite3.Connection, session: Session, references: list[EntityReference]
) -> tuple[str, ...]:
    snippets: list[str] = []
    seen: set[tuple[TargetType, str, str | None, int | None, int | None]] = set()
    for reference in references:
        key = (
            reference.target_type,
            reference.target_id,
            reference.selected_text,
            reference.start_line,
            reference.end_line,
        )
        if key in seen:
            continue
        seen.add(key)
        target = _target(conn, session, reference)
        if _hidden(session, target):
            raise HTTPException(403, "Reference is outside session visibility scope")
        if reference.selected_text is None:
            label = "章节" if isinstance(target, Chapter) else "设定"
            snippets.append(f"{label}：{target.title}\n\n{target.content}")
            continue
        lines = ""
        if reference.start_line is not None:
            lines = f"\n客户端标注行号：{reference.start_line}"
            if reference.end_line is not None:
                lines += f"–{reference.end_line}"
        snippets.append(
            f"选中片段（客户端文本快照，可能包含未保存修改，不代表服务器正文）：\n"
            f"{_source(target)}{lines}\n\n{reference.selected_text}"
        )
    return tuple(snippets)
