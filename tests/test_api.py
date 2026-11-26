"""Core HTTP/SSE workflows, entirely in-process with mocked upstream HTTP."""

import asyncio
import json
import sqlite3
import threading
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager, closing
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import httpx
from fastapi import FastAPI
from pydantic import JsonValue, TypeAdapter
from starlette.types import Message, Scope

from backend.agent.tools import (
    PROPOSE_CHAPTER_EDIT_TOOL,
    ToolContext,
    ToolExecutionResult,
)
from backend.api.app import create_app
from backend.api.events import EventBus
from backend.config import load_config
from backend.llm.client import LLMClient
from backend.storage.migrations import MIGRATIONS


def obj(response: httpx.Response) -> dict[str, JsonValue]:
    response.raise_for_status()
    return TypeAdapter(dict[str, JsonValue]).validate_json(response.content)


def identifier(response: httpx.Response) -> str:
    value = obj(response)["id"]
    assert isinstance(value, str)
    return value


def completion(text: str = "done") -> httpx.Response:
    payload = {"choices": [{"delta": {"content": text}, "finish_reason": "stop"}]}
    return httpx.Response(200, text=f"data: {json.dumps(payload)}\n\ndata: [DONE]\n\n")


def tool_call(name: str, arguments: dict[str, JsonValue]) -> httpx.Response:
    payload = {
        "choices": [
            {
                "delta": {
                    "tool_calls": [
                        {
                            "index": 0,
                            "id": "call-1",
                            "function": {
                                "name": name,
                                "arguments": json.dumps(arguments),
                            },
                        }
                    ]
                },
                "finish_reason": "tool_calls",
            }
        ]
    }
    return httpx.Response(200, text=f"data: {json.dumps(payload)}\n\ndata: [DONE]\n\n")


@asynccontextmanager
async def workspace(
    handler: Callable[[httpx.Request], httpx.Response],
    *,
    data_dir: Path | None = None,
    static_dir: Path | None = None,
) -> AsyncIterator[tuple[FastAPI, httpx.AsyncClient, EventBus]]:
    root = Path(__file__).resolve().parents[1] / ".tmp" / "20261126-tests"
    root.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(dir=root, prefix="20261126-api-") as directory:
        events = EventBus(heartbeat=0.1)
        llm = LLMClient(transport=httpx.MockTransport(handler))
        app = create_app(
            load_config(data_dir=data_dir or directory),
            llm=llm,
            events=events,
            static_dir=static_dir,
        )
        async with (
            app.router.lifespan_context(app),
            httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                base_url="http://testserver",
            ) as client,
        ):
            yield app, client, events


async def configure(client: httpx.AsyncClient) -> tuple[str, str, str]:
    book_id = identifier(await client.post("/api/books", json={"title": "Book"}))
    provider = await client.post(
        "/api/providers",
        json={
            "name": "Mock",
            "base_url": "https://mock.invalid/v1",
            "preset": "custom",
            "api_key": "test-secret",
            "headers": {"X-Private": "private-header"},
            "default_protocol": "openai-chat",
        },
    )
    provider_id = identifier(provider)
    assert obj(provider)["api_key_configured"] is True
    assert obj(provider)["headers_configured"] is True
    assert "test-secret" not in provider.text and "private-header" not in provider.text
    assert "encrypted" not in provider.text
    model_id = identifier(
        await client.post(
            f"/api/providers/{provider_id}/models",
            json={
                "model_id": "mock-model",
                "protocol_override": "openai-chat",
            },
        )
    )
    session_id = identifier(
        await client.post(f"/api/books/{book_id}/sessions", json={"mode": "write"})
    )
    return book_id, model_id, session_id


async def terminal(client: httpx.AsyncClient, run_id: str, status: str) -> dict[str, JsonValue]:
    async with asyncio.timeout(5):
        while True:
            run = obj(await client.get(f"/api/runs/{run_id}"))
            if run["status"] == status:
                return run
            assert run["status"] == "running", run
            await asyncio.sleep(0.01)


@asynccontextmanager
async def sse(app: FastAPI, last_id: str | None = None) -> AsyncIterator[asyncio.Queue[str]]:
    """Exercise StreamingResponse directly: ASGITransport buffers infinite bodies."""
    chunks: asyncio.Queue[str] = asyncio.Queue()
    disconnected = asyncio.Event()
    headers = [] if last_id is None else [(b"last-event-id", last_id.encode())]
    scope: Scope = {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": "2.3"},
        "http_version": "1.1",
        "method": "GET",
        "scheme": "http",
        "path": "/api/events",
        "raw_path": b"/api/events",
        "query_string": b"",
        "root_path": "",
        "headers": [(b"host", b"testserver"), *headers],
        "client": ("127.0.0.1", 12345),
        "server": ("testserver", 80),
    }

    async def receive() -> Message:
        await disconnected.wait()
        return {"type": "http.disconnect"}

    async def send(message: Message) -> None:
        if message["type"] == "http.response.start":
            assert message["status"] == 200
        elif message["type"] == "http.response.body":
            await chunks.put(message.get("body", b"").decode())

    task = asyncio.create_task(app(scope, receive, send))
    try:
        assert await asyncio.wait_for(chunks.get(), 5) == ": connected\n\n"
        yield chunks
    finally:
        disconnected.set()
        await asyncio.wait_for(task, 5)


async def next_event(queue: asyncio.Queue[str]) -> tuple[str, str, dict[str, JsonValue]]:
    async with asyncio.timeout(5):
        while True:
            frame = await queue.get()
            if frame.startswith(":"):
                continue
            fields = dict(line.split(": ", 1) for line in frame.strip().splitlines())
            return (
                fields["id"],
                fields["event"],
                TypeAdapter(dict[str, JsonValue]).validate_json(fields["data"]),
            )


def test_content_pending_changes_and_credentials() -> None:
    async def scenario() -> None:
        chapter_id = ""
        calls = 0

        def upstream(request: httpx.Request) -> httpx.Response:
            nonlocal calls
            assert request.headers["authorization"] == "Bearer test-secret"
            assert request.headers["x-private"] == "private-header"
            calls += 1
            if calls == 1:
                return tool_call(
                    "propose_chapter_edit",
                    {
                        "action": "update",
                        "target_id": chapter_id,
                        "content": "revised story",
                    },
                )
            return completion()

        async with workspace(upstream) as (_, client, _):
            assert obj(await client.get("/health"))["service"] == "inkwell"
            assert (await client.get("/writer/unknown")).status_code == 404
            book_id, model_id, session_id = await configure(client)
            volume_id = identifier(
                await client.post(f"/api/books/{book_id}/volumes", json={"title": "Volume"})
            )
            chapter_id = identifier(
                await client.post(
                    f"/api/books/{book_id}/chapters",
                    json={
                        "title": "Chapter",
                        "content": "original story",
                        "volume_id": volume_id,
                    },
                )
            )
            entry_id = identifier(
                await client.post(
                    f"/api/books/{book_id}/entries",
                    json={
                        "title": "Hero",
                        "kind": "character",
                        "content": "a traveller",
                    },
                )
            )
            assert (
                obj(
                    await client.patch(
                        f"/api/entries/{entry_id}", json={"content": "brave traveller"}
                    )
                )["title"]
                == "Hero"
            )
            assert (
                obj(await client.patch(f"/api/chapters/{chapter_id}", json={"volume_id": None}))[
                    "volume_id"
                ]
                is None
            )
            search = await client.get(f"/api/books/{book_id}/search", params={"q": "traveller"})
            assert entry_id in search.text
            run_id = identifier(
                await client.post(
                    f"/api/sessions/{session_id}/runs",
                    json={"text": "revise", "model_id": model_id},
                )
            )
            await terminal(client, run_id, "completed")
            pending_response = await client.get(f"/api/books/{book_id}/pending-changes")
            pending = TypeAdapter(list[dict[str, JsonValue]]).validate_json(
                pending_response.content
            )
            assert len(pending) == 1 and pending[0]["status"] == "pending"
            assert (
                obj(await client.get(f"/api/chapters/{chapter_id}"))["content"] == "original story"
            )
            change_id = pending[0]["id"]
            # Approval must not overwrite edits made after the suggestion was created.
            await client.patch(f"/api/chapters/{chapter_id}", json={"content": "concurrent edit"})
            assert (await client.post(f"/api/pending-changes/{change_id}/apply")).status_code == 409
            assert (
                obj(await client.get(f"/api/chapters/{chapter_id}"))["content"] == "concurrent edit"
            )
            assert obj(await client.get(f"/api/pending-changes/{change_id}"))["status"] == "pending"
            await client.patch(f"/api/chapters/{chapter_id}", json={"content": "original story"})
            applied = obj(await client.post(f"/api/pending-changes/{change_id}/apply"))
            assert applied["status"] == "applied"
            assert (
                obj(await client.get(f"/api/chapters/{chapter_id}"))["content"] == "revised story"
            )
            revisions = await client.get(
                f"/api/books/{book_id}/revisions", params={"target_id": chapter_id}
            )
            assert "pending_apply" in revisions.text and "revised story" in revisions.text
            assert (await client.post(f"/api/pending-changes/{change_id}/apply")).status_code == 409
            invalid = await client.post(
                "/api/providers", json={"api_key": {"secret": "do-not-echo"}}
            )
            assert invalid.status_code == 422 and "do-not-echo" not in invalid.text
            providers = await client.get("/api/providers")
            assert "test-secret" not in providers.text and "encrypted" not in providers.text
            assert (await client.delete(f"/api/entries/{entry_id}")).status_code == 204
            assert (await client.get(f"/api/entries/{entry_id}")).status_code == 404
            assert (await client.delete(f"/api/books/{book_id}")).status_code == 204

        root = Path(__file__).resolve().parents[1] / ".tmp" / "20261126-tests"
        with TemporaryDirectory(dir=root, prefix="20261126-static-") as directory:
            dist = Path(directory)
            (dist / "index.html").write_text("<html>Inkwell test UI</html>", encoding="utf-8")
            (dist / "bundle.js").write_text("console.log('test');", encoding="utf-8")
            async with workspace(upstream, static_dir=dist) as (_, client, _):
                assert "Inkwell test UI" in (await client.get("/writer/chapter")).text
                assert "console.log" in (await client.get("/bundle.js")).text
                assert obj(await client.get("/health"))["service"] == "inkwell"
                assert (await client.get("/api/unknown")).status_code == 404
                assert (await client.get("/missing.js")).status_code == 404

    asyncio.run(scenario())


def test_global_sse_live_and_replay() -> None:
    async def scenario() -> None:
        async with workspace(lambda _: completion("streamed text")) as (app, client, _):
            async with sse(app, "1") as queue:
                _, name, _ = await next_event(queue)
                assert name == "resync_required"
            book_id, model_id, session_id = await configure(client)
            async with sse(app) as queue:
                run_id = identifier(
                    await client.post(
                        f"/api/sessions/{session_id}/runs",
                        json={"text": "write", "model_id": model_id},
                    )
                )
                first_id, name, payload = await next_event(queue)
                assert name == "run_started"
                assert (payload["book_id"], payload["session_id"], payload["run_id"]) == (
                    book_id,
                    session_id,
                    run_id,
                )
                second_id, name, payload = await next_event(queue)
                assert int(second_id) > int(first_id)
                assert name == "text_delta" and payload["delta"] == "streamed text"
                await terminal(client, run_id, "completed")
            async with sse(app, first_id) as queue:
                replay_id, name, payload = await next_event(queue)
                assert replay_id == second_id and name == "text_delta"
                assert payload["run_id"] == run_id
            async with sse(app, "1") as queue:
                _, name, _ = await next_event(queue)
                assert name == "resync_required"

    asyncio.run(scenario())


def test_ask_user_resume_conflict_and_cancel() -> None:
    async def scenario() -> None:
        calls = 0
        blocked = asyncio.Event()
        closed = asyncio.Event()

        class BlockingStream(httpx.AsyncByteStream):
            async def __aiter__(self) -> AsyncIterator[bytes]:
                blocked.set()
                await asyncio.Event().wait()
                yield b""

            async def aclose(self) -> None:
                closed.set()

        def upstream(_: httpx.Request) -> httpx.Response:
            nonlocal calls
            calls += 1
            if calls == 1:
                return tool_call("ask_user", {"question": "Which ending?", "options": ["A", "B"]})
            if calls == 2:
                return completion("Ending A")
            return httpx.Response(200, stream=BlockingStream())

        root = Path(__file__).resolve().parents[1] / ".tmp" / "20261126-tests"
        root.mkdir(parents=True, exist_ok=True)
        with TemporaryDirectory(dir=root, prefix="20261126-restart-") as directory:
            data_dir = Path(directory)
            async with workspace(upstream, data_dir=data_dir) as (_, client, _):
                _, model_id, session_id = await configure(client)
                run_id = identifier(
                    await client.post(
                        f"/api/sessions/{session_id}/runs",
                        json={"text": "choose", "model_id": model_id},
                    )
                )
                waiting = await terminal(client, run_id, "waiting_user")
                assert "Which ending?" in str(waiting["pending_question_json"])
                assert (
                    await client.post(
                        f"/api/sessions/{session_id}/runs",
                        json={"text": "conflict", "model_id": model_id},
                    )
                ).status_code == 409

            # A fresh application reads the suspended question and encrypted credentials.
            async with workspace(upstream, data_dir=data_dir) as (_, client, _):
                assert obj(await client.get(f"/api/runs/{run_id}"))["status"] == "waiting_user"
                resumed_id = identifier(
                    await client.post(
                        f"/api/runs/{run_id}/answer",
                        json={"text": "A"},
                    )
                )
                assert resumed_id != run_id
                await terminal(client, resumed_id, "completed")
                assert obj(await client.get(f"/api/runs/{run_id}"))["status"] == "completed"
                messages = await client.get(f"/api/sessions/{session_id}/messages")
                assert "Ending A" in messages.text
                cancel_id = identifier(
                    await client.post(
                        f"/api/sessions/{session_id}/runs",
                        json={"text": "wait", "model_id": model_id},
                    )
                )
                await asyncio.wait_for(blocked.wait(), 5)
                assert (
                    await client.post(
                        f"/api/runs/{cancel_id}/steer",
                        json={"text": "change direction"},
                    )
                ).is_success
                assert (
                    obj(await client.post(f"/api/runs/{cancel_id}/cancel"))["status"] == "cancelled"
                )
                await asyncio.wait_for(closed.wait(), 5)
                blocked.clear()
                closed.clear()
                shutdown_id = identifier(
                    await client.post(
                        f"/api/sessions/{session_id}/runs",
                        json={"text": "shutdown", "model_id": model_id},
                    )
                )
                await asyncio.wait_for(blocked.wait(), 5)
            assert closed.is_set(), "Application shutdown must close the live upstream stream"
            async with workspace(upstream, data_dir=data_dir) as (_, client, _):
                assert obj(await client.get(f"/api/runs/{shutdown_id}"))["status"] == "cancelled"

    asyncio.run(scenario())


def test_cancel_waits_for_tool_commit_before_releasing_session() -> None:
    async def scenario() -> None:
        started = threading.Event()
        release = threading.Event()
        calls = 0

        def blocked_tool(context: ToolContext, arguments: str) -> ToolExecutionResult:
            started.set()
            if not release.wait(5):
                raise TimeoutError("Test did not release the tool worker")
            return PROPOSE_CHAPTER_EDIT_TOOL.execute(context, arguments)

        def upstream(_: httpx.Request) -> httpx.Response:
            nonlocal calls
            calls += 1
            if calls == 1:
                return tool_call(
                    "propose_chapter_edit",
                    {
                        "action": "create",
                        "title": "Draft",
                        "content": "pending draft",
                    },
                )
            return completion("next turn")

        wrapped = replace(PROPOSE_CHAPTER_EDIT_TOOL, runner=blocked_tool)
        with patch("backend.agent.loop.tools_for_mode", return_value=(wrapped,)):
            async with workspace(upstream) as (_, client, _):
                book_id, model_id, session_id = await configure(client)
                run_id = identifier(
                    await client.post(
                        f"/api/sessions/{session_id}/runs",
                        json={"text": "propose", "model_id": model_id},
                    )
                )
                try:
                    assert await asyncio.wait_for(asyncio.to_thread(started.wait, 5), 6)
                    cancel = asyncio.create_task(client.post(f"/api/runs/{run_id}/cancel"))
                    await asyncio.sleep(0.02)
                    assert not cancel.done(), "Cancellation must wait for the tool transaction"
                    conflict = await client.post(
                        f"/api/sessions/{session_id}/runs",
                        json={"text": "too early", "model_id": model_id},
                    )
                    assert conflict.status_code == 409
                finally:
                    release.set()
                assert obj(await asyncio.wait_for(cancel, 5))["status"] == "cancelled"
                messages = TypeAdapter(list[dict[str, JsonValue]]).validate_json(
                    (await client.get(f"/api/sessions/{session_id}/messages")).content
                )
                paired = [message for message in messages if message["role"] == "tool"]
                assert len(paired) == 1
                assert paired[0]["run_id"] == run_id
                assert "call-1" in str(paired[0]["blocks_json"])
                pending = await client.get(f"/api/books/{book_id}/pending-changes")
                assert "pending draft" in pending.text
                next_id = identifier(
                    await client.post(
                        f"/api/sessions/{session_id}/runs",
                        json={"text": "continue", "model_id": model_id},
                    )
                )
                await terminal(client, next_id, "completed")

    asyncio.run(scenario())


def test_model_errors_survive_sse_history_and_restart_without_secrets() -> None:
    async def scenario() -> None:
        status = 401
        message = "test-secret private-header authentication rejected"

        def upstream(_: httpx.Request) -> httpx.Response:
            return httpx.Response(
                status,
                json={"error": {"message": message}},
                headers={"Retry-After": "0.01"},
            )

        root = Path(__file__).resolve().parents[1] / ".tmp" / "20261126-tests"
        root.mkdir(parents=True, exist_ok=True)
        with TemporaryDirectory(dir=root, prefix="20261126-error-history-") as directory:
            data_dir = Path(directory)
            saved: dict[str, dict[str, JsonValue]] = {}
            async with workspace(upstream, data_dir=data_dir) as (app, client, _):
                _, model_id, session_id = await configure(client)
                provider_id = obj(await client.get(f"/api/models/{model_id}"))["provider_id"]
                for status, category, error_message in (
                    (401, "auth", "test-secret private-header authentication rejected"),
                    (429, "rate_limit", "test-secret private-header too many requests"),
                    (400, "context_overflow", "test-secret private-header context length exceeded"),
                ):
                    message = error_message
                    discovery = await client.post(f"/api/providers/{provider_id}/discover")
                    assert discovery.status_code == 502
                    discovered = TypeAdapter(dict[str, JsonValue]).validate_json(discovery.content)
                    error = discovered["error"]
                    assert isinstance(error, dict)
                    assert error["category"] == category
                    assert error["status_code"] == status
                    assert error["retryable"] == (category == "rate_limit")
                    assert discovered["detail"] == error["message"]
                    assert "test-secret" not in discovery.text
                    assert "private-header" not in discovery.text

                    async with sse(app) as queue:
                        run_id = identifier(
                            await client.post(
                                f"/api/sessions/{session_id}/runs",
                                json={"text": "write", "model_id": model_id},
                            )
                        )
                        retries = 0
                        while True:
                            _, name, payload = await next_event(queue)
                            serialized = json.dumps(payload)
                            assert "test-secret" not in serialized
                            assert "private-header" not in serialized
                            if name == "attempt_reset":
                                retries += 1
                                assert payload["error"] == error
                                assert payload["reason"] == error["message"]
                            if name == "run_done":
                                assert payload["status"] == "failed"
                                assert payload["error"] == error
                                assert payload["error_message"] == error["message"]
                                break
                        assert retries == (2 if category == "rate_limit" else 0)
                    failed = await terminal(client, run_id, "failed")
                    assert failed["error"] == error
                    assert failed["error_message"] == error["message"]
                    saved[run_id] = error

            async with workspace(upstream, data_dir=data_dir) as (_, client, _):
                history_response = await client.get(f"/api/sessions/{session_id}/runs")
                history = TypeAdapter(list[dict[str, JsonValue]]).validate_json(
                    history_response.content
                )
                assert len(history) == len(saved)
                for run_id, error in saved.items():
                    assert obj(await client.get(f"/api/runs/{run_id}"))["error"] == error
                    assert next(item for item in history if item["id"] == run_id)["error"] == error
                assert "test-secret" not in history_response.text
                assert "private-header" not in history_response.text

    asyncio.run(scenario())


def test_safe_validation_details_and_existing_database_upgrade() -> None:
    async def scenario() -> None:
        root = Path(__file__).resolve().parents[1] / ".tmp" / "20261126-tests"
        root.mkdir(parents=True, exist_ok=True)
        with TemporaryDirectory(dir=root, prefix="20261126-error-migration-") as directory:
            data_dir = Path(directory)
            with closing(sqlite3.connect(data_dir / "inkwell.sqlite3")) as conn, conn:
                for version, statements in MIGRATIONS:
                    if version == 1:
                        for statement in statements:
                            conn.execute(statement)
                conn.execute("PRAGMA user_version = 1")
                conn.execute(
                    "INSERT INTO books (id,title,created_at,updated_at) VALUES (?,?,?,?)",
                    ("old-book", "Existing manuscript", "2026-11-26", "2026-11-26"),
                )
                conn.execute(
                    "INSERT INTO sessions (id,book_id,created_at,updated_at) VALUES (?,?,?,?)",
                    ("old-session", "old-book", "2026-11-26", "2026-11-26"),
                )
                conn.execute(
                    "INSERT INTO runs (id,session_id,book_id,status,error_message,started_at) "
                    "VALUES (?,?,?,?,?,?)",
                    (
                        "old-run",
                        "old-session",
                        "old-book",
                        "failed",
                        "test-secret private-header in old raw provider error",
                        "2026-11-26",
                    ),
                )
            async with workspace(lambda _: completion(), data_dir=data_dir) as (_, client, _):
                assert (
                    obj(await client.get("/api/books/old-book"))["title"] == "Existing manuscript"
                )
                old = await client.get("/api/runs/old-run")
                assert obj(old)["status"] == "failed"
                assert "test-secret" not in old.text and "private-header" not in old.text
                history = await client.get("/api/sessions/old-session/runs")
                assert "test-secret" not in history.text and "private-header" not in history.text

                invalid = await client.post(
                    "/api/providers",
                    json={
                        "api_key": {"secret": "test-secret"},
                        "headers": {"private-header": {"value": "test-secret"}},
                        "test-secret": "private-header",
                    },
                )
                assert invalid.status_code == 422
                details = TypeAdapter(dict[str, JsonValue]).validate_json(invalid.content)["detail"]
                assert isinstance(details, list) and len(details) == 3
                assert "test-secret" not in invalid.text and "private-header" not in invalid.text
                for detail in details:
                    assert isinstance(detail, dict)
                    assert set(detail) == {"loc", "type", "msg"}
                    assert isinstance(detail["loc"], list)
                    assert isinstance(detail["type"], str) and detail["type"]
                    assert isinstance(detail["msg"], str) and detail["msg"]
                assert any(
                    isinstance(detail, dict) and detail["loc"] == ["body", "api_key"]
                    for detail in details
                )
                invalid_title = await client.post("/api/books", json={"title": ""})
                title_details = TypeAdapter(dict[str, JsonValue]).validate_json(
                    invalid_title.content
                )["detail"]
                assert isinstance(title_details, list)
                assert isinstance(title_details[0], dict)
                assert title_details[0]["loc"] == ["body", "title"]
                assert title_details[0]["type"] == "string_too_short"
            with closing(sqlite3.connect(data_dir / "inkwell.sqlite3")) as conn:
                assert conn.execute("PRAGMA user_version").fetchone()[0] > 1

    asyncio.run(scenario())
