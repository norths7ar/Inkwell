"""Resolve explicit per-turn references using the same book/scope boundary as tools."""

import sqlite3

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field

from backend.storage.models import Session, TargetType, Visibility
from backend.storage.repos import content


class EntityReference(BaseModel):
    model_config = ConfigDict(extra="forbid")
    target_type: TargetType
    target_id: str = Field(min_length=1)


def resolve_references(
    conn: sqlite3.Connection, session: Session, references: list[EntityReference]
) -> tuple[str, ...]:
    snippets: list[str] = []
    seen: set[tuple[TargetType, str]] = set()
    for reference in references:
        key = (reference.target_type, reference.target_id)
        if key in seen:
            continue
        seen.add(key)
        if reference.target_type == TargetType.CHAPTER:
            chapter = content.get_chapter(conn, reference.target_id)
            if chapter is None or chapter.book_id != session.book_id:
                raise HTTPException(404, "Reference not found in this book")
            snippets.append(f"章节：{chapter.title}\n\n{chapter.content}")
        else:
            entry = content.get_entry(conn, reference.target_id)
            if entry is None or entry.book_id != session.book_id:
                raise HTTPException(404, "Reference not found in this book")
            if (
                session.visibility_scope == Visibility.LOCAL
                and entry.visibility == Visibility.GLOBAL
            ):
                raise HTTPException(403, "Reference is outside session visibility scope")
            snippets.append(f"设定：{entry.title}\n\n{entry.content}")
    return tuple(snippets)
