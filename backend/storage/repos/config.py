"""Repositories for model providers, model overrides, and global settings."""

from __future__ import annotations

import sqlite3

from backend.storage.ids import new_id, utc_now
from backend.storage.migrations import ensure_transaction
from backend.storage.models import ModelRecord, ProtocolMode, ProtocolName, Provider
from backend.storage.repos.content import UNSET, Unset


def _provider_from_row(row: sqlite3.Row) -> Provider:
    return Provider(
        id=str(row["id"]),
        name=str(row["name"]),
        preset=str(row["preset"]),
        base_url=str(row["base_url"]),
        discovery_url=str(row["discovery_url"]),
        default_protocol=ProtocolName(str(row["default_protocol"])),
        api_key_encrypted=str(row["api_key_encrypted"]),
        headers_encrypted=str(row["headers_encrypted"]),
        enabled=bool(int(row["enabled"])),
        created_at=str(row["created_at"]),
        updated_at=str(row["updated_at"]),
    )


def _model_from_row(row: sqlite3.Row) -> ModelRecord:
    raw_override = row["protocol_override"]
    raw_ctx = row["context_window"]
    raw_max_out = row["max_output_tokens"]
    return ModelRecord(
        id=str(row["id"]),
        provider_id=str(row["provider_id"]),
        model_id=str(row["model_id"]),
        display_name=str(row["display_name"]),
        protocol_override=ProtocolName(str(raw_override)) if raw_override is not None else None,
        context_window=int(raw_ctx) if raw_ctx is not None else None,
        max_output_tokens=int(raw_max_out) if raw_max_out is not None else None,
        capabilities_json=str(row["capabilities_json"]),
        enabled=bool(int(row["enabled"])),
        created_at=str(row["created_at"]),
        updated_at=str(row["updated_at"]),
        discovered_json=str(row["discovered_json"]),
        acked_upstream_json=str(row["acked_upstream_json"]),
        protocol_mode=ProtocolMode(str(row["protocol_mode"])),
    )


def create_provider(
    conn: sqlite3.Connection,
    *,
    name: str,
    base_url: str,
    default_protocol: ProtocolName,
    preset: str = "",
    discovery_url: str = "",
    api_key_encrypted: str = "",
    headers_encrypted: str = "",
    enabled: bool = True,
    provider_id: str | None = None,
) -> Provider:
    now = utc_now()
    resolved_id = provider_id or new_id()
    with ensure_transaction(conn):
        conn.execute(
            """
            INSERT INTO providers (
                id, name, preset, base_url, discovery_url, default_protocol,
                api_key_encrypted, headers_encrypted, enabled, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                resolved_id,
                name,
                preset,
                base_url,
                discovery_url,
                default_protocol.value,
                api_key_encrypted,
                headers_encrypted,
                int(enabled),
                now,
                now,
            ),
        )
    provider = get_provider(conn, resolved_id)
    if provider is None:
        raise RuntimeError(f"Failed to load newly created provider {resolved_id}")
    return provider


def get_provider(conn: sqlite3.Connection, provider_id: str) -> Provider | None:
    row = conn.execute("SELECT * FROM providers WHERE id = ?", (provider_id,)).fetchone()
    return _provider_from_row(row) if row is not None else None


def list_providers(conn: sqlite3.Connection) -> list[Provider]:
    rows = conn.execute("SELECT * FROM providers ORDER BY created_at ASC, id ASC").fetchall()
    return [_provider_from_row(row) for row in rows]


def update_provider(
    conn: sqlite3.Connection,
    provider_id: str,
    *,
    name: str | Unset = UNSET,
    preset: str | Unset = UNSET,
    base_url: str | Unset = UNSET,
    discovery_url: str | Unset = UNSET,
    default_protocol: ProtocolName | Unset = UNSET,
    api_key_encrypted: str | Unset = UNSET,
    headers_encrypted: str | Unset = UNSET,
    enabled: bool | Unset = UNSET,
) -> Provider:
    existing = get_provider(conn, provider_id)
    if existing is None:
        raise LookupError(f"Provider not found: {provider_id}")
    now = utc_now()
    with ensure_transaction(conn):
        conn.execute(
            """
            UPDATE providers
            SET name = ?, preset = ?, base_url = ?, discovery_url = ?, default_protocol = ?,
                api_key_encrypted = ?, headers_encrypted = ?, enabled = ?, updated_at = ?
            WHERE id = ?
            """,
            (
                existing.name if isinstance(name, Unset) else name,
                existing.preset if isinstance(preset, Unset) else preset,
                existing.base_url if isinstance(base_url, Unset) else base_url,
                existing.discovery_url if isinstance(discovery_url, Unset) else discovery_url,
                (
                    existing.default_protocol
                    if isinstance(default_protocol, Unset)
                    else default_protocol
                ).value,
                (
                    existing.api_key_encrypted
                    if isinstance(api_key_encrypted, Unset)
                    else api_key_encrypted
                ),
                (
                    existing.headers_encrypted
                    if isinstance(headers_encrypted, Unset)
                    else headers_encrypted
                ),
                int(existing.enabled if isinstance(enabled, Unset) else enabled),
                now,
                provider_id,
            ),
        )
    updated = get_provider(conn, provider_id)
    if updated is None:
        raise LookupError(f"Provider not found after update: {provider_id}")
    return updated


def delete_provider(conn: sqlite3.Connection, provider_id: str) -> bool:
    with ensure_transaction(conn):
        cursor = conn.execute("DELETE FROM providers WHERE id = ?", (provider_id,))
    return cursor.rowcount > 0


def upsert_model(
    conn: sqlite3.Connection,
    *,
    provider_id: str,
    model_id: str,
    display_name: str,
    protocol_override: ProtocolName | None = None,
    context_window: int | None = None,
    max_output_tokens: int | None = None,
    capabilities_json: str = "{}",
    enabled: bool = True,
    record_id: str | None = None,
    discovered_json: str | Unset = UNSET,
    acked_upstream_json: str | Unset = UNSET,
    protocol_mode: ProtocolMode | None = None,
) -> ModelRecord:
    now = utc_now()
    existing_row = conn.execute(
        "SELECT id, discovered_json, acked_upstream_json FROM models WHERE provider_id = ? AND model_id = ?",
        (provider_id, model_id),
    ).fetchone()
    resolved_id = str(existing_row["id"]) if existing_row is not None else (record_id or new_id())
    resolved_discovered = (
        (str(existing_row["discovered_json"]) if existing_row is not None else "{}")
        if isinstance(discovered_json, Unset)
        else discovered_json
    )
    resolved_acked = (
        (str(existing_row["acked_upstream_json"]) if existing_row is not None else "{}")
        if isinstance(acked_upstream_json, Unset)
        else acked_upstream_json
    )
    override_val = protocol_override.value if protocol_override is not None else None
    resolved_mode = protocol_mode or (
        ProtocolMode.OVERRIDE if protocol_override is not None else ProtocolMode.INHERIT
    )
    with ensure_transaction(conn):
        conn.execute(
            """
            INSERT INTO models (
                id, provider_id, model_id, display_name, protocol_override,
                context_window, max_output_tokens, capabilities_json, enabled,
                discovered_json, acked_upstream_json, protocol_mode, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT (provider_id, model_id) DO UPDATE SET
                display_name = excluded.display_name,
                protocol_override = excluded.protocol_override,
                context_window = excluded.context_window,
                max_output_tokens = excluded.max_output_tokens,
                capabilities_json = excluded.capabilities_json,
                enabled = excluded.enabled,
                discovered_json = excluded.discovered_json,
                acked_upstream_json = excluded.acked_upstream_json,
                protocol_mode = excluded.protocol_mode,
                updated_at = excluded.updated_at
            """,
            (
                resolved_id,
                provider_id,
                model_id,
                display_name,
                override_val,
                context_window,
                max_output_tokens,
                capabilities_json,
                int(enabled),
                resolved_discovered,
                resolved_acked,
                resolved_mode.value,
                now,
                now,
            ),
        )
    model = get_model(conn, resolved_id)
    if model is None:
        raise RuntimeError(f"Failed to load upserted model {resolved_id}")
    return model


def update_model_snapshots(
    conn: sqlite3.Connection,
    record_id: str,
    *,
    discovered_json: str | Unset = UNSET,
    acked_upstream_json: str | Unset = UNSET,
) -> ModelRecord:
    existing = get_model(conn, record_id)
    if existing is None:
        raise LookupError(f"Model not found: {record_id}")
    next_discovered = (
        existing.discovered_json if isinstance(discovered_json, Unset) else discovered_json
    )
    next_acked = (
        existing.acked_upstream_json
        if isinstance(acked_upstream_json, Unset)
        else acked_upstream_json
    )
    if next_discovered == existing.discovered_json and next_acked == existing.acked_upstream_json:
        return existing
    now = utc_now()
    with ensure_transaction(conn):
        conn.execute(
            """
            UPDATE models
            SET discovered_json = ?, acked_upstream_json = ?, updated_at = ?
            WHERE id = ?
            """,
            (next_discovered, next_acked, now, record_id),
        )
    updated = get_model(conn, record_id)
    if updated is None:
        raise LookupError(f"Model not found after snapshot update: {record_id}")
    return updated


def get_model(conn: sqlite3.Connection, record_id: str) -> ModelRecord | None:
    row = conn.execute("SELECT * FROM models WHERE id = ?", (record_id,)).fetchone()
    return _model_from_row(row) if row is not None else None


def list_models(
    conn: sqlite3.Connection,
    *,
    provider_id: str | None = None,
) -> list[ModelRecord]:
    if provider_id is None:
        rows = conn.execute(
            "SELECT * FROM models ORDER BY provider_id ASC, display_name ASC"
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM models WHERE provider_id = ? ORDER BY display_name ASC",
            (provider_id,),
        ).fetchall()
    return [_model_from_row(row) for row in rows]


def delete_model(conn: sqlite3.Connection, record_id: str) -> bool:
    with ensure_transaction(conn):
        cursor = conn.execute("DELETE FROM models WHERE id = ?", (record_id,))
    return cursor.rowcount > 0


def get_setting(conn: sqlite3.Connection, key: str) -> str | None:
    row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    return str(row["value"]) if row is not None else None


def set_setting(conn: sqlite3.Connection, key: str, value: str) -> None:
    now = utc_now()
    with ensure_transaction(conn):
        conn.execute(
            """
            INSERT INTO settings (key, value, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT (key) DO UPDATE SET
                value = excluded.value,
                updated_at = excluded.updated_at
            """,
            (key, value, now),
        )


def list_settings(conn: sqlite3.Connection) -> dict[str, str]:
    rows = conn.execute("SELECT key, value FROM settings ORDER BY key ASC").fetchall()
    return {str(row["key"]): str(row["value"]) for row in rows}
