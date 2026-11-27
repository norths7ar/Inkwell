"""Visibility and optimistic concurrency for AI inspection of pending proposals."""

import hashlib
import json
import sqlite3
from dataclasses import asdict

from pydantic import BaseModel

from backend.storage.models import PendingChange, PendingStatus, TargetType, Visibility
from backend.storage.repos.changes import get_pending_change
from backend.storage.repos.content import get_chapter, get_entry


class _VisibilityPayload(BaseModel):
    visibility: Visibility | None = None


def revision_token(change: PendingChange) -> str:
    return hashlib.sha256(json.dumps(asdict(change), sort_keys=True).encode()).hexdigest()


def visible(
    conn: sqlite3.Connection, change: PendingChange, book_id: str, scope: Visibility
) -> bool:
    if change.book_id != book_id:
        return False
    if change.target_type == TargetType.CHAPTER:
        target = get_chapter(conn, change.target_id) if change.target_id else None
        return change.target_id is None or (target is not None and target.book_id == book_id)
    target = get_entry(conn, change.target_id) if change.target_id else None
    if change.target_id and (target is None or target.book_id != book_id):
        return False
    payload = _VisibilityPayload.model_validate_json(change.payload_json)
    if scope == Visibility.LOCAL:
        return payload.visibility != Visibility.GLOBAL and (
            target is None or target.visibility == Visibility.LOCAL
        )
    return True


def revise(
    conn: sqlite3.Connection, change: PendingChange, expected: str, content: str, title: str
) -> PendingChange:
    if change.status != PendingStatus.PENDING:
        raise ValueError("Only pending proposals may be revised")
    if revision_token(change) != expected:
        raise ValueError("Proposal changed; read it again before revising")
    count = conn.execute(
        """UPDATE pending_changes SET after_content=?, title=?
           WHERE id=? AND status='pending' AND after_content=? AND title=? AND payload_json=? AND before_content=?""",
        (
            content,
            title,
            change.id,
            change.after_content,
            change.title,
            change.payload_json,
            change.before_content,
        ),
    ).rowcount
    if count != 1:
        raise ValueError("Proposal changed or was resolved; read it again before revising")
    updated = get_pending_change(conn, change.id)
    if updated is None:
        raise LookupError("Proposal no longer exists")
    return updated
