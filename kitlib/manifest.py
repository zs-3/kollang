"""The ``kol.toml`` manifest: parsing, validation and target discovery."""
from __future__ import annotations

import difflib
import glob
import os
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from . import names, tomlio
from .errors import KitError
from .semver import Req, Version, VersionError

MANIFEST_NAME = "kol.toml"

PACKAGE_KEYS = {
    "name", "version", "description", "authors", "license", "license-file",
    "repository", "homepage", "readme", "keywords", "kol", "include",
    "exclude", "publish",
}
DEP_KEYS = {"version", "path", "url", "archive", "checksum"}
UNSUPPORTED_DEP_KEYS = {
    "git": "git dependencies need network support, which this offline version of Kit does not have yet",
    "registry": "per-dependency registries are not supported yet (Kit searches the configured registries in priority order)",
    "branch": "git dependencies are not supported yet",
    "tag": "git dependencies are not supported yet",
    "rev": "git dependencies are not supported yet",
    "optional": "optional dependencies are not supported yet",
    "features": "features are not supported yet",
}
KNOWN_TOP_LEVEL = {"package", "dependencies", "dev-dependencies", "lib", "bin", "scripts"}


@dataclass
class Dependency:
    name: str
    req: Optional[Req] = None        # version requirement (registry source)
    path: Optional[str] = None       # path as written, relative to its manifest
    dev: bool = False
    url: Optional[str] = None        # http(s) URL of a .kitpkg archive
    archive: Optional[str] = None    # path of a local .kitpkg, relative to its manifest
    checksum: Optional[str] = None   # sha256 hex that the archive must have (url/archive)

    @property
    def is_path(self) -> bool:
        return self.path is not None

    @property
    def is_archive(self) -> bool:
        return self.url is not None or self.archive is not None

    def describe(self) -> str:
        if self.path is not None:
            return f"path {self.path}" + (f" ({self.req})" if self.req else "")
        if self.url is not None:
            return f"url {self.url}" + (f" ({self.req})" if self.req else "")
        if self.archive is not None:
            return f"archive {self.archive}" + (f" ({self.req})" if self.req else "")
        return str(self.req)


@dataclass
class BinTarget:
    name: str
    path: str  # relative to the package root, forward slashes


@dataclass
class Manifest:
    root: str                      # absolute package directory
    file: str                      # absolute path to kol.toml
    text: str
    name: str
    version: Version
    description: str = ""
    authors: List[str] = field(default_factory=list)
    license: str = ""
    license_file: str = ""
    repository: str = ""
    homepage: str = ""
    readme: str = ""
    keywords: List[str] = field(default_factory=list)
    kol_req: Optional[Req] = None
    include: List[str] = field(default_factory=list)
    exclude: List[str] = field(default_factory=list)
    publish: bool = True
    lib_path: Optional[str] = None
    bins: List[BinTarget] = field(default_factory=list)
    deps: Dict[str, Dependency] = field(default_factory=dict)
    dev_deps: Dict[str, Dependency] = field(default_factory=dict)
    scripts: Dict[str, str] = field(default_factory=dict)
    warnings: List[str] = field(default_factory=list)

    # -- convenience ---------------------------------------------------------
    @property
    def module_dir(self) -> str:
        """Directory that ``use <pkg>.<module>`` paths are relative to."""
        if self.lib_path:
            return os.path.dirname(self.lib_path) or "."
        return "src"

    def abs(self, rel: str) -> str:
        return os.path.normpath(os.path.join(self.root, rel))

    def all_deps(self) -> Dict[str, Dependency]:
        merged = dict(self.deps)
        merged.update(self.dev_deps)
        return merged

    def bin_named(self, name: str) -> Optional[BinTarget]:
        for b in self.bins:
            if b.name == name:
                return b
        return None


# ---------------------------------------------------------------------------

def _fail(file: str, message: str, hint: Optional[str] = None) -> KitError:
    return KitError(f"{file}: {message}", hint=hint)


def _suggest(word: str, options) -> Optional[str]:
    m = difflib.get_close_matches(word, sorted(options), n=1, cutoff=0.6)
    return f"did you mean '{m[0]}'?" if m else None


def _str_list(file: str, table: str, key: str, value) -> List[str]:
    if not isinstance(value, list) or not all(isinstance(x, str) for x in value):
        raise _fail(file, f"[{table}] '{key}' must be an array of strings")
    return list(value)


def _parse_dep(file: str, section: str, name: str, spec, dev: bool) -> Dependency:
    problem = names.check_name(name)
    if problem:
        raise _fail(file, f"[{section}] {problem}")
    if isinstance(spec, str):
        try:
            return Dependency(name, req=Req.parse(spec), dev=dev)
        except VersionError as e:
            raise _fail(file, f"[{section}] {name}: {e}",
                        hint='write a requirement such as "^1.2", "~0.3.1" or ">=1.0, <2.0"')
    if not isinstance(spec, dict):
        raise _fail(file, f"[{section}] {name}: expected a version string or a table like "
                          f'{{ version = "1.0" }}, {{ path = "../{name}" }}, '
                          f'{{ url = "https://.../{name}-1.0.0.kitpkg" }} or {{ archive = "vendor/{name}-1.0.0.kitpkg" }}')
    for key in spec:
        if key in UNSUPPORTED_DEP_KEYS:
            raise _fail(file, f"[{section}] {name}: '{key}' is not supported: {UNSUPPORTED_DEP_KEYS[key]}")
        if key not in DEP_KEYS:
            raise _fail(file, f"[{section}] {name}: unknown key '{key}'",
                        hint=_suggest(key, DEP_KEYS) or "valid keys: version, path")
    req = None
    if "version" in spec:
        if not isinstance(spec["version"], str):
            raise _fail(file, f"[{section}] {name}: 'version' must be a string")
        try:
            req = Req.parse(spec["version"])
        except VersionError as e:
            raise _fail(file, f"[{section}] {name}: {e}")
    path = None
    if "path" in spec:
        if not isinstance(spec["path"], str) or not spec["path"]:
            raise _fail(file, f"[{section}] {name}: 'path' must be a non-empty string")
        path = spec["path"].replace("\\", "/")
    url = archive_path = checksum = None
    if "url" in spec:
        if not isinstance(spec["url"], str) or not spec["url"].lower().startswith(("http://", "https://")):
            raise _fail(file, f"[{section}] {name}: 'url' must be an http:// or https:// URL")
        url = spec["url"]
    if "archive" in spec:
        if not isinstance(spec["archive"], str) or not spec["archive"].endswith(".kitpkg"):
            raise _fail(file, f"[{section}] {name}: 'archive' must be a path to a .kitpkg file")
        archive_path = spec["archive"].replace("\\", "/")
    if "checksum" in spec:
        from .fetch import normalize_checksum
        try:
            checksum = normalize_checksum(str(spec["checksum"]))
        except ValueError as e:
            raise _fail(file, f"[{section}] {name}: {e}")
    sources = [k for k, v in (("path", path), ("url", url), ("archive", archive_path)) if v is not None]
    if len(sources) > 1:
        raise _fail(file, f"[{section}] {name}: {' and '.join(sources)} are different sources; use only one")
    if checksum is not None and url is None and archive_path is None:
        raise _fail(file, f"[{section}] {name}: 'checksum' only applies to 'url' and 'archive' dependencies")
    if req is None and not sources:
        raise _fail(file, f"[{section}] {name}: needs a 'version', 'path', 'url' or 'archive'")
    return Dependency(name, req=req, path=path, dev=dev, url=url, archive=archive_path, checksum=checksum)


def parse(text: str, file: str) -> Manifest:
    """Parse and validate manifest ``text`` that lives at ``file``."""
    file = os.path.abspath(file)
    root = os.path.dirname(file)
    try:
        data = tomlio.loads(text)
    except tomlio.TomlError as e:
        raise _fail(file, f"invalid TOML: {e}")

    warnings: List[str] = []
    for key in data:
        if key not in KNOWN_TOP_LEVEL:
            warnings.append(f"unknown top-level key '{key}' in {MANIFEST_NAME} (ignored)")

    pkg = data.get("package")
    if not isinstance(pkg, dict):
        raise _fail(file, "missing [package] section",
                    hint="a manifest needs at least:\n[package]\nname = \"myapp\"\nversion = \"0.1.0\"")

    for key in pkg:
        if key not in PACKAGE_KEYS:
            warnings.append(f"unknown key '{key}' in [package] (ignored)"
                            + (f"; {_suggest(key, PACKAGE_KEYS)}" if _suggest(key, PACKAGE_KEYS) else ""))

    for req_key in ("name", "version"):
        if req_key not in pkg:
            raise _fail(file, f"[package] is missing '{req_key}'")
    for str_key in ("name", "version", "description", "license", "license-file",
                    "repository", "homepage", "readme", "kol"):
        if str_key in pkg and not isinstance(pkg[str_key], str):
            raise _fail(file, f"[package] '{str_key}' must be a string")

    problem = names.check_name(pkg["name"])
    if problem:
        raise _fail(file, f"[package] {problem}")
    try:
        version = Version.parse(pkg["version"])
    except VersionError as e:
        raise _fail(file, f"[package] {e}")
    if version.build:
        raise _fail(file, "[package] build metadata (+...) is not allowed in package versions")

    kol_req = None
    if "kol" in pkg:
        try:
            kol_req = Req.parse(pkg["kol"])
        except VersionError as e:
            raise _fail(file, f"[package] 'kol': {e}")

    m = Manifest(
        root=root, file=file, text=text, name=pkg["name"], version=version,
        description=pkg.get("description", ""),
        authors=_str_list(file, "package", "authors", pkg.get("authors", [])),
        license=pkg.get("license", ""),
        license_file=pkg.get("license-file", ""),
        repository=pkg.get("repository", ""),
        homepage=pkg.get("homepage", ""),
        readme=pkg.get("readme", ""),
        keywords=_str_list(file, "package", "keywords", pkg.get("keywords", [])),
        kol_req=kol_req,
        include=_str_list(file, "package", "include", pkg.get("include", [])),
        exclude=_str_list(file, "package", "exclude", pkg.get("exclude", [])),
        publish=bool(pkg.get("publish", True)),
        warnings=warnings,
    )

    # -- dependencies --------------------------------------------------------
    for section, attr, dev in (("dependencies", "deps", False),
                               ("dev-dependencies", "dev_deps", True)):
        table = data.get(section, {})
        if not isinstance(table, dict):
            raise _fail(file, f"[{section}] must be a table")
        for dep_name, spec in table.items():
            if dep_name == m.name:
                raise _fail(file, f"[{section}] package '{m.name}' cannot depend on itself")
            getattr(m, attr)[dep_name] = _parse_dep(file, section, dep_name, spec, dev)
    both = sorted(set(m.deps) & set(m.dev_deps))
    if both:
        raise _fail(file, f"'{both[0]}' is listed in both [dependencies] and [dev-dependencies]",
                    hint="keep it in [dependencies] if the package itself uses it")

    # -- targets -------------------------------------------------------------
    lib = data.get("lib")
    if lib is not None:
        if not isinstance(lib, dict):
            raise _fail(file, "[lib] must be a table")
        for key in lib:
            if key != "path":
                raise _fail(file, f"[lib] unknown key '{key}' (only 'path' is supported)")
        if "path" in lib:
            if not isinstance(lib["path"], str) or not lib["path"]:
                raise _fail(file, "[lib] 'path' must be a non-empty string")
            m.lib_path = lib["path"].replace("\\", "/")
    if m.lib_path is None and os.path.isfile(os.path.join(root, "src", "lib.kol")):
        m.lib_path = "src/lib.kol"
    if m.lib_path is not None:
        _check_rel_path(file, "[lib] path", m.lib_path)

    bin_section = data.get("bin")
    explicit: List[dict] = []
    if isinstance(bin_section, dict):
        explicit = [bin_section]
    elif isinstance(bin_section, list):
        explicit = bin_section
    elif bin_section is not None:
        raise _fail(file, "[bin] must be a table or an array of tables")
    for entry in explicit:
        if not isinstance(entry, dict):
            raise _fail(file, "[[bin]] entries must be tables")
        for key in entry:
            if key not in ("name", "path"):
                raise _fail(file, f"[bin] unknown key '{key}' (supported: name, path)")
        bpath = str(entry.get("path", "src/main.kol")).replace("\\", "/")
        bname = str(entry.get("name", m.name))
        _check_rel_path(file, "[bin] path", bpath)
        _check_bin_name(file, bname)
        m.bins.append(BinTarget(bname, bpath))
    if not explicit and os.path.isfile(os.path.join(root, "src", "main.kol")):
        m.bins.append(BinTarget(m.name, "src/main.kol"))
    # src/bin/*.kol -> one extra binary per file (name = file stem)
    taken = {b.name for b in m.bins}
    for extra in sorted(glob.glob(os.path.join(root, "src", "bin", "*.kol"))):
        stem = os.path.splitext(os.path.basename(extra))[0]
        if stem in taken:
            continue
        m.bins.append(BinTarget(stem, "src/bin/" + os.path.basename(extra)))
        taken.add(stem)
    seen: Dict[str, str] = {}
    for b in m.bins:
        if b.name in seen:
            raise _fail(file, f"two binaries are named '{b.name}'")
        seen[b.name] = b.path

    scripts = data.get("scripts", {})
    if not isinstance(scripts, dict) or not all(isinstance(v, str) for v in scripts.values()):
        raise _fail(file, "[scripts] must map names to command strings")
    m.scripts = dict(scripts)

    return m


def _check_rel_path(file: str, label: str, rel: str) -> None:
    norm = os.path.normpath(rel)
    if os.path.isabs(rel) or norm.startswith(".."):
        raise _fail(file, f"{label} '{rel}' must be a relative path inside the package")
    if not rel.endswith(".kol"):
        raise _fail(file, f"{label} '{rel}' must point to a .kol file")


def _check_bin_name(file: str, bname: str) -> None:
    import re
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]*", bname):
        raise _fail(file, f"invalid binary name '{bname}'")


def load(path: str) -> Manifest:
    """Load ``path`` (a kol.toml file or the directory containing one)."""
    if os.path.isdir(path):
        path = os.path.join(path, MANIFEST_NAME)
    if not os.path.isfile(path):
        raise KitError(f"cannot find {MANIFEST_NAME} at {path}")
    with open(path, "r", encoding="utf-8") as f:
        text = f.read()
    return parse(text, path)


def find_root(start: str) -> Optional[str]:
    """Nearest ancestor directory (inclusive) containing kol.toml."""
    cur = os.path.abspath(start)
    if os.path.isfile(cur):
        cur = os.path.dirname(cur)
    while True:
        if os.path.isfile(os.path.join(cur, MANIFEST_NAME)):
            return cur
        parent = os.path.dirname(cur)
        if parent == cur:
            return None
        cur = parent
