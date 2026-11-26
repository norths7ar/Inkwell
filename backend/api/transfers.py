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
from fastapi.responses import FileResponse

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
    is_zip = path.suffix.lower() == ".zip"
    return BackupRecord(
        name=path.name,
        size_bytes=info.st_size,
        created_at=datetime.fromtimestamp(info.st_mtime, UTC).isoformat(),
        kind="zip" if is_zip else "sqlite",
        includes_attachments=is_zip,
        includes_encryption_key=False,
    )


def _safe_backup_path(backups_dir: Path, name: str) -> Path:
    if (
        not name
        or Path(name).name != name
        or not name.startswith("inkwell-")
        or not (name.endswith(".zip") or name.endswith(".sqlite3"))
    ):
        raise HTTPException(404, "Backup not found")
    target = backups_dir / name
    if not target.is_file() or target.is_symlink():
        raise HTTPException(404, "Backup not found")
    return target


def create_router(runtime: ApiRuntime) -> APIRouter:
    router = APIRouter()
    started = time.monotonic()
    covers_dir = runtime.config.attachments_dir / "covers"

    def list_backups() -> list[BackupRecord]:
        files = [
            *runtime.config.backups_dir.glob("inkwell-*.zip"),
            *runtime.config.backups_dir.glob("inkwell-*.sqlite3"),
        ]
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
        token = uuid4().hex[:8]
        destination = directory / f"inkwell-{stamp}-{token}.zip"
        db_partial = directory / f"inkwell-{stamp}-{token}.sqlite3.partial"
        zip_partial = directory / f"inkwell-{stamp}-{token}.zip.partial"
        try:
            with closing(sqlite3.connect(db_partial)) as target:
                runtime.conn.backup(target)
                if target.execute("PRAGMA quick_check").fetchone() != ("ok",):
                    raise RuntimeError("Backup integrity check failed")
            with zipfile.ZipFile(zip_partial, "w", compression=zipfile.ZIP_DEFLATED) as pkg:
                pkg.write(db_partial, "inkwell.sqlite3")
                attachments_dir = runtime.config.attachments_dir
                if attachments_dir.is_dir():
                    for path in sorted(attachments_dir.rglob("*")):
                        if path.is_file() and not path.is_symlink():
                            rel = path.relative_to(attachments_dir).as_posix()
                            pkg.write(path, f"attachments/{rel}")
            zip_partial.replace(destination)
        finally:
            db_partial.unlink(missing_ok=True)
            zip_partial.unlink(missing_ok=True)
        result = _backup_record(destination)
        runtime.events.publish(
            "content_changed", {"book_id": None, "resource": "backup", "resource_id": result.name}
        )
        return result

    @router.get("/backups/{name}")
    async def download_backup(name: str) -> FileResponse:
        target = _safe_backup_path(runtime.config.backups_dir, name)
        media_type = (
            "application/zip" if target.suffix.lower() == ".zip" else "application/octet-stream"
        )
        return FileResponse(target, media_type=media_type, filename=target.name)

    @router.delete("/backups/{name}", status_code=204)
    async def remove_backup(name: str) -> Response:
        target = _safe_backup_path(runtime.config.backups_dir, name)
        target.unlink()
        runtime.events.publish(
            "content_changed", {"book_id": None, "resource": "backup", "resource_id": name}
        )
        return Response(status_code=204)

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
