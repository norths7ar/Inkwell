"""Review batches, reversible revisions, and cover file lifecycle via the API."""

import asyncio
import sqlite3
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import httpx
from pydantic import TypeAdapter

from backend.config import AppConfig, load_config
from backend.storage.db import Database
from backend.storage.models import PendingAction, PendingStatus, Revision, TargetType
from backend.storage.repos import changes
from tests.test_api import identifier, obj, workspace


@asynccontextmanager
async def content_workspace() -> AsyncIterator[tuple[httpx.AsyncClient, AppConfig]]:
    root = Path(__file__).resolve().parents[1] / ".tmp" / "20261126-tests"
    root.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(dir=root, prefix="20261126-m2-content-") as directory:
        settings = load_config(data_dir=directory)

        def reject_network(request: httpx.Request) -> httpx.Response:
            raise AssertionError(f"Unexpected model request: {request.url}")

        async with workspace(reject_network, data_dir=settings.data_dir) as (_, client, _):
            yield client, settings


async def history(client: httpx.AsyncClient, book_id: str, target_id: str) -> list[Revision]:
    response = await client.get(f"/api/books/{book_id}/revisions", params={"target_id": target_id})
    response.raise_for_status()
    return TypeAdapter(list[Revision]).validate_json(response.content)


def test_atomic_review_batches_and_conflicts() -> None:
    async def scenario() -> None:
        async with content_workspace() as (client, settings):
            book_id = identifier(await client.post("/api/books", json={"title": "Review"}))
            chapter_id = identifier(
                await client.post(
                    f"/api/books/{book_id}/chapters",
                    json={"title": "Chapter", "content": "original"},
                )
            )
            other_book = identifier(await client.post("/api/books", json={"title": "Other"}))
            db = Database(settings.db_path)
            with db.transaction() as conn:
                first = changes.create_pending_change(
                    conn,
                    book_id=book_id,
                    target_type=TargetType.CHAPTER,
                    target_id=chapter_id,
                    action=PendingAction.UPDATE,
                    before_content="original",
                    after_content="first version",
                )
                second = changes.create_pending_change(
                    conn,
                    book_id=book_id,
                    target_type=TargetType.CHAPTER,
                    target_id=chapter_id,
                    action=PendingAction.UPDATE,
                    before_content="different baseline",
                    after_content="second version",
                )
                foreign = changes.create_pending_change(
                    conn,
                    book_id=other_book,
                    target_type=TargetType.ENTRY,
                    action=PendingAction.CREATE,
                    after_content="foreign",
                )
            original_history = await history(client, book_id, chapter_id)
            failed = await client.post(
                f"/api/books/{book_id}/pending-changes/apply",
                json={"change_ids": [first.id, second.id]},
            )
            assert failed.status_code == 409
            assert '"change_id":"' + second.id + '"' in failed.text
            assert obj(await client.get(f"/api/chapters/{chapter_id}"))["content"] == "original"
            assert await history(client, book_id, chapter_id) == original_history
            for change_id in (first.id, second.id):
                assert (
                    obj(await client.get(f"/api/pending-changes/{change_id}"))["status"]
                    == "pending"
                )
            rejected = await client.post(
                f"/api/books/{book_id}/pending-changes/reject",
                json={"change_ids": [first.id, foreign.id]},
            )
            assert rejected.status_code == 409
            assert obj(await client.get(f"/api/pending-changes/{first.id}"))["status"] == "pending"
            with db.transaction() as conn:
                conn.execute(
                    "UPDATE pending_changes SET before_content=? WHERE id=?",
                    ("first version", second.id),
                )
            result = obj(
                await client.post(
                    f"/api/books/{book_id}/pending-changes/apply",
                    json={"change_ids": [first.id, second.id]},
                )
            )
            assert result["count"] == 2
            assert (
                obj(await client.get(f"/api/chapters/{chapter_id}"))["content"] == "second version"
            )
            assert len(await history(client, book_id, chapter_id)) == len(original_history) + 2
            assert (
                await client.post(
                    f"/api/books/{book_id}/pending-changes/apply", json={"change_ids": [first.id]}
                )
            ).status_code == 409
            with db.transaction() as conn:
                changes.create_pending_change(
                    conn,
                    book_id=book_id,
                    target_type=TargetType.ENTRY,
                    action=PendingAction.CREATE,
                    after_content="reject me",
                )
            assert (
                obj(await client.post(f"/api/books/{book_id}/pending-changes/reject", json={}))[
                    "count"
                ]
                == 1
            )
            with db.transaction() as conn:
                assert not changes.list_pending_changes(conn, book_id, status=PendingStatus.PENDING)
                untouched = changes.get_pending_change(conn, foreign.id)
                assert untouched is not None and untouched.status == PendingStatus.PENDING

    asyncio.run(scenario())


def test_revision_restore_metadata_fts_and_reversibility() -> None:
    async def scenario() -> None:
        async with content_workspace() as (client, _):
            book_id = identifier(await client.post("/api/books", json={"title": "History"}))
            volume_id = identifier(
                await client.post(f"/api/books/{book_id}/volumes", json={"title": "Volume"})
            )
            chapter_id = identifier(
                await client.post(
                    f"/api/books/{book_id}/chapters",
                    json={
                        "title": "Original chapter",
                        "content": "originalneedle",
                        "volume_id": volume_id,
                        "sort_order": 7,
                    },
                )
            )
            original = (await history(client, book_id, chapter_id))[0]
            await client.patch(
                f"/api/chapters/{chapter_id}",
                json={
                    "title": "Edited",
                    "content": "editedneedle",
                    "volume_id": None,
                    "sort_order": 9,
                },
            )
            edited = (await history(client, book_id, chapter_id))[0]
            restored = obj(
                await client.post(
                    f"/api/chapters/{chapter_id}/restore", json={"revision_id": original.id}
                )
            )
            assert (
                TypeAdapter(Revision).validate_python(restored["revision"]).source.value
                == "restore"
            )
            chapter = obj(await client.get(f"/api/chapters/{chapter_id}"))
            assert chapter["volume_id"] == volume_id and chapter["sort_order"] == 7
            assert chapter["title"] == original.title and chapter["content"] == original.content
            assert (
                chapter_id
                in (
                    await client.get(f"/api/books/{book_id}/search", params={"q": "originalneedle"})
                ).text
            )
            assert (
                chapter_id
                not in (
                    await client.get(f"/api/books/{book_id}/search", params={"q": "editedneedle"})
                ).text
            )
            assert (
                await client.post(
                    f"/api/chapters/{chapter_id}/restore", json={"revision_id": edited.id}
                )
            ).status_code == 200
            assert obj(await client.get(f"/api/chapters/{chapter_id}"))["content"] == "editedneedle"
            entry_id = identifier(
                await client.post(
                    f"/api/books/{book_id}/entries",
                    json={
                        "title": "Secret",
                        "kind": "world",
                        "category": "Folder",
                        "visibility": "global",
                        "content": "hidden lore",
                        "sort_order": 4,
                    },
                )
            )
            entry_revision = (await history(client, book_id, entry_id))[0]
            await client.patch(
                f"/api/entries/{entry_id}",
                json={"kind": "note", "category": "", "visibility": "local", "content": "public"},
            )
            assert (
                await client.post(
                    f"/api/entries/{entry_id}/restore", json={"revision_id": entry_revision.id}
                )
            ).status_code == 200
            entry = obj(await client.get(f"/api/entries/{entry_id}"))
            assert entry["kind"] == "world" and entry["category"] == "Folder"
            assert entry["visibility"] == "global" and entry["sort_order"] == 4
            assert (
                await client.post(
                    f"/api/entries/{entry_id}/restore", json={"revision_id": original.id}
                )
            ).status_code == 409
            await client.delete(f"/api/entries/{entry_id}")
            assert (
                await client.post(
                    f"/api/entries/{entry_id}/restore", json={"revision_id": entry_revision.id}
                )
            ).status_code == 200
            assert obj(await client.get(f"/api/entries/{entry_id}"))["content"] == "hidden lore"

    asyncio.run(scenario())


def test_cover_validation_replacement_failure_and_cleanup() -> None:
    async def scenario() -> None:
        async with content_workspace() as (client, settings):
            book_id = identifier(await client.post("/api/books", json={"title": "Cover"}))
            url = f"/api/books/{book_id}/cover"
            covers = settings.attachments_dir / "covers"
            assert (await client.get(url)).status_code == 404
            assert (
                await client.put(url, content=b"bad", headers={"Content-Type": "image/gif"})
            ).status_code == 415
            assert (
                await client.put(url, content=b"", headers={"Content-Type": "image/png"})
            ).status_code == 400
            assert (
                await client.put(
                    url, content=b"x" * (5 * 1024 * 1024 + 1), headers={"Content-Type": "image/png"}
                )
            ).status_code == 413
            assert not covers.exists()
            png = b"\x89PNG\r\n\x1a\npreview"
            first = obj(await client.put(url, content=png, headers={"Content-Type": "image/png"}))
            first_name = first["cover_path"]
            assert isinstance(first_name, str)
            assert (covers / first_name).read_bytes() == png
            response = await client.get(url)
            assert response.content == png and response.headers["content-type"] == "image/png"
            assert response.headers["cache-control"] == "no-cache"
            with patch(
                "backend.api.content.content.update_book",
                side_effect=sqlite3.IntegrityError("forced"),
            ):
                assert (
                    await client.put(
                        url, content=b"replacement", headers={"Content-Type": "image/png"}
                    )
                ).status_code == 409
            assert (await client.get(url)).content == png
            assert list(covers.iterdir()) == [covers / first_name]
            second = obj(
                await client.put(url, content=b"webp", headers={"Content-Type": "image/webp"})
            )
            second_name = second["cover_path"]
            assert isinstance(second_name, str) and second_name != first_name
            assert not (covers / first_name).exists()
            assert (await client.get(url)).headers["content-type"] == "image/webp"
            assert obj(await client.delete(url))["cover_path"] == ""
            assert not list(covers.iterdir()) and (await client.get(url)).status_code == 404
            assert (await client.delete(url)).status_code == 200
            limit = b"x" * (5 * 1024 * 1024)
            assert (
                await client.put(
                    url, content=limit, headers={"Content-Type": "image/jpeg; charset=binary"}
                )
            ).status_code == 200
            assert (await client.delete(f"/api/books/{book_id}")).status_code == 204
            assert not list(covers.iterdir()) and (await client.get(url)).status_code == 404
            assert (
                await client.put(url, content=png, headers={"Content-Type": "image/png"})
            ).status_code == 404

    asyncio.run(scenario())
