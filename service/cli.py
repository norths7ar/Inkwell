"""Service Desk-compatible command-line interface."""

import argparse
import json
import sys
from contextlib import ExitStack

from .config import Instance
from .contract import ManagerError, Status
from .lifecycle import Lifecycle
from .platform import LockBusy, management_lock
from .runtime import Runtime


class Options(argparse.Namespace):
    action: str
    data_path: str | None
    host: str | None
    port: int | None
    json: bool


def payload(status: Status, instance: Instance) -> dict[str, str | int | bool | None]:
    result: dict[str, str | int | bool | None] = {
        "schema_version": 1,
        "state": status.state,
        "detail": status.detail,
        "pid": status.pid,
        "port": instance.port,
        "data_directory": str(instance.data),
    }
    if status.busy:
        result["busy"] = True
    return result


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(prog="python -m service", description="Manage local Inkwell.")
    result.add_argument(
        "action",
        nargs="?",
        type=str.lower,
        default="status",
        choices=("start", "stop", "restart", "status"),
    )
    result.add_argument("--host", help="Listener host (default: INKWELL_HOST or 127.0.0.1).")
    result.add_argument("--port", type=int, help="Port (default: INKWELL_PORT or 18082).")
    result.add_argument(
        "--data-path",
        "--data-dir",
        dest="data_path",
        help="Data directory (default: INKWELL_DATA_DIR or per-user Inkwell directory).",
    )
    result.add_argument(
        "--json",
        action="store_true",
        help="Emit machine-readable lifecycle result; status always emits JSON.",
    )
    return result


def main(argv: list[str] | None = None) -> int:
    options = parser().parse_args(argv, namespace=Options())
    try:
        instance = Instance.from_arguments(options.data_path, options.host, options.port)
        if not 1 <= instance.port <= 65535:
            raise ValueError("Service management requires a fixed port between 1 and 65535.")
    except Exception as error:
        detail = (
            f"Unable to initialize Inkwell manager: {type(error).__name__}. Check configuration."
        )
        output: dict[str, str | int | bool | None] = {
            "schema_version": 1,
            "state": "unknown",
            "detail": detail,
            "pid": None,
            "port": options.port,
            "data_directory": options.data_path,
        }
        if options.action != "status":
            print(detail, file=sys.stderr)
            output.update(ok=False, action=options.action)
        if options.json or options.action == "status":
            print(json.dumps(output))
        return 1
    runtime = Runtime(instance)
    lifecycle = Lifecycle(runtime, (lambda _: None) if options.json else print)
    try:
        with ExitStack() as resources:
            if options.action == "status":
                print(json.dumps(payload(runtime.status(), instance)))
                return 0
            instance.runtime_directory.mkdir(parents=True, exist_ok=True)
            try:
                resources.enter_context(management_lock(instance.lock_path))
            except LockBusy:
                raise ManagerError(
                    "Another lifecycle operation holds the management lock.", "warning", busy=True
                ) from None
            if options.action == "start":
                lifecycle.start()
            elif options.action == "stop":
                lifecycle.stop()
            else:
                lifecycle.restart()
            status = runtime.status(lock_held=True)
            expected = "stopped" if options.action == "stop" else "running"
            if status.state != expected:
                raise ManagerError(
                    f"Inkwell {options.action} could not be confirmed: {status.detail}",
                    status.state,
                )
            if options.json:
                print(
                    json.dumps(payload(status, instance) | {"ok": True, "action": options.action})
                )
            return 0
    except Exception as error:
        status = (
            Status(error.state, str(error), busy=error.busy)
            if isinstance(error, ManagerError)
            else Status(
                "unknown",
                f"Inkwell {options.action} failed: {type(error).__name__}. Check permissions and logs.",
            )
        )
        if options.action == "status":
            print(json.dumps(payload(status, instance)))
        else:
            print(status.detail, file=sys.stderr)
            if options.json:
                print(
                    json.dumps(payload(status, instance) | {"ok": False, "action": options.action})
                )
        return 1
