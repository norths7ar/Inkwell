"""Provider, model, prompt and global settings endpoints."""

from __future__ import annotations

import json

from fastapi import APIRouter, HTTPException, Response
from pydantic import Field

from backend.agent.context import GLOBAL_RULES_SETTING_KEY
from backend.agent.prompts import DEFAULT_MODE_PROMPTS, mode_prompt_setting_key, resolve_mode_prompt
from backend.api.content import Input, required, supplied
from backend.api.runtime import ApiRuntime
from backend.llm.presets import PRESETS, ProviderPreset, build_endpoint_from_preset
from backend.llm.types import DiscoveredModel, WireProtocol
from backend.storage.migrations import ensure_transaction
from backend.storage.models import ModelRecord, ProtocolName, Provider, SessionMode
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
    context_window: int | None = Field(default=None, gt=0)
    max_output_tokens: int | None = Field(default=None, gt=0)
    capabilities_json: str = "{}"
    enabled: bool = True


class ModelPatch(Input):
    display_name: str = ""
    protocol_override: ProtocolName | None = None
    context_window: int | None = Field(default=None, gt=0)
    max_output_tokens: int | None = Field(default=None, gt=0)
    capabilities_json: str = "{}"
    enabled: bool = True


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
        # Discovery is a preview: importing models is an explicit POST so overrides survive.
        return await runtime.llm.list_models(runtime.endpoint(provider))

    @router.get("/models")
    async def models(provider_id: str | None = None) -> list[ModelRecord]:
        return config.list_models(runtime.conn, provider_id=provider_id)

    @router.post("/providers/{provider_id}/models", status_code=201)
    async def add_model(provider_id: str, body: ModelInput) -> ModelRecord:
        required(config.get_provider(runtime.conn, provider_id))
        if any(
            item.model_id == body.model_id
            for item in config.list_models(runtime.conn, provider_id=provider_id)
        ):
            raise HTTPException(409, "Model already exists; use PATCH to change it")
        item = config.upsert_model(
            runtime.conn,
            provider_id=provider_id,
            model_id=body.model_id,
            display_name=body.display_name or body.model_id,
            protocol_override=body.protocol_override,
            context_window=body.context_window,
            max_output_tokens=body.max_output_tokens,
            capabilities_json=body.capabilities_json,
            enabled=body.enabled,
        )
        changed("model", item.id)
        return item

    @router.get("/models/{model_id}")
    async def model(model_id: str) -> ModelRecord:
        return required(config.get_model(runtime.conn, model_id))

    @router.patch("/models/{model_id}")
    async def edit_model(model_id: str, body: ModelPatch) -> ModelRecord:
        old = required(config.get_model(runtime.conn, model_id))
        fields = body.model_fields_set
        item = config.upsert_model(
            runtime.conn,
            provider_id=old.provider_id,
            model_id=old.model_id,
            display_name=body.display_name if "display_name" in fields else old.display_name,
            protocol_override=(
                body.protocol_override if "protocol_override" in fields else old.protocol_override
            ),
            context_window=body.context_window
            if "context_window" in fields
            else old.context_window,
            max_output_tokens=(
                body.max_output_tokens if "max_output_tokens" in fields else old.max_output_tokens
            ),
            capabilities_json=(
                body.capabilities_json if "capabilities_json" in fields else old.capabilities_json
            ),
            enabled=body.enabled if "enabled" in fields else old.enabled,
        )
        changed("model", item.id)
        return item

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
