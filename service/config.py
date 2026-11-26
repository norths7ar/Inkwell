"""Resolve the managed instance using the same configuration as the backend."""

import os
from dataclasses import dataclass
from pathlib import Path

from backend.config import load_config


def same_path(left: str | Path, right: str | Path) -> bool:
    return os.path.normcase(str(Path(left).resolve())) == os.path.normcase(
        str(Path(right).resolve())
    )


@dataclass(frozen=True)
class Instance:
    root: Path
    data: Path
    host: str
    port: int

    @property
    def python(self) -> Path:
        return self.root / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")

    @property
    def base_python(self) -> Path | None:
        path = self.root / ".venv/pyvenv.cfg"
        if path.is_file():
            for line in path.read_text(encoding="utf-8").splitlines():
                key, separator, value = line.partition("=")
                if separator and key.strip() == "home":
                    return Path(value.strip()) / ("python.exe" if os.name == "nt" else "python")
        return None

    @property
    def runtime_directory(self) -> Path:
        return self.data / "runtime"

    @property
    def state_path(self) -> Path:
        return self.runtime_directory / "inkwell.json"

    @property
    def lock_path(self) -> Path:
        return self.runtime_directory / "inkwell.lock"

    @property
    def health_url(self) -> str:
        host = {"0.0.0.0": "127.0.0.1", "::": "::1"}.get(self.host, self.host)
        if ":" in host:
            host = f"[{host}]"
        return f"http://{host}:{self.port}/health"

    @classmethod
    def from_arguments(cls, data: str | None, host: str | None, port: int | None) -> Instance:
        config = load_config(data_dir=data, host=host, port=port)
        return cls(Path(__file__).resolve().parents[1], config.data_dir, config.host, config.port)
