"""REST access to the writing workspace and its review history."""

from __future__ import annotations

import json

from fastapi import APIRouter, HTTPException, Query, Response
from pydantic import BaseModel, ConfigDict, Field, JsonValue, TypeAdapter

from backend.api.runtime import ApiRuntime
from backend.storage.migrations import ensure_transaction
from backend.storage.models import (
    Book,
    Chapter,
    Entry,
    EntryKind,
    PendingAction,
    PendingChange,
    PendingStatus,
    Revision,
    RevisionSource,
    SearchHit,
    TargetType,
    Visibility,
    Volume,
)
from backend.storage.repos import changes, content
from backend.storage.repos.content import UNSET, Unset


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid")


class BookInput(Input):
    title: str = Field(default="未命名作品", min_length=1)
    description: str = ""
    rules_markdown: str = ""
    default_visibility: Visibility = Visibility.LOCAL


class VolumeInput(Input):
    title: str = Field(default="未命名分卷", min_length=1)
    description: str = ""
    sort_order: int = 0


class ChapterInput(Input):
    title: str = Field(default="未命名章节", min_length=1)
    content: str = ""
    summary: str = ""
    volume_id: str | None = None
    sort_order: int = 0


class EntryInput(Input):
    title: str = Field(default="未命名设定", min_length=1)
    kind: EntryKind = EntryKind.NOTE
    category: str = ""
    content: str = ""
    visibility: Visibility = Visibility.LOCAL
    meta_json: str = "{}"
    sort_order: int = 0


def supplied[T](body: BaseModel, name: str, value: T) -> T | Unset:
    return value if name in body.model_fields_set else UNSET


def required[T](value: T | None) -> T:
    if value is None:
        raise HTTPException(404, "Resource not found")
    return value


def create_router(runtime: ApiRuntime) -> APIRouter:
    router = APIRouter()

    def changed(book_id: str, resource: str, resource_id: str) -> None:
        runtime.events.publish(
            "content_changed",
            {
                "book_id": book_id,
                "resource": resource,
                "resource_id": resource_id,
            },
        )

    def check_volume(book_id: str, volume_id: str | None) -> None:
        if (
            volume_id is not None
            and required(content.get_volume(runtime.conn, volume_id)).book_id != book_id
        ):
            raise HTTPException(409, "Volume belongs to another book")

    def revision(item: Chapter | Entry) -> None:
        snapshot = (
            {"volume_id": item.volume_id, "summary": item.summary, "sort_order": item.sort_order}
            if isinstance(item, Chapter)
            else {
                "kind": item.kind,
                "category": item.category,
                "visibility": item.visibility,
                "meta_json": item.meta_json,
                "sort_order": item.sort_order,
            }
        )
        changes.create_revision(
            runtime.conn,
            book_id=item.book_id,
            target_type=(TargetType.CHAPTER if isinstance(item, Chapter) else TargetType.ENTRY),
            target_id=item.id,
            source=RevisionSource.USER,
            title=item.title,
            content=item.content,
            snapshot_json=json.dumps(snapshot),
        )

    @router.get("/books")
    async def books() -> list[Book]:
        return content.list_books(runtime.conn)

    @router.post("/books", status_code=201)
    async def add_book(body: BookInput) -> Book:
        item = content.create_book(
            runtime.conn,
            title=body.title,
            description=body.description,
            rules_markdown=body.rules_markdown,
            default_visibility=body.default_visibility,
        )
        changed(item.id, "book", item.id)
        return item

    @router.get("/books/{book_id}")
    async def book(book_id: str) -> Book:
        return required(content.get_book(runtime.conn, book_id))

    @router.patch("/books/{book_id}")
    async def edit_book(book_id: str, body: BookInput) -> Book:
        item = content.update_book(
            runtime.conn,
            book_id,
            title=supplied(body, "title", body.title),
            description=supplied(body, "description", body.description),
            rules_markdown=supplied(body, "rules_markdown", body.rules_markdown),
            default_visibility=supplied(body, "default_visibility", body.default_visibility),
        )
        changed(book_id, "book", book_id)
        return item

    @router.delete("/books/{book_id}", status_code=204)
    async def remove_book(book_id: str) -> Response:
        required(content.get_book(runtime.conn, book_id))
        runtime.ensure_book_idle(book_id)
        content.delete_book(runtime.conn, book_id)
        changed(book_id, "book", book_id)
        return Response(status_code=204)

    @router.get("/books/{book_id}/volumes")
    async def volumes(book_id: str) -> list[Volume]:
        required(content.get_book(runtime.conn, book_id))
        return content.list_volumes(runtime.conn, book_id)

    @router.post("/books/{book_id}/volumes", status_code=201)
    async def add_volume(book_id: str, body: VolumeInput) -> Volume:
        required(content.get_book(runtime.conn, book_id))
        item = content.create_volume(
            runtime.conn,
            book_id=book_id,
            title=body.title,
            description=body.description,
            sort_order=body.sort_order,
        )
        changed(book_id, "volume", item.id)
        return item

    @router.get("/volumes/{volume_id}")
    async def volume(volume_id: str) -> Volume:
        return required(content.get_volume(runtime.conn, volume_id))

    @router.patch("/volumes/{volume_id}")
    async def edit_volume(volume_id: str, body: VolumeInput) -> Volume:
        item = content.update_volume(
            runtime.conn,
            volume_id,
            title=supplied(body, "title", body.title),
            description=supplied(body, "description", body.description),
            sort_order=supplied(body, "sort_order", body.sort_order),
        )
        changed(item.book_id, "volume", item.id)
        return item

    @router.delete("/volumes/{volume_id}", status_code=204)
    async def remove_volume(volume_id: str) -> Response:
        item = required(content.get_volume(runtime.conn, volume_id))
        content.delete_volume(runtime.conn, volume_id)
        changed(item.book_id, "volume", item.id)
        return Response(status_code=204)

    @router.get("/books/{book_id}/chapters")
    async def chapters(book_id: str, volume_id: str | None = None) -> list[Chapter]:
        required(content.get_book(runtime.conn, book_id))
        return content.list_chapters(runtime.conn, book_id, volume_id=volume_id)

    @router.post("/books/{book_id}/chapters", status_code=201)
    async def add_chapter(book_id: str, body: ChapterInput) -> Chapter:
        required(content.get_book(runtime.conn, book_id))
        check_volume(book_id, body.volume_id)
        with ensure_transaction(runtime.conn):
            item = content.create_chapter(
                runtime.conn,
                book_id=book_id,
                title=body.title,
                content=body.content,
                summary=body.summary,
                volume_id=body.volume_id,
                sort_order=body.sort_order,
            )
            revision(item)
        changed(book_id, "chapter", item.id)
        return item

    @router.get("/chapters/{chapter_id}")
    async def chapter(chapter_id: str) -> Chapter:
        return required(content.get_chapter(runtime.conn, chapter_id))

    @router.patch("/chapters/{chapter_id}")
    async def edit_chapter(chapter_id: str, body: ChapterInput) -> Chapter:
        existing = required(content.get_chapter(runtime.conn, chapter_id))
        if "volume_id" in body.model_fields_set:
            check_volume(existing.book_id, body.volume_id)
        with ensure_transaction(runtime.conn):
            item = content.update_chapter(
                runtime.conn,
                chapter_id,
                title=supplied(body, "title", body.title),
                content=supplied(body, "content", body.content),
                summary=supplied(body, "summary", body.summary),
                volume_id=supplied(body, "volume_id", body.volume_id),
                sort_order=supplied(body, "sort_order", body.sort_order),
            )
            revision(item)
        changed(item.book_id, "chapter", item.id)
        return item

    @router.delete("/chapters/{chapter_id}", status_code=204)
    async def remove_chapter(chapter_id: str) -> Response:
        item = required(content.get_chapter(runtime.conn, chapter_id))
        with ensure_transaction(runtime.conn):
            revision(item)
            content.delete_chapter(runtime.conn, chapter_id)
        changed(item.book_id, "chapter", item.id)
        return Response(status_code=204)

    @router.get("/books/{book_id}/entries")
    async def entries(
        book_id: str, kind: EntryKind | None = None, visibility: Visibility | None = None
    ) -> list[Entry]:
        required(content.get_book(runtime.conn, book_id))
        return content.list_entries(runtime.conn, book_id, kind=kind, visibility=visibility)

    @router.post("/books/{book_id}/entries", status_code=201)
    async def add_entry(book_id: str, body: EntryInput) -> Entry:
        required(content.get_book(runtime.conn, book_id))
        with ensure_transaction(runtime.conn):
            item = content.create_entry(
                runtime.conn,
                book_id=book_id,
                title=body.title,
                kind=body.kind,
                category=body.category,
                content=body.content,
                visibility=body.visibility,
                meta_json=body.meta_json,
                sort_order=body.sort_order,
            )
            revision(item)
        changed(book_id, "entry", item.id)
        return item

    @router.get("/entries/{entry_id}")
    async def entry(entry_id: str) -> Entry:
        return required(content.get_entry(runtime.conn, entry_id))

    @router.patch("/entries/{entry_id}")
    async def edit_entry(entry_id: str, body: EntryInput) -> Entry:
        with ensure_transaction(runtime.conn):
            item = content.update_entry(
                runtime.conn,
                entry_id,
                title=supplied(body, "title", body.title),
                kind=supplied(body, "kind", body.kind),
                category=supplied(body, "category", body.category),
                content=supplied(body, "content", body.content),
                visibility=supplied(body, "visibility", body.visibility),
                meta_json=supplied(body, "meta_json", body.meta_json),
                sort_order=supplied(body, "sort_order", body.sort_order),
            )
            revision(item)
        changed(item.book_id, "entry", item.id)
        return item

    @router.delete("/entries/{entry_id}", status_code=204)
    async def remove_entry(entry_id: str) -> Response:
        item = required(content.get_entry(runtime.conn, entry_id))
        with ensure_transaction(runtime.conn):
            revision(item)
            content.delete_entry(runtime.conn, entry_id)
        changed(item.book_id, "entry", item.id)
        return Response(status_code=204)

    @router.get("/books/{book_id}/search")
    async def search(
        book_id: str, q: str, limit: int = Query(default=20, ge=1, le=200)
    ) -> list[SearchHit]:
        required(content.get_book(runtime.conn, book_id))
        return content.search_book_content(runtime.conn, book_id, q, limit=limit)

    @router.get("/books/{book_id}/pending-changes")
    async def pending(book_id: str, status: PendingStatus | None = None) -> list[PendingChange]:
        required(content.get_book(runtime.conn, book_id))
        return changes.list_pending_changes(runtime.conn, book_id, status=status)

    @router.get("/pending-changes/{change_id}")
    async def pending_change(change_id: str) -> PendingChange:
        return required(changes.get_pending_change(runtime.conn, change_id))

    @router.post("/pending-changes/{change_id}/apply")
    async def apply(change_id: str) -> PendingChange:
        with ensure_transaction(runtime.conn):
            pending = required(changes.get_pending_change(runtime.conn, change_id))
            if pending.action != PendingAction.CREATE:
                target = required(
                    content.get_chapter(runtime.conn, pending.target_id or "")
                    if pending.target_type == TargetType.CHAPTER
                    else content.get_entry(runtime.conn, pending.target_id or "")
                )
                if target.book_id != pending.book_id or target.content != pending.before_content:
                    raise HTTPException(409, "Pending change no longer matches current content")
            if pending.target_type == TargetType.CHAPTER:
                payload = TypeAdapter(dict[str, JsonValue]).validate_json(pending.payload_json)
                volume_id = payload.get("volume_id")
                if isinstance(volume_id, str):
                    check_volume(pending.book_id, volume_id)
            item = changes.apply_pending_change(runtime.conn, change_id)
        runtime.events.publish(
            "content_changed",
            {
                "book_id": item.book_id,
                "session_id": item.session_id,
                "run_id": item.run_id,
                "resource": "pending_change",
                "resource_id": item.id,
                "target_id": item.target_id,
            },
        )
        return item

    @router.post("/pending-changes/{change_id}/reject")
    async def reject(change_id: str) -> PendingChange:
        item = changes.reject_pending_change(runtime.conn, change_id)
        runtime.events.publish(
            "content_changed",
            {
                "book_id": item.book_id,
                "session_id": item.session_id,
                "run_id": item.run_id,
                "resource": "pending_change",
                "resource_id": item.id,
            },
        )
        return item

    @router.get("/books/{book_id}/revisions")
    async def revisions(
        book_id: str, target_type: TargetType | None = None, target_id: str | None = None
    ) -> list[Revision]:
        required(content.get_book(runtime.conn, book_id))
        return changes.list_revisions(
            runtime.conn, book_id, target_type=target_type, target_id=target_id
        )

    @router.get("/revisions/{revision_id}")
    async def get_revision(revision_id: str) -> Revision:
        return required(changes.get_revision(runtime.conn, revision_id))

    return router
