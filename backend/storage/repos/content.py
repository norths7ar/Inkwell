"""Repositories for books, volumes, chapters, entries, and full-text search."""

from __future__ import annotations

import sqlite3
from enum import Enum

from backend.storage.ids import new_id, utc_now
from backend.storage.migrations import ensure_transaction
from backend.storage.models import (
    Book,
    Chapter,
    Entry,
    EntryKind,
    SearchHit,
    TargetType,
    Visibility,
    Volume,
    validate_entry_meta,
)


class Unset(Enum):
    """Sentinel distinguishing omitted update fields from explicit None values."""

    VALUE = "unset"


UNSET = Unset.VALUE


def count_words(text: str) -> int:
    """Count non-whitespace characters for consistent CJK/Latin length tracking."""
    return sum(1 for char in text if not char.isspace())


def upsert_fts(
    conn: sqlite3.Connection,
    *,
    target_type: TargetType,
    target_id: str,
    book_id: str,
    title: str,
    body: str,
) -> None:
    """Replace the FTS5 index row for a single chapter or entry."""
    delete_fts(conn, target_type=target_type, target_id=target_id)
    conn.execute(
        """
        INSERT INTO content_fts (target_type, target_id, book_id, title, body)
        VALUES (?, ?, ?, ?, ?)
        """,
        (target_type.value, target_id, book_id, title, body),
    )


def delete_fts(conn: sqlite3.Connection, *, target_type: TargetType, target_id: str) -> None:
    conn.execute(
        "DELETE FROM content_fts WHERE target_type = ? AND target_id = ?",
        (target_type.value, target_id),
    )


def delete_book_fts(conn: sqlite3.Connection, *, book_id: str) -> None:
    conn.execute("DELETE FROM content_fts WHERE book_id = ?", (book_id,))


def _book_from_row(row: sqlite3.Row) -> Book:
    return Book(
        id=str(row["id"]),
        title=str(row["title"]),
        description=str(row["description"]),
        rules_markdown=str(row["rules_markdown"]),
        default_visibility=Visibility(str(row["default_visibility"])),
        created_at=str(row["created_at"]),
        updated_at=str(row["updated_at"]),
        cover_path=str(row["cover_path"]),
    )


def _volume_from_row(row: sqlite3.Row) -> Volume:
    return Volume(
        id=str(row["id"]),
        book_id=str(row["book_id"]),
        title=str(row["title"]),
        description=str(row["description"]),
        sort_order=int(row["sort_order"]),
        created_at=str(row["created_at"]),
        updated_at=str(row["updated_at"]),
    )


def _chapter_from_row(row: sqlite3.Row) -> Chapter:
    raw_volume_id = row["volume_id"]
    return Chapter(
        id=str(row["id"]),
        book_id=str(row["book_id"]),
        volume_id=str(raw_volume_id) if raw_volume_id is not None else None,
        title=str(row["title"]),
        content=str(row["content"]),
        summary=str(row["summary"]),
        word_count=int(row["word_count"]),
        sort_order=int(row["sort_order"]),
        created_at=str(row["created_at"]),
        updated_at=str(row["updated_at"]),
    )


def _entry_from_row(row: sqlite3.Row) -> Entry:
    return Entry(
        id=str(row["id"]),
        book_id=str(row["book_id"]),
        kind=EntryKind(str(row["kind"])),
        title=str(row["title"]),
        category=str(row["category"]),
        content=str(row["content"]),
        visibility=Visibility(str(row["visibility"])),
        meta_json=str(row["meta_json"]),
        sort_order=int(row["sort_order"]),
        created_at=str(row["created_at"]),
        updated_at=str(row["updated_at"]),
    )


def create_book(
    conn: sqlite3.Connection,
    *,
    title: str,
    description: str = "",
    rules_markdown: str = "",
    default_visibility: Visibility = Visibility.LOCAL,
    cover_path: str = "",
    book_id: str | None = None,
) -> Book:
    now = utc_now()
    resolved_id = book_id or new_id()
    with ensure_transaction(conn):
        conn.execute(
            """
            INSERT INTO books (
                id, title, description, rules_markdown, default_visibility, cover_path,
                created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                resolved_id,
                title,
                description,
                rules_markdown,
                default_visibility.value,
                cover_path,
                now,
                now,
            ),
        )
    book = get_book(conn, resolved_id)
    if book is None:
        raise RuntimeError(f"Failed to load newly created book {resolved_id}")
    return book


def get_book(conn: sqlite3.Connection, book_id: str) -> Book | None:
    row = conn.execute("SELECT * FROM books WHERE id = ?", (book_id,)).fetchone()
    return _book_from_row(row) if row is not None else None


def list_books(conn: sqlite3.Connection) -> list[Book]:
    rows = conn.execute("SELECT * FROM books ORDER BY updated_at DESC, id ASC").fetchall()
    return [_book_from_row(row) for row in rows]


def update_book(
    conn: sqlite3.Connection,
    book_id: str,
    *,
    title: str | Unset = UNSET,
    description: str | Unset = UNSET,
    rules_markdown: str | Unset = UNSET,
    default_visibility: Visibility | Unset = UNSET,
    cover_path: str | Unset = UNSET,
) -> Book:
    existing = get_book(conn, book_id)
    if existing is None:
        raise LookupError(f"Book not found: {book_id}")
    now = utc_now()
    with ensure_transaction(conn):
        conn.execute(
            """
            UPDATE books
            SET title = ?, description = ?, rules_markdown = ?, default_visibility = ?,
                cover_path = ?, updated_at = ?
            WHERE id = ?
            """,
            (
                existing.title if isinstance(title, Unset) else title,
                existing.description if isinstance(description, Unset) else description,
                existing.rules_markdown if isinstance(rules_markdown, Unset) else rules_markdown,
                (
                    existing.default_visibility
                    if isinstance(default_visibility, Unset)
                    else default_visibility
                ).value,
                existing.cover_path if isinstance(cover_path, Unset) else cover_path,
                now,
                book_id,
            ),
        )
    updated = get_book(conn, book_id)
    if updated is None:
        raise LookupError(f"Book not found after update: {book_id}")
    return updated


def delete_book(conn: sqlite3.Connection, book_id: str) -> bool:
    with ensure_transaction(conn):
        delete_book_fts(conn, book_id=book_id)
        cursor = conn.execute("DELETE FROM books WHERE id = ?", (book_id,))
    return cursor.rowcount > 0


def create_volume(
    conn: sqlite3.Connection,
    *,
    book_id: str,
    title: str,
    description: str = "",
    sort_order: int = 0,
    volume_id: str | None = None,
) -> Volume:
    now = utc_now()
    resolved_id = volume_id or new_id()
    with ensure_transaction(conn):
        conn.execute(
            """
            INSERT INTO volumes (id, book_id, title, description, sort_order, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (resolved_id, book_id, title, description, sort_order, now, now),
        )
    volume = get_volume(conn, resolved_id)
    if volume is None:
        raise RuntimeError(f"Failed to load newly created volume {resolved_id}")
    return volume


def get_volume(conn: sqlite3.Connection, volume_id: str) -> Volume | None:
    row = conn.execute("SELECT * FROM volumes WHERE id = ?", (volume_id,)).fetchone()
    return _volume_from_row(row) if row is not None else None


def list_volumes(conn: sqlite3.Connection, book_id: str) -> list[Volume]:
    rows = conn.execute(
        "SELECT * FROM volumes WHERE book_id = ? ORDER BY sort_order ASC, created_at ASC",
        (book_id,),
    ).fetchall()
    return [_volume_from_row(row) for row in rows]


def update_volume(
    conn: sqlite3.Connection,
    volume_id: str,
    *,
    title: str | Unset = UNSET,
    description: str | Unset = UNSET,
    sort_order: int | Unset = UNSET,
) -> Volume:
    existing = get_volume(conn, volume_id)
    if existing is None:
        raise LookupError(f"Volume not found: {volume_id}")
    now = utc_now()
    with ensure_transaction(conn):
        conn.execute(
            """
            UPDATE volumes
            SET title = ?, description = ?, sort_order = ?, updated_at = ?
            WHERE id = ?
            """,
            (
                existing.title if isinstance(title, Unset) else title,
                existing.description if isinstance(description, Unset) else description,
                existing.sort_order if isinstance(sort_order, Unset) else sort_order,
                now,
                volume_id,
            ),
        )
    updated = get_volume(conn, volume_id)
    if updated is None:
        raise LookupError(f"Volume not found after update: {volume_id}")
    return updated


def delete_volume(conn: sqlite3.Connection, volume_id: str) -> bool:
    with ensure_transaction(conn):
        cursor = conn.execute("DELETE FROM volumes WHERE id = ?", (volume_id,))
    return cursor.rowcount > 0


def create_chapter(
    conn: sqlite3.Connection,
    *,
    book_id: str,
    title: str,
    content: str = "",
    summary: str = "",
    volume_id: str | None = None,
    sort_order: int = 0,
    chapter_id: str | None = None,
) -> Chapter:
    now = utc_now()
    resolved_id = chapter_id or new_id()
    words = count_words(content)
    with ensure_transaction(conn):
        conn.execute(
            """
            INSERT INTO chapters (
                id, book_id, volume_id, title, content, summary,
                word_count, sort_order, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (resolved_id, book_id, volume_id, title, content, summary, words, sort_order, now, now),
        )
        upsert_fts(
            conn,
            target_type=TargetType.CHAPTER,
            target_id=resolved_id,
            book_id=book_id,
            title=title,
            body=content,
        )
    chapter = get_chapter(conn, resolved_id)
    if chapter is None:
        raise RuntimeError(f"Failed to load newly created chapter {resolved_id}")
    return chapter


def get_chapter(conn: sqlite3.Connection, chapter_id: str) -> Chapter | None:
    row = conn.execute("SELECT * FROM chapters WHERE id = ?", (chapter_id,)).fetchone()
    return _chapter_from_row(row) if row is not None else None


def list_chapters(
    conn: sqlite3.Connection,
    book_id: str,
    *,
    volume_id: str | None = None,
) -> list[Chapter]:
    if volume_id is None:
        rows = conn.execute(
            "SELECT * FROM chapters WHERE book_id = ? ORDER BY sort_order ASC, created_at ASC",
            (book_id,),
        ).fetchall()
    else:
        rows = conn.execute(
            """
            SELECT * FROM chapters
            WHERE book_id = ? AND volume_id = ?
            ORDER BY sort_order ASC, created_at ASC
            """,
            (book_id, volume_id),
        ).fetchall()
    return [_chapter_from_row(row) for row in rows]


def update_chapter(
    conn: sqlite3.Connection,
    chapter_id: str,
    *,
    volume_id: str | None | Unset = UNSET,
    title: str | Unset = UNSET,
    content: str | Unset = UNSET,
    summary: str | Unset = UNSET,
    sort_order: int | Unset = UNSET,
) -> Chapter:
    existing = get_chapter(conn, chapter_id)
    if existing is None:
        raise LookupError(f"Chapter not found: {chapter_id}")
    new_title = existing.title if isinstance(title, Unset) else title
    new_content = existing.content if isinstance(content, Unset) else content
    new_volume_id = existing.volume_id if isinstance(volume_id, Unset) else volume_id
    new_summary = existing.summary if isinstance(summary, Unset) else summary
    new_sort_order = existing.sort_order if isinstance(sort_order, Unset) else sort_order
    words = count_words(new_content)
    now = utc_now()
    with ensure_transaction(conn):
        conn.execute(
            """
            UPDATE chapters
            SET volume_id = ?, title = ?, content = ?, summary = ?,
                word_count = ?, sort_order = ?, updated_at = ?
            WHERE id = ?
            """,
            (
                new_volume_id,
                new_title,
                new_content,
                new_summary,
                words,
                new_sort_order,
                now,
                chapter_id,
            ),
        )
        upsert_fts(
            conn,
            target_type=TargetType.CHAPTER,
            target_id=chapter_id,
            book_id=existing.book_id,
            title=new_title,
            body=new_content,
        )
    updated = get_chapter(conn, chapter_id)
    if updated is None:
        raise LookupError(f"Chapter not found after update: {chapter_id}")
    return updated


def delete_chapter(conn: sqlite3.Connection, chapter_id: str) -> bool:
    with ensure_transaction(conn):
        delete_fts(conn, target_type=TargetType.CHAPTER, target_id=chapter_id)
        cursor = conn.execute("DELETE FROM chapters WHERE id = ?", (chapter_id,))
    return cursor.rowcount > 0


def create_entry(
    conn: sqlite3.Connection,
    *,
    book_id: str,
    kind: EntryKind,
    title: str,
    category: str = "",
    content: str = "",
    visibility: Visibility = Visibility.LOCAL,
    meta_json: str = "{}",
    sort_order: int = 0,
    entry_id: str | None = None,
) -> Entry:
    normalized_meta = validate_entry_meta(kind, meta_json)
    now = utc_now()
    resolved_id = entry_id or new_id()
    with ensure_transaction(conn):
        conn.execute(
            """
            INSERT INTO entries (
                id, book_id, kind, title, category, content,
                visibility, meta_json, sort_order, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                resolved_id,
                book_id,
                kind.value,
                title,
                category,
                content,
                visibility.value,
                normalized_meta,
                sort_order,
                now,
                now,
            ),
        )
        upsert_fts(
            conn,
            target_type=TargetType.ENTRY,
            target_id=resolved_id,
            book_id=book_id,
            title=title,
            body=content,
        )
    entry = get_entry(conn, resolved_id)
    if entry is None:
        raise RuntimeError(f"Failed to load newly created entry {resolved_id}")
    return entry


def get_entry(conn: sqlite3.Connection, entry_id: str) -> Entry | None:
    row = conn.execute("SELECT * FROM entries WHERE id = ?", (entry_id,)).fetchone()
    return _entry_from_row(row) if row is not None else None


def list_entries(
    conn: sqlite3.Connection,
    book_id: str,
    *,
    kind: EntryKind | None = None,
    visibility: Visibility | None = None,
) -> list[Entry]:
    clauses = ["book_id = ?"]
    params: list[str] = [book_id]
    if kind is not None:
        clauses.append("kind = ?")
        params.append(kind.value)
    if visibility is not None:
        clauses.append("visibility = ?")
        params.append(visibility.value)
    where_sql = " AND ".join(clauses)
    rows = conn.execute(
        f"SELECT * FROM entries WHERE {where_sql} ORDER BY sort_order ASC, created_at ASC",
        params,
    ).fetchall()
    return [_entry_from_row(row) for row in rows]


def update_entry(
    conn: sqlite3.Connection,
    entry_id: str,
    *,
    kind: EntryKind | Unset = UNSET,
    title: str | Unset = UNSET,
    category: str | Unset = UNSET,
    content: str | Unset = UNSET,
    visibility: Visibility | Unset = UNSET,
    meta_json: str | Unset = UNSET,
    sort_order: int | Unset = UNSET,
) -> Entry:
    existing = get_entry(conn, entry_id)
    if existing is None:
        raise LookupError(f"Entry not found: {entry_id}")
    new_kind = existing.kind if isinstance(kind, Unset) else kind
    new_title = existing.title if isinstance(title, Unset) else title
    new_category = existing.category if isinstance(category, Unset) else category
    new_content = existing.content if isinstance(content, Unset) else content
    new_visibility = existing.visibility if isinstance(visibility, Unset) else visibility
    raw_meta = existing.meta_json if isinstance(meta_json, Unset) else meta_json
    normalized_meta = validate_entry_meta(new_kind, raw_meta)
    new_sort_order = existing.sort_order if isinstance(sort_order, Unset) else sort_order
    now = utc_now()
    with ensure_transaction(conn):
        conn.execute(
            """
            UPDATE entries
            SET kind = ?, title = ?, category = ?, content = ?,
                visibility = ?, meta_json = ?, sort_order = ?, updated_at = ?
            WHERE id = ?
            """,
            (
                new_kind.value,
                new_title,
                new_category,
                new_content,
                new_visibility.value,
                normalized_meta,
                new_sort_order,
                now,
                entry_id,
            ),
        )
        upsert_fts(
            conn,
            target_type=TargetType.ENTRY,
            target_id=entry_id,
            book_id=existing.book_id,
            title=new_title,
            body=new_content,
        )
    updated = get_entry(conn, entry_id)
    if updated is None:
        raise LookupError(f"Entry not found after update: {entry_id}")
    return updated


def delete_entry(conn: sqlite3.Connection, entry_id: str) -> bool:
    with ensure_transaction(conn):
        delete_fts(conn, target_type=TargetType.ENTRY, target_id=entry_id)
        cursor = conn.execute("DELETE FROM entries WHERE id = ?", (entry_id,))
    return cursor.rowcount > 0


def _escape_like(term: str) -> str:
    return term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _make_snippet(title: str, body: str, term: str, radius: int = 24) -> str:
    idx = body.find(term)
    if idx < 0:
        return title if not body else body[: radius * 2]
    start = max(0, idx - radius)
    end = min(len(body), idx + len(term) + radius)
    prefix = "..." if start > 0 else ""
    suffix = "..." if end < len(body) else ""
    return f"{prefix}{body[start:end]}{suffix}"


def search_book_content(
    conn: sqlite3.Connection,
    book_id: str,
    query: str,
    *,
    limit: int = 20,
) -> list[SearchHit]:
    """Search chapters and entries within one book via FTS5 trigram or short LIKE fallback."""
    term = query.strip()
    if not term or limit <= 0:
        return []

    if len(term) >= 3:
        fts_query = '"' + term.replace('"', '""') + '"'
        rows = conn.execute(
            """
            SELECT
                target_type,
                target_id,
                book_id,
                title,
                snippet(content_fts, 4, '[', ']', '...', 12) AS snippet
            FROM content_fts
            WHERE content_fts MATCH ? AND book_id = ?
            ORDER BY rank
            LIMIT ?
            """,
            (fts_query, book_id, limit),
        ).fetchall()
        return [
            SearchHit(
                target_type=TargetType(str(row["target_type"])),
                target_id=str(row["target_id"]),
                book_id=str(row["book_id"]),
                title=str(row["title"]),
                snippet=str(row["snippet"] or row["title"]),
            )
            for row in rows
        ]

    like_pattern = f"%{_escape_like(term)}%"
    rows = conn.execute(
        """
        SELECT 'chapter' AS target_type, id AS target_id, book_id, title, content AS body
        FROM chapters
        WHERE book_id = ? AND (title LIKE ? ESCAPE '\\' OR content LIKE ? ESCAPE '\\')
        UNION ALL
        SELECT 'entry' AS target_type, id AS target_id, book_id, title, content AS body
        FROM entries
        WHERE book_id = ? AND (title LIKE ? ESCAPE '\\' OR content LIKE ? ESCAPE '\\')
        LIMIT ?
        """,
        (book_id, like_pattern, like_pattern, book_id, like_pattern, like_pattern, limit),
    ).fetchall()
    return [
        SearchHit(
            target_type=TargetType(str(row["target_type"])),
            target_id=str(row["target_id"]),
            book_id=str(row["book_id"]),
            title=str(row["title"]),
            snippet=_make_snippet(str(row["title"]), str(row["body"]), term),
        )
        for row in rows
    ]
