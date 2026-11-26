"""Book archive round trips and online SQLite snapshots, without external I/O."""

import asyncio
import io
import sqlite3
import zipfile
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

import httpx
from pydantic import TypeAdapter

from backend import archive
from backend.api.transfers import BackupRecord, SystemStatus
from backend.storage.models import Book, Chapter, Entry, Volume
from tests.test_api import identifier, obj, workspace


def no_upstream(_request: httpx.Request) -> httpx.Response:
    raise AssertionError("Transfers must not contact a model provider")


def test_book_archive_round_trip_preserves_structure_visibility_and_cover() -> None:
    async def scenario() -> None:
        async with workspace(no_upstream) as (_, client, _):
            book_id = identifier(
                await client.post(
                    "/api/books",
                    json={
                        "title": "书名 / 测试",
                        "description": "简介",
                        "rules_markdown": "规则\n第二行",
                        "default_visibility": "global",
                    },
                )
            )
            volume_id = identifier(
                await client.post(
                    f"/api/books/{book_id}/volumes",
                    json={"title": "第一卷", "description": "卷说明", "sort_order": 3},
                )
            )
            for volume, text in [(volume_id, "卷内正文\n\n保留空行"), (None, "未分卷正文")]:
                await client.post(
                    f"/api/books/{book_id}/chapters",
                    json={
                        "title": "同名章节",
                        "content": text,
                        "volume_id": volume,
                        "sort_order": 5,
                    },
                )
            for kind, visibility in [
                ("character", "local"),
                ("world", "global"),
                ("note", "local"),
            ]:
                await client.post(
                    f"/api/books/{book_id}/entries",
                    json={
                        "title": "同名条目",
                        "kind": kind,
                        "visibility": visibility,
                        "category": "资料/夹\\旧" if kind == "note" else "资料夹",
                        "content": f"{kind}内容",
                        "sort_order": 7,
                        "meta_json": '{"tags":["重要"],"custom":"preserved"}',
                    },
                )
            cover = b"\x89PNG\r\n\x1a\npreview-cover"
            upload = await client.put(
                f"/api/books/{book_id}/cover", content=cover, headers={"content-type": "image/png"}
            )
            assert upload.status_code == 200
            exported = await client.get(f"/api/books/{book_id}/export")
            assert exported.status_code == 200
            assert exported.headers["content-type"] == "application/zip"
            assert "filename*=UTF-8''" in exported.headers["content-disposition"]
            with zipfile.ZipFile(io.BytesIO(exported.content)) as package:
                names = package.namelist()
                assert any(name.startswith("正文/0001-第一卷/") for name in names)
                assert any(name.startswith("正文/未分卷/") for name in names)
                for kind in ("人物", "世界"):
                    assert any(name.startswith(f"设定/{kind}/资料夹/") for name in names)
                assert any(name.startswith("设定/笔记/资料_夹_旧/") for name in names)
                manifest = archive.Manifest.model_validate_json(package.read("manifest.json"))
                assert manifest.version == 1
                assert manifest.cover is not None
                assert package.read(manifest.cover) == cover

            imported = await client.post(
                "/api/books/import",
                content=exported.content,
                headers={"content-type": "application/zip"},
            )
            assert imported.status_code == 201
            copied = TypeAdapter(Book).validate_json(imported.content)
            original = TypeAdapter(Book).validate_json(
                (await client.get(f"/api/books/{book_id}")).content
            )
            assert copied.id != original.id
            assert copied.title == original.title
            assert copied.description == original.description
            assert copied.rules_markdown == original.rules_markdown
            assert copied.default_visibility == original.default_visibility
            assert copied.created_at == original.created_at
            assert copied.updated_at == original.updated_at
            assert copied.cover_path != original.cover_path
            assert (await client.get(f"/api/books/{copied.id}/cover")).content == cover
            volumes = TypeAdapter(list[Volume]).validate_json(
                (await client.get(f"/api/books/{copied.id}/volumes")).content
            )
            assert [(item.title, item.description, item.sort_order) for item in volumes] == [
                ("第一卷", "卷说明", 3)
            ]
            assert volumes[0].id != volume_id
            chapters = TypeAdapter(list[Chapter]).validate_json(
                (await client.get(f"/api/books/{copied.id}/chapters")).content
            )
            assert len(chapters) == 2
            assert {item.content for item in chapters} == {"卷内正文\n\n保留空行", "未分卷正文"}
            assert {item.volume_id for item in chapters} == {volumes[0].id, None}
            entries = TypeAdapter(list[Entry]).validate_json(
                (await client.get(f"/api/books/{copied.id}/entries")).content
            )
            assert len(entries) == 3
            assert {item.kind.value: item.visibility.value for item in entries} == {
                "character": "local",
                "world": "global",
                "note": "local",
            }
            assert all(
                item.category == ("资料/夹\\旧" if item.kind.value == "note" else "资料夹")
                and '"custom":"preserved"' in item.meta_json
                for item in entries
            )
            assert all(item.sort_order == 7 for item in entries)
            search = await client.get(f"/api/books/{copied.id}/search", params={"q": "卷内正文"})
            assert len(TypeAdapter(list[dict[str, str]]).validate_json(search.content)) == 1
            for format in ("txt", "md"):
                response = await client.get(
                    f"/api/books/{book_id}/export", params={"format": format}
                )
                assert response.status_code == 200
                assert response.text.index("卷内正文") < response.text.index("未分卷正文")
                assert "world内容" not in response.text
            assert (await client.get(f"/api/books/{book_id}/export?format=pdf")).status_code == 422

    asyncio.run(scenario())


def test_rejected_archives_are_atomic_and_snapshot_contains_committed_wal_data() -> None:
    async def scenario() -> None:
        async with workspace(no_upstream) as (_, client, _):
            book_id = identifier(await client.post("/api/books", json={"title": "Snapshot book"}))
            cover = await client.put(
                f"/api/books/{book_id}/cover",
                content=b"\x89PNG\r\n\x1a\nsnapshot-cover",
                headers={"content-type": "image/png"},
            )
            assert cover.status_code == 200
            exported = await client.get(f"/api/books/{book_id}/export")
            exported.raise_for_status()

            def rewritten(
                *, unsafe: bool = False, duplicate: bool = False, missing: bool = False
            ) -> bytes:
                target = io.BytesIO()
                with (
                    zipfile.ZipFile(io.BytesIO(exported.content)) as original,
                    zipfile.ZipFile(target, "w") as altered,
                ):
                    manifest = archive.Manifest.model_validate_json(original.read("manifest.json"))
                    if missing:
                        manifest.chapters.append(
                            archive.ArchiveChapter(
                                title="missing",
                                created_at="",
                                updated_at="",
                                path="正文/missing.md",
                                volume_key=None,
                                sort_order=0,
                            )
                        )
                    altered.writestr("manifest.json", manifest.model_dump_json())
                    if unsafe:
                        altered.writestr("../escaped.md", "unsafe")
                    if duplicate:
                        altered.writestr("MANIFEST.JSON", "{}")
                return target.getvalue()

            for raw in (
                b"not a zip",
                rewritten(unsafe=True),
                rewritten(duplicate=True),
                rewritten(missing=True),
            ):
                response = await client.post(
                    "/api/books/import", content=raw, headers={"content-type": "application/zip"}
                )
                assert response.status_code == 400
            with patch("backend.archive.MAX_EXPANDED_BYTES", 1):
                rejected = await client.post(
                    "/api/books/import",
                    content=exported.content,
                    headers={"content-type": "application/zip"},
                )
                assert rejected.status_code == 400
            with patch("backend.archive.MAX_ARCHIVE_BYTES", 1):
                rejected = await client.post(
                    "/api/books/import", content=b"12", headers={"content-type": "application/zip"}
                )
                assert rejected.status_code == 413
            assert (await client.post("/api/books/import", content=b"x")).status_code == 415
            assert (
                len(TypeAdapter(list[Book]).validate_json((await client.get("/api/books")).content))
                == 1
            )

            # Fail after import has begun writing; book creation must still roll back.
            with patch("backend.archive._restore_times", side_effect=ValueError("forced rollback")):
                failed = await client.post(
                    "/api/books/import",
                    content=exported.content,
                    headers={"content-type": "application/zip"},
                )
                assert failed.status_code == 400
            assert (
                len(TypeAdapter(list[Book]).validate_json((await client.get("/api/books")).content))
                == 1
            )
            snapshot = await client.post("/api/backups")
            assert snapshot.status_code == 201
            record = TypeAdapter(BackupRecord).validate_json(snapshot.content)
            assert record.size_bytes > 0
            assert not record.includes_attachments and not record.includes_encryption_key
            status = TypeAdapter(SystemStatus).validate_json(
                (await client.get("/api/system/status")).content
            )
            assert status.books == 1 and status.running_runs == 0 and status.waiting_runs == 0
            assert status.database_bytes > 0 and status.backups == [record]
            assert len(list((Path(status.data_dir) / "attachments" / "covers").iterdir())) == 1
            assert Path(status.backups_dir).parent == Path(status.data_dir)
            with closing(sqlite3.connect(Path(status.backups_dir) / record.name)) as backup_conn:
                assert backup_conn.execute("PRAGMA integrity_check").fetchone() == ("ok",)
                assert backup_conn.execute("SELECT title FROM books").fetchone() == (
                    "Snapshot book",
                )
                assert backup_conn.execute("PRAGMA user_version").fetchone() == (
                    status.schema_version,
                )
            assert not list(Path(status.backups_dir).glob("*.partial"))
            listed = TypeAdapter(list[BackupRecord]).validate_json(
                (await client.get("/api/backups")).content
            )
            assert listed == [record]
            assert "secret" not in obj(await client.get("/api/system/status"))

    asyncio.run(scenario())
