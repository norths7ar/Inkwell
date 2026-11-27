"""Native Gemini wire contracts and persisted Agent tool turns, without network access."""

import asyncio
import json
from collections.abc import AsyncIterator

import httpx
import pytest

from backend.agent.serialization import deserialize_blocks, serialize_blocks
from backend.llm._json import JsonObject, JsonValue, as_list, as_object, parse_json_object
from backend.llm.client import LLMClient
from backend.llm.errors import ErrorCategory, LLMError
from backend.llm.presets import build_endpoint_from_preset
from backend.llm.protocols import anthropic_messages, google_genai
from backend.llm.types import (
    EndpointConfig,
    GenerateRequest,
    LLMMessage,
    ReasoningBlock,
    Role,
    TextBlock,
    ToolCallBlock,
    ToolResultBlock,
    ToolSpec,
    WireProtocol,
)
from tests.test_api import identifier, obj, terminal, workspace


def chunk(parts: list[JsonValue], *, finish: str | None = None) -> JsonObject:
    candidate: JsonObject = {"index": 0, "content": {"role": "model", "parts": parts}}
    if finish:
        candidate["finishReason"] = finish
    return {"candidates": [candidate]}


class Frames(httpx.AsyncByteStream):
    def __init__(self, frames: list[JsonObject]):
        self.frames = frames

    async def __aiter__(self) -> AsyncIterator[bytes]:
        for frame in self.frames:
            data = ("data: " + json.dumps(frame, ensure_ascii=False) + "\r\n\r\n").encode()
            # Split arbitrary bytes, including UTF-8 and the SSE separator.
            for start in range(0, len(data), 7):
                yield data[start : start + 7]
            await asyncio.sleep(0)


def streamed(frames: list[JsonObject]) -> httpx.Response:
    return httpx.Response(200, stream=Frames(frames), headers={"content-type": "text/event-stream"})


def test_google_stream_signatures_order_usage_and_tool_replay() -> None:
    async def scenario() -> None:
        captured: list[httpx.Request] = []
        frames = [
            chunk([{"text": "先", "thought": True}]),
            chunk([{"text": "思考", "thought": True}]),
            chunk([{"thoughtSignature": "reason-signature"}]),
            chunk([{"text": "准备", "thoughtSignature": "text-signature"}]),
            chunk([{"text": "读取"}]),
            chunk([{"functionCall": {"name": "read", "args": {"id": "one"}, "id": "call-1"}}]),
            chunk([{"thoughtSignature": "call-signature"}]),
            chunk(
                [
                    {
                        "functionCall": {"name": "read", "args": {"id": "two"}},
                        "thoughtSignature": "parallel-signature",
                    },
                    {"text": "稍候", "thoughtSignature": "tail-signature"},
                ],
                finish="STOP",
            ),
            {
                "usageMetadata": {
                    "promptTokenCount": 20,
                    "candidatesTokenCount": 8,
                    "thoughtsTokenCount": 5,
                    "cachedContentTokenCount": 4,
                }
            },
        ]

        def upstream(request: httpx.Request) -> httpx.Response:
            captured.append(request)
            return streamed(frames)

        endpoint = EndpointConfig(
            "https://mock.invalid/v1beta", WireProtocol.OPENAI_CHAT, api_key="secret"
        )
        request = GenerateRequest(
            "models/gemini-3-flash-preview",
            (LLMMessage.text(Role.SYSTEM, "rules"), LLMMessage.text(Role.USER, "read")),
            protocol_override=WireProtocol.GOOGLE_GENAI,
            reasoning_effort="medium",
            tools=(
                ToolSpec(
                    "read",
                    "Read",
                    {
                        "type": "object",
                        "$defs": {"ID": {"type": "string"}},
                        "properties": {"id": {"$ref": "#/$defs/ID"}},
                    },
                ),
            ),
        )
        async with LLMClient(transport=httpx.MockTransport(upstream)) as client:
            response = await client.complete(endpoint, request)
        sent = captured[0]
        assert sent.url.path == "/v1beta/models/gemini-3-flash-preview:streamGenerateContent"
        assert sent.url.params["alt"] == "sse"
        assert sent.headers["x-goog-api-key"] == "secret" and "authorization" not in sent.headers
        payload = parse_json_object(sent.content.decode())
        assert payload["generationConfig"] == {
            "thinkingConfig": {"includeThoughts": True, "thinkingLevel": "MEDIUM"}
        }
        assert "parametersJsonSchema" in json.dumps(payload["tools"])
        assert "$defs" in json.dumps(payload["tools"])
        assert response.finish_reason == "tool_calls"
        assert response.usage.input_tokens == 20 and response.usage.output_tokens == 13
        assert response.usage.cache_read_tokens == 4
        blocks = response.blocks
        assert isinstance(blocks[0], ReasoningBlock) and blocks[0].text == "先思考"
        assert blocks[0].signature == "reason-signature"
        assert blocks[1] == TextBlock("准备", signature="text-signature")
        assert blocks[2] == TextBlock("读取")
        assert isinstance(blocks[3], ToolCallBlock) and blocks[3].signature == "call-signature"
        assert isinstance(blocks[4], ToolCallBlock) and blocks[4].signature == "parallel-signature"
        assert blocks[3].call_id != blocks[4].call_id
        assert blocks[5] == TextBlock("稍候", signature="tail-signature")
        assert deserialize_blocks(serialize_blocks(blocks)) == blocks
        replay = GenerateRequest(
            "gemini-3-flash-preview",
            (
                LLMMessage(Role.ASSISTANT, blocks),
                LLMMessage(
                    Role.TOOL,
                    (
                        ToolResultBlock(blocks[3].call_id, "read", '{"text":"正文"}'),
                        ToolResultBlock(blocks[4].call_id, "read", "not found", True),
                    ),
                ),
            ),
        )
        _, _, body = google_genai.build_request(endpoint, replay)
        contents = as_list(body["contents"])
        assert contents is not None
        model = as_object(contents[0])
        assert model is not None
        parts = as_list(model["parts"])
        assert parts is not None
        assert parts[3] == {
            "functionCall": {"name": "read", "args": {"id": "one"}, "id": "call-1"},
            "thoughtSignature": "call-signature",
        }
        assert "reason-signature" in json.dumps(body) and "tail-signature" in json.dumps(body)
        assert "functionResponse" in json.dumps(body) and '"error": "not found"' in json.dumps(body)
        _, _, anthropic = anthropic_messages.build_request(endpoint, replay)
        assert "signature" not in json.dumps(anthropic)
        _, _, from_anthropic = google_genai.build_request(
            endpoint,
            GenerateRequest(
                "gemini-2.5-flash",
                (
                    LLMMessage(
                        Role.ASSISTANT,
                        (
                            ReasoningBlock("private reasoning", signature="anthropic-only"),
                            TextBlock("visible answer"),
                        ),
                    ),
                ),
            ),
        )
        assert "anthropic-only" not in json.dumps(from_anthropic)
        assert "visible answer" in json.dumps(from_anthropic)

    asyncio.run(scenario())


def test_google_discovery_pagination_reasoning_and_errors() -> None:
    async def scenario() -> None:
        endpoint = build_endpoint_from_preset("gemini", api_key="secret")
        pages: list[str] = []

        def discover(request: httpx.Request) -> httpx.Response:
            assert request.headers["x-goog-api-key"] == "secret"
            pages.append(request.url.params.get("pageToken", ""))
            if len(pages) == 1:
                return httpx.Response(
                    200,
                    json={
                        "models": [
                            {
                                "name": "models/embed",
                                "supportedGenerationMethods": ["embedContent"],
                            },
                            {
                                "name": "models/gemini-2.5-flash",
                                "displayName": "Gemini Flash",
                                "supportedGenerationMethods": ["generateContent"],
                            },
                        ],
                        "nextPageToken": "page/2",
                    },
                )
            return httpx.Response(
                200,
                json={
                    "models": [
                        {
                            "name": "models/gemini-2.5-pro",
                            "supportedGenerationMethods": ["generateContent"],
                        }
                    ]
                },
            )

        async with LLMClient(transport=httpx.MockTransport(discover)) as client:
            models = await client.list_models(endpoint)
        assert pages == ["", "page/2"]
        assert [model.model_id for model in models] == ["gemini-2.5-flash", "gemini-2.5-pro"]
        assert models[0].display_name == "Gemini Flash"
        for model, effort, expected in [
            ("gemini-2.5-flash", "none", {"includeThoughts": False, "thinkingBudget": 0}),
            ("gemini-2.5-pro", "none", {"includeThoughts": False, "thinkingBudget": 128}),
            ("gemini-2.5-flash", "high", {"includeThoughts": True, "thinkingBudget": 24576}),
            ("gemini-3-pro-preview", "medium", {"includeThoughts": True, "thinkingLevel": "HIGH"}),
        ]:
            _, _, body = google_genai.build_request(
                endpoint, GenerateRequest(model, (), reasoning_effort=effort)
            )
            assert body["generationConfig"] == {"thinkingConfig": expected}
        _, _, limited = google_genai.build_request(
            endpoint,
            GenerateRequest(
                "gemini-2.5-flash", (), reasoning_effort="high", max_output_tokens=1024
            ),
        )
        assert limited["generationConfig"] == {
            "maxOutputTokens": 1024,
            "thinkingConfig": {"includeThoughts": True, "thinkingBudget": 512},
        }
        cases: list[tuple[int, JsonObject, ErrorCategory, bool]] = [
            (
                400,
                {
                    "message": "API key not valid",
                    "status": "INVALID_ARGUMENT",
                    "details": [{"reason": "API_KEY_INVALID"}],
                },
                ErrorCategory.AUTH,
                False,
            ),
            (
                429,
                {
                    "message": "quota exceeded",
                    "status": "RESOURCE_EXHAUSTED",
                    "details": [{"retryDelay": "3.5s"}],
                },
                ErrorCategory.RATE_LIMIT,
                True,
            ),
            (
                429,
                {
                    "message": "quota exceeded",
                    "status": "RESOURCE_EXHAUSTED",
                    "details": [{"violations": [{"quotaId": "GenerateRequestsPerDayPerProject"}]}],
                },
                ErrorCategory.QUOTA,
                False,
            ),
            (
                400,
                {"message": "Input exceeds the maximum token limit", "status": "INVALID_ARGUMENT"},
                ErrorCategory.CONTEXT_OVERFLOW,
                False,
            ),
            (503, {"message": "unavailable", "status": "UNAVAILABLE"}, ErrorCategory.SERVER, True),
        ]
        for status, error, category, retryable in cases:

            def failing(
                _request: httpx.Request, code: int = status, payload: JsonObject = error
            ) -> httpx.Response:
                return httpx.Response(code, json={"error": payload})

            async with LLMClient(transport=httpx.MockTransport(failing)) as client:
                with pytest.raises(LLMError) as raised:
                    await client.complete(endpoint, GenerateRequest("gemini-2.5-flash", ()))
            assert raised.value.category == category and raised.value.retryable is retryable
            if category == ErrorCategory.RATE_LIMIT:
                assert raised.value.retry_after_seconds == 3.5
        for frames, category in [
            (
                [{"error": {"code": 429, "status": "RESOURCE_EXHAUSTED", "message": "rate"}}],
                ErrorCategory.RATE_LIMIT,
            ),
            ([{"promptFeedback": {"blockReason": "SAFETY"}}], ErrorCategory.INVALID_REQUEST),
            ([chunk([{"text": "truncated"}])], ErrorCategory.PROTOCOL),
        ]:

            def stream_error(
                _request: httpx.Request, payload: list[JsonObject] = frames
            ) -> httpx.Response:
                return streamed(payload)

            async with LLMClient(transport=httpx.MockTransport(stream_error)) as client:
                with pytest.raises(LLMError) as raised:
                    await client.complete(endpoint, GenerateRequest("gemini-2.5-flash", ()))
            assert raised.value.category == category

    asyncio.run(scenario())


def test_google_agent_tool_signatures_persist_and_replay() -> None:
    async def scenario() -> None:
        requests: list[JsonObject] = []
        chapter_id = ""

        def upstream(request: httpx.Request) -> httpx.Response:
            assert request.headers["x-goog-api-key"] == "private-key"
            payload = parse_json_object(request.content.decode())
            requests.append(payload)
            if len(requests) == 1:
                return streamed(
                    [
                        chunk(
                            [
                                {
                                    "text": "读取前思考",
                                    "thought": True,
                                    "thoughtSignature": "reason-state",
                                },
                                {
                                    "functionCall": {
                                        "name": "read_chapter",
                                        "args": {"chapter_id": chapter_id},
                                        "id": "read-1",
                                    },
                                    "thoughtSignature": "tool-state",
                                },
                            ],
                            finish="STOP",
                        )
                    ]
                )
            assert "tool-state" in json.dumps(payload) and "reason-state" in json.dumps(payload)
            assert "functionResponse" in json.dumps(payload) and chapter_id in json.dumps(payload)
            return streamed(
                [chunk([{"text": "已读", "thoughtSignature": "answer-state"}], finish="STOP")]
            )

        async with workspace(upstream) as (_, client, _):
            presets = (await client.get("/api/provider-presets")).json()
            assert any(preset["id"] == "gemini" for preset in presets)
            provider = obj(
                await client.post(
                    "/api/providers", json={"preset": "gemini", "api_key": "private-key"}
                )
            )
            assert provider["default_protocol"] == "google-genai"
            assert "private-key" not in json.dumps(provider)
            model = identifier(
                await client.post(
                    f"/api/providers/{provider['id']}/models", json={"model_id": "gemini-2.5-flash"}
                )
            )
            book = identifier(await client.post("/api/books", json={"title": "Gemini book"}))
            chapter_id = identifier(
                await client.post(
                    f"/api/books/{book}/chapters",
                    json={"title": "Chapter", "content": "chapter-content"},
                )
            )
            session = identifier(
                await client.post(f"/api/books/{book}/sessions", json={"title": "Existing title"})
            )
            run = identifier(
                await client.post(
                    f"/api/sessions/{session}/runs",
                    json={"text": "read", "model_id": model, "reasoning_effort": "low"},
                )
            )
            await terminal(client, run, "completed")
            messages = (await client.get(f"/api/sessions/{session}/messages")).json()
            encoded = json.dumps(messages)
            assert "tool-state" in encoded and "answer-state" in encoded
            assert len(requests) == 2

    asyncio.run(scenario())
