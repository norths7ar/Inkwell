"""Owned-process startup and authenticated graceful shutdown."""

import os
import secrets
import time
from collections.abc import Callable
from datetime import UTC, datetime

import httpx
import psutil

from .contract import ManagerError, RuntimeRecord
from .platform import cleanup_failed_launch, launch
from .runtime import Runtime


class Lifecycle:
    def __init__(self, runtime: Runtime, report: Callable[[str], None] = print):
        self.runtime = runtime
        self.instance = runtime.instance
        self.report = report

    def start(self) -> None:
        current = self.runtime.status(lock_held=True)
        if current.state == "running":
            self.report(f"Inkwell is already running. PID: {current.pid}.")
            return
        if current.state != "stopped":
            raise ManagerError(current.detail, current.state)
        if not self.instance.python.is_file():
            raise ManagerError("Run uv sync --frozen in the repository before starting Inkwell.")
        logs = self.instance.data / "logs"
        logs.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S-%f")
        stdout = logs / f"inkwell-{stamp}.stdout.log"
        stderr = logs / f"inkwell-{stamp}.stderr.log"
        token = secrets.token_hex(32)
        environment = os.environ | {
            "INKWELL_DATA_DIR": str(self.instance.data),
            "INKWELL_SHUTDOWN_TOKEN": token,
            "PYTHONUNBUFFERED": "1",
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONIOENCODING": "utf-8",
        }
        command = [
            str(self.instance.python),
            "-m",
            "backend",
            "serve",
            "--host",
            self.instance.host,
            "--port",
            str(self.instance.port),
            "--data-dir",
            str(self.instance.data),
        ]
        launcher = launch(command, self.instance.root, environment, stdout, stderr)
        try:
            identity = psutil.Process(launcher.pid)
        except psutil.NoSuchProcess:
            launcher.wait(timeout=5)
            raise ManagerError(f"Inkwell exited during startup. See {stderr}") from None
        complete = False
        try:
            deadline = time.monotonic() + 45
            while time.monotonic() < deadline:
                if launcher.poll() is not None:
                    raise ManagerError(f"Inkwell exited during startup. See {stderr}")
                listeners = self.runtime.listeners()
                if len(listeners) == 1 and self.runtime.healthy(next(iter(listeners))):
                    listener = psutil.Process(next(iter(listeners)))
                    if listener.pid != identity.pid and listener.ppid() != identity.pid:
                        raise ManagerError("Port was claimed by an unrelated process.", "warning")
                    state = RuntimeRecord(
                        pid=listener.pid,
                        launcher_pid=identity.pid,
                        port=self.instance.port,
                        host=self.instance.host,
                        started_at=datetime.fromtimestamp(listener.create_time(), UTC).isoformat(),
                        python=str(self.instance.python),
                        data_directory=str(self.instance.data),
                        stdout=str(stdout),
                        stderr=str(stderr),
                        shutdown_token=token,
                    )
                    if self.runtime.owned_process(state) is None:
                        raise ManagerError("Inkwell exited during startup.")
                    self.runtime.publish_state(state)
                    complete = True
                    self.report(f"Inkwell started in background. PID: {listener.pid}.")
                    return
                time.sleep(0.25)
            raise ManagerError(f"Inkwell did not become healthy within 45 seconds. See {stderr}")
        finally:
            if not complete:
                cleanup_failed_launch(identity)
                launcher.wait(timeout=5)

    def stop(self) -> None:
        state = self.runtime.read_state()
        process = self.runtime.owned_process(state)
        if process is None or state is None:
            if self.runtime.listeners() or self.runtime.healthy():
                raise ManagerError(
                    "Unverified process owns the service port; refusing to stop it.", "warning"
                )
            self.instance.state_path.unlink(missing_ok=True)
            self.report("Inkwell is not running.")
            return
        if process.pid not in self.runtime.listeners():
            raise ManagerError(
                "Managed process is not listening. Inspect logs before manual intervention.",
                "warning",
            )
        try:
            with httpx.Client(trust_env=False, timeout=5) as client:
                response = client.post(
                    self.instance.health_url + "/shutdown",
                    headers={"X-Inkwell-Shutdown-Token": state.shutdown_token},
                )
                response.raise_for_status()
        except httpx.HTTPError:
            raise ManagerError(
                "Graceful shutdown request failed. Existing state was preserved."
            ) from None
        deadline = time.monotonic() + 30
        # is_running checks the creation time, so a reused PID cannot extend this wait.
        while process.is_running() and time.monotonic() < deadline:
            time.sleep(0.1)
        if process.is_running():
            raise ManagerError(
                "Shutdown has not finished; no forced termination was performed.", "warning"
            )
        if self.runtime.listeners() or self.runtime.healthy():
            raise ManagerError(
                "Process exited but service port remains reachable. State preserved.", "warning"
            )
        self.instance.state_path.unlink()
        self.report(f"Inkwell stopped gracefully. PID: {process.pid}.")

    def restart(self) -> None:
        self.stop()
        self.start()
