"""Atomic conversation branching and rollback without changing published book content."""

import sqlite3
from dataclasses import dataclass

from fastapi import HTTPException

from backend.agent.serialization import deserialize_blocks
from backend.llm.types import TextBlock, ToolCallBlock, ToolResultBlock
from backend.storage.ids import new_id, utc_now
from backend.storage.migrations import ensure_transaction
from backend.storage.models import Message, MessageRole, Session
from backend.storage.repos import sessions


@dataclass(frozen=True, slots=True)
class RollbackResult:
    session_id: str
    message_id: str
    user_text: str | None
    deleted_messages: int
    deleted_runs: int


def require_message(conn: sqlite3.Connection, session_id: str, message_id: str) -> Message:
    message = next(
        (item for item in sessions.list_messages(conn, session_id) if item.id == message_id), None
    )
    if message is None:
        raise LookupError("Message not found in session")
    return message


def validate_boundary(messages: list[Message]) -> None:
    """Do not leave an assistant tool call without its matching result in the prefix."""
    pending: set[str] = set()
    for message in messages:
        for block in deserialize_blocks(message.blocks_json):
            if isinstance(block, ToolCallBlock):
                pending.add(block.call_id)
            elif isinstance(block, ToolResultBlock):
                if block.call_id not in pending:
                    raise HTTPException(409, "Message boundary contains an unmatched tool result")
                pending.remove(block.call_id)
    if pending:
        raise HTTPException(409, "Choose a message boundary after all tool results")


def rollback(conn: sqlite3.Connection, session_id: str, message_id: str) -> RollbackResult:
    target = require_message(conn, session_id, message_id)
    history = sessions.list_messages(conn, session_id)
    validate_boundary([item for item in history if item.seq < target.seq])
    removed = [item for item in history if item.seq >= target.seq]
    user = next((item for item in removed if item.role == MessageRole.USER), None)
    user_text = (
        "".join(
            block.text
            for block in deserialize_blocks(user.blocks_json)
            if isinstance(block, TextBlock)
        )
        if user is not None
        else None
    )
    affected_runs = {item.run_id for item in removed if item.run_id is not None}
    deleted_runs = 0
    with ensure_transaction(conn):
        conn.execute(
            "DELETE FROM compactions WHERE session_id = ? AND through_seq >= ?",
            (session_id, target.seq),
        )
        conn.execute(
            "DELETE FROM messages WHERE session_id = ? AND seq >= ?", (session_id, target.seq)
        )
        for run_id in affected_runs:
            # Revisions remain durable; their pending_change_id becomes NULL via its FK.
            conn.execute("DELETE FROM pending_changes WHERE run_id = ?", (run_id,))
            deleted_runs += conn.execute(
                "DELETE FROM runs WHERE id = ? AND NOT EXISTS (SELECT 1 FROM messages WHERE run_id = ?)",
                (run_id, run_id),
            ).rowcount
        conn.execute("UPDATE sessions SET updated_at = ? WHERE id = ?", (utc_now(), session_id))
    return RollbackResult(session_id, message_id, user_text, len(removed), deleted_runs)


def fork(
    conn: sqlite3.Connection, source: Session, message_id: str, title: str | None = None
) -> Session:
    target = require_message(conn, source.id, message_id)
    prefix = [item for item in sessions.list_messages(conn, source.id) if item.seq <= target.seq]
    validate_boundary(prefix)
    with ensure_transaction(conn):
        result = sessions.create_session(
            conn,
            book_id=source.book_id,
            title=title if title is not None else f"{source.title or '会话'} · 分支",
            mode=source.mode,
            visibility_scope=source.visibility_scope,
        )
        run_ids: dict[str, str] = {}
        for message in prefix:
            if message.run_id is not None and message.run_id not in run_ids:
                copied_id = new_id()
                run_ids[message.run_id] = copied_id
                conn.execute(
                    """INSERT INTO runs (
                        id, session_id, book_id, status, model_id, pending_question_json,
                        error_message, input_tokens, output_tokens, cache_read_tokens,
                        cache_write_tokens, started_at, finished_at, error_json,
                        reasoning_effort, references_json, reasoning_duration_ms
                    ) SELECT ?, ?, book_id, status, model_id, NULL,
                        error_message, input_tokens, output_tokens, cache_read_tokens,
                        cache_write_tokens, started_at, finished_at, error_json,
                        reasoning_effort, references_json, reasoning_duration_ms
                      FROM runs WHERE id = ?""",
                    (copied_id, result.id, message.run_id),
                )
            copied = sessions.append_message(
                conn,
                session_id=result.id,
                book_id=result.book_id,
                role=message.role,
                blocks_json=message.blocks_json,
                run_id=run_ids.get(message.run_id) if message.run_id else None,
            )
            conn.execute(
                "UPDATE messages SET created_at = ? WHERE id = ?", (message.created_at, copied.id)
            )
        for anchor in conn.execute(
            "SELECT * FROM compactions WHERE session_id = ? AND through_seq <= ?",
            (source.id, target.seq),
        ).fetchall():
            conn.execute(
                """INSERT INTO compactions
                   (id, session_id, through_seq, summary_json, model_id, input_tokens, output_tokens, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    new_id(),
                    result.id,
                    int(anchor["through_seq"]),
                    str(anchor["summary_json"]),
                    anchor["model_id"],
                    int(anchor["input_tokens"]),
                    int(anchor["output_tokens"]),
                    str(anchor["created_at"]),
                ),
            )
    return sessions.get_session(conn, result.id) or result
