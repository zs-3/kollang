"""Dependency resolution.

Kol imports are flat - every function lands in one global namespace - so two
versions of the same package can never coexist in one program.  The resolver
therefore picks exactly **one version per package name** and fails with an
explanation when the requirements cannot all be met.

Strategy: depth-first search with backtracking.  At each step the unresolved
package with the fewest viable versions is chosen (fail-fast), and its
candidates are tried locked-version first, then newest first.  Yanked versions
are only used when already locked; pre-releases only when a requirement asks
for them; versions needing a newer ``kol`` compiler are skipped.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set, Tuple

from . import archive as archive_mod, fetch, manifest as manifest_mod
from .errors import KitError
from .manifest import Manifest
from .registry import IndexEntry, RegistrySet
from .semver import Req, Version, VersionError

MAX_STEPS = 50000


@dataclass
class Requirement:
    name: str
    req: Optional[Req]
    path: Optional[str]          # absolute directory for path dependencies
    requirer: str                # e.g. "myapp" or "greeter v1.2.0"
    url: Optional[str] = None            # archive source: http(s) URL ...
    archive: Optional[str] = None        # ... or absolute path of a local .kitpkg
    checksum: Optional[str] = None       # sha256 hex pinned in kol.toml

    def describe(self) -> str:
        if self.path:
            return f"path {self.path}" + (f" ({self.req})" if self.req else "")
        if self.url:
            return f"url {self.url}" + (f" ({self.req})" if self.req else "")
        if self.archive:
            return f"archive {self.archive}" + (f" ({self.req})" if self.req else "")
        return str(self.req)


@dataclass
class Candidate:
    name: str
    version: Version
    source: str                  # "registry" | "path" | "archive"
    registry: str = ""
    entry: Optional[IndexEntry] = None
    manifest: Optional[Manifest] = None
    path: Optional[str] = None
    deps: List[Requirement] = field(default_factory=list)
    archive_path: Optional[str] = None   # local .kitpkg (archive source)
    origin: Optional[str] = None         # URL or absolute file the archive came from
    checksum: Optional[str] = None       # sha256 hex of the archive

    @property
    def label(self) -> str:
        return f"{self.name} v{self.version}"


@dataclass
class Node:
    """One package in the resolved graph."""
    name: str
    version: Version
    source: str                  # "registry" | "path" | "archive"
    registry: str = ""
    path: Optional[str] = None   # absolute directory (path packages)
    checksum: Optional[str] = None
    deps: List[str] = field(default_factory=list)
    entry: Optional[IndexEntry] = None
    manifest: Optional[Manifest] = None
    archive_path: Optional[str] = None
    origin: Optional[str] = None

    @property
    def label(self) -> str:
        return f"{self.name} v{self.version}"


@dataclass
class Resolution:
    nodes: Dict[str, Node]
    root_deps: List[str]         # direct dependencies of the root (normal + dev)


@dataclass
class _Failure:
    depth: int
    kind: str                    # missing | nomatch | conflict | source
    name: str
    reqs: List[Requirement]
    detail: str = ""
    available: List[str] = field(default_factory=list)


class Resolver:
    def __init__(self, registries: RegistrySet, root: Manifest,
                 locked: Optional[Dict[str, Version]] = None,
                 unlock: Optional[Set[str]] = None,
                 kol_version: Optional[Version] = None,
                 include_dev: bool = True,
                 locked_checksums: Optional[Dict[str, str]] = None):
        self.registries = registries
        self.root = root
        self.locked = locked or {}
        self.unlock = unlock or set()
        self.kol_version = kol_version.core if kol_version else None
        self.include_dev = include_dev
        self.locked_checksums = locked_checksums or {}
        self._archive_cache: Dict[str, Candidate] = {}
        self._failure: Optional[_Failure] = None
        self._steps = 0
        self._path_cache: Dict[str, Candidate] = {}

    # -- public ------------------------------------------------------------
    def resolve(self) -> Resolution:
        constraints: Dict[str, List[Requirement]] = {}
        root_deps: List[str] = []
        deps = dict(self.root.deps)
        if self.include_dev:
            deps.update(self.root.dev_deps)
        for name in sorted(deps):
            constraints[name] = [self._requirement(deps[name], self.root.root, self.root.name)]
            root_deps.append(name)

        result = self._solve({}, constraints, 0)
        if result is None:
            raise self._explain()
        nodes = {}
        for name, cand in result.items():
            nodes[name] = Node(
                name=name, version=cand.version, source=cand.source,
                registry=cand.registry, path=cand.path,
                checksum=cand.entry.checksum if cand.entry else cand.checksum,
                deps=sorted({d.name for d in cand.deps}),
                entry=cand.entry,
                # path packages are read in place; registry/archive packages are read from the store
                manifest=cand.manifest if cand.source == "path" else None,
                archive_path=cand.archive_path, origin=cand.origin,
            )
        self._check_cycles(nodes)
        return Resolution(nodes, root_deps)

    # -- requirement construction -------------------------------------------
    def _requirement(self, dep, base_dir: str, requirer: str) -> Requirement:
        path = None
        if dep.path is not None:
            path = os.path.normpath(os.path.join(base_dir, dep.path))
        archive = None
        if dep.archive is not None:
            archive = os.path.normpath(os.path.join(base_dir, dep.archive))
        return Requirement(dep.name, dep.req, path, requirer,
                           url=dep.url, archive=archive, checksum=dep.checksum)

    # -- candidates ---------------------------------------------------------
    def _path_candidate(self, name: str, directory: str, reqs: List[Requirement]) -> Candidate:
        if directory in self._path_cache:
            return self._path_cache[directory]
        if not os.path.isfile(os.path.join(directory, manifest_mod.MANIFEST_NAME)):
            self._record(_Failure(0, "source", name, reqs,
                                  f"path dependency '{name}' points to {directory}, which has no {manifest_mod.MANIFEST_NAME}"))
            raise _SearchFail()
        m = manifest_mod.load(directory)
        if m.name != name:
            self._record(_Failure(0, "source", name, reqs,
                                  f"the dependency is named '{name}' but {directory} contains package '{m.name}'"))
            raise _SearchFail()
        cand = Candidate(name=name, version=m.version, source="path", path=directory, manifest=m)
        for dname in sorted(m.deps):
            cand.deps.append(self._requirement(m.deps[dname], m.root, cand.label))
        self._path_cache[directory] = cand
        return cand

    def _kol_ok(self, kol_req: Optional[Req]) -> bool:
        if kol_req is None or self.kol_version is None:
            return True
        return kol_req.matches(self.kol_version)

    def _archive_candidate(self, name: str, req: Requirement, reqs: List[Requirement]) -> Candidate:
        """A package that comes from a .kitpkg file or URL: fetch it, verify it, read its manifest."""
        key = req.url or req.archive
        if key in self._archive_cache:
            return self._archive_cache[key]
        expected, expected_from = req.checksum, "kol.toml"
        if expected is None and name not in self.unlock:
            expected, expected_from = self.locked_checksums.get(name), "kol.lock"
        if req.url:
            local, sha = fetch.download(req.url, expected, expected_from)
        else:
            local = req.archive
            if not os.path.isfile(local):
                self._record(_Failure(0, "source", name, reqs, f"archive '{local}' does not exist"))
                raise _SearchFail()
            sha = archive_mod.sha256_file(local)
            if expected and sha != expected:
                raise KitError(
                    f"checksum mismatch for archive {local}\n"
                    f"  {expected_from} expects  sha256:{expected}\n"
                    f"  the file is             sha256:{sha}",
                    hint=f"the archive changed after it was locked. If that is intended, run `kit update {name}`.")
        try:
            m = archive_mod.read_manifest(local)
        except KitError:
            if req.url:  # do not keep a non-package in the download cache
                try:
                    os.unlink(local)
                except OSError:
                    pass
            raise
        if m.name != name:
            self._record(_Failure(0, "source", name, reqs,
                                  f"the dependency is named '{name}' but {key} contains package '{m.name}'"))
            raise _SearchFail()
        if m.kol_req is not None and not self._kol_ok(m.kol_req):
            self._record(_Failure(0, "source", name, reqs,
                                  f"{m.name} v{m.version} needs kol {m.kol_req}, but this is kol {self.kol_version}"))
            raise _SearchFail()
        cand = Candidate(name=name, version=m.version, source="archive", manifest=m,
                         archive_path=local, origin=key, checksum=sha)
        for dname in sorted(m.deps):
            dep = m.deps[dname]
            if dep.path is not None or dep.is_archive:
                raise KitError(
                    f"{cand.label} (from {key}) depends on '{dname}' by {'path' if dep.path else 'url/archive'}, "
                    f"which only works inside its original project",
                    hint="packages distributed as archives may only depend on registry packages")
            cand.deps.append(Requirement(dname, dep.req, None, cand.label))
        self._archive_cache[key] = cand
        return cand

    def _candidates(self, name: str, reqs: List[Requirement], depth: int) -> List[Candidate]:
        # A url/archive requirement pins the package to that file.
        archive_reqs = [r for r in reqs if r.url or r.archive]
        if archive_reqs:
            keys = {r.url or r.archive for r in archive_reqs}
            if len(keys) > 1 or any(r.path for r in reqs):
                self._record(_Failure(depth, "source", name, reqs,
                                      "different sources were requested for the same package"))
                return []
            try:
                cand = self._archive_candidate(name, archive_reqs[0], reqs)
            except _SearchFail:
                return []
            if all(r.req is None or r.req.matches(cand.version) for r in reqs):
                return [cand]
            self._record(_Failure(depth, "nomatch", name, reqs, available=[f"{cand.version} (archive)"]))
            return []

        # A path requirement pins the package to that directory.
        path_reqs = [r for r in reqs if r.path]
        if path_reqs:
            dirs = {r.path for r in path_reqs}
            if len(dirs) > 1:
                self._record(_Failure(depth, "source", name, reqs,
                                      "different path sources were requested for the same package"))
                return []
            try:
                cand = self._path_candidate(name, path_reqs[0].path, reqs)
            except _SearchFail:
                return []
            if all(r.req is None or r.req.matches(cand.version) for r in reqs):
                return [cand]
            self._record(_Failure(depth, "nomatch", name, reqs,
                                  available=[f"{cand.version} (path)"]))
            return []

        entries = self.registries.entries(name)
        if not entries:
            self._record(_Failure(depth, "missing", name, reqs))
            return []
        locked = self.locked.get(name) if name not in self.unlock else None
        viable: List[Candidate] = []
        available: List[str] = []
        for e in sorted(entries, key=lambda x: x.version, reverse=True):
            note = ""
            if e.yanked and not (locked is not None and e.version == locked):
                note = " (yanked)"
            else:
                try:
                    kol_req = Req.parse(e.kol) if e.kol else None
                except VersionError:
                    kol_req = None
                if not self._kol_ok(kol_req):
                    note = f" (needs kol {e.kol})"
            available.append(f"{e.version}{note}")
            if note:
                continue
            if all(r.req is not None and r.req.matches(e.version) for r in reqs):
                viable.append(self._registry_candidate(e))
        if not viable:
            self._record(_Failure(depth, "nomatch", name, reqs, available=available))
            return []
        if locked is not None:
            viable.sort(key=lambda c: 0 if c.version == locked else 1)
        return viable

    def _registry_candidate(self, e: IndexEntry) -> Candidate:
        cand = Candidate(name=e.name, version=e.version, source="registry",
                         registry=e.registry, entry=e)
        for d in e.deps:
            try:
                req = Req.parse(d.req)
            except VersionError as ex:
                raise KitError(f"registry index entry for {e.name} v{e.version} has an invalid "
                               f"requirement for '{d.name}': {ex}")
            cand.deps.append(Requirement(d.name, req, None, cand.label))
        return cand

    # -- search -----------------------------------------------------------------
    def _solve(self, selected: Dict[str, Candidate],
               constraints: Dict[str, List[Requirement]], depth: int
               ) -> Optional[Dict[str, Candidate]]:
        self._steps += 1
        if self._steps > MAX_STEPS:
            raise KitError("dependency resolution is too complex (gave up after "
                           f"{MAX_STEPS} steps)",
                           hint="loosen some version requirements or remove conflicting packages")
        unresolved = sorted(n for n in constraints if n not in selected)
        if not unresolved:
            return selected

        chosen_name = None
        chosen: List[Candidate] = []
        for name in unresolved:
            cands = self._candidates(name, constraints[name], depth)
            if not cands:
                return None
            if chosen_name is None or len(cands) < len(chosen):
                chosen_name, chosen = name, cands

        for cand in chosen:
            new_selected = dict(selected)
            new_selected[chosen_name] = cand
            new_constraints = {k: list(v) for k, v in constraints.items()}
            ok = True
            for dep in cand.deps:
                existing = new_selected.get(dep.name)
                if existing is not None and not self._satisfies(existing, dep):
                    self._record(_Failure(
                        depth + 1, "conflict", dep.name,
                        new_constraints.get(dep.name, []) + [dep],
                        detail=f"{existing.label} is already selected",
                        available=[str(existing.version)]))
                    ok = False
                    break
                new_constraints.setdefault(dep.name, []).append(dep)
            if not ok:
                continue
            result = self._solve(new_selected, new_constraints, depth + 1)
            if result is not None:
                return result
        return None

    @staticmethod
    def _satisfies(cand: Candidate, dep: Requirement) -> bool:
        if dep.path and cand.path != dep.path:
            return False
        if (dep.url or dep.archive) and cand.origin != (dep.url or dep.archive):
            return False
        if dep.req is not None and not dep.req.matches(cand.version):
            return False
        return True

    # -- cycles -----------------------------------------------------------------
    @staticmethod
    def _check_cycles(nodes: Dict[str, Node]) -> None:
        state: Dict[str, int] = {}
        stack: List[str] = []

        def visit(n: str) -> None:
            state[n] = 1
            stack.append(n)
            for d in nodes[n].deps:
                if d not in nodes:
                    continue
                if state.get(d) == 1:
                    cycle = stack[stack.index(d):] + [d]
                    raise KitError("dependency cycle: " + " -> ".join(cycle),
                                   hint="packages cannot depend on each other; move the shared code into a third package")
                if d not in state:
                    visit(d)
            stack.pop()
            state[n] = 2

        for n in sorted(nodes):
            if n not in state:
                visit(n)

    # -- failure reporting ---------------------------------------------------------
    def _record(self, failure: _Failure) -> None:
        if self._failure is None or failure.depth >= self._failure.depth:
            self._failure = failure

    def _explain(self) -> KitError:
        f = self._failure
        if f is None:
            return KitError("failed to resolve dependencies")
        by = "\n".join(f"    {r.describe():<14} required by {r.requirer}" for r in f.reqs)
        if f.kind == "missing":
            suggestions = self.registries.suggest(f.name)
            hint = None
            if suggestions:
                hint = "did you mean " + " or ".join(f"'{s}'" for s in suggestions) + "?"
            elif not any(r.exists() for r in self.registries.registries if hasattr(r, "exists")):
                hint = "no registry exists yet; publish a package with `kit publish`, or use a path dependency"
            else:
                hint = "`kit search` lists the packages in your registries; path dependencies are also supported"
            searched = ", ".join(r.describe() for r in self.registries.registries)
            return KitError(
                f"package '{f.name}' was not found in any registry\n{by}",
                notes=[f"searched: {searched}"], hint=hint)
        if f.kind == "nomatch":
            avail = ", ".join(f.available) if f.available else "none"
            return KitError(
                f"no version of '{f.name}' satisfies all requirements:\n{by}\n"
                f"  available versions: {avail}",
                hint=f"relax one of the requirements; `kit why {f.name}` shows who needs it")
        if f.kind == "conflict":
            return KitError(
                f"conflicting requirements for '{f.name}' ({f.detail}):\n{by}",
                hint="only one version of a package can be used (Kol imports are flat); "
                     "relax a requirement or update the packages that disagree")
        return KitError(f.detail or f"cannot resolve '{f.name}'")


class _SearchFail(Exception):
    """Internal: abandon the current candidate lookup (failure already recorded)."""
