"""Single-process guard (implementation.md §13.2).

The exactly-once guarantees assume one server process per database file. A second uvicorn worker or a
second copy of the app pointed at the same database must refuse to start rather than run beside it.
"""
from __future__ import annotations

import fcntl
import os
from pathlib import Path

_held: dict[str, int] = {}


class AlreadyRunning(RuntimeError):
    pass


def lock_path(database_path: str) -> str:
    return str(Path(database_path).resolve()) + ".lock"


def acquire(database_path: str) -> None:
    path = lock_path(database_path)
    if path in _held:
        return  # this process already holds it
    fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        os.close(fd)
        raise AlreadyRunning(
            f"Another Nipta process is already using {database_path}. Run a single uvicorn worker "
            "(no --workers) and stop any other copy before starting this one."
        ) from None
    os.ftruncate(fd, 0)
    os.write(fd, str(os.getpid()).encode())
    _held[path] = fd


def release(database_path: str) -> None:
    fd = _held.pop(lock_path(database_path), None)
    if fd is not None:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)
