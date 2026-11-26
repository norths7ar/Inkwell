"""Persistent context overlays; original conversation messages are never removed."""

import sqlite3
from dataclasses import dataclass

from pydantic import BaseModel, ConfigDict

from backend.storage.ids import new_id, utc_now
from backend.storage.migrations import ensure_transaction


class CompactionSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")
    objective: str
    facts: list[str]
    decisions: list[str]
    open_questions: list[str]
    next_steps: list[str]


@dataclass(frozen=True, slots=True)
class Compaction:
    id: str
    session_id: str
    through_seq: int
    summary: CompactionSummary
    model_id: str | None
    input_tokens: int
    output_tokens: int
    created_at: str


def _from_row(row: sqlite3.Row) -> Compaction:
    return Compaction(
        id=str(row["id"]),
        session_id=str(row["session_id"]),
        through_seq=int(row["through_seq"]),
        summary=CompactionSummary.model_validate_json(str(row["summary_json"])),
        model_id=str(row["model_id"]) if row["model_id"] is not None else None,
        input_tokens=int(row["input_tokens"]),
        output_tokens=int(row["output_tokens"]),
        created_at=str(row["created_at"]),
    )


def list_compactions(conn: sqlite3.Connection, session_id: str) -> list[Compaction]:
    return [
        _from_row(row)
        for row in conn.execute(
            "SELECT * FROM compactions WHERE session_id = ? ORDER BY through_seq DESC, created_at DESC, id DESC",
            (session_id,),
        ).fetchall()
    ]


def latest_compaction(conn: sqlite3.Connection, session_id: str) -> Compaction | None:
    row = conn.execute(
        "SELECT * FROM compactions WHERE session_id = ? ORDER BY through_seq DESC, created_at DESC, id DESC LIMIT 1",
        (session_id,),
    ).fetchone()
    return _from_row(row) if row is not None else None


def create_compaction(
    conn: sqlite3.Connection,
    *,
    session_id: str,
    through_seq: int,
    summary: CompactionSummary,
    model_id: str,
    input_tokens: int,
    output_tokens: int,
) -> Compaction:
    result = Compaction(
        new_id(), session_id, through_seq, summary, model_id, input_tokens, output_tokens, utc_now()
    )
    with ensure_transaction(conn):
        conn.execute(
            """INSERT INTO compactions
               (id, session_id, through_seq, summary_json, model_id, input_tokens, output_tokens, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                result.id,
                session_id,
                through_seq,
                summary.model_dump_json(),
                model_id,
                input_tokens,
                output_tokens,
                result.created_at,
            ),
        )
    return result
