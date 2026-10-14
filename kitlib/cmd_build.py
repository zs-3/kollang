"""Commands that build and run code: run, build, check, test, clean, script, doctor."""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time

from . import build, config, lockfile, manifest as manifest_mod, paths, store
from .errors import KitError
from .project import Project, kol_version
from .registry import DirectoryRegistry
from .ui import console, plural


def cmd_build(args) -> int:
    project = Project.discover()
    build.ensure_synced(project)
    targets = project.manifest.bins
    if args.bin or not args.all:
        targets = [build.pick_bin(project, args.bin)] if (args.bin or len(targets) <= 1) else targets
    if not targets:
        raise KitError("nothing to build: no binary target", hint="create src/main.kol")
    for t in targets:
        build.build_bin(project, t, args.release)
    return 0


def cmd_run(args) -> int:
    project = Project.discover()
    build.ensure_synced(project)
    target = build.pick_bin(project, args.bin)
    exe = build.build_bin(project, target, args.release)
    console.status("Running", os.path.relpath(exe, os.getcwd()))
    sys.stdout.flush()
    return subprocess.run([exe] + list(args.args)).returncode


def cmd_check(args) -> int:
    project = Project.discover()
    build.ensure_synced(project)
    n = build.check_project(project)
    console.status("Finished", f"{plural(n, 'target')} OK")
    return 0


def cmd_test(args) -> int:
    project = Project.discover()
    build.ensure_synced(project)
    tests = build.find_tests(project, args.filter)
    if not tests:
        console.info("No tests found (put .kol programs in tests/, with optional .expected output files).")
        return 0
    passed = failed = 0
    start = time.time()
    failures = []
    for path in tests:
        rel = os.path.relpath(path, os.path.join(project.root, "tests"))
        ok, detail = build.run_test(project, path, args.release)
        if ok:
            passed += 1
            console.out(f"test {rel} ... ok")
        else:
            failed += 1
            console.out(f"test {rel} ... FAILED")
            failures.append((rel, detail))
    for rel, detail in failures:
        console.out(f"\n---- {rel} ----\n{detail}")
    console.out(f"\ntest result: {'ok' if not failed else 'FAILED'}. {passed} passed; {failed} failed "
                f"({time.time() - start:.1f}s)")
    return 1 if failed else 0


def cmd_clean(args) -> int:
    project = Project.discover()
    target = os.path.join(project.root, "target")
    if os.path.isdir(target):
        shutil.rmtree(target)
        console.status("Removed", "target/")
    else:
        console.status("Clean", "nothing to remove")
    return 0


def cmd_script(args) -> int:
    project = Project.discover()
    scripts = project.manifest.scripts
    if not args.name:
        if not scripts:
            console.out("No [scripts] defined in kol.toml.")
            return 0
        for name, cmd in sorted(scripts.items()):
            console.out(f"{name:<14} {cmd}")
        return 0
    if args.name not in scripts:
        raise KitError(f"no script named '{args.name}' in [scripts]",
                       hint="`kit script` lists the available scripts")
    env = dict(os.environ)
    env["PATH"] = os.pathsep.join([
        os.path.join(project.root, "target", "debug"),
        os.path.join(project.root, "target", "release"),
        os.path.dirname(os.path.abspath(sys.argv[0])),
        env.get("PATH", ""),
    ])
    env["KIT_PROJECT_ROOT"] = project.root
    cmd = scripts[args.name] + "".join(" " + _shell_quote(a) for a in args.args)
    console.status("Running", f"script '{args.name}': {scripts[args.name]}")
    return subprocess.run(cmd, shell=True, cwd=project.root, env=env).returncode


def _shell_quote(s: str) -> str:
    import shlex
    return shlex.quote(s)


def cmd_version(args) -> int:
    from . import KIT_VERSION
    console.out(f"kit {KIT_VERSION} (kol {kol_version()})")
    return 0


# ---------------------------------------------------------------------------
# doctor
# ---------------------------------------------------------------------------

def cmd_doctor(args) -> int:
    problems = 0

    def ok(msg: str) -> None:
        console.out(f"  ok    {msg}")

    def warn(msg: str) -> None:
        console.out(f"  warn  {msg}")

    def bad(msg: str) -> None:
        nonlocal problems
        problems += 1
        console.out(f"  FAIL  {msg}")

    from . import tomlio, KIT_VERSION
    console.out(f"kit {KIT_VERSION}")
    ok(f"python {sys.version.split()[0]} (TOML via {tomlio.backend()})")
    ok(f"kol compiler {kol_version()} at {paths.compiler_root()}")
    cc = next((c for c in ("gcc", "clang", "tcc") if shutil.which(c)), None)
    (ok if cc else bad)(f"C compiler: {cc or 'none found (install gcc or clang)'}")

    home = paths.kit_home()
    try:
        os.makedirs(home, exist_ok=True)
        probe = os.path.join(home, ".write-test")
        with open(probe, "w") as f:
            f.write("x")
        os.unlink(probe)
        ok(f"KIT_HOME writable: {home}")
    except OSError as e:
        bad(f"KIT_HOME not writable: {home} ({e})")

    try:
        for r in config.load_registries():
            reg = DirectoryRegistry(r.name, r.path)
            if reg.exists():
                ok(f"registry '{r.name}': {plural(len(reg.names()), 'package')} at {r.path}")
            else:
                warn(f"registry '{r.name}' does not exist yet ({r.path}); it is created by `kit publish`")
    except KitError as e:
        bad(e.message)

    broken = []
    for i in store.list_installed():
        p = store.verify(i["dir"])
        if p:
            broken.append(f"{i['name']} v{i['version']}: {p}")
    if broken:
        for b in broken:
            bad(f"store: {b}")
    else:
        ok(f"package store intact ({plural(len(store.list_installed()), 'package')})")

    root = manifest_mod.find_root(os.getcwd())
    if root:
        try:
            project = Project(manifest_mod.load(root))
            for w in project.manifest.warnings:
                warn(w)
            ok(f"project '{project.manifest.name}' v{project.manifest.version} ({root})")
            lock = lockfile.Lockfile.load(project.lock_path)
            if lock is None:
                warn("kol.lock is missing (run `kit install`)")
            elif not os.path.isfile(project.link_path):
                warn("dependencies are not linked yet (run `kit install`)")
            else:
                ok("kol.lock and link map present")
        except KitError as e:
            bad(e.message)
    else:
        console.out("  --    not inside a project")
    if problems:
        console.out(f"\n{plural(problems, 'problem')} found.")
        return 1
    console.out("\nAll good.")
    return 0
