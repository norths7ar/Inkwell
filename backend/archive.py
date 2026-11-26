"""Versioned, portable book archives; importing always creates a separate book."""

import io
import re
import sqlite3
import stat
import zipfile
from pathlib import Path, PurePosixPath
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from backend.storage.migrations import ensure_transaction
from backend.storage.models import Book, Chapter, EntryKind, Visibility, validate_entry_meta
from backend.storage.repos import content

MAX_ARCHIVE_BYTES = 64 * 1024 * 1024
MAX_EXPANDED_BYTES = 128 * 1024 * 1024
MAX_MEMBERS = 10_000
_KINDS = {
    EntryKind.CHARACTER: "人物",
    EntryKind.WORLD: "世界",
    EntryKind.OUTLINE: "提纲",
    EntryKind.NOTE: "笔记",
}
_COVER_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}


class ArchiveRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str
    created_at: str
    updated_at: str


class ArchiveBook(ArchiveRecord):
    description: str
    rules_markdown: str
    default_visibility: Visibility


class ArchiveVolume(ArchiveRecord):
    key: str
    description: str
    sort_order: int


class ArchiveChapter(ArchiveRecord):
    path: str
    volume_key: str | None
    summary: str = ""
    sort_order: int


class ArchiveEntry(ArchiveRecord):
    path: str
    kind: EntryKind
    category: str
    visibility: Visibility
    meta_json: str
    sort_order: int


class Manifest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    format: Literal["inkwell-book"] = "inkwell-book"
    version: Literal[1] = 1
    book: ArchiveBook
    volumes: list[ArchiveVolume] = Field(default_factory=list)
    chapters: list[ArchiveChapter] = Field(default_factory=list)
    entries: list[ArchiveEntry] = Field(default_factory=list)
    cover: str | None = None


def safe_name(title: str) -> str:
    """Portable filename component; manifest retains the unmodified title."""
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", title).strip(" .")[:70] or "未命名"
    reserved = {
        "CON",
        "PRN",
        "AUX",
        "NUL",
        *[f"COM{i}" for i in range(1, 10)],
        *[f"LPT{i}" for i in range(1, 10)],
    }
    return f"_{name}" if name.split(".", 1)[0].upper() in reserved else name


def ordered_chapters(conn: sqlite3.Connection, book_id: str) -> list[Chapter]:
    chapters = content.list_chapters(conn, book_id)
    volumes = content.list_volumes(conn, book_id)
    return [
        chapter
        for volume_id in [*[volume.id for volume in volumes], None]
        for chapter in chapters
        if chapter.volume_id == volume_id
    ]


def export_text(conn: sqlite3.Connection, book: Book, *, markdown: bool) -> bytes:
    parts = [f"# {book.title}" if markdown else book.title]
    volumes = {volume.id: volume.title for volume in content.list_volumes(conn, book.id)}
    previous_volume: str | None = None
    for chapter in ordered_chapters(conn, book.id):
        if chapter.volume_id is not None and chapter.volume_id != previous_volume:
            title = volumes[chapter.volume_id]
            parts.append(f"## {title}" if markdown else title)
        previous_volume = chapter.volume_id
        parts.extend([f"### {chapter.title}" if markdown else chapter.title, chapter.content])
    return ("\n\n".join(parts) + "\n").encode("utf-8")


def export_zip(conn: sqlite3.Connection, book: Book, covers_dir: Path) -> bytes:
    volumes = content.list_volumes(conn, book.id)
    volume_names = {
        volume.id: f"{index:04d}-{safe_name(volume.title)}"
        for index, volume in enumerate(volumes, 1)
    }
    manifest = Manifest(
        book=ArchiveBook(
            title=book.title,
            description=book.description,
            rules_markdown=book.rules_markdown,
            default_visibility=book.default_visibility,
            created_at=book.created_at,
            updated_at=book.updated_at,
        ),
        volumes=[
            ArchiveVolume(
                key=volume.id,
                title=volume.title,
                description=volume.description,
                sort_order=volume.sort_order,
                created_at=volume.created_at,
                updated_at=volume.updated_at,
            )
            for volume in volumes
        ],
    )
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for index, chapter in enumerate(ordered_chapters(conn, book.id), 1):
            folder = volume_names.get(chapter.volume_id or "", "未分卷")
            path = f"正文/{folder}/{index:04d}-{safe_name(chapter.title)}.md"
            archive.writestr(path, chapter.content.encode("utf-8"))
            manifest.chapters.append(
                ArchiveChapter(
                    path=path,
                    title=chapter.title,
                    volume_key=chapter.volume_id,
                    summary=chapter.summary,
                    sort_order=chapter.sort_order,
                    created_at=chapter.created_at,
                    updated_at=chapter.updated_at,
                )
            )
        for index, entry in enumerate(content.list_entries(conn, book.id), 1):
            category = f"/{safe_name(entry.category)}" if entry.category else ""
            path = f"设定/{_KINDS[entry.kind]}{category}/{index:04d}-{safe_name(entry.title)}.md"
            archive.writestr(path, entry.content.encode("utf-8"))
            manifest.entries.append(
                ArchiveEntry(
                    path=path,
                    title=entry.title,
                    kind=entry.kind,
                    category=entry.category,
                    visibility=entry.visibility,
                    meta_json=entry.meta_json,
                    sort_order=entry.sort_order,
                    created_at=entry.created_at,
                    updated_at=entry.updated_at,
                )
            )
        if book.cover_path:
            cover = covers_dir / Path(book.cover_path).name
            if not cover.is_file() or cover.suffix.lower() not in _COVER_EXTENSIONS:
                raise ValueError("Book cover is missing or unsupported")
            manifest.cover = f"封面/cover{cover.suffix.lower()}"
            archive.writestr(manifest.cover, cover.read_bytes())
        archive.writestr("manifest.json", manifest.model_dump_json(indent=2).encode("utf-8"))
    result = buffer.getvalue()
    # Every archive produced by this API must be accepted by its importer.
    if len(result) > MAX_ARCHIVE_BYTES:
        raise ValueError("Book archive exceeds the 64 MiB import limit")
    with zipfile.ZipFile(io.BytesIO(result)) as archive:
        _validate_members(archive)
    return result


def _validate_members(archive: zipfile.ZipFile) -> None:
    members = archive.infolist()
    if len(members) > MAX_MEMBERS or sum(item.file_size for item in members) > MAX_EXPANDED_BYTES:
        raise ValueError("Archive exceeds member or expanded size limits")
    names: set[str] = set()
    for member in members:
        name = member.filename
        path = PurePosixPath(name)
        if (
            not name
            or "\\" in name
            or ":" in name
            or "\x00" in name
            or path.is_absolute()
            or any(part in ("", ".", "..") for part in name.rstrip("/").split("/"))
            or name.casefold() in names
            or stat.S_ISLNK(member.external_attr >> 16)
            or member.flag_bits & 1
        ):
            raise ValueError("Archive contains unsafe or duplicate member paths")
        names.add(name.casefold())


def _restore_times(
    conn: sqlite3.Connection, table: str, record_id: str, source: ArchiveRecord
) -> None:
    # Table names are fixed by the callers, never read from the archive.
    conn.execute(
        f"UPDATE {table} SET created_at=?, updated_at=? WHERE id=?",
        (source.created_at, source.updated_at, record_id),
    )


def import_zip(conn: sqlite3.Connection, raw: bytes, covers_dir: Path) -> Book:
    """Validate the entire package before writing; rollback also removes a new cover."""
    if len(raw) > MAX_ARCHIVE_BYTES:
        raise ValueError("Archive exceeds 64 MiB")
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        _validate_members(archive)
        manifest = Manifest.model_validate_json(archive.read("manifest.json"))
        volume_keys = [volume.key for volume in manifest.volumes]
        if len(set(volume_keys)) != len(volume_keys):
            raise ValueError("Duplicate volume keys")
        paths = [item.path for item in [*manifest.chapters, *manifest.entries]]
        if len(set(paths)) != len(paths):
            raise ValueError("Duplicate content references")
        if any(
            item.volume_key is not None and item.volume_key not in volume_keys
            for item in manifest.chapters
        ):
            raise ValueError("Chapter references an unknown volume")
        for entry in manifest.entries:
            # Category is one opaque label, not a filesystem path. Export sanitizes
            # its directory component; the manifest preserves existing labels verbatim.
            validate_entry_meta(entry.kind, entry.meta_json)
        texts = {path: archive.read(path).decode("utf-8") for path in paths}
        cover_bytes: bytes | None = None
        if manifest.cover is not None:
            if Path(manifest.cover).suffix.lower() not in _COVER_EXTENSIONS:
                raise ValueError("Unsupported cover format")
            cover_bytes = archive.read(manifest.cover)
            if not cover_bytes or len(cover_bytes) > 5 * 1024 * 1024:
                raise ValueError("Cover must contain at most 5 MiB")
    created_cover: Path | None = None
    try:
        with ensure_transaction(conn):
            book = content.create_book(
                conn,
                title=manifest.book.title,
                description=manifest.book.description,
                rules_markdown=manifest.book.rules_markdown,
                default_visibility=manifest.book.default_visibility,
            )
            volume_ids: dict[str, str] = {}
            for volume in manifest.volumes:
                item = content.create_volume(
                    conn,
                    book_id=book.id,
                    title=volume.title,
                    description=volume.description,
                    sort_order=volume.sort_order,
                )
                volume_ids[volume.key] = item.id
                _restore_times(conn, "volumes", item.id, volume)
            for chapter in manifest.chapters:
                item = content.create_chapter(
                    conn,
                    book_id=book.id,
                    title=chapter.title,
                    content=texts[chapter.path],
                    summary=chapter.summary,
                    sort_order=chapter.sort_order,
                    volume_id=volume_ids[chapter.volume_key]
                    if chapter.volume_key is not None
                    else None,
                )
                _restore_times(conn, "chapters", item.id, chapter)
            for entry in manifest.entries:
                item = content.create_entry(
                    conn,
                    book_id=book.id,
                    title=entry.title,
                    content=texts[entry.path],
                    kind=entry.kind,
                    category=entry.category,
                    visibility=entry.visibility,
                    meta_json=entry.meta_json,
                    sort_order=entry.sort_order,
                )
                _restore_times(conn, "entries", item.id, entry)
            if cover_bytes is not None and manifest.cover is not None:
                covers_dir.mkdir(parents=True, exist_ok=True)
                created_cover = covers_dir / f"{book.id}{Path(manifest.cover).suffix.lower()}"
                created_cover.write_bytes(cover_bytes)
                content.update_book(conn, book.id, cover_path=created_cover.name)
            _restore_times(conn, "books", book.id, manifest.book)
        result = content.get_book(conn, book.id)
        if result is None:
            raise RuntimeError("Imported book was not persisted")
        return result
    except BaseException:
        if created_cover is not None:
            created_cover.unlink(missing_ok=True)
        raise
