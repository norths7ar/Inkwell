"""Book transfers and local database snapshot/status REST contracts."""

import sqlite3
import time
import zipfile
from contextlib import closing
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal
from urllib.parse import quote
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Request, Response

from backend import archive
from backend.api.runtime import ApiRuntime
from backend.storage.migrations import current_version, ensure_transaction
from backend.storage.models import Book
from backend.storage.repos import content


@dataclass(frozen=True, slots=True)
class BackupRecord:
    name: str
    size_bytes: int
    created_at: str
    kind: str = "sqlite"
    includes_attachments: bool = False
    includes_encryption_key: bool = False


@dataclass(frozen=True, slots=True)
class SystemStatus:
    data_dir: str
    database_path: str
    database_bytes: int
    wal_bytes: int
    attachments_bytes: int
    backups_dir: str
    backups: list[BackupRecord]
    schema_version: int
    host: str
    port: int
    uptime_seconds: int
    books: int
    sessions: int
    running_runs: int
    waiting_runs: int


def _size(path: Path) -> int:
    return path.stat().st_size if path.is_file() else 0


def _backup_record(path: Path) -> BackupRecord:
    info = path.stat()
    return BackupRecord(
        name=path.name,
        size_bytes=info.st_size,
        created_at=datetime.fromtimestamp(info.st_mtime, UTC).isoformat(),
    )


def create_router(runtime: ApiRuntime) -> APIRouter:
    router = APIRouter()
    started = time.monotonic()
    covers_dir = runtime.config.attachments_dir / "covers"

    def list_backups() -> list[BackupRecord]:
        files = runtime.config.backups_dir.glob("inkwell-*.sqlite3")
        return sorted(
            [_backup_record(path) for path in files if path.is_file() and not path.is_symlink()],
            key=lambda item: (item.created_at, item.name),
            reverse=True,
        )

    @router.get("/books/{book_id}/export")
    async def export_book(book_id: str, format: Literal["zip", "txt", "md"] = "zip") -> Response:
        # A read transaction keeps the package coherent even if another DB connection writes.
        with ensure_transaction(runtime.conn):
            book = content.get_book(runtime.conn, book_id)
            if book is None:
                raise LookupError("Book not found")
            if format == "zip":
                body = archive.export_zip(runtime.conn, book, covers_dir)
                media_type = "application/zip"
            else:
                body = archive.export_text(runtime.conn, book, markdown=format == "md")
                media_type = "text/markdown" if format == "md" else "text/plain"
        filename = f"{archive.safe_name(book.title)}.{format}"
        return Response(
            body,
            media_type=media_type,
            headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(filename)}"},
        )

    @router.post("/books/import", status_code=201)
    async def import_book(request: Request) -> Book:
        media_type = request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
        if media_type not in ("application/zip", "application/x-zip-compressed"):
            raise HTTPException(415, "Expected application/zip")
        raw = bytearray()
        async for chunk in request.stream():
            if len(raw) + len(chunk) > archive.MAX_ARCHIVE_BYTES:
                raise HTTPException(413, "Archive exceeds 64 MiB")
            raw.extend(chunk)
        try:
            book = archive.import_zip(runtime.conn, bytes(raw), covers_dir)
        except (ValueError, KeyError, zipfile.BadZipFile, NotImplementedError) as exc:
            raise HTTPException(400, "Invalid Inkwell book archive") from exc
        runtime.events.publish(
            "content_changed", {"book_id": book.id, "resource": "book", "resource_id": book.id}
        )
        return book

    @router.get("/backups")
    async def backups() -> list[BackupRecord]:
        return list_backups()

    @router.post("/backups", status_code=201)
    async def backup() -> BackupRecord:
        directory = runtime.config.backups_dir
        directory.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
        destination = directory / f"inkwell-{stamp}-{uuid4().hex[:8]}.sqlite3"
        partial = destination.with_suffix(".partial")
        try:
            with closing(sqlite3.connect(partial)) as target:
                runtime.conn.backup(target)
                if target.execute("PRAGMA quick_check").fetchone() != ("ok",):
                    raise RuntimeError("Snapshot integrity check failed")
            partial.replace(destination)
        finally:
            partial.unlink(missing_ok=True)
        result = _backup_record(destination)
        runtime.events.publish(
            "content_changed", {"book_id": None, "resource": "backup", "resource_id": result.name}
        )
        return result

    @router.get("/system/status")
    async def status() -> SystemStatus:
        def count(table: str) -> int:
            return int(runtime.conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])

        return SystemStatus(
            data_dir=str(runtime.config.data_dir),
            database_path=str(runtime.config.db_path),
            database_bytes=_size(runtime.config.db_path),
            wal_bytes=_size(Path(f"{runtime.config.db_path}-wal")),
            attachments_bytes=sum(
                _size(path)
                for path in runtime.config.attachments_dir.rglob("*")
                if not path.is_symlink()
            ),
            backups_dir=str(runtime.config.backups_dir),
            backups=list_backups(),
            schema_version=current_version(runtime.conn),
            host=runtime.config.host,
            port=runtime.config.port,
            uptime_seconds=int(time.monotonic() - started),
            books=count("books"),
            sessions=count("sessions"),
            running_runs=int(
                runtime.conn.execute("SELECT COUNT(*) FROM runs WHERE status='running'").fetchone()[
                    0
                ]
            ),
            waiting_runs=int(
                runtime.conn.execute(
                    "SELECT COUNT(*) FROM runs WHERE status='waiting_user'"
                ).fetchone()[0]
            ),
        )

    return router
