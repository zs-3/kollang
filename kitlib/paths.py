"""Where Kit keeps things, plus locking and atomic-write helpers.

    $KIT_HOME (default ~/.kit)
        config.toml        registry list
        registry/          the default local registry
        store/             unpacked packages, one directory per exact build
        global/            pseudo-project behind ``kit install -g``
        projects.json      projects Kit has synced (used by cache pruning)
        .lock              cross-process lock for store/registry mutations
"""
from __future__ import annotations

import contextlib
import json
import os
import tempfile
from typing import Any, Dict, Iterator, List, Union

from compiler.packages import global_root, kit_home  # single source of truth

try:
    import fcntl
except ImportError:  # pragma: no cover - non-POSIX
    fcntl = None

__all__ = [
    "kit_home", "global_root", "store_dir", "default_registry_dir",
    "config_file", "projects_file", "compiler_root", "FileLock",
    "atomic_write", "ensure_dir", "read_json", "write_json",
]


def store_dir() -> str:
    return os.path.join(kit_home(), "store")


def default_registry_dir() -> str:
    return os.path.join(kit_home(), "registry")


def config_file() -> str:
    return os.path.join(kit_home(), "config.toml")


def projects_file() -> str:
    return os.path.join(kit_home(), "projects.json")


def compiler_root() -> str:
    """Directory holding kol.py, compiler/ and runtime/."""
    env = os.environ.get("KOL_HOME")
    if env:
        return os.path.abspath(os.path.expanduser(env))
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def ensure_dir(path: str) -> str:
    os.makedirs(path, exist_ok=True)
    return path


def atomic_write(path: str, data: Union[str, bytes]) -> None:
    """Write ``data`` to ``path`` so readers never see a half-written file."""
    directory = os.path.dirname(os.path.abspath(path))
    os.makedirs(directory, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".kit-tmp-", dir=directory)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data.encode("utf-8") if isinstance(data, str) else data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise


def read_json(path: str, default: Any = None) -> Any:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return default
    except ValueError:
        return default


def write_json(path: str, data: Any) -> None:
    atomic_write(path, json.dumps(data, indent=2, sort_keys=True) + "\n")


class FileLock:
    """Exclusive advisory lock on a file; re-entrant within one process."""

    _held: Dict[str, int] = {}

    def __init__(self, path: str):
        self.path = os.path.abspath(path)
        self._fd = None

    def __enter__(self) -> "FileLock":
        if FileLock._held.get(self.path, 0) > 0:
            FileLock._held[self.path] += 1
            return self
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        self._fd = os.open(self.path, os.O_RDWR | os.O_CREAT, 0o644)
        if fcntl is not None:
            fcntl.flock(self._fd, fcntl.LOCK_EX)
        FileLock._held[self.path] = 1
        return self

    def __exit__(self, *exc) -> None:
        FileLock._held[self.path] -= 1
        if FileLock._held[self.path] == 0:
            del FileLock._held[self.path]
            if self._fd is not None:
                if fcntl is not None:
                    fcntl.flock(self._fd, fcntl.LOCK_UN)
                os.close(self._fd)
                self._fd = None


def home_lock() -> FileLock:
    return FileLock(os.path.join(kit_home(), ".lock"))
