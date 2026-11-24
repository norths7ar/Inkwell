"""Runtime configuration for Inkwell."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8765


@dataclass(frozen=True, slots=True)
class AppConfig:
    """Resolved runtime paths and listener settings."""

    data_dir: Path
    host: str
    port: int

    @property
    def db_path(self) -> Path:
        return self.data_dir / "inkwell.sqlite3"

    @property
    def backups_dir(self) -> Path:
        return self.data_dir / "backups"

    @property
    def attachments_dir(self) -> Path:
        return self.data_dir / "attachments"

    @property
    def logs_dir(self) -> Path:
        return self.data_dir / "logs"

    @property
    def key_path(self) -> Path:
        return self.data_dir / "secret.key"

    def ensure_data_dirs(self) -> None:
        """Create the writable data directory tree if missing."""
        for directory in (
            self.data_dir,
            self.backups_dir,
            self.attachments_dir,
            self.logs_dir,
        ):
            directory.mkdir(parents=True, exist_ok=True)


def default_data_dir() -> Path:
    """Return the platform-standard per-user data directory."""
    if os.name == "nt":
        local_app_data = os.getenv("LOCALAPPDATA")
        base = Path(local_app_data) if local_app_data else Path.home() / "AppData" / "Local"
        return base / "Inkwell"

    xdg_data_home = os.getenv("XDG_DATA_HOME")
    base = Path(xdg_data_home) if xdg_data_home else Path.home() / ".local" / "share"
    return base / "inkwell"


def _parse_port(raw_port: int | str) -> int:
    port = int(raw_port)
    if not 1 <= port <= 65535:
        raise ValueError(f"Port must be between 1 and 65535, got {port}")
    return port


def load_config(
    *,
    data_dir: Path | str | None = None,
    host: str | None = None,
    port: int | None = None,
) -> AppConfig:
    """Load runtime config from explicit overrides, environment variables, or defaults."""
    raw_dir = data_dir if data_dir is not None else os.getenv("INKWELL_DATA_DIR")
    resolved_dir = Path(raw_dir).expanduser().resolve() if raw_dir else default_data_dir().resolve()

    raw_host = host if host is not None else os.getenv("INKWELL_HOST")
    resolved_host = raw_host.strip() if raw_host and raw_host.strip() else DEFAULT_HOST

    raw_port: int | str = port if port is not None else (os.getenv("INKWELL_PORT") or DEFAULT_PORT)
    resolved_port = _parse_port(raw_port)

    return AppConfig(
        data_dir=resolved_dir,
        host=resolved_host,
        port=resolved_port,
    )
