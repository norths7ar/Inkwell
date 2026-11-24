"""Single-SQLite database connection and transaction helper."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from backend.storage.migrations import ensure_transaction, migrate


class Database:
    """Manages connections and schema initialization for a single SQLite database."""

    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path

    def connect(self) -> sqlite3.Connection:
        """Open a configured SQLite connection with explicit transaction semantics."""
        conn = sqlite3.connect(self.db_path, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA busy_timeout = 30000")
        conn.execute("PRAGMA synchronous = NORMAL")
        return conn

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        """Open a connection, wrap it in a transaction, and close on exit."""
        conn = self.connect()
        try:
            with ensure_transaction(conn):
                yield conn
        finally:
            conn.close()

    def initialize(self) -> int:
        """Ensure parent directory exists, migrate schema, and return schema version."""
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = self.connect()
        try:
            return migrate(conn)
        finally:
            conn.close()
