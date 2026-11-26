"""Transactional SQLite schema migrations via PRAGMA user_version."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager

SCHEMA_VERSION = 4

_V1_STATEMENTS: tuple[str, ...] = (
    """
    CREATE TABLE books (
        id TEXT PRIMARY KEY,
        title TEXT NOT NULL,
        description TEXT NOT NULL DEFAULT '',
        rules_markdown TEXT NOT NULL DEFAULT '',
        default_visibility TEXT NOT NULL DEFAULT 'local'
            CHECK (default_visibility IN ('local', 'global')),
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE volumes (
        id TEXT PRIMARY KEY,
        book_id TEXT NOT NULL REFERENCES books(id) ON DELETE CASCADE,
        title TEXT NOT NULL,
        description TEXT NOT NULL DEFAULT '',
        sort_order INTEGER NOT NULL DEFAULT 0,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE chapters (
        id TEXT PRIMARY KEY,
        book_id TEXT NOT NULL REFERENCES books(id) ON DELETE CASCADE,
        volume_id TEXT REFERENCES volumes(id) ON DELETE SET NULL,
        title TEXT NOT NULL,
        content TEXT NOT NULL DEFAULT '',
        summary TEXT NOT NULL DEFAULT '',
        word_count INTEGER NOT NULL DEFAULT 0,
        sort_order INTEGER NOT NULL DEFAULT 0,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE entries (
        id TEXT PRIMARY KEY,
        book_id TEXT NOT NULL REFERENCES books(id) ON DELETE CASCADE,
        kind TEXT NOT NULL CHECK (kind IN ('character', 'world', 'note')),
        title TEXT NOT NULL,
        category TEXT NOT NULL DEFAULT '',
        content TEXT NOT NULL DEFAULT '',
        visibility TEXT NOT NULL DEFAULT 'local'
            CHECK (visibility IN ('local', 'global')),
        meta_json TEXT NOT NULL DEFAULT '{}',
        sort_order INTEGER NOT NULL DEFAULT 0,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE sessions (
        id TEXT PRIMARY KEY,
        book_id TEXT NOT NULL REFERENCES books(id) ON DELETE CASCADE,
        title TEXT NOT NULL DEFAULT '',
        mode TEXT NOT NULL DEFAULT 'discuss'
            CHECK (mode IN ('discuss', 'write', 'review')),
        visibility_scope TEXT NOT NULL DEFAULT 'local'
            CHECK (visibility_scope IN ('local', 'global')),
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE runs (
        id TEXT PRIMARY KEY,
        session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
        book_id TEXT NOT NULL REFERENCES books(id) ON DELETE CASCADE,
        status TEXT NOT NULL
            CHECK (status IN ('running', 'waiting_user', 'completed', 'cancelled', 'failed')),
        model_id TEXT,
        pending_question_json TEXT,
        error_message TEXT,
        input_tokens INTEGER NOT NULL DEFAULT 0,
        output_tokens INTEGER NOT NULL DEFAULT 0,
        cache_read_tokens INTEGER NOT NULL DEFAULT 0,
        cache_write_tokens INTEGER NOT NULL DEFAULT 0,
        started_at TEXT NOT NULL,
        finished_at TEXT
    )
    """,
    """
    CREATE TABLE messages (
        id TEXT PRIMARY KEY,
        session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
        book_id TEXT NOT NULL REFERENCES books(id) ON DELETE CASCADE,
        run_id TEXT REFERENCES runs(id) ON DELETE SET NULL,
        seq INTEGER NOT NULL,
        role TEXT NOT NULL CHECK (role IN ('system', 'user', 'assistant', 'tool')),
        blocks_json TEXT NOT NULL,
        created_at TEXT NOT NULL,
        UNIQUE (session_id, seq)
    )
    """,
    """
    CREATE TABLE pending_changes (
        id TEXT PRIMARY KEY,
        book_id TEXT NOT NULL REFERENCES books(id) ON DELETE CASCADE,
        session_id TEXT REFERENCES sessions(id) ON DELETE SET NULL,
        run_id TEXT REFERENCES runs(id) ON DELETE SET NULL,
        target_type TEXT NOT NULL CHECK (target_type IN ('chapter', 'entry')),
        target_id TEXT,
        action TEXT NOT NULL CHECK (action IN ('create', 'update', 'delete')),
        title TEXT NOT NULL DEFAULT '',
        before_content TEXT NOT NULL DEFAULT '',
        after_content TEXT NOT NULL DEFAULT '',
        payload_json TEXT NOT NULL DEFAULT '{}',
        status TEXT NOT NULL DEFAULT 'pending'
            CHECK (status IN ('pending', 'applied', 'rejected')),
        created_at TEXT NOT NULL,
        resolved_at TEXT
    )
    """,
    """
    CREATE TABLE revisions (
        id TEXT PRIMARY KEY,
        book_id TEXT NOT NULL REFERENCES books(id) ON DELETE CASCADE,
        target_type TEXT NOT NULL CHECK (target_type IN ('chapter', 'entry')),
        target_id TEXT NOT NULL,
        source TEXT NOT NULL CHECK (source IN ('user', 'pending_apply', 'restore')),
        pending_change_id TEXT REFERENCES pending_changes(id) ON DELETE SET NULL,
        title TEXT NOT NULL,
        content TEXT NOT NULL,
        snapshot_json TEXT NOT NULL DEFAULT '{}',
        created_at TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE providers (
        id TEXT PRIMARY KEY,
        name TEXT NOT NULL,
        preset TEXT NOT NULL DEFAULT '',
        base_url TEXT NOT NULL,
        discovery_url TEXT NOT NULL DEFAULT '',
        default_protocol TEXT NOT NULL CHECK (
            default_protocol IN (
                'openai-chat',
                'openai-responses',
                'anthropic-messages',
                'google-genai'
            )
        ),
        api_key_encrypted TEXT NOT NULL DEFAULT '',
        headers_encrypted TEXT NOT NULL DEFAULT '',
        enabled INTEGER NOT NULL DEFAULT 1,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE models (
        id TEXT PRIMARY KEY,
        provider_id TEXT NOT NULL REFERENCES providers(id) ON DELETE CASCADE,
        model_id TEXT NOT NULL,
        display_name TEXT NOT NULL,
        protocol_override TEXT CHECK (
            protocol_override IS NULL
            OR protocol_override IN (
                'openai-chat',
                'openai-responses',
                'anthropic-messages',
                'google-genai'
            )
        ),
        context_window INTEGER,
        max_output_tokens INTEGER,
        capabilities_json TEXT NOT NULL DEFAULT '{}',
        enabled INTEGER NOT NULL DEFAULT 1,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        UNIQUE (provider_id, model_id)
    )
    """,
    """
    CREATE TABLE settings (
        key TEXT PRIMARY KEY,
        value TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )
    """,
    """
    CREATE VIRTUAL TABLE content_fts USING fts5(
        target_type UNINDEXED,
        target_id UNINDEXED,
        book_id UNINDEXED,
        title,
        body,
        tokenize = 'trigram'
    )
    """,
    "CREATE INDEX idx_volumes_book_order ON volumes (book_id, sort_order)",
    "CREATE INDEX idx_chapters_book_order ON chapters (book_id, sort_order)",
    "CREATE INDEX idx_entries_book_order ON entries (book_id, kind, sort_order)",
    "CREATE INDEX idx_pending_changes_book_status ON pending_changes (book_id, status)",
    "CREATE INDEX idx_revisions_target ON revisions (book_id, target_type, target_id, created_at)",
    "CREATE INDEX idx_messages_session_seq ON messages (session_id, seq)",
    "CREATE INDEX idx_runs_session_started ON runs (session_id, started_at)",
)

MIGRATIONS: tuple[tuple[int, tuple[str, ...]], ...] = (
    (1, _V1_STATEMENTS),
    (2, ("ALTER TABLE runs ADD COLUMN error_json TEXT",)),
    (3, ("ALTER TABLE books ADD COLUMN cover_path TEXT NOT NULL DEFAULT ''",)),
    (
        4,
        (
            "ALTER TABLE sessions ADD COLUMN pinned INTEGER NOT NULL DEFAULT 0 CHECK(pinned IN (0, 1))",
            "ALTER TABLE runs ADD COLUMN reasoning_effort TEXT",
            "ALTER TABLE runs ADD COLUMN references_json TEXT NOT NULL DEFAULT '[]'",
            "ALTER TABLE runs ADD COLUMN reasoning_duration_ms INTEGER NOT NULL DEFAULT 0",
            """CREATE TABLE compactions (
            id TEXT PRIMARY KEY,
            session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
            through_seq INTEGER NOT NULL,
            summary_json TEXT NOT NULL,
            model_id TEXT,
            input_tokens INTEGER NOT NULL DEFAULT 0,
            output_tokens INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL
        )""",
            "CREATE INDEX idx_compactions_session_seq ON compactions(session_id, through_seq)",
        ),
    ),
)


@contextmanager
def ensure_transaction(conn: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    """Run operations inside an explicit transaction unless one is already active."""
    if conn.in_transaction:
        yield conn
        return
    conn.execute("BEGIN")
    try:
        yield conn
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    else:
        conn.execute("COMMIT")


def current_version(conn: sqlite3.Connection) -> int:
    row = conn.execute("PRAGMA user_version").fetchone()
    return int(row[0]) if row is not None else 0


def migrate(conn: sqlite3.Connection) -> int:
    """Apply pending schema migrations sequentially and return final schema version."""
    version = current_version(conn)
    if version > SCHEMA_VERSION:
        raise RuntimeError(
            f"Database schema version {version} is newer than supported version {SCHEMA_VERSION}"
        )

    for target_version, statements in MIGRATIONS:
        if target_version <= version:
            continue
        with ensure_transaction(conn):
            for statement in statements:
                conn.execute(statement)
            conn.execute(f"PRAGMA user_version = {target_version}")
        version = target_version

    return version
