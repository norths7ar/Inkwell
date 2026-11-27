"""Companion focus and selected snapshots through real REST and mocked model calls."""

import asyncio
import json

import httpx
from pydantic import JsonValue, TypeAdapter

from tests.test_api import completion, configure, identifier, terminal, tool_call, workspace


def test_focus_is_lightweight_and_hidden_focus_is_silently_ignored() -> None:
    async def scenario() -> None:
        requests: list[str] = []

        def upstream(request: httpx.Request) -> httpx.Response:
            requests.append(request.content.decode())
            return completion()

        async with workspace(upstream) as (_, client, _):
            book, model, session = await configure(client)
            await client.patch(f"/api/sessions/{session}", json={"title": "Focus"})
            chapter = identifier(
                await client.post(
                    f"/api/books/{book}/chapters",
                    json={"title": "chapter-anchor", "content": "chapter-body-not-injected"},
                )
            )
            entry = identifier(
                await client.post(
                    f"/api/books/{book}/entries",
                    json={
                        "kind": "outline",
                        "title": "entry-anchor",
                        "category": "folder-anchor",
                        "content": "entry-body-not-injected",
                    },
                )
            )
            hidden = identifier(
                await client.post(
                    f"/api/books/{book}/entries",
                    json={
                        "kind": "note",
                        "title": "hidden-anchor",
                        "category": "hidden-folder",
                        "visibility": "global",
                        "content": "hidden-body",
                    },
                )
            )
            for kind, target in (("chapter", chapter), ("entry", entry), ("entry", hidden)):
                run = identifier(
                    await client.post(
                        f"/api/sessions/{session}/runs",
                        json={
                            "text": "Continue",
                            "model_id": model,
                            "current_focus": {"target_type": kind, "target_id": target},
                        },
                    )
                )
                state = await terminal(client, run, "completed")
                assert state["references_json"] == "[]"
            assert chapter in requests[0] and "chapter-anchor" in requests[0]
            assert entry in requests[1] and "entry-anchor" in requests[1]
            assert "folder-anchor" in requests[1] and "outline" in requests[1]
            assert "chapter-anchor" not in requests[1]  # Focus is current-turn only.
            assert all("body-not-injected" not in body for body in requests)
            assert hidden not in requests[-1]
            assert "hidden-anchor" not in requests[-1] and "hidden-folder" not in requests[-1]
            assert "hidden-body" not in requests[-1]
            other = identifier(await client.post("/api/books", json={"title": "Other"}))
            other_chapter = identifier(
                await client.post(f"/api/books/{other}/chapters", json={"title": "Other"})
            )
            denied = await client.post(
                f"/api/sessions/{session}/runs",
                json={
                    "text": "Cross-book focus",
                    "model_id": model,
                    "current_focus": {"target_type": "chapter", "target_id": other_chapter},
                },
            )
            assert denied.status_code == 404 and len(requests) == 3

    asyncio.run(scenario())


def test_selected_snapshots_validate_scope_and_survive_waiting_answer() -> None:
    async def scenario() -> None:
        requests: list[dict[str, JsonValue]] = []

        def upstream(request: httpx.Request) -> httpx.Response:
            requests.append(TypeAdapter(dict[str, JsonValue]).validate_json(request.content))
            if len(requests) == 1:
                return tool_call("ask_user", {"question": "Continue?"})
            return completion()

        async with workspace(upstream) as (_, client, _):
            book, model, session = await configure(client)
            await client.patch(f"/api/sessions/{session}", json={"title": "Selection"})
            chapter = identifier(
                await client.post(
                    f"/api/books/{book}/chapters",
                    json={"title": "Source", "content": "server-body-not-injected"},
                )
            )
            hidden = identifier(
                await client.post(
                    f"/api/books/{book}/entries",
                    json={"title": "Hidden", "kind": "note", "visibility": "global"},
                )
            )
            other = identifier(await client.post("/api/books", json={"title": "Other"}))
            foreign = identifier(
                await client.post(f"/api/books/{other}/chapters", json={"title": "Other"})
            )
            for kind, target, expected in (("entry", hidden, 403), ("chapter", foreign, 404)):
                denied = await client.post(
                    f"/api/sessions/{session}/runs",
                    json={
                        "text": "Selection",
                        "model_id": model,
                        "references": [
                            {"target_type": kind, "target_id": target, "selected_text": "secret"}
                        ],
                    },
                )
                assert denied.status_code == expected
                assert "secret" not in denied.text
            base: dict[str, JsonValue] = {"target_type": "chapter", "target_id": chapter}
            for extra in (
                {"selected_text": "x" * 12001},
                {"selected_text": ""},
                {"selected_text": "x", "start_line": 0},
                {"selected_text": "x", "start_line": 4, "end_line": 3},
                {"end_line": 3},
            ):
                denied = await client.post(
                    f"/api/sessions/{session}/runs",
                    json={"text": "Bad", "model_id": model, "references": [base | extra]},
                )
                assert denied.status_code == 422
            assert not requests
            assert (await client.get(f"/api/sessions/{session}/messages")).json() == []
            references = [
                base | {"selected_text": "unsaved-snapshot-one", "start_line": 2, "end_line": 4},
                base | {"selected_text": "unsaved-snapshot-two"},
            ]
            run = identifier(
                await client.post(
                    f"/api/sessions/{session}/runs",
                    json={"text": "Revise", "model_id": model, "references": references},
                )
            )
            await terminal(client, run, "waiting_user")
            resumed = identifier(await client.post(f"/api/runs/{run}/answer", json={"text": "Yes"}))
            state = await terminal(client, resumed, "completed")
            assert isinstance(state["references_json"], str)
            assert json.loads(state["references_json"]) == references
            for payload in requests:
                body = json.dumps(payload, ensure_ascii=False)
                assert "unsaved-snapshot-one" in body and "unsaved-snapshot-two" in body
                assert chapter in body and "2–4" in body
                assert "server-body-not-injected" not in body
            # Existing whole-document references remain a distinct compatible form.
            full = identifier(
                await client.post(
                    f"/api/sessions/{session}/runs",
                    json={"text": "Full", "model_id": model, "references": [base]},
                )
            )
            await terminal(client, full, "completed")
            assert "server-body-not-injected" in json.dumps(requests[-1])
            assert "unsaved-snapshot-one" not in json.dumps(requests[-1])

    asyncio.run(scenario())
