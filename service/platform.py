"""Platform-specific management locks and detached launch."""

import os
import subprocess
from collections.abc import Iterator
from contextlib import contextmanager, suppress
from pathlib import Path

import psutil

if os.name == "nt":
    import msvcrt
else:
    import fcntl


class LockBusy(Exception):
    """Another lifecycle action owns this instance."""


@contextmanager
def management_lock(path: Path, *, probe: bool = False) -> Iterator[None]:
    # Probing a stopped instance must not create its data directory or lock file.
    with path.open("r+b" if probe else "a+b") as stream:
        stream.seek(0)
        try:
            if os.name == "nt":
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as error:
            if error.errno not in (11, 13, 35, 36):
                raise
            raise LockBusy from None
        try:
            yield
        finally:
            stream.seek(0)
            if os.name == "nt":
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


def launch(
    command: list[str], root: Path, environment: dict[str, str], stdout: Path, stderr: Path
) -> subprocess.Popen[bytes]:
    with stdout.open("ab", buffering=0) as out, stderr.open("ab", buffering=0) as err:
        return subprocess.Popen(
            command,
            cwd=root,
            env=environment,
            stdin=subprocess.DEVNULL,
            stdout=out,
            stderr=err,
            close_fds=True,
            creationflags=(subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP)
            if os.name == "nt"
            else 0,
            start_new_session=os.name != "nt",
        )


def cleanup_failed_launch(process: psutil.Process) -> None:
    """Only kill the tree created by this failed startup, with PID reuse protection."""
    if not process.is_running():
        return
    children = process.children(recursive=True)
    for child in reversed(children):
        with suppress(psutil.NoSuchProcess):
            child.kill()
    with suppress(psutil.NoSuchProcess):
        process.kill()
    psutil.wait_procs([*children, process], timeout=5)
