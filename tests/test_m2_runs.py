"""Run references, reasoning telemetry and v3 upgrade through real ASGI routes."""

import asyncio
import json
import sqlite3
from collections.abc import AsyncIterator
from contextlib import closing
from pathlib import Path
from tempfile import TemporaryDirectory

import httpx
from pydantic import JsonValue, TypeAdapter

from backend.storage.migrations import MIGRATIONS, SCHEMA_VERSION
from tests.test_api import (
    configure,
    identifier,
    next_event,
    obj,
    sse,
    terminal,
    tool_call,
    workspace,
)


class ReasoningStream(httpx.AsyncByteStream):
    async def __aiter__(self) -> AsyncIterator[bytes]:
        yield b'data: {"choices":[{"delta":{"reasoning_content":"consider"}}]}\n\n'
        await asyncio.sleep(0.025)
        yield b'data: {"choices":[{"delta":{"content":"answer"},"finish_reason":"stop"}]}\n\ndata: [DONE]\n\n'


def test_references_reasoning_timing_and_waiting_answer() -> None:
    async def scenario() -> None:
        requests: list[dict[str, JsonValue]] = []

        def upstream(request: httpx.Request) -> httpx.Response:
            requests.append(TypeAdapter(dict[str, JsonValue]).validate_json(request.content))
            if len(requests) == 1:
                return tool_call("ask_user", {"question": "Continue?", "options": ["Yes"]})
            return httpx.Response(200, stream=ReasoningStream())

        async with workspace(upstream) as (app, client, _):
            book, model, session = await configure(client)
            chapter = identifier(
                await client.post(
                    f"/api/books/{book}/chapters",
                    json={
                        "title": "Referenced chapter",
                        "content": "chapter-reference-text",
                    },
                )
            )
            entry = identifier(
                await client.post(
                    f"/api/books/{book}/entries",
                    json={
                        "title": "Hidden",
                        "kind": "note",
                        "visibility": "global",
                        "content": "hidden-secret",
                    },
                )
            )
            other_book = identifier(await client.post("/api/books", json={"title": "Other"}))
            other_chapter = identifier(
                await client.post(f"/api/books/{other_book}/chapters", json={"title": "Other"})
            )
            for target_type, target_id, status in (
                ("entry", entry, 403),
                ("chapter", other_chapter, 404),
            ):
                denied = await client.post(
                    f"/api/sessions/{session}/runs",
                    json={
                        "text": "Read",
                        "model_id": model,
                        "references": [{"target_type": target_type, "target_id": target_id}],
                    },
                )
                assert denied.status_code == status
                assert "hidden-secret" not in denied.text
            assert not requests
            assert (await client.get(f"/api/sessions/{session}/messages")).json() == []
            run = identifier(
                await client.post(
                    f"/api/sessions/{session}/runs",
                    json={
                        "text": "Read this",
                        "model_id": model,
                        "reasoning_effort": "high",
                        "references": [{"target_type": "chapter", "target_id": chapter}],
                    },
                )
            )
            await terminal(client, run, "waiting_user")
            assert requests[0]["reasoning_effort"] == "high"
            assert "chapter-reference-text" in json.dumps(requests[0])
            assert "hidden-secret" not in json.dumps(requests[0])
            async with sse(app) as stream:
                resumed = identifier(
                    await client.post(f"/api/runs/{run}/answer", json={"text": "Yes"})
                )
                completed = await terminal(client, resumed, "completed")
                duration = completed["reasoning_duration_ms"]
                assert isinstance(duration, int) and duration >= 15
                assert completed["reasoning_effort"] == "high"
                assert "chapter-reference-text" in json.dumps(requests[1])
                assert requests[1]["reasoning_effort"] == "high"
                saw_timing = False
                while True:
                    _, name, event = await next_event(stream)
                    if name == "reasoning_completed":
                        saw_timing = True
                        assert event["run_id"] == resumed
                        assert event["book_id"] == book and event["session_id"] == session
                        assert event["duration_ms"] == duration
                    if name == "run_done":
                        assert event["reasoning_duration_ms"] == duration
                        break
                assert saw_timing
            messages = (await client.get(f"/api/sessions/{session}/messages")).text
            assert "duration_ms" in messages
            await client.patch(f"/api/sessions/{session}", json={"visibility_scope": "global"})
            allowed = identifier(
                await client.post(
                    f"/api/sessions/{session}/runs",
                    json={
                        "text": "Read hidden",
                        "model_id": model,
                        "reasoning_effort": "low",
                        "references": [{"target_type": "entry", "target_id": entry}],
                    },
                )
            )
            await terminal(client, allowed, "completed")
            assert "hidden-secret" in json.dumps(requests[-1])
            assert (
                await client.patch(f"/api/sessions/{session}", json={"visibility_scope": "local"})
            ).status_code == 409

    asyncio.run(scenario())


def test_v3_upgrade_preserves_history_and_defaults_new_fields() -> None:
    async def scenario() -> None:
        root = Path(__file__).resolve().parents[1] / ".tmp" / "20261126-tests"
        root.mkdir(parents=True, exist_ok=True)
        with TemporaryDirectory(dir=root, prefix="20261126-m2-upgrade-") as directory:
            path = Path(directory) / "inkwell.sqlite3"
            with closing(sqlite3.connect(path)) as conn, conn:
                for version, statements in MIGRATIONS:
                    if version <= 3:
                        for statement in statements:
                            conn.execute(statement)
                conn.execute("PRAGMA user_version=3")
                conn.execute(
                    "INSERT INTO books(id,title,created_at,updated_at,cover_path) VALUES('b','Old','t','t','b.png')"
                )
                conn.execute(
                    "INSERT INTO sessions(id,book_id,created_at,updated_at) VALUES('s','b','t','t')"
                )
                conn.execute(
                    "INSERT INTO runs(id,session_id,book_id,status,started_at) VALUES('r','s','b','completed','t')"
                )
                conn.execute(
                    "INSERT INTO messages(id,session_id,book_id,run_id,seq,role,blocks_json,created_at) VALUES('m','s','b','r',1,'assistant',?, 't')",
                    (json.dumps([{"type": "reasoning", "text": "old reasoning"}]),),
                )
            async with workspace(lambda _: httpx.Response(500), data_dir=Path(directory)) as (
                _,
                client,
                _,
            ):
                assert obj(await client.get("/api/sessions/s"))["pinned"] is False
                run = obj(await client.get("/api/runs/r"))
                assert run["reasoning_duration_ms"] == 0 and run["reasoning_effort"] is None
                assert run["references_json"] == "[]"
                assert "old reasoning" in (await client.get("/api/sessions/s/messages")).text
                assert obj(await client.get("/api/books/b"))["cover_path"] == "b.png"
                assert (await client.get("/api/sessions/s/compactions")).json() == []
            with closing(sqlite3.connect(path)) as conn:
                assert conn.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
                assert conn.execute("PRAGMA foreign_key_check").fetchall() == []

    asyncio.run(scenario())
