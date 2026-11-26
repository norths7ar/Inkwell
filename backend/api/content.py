"""REST access to the writing workspace and its review history."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query, Request, Response
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, Field, JsonValue, TypeAdapter

from backend.api.runtime import ApiRuntime
from backend.storage.ids import new_id
from backend.storage.migrations import ensure_transaction
from backend.storage.models import (
    Book,
    Chapter,
    Entry,
    EntryFolder,
    EntryKind,
    PendingAction,
    PendingChange,
    PendingStatus,
    Revision,
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


class EntryFolderInput(Input):
    kind: EntryKind = EntryKind.NOTE
    title: str = Field(default="未命名文件夹", min_length=1)
    description: str = ""
    sort_order: int = 0


class TrashRestoreInput(Input):
    target_type: TargetType
    target_id: str = Field(min_length=1)


class TrashPayload(BaseModel):
    chapters: list[Chapter]
    entries: list[Entry]


class BatchChangesInput(Input):
    change_ids: list[str] | None = Field(default=None, min_length=1, max_length=1000)


class BatchChangesResult(BaseModel):
    changes: list[PendingChange]
    count: int


class RestoreInput(Input):
    revision_id: str = Field(min_length=1)


class RestoreResult(BaseModel):
    target: Chapter | Entry
    revision: Revision


def supplied[T](body: BaseModel, name: str, value: T) -> T | Unset:
    return value if name in body.model_fields_set else UNSET


def required[T](value: T | None) -> T:
    if value is None:
        raise HTTPException(404, "Resource not found")
    return value


_COVER_MIME_TO_EXT: dict[str, str] = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
}
_COVER_EXT_TO_MIME: dict[str, str] = {ext: mime for mime, ext in _COVER_MIME_TO_EXT.items()}
_MAX_COVER_BYTES = 5 * 1024 * 1024


def create_router(runtime: ApiRuntime) -> APIRouter:
    router = APIRouter()
    covers_dir = runtime.config.attachments_dir / "covers"

    def remove_cover_file(cover_path: str) -> None:
        if not cover_path:
            return
        target = covers_dir / Path(cover_path).name
        if target.is_file():
            target.unlink()

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
        changes.record_content_revision(runtime.conn, item)

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
        existing = required(content.get_book(runtime.conn, book_id))
        runtime.ensure_book_idle(book_id)
        content.delete_book(runtime.conn, book_id)
        remove_cover_file(existing.cover_path)
        changed(book_id, "book", book_id)
        return Response(status_code=204)

    @router.get("/books/{book_id}/cover")
    async def get_book_cover(book_id: str) -> FileResponse:
        existing = required(content.get_book(runtime.conn, book_id))
        if not existing.cover_path:
            raise HTTPException(404, "Cover not found")
        target = covers_dir / Path(existing.cover_path).name
        if not target.is_file():
            raise HTTPException(404, "Cover not found")
        media_type = _COVER_EXT_TO_MIME.get(target.suffix.lower(), "application/octet-stream")
        return FileResponse(target, media_type=media_type, headers={"Cache-Control": "no-cache"})

    @router.put("/books/{book_id}/cover")
    async def upload_book_cover(book_id: str, request: Request) -> Book:
        existing = required(content.get_book(runtime.conn, book_id))
        media_type = request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
        ext = _COVER_MIME_TO_EXT.get(media_type)
        if ext is None:
            raise HTTPException(415, "Cover must be JPEG, PNG, or WebP")
        raw = bytearray()
        async for chunk in request.stream():
            if len(raw) + len(chunk) > _MAX_COVER_BYTES:
                raise HTTPException(413, "Cover file exceeds 5 MB")
            raw.extend(chunk)
        if not raw:
            raise HTTPException(400, "Cover file is empty")
        existing = required(content.get_book(runtime.conn, book_id))
        covers_dir.mkdir(parents=True, exist_ok=True)
        filename = f"{book_id}-{new_id()}{ext}"
        target = covers_dir / filename
        try:
            target.write_bytes(raw)
            item = content.update_book(runtime.conn, book_id, cover_path=filename)
        except BaseException:
            target.unlink(missing_ok=True)
            raise
        remove_cover_file(existing.cover_path)
        changed(book_id, "book", book_id)
        return item

    @router.delete("/books/{book_id}/cover")
    async def delete_book_cover(book_id: str) -> Book:
        existing = required(content.get_book(runtime.conn, book_id))
        item = content.update_book(runtime.conn, book_id, cover_path="")
        remove_cover_file(existing.cover_path)
        changed(book_id, "book", book_id)
        return item

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

    @router.get("/books/{book_id}/folders")
    async def entry_folders(book_id: str, kind: EntryKind | None = None) -> list[EntryFolder]:
        required(content.get_book(runtime.conn, book_id))
        return content.list_entry_folders(runtime.conn, book_id, kind=kind)

    @router.post("/books/{book_id}/folders", status_code=201)
    async def add_entry_folder(book_id: str, body: EntryFolderInput) -> EntryFolder:
        required(content.get_book(runtime.conn, book_id))
        if not body.title.strip():
            raise HTTPException(422, "Folder title cannot be blank")
        try:
            item = content.create_entry_folder(
                runtime.conn,
                book_id=book_id,
                kind=body.kind,
                title=body.title,
                description=body.description,
                sort_order=body.sort_order,
            )
        except sqlite3.IntegrityError as exc:
            raise HTTPException(409, "Folder with this name already exists") from exc
        changed(book_id, "folder", item.id)
        return item

    @router.patch("/folders/{folder_id}")
    async def edit_entry_folder(folder_id: str, body: EntryFolderInput) -> EntryFolder:
        required(content.get_entry_folder(runtime.conn, folder_id))
        if "title" in body.model_fields_set and not body.title.strip():
            raise HTTPException(422, "Folder title cannot be blank")
        try:
            item = content.update_entry_folder(
                runtime.conn,
                folder_id,
                title=supplied(body, "title", body.title),
                description=supplied(body, "description", body.description),
                sort_order=supplied(body, "sort_order", body.sort_order),
            )
        except sqlite3.IntegrityError as exc:
            raise HTTPException(409, "Folder with this name already exists") from exc
        changed(item.book_id, "folder", item.id)
        return item

    @router.delete("/folders/{folder_id}", status_code=204)
    async def remove_entry_folder(folder_id: str) -> Response:
        existing = required(content.get_entry_folder(runtime.conn, folder_id))
        content.delete_entry_folder(runtime.conn, folder_id)
        changed(existing.book_id, "folder", folder_id)
        return Response(status_code=204)

    @router.get("/books/{book_id}/trash")
    async def trash(book_id: str) -> TrashPayload:
        required(content.get_book(runtime.conn, book_id))
        return TrashPayload(
            chapters=content.list_deleted_chapters(runtime.conn, book_id),
            entries=content.list_deleted_entries(runtime.conn, book_id),
        )

    @router.post("/books/{book_id}/trash/restore")
    async def restore_from_trash(book_id: str, body: TrashRestoreInput) -> Chapter | Entry:
        required(content.get_book(runtime.conn, book_id))
        if body.target_type == TargetType.CHAPTER:
            existing = required(
                content.get_chapter(runtime.conn, body.target_id, include_deleted=True)
            )
            if existing.book_id != book_id or existing.deleted_at is None:
                raise HTTPException(404, "Deleted chapter not found")
            restored: Chapter | Entry = content.restore_chapter(runtime.conn, body.target_id)
        else:
            existing_entry = required(
                content.get_entry(runtime.conn, body.target_id, include_deleted=True)
            )
            if existing_entry.book_id != book_id or existing_entry.deleted_at is None:
                raise HTTPException(404, "Deleted entry not found")
            restored = content.restore_entry(runtime.conn, body.target_id)
        changed(book_id, body.target_type.value, body.target_id)
        return restored

    @router.delete("/books/{book_id}/trash/{target_type}/{target_id}", status_code=204)
    async def purge_trash_item(book_id: str, target_type: TargetType, target_id: str) -> Response:
        required(content.get_book(runtime.conn, book_id))
        if target_type == TargetType.CHAPTER:
            existing = required(content.get_chapter(runtime.conn, target_id, include_deleted=True))
            if existing.book_id != book_id or existing.deleted_at is None:
                raise HTTPException(404, "Deleted chapter not found")
            content.delete_chapter(runtime.conn, target_id, permanent=True)
        else:
            existing_entry = required(
                content.get_entry(runtime.conn, target_id, include_deleted=True)
            )
            if existing_entry.book_id != book_id or existing_entry.deleted_at is None:
                raise HTTPException(404, "Deleted entry not found")
            content.delete_entry(runtime.conn, target_id, permanent=True)
        changed(book_id, "trash", target_id)
        return Response(status_code=204)

    @router.delete("/books/{book_id}/trash", status_code=204)
    async def purge_trash(book_id: str) -> Response:
        required(content.get_book(runtime.conn, book_id))
        content.empty_trash(runtime.conn, book_id)
        changed(book_id, "trash", book_id)
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

    def check_pending(pending: PendingChange) -> None:
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

    def publish_pending(item: PendingChange) -> None:
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

    def batch_changes(book_id: str, body: BatchChangesInput, *, apply: bool) -> BatchChangesResult:
        required(content.get_book(runtime.conn, book_id))
        ids = body.change_ids
        if ids is None:
            ids = [
                item.id
                for item in reversed(
                    changes.list_pending_changes(
                        runtime.conn, book_id, status=PendingStatus.PENDING
                    )
                )
            ]
        if len(ids) != len(set(ids)):
            raise HTTPException(422, "Duplicate pending change IDs")
        updated: list[PendingChange] = []
        with ensure_transaction(runtime.conn):
            for change_id in ids:
                try:
                    pending = required(changes.get_pending_change(runtime.conn, change_id))
                    if pending.book_id != book_id:
                        raise HTTPException(409, "Pending change belongs to another book")
                    if apply:
                        check_pending(pending)
                        updated.append(changes.apply_pending_change(runtime.conn, change_id))
                    else:
                        updated.append(changes.reject_pending_change(runtime.conn, change_id))
                except (HTTPException, LookupError, ValueError) as exc:
                    raise HTTPException(
                        409,
                        detail={
                            "code": "batch_conflict",
                            "change_id": change_id,
                            "atomic": True,
                            "message": "No changes were committed; refresh the pending change and its target",
                        },
                    ) from exc
        for item in updated:
            publish_pending(item)
        return BatchChangesResult(changes=updated, count=len(updated))

    @router.post("/books/{book_id}/pending-changes/apply")
    async def apply_batch(book_id: str, body: BatchChangesInput) -> BatchChangesResult:
        return batch_changes(book_id, body, apply=True)

    @router.post("/books/{book_id}/pending-changes/reject")
    async def reject_batch(book_id: str, body: BatchChangesInput) -> BatchChangesResult:
        return batch_changes(book_id, body, apply=False)

    @router.post("/pending-changes/{change_id}/apply")
    async def apply(change_id: str) -> PendingChange:
        with ensure_transaction(runtime.conn):
            pending = required(changes.get_pending_change(runtime.conn, change_id))
            check_pending(pending)
            item = changes.apply_pending_change(runtime.conn, change_id)
        publish_pending(item)
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

    def restore(target_type: TargetType, target_id: str, body: RestoreInput) -> RestoreResult:
        original = required(changes.get_revision(runtime.conn, body.revision_id))
        required(content.get_book(runtime.conn, original.book_id))
        item, saved = changes.restore_revision(
            runtime.conn, body.revision_id, target_type=target_type, target_id=target_id
        )
        changed(item.book_id, target_type.value, item.id)
        return RestoreResult(target=item, revision=saved)

    @router.post("/chapters/{chapter_id}/restore")
    async def restore_chapter(chapter_id: str, body: RestoreInput) -> RestoreResult:
        return restore(TargetType.CHAPTER, chapter_id, body)

    @router.post("/entries/{entry_id}/restore")
    async def restore_entry(entry_id: str, body: RestoreInput) -> RestoreResult:
        return restore(TargetType.ENTRY, entry_id, body)

    return router
