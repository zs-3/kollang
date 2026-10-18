"""Package registries.

A registry answers three questions: which versions of ``name`` exist (and what
do they depend on), where is the archive of one version, and - for writable
registries - accept a new version.  The resolver, store and CLI only use the
``Registry`` interface below, so a network registry can be added later by
implementing the same methods.

On-disk layout of a directory registry (modelled on cargo's sparse index so
the same tree can later be served statically over HTTP)::

    registry.json                       {"format": 1}
    index/<shard>/<name>                one JSON object per published version
    packages/<name>/<name>-<ver>.kitpkg immutable archives

``<shard>`` is ``1``, ``2``, ``3/<first letter>`` or ``<ab>/<cd>`` by name length.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import time
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional

from . import INDEX_VERSION, paths
from .errors import KitError
from .semver import Version

_NAME_SAFE = re.compile(r"^[a-z][a-z0-9_]*$")
REGISTRY_META = "registry.json"


@dataclass
class IndexDep:
    name: str
    req: str


@dataclass
class IndexEntry:
    name: str
    version: Version
    deps: List[IndexDep] = field(default_factory=list)
    checksum: str = ""
    yanked: bool = False
    kol: Optional[str] = None
    description: str = ""
    license: str = ""
    keywords: List[str] = field(default_factory=list)
    authors: List[str] = field(default_factory=list)
    size: int = 0
    files: int = 0
    published: str = ""
    registry: str = ""

    def to_json(self) -> dict:
        return {
            "name": self.name,
            "vers": str(self.version),
            "deps": [{"name": d.name, "req": d.req} for d in self.deps],
            "cksum": self.checksum,
            "yanked": self.yanked,
            "kol": self.kol,
            "description": self.description,
            "license": self.license,
            "keywords": self.keywords,
            "authors": self.authors,
            "size": self.size,
            "files": self.files,
            "published": self.published,
        }

    @classmethod
    def from_json(cls, d: dict, registry: str) -> "IndexEntry":
        return cls(
            name=d["name"],
            version=Version.parse(d["vers"]),
            deps=[IndexDep(x["name"], x["req"]) for x in d.get("deps", [])],
            checksum=d.get("cksum", ""),
            yanked=bool(d.get("yanked", False)),
            kol=d.get("kol"),
            description=d.get("description", ""),
            license=d.get("license", ""),
            keywords=list(d.get("keywords", [])),
            authors=list(d.get("authors", [])),
            size=int(d.get("size", 0)),
            files=int(d.get("files", 0)),
            published=d.get("published", ""),
            registry=registry,
        )


def shard(name: str) -> str:
    n = len(name)
    if n == 1:
        return "1"
    if n == 2:
        return "2"
    if n == 3:
        return f"3/{name[0]}"
    return f"{name[:2]}/{name[2:4]}"


def now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


class Registry:
    """Interface every registry implements."""

    name: str = ""
    writable: bool = False

    def has(self, name: str) -> bool:
        raise NotImplementedError

    def entries(self, name: str) -> List[IndexEntry]:
        raise NotImplementedError

    def names(self) -> List[str]:
        raise NotImplementedError

    def fetch(self, entry: IndexEntry) -> str:
        """Local path of the package archive (downloading it if needed)."""
        raise NotImplementedError

    def publish(self, archive_path: str, entry: IndexEntry) -> None:
        raise KitError(f"registry '{self.name}' does not accept published packages")

    def set_yanked(self, name: str, version: Version, yanked: bool) -> None:
        raise KitError(f"registry '{self.name}' does not support yanking")

    def describe(self) -> str:
        return self.name


class DirectoryRegistry(Registry):
    """A registry stored as a directory tree (see module docstring)."""

    writable = True

    def __init__(self, name: str, root: str):
        self.name = name
        self.root = os.path.abspath(root)

    def describe(self) -> str:
        return f"{self.name} ({self.root})"

    # -- layout ----------------------------------------------------------
    def _index_path(self, name: str) -> str:
        return os.path.join(self.root, "index", *shard(name).split("/"), name)

    def archive_path(self, name: str, version: Version) -> str:
        return os.path.join(self.root, "packages", name, f"{name}-{version}.kitpkg")

    def exists(self) -> bool:
        return os.path.isdir(self.root)

    def init(self) -> None:
        os.makedirs(os.path.join(self.root, "index"), exist_ok=True)
        os.makedirs(os.path.join(self.root, "packages"), exist_ok=True)
        meta = os.path.join(self.root, REGISTRY_META)
        if not os.path.exists(meta):
            paths.write_json(meta, {"format": INDEX_VERSION})
        else:
            data = paths.read_json(meta, {}) or {}
            if data.get("format", INDEX_VERSION) > INDEX_VERSION:
                raise KitError(f"registry {self.root} uses format {data['format']}, which is newer "
                               f"than this Kit understands ({INDEX_VERSION})",
                               hint="upgrade Kit")

    # -- reading ----------------------------------------------------------
    def _read_index(self, name: str) -> List[IndexEntry]:
        if not _NAME_SAFE.match(name):
            return []
        path = self._index_path(name)
        try:
            with open(path, "r", encoding="utf-8") as f:
                lines = f.read().splitlines()
        except FileNotFoundError:
            return []
        out: List[IndexEntry] = []
        for lineno, line in enumerate(lines, 1):
            line = line.strip()
            if not line:
                continue
            try:
                out.append(IndexEntry.from_json(json.loads(line), self.name))
            except (ValueError, KeyError, TypeError) as e:
                raise KitError(f"registry index is corrupt: {path}:{lineno}: {e}",
                               hint="restore the file from a backup or republish the package")
        out.sort(key=lambda e: e.version)
        return out

    def has(self, name: str) -> bool:
        return bool(_NAME_SAFE.match(name)) and os.path.isfile(self._index_path(name))

    def entries(self, name: str) -> List[IndexEntry]:
        return self._read_index(name)

    def names(self) -> List[str]:
        found: List[str] = []
        base = os.path.join(self.root, "index")
        for dirpath, _dirs, files in os.walk(base):
            for fn in files:
                if _NAME_SAFE.match(fn):
                    found.append(fn)
        return sorted(set(found))

    def fetch(self, entry: IndexEntry) -> str:
        path = self.archive_path(entry.name, entry.version)
        if not os.path.isfile(path):
            raise KitError(
                f"registry '{self.name}' lists {entry.name} v{entry.version} but its archive is missing",
                notes=[path], hint="republish the package or restore the registry from a backup")
        return path

    # -- writing ----------------------------------------------------------
    def publish(self, archive_path: str, entry: IndexEntry) -> None:
        with paths.FileLock(os.path.join(self.root, ".lock")):
            self.init()
            existing = self._read_index(entry.name)
            if any(e.version == entry.version for e in existing):
                raise KitError(
                    f"{entry.name} v{entry.version} is already published to registry '{self.name}'",
                    hint="published versions are immutable; bump `version` in kol.toml")
            dest = self.archive_path(entry.name, entry.version)
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            tmp = dest + ".tmp"
            shutil.copyfile(archive_path, tmp)
            os.replace(tmp, dest)
            lines = [json.dumps(e.to_json(), sort_keys=True) for e in existing]
            lines.append(json.dumps(entry.to_json(), sort_keys=True))
            paths.atomic_write(self._index_path(entry.name), "\n".join(lines) + "\n")

    def set_yanked(self, name: str, version: Version, yanked: bool) -> None:
        with paths.FileLock(os.path.join(self.root, ".lock")):
            entries = self._read_index(name)
            hit = [e for e in entries if e.version == version]
            if not hit:
                raise KitError(f"{name} v{version} is not in registry '{self.name}'")
            hit[0].yanked = yanked
            lines = [json.dumps(e.to_json(), sort_keys=True) for e in entries]
            paths.atomic_write(self._index_path(name), "\n".join(lines) + "\n")


class RegistrySet:
    """Registries searched in priority order; the first to know a name owns it."""

    def __init__(self, registries: List[Registry]):
        self.registries = registries

    @classmethod
    def from_config(cls) -> "RegistrySet":
        from . import config
        return cls([DirectoryRegistry(r.name, r.path) for r in config.load_registries()])

    def owner(self, name: str) -> Optional[Registry]:
        for reg in self.registries:
            if reg.has(name):
                return reg
        return None

    def entries(self, name: str) -> List[IndexEntry]:
        reg = self.owner(name)
        return reg.entries(name) if reg else []

    def get(self, name: str, version: Version) -> Optional[IndexEntry]:
        for e in self.entries(name):
            if e.version == version:
                return e
        return None

    def names(self) -> List[str]:
        seen = set()
        for reg in self.registries:
            seen.update(reg.names())
        return sorted(seen)

    def by_name(self, registry_name: str) -> Optional[Registry]:
        for reg in self.registries:
            if reg.name == registry_name:
                return reg
        return None

    def default_writable(self) -> Registry:
        for reg in self.registries:
            if reg.writable:
                return reg
        raise KitError("no writable registry is configured",
                       hint="add one with `kit registry add <name> <path>`")

    def search(self, query: str) -> List[IndexEntry]:
        """Latest non-yanked version of every package matching ``query``."""
        q = query.lower().strip()
        hits: List[IndexEntry] = []
        for name in self.names():
            entries = [e for e in self.entries(name) if not e.yanked] or self.entries(name)
            if not entries:
                continue
            latest = entries[-1]
            hay = " ".join([name, latest.description, " ".join(latest.keywords)]).lower()
            if not q or q in hay:
                hits.append(latest)
        hits.sort(key=lambda e: (0 if e.name == q else 1 if e.name.startswith(q) else 2, e.name))
        return hits

    def suggest(self, name: str, n: int = 3) -> List[str]:
        import difflib
        return difflib.get_close_matches(name, self.names(), n=n, cutoff=0.6)
