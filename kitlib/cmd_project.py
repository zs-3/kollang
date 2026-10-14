"""Commands that create and change a project: new, init, add, remove, install, update."""
from __future__ import annotations

import os
import re
from typing import List, Optional, Set, Tuple

from . import manifest as manifest_mod, manifest_edit, names, paths
from .errors import KitError
from .project import Project, SyncResult
from .registry import RegistrySet
from .semver import Req, Version, VersionError, latest
from .ui import console, plural

# ---------------------------------------------------------------------------
# templates
# ---------------------------------------------------------------------------

def manifest_template(name: str) -> str:
    return (
        "[package]\n"
        f'name = "{name}"\n'
        'version = "0.1.0"\n'
        'description = ""\n'
        'license = "MIT"\n'
        "\n"
        "[dependencies]\n"
        "\n"
        "[dev-dependencies]\n"
    )


def main_template(name: str) -> str:
    return f'print("Hello from {name}!")\n'


def lib_template(name: str) -> str:
    return (
        f"-- {name}: library entry point. Other projects use it with `use {name}`.\n"
        "-- Imports are flat: every function lands in one global namespace,\n"
        f"-- so prefix public names with the package name to avoid clashes.\n"
        "\n"
        f"fn {name}_greet(who: str) -> str\n"
        '    return "Hello, " + who + "!"\n'
        "end\n"
    )


def lib_test_template(name: str) -> str:
    return (
        f"use {name}\n"
        "\n"
        f'print({name}_greet("Kol"))\n'
    )


GITIGNORE = "target/\n.kit/\n*.c\n"


def readme_template(name: str, lib: bool) -> str:
    body = f"# {name}\n\n"
    if lib:
        body += f"A Kol library.\n\n```kol\nuse {name}\n```\n"
    else:
        body += "A Kol application.\n\n```sh\nkit run\n```\n"
    return body


def _write_new(path: str, name: str, lib: bool, force_tests: bool = True) -> List[str]:
    created = []

    def put(rel: str, text: str) -> None:
        full = os.path.join(path, rel)
        if os.path.exists(full):
            return
        os.makedirs(os.path.dirname(full), exist_ok=True)
        paths.atomic_write(full, text)
        created.append(rel)

    put(manifest_mod.MANIFEST_NAME, manifest_template(name))
    if lib:
        put("src/lib.kol", lib_template(name))
        put("tests/basic.kol", lib_test_template(name))
        put("tests/basic.expected", "Hello, Kol!\n")
    else:
        put("src/main.kol", main_template(name))
    put(".gitignore", GITIGNORE)
    put("README.md", readme_template(name, lib))
    return created


def cmd_new(args) -> int:
    target = os.path.abspath(args.path)
    name = args.name or names.sanitize(os.path.basename(target))
    problem = names.check_name(name)
    if problem:
        raise KitError(problem, hint="choose another name with --name")
    if os.path.exists(target):
        raise KitError(f"destination '{args.path}' already exists",
                       hint="use `kit init` to turn an existing directory into a project")
    os.makedirs(target)
    created = _write_new(target, name, args.lib)
    kind = "library" if args.lib else "application"
    console.status("Created", f"{kind} '{name}' in {args.path}")
    for rel in created:
        console.detail(f"  {rel}")
    console.info(f"\nNext: cd {args.path} && kit run" if not args.lib else f"\nNext: cd {args.path} && kit test")
    return 0


def cmd_init(args) -> int:
    target = os.path.abspath(args.path)
    if not os.path.isdir(target):
        raise KitError(f"'{args.path}' is not a directory")
    if os.path.exists(os.path.join(target, manifest_mod.MANIFEST_NAME)):
        raise KitError(f"{manifest_mod.MANIFEST_NAME} already exists in {args.path}")
    name = args.name or names.sanitize(os.path.basename(target))
    problem = names.check_name(name)
    if problem:
        raise KitError(problem, hint="choose another name with --name")
    has_sources = os.path.isdir(os.path.join(target, "src")) and any(
        f.endswith(".kol") for f in os.listdir(os.path.join(target, "src")))
    lib = args.lib
    created = _write_new(target, name, lib) if not has_sources else _write_manifest_only(target, name)
    console.status("Initialized", f"package '{name}' in {args.path}")
    for rel in created:
        console.detail(f"  {rel}")
    return 0


def _write_manifest_only(target: str, name: str) -> List[str]:
    paths.atomic_write(os.path.join(target, manifest_mod.MANIFEST_NAME), manifest_template(name))
    return [manifest_mod.MANIFEST_NAME]


# ---------------------------------------------------------------------------
# dependency specs
# ---------------------------------------------------------------------------

def parse_spec(spec: str) -> Tuple[str, Optional[str]]:
    """``name`` or ``name@requirement``."""
    if "@" in spec:
        name, req = spec.split("@", 1)
    else:
        name, req = spec, None
    name = name.strip()
    problem = names.check_name(name)
    if problem:
        raise KitError(f"bad package spec '{spec}': {problem}")
    if req is not None:
        req = req.strip()
        try:
            Req.parse(req)
        except VersionError as e:
            raise KitError(f"bad package spec '{spec}': {e}")
    return name, req


def pick_requirement(registries: RegistrySet, name: str, explicit: Optional[str]) -> str:
    """The requirement string to write into kol.toml for ``name``."""
    entries = registries.entries(name)
    if not entries:
        suggestions = registries.suggest(name)
        hint = ("did you mean " + " or ".join(f"'{s}'" for s in suggestions) + "?") if suggestions \
            else "`kit search` lists available packages; use --path for a local package"
        raise KitError(f"package '{name}' was not found in any registry", hint=hint)
    if explicit:
        req = Req.parse(explicit)
        if not any(req.matches(e.version) and not e.yanked for e in entries):
            avail = ", ".join(str(e.version) + (" (yanked)" if e.yanked else "") for e in entries)
            raise KitError(f"no version of '{name}' matches '{explicit}'",
                           notes=[f"available: {avail}"])
        return explicit
    versions = [e.version for e in entries if not e.yanked]
    best = latest(versions)
    if best is None:  # only pre-releases (or only yanked)
        best = latest(versions, allow_pre=True)
    if best is None:
        raise KitError(f"every version of '{name}' has been yanked")
    return f"^{best}"


def _sync_report(result: SyncResult) -> None:
    for node in result.added:
        console.status("Added", node.label)
    for name, old, new in result.changed:
        console.status("Updated", f"{name} v{old} -> v{new}")
    for name in result.removed:
        console.status("Removed", name)


def cmd_add(args) -> int:
    project = Project.discover()
    original = project.manifest.text
    table = "dev-dependencies" if args.dev else "dependencies"
    other = "dependencies" if args.dev else "dev-dependencies"
    text = original
    specs: List[Tuple[str, Optional[str], Optional[str]]] = []  # name, req, path

    if args.path:
        if len(args.specs) > 1:
            raise KitError("--path takes a single package")
        directory = os.path.abspath(args.path)
        pm = manifest_mod.load(directory)
        name = pm.name
        if args.specs:
            wanted, _ = parse_spec(args.specs[0])
            if wanted != name:
                raise KitError(f"{args.path} contains package '{name}', not '{wanted}'")
        rel = os.path.relpath(directory, project.root).replace(os.sep, "/")
        specs.append((name, None, rel))
    else:
        if not args.specs:
            raise KitError("nothing to add", hint="usage: kit add <package>[@requirement] ... [--dev] [--path DIR]")
        for raw in args.specs:
            name, explicit = parse_spec(raw)
            req = pick_requirement(project.registries, name, explicit)
            specs.append((name, req, None))

    for name, req, path in specs:
        if name == project.manifest.name:
            raise KitError(f"a package cannot depend on itself ('{name}')")
        if other in manifest_edit.tables_with(text, name):
            text, _ = manifest_edit.remove_dependency(text, other, name)
            console.warn(f"'{name}' moved from [{other}] to [{table}]")
        text = manifest_edit.set_dependency(text, table, name, req, path)
        shown = f"path {path}" if path else req
        console.status("Adding", f"{name} {shown} to [{table}]")

    project.write_manifest_text(text)
    try:
        result = project.sync()
    except BaseException:
        paths.atomic_write(project.manifest.file, original)  # roll back
        console.warn(f"{manifest_mod.MANIFEST_NAME} restored: nothing was changed")
        raise
    _sync_report(result)
    return 0


def cmd_remove(args) -> int:
    project = Project.discover()
    original = project.manifest.text
    text = original
    for name in args.names:
        found = manifest_edit.tables_with(text, name)
        if not found:
            raise KitError(f"'{name}' is not a dependency of {project.manifest.name}",
                           hint="`kit list` shows the current dependencies")
        for table in found:
            text, _ = manifest_edit.remove_dependency(text, table, name)
        console.status("Removing", f"{name} from [{', '.join(found)}]")
    project.write_manifest_text(text)
    try:
        result = project.sync()
    except BaseException:
        paths.atomic_write(project.manifest.file, original)
        console.warn(f"{manifest_mod.MANIFEST_NAME} restored: nothing was changed")
        raise
    _sync_report(result)
    return 0


# ---------------------------------------------------------------------------
# install / update
# ---------------------------------------------------------------------------

GLOBAL_NAME = "global_env"


def global_project() -> Project:
    root = paths.global_root()
    manifest_file = os.path.join(root, manifest_mod.MANIFEST_NAME)
    if not os.path.isfile(manifest_file):
        os.makedirs(root, exist_ok=True)
        paths.atomic_write(manifest_file, manifest_template(GLOBAL_NAME).replace(
            'description = ""', 'description = "Packages installed with `kit install -g`"'))
    return Project(manifest_mod.load(root))


def _install_into(project: Project, specs: List[str], label: str) -> int:
    original = project.manifest.text
    text = original
    for raw in specs:
        name, explicit = parse_spec(raw)
        req = pick_requirement(project.registries, name, explicit)
        text = manifest_edit.set_dependency(text, "dependencies", name, req, None)
        console.status("Adding", f"{name} {req} {label}")
    project.write_manifest_text(text)
    try:
        result = project.sync()
    except BaseException:
        paths.atomic_write(project.manifest.file, original)
        raise
    _sync_report(result)
    return 0


def cmd_install(args) -> int:
    if args.global_:
        project = global_project()
        if not args.specs:
            result = project.sync(frozen=args.frozen)
            _sync_report(result)
            return 0
        _install_into(project, args.specs, "(global)")
        console.info("Global packages can be used from any Kol file: `use <package>`")
        return 0

    root = manifest_mod.find_root(os.getcwd())
    if args.specs:
        if root is None:
            console.info("No project found here, so installing globally (for scripts).")
            _install_into(global_project(), args.specs, "(global)")
            return 0
        add_args = type("A", (), {"specs": args.specs, "dev": False, "path": None})()
        return cmd_add(add_args)

    project = Project.discover()
    result = project.sync(frozen=args.frozen)
    _sync_report(result)
    total = len(result.resolution.nodes)
    if not (result.added or result.changed or result.removed or result.installed):
        console.status("Up to date", plural(total, "dependency", "dependencies"))
    else:
        console.status("Done", plural(total, "package") + " in the dependency graph")
    return 0


def cmd_update(args) -> int:
    project = Project.discover()
    names_: Set[str] = set()
    if args.names:
        locked = set(project.manifest.all_deps())
        from . import lockfile
        lock = lockfile.Lockfile.load(project.lock_path)
        known = locked | (set(lock.packages) if lock else set())
        for n in args.names:
            if n not in known:
                raise KitError(f"'{n}' is not in this project's dependency graph")
        names_ = set(args.names)
    result = project.sync(update=names_, update_all=not args.names)
    _sync_report(result)
    if not (result.added or result.changed or result.removed):
        console.status("Up to date", "nothing to update")
    return 0
