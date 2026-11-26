"""Service Desk version-one status contract."""

from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

type ServiceState = Literal["running", "stopped", "warning", "unknown"]


class HealthResponse(BaseModel):
    model_config = ConfigDict(strict=True)

    schema_version: Literal[1]
    ok: bool
    service: Literal["inkwell"]
    pid: int = Field(gt=0)
    port: int = Field(ge=1, le=65535)
    detail: str


@dataclass(frozen=True)
class Status:
    state: ServiceState
    detail: str
    pid: int | None = None
    busy: bool = False


class ManagerError(Exception):
    def __init__(self, detail: str, state: ServiceState = "unknown", *, busy: bool = False):
        super().__init__(detail)
        self.state: ServiceState = state
        self.busy = busy


class RuntimeRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    pid: int = Field(gt=0)
    launcher_pid: int = Field(gt=0)
    port: int = Field(ge=1, le=65535)
    host: str
    started_at: str
    python: str
    data_directory: str
    stdout: str
    stderr: str
    shutdown_token: str = Field(min_length=32)
