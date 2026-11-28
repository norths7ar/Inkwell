"""Provider, model, prompt and global settings endpoints."""

from __future__ import annotations

import json
from typing import Literal

from fastapi import APIRouter, HTTPException, Response
from pydantic import Field

from backend.agent.context import GLOBAL_RULES_SETTING_KEY
from backend.agent.prompts import DEFAULT_MODE_PROMPTS, mode_prompt_setting_key, resolve_mode_prompt
from backend.api.content import Input, required, supplied
from backend.api.runtime import ApiRuntime
from backend.llm.catalog import CatalogSnapshot, CatalogStatus, ModelCatalogCache
from backend.llm.model_params import (
    ParamField,
    ParamSnapshot,
    ParamSource,
    UpstreamSource,
    dump_param_snapshot,
    parse_param_snapshot,
    resolve_model_params,
    snapshot_from_discovered,
)
from backend.llm.presets import PRESETS, ProviderPreset, build_endpoint_from_preset
from backend.llm.types import DiscoveredModel, WireProtocol
from backend.storage.migrations import ensure_transaction
from backend.storage.models import ModelRecord, ProtocolMode, ProtocolName, Provider, SessionMode
from backend.storage.repos import config
from backend.storage.repos.content import UNSET


class ProviderInput(Input):
    name: str = Field(default="自定义提供商", min_length=1)
    base_url: str = ""
    preset: str = ""
    discovery_url: str = ""
    default_protocol: ProtocolName = ProtocolName.OPENAI_CHAT
    api_key: str = ""
    headers: dict[str, str] = Field(default_factory=dict)
    enabled: bool = True


class ProviderView(Input):
    id: str
    name: str
    preset: str
    base_url: str
    discovery_url: str
    default_protocol: ProtocolName
    api_key_configured: bool
    headers_configured: bool
    enabled: bool
    created_at: str
    updated_at: str


def provider_view(provider: Provider) -> ProviderView:
    return ProviderView(
        id=provider.id,
        name=provider.name,
        preset=provider.preset,
        base_url=provider.base_url,
        discovery_url=provider.discovery_url,
        default_protocol=provider.default_protocol,
        api_key_configured=bool(provider.api_key_encrypted),
        headers_configured=bool(provider.headers_encrypted),
        enabled=provider.enabled,
        created_at=provider.created_at,
        updated_at=provider.updated_at,
    )


class ModelInput(Input):
    model_id: str = Field(min_length=1)
    display_name: str = ""
    protocol_override: ProtocolName | None = None
    protocol_mode: ProtocolMode | None = None
    context_window: int | None = Field(default=None, gt=0)
    max_output_tokens: int | None = Field(default=None, gt=0)
    capabilities_json: str = "{}"
    enabled: bool = True
    discovered_protocol: ProtocolName | None = None
    discovered_context_window: int | None = Field(default=None, gt=0)
    discovered_max_output_tokens: int | None = Field(default=None, gt=0)


class ModelPatch(Input):
    display_name: str = ""
    protocol_override: ProtocolName | None = None
    protocol_mode: ProtocolMode | None = None
    context_window: int | None = Field(default=None, gt=0)
    max_output_tokens: int | None = Field(default=None, gt=0)
    capabilities_json: str = "{}"
    enabled: bool = True


class ResolveChangeInput(Input):
    field: ParamField
    action: Literal["accept", "keep"]
    expected_upstream_value: str | int
    expected_upstream_source: UpstreamSource
    expected_user_value: str | int


class UpstreamChangeView(Input):
    field: ParamField
    user_value: str | int
    upstream_value: str | int
    upstream_source: UpstreamSource


class ModelView(Input):
    id: str
    provider_id: str
    model_id: str
    display_name: str
    protocol_override: ProtocolName | None
    protocol_mode: ProtocolMode
    context_window: int | None
    max_output_tokens: int | None
    capabilities_json: str
    enabled: bool
    created_at: str
    updated_at: str
    effective_protocol: ProtocolName
    effective_protocol_override: ProtocolName | None
    protocol_source: ParamSource
    effective_context_window: int | None
    context_window_source: ParamSource
    effective_max_output_tokens: int | None
    max_output_tokens_source: ParamSource
    upstream_protocol: ProtocolName | None
    upstream_protocol_source: UpstreamSource | None
    upstream_context_window: int | None
    upstream_context_window_source: UpstreamSource | None
    upstream_max_output_tokens: int | None
    upstream_max_output_tokens_source: UpstreamSource | None
    pending_changes: list[UpstreamChangeView]


def model_view(
    model: ModelRecord,
    provider: Provider,
    catalog: CatalogSnapshot | None,
) -> ModelView:
    resolved = resolve_model_params(model, provider, catalog)
    return ModelView(
        id=model.id,
        provider_id=model.provider_id,
        model_id=model.model_id,
        display_name=model.display_name,
        protocol_override=model.protocol_override,
        protocol_mode=model.protocol_mode,
        context_window=model.context_window,
        max_output_tokens=model.max_output_tokens,
        capabilities_json=model.capabilities_json,
        enabled=model.enabled,
        created_at=model.created_at,
        updated_at=model.updated_at,
        effective_protocol=resolved.effective_protocol,
        effective_protocol_override=resolved.effective_protocol_override,
        protocol_source=resolved.protocol_source,
        effective_context_window=resolved.effective_context_window,
        context_window_source=resolved.context_window_source,
        effective_max_output_tokens=resolved.effective_max_output_tokens,
        max_output_tokens_source=resolved.max_output_tokens_source,
        upstream_protocol=resolved.upstream_protocol,
        upstream_protocol_source=resolved.upstream_protocol_source,
        upstream_context_window=resolved.upstream_context_window,
        upstream_context_window_source=resolved.upstream_context_window_source,
        upstream_max_output_tokens=resolved.upstream_max_output_tokens,
        upstream_max_output_tokens_source=resolved.upstream_max_output_tokens_source,
        pending_changes=[
            UpstreamChangeView(
                field=item.field,
                user_value=item.user_value,
                upstream_value=item.upstream_value,
                upstream_source=item.upstream_source,
            )
            for item in resolved.pending_changes
        ],
    )


class SettingsInput(Input):
    default_model_id: str | None = None
    background_model_id: str | None = None
    global_rules_markdown: str = ""


class PromptInput(Input):
    markdown: str


class PromptView(Input):
    mode: SessionMode
    markdown: str
    default_markdown: str


class CatalogLookupView(Input):
    found: bool
    display_name: str = ""
    context_window: int | None = None
    max_output_tokens: int | None = None
    recommended_protocol: ProtocolName | None = None
    match_scope: str = "none"
    source_provider: str | None = None
    stale: bool = False


def _protocol_selection(
    body: ModelInput | ModelPatch, old: ModelRecord | None = None
) -> tuple[ProtocolMode, ProtocolName | None]:
    fields = body.model_fields_set
    protocol = (
        body.protocol_override
        if "protocol_override" in fields or old is None
        else old.protocol_override
    )
    if "protocol_mode" in fields:
        if body.protocol_mode is None:
            raise HTTPException(422, "Protocol mode must be auto, inherit, or override")
        mode = body.protocol_mode
    elif "protocol_override" in fields:
        mode = ProtocolMode.OVERRIDE if protocol is not None else ProtocolMode.INHERIT
    else:
        mode = old.protocol_mode if old is not None else ProtocolMode.AUTO
    if mode == ProtocolMode.OVERRIDE:
        if protocol is None:
            raise HTTPException(422, "An override protocol is required")
        return mode, protocol
    return mode, None


def create_router(runtime: ApiRuntime) -> APIRouter:
    router = APIRouter()

    def changed(resource: str, resource_id: str | None = None) -> None:
        runtime.events.publish(
            "content_changed", {"book_id": None, "resource": resource, "resource_id": resource_id}
        )

    @router.get("/provider-presets")
    async def presets() -> list[ProviderPreset]:
        return list(PRESETS.values())

    @router.get("/providers")
    async def providers() -> list[ProviderView]:
        return [provider_view(item) for item in config.list_providers(runtime.conn)]

    @router.post("/providers", status_code=201)
    async def add_provider(body: ProviderInput) -> ProviderView:
        endpoint = build_endpoint_from_preset(
            body.preset or "custom",
            base_url=body.base_url or None,
            discovery_url=body.discovery_url or None,
            default_protocol=(
                WireProtocol(body.default_protocol.value)
                if "default_protocol" in body.model_fields_set
                else None
            ),
        )
        item = config.create_provider(
            runtime.conn,
            name=body.name,
            preset=body.preset,
            base_url=endpoint.base_url,
            discovery_url=endpoint.discovery_url,
            default_protocol=ProtocolName(endpoint.default_protocol.value),
            enabled=body.enabled,
            api_key_encrypted=runtime.cipher.encrypt(body.api_key),
            headers_encrypted=runtime.cipher.encrypt(
                json.dumps(body.headers) if body.headers else ""
            ),
        )
        changed("provider", item.id)
        return provider_view(item)

    @router.get("/providers/{provider_id}")
    async def provider(provider_id: str) -> ProviderView:
        return provider_view(required(config.get_provider(runtime.conn, provider_id)))

    @router.patch("/providers/{provider_id}")
    async def edit_provider(provider_id: str, body: ProviderInput) -> ProviderView:
        old = required(config.get_provider(runtime.conn, provider_id))
        fields = body.model_fields_set
        build_endpoint_from_preset(
            (body.preset if "preset" in fields else old.preset) or "custom",
            base_url=(body.base_url if "base_url" in fields else old.base_url) or None,
        )
        item = config.update_provider(
            runtime.conn,
            provider_id,
            name=supplied(body, "name", body.name),
            preset=supplied(body, "preset", body.preset),
            base_url=supplied(body, "base_url", body.base_url),
            discovery_url=supplied(body, "discovery_url", body.discovery_url),
            default_protocol=supplied(body, "default_protocol", body.default_protocol),
            enabled=supplied(body, "enabled", body.enabled),
            api_key_encrypted=(
                runtime.cipher.encrypt(body.api_key)
                if "api_key" in body.model_fields_set
                else UNSET
            ),
            headers_encrypted=(
                runtime.cipher.encrypt(json.dumps(body.headers) if body.headers else "")
                if "headers" in body.model_fields_set
                else UNSET
            ),
        )
        changed("provider", item.id)
        return provider_view(item)

    @router.delete("/providers/{provider_id}", status_code=204)
    async def remove_provider(provider_id: str) -> Response:
        required(config.get_provider(runtime.conn, provider_id))
        config.delete_provider(runtime.conn, provider_id)
        changed("provider", provider_id)
        return Response(status_code=204)

    @router.post("/providers/{provider_id}/discover")
    async def discover(provider_id: str) -> list[DiscoveredModel]:
        provider = required(config.get_provider(runtime.conn, provider_id))
        # Discovery previews new models and refreshes pure server snapshots on existing models.
        endpoint = runtime.endpoint(provider)
        discovered = await runtime.llm.list_models(endpoint)
        server_by_id = {
            item.model_id: dump_param_snapshot(snapshot_from_discovered(item))
            for item in discovered
            if item.supported
        }
        snapshot_updated = False
        for existing in config.list_models(runtime.conn, provider_id=provider_id):
            next_snapshot = server_by_id.get(existing.model_id)
            if next_snapshot is not None and next_snapshot != existing.discovered_json:
                config.update_model_snapshots(
                    runtime.conn,
                    existing.id,
                    discovered_json=next_snapshot,
                )
                snapshot_updated = True
        if snapshot_updated:
            changed("model")
        catalog = ModelCatalogCache(runtime.config.catalog_path)
        return catalog.enrich_discovered(provider.preset, discovered)

    @router.get("/model-catalog")
    async def model_catalog_status() -> CatalogStatus:
        return ModelCatalogCache(runtime.config.catalog_path).status()

    @router.post("/model-catalog/refresh")
    async def refresh_model_catalog() -> CatalogStatus:
        status = await ModelCatalogCache(runtime.config.catalog_path).refresh(
            runtime.llm.http_client
        )
        changed("model_catalog")
        return status

    @router.get("/model-catalog/lookup")
    async def lookup_model_catalog(
        model_id: str,
        preset: str = "",
    ) -> CatalogLookupView:
        entry = ModelCatalogCache(runtime.config.catalog_path).lookup(preset, model_id)
        if entry is None:
            return CatalogLookupView(found=False)
        recommended: ProtocolName | None = None
        if entry.match_scope == "provider" and entry.protocol is not None:
            recommended = ProtocolName(entry.protocol.value)
        return CatalogLookupView(
            found=True,
            display_name=entry.display_name,
            context_window=entry.context_window,
            max_output_tokens=entry.max_output_tokens,
            recommended_protocol=recommended,
            match_scope=entry.match_scope,
            source_provider=entry.source_provider,
            stale=entry.stale,
        )

    @router.get("/models")
    async def models(provider_id: str | None = None) -> list[ModelView]:
        providers_by_id = {item.id: item for item in config.list_providers(runtime.conn)}
        catalog = ModelCatalogCache(runtime.config.catalog_path).snapshot()
        return [
            model_view(item, providers_by_id[item.provider_id], catalog)
            for item in config.list_models(runtime.conn, provider_id=provider_id)
            if item.provider_id in providers_by_id
        ]

    @router.post("/providers/{provider_id}/models", status_code=201)
    async def add_model(provider_id: str, body: ModelInput) -> ModelView:
        provider_row = required(config.get_provider(runtime.conn, provider_id))
        protocol_mode, protocol_override = _protocol_selection(body)
        if any(
            item.model_id == body.model_id
            for item in config.list_models(runtime.conn, provider_id=provider_id)
        ):
            raise HTTPException(409, "Model already exists; use PATCH to change it")
        discovered_json = dump_param_snapshot(
            ParamSnapshot(
                protocol=body.discovered_protocol,
                context_window=body.discovered_context_window,
                max_output_tokens=body.discovered_max_output_tokens,
            )
        )
        catalog = ModelCatalogCache(runtime.config.catalog_path).snapshot()
        draft = ModelRecord(
            id="",
            provider_id=provider_id,
            model_id=body.model_id,
            display_name=body.display_name or body.model_id,
            protocol_override=protocol_override,
            protocol_mode=protocol_mode,
            context_window=body.context_window,
            max_output_tokens=body.max_output_tokens,
            capabilities_json=body.capabilities_json,
            enabled=body.enabled,
            created_at="",
            updated_at="",
            discovered_json=discovered_json,
            acked_upstream_json="{}",
        )
        resolved_draft = resolve_model_params(draft, provider_row, catalog)
        acked_json = dump_param_snapshot(
            ParamSnapshot(
                protocol=(
                    resolved_draft.upstream_protocol if protocol_override is not None else None
                ),
                context_window=(
                    resolved_draft.upstream_context_window
                    if body.context_window is not None
                    else None
                ),
                max_output_tokens=(
                    resolved_draft.upstream_max_output_tokens
                    if body.max_output_tokens is not None
                    else None
                ),
            )
        )
        item = config.upsert_model(
            runtime.conn,
            provider_id=provider_id,
            model_id=body.model_id,
            display_name=body.display_name or body.model_id,
            protocol_override=protocol_override,
            protocol_mode=protocol_mode,
            context_window=body.context_window,
            max_output_tokens=body.max_output_tokens,
            capabilities_json=body.capabilities_json,
            enabled=body.enabled,
            discovered_json=discovered_json,
            acked_upstream_json=acked_json,
        )
        changed("model", item.id)
        return model_view(item, provider_row, catalog)

    @router.get("/models/{model_id}")
    async def model(model_id: str) -> ModelView:
        item = required(config.get_model(runtime.conn, model_id))
        provider_row = required(config.get_provider(runtime.conn, item.provider_id))
        catalog = ModelCatalogCache(runtime.config.catalog_path).snapshot()
        return model_view(item, provider_row, catalog)

    @router.patch("/models/{model_id}")
    async def edit_model(model_id: str, body: ModelPatch) -> ModelView:
        old = required(config.get_model(runtime.conn, model_id))
        provider_row = required(config.get_provider(runtime.conn, old.provider_id))
        fields = body.model_fields_set
        protocol_mode, next_protocol = _protocol_selection(body, old)
        next_ctx = body.context_window if "context_window" in fields else old.context_window
        next_out = (
            body.max_output_tokens if "max_output_tokens" in fields else old.max_output_tokens
        )
        catalog = ModelCatalogCache(runtime.config.catalog_path).snapshot()
        resolved_old = resolve_model_params(old, provider_row, catalog)
        acked = parse_param_snapshot(old.acked_upstream_json)
        acked_protocol = acked.protocol
        acked_ctx = acked.context_window
        acked_out = acked.max_output_tokens
        if "protocol_override" in fields or "protocol_mode" in fields:
            acked_protocol = resolved_old.upstream_protocol if next_protocol is not None else None
        if "context_window" in fields:
            acked_ctx = resolved_old.upstream_context_window if next_ctx is not None else None
        if "max_output_tokens" in fields:
            acked_out = resolved_old.upstream_max_output_tokens if next_out is not None else None
        item = config.upsert_model(
            runtime.conn,
            provider_id=old.provider_id,
            model_id=old.model_id,
            display_name=body.display_name if "display_name" in fields else old.display_name,
            protocol_override=next_protocol,
            protocol_mode=protocol_mode,
            context_window=next_ctx,
            max_output_tokens=next_out,
            capabilities_json=(
                body.capabilities_json if "capabilities_json" in fields else old.capabilities_json
            ),
            enabled=body.enabled if "enabled" in fields else old.enabled,
            acked_upstream_json=dump_param_snapshot(
                ParamSnapshot(
                    protocol=acked_protocol,
                    context_window=acked_ctx,
                    max_output_tokens=acked_out,
                )
            ),
        )
        changed("model", item.id)
        return model_view(item, provider_row, catalog)

    @router.post("/models/{model_id}/resolve-change")
    async def resolve_model_change(model_id: str, body: ResolveChangeInput) -> ModelView:
        old = required(config.get_model(runtime.conn, model_id))
        provider_row = required(config.get_provider(runtime.conn, old.provider_id))
        catalog = ModelCatalogCache(runtime.config.catalog_path).snapshot()
        resolved = resolve_model_params(old, provider_row, catalog)
        expected = next(
            (change for change in resolved.pending_changes if change.field == body.field), None
        )
        if (
            expected is None
            or expected.upstream_value != body.expected_upstream_value
            or expected.upstream_source != body.expected_upstream_source
            or expected.user_value != body.expected_user_value
        ):
            raise HTTPException(
                409, "Model parameter change has changed; refresh and review it again"
            )
        acked = parse_param_snapshot(old.acked_upstream_json)

        next_protocol = old.protocol_override
        protocol_mode = old.protocol_mode
        next_ctx = old.context_window
        next_out = old.max_output_tokens
        acked_protocol = acked.protocol
        acked_ctx = acked.context_window
        acked_out = acked.max_output_tokens

        if body.field == "protocol":
            if body.action == "accept":
                next_protocol = None
                protocol_mode = ProtocolMode.AUTO
                acked_protocol = None
            else:
                acked_protocol = resolved.upstream_protocol
        elif body.field == "context_window":
            if body.action == "accept":
                next_ctx = None
                acked_ctx = None
            else:
                acked_ctx = resolved.upstream_context_window
        elif body.field == "max_output_tokens":
            if body.action == "accept":
                next_out = None
                acked_out = None
            else:
                acked_out = resolved.upstream_max_output_tokens

        item = config.upsert_model(
            runtime.conn,
            provider_id=old.provider_id,
            model_id=old.model_id,
            display_name=old.display_name,
            protocol_override=next_protocol,
            protocol_mode=protocol_mode,
            context_window=next_ctx,
            max_output_tokens=next_out,
            capabilities_json=old.capabilities_json,
            enabled=old.enabled,
            acked_upstream_json=dump_param_snapshot(
                ParamSnapshot(
                    protocol=acked_protocol,
                    context_window=acked_ctx,
                    max_output_tokens=acked_out,
                )
            ),
        )
        changed("model", item.id)
        return model_view(item, provider_row, catalog)

    @router.delete("/models/{model_id}", status_code=204)
    async def remove_model(model_id: str) -> Response:
        required(config.get_model(runtime.conn, model_id))
        config.delete_model(runtime.conn, model_id)
        changed("model", model_id)
        return Response(status_code=204)

    @router.get("/settings")
    async def settings() -> SettingsInput:
        return SettingsInput(
            default_model_id=config.get_setting(runtime.conn, "default_model_id") or None,
            background_model_id=config.get_setting(runtime.conn, "background_model_id") or None,
            global_rules_markdown=config.get_setting(runtime.conn, GLOBAL_RULES_SETTING_KEY) or "",
        )

    @router.patch("/settings")
    async def edit_settings(body: SettingsInput) -> SettingsInput:
        with ensure_transaction(runtime.conn):
            for key, model_id in (
                ("default_model_id", body.default_model_id),
                ("background_model_id", body.background_model_id),
            ):
                if key in body.model_fields_set:
                    if model_id is not None:
                        required(config.get_model(runtime.conn, model_id))
                    config.set_setting(runtime.conn, key, model_id or "")
            if "global_rules_markdown" in body.model_fields_set:
                config.set_setting(
                    runtime.conn, GLOBAL_RULES_SETTING_KEY, body.global_rules_markdown
                )
        changed("settings")
        return await settings()

    @router.get("/settings/prompts")
    async def prompts() -> list[PromptView]:
        return [
            PromptView(
                mode=mode,
                markdown=resolve_mode_prompt(runtime.conn, mode),
                default_markdown=DEFAULT_MODE_PROMPTS[mode],
            )
            for mode in SessionMode
        ]

    @router.put("/settings/prompts/{mode}")
    async def edit_prompt(mode: SessionMode, body: PromptInput) -> PromptView:
        config.set_setting(runtime.conn, mode_prompt_setting_key(mode), body.markdown)
        changed("prompt", mode.value)
        return PromptView(
            mode=mode,
            markdown=resolve_mode_prompt(runtime.conn, mode),
            default_markdown=DEFAULT_MODE_PROMPTS[mode],
        )

    @router.delete("/settings/prompts/{mode}")
    async def reset_prompt(mode: SessionMode) -> PromptView:
        config.set_setting(runtime.conn, mode_prompt_setting_key(mode), "")
        changed("prompt", mode.value)
        return PromptView(
            mode=mode,
            markdown=DEFAULT_MODE_PROMPTS[mode],
            default_markdown=DEFAULT_MODE_PROMPTS[mode],
        )

    return router
