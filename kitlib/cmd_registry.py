"""Registry-facing and inspection commands."""
from __future__ import annotations

import json
import os
import shutil
import tempfile
from typing import Dict, List, Optional

from . import archive, config, lockfile, manifest as manifest_mod, paths, store
from .errors import KitError
from .manifest import Manifest
from .project import Project
from .registry import DirectoryRegistry, IndexDep, IndexEntry, RegistrySet, now_iso
from .semver import Req, Version, VersionError
from .ui import console, human_size, plural, table


# ---------------------------------------------------------------------------
# search / info
# ---------------------------------------------------------------------------

def cmd_search(args) -> int:
    regs = RegistrySet.from_config()
    hits = regs.search(args.query or "")
    if not hits:
        console.out(f"No packages match '{args.query}'." if args.query else "The registries are empty.")
        if not any(getattr(r, "exists", lambda: True)() for r in regs.registries):
            console.info("hint: no registry exists yet; `kit publish` creates one.")
        return 0
    rows = [[e.name, str(e.version), e.description[:60] + ("…" if len(e.description) > 60 else "")] for e in hits[:args.limit]]
    console.out(table(rows, ["NAME", "VERSION", "DESCRIPTION"]))
    if len(hits) > args.limit:
        console.info(f"... and {len(hits) - args.limit} more (use --limit)")
    return 0


def cmd_info(args) -> int:
    from .cmd_project import parse_spec
    name, req_s = parse_spec(args.spec)
    regs = RegistrySet.from_config()
    entries = regs.entries(name)
    if not entries:
        suggestions = regs.suggest(name)
        raise KitError(f"package '{name}' was not found in any registry",
                       hint=("did you mean " + " or ".join(f"'{s}'" for s in suggestions) + "?") if suggestions else None)
    req = Req.parse(req_s) if req_s else None
    candidates = [e for e in entries if (req is None or req.matches(e.version)) and not e.yanked] or \
                 [e for e in entries if req is None or req.matches(e.version)]
    if not candidates:
        avail = ", ".join(str(x.version) + (" (yanked)" if x.yanked else "") for x in reversed(entries))
        raise KitError(f"no version of '{name}' matches '{req_s}'", notes=[f"available: {avail}"])
    e = candidates[-1]
    if args.json:
        data = e.to_json()
        data["registry"] = e.registry
        data["versions"] = [{"version": str(x.version), "yanked": x.yanked} for x in entries]
        console.out(json.dumps(data, indent=2))
        return 0
    console.out(f"{e.name} v{e.version}" + ("  (YANKED)" if e.yanked else ""))
    if e.description:
        console.out(e.description)
    console.out("")
    rows = [("license", e.license or "-"), ("kol", e.kol or "any"),
            ("registry", e.registry), ("published", e.published or "-"),
            ("size", f"{human_size(e.size)} in {plural(e.files, 'file')}"),
            ("authors", ", ".join(e.authors) or "-"), ("keywords", ", ".join(e.keywords) or "-"),
            ("checksum", "sha256:" + e.checksum[:16] + "…")]
    for k, v in rows:
        console.out(f"  {k:<10} {v}")
    console.out("")
    if e.deps:
        console.out("  dependencies:")
        for d in e.deps:
            console.out(f"    {d.name} {d.req}")
    else:
        console.out("  dependencies: none")
    console.out("")
    console.out("  versions: " + ", ".join(
        f"{x.version}" + ("*" if x.yanked else "") for x in reversed(entries)) +
        ("   (* = yanked)" if any(x.yanked for x in entries) else ""))
    return 0


# ---------------------------------------------------------------------------
# list / tree / why
# ---------------------------------------------------------------------------

def _lock_for(project: Project) -> lockfile.Lockfile:
    from .build import ensure_synced
    ensure_synced(project)
    lock = lockfile.Lockfile.load(project.lock_path)
    if lock is None:
        raise KitError(f"{lockfile.LOCK_NAME} is missing", hint="run `kit install`")
    return lock


def cmd_list(args) -> int:
    project = Project.discover()
    lock = _lock_for(project)
    direct = project.manifest.all_deps()
    rows = []
    for name in sorted(lock.packages):
        p = lock.packages[name]
        if not p.source:
            continue
        kind = "dev" if name in project.manifest.dev_deps else ("direct" if name in direct else "indirect")
        rows.append([name, str(p.version), kind, p.source])
    if args.json:
        console.out(json.dumps([dict(zip(["name", "version", "kind", "source"], r)) for r in rows], indent=2))
        return 0
    if not rows:
        console.out(f"{project.manifest.name} has no dependencies.")
        return 0
    console.out(table(rows, ["NAME", "VERSION", "KIND", "SOURCE"]))
    return 0


def _tree_lines(lock: lockfile.Lockfile, root: str, max_depth: Optional[int]) -> List[str]:
    lines: List[str] = []
    seen = set()

    def label(n: str) -> str:
        p = lock.packages[n]
        return f"{n} v{p.version}"

    def walk(n: str, prefix: str, depth: int) -> None:
        children = sorted(lock.packages[n].deps)
        for i, c in enumerate(children):
            last = i == len(children) - 1
            branch = "└── " if last else "├── "
            dup = c in seen
            lines.append(prefix + branch + label(c) + (" (*)" if dup and lock.packages[c].deps else ""))
            if dup:
                continue
            seen.add(c)
            if max_depth is None or depth + 1 < max_depth:
                walk(c, prefix + ("    " if last else "│   "), depth + 1)

    lines.append(label(root))
    walk(root, "", 0)
    return lines


def cmd_tree(args) -> int:
    project = Project.discover()
    lock = _lock_for(project)
    root = project.manifest.name
    if args.json:
        def node(n: str, stack: tuple) -> dict:
            p = lock.packages[n]
            return {"name": n, "version": str(p.version),
                    "dependencies": [] if n in stack else [node(d, stack + (n,)) for d in sorted(p.deps)]}
        console.out(json.dumps(node(root, ()), indent=2))
        return 0
    for line in _tree_lines(lock, root, args.depth):
        console.out(line)
    if any(" (*)" in l for l in _tree_lines(lock, root, args.depth)):
        console.out("\n(*) dependencies shown above")
    return 0


def cmd_why(args) -> int:
    project = Project.discover()
    lock = _lock_for(project)
    target = args.name
    if target not in lock.packages or not lock.packages[target].source:
        raise KitError(f"'{target}' is not in this project's dependency graph")
    root = project.manifest.name
    paths_found: List[List[str]] = []

    def dfs(n: str, trail: List[str]) -> None:
        if n == target:
            paths_found.append(trail + [n])
            return
        for d in sorted(lock.packages[n].deps):
            if d not in trail:
                dfs(d, trail + [n])

    dfs(root, [])
    p = lock.packages[target]
    console.out(f"{target} v{p.version} is required by:")
    for trail in paths_found:
        console.out("  " + " -> ".join(
            (n if n == root else f"{n} v{lock.packages[n].version}") for n in trail))
    return 0


# ---------------------------------------------------------------------------
# pack / publish / yank
# ---------------------------------------------------------------------------

def _syntax_check(root: str, files: List[str]) -> None:
    """Parse every .kol file so a broken package can't be published."""
    from .project import kol_version  # noqa: F401  (ensures compiler root on sys.path)
    from compiler.lexer import Lexer
    from compiler.parser import Parser
    for rel in files:
        if not rel.endswith(".kol"):
            continue
        full = os.path.join(root, rel)
        with open(full, "r", encoding="utf-8") as f:
            src = f.read()
        try:
            Parser(Lexer(full, src).tokenize(), full).parse()
        except Exception as e:
            fmt = getattr(e, "format", None)
            raise KitError(f"{rel} does not parse; refusing to package it",
                           notes=[(fmt(src) if fmt else str(e))],
                           hint="fix the error or pass --no-verify")


def _index_entry(m: Manifest, pk: archive.PackResult, registry_name: str) -> IndexEntry:
    deps: List[IndexDep] = []
    for name, d in sorted(m.deps.items()):
        if d.req is None:
            raise KitError(
                f"dependency '{name}' is a path dependency without a version",
                hint=f'published packages need a version: {name} = {{ version = "1.0", path = "{d.path}" }}')
        deps.append(IndexDep(name, str(d.req)))
    return IndexEntry(
        name=m.name, version=m.version, deps=deps, checksum=pk.checksum,
        kol=str(m.kol_req) if m.kol_req else None, description=m.description,
        license=m.license, keywords=m.keywords, authors=m.authors,
        size=pk.size, files=len(pk.files), published=now_iso(), registry=registry_name)


def _prepare_package(project: Project, verify: bool, dest_dir: str) -> tuple:
    m = project.manifest
    files = archive.collect_files(project.root, m)
    if manifest_mod.MANIFEST_NAME not in files:
        raise KitError(f"{manifest_mod.MANIFEST_NAME} would be excluded from the package")
    if not any(f.endswith(".kol") for f in files):
        raise KitError("package contains no .kol files")
    if m.lib_path and m.lib_path not in files:
        raise KitError(f"the library file {m.lib_path} would be excluded from the package")
    if verify:
        _syntax_check(project.root, files)
    dest = os.path.join(dest_dir, f"{m.name}-{m.version}{archive.ARCHIVE_EXT}")
    pk = archive.pack(project.root, m, dest, files)
    if pk.size > archive.MAX_ARCHIVE_BYTES:
        os.unlink(dest)
        raise KitError(f"package is {human_size(pk.size)}; the limit is {human_size(archive.MAX_ARCHIVE_BYTES)}",
                       hint="exclude large files via `exclude` in kol.toml or a .kitignore file")
    return m, pk


def cmd_pack(args) -> int:
    project = Project.discover()
    out_dir = os.path.join(project.root, "target", "package")
    m, pk = _prepare_package(project, not args.no_verify, out_dir)
    if args.list:
        for f in pk.files:
            console.out(f)
    console.status("Packaged", f"{m.name} v{m.version} ({plural(len(pk.files), 'file')}, {human_size(pk.size)})")
    console.info(f"             {os.path.relpath(pk.path, os.getcwd())}")
    console.info(f"             sha256:{pk.checksum}")
    return 0


def cmd_publish(args) -> int:
    project = Project.discover()
    m = project.manifest
    if not m.publish:
        raise KitError(f"'{m.name}' has `publish = false` in kol.toml")
    missing = [k for k, v in (("description", m.description.strip()), ("license", m.license.strip() or m.license_file.strip())) if not v]
    if missing:
        raise KitError(f"[package] is missing: {', '.join(missing)}",
                       hint="registries need a description and a license so people can judge a package")
    if not m.lib_path:
        raise KitError(f"'{m.name}' has no library to publish (no src/lib.kol)",
                       hint="a published package must provide src/lib.kol (or [lib] path = ...)")

    for dname, dep in sorted(m.deps.items()):
        if dep.is_archive:
            raise KitError(
                f"dependency '{dname}' comes from a {'url' if dep.url else 'archive file'}, "
                f"which a published package cannot use",
                hint="publish that package to a registry and depend on it by version")

    regs = RegistrySet.from_config()
    if args.registry:
        reg = regs.by_name(args.registry)
        if reg is None:
            raise KitError(f"registry '{args.registry}' is not configured",
                           hint="`kit registry list` shows the configured registries")
    else:
        reg = regs.default_writable()
    owner = regs.owner(m.name)
    if owner is not None and owner is not reg:
        raise KitError(f"the name '{m.name}' already belongs to registry '{owner.name}'",
                       hint="use that registry (--registry) or choose another package name")

    tmp = tempfile.mkdtemp(prefix="kit-publish-")
    try:
        _m, pk = _prepare_package(project, not args.no_verify, tmp)
        entry = _index_entry(m, pk, reg.name)
        for d in entry.deps:
            if regs.owner(d.name) is None and not m.deps[d.name].is_path:
                console.warn(f"dependency '{d.name}' is not in any configured registry")
        if args.dry_run:
            console.status("Dry run", f"would publish {m.name} v{m.version} to registry '{reg.name}' "
                                      f"({plural(len(pk.files), 'file')}, {human_size(pk.size)})")
            return 0
        reg.publish(pk.path, entry)
        console.status("Published", f"{m.name} v{m.version} to registry '{reg.name}'")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return 0


def cmd_yank(args) -> int:
    from .cmd_project import parse_spec
    name, ver = parse_spec(args.spec)
    if ver is None:
        raise KitError("specify a version: kit yank name@1.2.3")
    try:
        version = Version.parse(ver)
    except VersionError as e:
        raise KitError(str(e))
    regs = RegistrySet.from_config()
    reg = regs.owner(name)
    if reg is None:
        raise KitError(f"package '{name}' was not found in any registry")
    reg.set_yanked(name, version, not args.undo)
    console.status("Unyanked" if args.undo else "Yanked", f"{name} v{version} in registry '{reg.name}'")
    if not args.undo:
        console.info("Existing lockfiles keep working; new resolutions will avoid this version.")
    return 0


# ---------------------------------------------------------------------------
# registry management
# ---------------------------------------------------------------------------

def cmd_registry(args) -> int:
    action = args.action
    regs = config.load_registries()
    if action == "list":
        rows = []
        for r in regs:
            reg = DirectoryRegistry(r.name, r.path)
            count = len(reg.names()) if reg.exists() else 0
            rows.append([r.name, r.path, plural(count, "package") if reg.exists() else "(not created yet)"])
        console.out(table(rows, ["NAME", "PATH", "CONTENTS"]))
        if os.environ.get("KIT_REGISTRY"):
            console.info("\n(KIT_REGISTRY is set and overrides config.toml)")
        return 0
    if os.environ.get("KIT_REGISTRY") and action in ("add", "remove"):
        raise KitError("KIT_REGISTRY is set, which overrides the registry configuration",
                       hint="unset KIT_REGISTRY first")
    if action == "init":
        target = os.path.abspath(args.path or args.name or paths.default_registry_dir())
        DirectoryRegistry("new", target).init()
        console.status("Created", f"registry at {target}")
        return 0
    if action == "add":
        if not args.name or not args.path:
            raise KitError("usage: kit registry add <name> <path>")
        config.check_registry_name(args.name)
        if any(r.name == args.name for r in regs):
            raise KitError(f"a registry named '{args.name}' already exists")
        path = os.path.abspath(os.path.expanduser(args.path))
        reg = DirectoryRegistry(args.name, path)
        reg.init()
        entry = config.RegistryConfig(args.name, path)
        regs.insert(0, entry) if args.first else regs.append(entry)
        config.save_registries(regs)
        console.status("Added", f"registry '{args.name}' -> {path}" + (" (highest priority)" if args.first else ""))
        return 0
    if action == "remove":
        if not args.name:
            raise KitError("usage: kit registry remove <name>")
        left = [r for r in regs if r.name != args.name]
        if len(left) == len(regs):
            raise KitError(f"no registry named '{args.name}'")
        config.save_registries(left)
        console.status("Removed", f"registry '{args.name}' (its files were not deleted)")
        return 0
    raise KitError(f"unknown registry action '{action}'")


# ---------------------------------------------------------------------------
# cache
# ---------------------------------------------------------------------------

def _referenced_store_dirs() -> set:
    refs = set()
    projects = list(paths.read_json(paths.projects_file(), []) or [])
    projects.append(paths.global_root())
    for root in projects:
        link = os.path.join(root, ".kit", "link.json")
        data = paths.read_json(link)
        if not data:
            continue
        for info in data.get("packages", {}).values():
            refs.add(os.path.realpath(info["dir"]))
    return refs


def _downloads():
    base = paths.downloads_dir()
    if not os.path.isdir(base):
        return []
    return [(os.path.join(base, f), os.path.getsize(os.path.join(base, f))) for f in sorted(os.listdir(base))
            if os.path.isfile(os.path.join(base, f))]


def cmd_cache(args) -> int:
    action = args.action
    installed = store.list_installed()
    if action == "list":
        if not installed:
            console.out("The package store is empty.")
            return 0
        rows = [[i["name"], i["version"], human_size(i["size"]), i.get("registry", "")] for i in installed]
        console.out(table(rows, ["NAME", "VERSION", "SIZE", "REGISTRY"]))
        console.out(f"\n{plural(len(installed), 'package')}, {human_size(sum(i['size'] for i in installed))} in {paths.store_dir()}")
        dl = _downloads()
        if dl:
            console.out(f"{plural(len(dl), 'downloaded archive')}, {human_size(sum(sz for _p, sz in dl))} in {paths.downloads_dir()}")
        return 0
    if action == "clean":
        refs = set() if args.all else _referenced_store_dirs()
        freed = 0
        removed = 0
        for i in installed:
            if os.path.realpath(i["dir"]) in refs:
                continue
            freed += i["size"]
            removed += 1
            console.detail(f"removing {i['name']} v{i['version']}")
            if not args.dry_run:
                store.remove(i["dir"])
        if args.all:
            for fp, sz in _downloads():
                freed += sz
                if not args.dry_run:
                    try:
                        os.unlink(fp)
                    except OSError:
                        pass
        verb = "Would remove" if args.dry_run else "Removed"
        console.status(verb, f"{plural(removed, 'package')} ({human_size(freed)})")
        if not args.all and removed == 0:
            console.info("Every cached package is used by a known project.")
        return 0
    if action == "verify":
        bad = 0
        for i in installed:
            problem = store.verify(i["dir"])
            if problem:
                bad += 1
                console.error(f"{i['name']} v{i['version']}: {problem}")
        if bad:
            raise KitError(f"{plural(bad, 'cached package')} failed verification",
                           hint="remove them with `kit cache clean --all`; Kit reinstalls what projects need")
        console.status("Verified", plural(len(installed), "cached package") + " intact")
        return 0
    raise KitError(f"unknown cache action '{action}'")
