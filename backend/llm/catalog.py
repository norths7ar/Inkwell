"""Refreshable local model metadata cache backed by models.dev."""

from __future__ import annotations

import json
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Literal, cast

import httpx

from backend.llm._json import JsonObject, JsonValue, as_object, get_int, get_str, parse_json_object
from backend.llm.errors import (
    ErrorCategory,
    LLMError,
    classify_http_error,
    classify_transport_error,
)
from backend.llm.types import DiscoveredModel, WireProtocol

MODELS_DEV_API_URL = "https://models.dev/api.json"
CATALOG_SCHEMA_VERSION = 2
STALE_AFTER_DAYS = 14
_MAX_CATALOG_BYTES = 16 * 1024 * 1024

MatchScope = Literal["none", "provider", "cross_provider"]

_PRESET_TO_CATALOG_PROVIDERS: dict[str, tuple[str, ...]] = {
    "openai": ("openai",),
    "anthropic": ("anthropic",),
    "deepseek": ("deepseek",),
    "gemini": ("google",),
    "mimo": ("xiaomi", "mimo"),
    "openrouter": ("openrouter",),
    "opencode-zen": ("opencode",),
    "opencode-go": ("opencode",),
}

_KNOWN_PREFIXES: tuple[str, ...] = (
    "models/",
    "openai/",
    "anthropic/",
    "google/",
    "deepseek/",
    "xiaomi/",
)


@dataclass(frozen=True, slots=True)
class CatalogEntry:
    model_id: str
    display_name: str = ""
    context_window: int | None = None
    max_output_tokens: int | None = None
    protocol: WireProtocol | None = None
    match_scope: MatchScope = "provider"
    source_provider: str | None = None
    stale: bool = False


@dataclass(frozen=True, slots=True)
class CatalogStatus:
    cached: bool
    fetched_at: str | None
    stale: bool
    stale_after_days: int
    provider_count: int
    model_count: int
    source: str = "models.dev"


def _positive_int(value: JsonValue | None) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value if value > 0 else None


def _protocol_from_npm(npm_package: str | None) -> WireProtocol | None:
    """Map an explicit models.dev SDK package declaration to a WireProtocol without guessing."""
    pkg = (npm_package or "").strip().lower()
    if pkg == "@ai-sdk/anthropic":
        return WireProtocol.ANTHROPIC_MESSAGES
    if pkg in ("@ai-sdk/google", "@ai-sdk/google-vertex"):
        return WireProtocol.GOOGLE_GENAI
    if pkg == "@ai-sdk/openai":
        return WireProtocol.OPENAI_RESPONSES
    if pkg == "@ai-sdk/openai-compatible":
        return WireProtocol.OPENAI_CHAT
    return None


def compact_models_dev_payload(raw_payload: JsonObject, *, fetched_at: str) -> JsonObject:
    """Distill a full models.dev/api.json payload into a compact Inkwell cache document."""
    providers_out: dict[str, JsonValue] = {}
    for provider_id, raw_provider in raw_payload.items():
        provider_obj = as_object(raw_provider)
        if provider_obj is None:
            continue
        models_obj = as_object(provider_obj.get("models"))
        if not models_obj:
            continue
        models_out: dict[str, JsonValue] = {}
        for model_key, raw_model in models_obj.items():
            model_obj = as_object(raw_model)
            if model_obj is None:
                continue
            model_id = (get_str(model_obj, "id") or model_key).strip()
            if not model_id:
                continue
            limit_obj = as_object(model_obj.get("limit"))
            context_window = _positive_int(limit_obj.get("context") if limit_obj else None)
            max_output = _positive_int(limit_obj.get("output") if limit_obj else None)
            model_provider = as_object(model_obj.get("provider"))
            model_npm = get_str(model_provider, "npm") if model_provider else None
            # Provider-wide defaults must not become permanent per-model overrides.
            protocol = _protocol_from_npm(model_npm)
            if context_window is None and max_output is None and protocol is None:
                continue
            entry: JsonObject = {}
            name = get_str(model_obj, "name")
            if name and name.strip():
                entry["name"] = name.strip()
            if context_window is not None:
                entry["context_window"] = context_window
            if max_output is not None:
                entry["max_output_tokens"] = max_output
            if protocol is not None:
                entry["protocol"] = protocol.value
            models_out[model_id] = entry
        if models_out:
            providers_out[provider_id] = models_out

    return {
        "schema_version": CATALOG_SCHEMA_VERSION,
        "source": "models.dev",
        "fetched_at": fetched_at,
        "providers": providers_out,
    }


def _is_stale(fetched_at: str | None, *, now: datetime | None = None) -> bool:
    if not fetched_at:
        return True
    try:
        parsed = datetime.fromisoformat(fetched_at)
    except ValueError:
        return True
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    current = now or datetime.now(UTC)
    return current - parsed >= timedelta(days=STALE_AFTER_DAYS)


def _decode_raw_entry(
    model_id: str,
    obj: JsonObject,
    *,
    match_scope: MatchScope,
    source_provider: str,
    stale: bool,
) -> CatalogEntry:
    raw_proto = get_str(obj, "protocol")
    protocol = WireProtocol(raw_proto) if raw_proto in WireProtocol else None
    return CatalogEntry(
        model_id=model_id,
        display_name=get_str(obj, "name") or "",
        context_window=get_int(obj, "context_window"),
        max_output_tokens=get_int(obj, "max_output_tokens"),
        protocol=protocol if match_scope == "provider" else None,
        match_scope=match_scope,
        source_provider=source_provider,
        stale=stale,
    )


def _strip_prefix(model_id: str) -> str:
    lowered = model_id.lower()
    for prefix in _KNOWN_PREFIXES:
        if lowered.startswith(prefix):
            return model_id[len(prefix) :]
    return model_id


def _unambiguous_int(values: list[int | None]) -> int | None:
    present = {v for v in values if v is not None}
    if len(present) == 1:
        return next(iter(present))
    return None


def _unambiguous_str(values: list[str]) -> str:
    present = {v for v in values if v}
    if len(present) == 1:
        return next(iter(present))
    return ""


class CatalogSnapshot:
    """In-memory indexed view of a single loaded model-catalog.json snapshot."""

    def __init__(self, doc: JsonObject, *, now: datetime | None = None) -> None:
        self.stale = _is_stale(get_str(doc, "fetched_at"), now=now)
        raw_providers = as_object(doc.get("providers")) or {}
        self.by_provider: dict[str, dict[str, tuple[str, JsonObject]]] = {}
        for provider_key, raw_models in raw_providers.items():
            models_map = as_object(raw_models)
            if not models_map:
                continue
            indexed: dict[str, tuple[str, JsonObject]] = {}
            for model_key, raw_entry in models_map.items():
                entry_obj = as_object(raw_entry)
                if entry_obj is not None:
                    indexed[model_key.lower()] = (model_key, entry_obj)
            if indexed:
                self.by_provider[cast(str, provider_key)] = indexed

    def _find_in_provider(
        self, provider_key: str, candidate_ids: list[str]
    ) -> tuple[str, JsonObject] | None:
        indexed = self.by_provider.get(provider_key)
        if not indexed:
            return None
        for cid in candidate_ids:
            found = indexed.get(cid.lower())
            if found is not None:
                return found
        return None

    def lookup(self, provider_preset: str, model_id: str) -> CatalogEntry | None:
        cleaned = model_id.strip()
        if not cleaned:
            return None
        candidate_ids = [cleaned]
        stripped = _strip_prefix(cleaned)
        if stripped != cleaned:
            candidate_ids.append(stripped)

        preferred_providers = _PRESET_TO_CATALOG_PROVIDERS.get(provider_preset.strip().lower(), ())
        for provider_key in preferred_providers:
            found = self._find_in_provider(provider_key, candidate_ids)
            if found is not None:
                matched_id, obj = found
                return _decode_raw_entry(
                    matched_id,
                    obj,
                    match_scope="provider",
                    source_provider=provider_key,
                    stale=self.stale,
                )

        # Cross-provider fallback: collect ALL matches, never recommend protocol,
        # and leave conflicting numeric fields empty instead of picking the first match.
        cross_matches: list[CatalogEntry] = []
        for provider_key in self.by_provider:
            if provider_key in preferred_providers:
                continue
            found = self._find_in_provider(provider_key, candidate_ids)
            if found is not None:
                matched_id, obj = found
                cross_matches.append(
                    _decode_raw_entry(
                        matched_id,
                        obj,
                        match_scope="cross_provider",
                        source_provider=provider_key,
                        stale=self.stale,
                    )
                )

        if not cross_matches:
            return None
        if len(cross_matches) == 1:
            return cross_matches[0]

        context_window = _unambiguous_int([m.context_window for m in cross_matches])
        max_output = _unambiguous_int([m.max_output_tokens for m in cross_matches])
        display_name = _unambiguous_str([m.display_name for m in cross_matches])
        if context_window is None and max_output is None and not display_name:
            return None
        sources = ", ".join(m.source_provider for m in cross_matches if m.source_provider)
        return CatalogEntry(
            model_id=cleaned,
            display_name=display_name,
            context_window=context_window,
            max_output_tokens=max_output,
            protocol=None,
            match_scope="cross_provider",
            source_provider=sources or None,
            stale=self.stale,
        )


class ModelCatalogCache:
    """On-disk cache of model metadata under INKWELL_DATA_DIR/model-catalog.json."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def _load(self) -> JsonObject | None:
        if not self.path.is_file():
            return None
        try:
            text = self.path.read_text(encoding="utf-8")
            doc = parse_json_object(text)
        except OSError, ValueError:
            return None
        if get_int(doc, "schema_version") != CATALOG_SCHEMA_VERSION:
            return None
        if as_object(doc.get("providers")) is None:
            return None
        return doc

    def status(self, *, now: datetime | None = None) -> CatalogStatus:
        doc = self._load()
        if doc is None:
            return CatalogStatus(
                cached=False,
                fetched_at=None,
                stale=True,
                stale_after_days=STALE_AFTER_DAYS,
                provider_count=0,
                model_count=0,
            )
        providers = as_object(doc.get("providers")) or {}
        model_count = sum(len(as_object(p) or {}) for p in providers.values())
        fetched_at = get_str(doc, "fetched_at")
        return CatalogStatus(
            cached=True,
            fetched_at=fetched_at,
            stale=_is_stale(fetched_at, now=now),
            stale_after_days=STALE_AFTER_DAYS,
            provider_count=len(providers),
            model_count=model_count,
            source=get_str(doc, "source") or "models.dev",
        )

    async def refresh(
        self,
        client: httpx.AsyncClient,
        *,
        url: str = MODELS_DEV_API_URL,
    ) -> CatalogStatus:
        try:
            response = await client.get(
                url,
                headers={"Accept": "application/json"},
                timeout=httpx.Timeout(connect=10.0, read=25.0, write=10.0, pool=10.0),
                follow_redirects=True,
            )
        except httpx.HTTPError as exc:
            raise classify_transport_error(exc) from exc

        if response.status_code >= 400:
            raise classify_http_error(response.status_code, response.text, response.headers)

        raw_bytes = response.content
        if len(raw_bytes) > _MAX_CATALOG_BYTES:
            raise LLMError(
                ErrorCategory.PROTOCOL,
                "Model catalog response exceeded size limit",
                retryable=False,
            )
        try:
            payload = parse_json_object(raw_bytes.decode("utf-8"))
        except (UnicodeDecodeError, ValueError) as exc:
            raise LLMError(
                ErrorCategory.PROTOCOL,
                f"Malformed model catalog JSON: {exc}",
                retryable=False,
            ) from exc

        fetched_at = datetime.now(UTC).isoformat()
        compact = compact_models_dev_payload(payload, fetched_at=fetched_at)
        providers = as_object(compact.get("providers")) or {}
        if not providers:
            raise LLMError(
                ErrorCategory.PROTOCOL,
                "Model catalog response contained no usable providers",
                retryable=False,
            )

        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = self.path.with_suffix(".tmp")
        tmp_path.write_text(json.dumps(compact, ensure_ascii=False), encoding="utf-8")
        tmp_path.replace(self.path)
        return self.status()

    def snapshot(self) -> CatalogSnapshot | None:
        doc = self._load()
        if doc is None:
            return None
        return CatalogSnapshot(doc)

    def lookup(self, provider_preset: str, model_id: str) -> CatalogEntry | None:
        snap = self.snapshot()
        if snap is None:
            return None
        return snap.lookup(provider_preset, model_id)

    def enrich_discovered(
        self,
        provider_preset: str,
        items: list[DiscoveredModel],
    ) -> list[DiscoveredModel]:
        """Fill missing metadata from a single loaded cache snapshot without overriding server facts."""
        if not items:
            return items
        snapshot = self.snapshot()
        enriched: list[DiscoveredModel] = []
        for item in items:
            if not item.supported:
                enriched.append(item)
                continue
            server_ctx = (
                item.server_context_window
                if item.server_context_window is not None
                else item.context_window
            )
            server_out = (
                item.server_max_output_tokens
                if item.server_max_output_tokens is not None
                else item.max_output_tokens
            )
            server_proto = (
                item.server_protocol
                if item.server_protocol is not None
                else item.recommended_protocol
            )
            entry = (
                snapshot.lookup(provider_preset, item.model_id) if snapshot is not None else None
            )
            # Precedence: server-disclosed protocol > direct-provider catalog > inherit default.
            resolved_protocol = server_proto
            if (
                resolved_protocol is None
                and entry is not None
                and entry.match_scope == "provider"
                and entry.protocol is not None
            ):
                resolved_protocol = entry.protocol
            if entry is None:
                enriched.append(
                    replace(
                        item,
                        server_context_window=server_ctx,
                        server_max_output_tokens=server_out,
                        server_protocol=server_proto,
                    )
                )
                continue
            display_name = item.display_name
            if (not display_name or display_name == item.model_id) and entry.display_name:
                display_name = entry.display_name
            enriched.append(
                replace(
                    item,
                    display_name=display_name,
                    context_window=server_ctx if server_ctx is not None else entry.context_window,
                    max_output_tokens=(
                        server_out if server_out is not None else entry.max_output_tokens
                    ),
                    recommended_protocol=resolved_protocol,
                    catalog_match=entry.match_scope,
                    catalog_provider=entry.source_provider,
                    catalog_stale=entry.stale,
                    server_context_window=server_ctx,
                    server_max_output_tokens=server_out,
                    server_protocol=server_proto,
                )
            )
        return enriched
