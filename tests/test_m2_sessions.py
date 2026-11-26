"""Conversation history management and compaction exercised through real ASGI routes."""

import asyncio
import json
from collections.abc import AsyncIterator
from contextlib import closing
from pathlib import Path
from tempfile import TemporaryDirectory

import httpx
import pytest
from pydantic import JsonValue, TypeAdapter

from backend.storage.db import Database
from backend.storage.models import PendingAction, TargetType
from backend.storage.repos.changes import create_pending_change
from tests.test_api import (
    completion,
    configure,
    identifier,
    next_event,
    obj,
    sse,
    terminal,
    tool_call,
    workspace,
)


def objects(response: httpx.Response) -> list[dict[str, JsonValue]]:
    response.raise_for_status()
    return TypeAdapter(list[dict[str, JsonValue]]).validate_json(response.content)


def test_session_pin_fork_rollback_clear_and_waiting_guards() -> None:
    async def scenario(data_dir: Path) -> None:
        ask = False

        def handler(_request: httpx.Request) -> httpx.Response:
            return (
                tool_call("ask_user", {"question": "Which way?", "options": ["A", "B"]})
                if ask
                else completion("assistant answer")
            )

        async with workspace(handler, data_dir=data_dir) as (app, client, _events):
            book_id, model_id, session_id = await configure(client)
            assert (
                await client.patch(
                    f"/api/sessions/{session_id}",
                    json={"pinned": True, "visibility_scope": "global"},
                )
            ).status_code == 200
            other_id = identifier(
                await client.post(f"/api/books/{book_id}/sessions", json={"title": "other"})
            )
            listed = objects(await client.get(f"/api/books/{book_id}/sessions"))
            assert listed[0]["id"] == session_id and listed[0]["pinned"] is True
            assert (
                await client.patch(
                    f"/api/sessions/{session_id}", json={"visibility_scope": "local"}
                )
            ).status_code == 409
            run_ids: list[str] = []
            for text in ("first user", "second user", "third user"):
                run_id = identifier(
                    await client.post(
                        f"/api/sessions/{session_id}/runs",
                        json={"text": text, "model_id": model_id},
                    )
                )
                run_ids.append(run_id)
                await terminal(client, run_id, "completed")
            messages = objects(await client.get(f"/api/sessions/{session_id}/messages"))
            assert len(messages) == 6
            foreign_message = messages[2]["id"]
            assert (
                await client.post(
                    f"/api/sessions/{other_id}/fork", json={"message_id": foreign_message}
                )
            ).status_code == 404
            branch = obj(
                await client.post(
                    f"/api/sessions/{session_id}/fork",
                    json={"message_id": messages[3]["id"], "title": "branch"},
                )
            )
            branch_id = branch["id"]
            assert isinstance(branch_id, str)
            assert branch["visibility_scope"] == "global" and branch["mode"] == "write"
            copied = objects(await client.get(f"/api/sessions/{branch_id}/messages"))
            assert [item["blocks_json"] for item in copied] == [
                item["blocks_json"] for item in messages[:4]
            ]
            assert all(
                item["id"] not in [original["id"] for original in messages] for item in copied
            )
            with closing(Database(data_dir / "inkwell.sqlite3").connect()) as conn:
                create_pending_change(
                    conn,
                    book_id=book_id,
                    session_id=session_id,
                    run_id=run_ids[1],
                    target_type=TargetType.CHAPTER,
                    action=PendingAction.CREATE,
                    title="not applied",
                    after_content="draft",
                )
            rolled = obj(
                await client.post(
                    f"/api/sessions/{session_id}/rollback", json={"message_id": messages[2]["id"]}
                )
            )
            assert rolled["user_text"] == "second user"
            assert rolled["deleted_messages"] == 4 and rolled["deleted_runs"] == 2
            assert len(objects(await client.get(f"/api/sessions/{session_id}/messages"))) == 2
            assert len(objects(await client.get(f"/api/sessions/{branch_id}/messages"))) == 4
            assert objects(await client.get(f"/api/books/{book_id}/pending-changes")) == []
            ask = True
            waiting_id = identifier(
                await client.post(
                    f"/api/sessions/{session_id}/runs",
                    json={"text": "question", "model_id": model_id},
                )
            )
            await terminal(client, waiting_id, "waiting_user")
            for path in ("rollback", "fork"):
                assert (
                    await client.post(
                        f"/api/sessions/{session_id}/{path}", json={"message_id": messages[0]["id"]}
                    )
                ).status_code == 409
            assert (
                await client.post(f"/api/sessions/{session_id}/compact", json={})
            ).status_code == 409
            assert (await client.delete(f"/api/books/{book_id}/sessions")).status_code == 409
            assert len(objects(await client.get(f"/api/books/{book_id}/sessions"))) == 3
            await client.post(f"/api/runs/{waiting_id}/cancel")
            async with sse(app) as queue:
                assert (await client.delete(f"/api/books/{book_id}/sessions")).status_code == 204
                deleted: set[str] = set()
                for _ in range(3):
                    _, event, payload = await next_event(queue)
                    assert event == "content_changed" and payload["resource"] == "session_deleted"
                    value = payload["session_id"]
                    assert isinstance(value, str)
                    deleted.add(value)
                assert deleted == {session_id, other_id, branch_id}
            assert objects(await client.get(f"/api/books/{book_id}/sessions")) == []

    root = Path(__file__).resolve().parents[1] / ".tmp" / "20261126-tests"
    root.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(dir=root, prefix="20261126-session-") as directory:
        asyncio.run(scenario(Path(directory)))


def test_compaction_preserves_history_and_reuses_anchor_after_restart() -> None:
    async def scenario(data_dir: Path) -> None:
        requests: list[dict[str, JsonValue]] = []
        empty_summary = False
        summary = {
            "objective": "continue the story",
            "facts": ["agreed fact"],
            "decisions": [],
            "open_questions": ["who sent the letter"],
            "next_steps": ["write next scene"],
        }

        def handler(request: httpx.Request) -> httpx.Response:
            body = TypeAdapter(dict[str, JsonValue]).validate_json(request.content)
            requests.append(body)
            if "你负责压缩小说写作会话" in request.content.decode():
                assert body["thinking"] == {"type": "disabled"}
                assert body["max_tokens"] == 8192
                return completion("" if empty_summary else json.dumps(summary))
            return completion("answer")

        async with workspace(handler, data_dir=data_dir) as (_app, client, _events):
            book_id, model_id, session_id = await configure(client)
            await client.patch(f"/api/sessions/{session_id}", json={"title": "fixed title"})
            models = objects(await client.get("/api/models"))
            provider_id = models[0]["provider_id"]
            await client.patch(
                f"/api/providers/{provider_id}", json={"base_url": "https://api.deepseek.com/v1"}
            )
            await client.patch("/api/settings", json={"default_model_id": model_id})
            await client.post(
                f"/api/books/{book_id}/entries",
                json={
                    "title": "secret",
                    "kind": "note",
                    "content": "GLOBAL_HIDDEN_SECRET",
                    "visibility": "global",
                },
            )
            for index in range(5):
                run_id = identifier(
                    await client.post(
                        f"/api/sessions/{session_id}/runs",
                        json={"text": f"history-{index}", "model_id": model_id},
                    )
                )
                await terminal(client, run_id, "completed")
            original = objects(await client.get(f"/api/sessions/{session_id}/messages"))
            compacted = obj(
                await client.post(
                    f"/api/sessions/{session_id}/compact", json={"keep_recent_turns": 2}
                )
            )
            assert compacted["through_seq"] == 6 and compacted["summary"] == summary
            assert objects(await client.get(f"/api/sessions/{session_id}/messages")) == original
            assert "GLOBAL_HIDDEN_SECRET" not in json.dumps(requests)
            branch_id = identifier(
                await client.post(
                    f"/api/sessions/{session_id}/fork", json={"message_id": original[-1]["id"]}
                )
            )
            assert len(objects(await client.get(f"/api/sessions/{branch_id}/compactions"))) == 1
            assert (
                await client.post(
                    f"/api/sessions/{session_id}/compact", json={"keep_recent_turns": 2}
                )
            ).status_code == 409
        async with workspace(handler, data_dir=data_dir) as (_app, client, _events):
            run_id = identifier(
                await client.post(
                    f"/api/sessions/{session_id}/runs",
                    json={"text": "resume", "model_id": model_id},
                )
            )
            await terminal(client, run_id, "completed")
            sent = json.dumps(requests[-1])
            assert "agreed fact" in sent and "history-0" not in sent and "history-3" in sent
            empty_summary = True
            assert (
                await client.post(
                    f"/api/sessions/{session_id}/compact", json={"keep_recent_turns": 2}
                )
            ).status_code == 502
            assert len(objects(await client.get(f"/api/sessions/{session_id}/compactions"))) == 1
            assert (
                await client.patch(f"/api/sessions/{session_id}", json={"pinned": True})
            ).status_code == 200
            # Rolling back into summarized history invalidates the overlay, not the raw prefix.
            rolled = obj(
                await client.post(
                    f"/api/sessions/{session_id}/rollback", json={"message_id": original[2]["id"]}
                )
            )
            assert rolled["user_text"] == "history-1"
            assert objects(await client.get(f"/api/sessions/{session_id}/compactions")) == []
            assert len(objects(await client.get(f"/api/sessions/{branch_id}/compactions"))) == 1

    root = Path(__file__).resolve().parents[1] / ".tmp" / "20261126-tests"
    root.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(dir=root, prefix="20261126-compaction-") as directory:
        asyncio.run(scenario(Path(directory)))


def test_compaction_lease_blocks_mutations_and_releases_on_request_cancel() -> None:
    async def scenario() -> None:
        entered = asyncio.Event()
        release = asyncio.Event()
        closed = asyncio.Event()

        class HeldSummary(httpx.AsyncByteStream):
            async def __aiter__(self) -> AsyncIterator[bytes]:
                entered.set()
                await release.wait()
                yield completion("unused summary").content

            async def aclose(self) -> None:
                closed.set()

        def handler(request: httpx.Request) -> httpx.Response:
            if "你负责压缩小说写作会话" in request.content.decode():
                return httpx.Response(200, stream=HeldSummary())
            return completion("answer")

        async with workspace(handler) as (_app, client, _events):
            book_id, model_id, session_id = await configure(client)
            await client.patch(f"/api/sessions/{session_id}", json={"title": "fixed title"})
            await client.patch("/api/settings", json={"default_model_id": model_id})
            for index in range(2):
                run_id = identifier(
                    await client.post(
                        f"/api/sessions/{session_id}/runs",
                        json={"text": f"turn-{index}", "model_id": model_id},
                    )
                )
                await terminal(client, run_id, "completed")
            original = objects(await client.get(f"/api/sessions/{session_id}/messages"))
            task = asyncio.create_task(
                client.post(
                    f"/api/sessions/{session_id}/compact",
                    json={"keep_recent_turns": 1},
                )
            )
            try:
                await asyncio.wait_for(entered.wait(), timeout=5)
                assert (
                    await client.post(
                        f"/api/sessions/{session_id}/runs",
                        json={"text": "cannot start", "model_id": model_id},
                    )
                ).status_code == 409
                for operation in ("rollback", "fork"):
                    assert (
                        await client.post(
                            f"/api/sessions/{session_id}/{operation}",
                            json={"message_id": original[0]["id"]},
                        )
                    ).status_code == 409
                assert (await client.delete(f"/api/sessions/{session_id}")).status_code == 409
                assert (await client.delete(f"/api/books/{book_id}")).status_code == 409
                assert (await client.delete(f"/api/books/{book_id}/sessions")).status_code == 409
            finally:
                task.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await task
            await asyncio.wait_for(closed.wait(), timeout=5)
            assert objects(await client.get(f"/api/sessions/{session_id}/compactions")) == []
            assert objects(await client.get(f"/api/sessions/{session_id}/messages")) == original
            resumed = identifier(
                await client.post(
                    f"/api/sessions/{session_id}/runs",
                    json={"text": "after cancellation", "model_id": model_id},
                )
            )
            await terminal(client, resumed, "completed")

    asyncio.run(scenario())
