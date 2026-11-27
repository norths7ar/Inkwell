"""Declarative provider presets separated from wire protocol implementations."""

from __future__ import annotations

from dataclasses import dataclass

from backend.llm.types import AuthStyle, EndpointConfig, ProviderQuirks, WireProtocol


@dataclass(frozen=True, slots=True)
class ProviderPreset:
    id: str
    name: str
    base_url: str
    default_protocol: WireProtocol
    auth_style: AuthStyle = AuthStyle.BEARER
    discovery_url: str = ""
    quirks: ProviderQuirks = ProviderQuirks()


PRESETS: dict[str, ProviderPreset] = {
    "gemini": ProviderPreset(
        id="gemini",
        name="Google Gemini",
        base_url="https://generativelanguage.googleapis.com/v1beta",
        default_protocol=WireProtocol.GOOGLE_GENAI,
        auth_style=AuthStyle.X_GOOG_API_KEY,
        discovery_url="https://generativelanguage.googleapis.com/v1beta/models",
    ),
    "openai": ProviderPreset(
        id="openai",
        name="OpenAI",
        base_url="https://api.openai.com/v1",
        default_protocol=WireProtocol.OPENAI_RESPONSES,
        discovery_url="https://api.openai.com/v1/models",
    ),
    "deepseek": ProviderPreset(
        id="deepseek",
        name="DeepSeek",
        base_url="https://api.deepseek.com",
        default_protocol=WireProtocol.OPENAI_CHAT,
        discovery_url="https://api.deepseek.com/models",
        quirks=ProviderQuirks(replay_reasoning_content=True),
    ),
    "mimo": ProviderPreset(
        id="mimo",
        name="Xiaomi MiMo",
        base_url="https://api.xiaomimimo.com/v1",
        default_protocol=WireProtocol.OPENAI_CHAT,
        discovery_url="https://api.xiaomimimo.com/v1/models",
        quirks=ProviderQuirks(replay_reasoning_content=True),
    ),
    "anthropic": ProviderPreset(
        id="anthropic",
        name="Anthropic",
        base_url="https://api.anthropic.com/v1",
        default_protocol=WireProtocol.ANTHROPIC_MESSAGES,
        auth_style=AuthStyle.X_API_KEY,
        discovery_url="https://api.anthropic.com/v1/models",
    ),
    "opencode-zen": ProviderPreset(
        id="opencode-zen",
        name="OpenCode Zen",
        base_url="https://opencode.ai/zen/v1",
        default_protocol=WireProtocol.OPENAI_CHAT,
        discovery_url="https://opencode.ai/zen/v1/models",
    ),
    "opencode-go": ProviderPreset(
        id="opencode-go",
        name="OpenCode Go",
        base_url="https://opencode.ai/zen/go/v1",
        default_protocol=WireProtocol.OPENAI_CHAT,
    ),
    "openrouter": ProviderPreset(
        id="openrouter",
        name="OpenRouter",
        base_url="https://openrouter.ai/api/v1",
        default_protocol=WireProtocol.OPENAI_CHAT,
        discovery_url="https://openrouter.ai/api/v1/models",
    ),
    "custom": ProviderPreset(
        id="custom",
        name="Custom OpenAI-Compatible",
        base_url="",
        default_protocol=WireProtocol.OPENAI_CHAT,
    ),
}


def build_endpoint_from_preset(
    preset_id: str,
    *,
    api_key: str = "",
    base_url: str | None = None,
    default_protocol: WireProtocol | None = None,
    discovery_url: str | None = None,
    headers: tuple[tuple[str, str], ...] = (),
) -> EndpointConfig:
    """Create an EndpointConfig from a named preset with optional user overrides."""
    preset = PRESETS.get(preset_id)
    if preset is None:
        raise ValueError(f"Unknown provider preset: {preset_id}")

    resolved_base = (base_url if base_url is not None else preset.base_url).strip()
    if not resolved_base:
        raise ValueError(f"Provider preset '{preset_id}' requires a base_url")

    return EndpointConfig(
        base_url=resolved_base,
        default_protocol=default_protocol or preset.default_protocol,
        api_key=api_key,
        auth_style=preset.auth_style,
        discovery_url=(
            discovery_url if discovery_url is not None else preset.discovery_url
        ).strip(),
        headers=headers,
        quirks=preset.quirks,
    )
