"""Package-aware import resolution for ``use <package>[.module]``.

``kit`` resolves a project's dependencies and writes a *link map*
(``.kit/link.json``) that records where every package lives on disk.  The
compiler only needs to read that file; it never parses ``kol.toml`` and never
talks to registries.  This module is that reader, plus the format constants
that ``kit`` shares so there is a single source of truth.

Visibility rules (checked per importing file):

* a file in package P may import P's own modules (``use p.util``) and the
  packages P declares as dependencies - not transitive ones;
* the root project's ``tests/``, ``examples/`` and ``benches/`` may also use
  its dev-dependencies;
* outside any project, ``kit install -g`` packages are available.
"""
import json
import os
import re
from typing import Dict, List, Optional, Tuple

LINK_DIR = ".kit"
LINK_FILE = "link.json"
LINK_FORMAT = 1
MANIFEST_NAME = "kol.toml"
DEV_SCOPE_DIRS = ("tests", "examples", "benches")

_SEGMENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def kit_home() -> str:
    """Kit's data directory: ``$KIT_HOME`` or ``~/.kit``."""
    env = os.environ.get("KIT_HOME")
    if env:
        return os.path.abspath(os.path.expanduser(env))
    return os.path.join(os.path.expanduser("~"), ".kit")


def global_root() -> str:
    """The pseudo-project that holds ``kit install -g`` packages."""
    return os.path.join(kit_home(), "global")


def link_path(project_root: str) -> str:
    return os.path.join(project_root, LINK_DIR, LINK_FILE)


def _real(path: str) -> str:
    return os.path.realpath(path)


def _is_within(path: str, directory: str) -> bool:
    return path == directory or path.startswith(directory.rstrip(os.sep) + os.sep)


class PackageContext:
    """The packages visible to one compilation (one project's link map)."""

    def __init__(self, project_root: str, data: Optional[dict], is_global: bool = False):
        self.project_root = project_root
        self.data = data            # None: kol.toml exists but nothing is installed
        self.is_global = is_global
        self._dirs: Optional[List[Tuple[str, str]]] = None

    # -- discovery ---------------------------------------------------------
    @classmethod
    def find(cls, entry_file: str) -> Optional["PackageContext"]:
        """Context for compiling ``entry_file`` (nearest project, else global)."""
        cur = os.path.dirname(os.path.abspath(entry_file))
        while True:
            lp = link_path(cur)
            if os.path.isfile(lp):
                return cls(cur, _read_link(lp))
            if os.path.isfile(os.path.join(cur, MANIFEST_NAME)):
                return cls(cur, None)
            parent = os.path.dirname(cur)
            if parent == cur:
                break
            cur = parent
        groot = global_root()
        lp = link_path(groot)
        if os.path.isfile(lp):
            return cls(groot, _read_link(lp), is_global=True)
        return None

    # -- ownership -----------------------------------------------------------
    def _package_dirs(self) -> List[Tuple[str, str]]:
        """(real directory, package name) pairs, longest directory first."""
        if self._dirs is None:
            pairs: List[Tuple[str, str]] = []
            if self.data:
                root = self.data.get("root", {})
                pairs.append((_real(root.get("dir", self.project_root)), root.get("name", "")))
                for name, info in self.data.get("packages", {}).items():
                    pairs.append((_real(info["dir"]), name))
            pairs.sort(key=lambda p: len(p[0]), reverse=True)
            self._dirs = pairs
        return self._dirs

    def owner_of(self, filepath: str) -> str:
        """Name of the package that contains ``filepath`` (root when none does)."""
        real = _real(filepath)
        for directory, name in self._package_dirs():
            if _is_within(real, directory):
                return name
        return self.data["root"]["name"] if self.data else ""

    def _in_dev_scope(self, filepath: str) -> bool:
        real = _real(filepath)
        root_dir = _real(self.data["root"]["dir"])
        return any(_is_within(real, os.path.join(root_dir, d)) for d in DEV_SCOPE_DIRS)

    # -- resolution ----------------------------------------------------------
    def resolve(self, import_path: str, importing_file: str
                ) -> Tuple[Optional[str], Optional[str], Optional[str]]:
        """Return ``(file, error, hint)``; ``file`` is set on success."""
        parts = import_path.split(".")
        pkg_name, rest = parts[0], parts[1:]
        for seg in rest:
            if not _SEGMENT_RE.match(seg):
                return None, f"invalid module path '{import_path}'", None

        if self.data is None:
            return None, (f"package '{pkg_name}' is not installed: this project has a "
                          f"{MANIFEST_NAME} but no resolved dependencies"), "run `kit install`"

        packages: Dict[str, dict] = self.data.get("packages", {})
        root = self.data["root"]
        owner = self.owner_of(importing_file)
        is_root = owner == root["name"]

        if pkg_name == owner:
            target = root if is_root else packages[owner]
            return self._module_file(pkg_name, target, rest)

        owner_info = root if is_root else packages.get(owner, {})
        visible = set(owner_info.get("deps", []))
        if is_root and self._in_dev_scope(importing_file):
            visible |= set(root.get("dev_deps", []))

        if pkg_name in visible and pkg_name in packages:
            return self._module_file(pkg_name, packages[pkg_name], rest)

        if is_root and pkg_name in root.get("dev_deps", []):
            return None, (f"'{pkg_name}' is a dev-dependency and can only be used from "
                          f"tests/, examples/ and benches/"), "move it to [dependencies] if src/ needs it"

        if pkg_name in packages:
            if is_root:
                return None, (f"package '{pkg_name}' is installed only as a transitive "
                              f"dependency, not a dependency of '{owner}'"), f"add it with `kit add {pkg_name}`"
            return None, (f"package '{owner}' uses '{pkg_name}' but does not declare it "
                          f"as a dependency"), f"'{owner}' needs a [dependencies] entry for '{pkg_name}'"
        if self.is_global:
            hint = f"install it with `kit install -g {pkg_name}`"
        else:
            hint = (f"add it with `kit add {pkg_name}` (`kit search {pkg_name}` looks in your "
                    f"registries)")
        return None, f"no package named '{pkg_name}' is available to this project", hint

    def _module_file(self, pkg_name: str, info: dict, rest: List[str]
                     ) -> Tuple[Optional[str], Optional[str], Optional[str]]:
        base = info["dir"]
        if not os.path.isdir(base):
            return None, f"files of package '{pkg_name}' are missing from disk ({base})", "run `kit install`"
        module_dir = os.path.join(base, info.get("module_dir") or "src")
        if not rest:
            lib = info.get("lib")
            if not lib:
                return None, f"package '{pkg_name}' has no library to import (no src/lib.kol)", None
            entry = os.path.join(base, lib)
            if not os.path.isfile(entry):
                return None, f"package '{pkg_name}' is missing its library file {lib}", "run `kit install`"
            return entry, None, None
        rel = os.path.join(*rest)
        for candidate in (os.path.join(module_dir, rel + ".kol"),
                          os.path.join(module_dir, rel, "lib.kol")):
            if os.path.isfile(candidate) and _is_within(_real(candidate), _real(base)):
                return candidate, None, None
        return None, f"package '{pkg_name}' has no module '{'.'.join(rest)}'", _available(pkg_name, module_dir)


def _available(pkg_name: str, module_dir: str) -> Optional[str]:
    try:
        mods = sorted(
            os.path.splitext(f)[0] for f in os.listdir(module_dir)
            if f.endswith(".kol") and f != "lib.kol"
        )
    except OSError:
        return None
    if not mods:
        return None
    return "modules in '%s': %s" % (pkg_name, ", ".join(mods))


def _read_link(path: str) -> Optional[dict]:
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or data.get("format") != LINK_FORMAT:
        return None
    return data
