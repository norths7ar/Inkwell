"""Resolve user choices over server and catalog facts, with direct-only catalog protocols."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Literal

from backend.llm._json import get_int, get_str, parse_json_object
from backend.llm.catalog import CatalogSnapshot
from backend.llm.types import DiscoveredModel
from backend.storage.models import ModelRecord, ProtocolMode, ProtocolName, Provider

ParamField = Literal["protocol", "context_window", "max_output_tokens"]
ParamSource = Literal["user", "server", "catalog", "default"]
UpstreamSource = Literal["server", "catalog"]


@dataclass(frozen=True, slots=True)
class ParamSnapshot:
    protocol: ProtocolName | None = None
    context_window: int | None = None
    max_output_tokens: int | None = None


@dataclass(frozen=True, slots=True)
class UpstreamParamChange:
    field: ParamField
    user_value: str | int
    upstream_value: str | int
    upstream_source: UpstreamSource


@dataclass(frozen=True, slots=True)
class ResolvedModelParams:
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
    pending_changes: tuple[UpstreamParamChange, ...]


def _valid_positive(value: int | None) -> int | None:
    return value if value is not None and value > 0 else None


def _valid_protocol(raw: str | None) -> ProtocolName | None:
    if raw is None or raw not in ProtocolName:
        return None
    return ProtocolName(raw)


def parse_param_snapshot(raw_json: str) -> ParamSnapshot:
    """Decode a persisted discovered_json or acked_upstream_json payload safely."""
    if not raw_json or raw_json.strip() in ("", "{}"):
        return ParamSnapshot()
    try:
        obj = parse_json_object(raw_json)
    except ValueError:
        return ParamSnapshot()
    return ParamSnapshot(
        protocol=_valid_protocol(get_str(obj, "protocol")),
        context_window=_valid_positive(get_int(obj, "context_window")),
        max_output_tokens=_valid_positive(get_int(obj, "max_output_tokens")),
    )


def dump_param_snapshot(snapshot: ParamSnapshot) -> str:
    payload: dict[str, int | str] = {}
    if snapshot.protocol is not None:
        payload["protocol"] = snapshot.protocol.value
    if snapshot.context_window is not None and snapshot.context_window > 0:
        payload["context_window"] = snapshot.context_window
    if snapshot.max_output_tokens is not None and snapshot.max_output_tokens > 0:
        payload["max_output_tokens"] = snapshot.max_output_tokens
    return json.dumps(payload, ensure_ascii=False, sort_keys=True)


def snapshot_from_discovered(item: DiscoveredModel) -> ParamSnapshot:
    """Extract only pure server-disclosed facts from a raw /models discovery item."""
    raw_proto = (
        item.server_protocol if item.server_protocol is not None else item.recommended_protocol
    )
    raw_ctx = (
        item.server_context_window
        if item.server_context_window is not None
        else item.context_window
    )
    raw_out = (
        item.server_max_output_tokens
        if item.server_max_output_tokens is not None
        else item.max_output_tokens
    )
    return ParamSnapshot(
        protocol=ProtocolName(raw_proto.value) if raw_proto is not None else None,
        context_window=_valid_positive(raw_ctx),
        max_output_tokens=_valid_positive(raw_out),
    )


def _pick_upstream[T](
    server_val: T | None,
    catalog_val: T | None,
) -> tuple[T | None, UpstreamSource | None]:
    if server_val is not None:
        return server_val, "server"
    if catalog_val is not None:
        return catalog_val, "catalog"
    return None, None


def resolve_model_params(
    model: ModelRecord,
    provider: Provider,
    catalog: CatalogSnapshot | None,
) -> ResolvedModelParams:
    """Resolve effective model parameters and detect unacknowledged upstream changes."""
    discovered = parse_param_snapshot(model.discovered_json)
    acked = parse_param_snapshot(model.acked_upstream_json)

    entry = catalog.lookup(provider.preset, model.model_id) if catalog is not None else None
    direct_catalog = entry if (entry is not None and entry.match_scope == "provider") else None

    cat_proto = (
        ProtocolName(direct_catalog.protocol.value)
        if direct_catalog is not None and direct_catalog.protocol is not None
        else None
    )
    cat_ctx = entry.context_window if entry is not None else None
    cat_out = entry.max_output_tokens if entry is not None else None

    up_proto, up_proto_src = _pick_upstream(discovered.protocol, cat_proto)
    up_ctx, up_ctx_src = _pick_upstream(discovered.context_window, cat_ctx)
    up_out, up_out_src = _pick_upstream(discovered.max_output_tokens, cat_out)

    # Effective protocol: user override > server snapshot > direct catalog > provider default.
    if model.protocol_mode == ProtocolMode.OVERRIDE and model.protocol_override is not None:
        eff_proto_override: ProtocolName | None = model.protocol_override
        eff_proto = model.protocol_override
        proto_source: ParamSource = "user"
    elif (
        model.protocol_mode == ProtocolMode.AUTO
        and up_proto is not None
        and up_proto_src is not None
    ):
        eff_proto_override = up_proto
        eff_proto = up_proto
        proto_source = up_proto_src
    else:
        eff_proto_override = None
        eff_proto = provider.default_protocol
        proto_source = "default"

    if model.context_window is not None:
        eff_ctx: int | None = model.context_window
        ctx_source: ParamSource = "user"
    elif up_ctx is not None and up_ctx_src is not None:
        eff_ctx = up_ctx
        ctx_source = up_ctx_src
    else:
        eff_ctx = None
        ctx_source = "default"

    if model.max_output_tokens is not None:
        eff_out: int | None = model.max_output_tokens
        out_source: ParamSource = "user"
    elif up_out is not None and up_out_src is not None:
        eff_out = up_out
        out_source = up_out_src
    else:
        eff_out = None
        out_source = "default"

    changes: list[UpstreamParamChange] = []
    if (
        model.protocol_mode == ProtocolMode.OVERRIDE
        and model.protocol_override is not None
        and up_proto is not None
        and up_proto_src is not None
        and up_proto != model.protocol_override
        and up_proto != acked.protocol
    ):
        changes.append(
            UpstreamParamChange(
                field="protocol",
                user_value=model.protocol_override.value,
                upstream_value=up_proto.value,
                upstream_source=up_proto_src,
            )
        )
    if (
        model.context_window is not None
        and up_ctx is not None
        and up_ctx_src is not None
        and up_ctx != model.context_window
        and up_ctx != acked.context_window
    ):
        changes.append(
            UpstreamParamChange(
                field="context_window",
                user_value=model.context_window,
                upstream_value=up_ctx,
                upstream_source=up_ctx_src,
            )
        )
    if (
        model.max_output_tokens is not None
        and up_out is not None
        and up_out_src is not None
        and up_out != model.max_output_tokens
        and up_out != acked.max_output_tokens
    ):
        changes.append(
            UpstreamParamChange(
                field="max_output_tokens",
                user_value=model.max_output_tokens,
                upstream_value=up_out,
                upstream_source=up_out_src,
            )
        )

    return ResolvedModelParams(
        effective_protocol=eff_proto,
        effective_protocol_override=eff_proto_override,
        protocol_source=proto_source,
        effective_context_window=eff_ctx,
        context_window_source=ctx_source,
        effective_max_output_tokens=eff_out,
        max_output_tokens_source=out_source,
        upstream_protocol=up_proto,
        upstream_protocol_source=up_proto_src,
        upstream_context_window=up_ctx,
        upstream_context_window_source=up_ctx_src,
        upstream_max_output_tokens=up_out,
        upstream_max_output_tokens_source=up_out_src,
        pending_changes=tuple(changes),
    )
