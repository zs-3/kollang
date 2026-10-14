"""A Kol project on disk and the ``sync`` operation that makes it buildable.

``sync`` is the heart of Kit:

    kol.toml --resolve--> package set --lock--> kol.lock
                              |
                              +--install--> store --link--> .kit/link.json

The compiler (``compiler/packages.py``) only reads ``.kit/link.json``.
"""
from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set

from . import lockfile, manifest as manifest_mod, paths, store
from .errors import KitError
from .manifest import Manifest
from .registry import RegistrySet
from .resolver import Node, Resolution, Resolver
from .semver import Version, VersionError
from .ui import console
from compiler import packages as compiler_packages


def kol_version() -> Version:
    """Version of the Kol compiler that Kit ships with."""
    root = paths.compiler_root()
    if root not in sys.path:
        sys.path.insert(0, root)
    try:
        import kol  # type: ignore
        return Version.parse(kol.KOL_VERSION)
    except Exception:
        return Version.parse("0.1.0-alpha")


@dataclass
class SyncResult:
    resolution: Resolution
    lock: lockfile.Lockfile
    lock_changed: bool
    installed: List[Node] = field(default_factory=list)   # newly unpacked
    added: List[Node] = field(default_factory=list)
    removed: List[str] = field(default_factory=list)
    changed: List[tuple] = field(default_factory=list)    # (name, old, new)


class Project:
    def __init__(self, manifest: Manifest, registries: Optional[RegistrySet] = None):
        self.manifest = manifest
        self.root = manifest.root
        self._registries = registries

    # -- discovery ---------------------------------------------------------
    @classmethod
    def discover(cls, start: Optional[str] = None) -> "Project":
        root = manifest_mod.find_root(start or os.getcwd())
        if root is None:
            raise KitError(
                f"could not find {manifest_mod.MANIFEST_NAME} in {start or os.getcwd()} or any parent directory",
                hint="create a project with `kit new <name>` or `kit init`")
        return cls(manifest_mod.load(root))

    @property
    def registries(self) -> RegistrySet:
        if self._registries is None:
            self._registries = RegistrySet.from_config()
        return self._registries

    @property
    def lock_path(self) -> str:
        return os.path.join(self.root, lockfile.LOCK_NAME)

    @property
    def link_path(self) -> str:
        return compiler_packages.link_path(self.root)

    def reload(self) -> None:
        self.manifest = manifest_mod.load(self.root)

    # -- checks ---------------------------------------------------------------
    def check_compiler_version(self) -> None:
        req = self.manifest.kol_req
        if req is not None and not req.matches(kol_version().core):
            raise KitError(
                f"{self.manifest.name} needs kol {req}, but this is kol {kol_version()}",
                hint="upgrade the Kol compiler or relax `kol` in [package]")

    # -- sync -----------------------------------------------------------------
    def sync(self, *, update: Optional[Set[str]] = None, update_all: bool = False,
             frozen: bool = False, include_dev: bool = True,
             quiet_unchanged: bool = False) -> SyncResult:
        """Resolve, lock, install and link.

        update:      package names to re-resolve ignoring their locked version
        update_all:  ignore the lockfile entirely
        frozen:      fail instead of changing/creating kol.lock
        """
        self.check_compiler_version()
        existing = lockfile.Lockfile.load(self.lock_path)
        if frozen and existing is None:
            raise KitError(f"{lockfile.LOCK_NAME} does not exist but --frozen was given",
                           hint="run `kit install` once and commit kol.lock")
        locked_versions = {} if (update_all or existing is None) else existing.versions()
        # Locked entries for packages that are no longer requested are ignored by the resolver.
        resolver = Resolver(
            self.registries, self.manifest, locked=locked_versions,
            unlock=set(update or ()), kol_version=kol_version(), include_dev=include_dev)
        resolution = resolver.resolve()

        new_lock = lockfile.from_resolution(
            self.manifest.name, self.manifest.version, resolution.root_deps,
            resolution.nodes, self.root)
        new_text = new_lock.dumps()
        old_text = None
        if os.path.isfile(self.lock_path):
            with open(self.lock_path, "r", encoding="utf-8") as f:
                old_text = f.read()
        changed_lock = new_text != old_text
        if frozen and changed_lock:
            raise KitError(
                f"{lockfile.LOCK_NAME} is out of date and --frozen was given",
                hint="run `kit install` locally and commit the updated kol.lock")

        result = SyncResult(resolution, new_lock, changed_lock)
        self._diff(existing, new_lock, result)

        # Install before touching any project files: a failed install leaves the project as it was.
        for name in sorted(resolution.nodes):
            node = resolution.nodes[name]
            if node.source == "path":
                continue
            was = store.is_installed(node.name, node.version, node.checksum or "")
            reg = self.registries.by_name(node.registry)
            if reg is None:
                raise KitError(f"registry '{node.registry}' (needed for {node.label}) is not configured",
                               hint="add it with `kit registry add`")
            locked_ck = None
            if existing and name in existing.packages and existing.packages[name].version == node.version:
                ck = existing.packages[name].checksum
                locked_ck = ck.split(":", 1)[1] if ck.startswith("sha256:") else None
            if not was:
                console.status("Installing", node.label)
            store.install(reg, node.entry, expected_checksum=locked_ck)
            if not was:
                result.installed.append(node)

        if changed_lock:
            paths.atomic_write(self.lock_path, new_text)
        self.write_link(resolution)
        self._remember()
        return result

    @staticmethod
    def _diff(old: Optional[lockfile.Lockfile], new: lockfile.Lockfile, result: SyncResult) -> None:
        old_pk = {n: p for n, p in (old.packages.items() if old else []) if p.source}
        new_pk = {n: p for n, p in new.packages.items() if p.source}
        for n in sorted(new_pk):
            if n not in old_pk:
                result.added.append(result.resolution.nodes[n])
            elif old_pk[n].version != new_pk[n].version:
                result.changed.append((n, old_pk[n].version, new_pk[n].version))
        result.removed = sorted(set(old_pk) - set(new_pk))

    # -- link map ------------------------------------------------------------------
    def node_dir(self, node: Node) -> str:
        if node.source == "path":
            return node.path
        return store.package_dir(node.name, node.version, node.checksum or "")

    def write_link(self, resolution: Resolution) -> None:
        m = self.manifest
        data = {
            "format": compiler_packages.LINK_FORMAT,
            "root": {
                "name": m.name,
                "dir": self.root,
                "module_dir": m.module_dir,
                "lib": m.lib_path,
                "deps": sorted(m.deps),
                "dev_deps": sorted(m.dev_deps),
            },
            "packages": {},
        }
        for name, node in sorted(resolution.nodes.items()):
            directory = self.node_dir(node)
            pm = node.manifest
            if pm is None:
                pm = manifest_mod.load(directory)
            data["packages"][name] = {
                "dir": directory,
                "version": str(node.version),
                "module_dir": pm.module_dir,
                "lib": pm.lib_path,
                "deps": sorted(node.deps),
                "source": node.source,
            }
        paths.write_json(self.link_path, data)

    def _remember(self) -> None:
        """Record this project so cache pruning knows which packages are in use."""
        try:
            with paths.home_lock():
                known = paths.read_json(paths.projects_file(), []) or []
                if self.root not in known:
                    known.append(self.root)
                    paths.write_json(paths.projects_file(), sorted(known))
        except OSError:
            pass

    # -- manifest edits -----------------------------------------------------------
    def write_manifest_text(self, text: str) -> None:
        paths.atomic_write(self.manifest.file, text)
        self.reload()
