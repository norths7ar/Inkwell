"""Repositories for sessions, agent runs, and structured messages."""

from __future__ import annotations

import sqlite3

from pydantic import TypeAdapter

from backend.errors import ErrorInfo
from backend.storage.ids import new_id, utc_now
from backend.storage.migrations import ensure_transaction
from backend.storage.models import (
    Message,
    MessageRole,
    Run,
    RunStatus,
    Session,
    SessionMode,
    Visibility,
)
from backend.storage.repos.content import UNSET, Unset


def _session_from_row(row: sqlite3.Row) -> Session:
    return Session(
        id=str(row["id"]),
        book_id=str(row["book_id"]),
        title=str(row["title"]),
        mode=SessionMode(str(row["mode"])),
        visibility_scope=Visibility(str(row["visibility_scope"])),
        created_at=str(row["created_at"]),
        updated_at=str(row["updated_at"]),
        pinned=bool(row["pinned"]),
    )


def _run_from_row(row: sqlite3.Row) -> Run:
    raw_model_id = row["model_id"]
    raw_question = row["pending_question_json"]
    raw_error = row["error_message"]
    raw_finished_at = row["finished_at"]
    return Run(
        id=str(row["id"]),
        session_id=str(row["session_id"]),
        book_id=str(row["book_id"]),
        status=RunStatus(str(row["status"])),
        model_id=str(raw_model_id) if raw_model_id is not None else None,
        pending_question_json=str(raw_question) if raw_question is not None else None,
        error_message=str(raw_error) if raw_error is not None else None,
        input_tokens=int(row["input_tokens"]),
        output_tokens=int(row["output_tokens"]),
        cache_read_tokens=int(row["cache_read_tokens"]),
        cache_write_tokens=int(row["cache_write_tokens"]),
        started_at=str(row["started_at"]),
        finished_at=str(raw_finished_at) if raw_finished_at is not None else None,
        error=(
            TypeAdapter(ErrorInfo).validate_json(str(row["error_json"]))
            if row["error_json"] is not None
            else None
        ),
        reasoning_effort=str(row["reasoning_effort"])
        if row["reasoning_effort"] is not None
        else None,
        references_json=str(row["references_json"]),
        reasoning_duration_ms=int(row["reasoning_duration_ms"]),
    )


def _message_from_row(row: sqlite3.Row) -> Message:
    raw_run_id = row["run_id"]
    return Message(
        id=str(row["id"]),
        session_id=str(row["session_id"]),
        book_id=str(row["book_id"]),
        run_id=str(raw_run_id) if raw_run_id is not None else None,
        seq=int(row["seq"]),
        role=MessageRole(str(row["role"])),
        blocks_json=str(row["blocks_json"]),
        created_at=str(row["created_at"]),
    )


def create_session(
    conn: sqlite3.Connection,
    *,
    book_id: str,
    title: str = "",
    mode: SessionMode = SessionMode.DISCUSS,
    visibility_scope: Visibility = Visibility.LOCAL,
    session_id: str | None = None,
    pinned: bool = False,
) -> Session:
    now = utc_now()
    resolved_id = session_id or new_id()
    with ensure_transaction(conn):
        conn.execute(
            """
            INSERT INTO sessions (id, book_id, title, mode, visibility_scope, created_at, updated_at, pinned)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                resolved_id,
                book_id,
                title,
                mode.value,
                visibility_scope.value,
                now,
                now,
                int(pinned),
            ),
        )
    session = get_session(conn, resolved_id)
    if session is None:
        raise RuntimeError(f"Failed to load newly created session {resolved_id}")
    return session


def get_session(conn: sqlite3.Connection, session_id: str) -> Session | None:
    row = conn.execute("SELECT * FROM sessions WHERE id = ?", (session_id,)).fetchone()
    return _session_from_row(row) if row is not None else None


def list_sessions(conn: sqlite3.Connection, book_id: str) -> list[Session]:
    rows = conn.execute(
        "SELECT * FROM sessions WHERE book_id = ? ORDER BY pinned DESC, updated_at DESC, id DESC",
        (book_id,),
    ).fetchall()
    return [_session_from_row(row) for row in rows]


def update_session(
    conn: sqlite3.Connection,
    session_id: str,
    *,
    title: str | Unset = UNSET,
    mode: SessionMode | Unset = UNSET,
    visibility_scope: Visibility | Unset = UNSET,
    pinned: bool | Unset = UNSET,
) -> Session:
    existing = get_session(conn, session_id)
    if existing is None:
        raise LookupError(f"Session not found: {session_id}")
    now = utc_now()
    with ensure_transaction(conn):
        conn.execute(
            """
            UPDATE sessions
            SET title = ?, mode = ?, visibility_scope = ?, updated_at = ?, pinned = ?
            WHERE id = ?
            """,
            (
                existing.title if isinstance(title, Unset) else title,
                (existing.mode if isinstance(mode, Unset) else mode).value,
                (
                    existing.visibility_scope
                    if isinstance(visibility_scope, Unset)
                    else visibility_scope
                ).value,
                now,
                int(existing.pinned if isinstance(pinned, Unset) else pinned),
                session_id,
            ),
        )
    updated = get_session(conn, session_id)
    if updated is None:
        raise LookupError(f"Session not found after update: {session_id}")
    return updated


def delete_session(conn: sqlite3.Connection, session_id: str) -> bool:
    with ensure_transaction(conn):
        cursor = conn.execute("DELETE FROM sessions WHERE id = ?", (session_id,))
    return cursor.rowcount > 0


def create_run(
    conn: sqlite3.Connection,
    *,
    session_id: str,
    book_id: str,
    model_id: str | None = None,
    run_id: str | None = None,
    reasoning_effort: str | None = None,
    references_json: str = "[]",
) -> Run:
    now = utc_now()
    resolved_id = run_id or new_id()
    with ensure_transaction(conn):
        conn.execute(
            """
            INSERT INTO runs (
                id, session_id, book_id, status, model_id, started_at, reasoning_effort, references_json
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                resolved_id,
                session_id,
                book_id,
                RunStatus.RUNNING.value,
                model_id,
                now,
                reasoning_effort,
                references_json,
            ),
        )
    run = get_run(conn, resolved_id)
    if run is None:
        raise RuntimeError(f"Failed to load newly created run {resolved_id}")
    return run


def get_run(conn: sqlite3.Connection, run_id: str) -> Run | None:
    row = conn.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()
    return _run_from_row(row) if row is not None else None


def list_runs(conn: sqlite3.Connection, session_id: str) -> list[Run]:
    rows = conn.execute(
        "SELECT * FROM runs WHERE session_id = ? ORDER BY started_at DESC, id DESC",
        (session_id,),
    ).fetchall()
    return [_run_from_row(row) for row in rows]


def set_run_waiting_user(
    conn: sqlite3.Connection,
    run_id: str,
    *,
    pending_question_json: str,
) -> Run:
    with ensure_transaction(conn):
        cursor = conn.execute(
            """
            UPDATE runs
            SET status = ?, pending_question_json = ?
            WHERE id = ?
            """,
            (RunStatus.WAITING_USER.value, pending_question_json, run_id),
        )
    if cursor.rowcount == 0:
        raise LookupError(f"Run not found: {run_id}")
    run = get_run(conn, run_id)
    if run is None:
        raise LookupError(f"Run not found after update: {run_id}")
    return run


def finish_run(
    conn: sqlite3.Connection,
    run_id: str,
    *,
    status: RunStatus,
    error_message: str | None = None,
    error: ErrorInfo | None = None,
    input_tokens: int = 0,
    output_tokens: int = 0,
    cache_read_tokens: int = 0,
    cache_write_tokens: int = 0,
) -> Run:
    if status in (RunStatus.RUNNING, RunStatus.WAITING_USER):
        raise ValueError(f"Expected terminal run status, got {status.value}")
    with ensure_transaction(conn):
        cursor = conn.execute(
            """
            UPDATE runs
            SET status = ?, pending_question_json = NULL, error_message = ?,
                input_tokens = ?, output_tokens = ?,
                cache_read_tokens = ?, cache_write_tokens = ?,
                finished_at = ?, error_json = ?
            WHERE id = ?
            """,
            (
                status.value,
                error.message if error is not None else error_message,
                input_tokens,
                output_tokens,
                cache_read_tokens,
                cache_write_tokens,
                utc_now(),
                TypeAdapter(ErrorInfo).dump_json(error).decode() if error is not None else None,
                run_id,
            ),
        )
    if cursor.rowcount == 0:
        raise LookupError(f"Run not found: {run_id}")
    run = get_run(conn, run_id)
    if run is None:
        raise LookupError(f"Run not found after finish: {run_id}")
    return run


def append_message(
    conn: sqlite3.Connection,
    *,
    session_id: str,
    book_id: str,
    role: MessageRole,
    blocks_json: str,
    run_id: str | None = None,
    message_id: str | None = None,
) -> Message:
    now = utc_now()
    resolved_id = message_id or new_id()
    with ensure_transaction(conn):
        seq_row = conn.execute(
            "SELECT COALESCE(MAX(seq), 0) + 1 AS next_seq FROM messages WHERE session_id = ?",
            (session_id,),
        ).fetchone()
        next_seq = int(seq_row["next_seq"]) if seq_row is not None else 1
        conn.execute(
            """
            INSERT INTO messages (
                id, session_id, book_id, run_id, seq, role, blocks_json, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (resolved_id, session_id, book_id, run_id, next_seq, role.value, blocks_json, now),
        )
        conn.execute("UPDATE sessions SET updated_at = ? WHERE id = ?", (now, session_id))
    row = conn.execute("SELECT * FROM messages WHERE id = ?", (resolved_id,)).fetchone()
    if row is None:
        raise RuntimeError(f"Failed to load newly appended message {resolved_id}")
    return _message_from_row(row)


def list_messages(conn: sqlite3.Connection, session_id: str) -> list[Message]:
    rows = conn.execute(
        "SELECT * FROM messages WHERE session_id = ? ORDER BY seq ASC",
        (session_id,),
    ).fetchall()
    return [_message_from_row(row) for row in rows]
