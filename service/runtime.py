"""Read-only ownership checks and atomic runtime records."""

import os
from datetime import datetime

import httpx
import psutil
from pydantic import ValidationError

from .config import Instance, same_path
from .contract import ManagerError, RuntimeRecord, Status
from .platform import LockBusy, management_lock


class Runtime:
    def __init__(self, instance: Instance):
        self.instance = instance

    def read_state(self) -> RuntimeRecord | None:
        try:
            content = self.instance.state_path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return None
        try:
            state = RuntimeRecord.model_validate_json(content)
            datetime.fromisoformat(state.started_at)
            return state
        except ValidationError, ValueError:
            raise ManagerError("Invalid runtime state. Existing state was preserved.") from None

    def owned_process(self, state: RuntimeRecord | None) -> psutil.Process | None:
        if state is None:
            return None
        try:
            process = psutil.Process(state.pid)
            command = process.cmdline()
            executable = process.exe()
            expected = ["-m", "backend", "serve"]
            command_matches = any(command[i : i + 3] == expected for i in range(len(command)))
            pairs = (
                ("--host", self.instance.host),
                ("--port", str(self.instance.port)),
                ("--data-dir", str(self.instance.data)),
            )
            arguments_match = all(
                any(command[i : i + 2] == list(pair) for i in range(len(command))) for pair in pairs
            )
            base = self.instance.base_python
            if not (
                command_matches
                and arguments_match
                and same_path(state.python, self.instance.python)
                and (
                    same_path(executable, self.instance.python)
                    or (base is not None and same_path(executable, base))
                )
                and same_path(state.data_directory, self.instance.data)
                and state.host == self.instance.host
                and state.port == self.instance.port
                and abs(
                    datetime.fromisoformat(state.started_at).timestamp() - process.create_time()
                )
                <= 0.000001
            ):
                raise ManagerError(
                    "Recorded PID is not this service; refusing to manage it.", "warning"
                )
            return process if process.is_running() else None
        except psutil.NoSuchProcess:
            return None

    def listeners(self) -> set[int]:
        result: set[int] = set()
        for connection in psutil.net_connections(kind="tcp"):
            if (
                connection.status == psutil.CONN_LISTEN
                and connection.laddr
                and connection.laddr.port == self.instance.port
            ):
                if connection.pid is None:
                    raise ManagerError("Listening process identity is unavailable.")
                result.add(connection.pid)
        return result

    def healthy(self) -> bool:
        try:
            with httpx.Client(trust_env=False, timeout=2) as client:
                response = client.get(self.instance.health_url)
                value: object = response.json()
                return (
                    response.is_success
                    and isinstance(value, dict)
                    and value.get("status") == "healthy"
                )
        except httpx.HTTPError, ValueError:
            return False

    def publish_state(self, state: RuntimeRecord) -> None:
        pending = self.instance.runtime_directory / "inkwell.pending.json"
        try:
            with pending.open("w", encoding="utf-8") as stream:
                stream.write(state.model_dump_json())
                stream.flush()
                os.fsync(stream.fileno())
            pending.replace(self.instance.state_path)
        finally:
            pending.unlink(missing_ok=True)

    def status(self, *, lock_held: bool = False) -> Status:
        if not lock_held:
            try:
                with management_lock(self.instance.lock_path, probe=True):
                    pass
            except FileNotFoundError:
                pass
            except LockBusy:
                return Status(
                    "warning", "Another lifecycle operation holds the management lock.", busy=True
                )
        try:
            process = self.owned_process(self.read_state())
        except ManagerError as error:
            if error.state != "warning":
                raise
            return Status(error.state, str(error))
        listeners = self.listeners()
        if process and process.pid in listeners and self.healthy():
            return Status(
                "running", "Process identity, listening port and health check passed.", process.pid
            )
        if process:
            return Status(
                "warning", "Managed process is alive but port or health check failed.", process.pid
            )
        if listeners or self.healthy():
            return Status("warning", "Service port is reachable without a verified owned process.")
        return Status(
            "stopped", "No managed process, listening port or healthy endpoint was found."
        )
