"""Proposal self-review through the real Agent Loop, global SSE and REST apply."""

import asyncio

import httpx

from backend.llm._json import as_list, as_object, get_str, parse_json_object
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


def test_agent_revises_one_proposal_then_author_applies() -> None:
    async def scenario() -> None:
        chapter_id = ""
        change_id = ""
        calls = 0

        def upstream(request: httpx.Request) -> httpx.Response:
            nonlocal calls, change_id
            calls += 1
            if calls == 1:
                return tool_call(
                    "propose_chapter_edit",
                    {
                        "action": "update",
                        "target_id": chapter_id,
                        "edits": [{"search": "旧句", "replace": "初稿"}],
                    },
                )
            messages = as_list(parse_json_object(request.content.decode()).get("messages"))
            assert messages
            last = as_object(messages[-1])
            assert last is not None and last.get("role") == "tool"
            result = parse_json_object(get_str(last, "content") or "")
            assert result.get("ok") is True
            if calls == 2:
                change_id = get_str(result, "pending_change_id") or ""
                assert change_id
                return tool_call("read_pending_change", {"change_id": change_id})
            data = as_object(result.get("data"))
            assert data is not None
            if calls == 3:
                token = get_str(data, "revision_token")
                assert token
                return tool_call(
                    "revise_pending_change",
                    {
                        "change_id": change_id,
                        "expected_revision": token,
                        "edits": [{"search": "初稿", "replace": "定稿"}],
                    },
                )
            assert calls == 4
            assert data["id"] == change_id
            assert data["after_content"] == "保留行  \n定稿\n末行\n"
            return completion("已修订原提案")

        async with workspace(upstream) as (app, client, _):
            book, model, session = await configure(client)
            chapter_id = identifier(
                await client.post(
                    f"/api/books/{book}/chapters",
                    json={"title": "片段改稿", "content": "保留行  \n旧句\n末行\n"},
                )
            )
            async with sse(app) as stream:
                run = identifier(
                    await client.post(
                        f"/api/sessions/{session}/runs",
                        json={"text": "改稿并自查", "model_id": model},
                    )
                )
                await terminal(client, run, "completed")
                created = 0
                updated = 0
                refreshed = False
                while True:
                    _, name, event = await next_event(stream)
                    if name in ("pending_change_created", "pending_change_updated"):
                        assert event["book_id"] == book and event["session_id"] == session
                        assert event["run_id"] == run
                        change = as_object(event.get("change"))
                        assert change is not None and change["id"] == change_id
                        created += name == "pending_change_created"
                        updated += name == "pending_change_updated"
                    if name == "content_changed" and event.get("resource") == "pending_change":
                        refreshed = True
                    if name == "run_done":
                        break
                assert (created, updated) == (1, 1)
                assert refreshed
            pending = (await client.get(f"/api/books/{book}/pending-changes")).json()
            assert len(pending) == 1 and pending[0]["id"] == change_id
            assert obj(await client.get(f"/api/chapters/{chapter_id}"))["content"] == (
                "保留行  \n旧句\n末行\n"
            )
            applied = obj(await client.post(f"/api/pending-changes/{change_id}/apply"))
            assert applied["status"] == "applied"
            assert obj(await client.get(f"/api/chapters/{chapter_id}"))["content"] == (
                "保留行  \n定稿\n末行\n"
            )
            revisions = (
                await client.get(
                    f"/api/books/{book}/revisions",
                    params={"target_type": "chapter", "target_id": chapter_id},
                )
            ).json()
            assert any(
                row["source"] == "pending_apply" and row["pending_change_id"] == change_id
                for row in revisions
            )
            assert calls == 4

    asyncio.run(scenario())
