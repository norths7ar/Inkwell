"""Tests for universal model discovery parsing and refreshable models.dev catalog cache."""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from backend.api.app import create_app
from backend.config import AppConfig
from backend.llm._json import JsonObject
from backend.llm.catalog import MODELS_DEV_API_URL, ModelCatalogCache
from backend.llm.client import LLMClient
from backend.llm.model_params import resolve_model_params
from backend.llm.types import AuthStyle, DiscoveredModel, EndpointConfig, WireProtocol
from backend.storage.migrations import MIGRATIONS, migrate
from backend.storage.models import ProtocolMode, ProtocolName
from backend.storage.repos import config as model_config


@pytest.mark.parametrize(
    "version, expected_mode", [(5, ProtocolMode.INHERIT), (6, ProtocolMode.AUTO)]
)
def test_protocol_selection_migration(version: int, expected_mode: ProtocolMode) -> None:
    with sqlite3.connect(":memory:", isolation_level=None) as conn:
        conn.row_factory = sqlite3.Row
        for target, statements in MIGRATIONS:
            if target <= version:
                for statement in statements:
                    conn.execute(statement)
        conn.execute(f"PRAGMA user_version = {version}")
        provider = model_config.create_provider(
            conn,
            name="Provider",
            base_url="https://mock.invalid",
            default_protocol=ProtocolName.OPENAI_CHAT,
        )
        conn.execute(
            """INSERT INTO models (
                id, provider_id, model_id, display_name, protocol_override, context_window,
                created_at, updated_at
            ) VALUES ('model', ?, 'model', 'Model', NULL, 64000, '', '')""",
            (provider.id,),
        )
        migrate(conn)
        model = model_config.get_model(conn, "model")
        assert model is not None
        assert model.protocol_mode == expected_mode
        resolved = resolve_model_params(model, provider, None)
        assert resolved.effective_protocol == ProtocolName.OPENAI_CHAT
        assert resolved.effective_context_window == 64000
        assert resolved.context_window_source == "user"


def _sample_models_dev() -> JsonObject:
    return {
        "opencode": {
            "npm": "@ai-sdk/openai-compatible",
            "models": {
                "claude-sonnet-4-5": {
                    "id": "claude-sonnet-4-5",
                    "name": "Claude Sonnet 4.5",
                    "limit": {"context": 200000, "output": 64000},
                    "provider": {"npm": "@ai-sdk/anthropic"},
                },
                "gpt-5": {
                    "id": "gpt-5",
                    "name": "GPT-5",
                    "limit": {"context": 400000, "output": 128000},
                    "provider": {"npm": "@ai-sdk/openai"},
                },
            },
        },
        "anthropic": {
            "npm": "@ai-sdk/anthropic",
            "models": {
                "claude-sonnet-4-5": {
                    "id": "claude-sonnet-4-5",
                    "name": "Claude Sonnet 4.5",
                    "limit": {"context": 200000, "output": 64000},
                }
            },
        },
        "provider-a": {
            "npm": "@ai-sdk/openai-compatible",
            "models": {
                "shared-model": {
                    "id": "shared-model",
                    "name": "Shared Model",
                    "limit": {"context": 128000, "output": 16384},
                }
            },
        },
        "provider-b": {
            "npm": "@ai-sdk/openai-compatible",
            "models": {
                "shared-model": {
                    "id": "shared-model",
                    "name": "Shared Model",
                    "limit": {"context": 256000, "output": 16384},
                }
            },
        },
        "google": {
            "npm": "@ai-sdk/google",
            "models": {
                "gemini-2.5-pro": {
                    "id": "gemini-2.5-pro",
                    "name": "Gemini 2.5 Pro",
                    "limit": {"context": 1048576, "output": 65536},
                }
            },
        },
    }


@pytest.mark.anyio
async def test_universal_discovery_extracts_limits_protocol_and_flags_non_chat() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/models")
        return httpx.Response(
            200,
            json={
                "data": [
                    {
                        "id": "whisper-1",
                        "owned_by": "openai",
                    },
                    {
                        "id": "openrouter-model",
                        "name": "OpenRouter Model",
                        "context_length": 256000,
                        "top_provider": {"max_completion_tokens": 32768},
                    },
                    {
                        "id": "opencode-model",
                        "displayName": "OpenCode Model",
                        "api": "openai-responses",
                        "limit": {"context": 1000000, "output": 65536},
                    },
                    {
                        "id": "text-embedding-3-large",
                        "owned_by": "openai",
                    },
                ]
            },
        )

    endpoint = EndpointConfig(
        base_url="https://example.test/v1",
        default_protocol=WireProtocol.OPENAI_CHAT,
        api_key="test-key",
    )
    async with LLMClient(transport=httpx.MockTransport(handler)) as client:
        models = await client.list_models(endpoint)

    assert [m.model_id for m in models] == [
        "openrouter-model",
        "opencode-model",
        "whisper-1",
        "text-embedding-3-large",
    ]
    assert models[0].supported is True
    assert models[0].context_window == 256000
    assert models[0].max_output_tokens == 32768
    assert models[0].recommended_protocol is None
    assert models[1].context_window == 1000000
    assert models[1].max_output_tokens == 65536
    assert models[1].recommended_protocol == WireProtocol.OPENAI_RESPONSES
    assert models[2].supported is False
    assert models[3].supported is False


@pytest.mark.anyio
async def test_gemini_discovery_extracts_token_limits() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "models": [
                    {
                        "name": "models/gemini-2.5-pro",
                        "displayName": "Gemini 2.5 Pro",
                        "supportedGenerationMethods": ["generateContent"],
                        "inputTokenLimit": 1048576,
                        "outputTokenLimit": 65536,
                    }
                ]
            },
        )

    endpoint = EndpointConfig(
        base_url="https://generativelanguage.googleapis.com/v1beta",
        default_protocol=WireProtocol.GOOGLE_GENAI,
        api_key="test-key",
        auth_style=AuthStyle.X_GOOG_API_KEY,
    )
    async with LLMClient(transport=httpx.MockTransport(handler)) as client:
        models = await client.list_models(endpoint)

    assert len(models) == 1
    assert models[0].model_id == "gemini-2.5-pro"
    assert models[0].context_window == 1048576
    assert models[0].max_output_tokens == 65536


@pytest.mark.anyio
async def test_catalog_cross_provider_safety_conflict_resolution_and_single_load(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cache = ModelCatalogCache(tmp_path / "model-catalog.json")
    initial = cache.status()
    assert initial.cached is False
    assert initial.stale is True

    def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == MODELS_DEV_API_URL
        return httpx.Response(200, json=_sample_models_dev())

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        updated = await cache.refresh(http)

    assert updated.cached is True
    assert updated.stale is False
    assert updated.provider_count == 5
    assert updated.model_count == 6

    # Direct provider match (opencode-zen -> opencode) may recommend explicit model protocol.
    opencode_claude = cache.lookup("opencode-zen", "claude-sonnet-4-5")
    assert opencode_claude is not None
    assert opencode_claude.match_scope == "provider"
    assert opencode_claude.source_provider == "opencode"
    assert opencode_claude.protocol == WireProtocol.ANTHROPIC_MESSAGES

    inherited = cache.lookup("gemini", "gemini-2.5-pro")
    assert inherited is not None and inherited.protocol is None
    assert inherited.context_window is not None

    # Cross-provider match (custom openai-chat gateway with claude model) MUST NOT recommend protocol.
    custom_claude = cache.lookup("custom", "claude-sonnet-4-5")
    assert custom_claude is not None
    assert custom_claude.match_scope == "cross_provider"
    assert custom_claude.protocol is None
    assert custom_claude.context_window == 200000
    assert custom_claude.max_output_tokens == 64000

    # Cross-provider conflict: provider-a (128k) vs provider-b (256k) disagrees on context_window,
    # agrees on max_output_tokens (16384) -> conflicting field stays None, agreed field is kept.
    conflicted = cache.lookup("custom", "shared-model")
    assert conflicted is not None
    assert conflicted.match_scope == "cross_provider"
    assert conflicted.context_window is None
    assert conflicted.max_output_tokens == 16384
    assert conflicted.protocol is None

    # Batch enrich_discovered reads the cache file at most once for N models.
    load_calls = 0
    original_load = cache._load

    def counted_load() -> JsonObject | None:
        nonlocal load_calls
        load_calls += 1
        return original_load()

    monkeypatch.setattr(cache, "_load", counted_load)
    batch = [
        DiscoveredModel(model_id="claude-sonnet-4-5", display_name="claude-sonnet-4-5"),
        DiscoveredModel(model_id="shared-model", display_name="shared-model"),
        DiscoveredModel(model_id="google/gemini-2.5-pro", display_name="google/gemini-2.5-pro"),
    ]
    enriched = cache.enrich_discovered("custom", batch)
    assert load_calls == 1
    assert all(item.recommended_protocol is None for item in enriched)
    assert enriched[0].catalog_match == "cross_provider"
    assert enriched[1].context_window is None
    assert enriched[1].max_output_tokens == 16384
    assert enriched[2].context_window == 1048576

    # Stale detection after 14 days.
    future = datetime.now(UTC) + timedelta(days=15)
    assert cache.status(now=future).stale is True


def test_catalog_endpoints_and_discover_precedence(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if str(request.url) == MODELS_DEV_API_URL:
            return httpx.Response(200, json=_sample_models_dev())
        if request.url.path.endswith("/models"):
            return httpx.Response(
                200,
                json={
                    "data": [
                        {
                            "id": "claude-sonnet-4-5",
                        },
                        {
                            "id": "gpt-5",
                            "context_window": 512000,
                            "protocol": "openai-chat",
                        },
                    ]
                },
            )
        return httpx.Response(404)

    config = AppConfig(data_dir=tmp_path, host="127.0.0.1", port=0)
    app = create_app(config=config, llm=LLMClient(transport=httpx.MockTransport(handler)))

    with TestClient(app) as client:
        status_before = client.get("/api/model-catalog")
        assert status_before.status_code == 200
        assert status_before.json()["cached"] is False

        refreshed = client.post("/api/model-catalog/refresh")
        assert refreshed.status_code == 200
        assert refreshed.json()["cached"] is True

        # Custom provider lookup: cross-provider match must never recommend anthropic-messages.
        custom_lookup = client.get(
            "/api/model-catalog/lookup",
            params={
                "model_id": "claude-sonnet-4-5",
                "preset": "custom",
                "default_protocol": "openai-chat",
            },
        )
        assert custom_lookup.status_code == 200
        custom_data = custom_lookup.json()
        assert custom_data["found"] is True
        assert custom_data["match_scope"] == "cross_provider"
        assert custom_data["recommended_protocol"] is None
        assert custom_data["context_window"] == 200000

        # OpenCode Zen provider: direct provider match recommends anthropic-messages for claude.
        provider_resp = client.post(
            "/api/providers",
            json={
                "name": "OpenCode Zen",
                "preset": "opencode-zen",
                "base_url": "https://opencode.ai/zen/v1",
                "default_protocol": "openai-chat",
                "api_key": "sk-test",
            },
        )
        assert provider_resp.status_code == 201
        provider_id = provider_resp.json()["id"]

        discover_resp = client.post(f"/api/providers/{provider_id}/discover")
        assert discover_resp.status_code == 200
        items = {item["model_id"]: item for item in discover_resp.json()}

        assert items["claude-sonnet-4-5"]["context_window"] == 200000
        assert items["claude-sonnet-4-5"]["max_output_tokens"] == 64000
        assert items["claude-sonnet-4-5"]["recommended_protocol"] == "anthropic-messages"
        assert items["claude-sonnet-4-5"]["catalog_match"] == "provider"
        assert items["claude-sonnet-4-5"]["catalog_provider"] == "opencode"

        # Server-disclosed context_window (512000) and protocol ("openai-chat") beat cache
        # (400000 / openai-responses); cache still fills missing max_output_tokens (128000).
        assert items["gpt-5"]["context_window"] == 512000
        assert items["gpt-5"]["max_output_tokens"] == 128000
        assert items["gpt-5"]["recommended_protocol"] == "openai-chat"

        imported = client.post(
            f"/api/providers/{provider_id}/models",
            json={"model_id": "gpt-5", "protocol_override": items["gpt-5"]["recommended_protocol"]},
        )
        assert imported.status_code == 201
        client.patch(f"/api/providers/{provider_id}", json={"default_protocol": "openai-responses"})
        saved = client.get(f"/api/models/{imported.json()['id']}")
        assert saved.json()["protocol_override"] == "openai-chat"

        equal_default = client.get(
            "/api/model-catalog/lookup",
            params={
                "model_id": "claude-sonnet-4-5",
                "preset": "opencode-zen",
                "default_protocol": "anthropic-messages",
            },
        )
        assert equal_default.json()["recommended_protocol"] == "anthropic-messages"

        cached_file = tmp_path / "model-catalog.json"
        assert cached_file.is_file()
        assert json.loads(cached_file.read_text(encoding="utf-8"))["source"] == "models.dev"


@pytest.mark.anyio
async def test_unsupported_disclosed_protocol_isolated_without_fallback(tmp_path: Path) -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "data": [
                    {"id": "claude-sonnet-4-5", "protocol": "unsupported-wire"},
                    {"id": "valid", "protocol": "openai-chat"},
                    {"id": "audio", "api": "realtime", "type": "audio"},
                ]
            },
        )

    async with LLMClient(transport=httpx.MockTransport(handler)) as client:
        models = await client.list_models(
            EndpointConfig("https://mock.invalid/v1", WireProtocol.OPENAI_CHAT)
        )
    assert models[0].model_id == "valid" and models[0].supported
    assert all(not model.supported for model in models[1:])
    assert "unsupported-wire" in models[1].unsupported_reason
    assert "realtime" in models[2].unsupported_reason
    cache = ModelCatalogCache(tmp_path / "catalog.json")
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _: httpx.Response(200, json=_sample_models_dev()))
    ) as client:
        await cache.refresh(client)
    enriched = cache.enrich_discovered("opencode-zen", models)
    assert enriched[1].recommended_protocol is None
    assert not enriched[1].supported
    assert enriched[1].context_window is None


def test_legacy_catalog_requires_refresh(tmp_path: Path) -> None:
    path = tmp_path / "catalog.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "providers": {"deepseek": {"deepseek-chat": {"protocol": "openai-chat"}}},
            }
        ),
        encoding="utf-8",
    )
    cache = ModelCatalogCache(path)
    assert not cache.status().cached
    assert cache.status().stale
    assert cache.lookup("deepseek", "deepseek-chat") is None
    assert path.is_file()  # Leave the old file untouched until a successful refresh.


def test_three_layer_auto_refresh_and_user_override_prompt_lifecycle(tmp_path: Path) -> None:
    catalog_ctx = 200000
    catalog_out = 64000
    server_ctx = 512000

    def handler(request: httpx.Request) -> httpx.Response:
        if str(request.url) == MODELS_DEV_API_URL:
            return httpx.Response(
                200,
                json={
                    "opencode": {
                        "npm": "@ai-sdk/openai-compatible",
                        "models": {
                            "claude-sonnet-4-5": {
                                "id": "claude-sonnet-4-5",
                                "name": "Claude Sonnet 4.5",
                                "limit": {"context": catalog_ctx, "output": catalog_out},
                                "provider": {"npm": "@ai-sdk/anthropic"},
                            },
                            "gpt-5": {
                                "id": "gpt-5",
                                "name": "GPT-5",
                                "limit": {"context": 400000, "output": 128000},
                                "provider": {"npm": "@ai-sdk/openai"},
                            },
                        },
                    }
                },
            )
        if request.url.path.endswith("/models"):
            return httpx.Response(
                200,
                json={
                    "data": [
                        {"id": "claude-sonnet-4-5"},
                        {"id": "gpt-5", "context_window": server_ctx, "protocol": "openai-chat"},
                    ]
                },
            )
        return httpx.Response(404)

    config = AppConfig(data_dir=tmp_path, host="127.0.0.1", port=0)
    app = create_app(config=config, llm=LLMClient(transport=httpx.MockTransport(handler)))

    with TestClient(app) as client:
        assert client.post("/api/model-catalog/refresh").status_code == 200
        provider_id = client.post(
            "/api/providers",
            json={
                "name": "OpenCode Zen",
                "preset": "opencode-zen",
                "base_url": "https://opencode.ai/zen/v1",
                "default_protocol": "openai-chat",
                "api_key": "sk-test",
            },
        ).json()["id"]

        # Import catalog-backed model with no user overrides -> follows catalog automatically.
        claude_id = client.post(
            f"/api/providers/{provider_id}/models",
            json={"model_id": "claude-sonnet-4-5"},
        ).json()["id"]
        claude = client.get(f"/api/models/{claude_id}").json()
        assert claude["context_window"] is None
        assert claude["effective_context_window"] == 200000
        assert claude["context_window_source"] == "catalog"
        assert claude["effective_protocol"] == "anthropic-messages"
        assert claude["pending_changes"] == []

        inherited = client.patch(
            f"/api/models/{claude_id}",
            json={"protocol_mode": "inherit", "protocol_override": None},
        ).json()
        assert inherited["protocol_mode"] == "inherit"
        assert inherited["effective_protocol"] == "openai-chat"
        client.patch(f"/api/providers/{provider_id}", json={"default_protocol": "openai-responses"})
        inherited = client.get(f"/api/models/{claude_id}").json()
        assert inherited["effective_protocol"] == "openai-responses"
        automatic = client.patch(f"/api/models/{claude_id}", json={"protocol_mode": "auto"}).json()
        assert automatic["effective_protocol"] == "anthropic-messages"
        assert automatic["protocol_source"] == "catalog"

        custom_provider = client.post(
            "/api/providers",
            json={"name": "Gateway", "preset": "custom", "base_url": "https://mock.invalid/v1"},
        ).json()["id"]
        imported = client.post(
            f"/api/providers/{custom_provider}/models",
            json={"model_id": "claude-sonnet-4-5", "protocol_mode": "auto"},
        ).json()
        assert imported["effective_context_window"] == 200000
        assert imported["effective_max_output_tokens"] == 64000
        assert imported["context_window_source"] == "catalog"
        assert imported["effective_protocol"] == "openai-chat"

        # Catalog refresh automatically updates non-overridden fields.
        catalog_ctx = 400000
        catalog_out = 128000
        assert client.post("/api/model-catalog/refresh").status_code == 200
        claude = client.get(f"/api/models/{claude_id}").json()
        assert claude["effective_context_window"] == 400000
        assert claude["effective_max_output_tokens"] == 128000
        assert claude["pending_changes"] == []

        # Import server-disclosed model -> server facts beat catalog and auto-refresh on discover.
        gpt_id = client.post(
            f"/api/providers/{provider_id}/models",
            json={
                "model_id": "gpt-5",
                "discovered_context_window": 512000,
                "discovered_protocol": "openai-chat",
            },
        ).json()["id"]
        gpt = client.get(f"/api/models/{gpt_id}").json()
        assert gpt["effective_context_window"] == 512000
        assert gpt["context_window_source"] == "server"
        assert gpt["effective_protocol"] == "openai-chat"
        assert gpt["protocol_source"] == "server"

        server_ctx = 1000000
        assert client.post(f"/api/providers/{provider_id}/discover").status_code == 200
        gpt = client.get(f"/api/models/{gpt_id}").json()
        assert gpt["effective_context_window"] == 1000000
        assert gpt["context_window_source"] == "server"
        assert gpt["pending_changes"] == []

        # User manually overrides context_window -> current upstream (400000) is acked, no prompt.
        patched = client.patch(
            f"/api/models/{claude_id}",
            json={"context_window": 100000},
        ).json()
        assert patched["effective_context_window"] == 100000
        assert patched["context_window_source"] == "user"
        assert patched["pending_changes"] == []

        # Upstream changes to 500000 -> user override preserved, single pending change surfaced.
        catalog_ctx = 500000
        assert client.post("/api/model-catalog/refresh").status_code == 200
        claude = client.get(f"/api/models/{claude_id}").json()
        assert claude["effective_context_window"] == 100000
        assert claude["pending_changes"] == [
            {
                "field": "context_window",
                "user_value": 100000,
                "upstream_value": 500000,
                "upstream_source": "catalog",
            }
        ]

        # User chooses "keep" -> prompt dismissed until upstream changes again.
        renamed = client.patch(
            f"/api/models/{claude_id}", json={"display_name": "Writing model"}
        ).json()
        assert renamed["display_name"] == "Writing model"
        assert renamed["pending_changes"] == claude["pending_changes"]
        kept = client.post(
            f"/api/models/{claude_id}/resolve-change",
            json={
                "field": "context_window",
                "action": "keep",
                "expected_user_value": 100000,
                "expected_upstream_value": 500000,
                "expected_upstream_source": "catalog",
            },
        ).json()
        assert kept["effective_context_window"] == 100000
        assert kept["pending_changes"] == []
        assert client.get(f"/api/models/{claude_id}").json()["pending_changes"] == []

        # Upstream changes again to 1000000 -> prompts again; user chooses "accept" -> follows catalog.
        catalog_ctx = 1000000
        assert client.post("/api/model-catalog/refresh").status_code == 200
        claude = client.get(f"/api/models/{claude_id}").json()
        assert len(claude["pending_changes"]) == 1
        assert claude["pending_changes"][0]["upstream_value"] == 1000000

        stale = client.post(
            f"/api/models/{claude_id}/resolve-change",
            json={
                "field": "context_window",
                "action": "keep",
                "expected_user_value": 100000,
                "expected_upstream_value": 500000,
                "expected_upstream_source": "catalog",
            },
        )
        assert stale.status_code == 409
        current = client.get(f"/api/models/{claude_id}").json()
        assert current["pending_changes"] == claude["pending_changes"]

        accepted = client.post(
            f"/api/models/{claude_id}/resolve-change",
            json={
                "field": "context_window",
                "action": "accept",
                "expected_user_value": 100000,
                "expected_upstream_value": 1000000,
                "expected_upstream_source": "catalog",
            },
        ).json()
        assert accepted["context_window"] is None
        assert accepted["effective_context_window"] == 1000000
        assert accepted["context_window_source"] == "catalog"
        assert accepted["pending_changes"] == []
