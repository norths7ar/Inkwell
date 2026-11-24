"""Repositories for pending changes and lightweight content revisions."""

from __future__ import annotations

import json
import sqlite3

from pydantic import BaseModel

from backend.storage.ids import new_id, utc_now
from backend.storage.migrations import ensure_transaction
from backend.storage.models import (
    EntryKind,
    PendingAction,
    PendingChange,
    PendingStatus,
    Revision,
    RevisionSource,
    TargetType,
    Visibility,
)
from backend.storage.repos.content import (
    UNSET,
    create_chapter,
    create_entry,
    delete_chapter,
    delete_entry,
    get_chapter,
    get_entry,
    update_chapter,
    update_entry,
)


class _ChapterChangePayload(BaseModel):
    volume_id: str | None = None
    summary: str | None = None
    sort_order: int | None = None


class _EntryChangePayload(BaseModel):
    kind: EntryKind | None = None
    category: str | None = None
    visibility: Visibility | None = None
    meta_json: str | None = None
    sort_order: int | None = None


def _pending_change_from_row(row: sqlite3.Row) -> PendingChange:
    raw_session_id = row["session_id"]
    raw_run_id = row["run_id"]
    raw_target_id = row["target_id"]
    raw_resolved_at = row["resolved_at"]
    return PendingChange(
        id=str(row["id"]),
        book_id=str(row["book_id"]),
        session_id=str(raw_session_id) if raw_session_id is not None else None,
        run_id=str(raw_run_id) if raw_run_id is not None else None,
        target_type=TargetType(str(row["target_type"])),
        target_id=str(raw_target_id) if raw_target_id is not None else None,
        action=PendingAction(str(row["action"])),
        title=str(row["title"]),
        before_content=str(row["before_content"]),
        after_content=str(row["after_content"]),
        payload_json=str(row["payload_json"]),
        status=PendingStatus(str(row["status"])),
        created_at=str(row["created_at"]),
        resolved_at=str(raw_resolved_at) if raw_resolved_at is not None else None,
    )


def _revision_from_row(row: sqlite3.Row) -> Revision:
    raw_pending_id = row["pending_change_id"]
    return Revision(
        id=str(row["id"]),
        book_id=str(row["book_id"]),
        target_type=TargetType(str(row["target_type"])),
        target_id=str(row["target_id"]),
        source=RevisionSource(str(row["source"])),
        pending_change_id=str(raw_pending_id) if raw_pending_id is not None else None,
        title=str(row["title"]),
        content=str(row["content"]),
        snapshot_json=str(row["snapshot_json"]),
        created_at=str(row["created_at"]),
    )


def create_revision(
    conn: sqlite3.Connection,
    *,
    book_id: str,
    target_type: TargetType,
    target_id: str,
    source: RevisionSource,
    title: str,
    content: str,
    snapshot_json: str = "{}",
    pending_change_id: str | None = None,
    revision_id: str | None = None,
) -> Revision:
    now = utc_now()
    resolved_id = revision_id or new_id()
    with ensure_transaction(conn):
        conn.execute(
            """
            INSERT INTO revisions (
                id, book_id, target_type, target_id, source,
                pending_change_id, title, content, snapshot_json, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                resolved_id,
                book_id,
                target_type.value,
                target_id,
                source.value,
                pending_change_id,
                title,
                content,
                snapshot_json,
                now,
            ),
        )
    revision = get_revision(conn, resolved_id)
    if revision is None:
        raise RuntimeError(f"Failed to load newly created revision {resolved_id}")
    return revision


def get_revision(conn: sqlite3.Connection, revision_id: str) -> Revision | None:
    row = conn.execute("SELECT * FROM revisions WHERE id = ?", (revision_id,)).fetchone()
    return _revision_from_row(row) if row is not None else None


def list_revisions(
    conn: sqlite3.Connection,
    book_id: str,
    *,
    target_type: TargetType | None = None,
    target_id: str | None = None,
) -> list[Revision]:
    clauses = ["book_id = ?"]
    params: list[str] = [book_id]
    if target_type is not None:
        clauses.append("target_type = ?")
        params.append(target_type.value)
    if target_id is not None:
        clauses.append("target_id = ?")
        params.append(target_id)
    where_sql = " AND ".join(clauses)
    rows = conn.execute(
        f"SELECT * FROM revisions WHERE {where_sql} ORDER BY created_at DESC, id DESC",
        params,
    ).fetchall()
    return [_revision_from_row(row) for row in rows]


def create_pending_change(
    conn: sqlite3.Connection,
    *,
    book_id: str,
    target_type: TargetType,
    action: PendingAction,
    target_id: str | None = None,
    session_id: str | None = None,
    run_id: str | None = None,
    title: str = "",
    before_content: str = "",
    after_content: str = "",
    payload_json: str = "{}",
    change_id: str | None = None,
) -> PendingChange:
    now = utc_now()
    resolved_id = change_id or new_id()
    with ensure_transaction(conn):
        conn.execute(
            """
            INSERT INTO pending_changes (
                id, book_id, session_id, run_id, target_type, target_id,
                action, title, before_content, after_content, payload_json,
                status, created_at, resolved_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL)
            """,
            (
                resolved_id,
                book_id,
                session_id,
                run_id,
                target_type.value,
                target_id,
                action.value,
                title,
                before_content,
                after_content,
                payload_json,
                PendingStatus.PENDING.value,
                now,
            ),
        )
    change = get_pending_change(conn, resolved_id)
    if change is None:
        raise RuntimeError(f"Failed to load newly created pending change {resolved_id}")
    return change


def get_pending_change(conn: sqlite3.Connection, change_id: str) -> PendingChange | None:
    row = conn.execute("SELECT * FROM pending_changes WHERE id = ?", (change_id,)).fetchone()
    return _pending_change_from_row(row) if row is not None else None


def list_pending_changes(
    conn: sqlite3.Connection,
    book_id: str,
    *,
    status: PendingStatus | None = None,
) -> list[PendingChange]:
    if status is None:
        rows = conn.execute(
            "SELECT * FROM pending_changes WHERE book_id = ? ORDER BY created_at DESC, id DESC",
            (book_id,),
        ).fetchall()
    else:
        rows = conn.execute(
            """
            SELECT * FROM pending_changes
            WHERE book_id = ? AND status = ?
            ORDER BY created_at DESC, id DESC
            """,
            (book_id, status.value),
        ).fetchall()
    return [_pending_change_from_row(row) for row in rows]


def reject_pending_change(conn: sqlite3.Connection, change_id: str) -> PendingChange:
    with ensure_transaction(conn):
        existing = get_pending_change(conn, change_id)
        if existing is None:
            raise LookupError(f"Pending change not found: {change_id}")
        if existing.status != PendingStatus.PENDING:
            raise ValueError(f"Pending change {change_id} is already {existing.status.value}")
        conn.execute(
            "UPDATE pending_changes SET status = ?, resolved_at = ? WHERE id = ?",
            (PendingStatus.REJECTED.value, utc_now(), change_id),
        )
    updated = get_pending_change(conn, change_id)
    if updated is None:
        raise LookupError(f"Pending change not found after reject: {change_id}")
    return updated


def _apply_chapter_change(
    conn: sqlite3.Connection,
    change: PendingChange,
) -> tuple[str, str, str, str]:
    payload = _ChapterChangePayload.model_validate_json(change.payload_json or "{}")
    if change.action == PendingAction.CREATE:
        chapter = create_chapter(
            conn,
            book_id=change.book_id,
            volume_id=payload.volume_id,
            title=change.title or "未命名章节",
            content=change.after_content,
            summary=payload.summary or "",
            sort_order=payload.sort_order if payload.sort_order is not None else 0,
        )
        snapshot = json.dumps({"volume_id": chapter.volume_id, "summary": chapter.summary})
        return chapter.id, chapter.title, chapter.content, snapshot

    if change.target_id is None:
        raise ValueError(f"Pending change {change.id} is missing target_id")
    existing = get_chapter(conn, change.target_id)
    if existing is None:
        raise LookupError(f"Target chapter not found: {change.target_id}")

    if change.action == PendingAction.UPDATE:
        chapter = update_chapter(
            conn,
            existing.id,
            volume_id=payload.volume_id if "volume_id" in payload.model_fields_set else UNSET,
            title=change.title if change.title else UNSET,
            content=change.after_content,
            summary=payload.summary if payload.summary is not None else UNSET,
            sort_order=payload.sort_order if payload.sort_order is not None else UNSET,
        )
        snapshot = json.dumps({"volume_id": chapter.volume_id, "summary": chapter.summary})
        return chapter.id, chapter.title, chapter.content, snapshot

    delete_chapter(conn, existing.id)
    snapshot = json.dumps(
        {"deleted": True, "volume_id": existing.volume_id, "summary": existing.summary}
    )
    return existing.id, existing.title, existing.content, snapshot


def _apply_entry_change(
    conn: sqlite3.Connection,
    change: PendingChange,
) -> tuple[str, str, str, str]:
    payload = _EntryChangePayload.model_validate_json(change.payload_json or "{}")
    if change.action == PendingAction.CREATE:
        entry = create_entry(
            conn,
            book_id=change.book_id,
            kind=payload.kind or EntryKind.NOTE,
            title=change.title or "未命名设定",
            category=payload.category or "",
            content=change.after_content,
            visibility=payload.visibility or Visibility.LOCAL,
            meta_json=payload.meta_json or "{}",
            sort_order=payload.sort_order if payload.sort_order is not None else 0,
        )
        snapshot = json.dumps(
            {
                "kind": entry.kind.value,
                "category": entry.category,
                "visibility": entry.visibility.value,
                "meta_json": entry.meta_json,
            }
        )
        return entry.id, entry.title, entry.content, snapshot

    if change.target_id is None:
        raise ValueError(f"Pending change {change.id} is missing target_id")
    existing = get_entry(conn, change.target_id)
    if existing is None:
        raise LookupError(f"Target entry not found: {change.target_id}")

    if change.action == PendingAction.UPDATE:
        entry = update_entry(
            conn,
            existing.id,
            kind=payload.kind if payload.kind is not None else UNSET,
            title=change.title if change.title else UNSET,
            category=payload.category if payload.category is not None else UNSET,
            content=change.after_content,
            visibility=payload.visibility if payload.visibility is not None else UNSET,
            meta_json=payload.meta_json if payload.meta_json is not None else UNSET,
            sort_order=payload.sort_order if payload.sort_order is not None else UNSET,
        )
        snapshot = json.dumps(
            {
                "kind": entry.kind.value,
                "category": entry.category,
                "visibility": entry.visibility.value,
                "meta_json": entry.meta_json,
            }
        )
        return entry.id, entry.title, entry.content, snapshot

    delete_entry(conn, existing.id)
    snapshot = json.dumps(
        {
            "deleted": True,
            "kind": existing.kind.value,
            "category": existing.category,
            "visibility": existing.visibility.value,
            "meta_json": existing.meta_json,
        }
    )
    return existing.id, existing.title, existing.content, snapshot


def apply_pending_change(conn: sqlite3.Connection, change_id: str) -> PendingChange:
    """Atomically apply a pending change, sync FTS5, record a revision, and mark applied."""
    with ensure_transaction(conn):
        existing = get_pending_change(conn, change_id)
        if existing is None:
            raise LookupError(f"Pending change not found: {change_id}")
        if existing.status != PendingStatus.PENDING:
            raise ValueError(f"Pending change {change_id} is already {existing.status.value}")

        if existing.target_type == TargetType.CHAPTER:
            target_id, title, content, snapshot = _apply_chapter_change(conn, existing)
        else:
            target_id, title, content, snapshot = _apply_entry_change(conn, existing)

        create_revision(
            conn,
            book_id=existing.book_id,
            target_type=existing.target_type,
            target_id=target_id,
            source=RevisionSource.PENDING_APPLY,
            pending_change_id=existing.id,
            title=title,
            content=content,
            snapshot_json=snapshot,
        )
        conn.execute(
            """
            UPDATE pending_changes
            SET status = ?, target_id = ?, resolved_at = ?
            WHERE id = ?
            """,
            (PendingStatus.APPLIED.value, target_id, utc_now(), change_id),
        )

    updated = get_pending_change(conn, change_id)
    if updated is None:
        raise LookupError(f"Pending change not found after apply: {change_id}")
    return updated
