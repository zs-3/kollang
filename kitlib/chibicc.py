"""chibicc support: a small C compiler Kit can build and use instead of gcc.

chibicc (https://github.com/rui314/chibicc) is a ~10k-line C11 compiler for
x86-64 Linux.  Kit uses it for two things:

* ``--cc chibicc`` on build/run/test - compile Kol's generated C with it;
* ``kit jit`` / ``kit run --jit`` - compile and run a program from
  memory-backed storage (/dev/shm and an anonymous memfd), so nothing is
  written to disk.

Facts this module is built around (all verified, see tests/test_kit_chibicc.py):

* The Kol runtime uses the GCC ``__atomic_*`` builtins, which chibicc lacks.
  ``chibicc_compat.h`` provides them on top of chibicc's two *real* atomic
  primitives (compare-and-swap, exchange) and is injected with ``-include``.
  The runtime itself is never modified.  (chibicc's own <stdatomic.h>
  ``atomic_fetch_add`` etc. are plain, non-atomic operations, so they are not
  used.)
* chibicc hardcodes its temp files to /tmp; Kit's build applies a one-line
  patch so it honours ``$TMPDIR`` and can keep them in RAM too.
* chibicc only understands ``-Idir`` (no space), finds its headers in an
  ``include/`` directory next to its own executable, and calls the system
  ``as`` and ``ld``.  It does not optimise, so generated code is slower than
  gcc -O2; it is for fast turnaround, not benchmarks.
"""
from __future__ import annotations

import contextlib
import json
import os
import platform
import shutil
import subprocess
import sys
import tempfile
from typing import Iterator, List, Optional, Tuple

from . import paths
from .errors import KitError
from .ui import console

REPO_URL = "https://github.com/rui314/chibicc.git"
# The exact commit Kit builds (git object ids are content hashes, so pinning
# the commit pins the source).  Tested: Kol's whole test corpus builds and runs.
PINNED_COMMIT = "90d1f7f199cc55b13c7fdb5839d1409806633fdb"

_TMP_ORIGINAL = 'char *path = strdup("/tmp/chibicc-XXXXXX");'
_TMP_PATCHED = ('char *kit_td = getenv("TMPDIR");\n'
                '  char *path = format("%s/chibicc-XXXXXX", kit_td && *kit_td ? kit_td : "/tmp");')


# -- locating ----------------------------------------------------------------

def install_dir() -> str:
    return os.path.join(paths.kit_home(), "tools", "chibicc")


def compat_header() -> str:
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), "chibicc_compat.h")


def platform_problem() -> Optional[str]:
    if not sys.platform.startswith("linux"):
        return f"chibicc only supports Linux (this is {sys.platform})"
    if platform.machine().lower() not in ("x86_64", "amd64"):
        return f"chibicc only supports x86-64 (this is {platform.machine()})"
    return None


def _has_headers(exe: str) -> bool:
    return os.path.isfile(os.path.join(os.path.dirname(exe), "include", "stddef.h"))


def find() -> Optional[str]:
    """Absolute path of a usable chibicc, or None.

    Order: $KIT_CHIBICC, the copy built by ``kit chibicc setup``, then PATH.
    A candidate must have its ``include/`` directory next to it."""
    env = os.environ.get("KIT_CHIBICC")
    if env:
        cand = os.path.abspath(os.path.expanduser(env))
        return cand if os.path.isfile(cand) and os.access(cand, os.X_OK) and _has_headers(cand) else None
    own = os.path.join(install_dir(), "chibicc")
    if os.path.isfile(own) and _has_headers(own):
        return own
    on_path = shutil.which("chibicc")
    if on_path and _has_headers(os.path.abspath(on_path)):
        return os.path.abspath(on_path)
    return None


def missing_system_tools() -> List[str]:
    """chibicc shells out to the system assembler and linker."""
    return [t for t in ("as", "ld") if not shutil.which(t)]


def require() -> str:
    problem = platform_problem()
    if problem:
        raise KitError(problem, hint="use the default C compiler (gcc/clang) on this machine")
    exe = find()
    if exe is None:
        if os.environ.get("KIT_CHIBICC"):
            raise KitError(f"KIT_CHIBICC={os.environ['KIT_CHIBICC']} is not a usable chibicc",
                           hint="it must be an executable with an include/ directory next to it")
        raise KitError("chibicc is not installed",
                       hint="run `kit chibicc setup` (downloads and builds it; needs git, make and a C compiler), "
                            "or set KIT_CHIBICC=/path/to/chibicc")
    missing = missing_system_tools()
    if missing:
        raise KitError(f"chibicc needs the system tool(s): {', '.join(missing)}",
                       hint="install binutils (e.g. `apt install binutils`)")
    return exe


def status() -> dict:
    exe = find()
    info = {"platform_ok": platform_problem() is None, "platform_problem": platform_problem(),
            "path": exe, "missing_tools": missing_system_tools(), "commit": None, "patched": None}
    if exe:
        meta = paths.read_json(os.path.join(os.path.dirname(exe), "BUILD_INFO.json"))
        if meta:
            info["commit"] = meta.get("commit")
            info["patched"] = meta.get("tmpdir_patch")
    return info


# -- building chibicc ------------------------------------------------------------

def apply_tmpdir_patch(source: str) -> str:
    """Make chibicc's temp files honour $TMPDIR (main.c's create_tmpfile)."""
    if _TMP_ORIGINAL not in source:
        raise KitError("cannot patch chibicc: the expected temp-file line was not found in main.c",
                       hint="the upstream source differs from the pinned commit")
    return source.replace(_TMP_ORIGINAL, _TMP_PATCHED, 1)


def _run(cmd: List[str], cwd: str, what: str, timeout: int = 600) -> subprocess.CompletedProcess:
    try:
        res = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout)
    except FileNotFoundError:
        raise KitError(f"'{cmd[0]}' was not found", hint=f"install it to build chibicc ({what})")
    except subprocess.TimeoutExpired:
        raise KitError(f"timed out: {' '.join(cmd[:3])}")
    if res.returncode != 0:
        raise KitError(f"{what} failed", notes=[(res.stderr or res.stdout).strip()[-1500:]])
    return res


def setup(force: bool = False) -> str:
    """Download the pinned chibicc source, patch, build, self-test and install it."""
    problem = platform_problem()
    if problem:
        raise KitError(problem)
    target = install_dir()
    if not force and os.path.isfile(os.path.join(target, "chibicc")):
        meta = paths.read_json(os.path.join(target, "BUILD_INFO.json")) or {}
        if meta.get("commit") == PINNED_COMMIT:
            console.status("Up to date", f"chibicc {PINNED_COMMIT[:9]} at {target}")
            return os.path.join(target, "chibicc")
    cc = next((c for c in ("cc", "gcc", "clang") if shutil.which(c)), None)
    if cc is None:
        raise KitError("no C compiler found to build chibicc", hint="install gcc or clang")
    for tool in ("git", "make"):
        if not shutil.which(tool):
            raise KitError(f"'{tool}' is required to build chibicc", hint=f"install {tool}")

    build_root = tempfile.mkdtemp(prefix="kit-chibicc-")
    try:
        src = os.path.join(build_root, "src")
        os.makedirs(src)
        console.status("Fetching", f"chibicc {PINNED_COMMIT[:9]} from GitHub")
        _run(["git", "init", "-q", src], build_root, "git init")
        _run(["git", "remote", "add", "origin", REPO_URL], src, "git remote")
        try:
            _run(["git", "fetch", "-q", "--depth", "1", "origin", PINNED_COMMIT], src, "git fetch")
        except KitError:
            console.detail("shallow fetch by commit failed; fetching full history")
            _run(["git", "fetch", "-q", "origin"], src, "git fetch")
        _run(["git", "checkout", "-q", PINNED_COMMIT], src, "git checkout")
        head = _run(["git", "rev-parse", "HEAD"], src, "git rev-parse").stdout.strip()
        if head != PINNED_COMMIT:
            raise KitError(f"fetched commit {head} does not match the pinned {PINNED_COMMIT}",
                           hint="refusing to build unexpected source")

        main_c = os.path.join(src, "main.c")
        with open(main_c, "r", encoding="utf-8") as f:
            patched = apply_tmpdir_patch(f.read())
        with open(main_c, "w", encoding="utf-8") as f:
            f.write(patched)

        console.status("Building", "chibicc")
        _run(["make", f"CC={cc}", "-j4", "chibicc"], src, "make")
        built = os.path.join(src, "chibicc")
        if not os.path.isfile(built):
            raise KitError("the build produced no chibicc binary")

        stage = os.path.join(build_root, "stage")
        os.makedirs(stage)
        shutil.copy2(built, os.path.join(stage, "chibicc"))
        shutil.copytree(os.path.join(src, "include"), os.path.join(stage, "include"))
        paths.write_json(os.path.join(stage, "BUILD_INFO.json"),
                         {"commit": PINNED_COMMIT, "repo": REPO_URL, "tmpdir_patch": True})

        console.status("Testing", "chibicc against the Kol runtime")
        _self_test(os.path.join(stage, "chibicc"), build_root)

        os.makedirs(os.path.dirname(target), exist_ok=True)
        if os.path.exists(target):
            shutil.rmtree(target)
        shutil.move(stage, target)
    finally:
        shutil.rmtree(build_root, ignore_errors=True)
    console.status("Installed", f"chibicc {PINNED_COMMIT[:9]} at {target}")
    return os.path.join(target, "chibicc")


_SELF_TEST_C = r"""
#include "kol_runtime.h"
int main(void) {
    uint32_t rc = 1;
    int ok = 1;
    ok &= (__atomic_add_fetch(&rc, 2, __ATOMIC_RELAXED) == 3);
    ok &= (__atomic_sub_fetch(&rc, 1, __ATOMIC_ACQ_REL) == 2);
    ok &= (__atomic_load_n(&rc, __ATOMIC_ACQUIRE) == 2);
    printf(ok ? "chibicc-ok\n" : "chibicc-BAD\n");
    return ok ? 0 : 1;
}
"""


def _self_test(exe: str, workdir: str) -> None:
    """Compile and run a program that includes the real Kol runtime and uses the shim."""
    src = os.path.join(workdir, "selftest.c")
    out = os.path.join(workdir, "selftest")
    with open(src, "w", encoding="utf-8") as f:
        f.write(_SELF_TEST_C)
    cmd = _command(exe, src, out)
    res = subprocess.run(cmd, capture_output=True, text=True, env=dict(os.environ, TMPDIR=workdir))
    if res.returncode != 0:
        raise KitError("the freshly built chibicc failed its self-test (could not compile the Kol runtime)",
                       notes=[res.stderr.strip()[-1500:]])
    run = subprocess.run([out], capture_output=True, text=True)
    if run.returncode != 0 or "chibicc-ok" not in run.stdout:
        raise KitError("the freshly built chibicc failed its self-test (wrong results)")


# -- compiling ------------------------------------------------------------------------

def _command(exe: str, c_file: str, out: str) -> List[str]:
    runtime = os.path.join(paths.compiler_root(), "runtime")
    # chibicc only parses -I<dir> glued together; -I <dir> is silently mis-read.
    return [exe, "-I" + runtime, "-include", compat_header(), "-o", out, c_file, "-lm", "-lpthread"]


def compile_file(c_file: str, out_exe: str, tmpdir: Optional[str] = None) -> None:
    """Compile ``c_file`` (Kol's generated C) to the executable ``out_exe``."""
    exe = require()
    env = dict(os.environ)
    if tmpdir:
        env["TMPDIR"] = tmpdir
    res = subprocess.run(_command(exe, c_file, out_exe), capture_output=True, text=True, env=env)
    if res.returncode != 0:
        raise KitError("chibicc failed to compile the program",
                       notes=[(res.stderr or res.stdout).strip()[-2000:]],
                       hint="this can be a chibicc limitation; retry with the default compiler (omit --cc chibicc)")


# -- running from memory ---------------------------------------------------------------

@contextlib.contextmanager
def ram_workdir() -> Iterator[Tuple[str, bool]]:
    """A private (0700) scratch directory, in /dev/shm (RAM) when available.

    Yields ``(path, in_ram)`` and always removes the directory afterwards."""
    base = "/dev/shm" if os.path.isdir("/dev/shm") and os.access("/dev/shm", os.W_OK | os.X_OK) else None
    d = tempfile.mkdtemp(prefix="kit-jit-", dir=base)
    try:
        yield d, base is not None
    finally:
        shutil.rmtree(d, ignore_errors=True)


def run_from_memory(exe_path: str, args: List[str]) -> int:
    """Run ``exe_path`` from an anonymous in-memory file (memfd) when possible."""
    memfd_create = getattr(os, "memfd_create", None)
    if memfd_create is not None:
        fd = -1
        try:
            fd = memfd_create("kol-jit", 0)  # flags 0: stays open across exec so /proc/self/fd/N works
            with open(exe_path, "rb") as f:
                while True:
                    chunk = f.read(1 << 20)
                    if not chunk:
                        break
                    os.write(fd, chunk)
            sys.stdout.flush()
            return subprocess.run([f"/proc/self/fd/{fd}"] + list(args), pass_fds=(fd,)).returncode
        except OSError as e:
            console.detail(f"memfd execution unavailable ({e}); running from the scratch directory")
        finally:
            if fd >= 0:
                os.close(fd)
    sys.stdout.flush()
    return subprocess.run([exe_path] + list(args)).returncode
