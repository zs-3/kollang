"""Building, running and testing Kol projects with the real Kol compiler.

Output goes to ``<project>/target/<profile>/``.  The Kol compiler is imported
in-process (``kol.compile_kol_to_c``), so package resolution is exactly what
``kol run``/``kol build`` do.
"""
from __future__ import annotations

import os
import subprocess
import sys
from typing import List, Optional, Tuple

from . import paths
from .errors import KitError
from .manifest import BinTarget
from .project import Project
from .ui import console


def _kol_module():
    root = paths.compiler_root()
    if root not in sys.path:
        sys.path.insert(0, root)
    import kol  # type: ignore
    return kol


def ensure_synced(project: Project) -> None:
    """Resolve/install automatically when dependencies changed or were never installed."""
    needs = not os.path.isfile(project.link_path)
    if not needs:
        link_m = os.path.getmtime(project.link_path)
        for f in (project.manifest.file, project.lock_path):
            if os.path.isfile(f) and os.path.getmtime(f) > link_m:
                needs = True
    if not needs:
        # a dependency may have been removed from the store (e.g. by `kit cache clean`)
        from compiler.packages import _read_link
        data = _read_link(project.link_path) or {}
        for info in data.get("packages", {}).values():
            if not os.path.isdir(info["dir"]):
                needs = True
                break
    if needs:
        project.sync()


def compile_to_c(entry: str, release: bool) -> str:
    kol = _kol_module()
    return kol.compile_kol_to_c(entry, release_mode=release)


def c_compile(c_code: str, out_bin: str, c_file: str, release: bool,
              extra_flags: Optional[List[str]] = None) -> None:
    kol = _kol_module()
    os.makedirs(os.path.dirname(out_bin), exist_ok=True)
    with open(c_file, "w", encoding="utf-8") as f:
        f.write(c_code)
    cc = kol.get_c_compiler()
    runtime_dir = os.path.join(paths.compiler_root(), "runtime")
    opt = ["-O3", "-march=native"] if release else ["-O2"]
    cmd = [cc] + (extra_flags or []) + opt + ["-I", runtime_dir, "-o", out_bin, c_file, "-lm", "-lpthread"]
    res = subprocess.run(cmd, capture_output=True, text=True)
    if res.returncode != 0:
        raise KitError("C compilation failed", notes=[res.stderr.strip()[-2000:]])


def pick_bin(project: Project, name: Optional[str]) -> BinTarget:
    bins = project.manifest.bins
    if not bins:
        raise KitError(f"package '{project.manifest.name}' has no binary target",
                       hint="create src/main.kol, or declare one with [[bin]] in kol.toml")
    if name is None:
        if len(bins) > 1:
            names = ", ".join(b.name for b in bins)
            raise KitError(f"this package has several binaries ({names}); choose one with --bin <name>")
        return bins[0]
    b = project.manifest.bin_named(name)
    if b is None:
        names = ", ".join(x.name for x in bins)
        raise KitError(f"no binary named '{name}' (available: {names})")
    return b


def build_bin(project: Project, target: BinTarget, release: bool) -> str:
    profile = "release" if release else "debug"
    out_dir = os.path.join(project.root, "target", profile)
    out_bin = os.path.join(out_dir, target.name)
    entry = project.manifest.abs(target.path)
    console.status("Compiling", f"{project.manifest.name} v{project.manifest.version} [{profile}] ({target.name})")
    c_code = compile_to_c(entry, release)
    c_compile(c_code, out_bin, out_bin + ".c", release)
    try:
        os.unlink(out_bin + ".c")
    except OSError:
        pass
    console.status("Finished", os.path.relpath(out_bin, os.getcwd()))
    return out_bin


def check_project(project: Project) -> int:
    """Type/syntax-check every target (library, binaries) without producing a binary."""
    kol = _kol_module()
    entries: List[Tuple[str, str]] = []
    if project.manifest.lib_path:
        entries.append(("lib", project.manifest.abs(project.manifest.lib_path)))
    for b in project.manifest.bins:
        entries.append((b.name, project.manifest.abs(b.path)))
    if not entries:
        raise KitError("nothing to check: no src/lib.kol or binary target")
    for label, path in entries:
        console.status("Checking", f"{label} ({os.path.relpath(path, project.root)})")
        kol.compile_kol_to_c(path)
    return len(entries)


def find_tests(project: Project, pattern: Optional[str]) -> List[str]:
    tdir = os.path.join(project.root, "tests")
    if not os.path.isdir(tdir):
        return []
    found = []
    for dirpath, dirnames, filenames in os.walk(tdir):
        dirnames.sort()
        for fn in sorted(filenames):
            if fn.endswith(".kol"):
                full = os.path.join(dirpath, fn)
                if pattern is None or pattern in os.path.relpath(full, tdir):
                    found.append(full)
    return found


def run_test(project: Project, path: str, release: bool) -> Tuple[bool, str]:
    """Compile and run one test program. Passing means exit 0 and, when a
    ``.expected`` file sits next to it, identical stdout."""
    profile = "release" if release else "debug"
    out_dir = os.path.join(project.root, "target", profile, "tests")
    rel = os.path.relpath(path, os.path.join(project.root, "tests"))
    name = os.path.splitext(rel)[0].replace(os.sep, "__")
    out_bin = os.path.join(out_dir, name)
    try:
        c_code = compile_to_c(path, release)
        c_compile(c_code, out_bin, out_bin + ".c", release)
    except KitError as e:
        return False, "build failed: " + e.message + "".join("\n" + n for n in e.notes)
    except Exception as e:  # KolError from the compiler
        fmt = getattr(e, "format", None)
        return False, "compile error: " + (fmt() if fmt else str(e))
    finally:
        try:
            os.unlink(out_bin + ".c")
        except OSError:
            pass
    res = subprocess.run([out_bin], capture_output=True, text=True, errors="replace")
    expected = os.path.splitext(path)[0] + ".expected"
    if res.returncode != 0:
        return False, f"exited with status {res.returncode}\n{res.stdout}{res.stderr}".rstrip()
    if os.path.isfile(expected):
        with open(expected, "r", encoding="utf-8") as f:
            want = f.read().strip()
        if res.stdout.strip() != want:
            return False, f"output mismatch\n--- expected\n{want}\n--- actual\n{res.stdout.strip()}"
    return True, ""
