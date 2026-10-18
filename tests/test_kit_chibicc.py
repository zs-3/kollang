#!/usr/bin/env python3
"""Tests for Kit's chibicc support (--cc chibicc, kit jit, kit chibicc).

Tests that need a real chibicc binary run only when one is available:
$KIT_TEST_CHIBICC, $KIT_CHIBICC, ~/.kit/tools/chibicc/chibicc (what
`kit chibicc setup` installs) or `chibicc` on PATH.  Otherwise they are
skipped (and say so); the rest always run.
"""
import glob
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
import warnings

warnings.simplefilter("ignore", ResourceWarning)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
KIT = os.path.join(ROOT, "kit.py")

from kitlib import chibicc  # noqa: E402
from kitlib.errors import KitError  # noqa: E402


def _usable(path):
    return bool(path) and os.path.isfile(path) and os.access(path, os.X_OK) and \
        os.path.isfile(os.path.join(os.path.dirname(os.path.abspath(path)), "include", "stddef.h"))


def find_chibicc():
    for cand in (os.environ.get("KIT_TEST_CHIBICC"), os.environ.get("KIT_CHIBICC"),
                 os.path.expanduser("~/.kit/tools/chibicc/chibicc"), shutil.which("chibicc")):
        if _usable(cand):
            return os.path.abspath(cand)
    return None


CHIBICC = find_chibicc()
NEEDS = None
if chibicc.platform_problem():
    NEEDS = chibicc.platform_problem()
elif CHIBICC is None:
    NEEDS = "no chibicc available (run `kit chibicc setup`)"
elif chibicc.missing_system_tools():
    NEEDS = "missing system tools: " + ", ".join(chibicc.missing_system_tools())


class Env:
    def __init__(self, with_chibicc=True):
        self.tmp = tempfile.mkdtemp(prefix="kit-cc-test-")
        self.home = os.path.join(self.tmp, "home")
        self.work = os.path.join(self.tmp, "work")
        os.makedirs(self.work)
        self.env = dict(os.environ, KIT_HOME=self.home, NO_COLOR="1")
        self.env.pop("KIT_REGISTRY", None)
        self.env.pop("KIT_CC", None)
        if with_chibicc and CHIBICC:
            self.env["KIT_CHIBICC"] = CHIBICC

    def close(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def kit(self, *args, cwd=None, env_extra=None, stdin=None):
        env = dict(self.env, **(env_extra or {}))
        res = subprocess.run([sys.executable, KIT] + list(args), cwd=cwd or self.work,
                             capture_output=True, text=True, env=env, input=stdin)
        res.all = res.stdout + res.stderr
        return res

    def write(self, path, text):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            f.write(text)
        return path


def shm_leftovers():
    try:
        return sorted(n for n in os.listdir("/dev/shm") if n.startswith("kit-jit-"))
    except OSError:
        return []


class AlwaysRunTests(unittest.TestCase):
    def setUp(self):
        self.e = Env(with_chibicc=False)
        self.addCleanup(self.e.close)

    def test_runtime_is_not_modified_for_chibicc(self):
        # chibicc support lives entirely in kitlib/; the Kol runtime stays compiler-neutral
        for header in glob.glob(os.path.join(ROOT, "runtime", "*.h")):
            text = open(header).read().lower()
            for needle in ("chibicc", "__tinyc__", "kit_compat", "kol_tls"):
                self.assertNotIn(needle, text, f"{os.path.basename(header)} mentions {needle}")

    def test_compat_header_ships(self):
        self.assertTrue(os.path.isfile(chibicc.compat_header()))

    def test_tmpdir_patch(self):
        src = 'int x;\n  ' + chibicc._TMP_ORIGINAL + '\n  int fd = mkstemp(path);\n'
        out = chibicc.apply_tmpdir_patch(src)
        self.assertIn('getenv("TMPDIR")', out)
        self.assertNotIn('strdup("/tmp/chibicc-XXXXXX")', out)
        with self.assertRaises(KitError):
            chibicc.apply_tmpdir_patch("int main(void) { return 0; }")

    def test_missing_chibicc_gives_instructions(self):
        if chibicc.platform_problem():
            self.skipTest(chibicc.platform_problem())
        f = self.e.write(os.path.join(self.e.work, "a.kol"), "print(1)\n")
        env = dict(PATH="/usr/bin:/bin")
        r = self.e.kit("jit", f, env_extra=env)
        self.assertEqual(r.returncode, 1)
        self.assertIn("chibicc is not installed", r.all)
        self.assertIn("kit chibicc setup", r.all)
        r = self.e.kit("jit", f, env_extra=dict(KIT_CHIBICC="/nonexistent/chibicc", **env))
        self.assertEqual(r.returncode, 1)
        self.assertIn("KIT_CHIBICC", r.all)
        r = self.e.kit("chibicc", "status", env_extra=env)
        self.assertEqual(r.returncode, 1)
        self.assertIn("not installed", r.all)

    def test_unknown_compiler_is_rejected(self):
        self.assertEqual(self.e.kit("build", "--cc", "nope").returncode, 2)
        r = self.e.kit("build", env_extra={"KIT_CC": "nope"}, cwd=self.e.work)
        self.assertEqual(r.returncode, 1)  # no project here, so this error comes first; still fails cleanly

    def test_default_compiler_still_gcc(self):
        proj = os.path.join(self.e.work, "p")
        self.e.write(os.path.join(proj, "kol.toml"), '[package]\nname = "p"\nversion = "0.1.0"\n')
        self.e.write(os.path.join(proj, "src", "main.kol"), 'print("gcc path")\n')
        r = self.e.kit("run", "-q", cwd=proj)
        self.assertEqual((r.returncode, r.stdout.strip()), (0, "gcc path"), r.all)


@unittest.skipIf(NEEDS, NEEDS or "")
class ChibiccTests(unittest.TestCase):
    def setUp(self):
        self.e = Env()
        self.addCleanup(self.e.close)

    def project(self, main, name="app", extra_toml=""):
        d = os.path.join(self.e.work, name)
        self.e.write(os.path.join(d, "kol.toml"), f'[package]\nname = "{name}"\nversion = "0.1.0"\n{extra_toml}')
        self.e.write(os.path.join(d, "src", "main.kol"), main)
        return d

    # -- the compat header ---------------------------------------------------
    def test_compat_header_semantics(self):
        c = r'''
#include <stdio.h>
#include <stdint.h>
int main(void) {
    int bad = 0;
    uint32_t a = 5; uint64_t b = 5; int c = 5;
    bad += __atomic_add_fetch(&a, 3, __ATOMIC_RELAXED) != 8;
    bad += __atomic_add_fetch(&b, 3, __ATOMIC_SEQ_CST) != 8;
    bad += __atomic_sub_fetch(&a, 2, __ATOMIC_ACQ_REL) != 6;
    bad += __atomic_sub_fetch(&b, 9, __ATOMIC_ACQ_REL) != (uint64_t)-1;
    bad += __atomic_load_n(&a, __ATOMIC_ACQUIRE) != 6;
    __atomic_store_n(&c, 42, __ATOMIC_RELEASE);
    bad += c != 42;
    bad += __atomic_exchange_n(&c, 7, __ATOMIC_SEQ_CST) != 42;
    bad += c != 7;
    int expected = 7;
    bad += !__atomic_compare_exchange_n(&c, &expected, 9, 0, __ATOMIC_SEQ_CST, __ATOMIC_SEQ_CST);
    bad += c != 9;
    expected = 1;
    bad += __atomic_compare_exchange_n(&c, &expected, 11, 0, __ATOMIC_SEQ_CST, __ATOMIC_SEQ_CST);
    bad += expected != 9;      /* failure writes back the current value */
    bad += c != 9;
    __builtin_ia32_pause();
    printf("%d\n", bad);
    return bad;
}
'''
        src = self.e.write(os.path.join(self.e.tmp, "compat.c"), c)
        out = os.path.join(self.e.tmp, "compat")
        r = subprocess.run([CHIBICC, "-include", chibicc.compat_header(), "-o", out, src],
                           capture_output=True, text=True, env=dict(os.environ, TMPDIR=self.e.tmp))
        self.assertEqual(r.returncode, 0, r.stderr)
        r = subprocess.run([out], capture_output=True, text=True)
        self.assertEqual((r.returncode, r.stdout.strip()), (0, "0"))

    # -- kit build / run / test with --cc chibicc -------------------------------------
    def test_build_and_run_with_chibicc(self):
        d = self.project('print("built by chibicc")\n')
        r = self.e.kit("build", "--cc", "chibicc", cwd=d)
        self.assertEqual(r.returncode, 0, r.all)
        self.assertIn("with chibicc", r.all)
        out = subprocess.run([os.path.join(d, "target", "debug", "app")], capture_output=True, text=True)
        self.assertEqual(out.stdout.strip(), "built by chibicc")
        r = self.e.kit("run", "-q", "--cc", "chibicc", cwd=d)
        self.assertEqual((r.returncode, r.stdout.strip()), (0, "built by chibicc"), r.all)

    def test_kit_cc_env_selects_chibicc(self):
        d = self.project('print("via env")\n')
        r = self.e.kit("build", cwd=d, env_extra={"KIT_CC": "chibicc"})
        self.assertIn("with chibicc", r.all)
        self.assertEqual(r.returncode, 0, r.all)

    def test_kit_test_with_chibicc(self):
        d = self.project('print(1)\n')
        self.e.write(os.path.join(d, "tests", "t.kol"), "print(2 + 3)\n")
        self.e.write(os.path.join(d, "tests", "t.expected"), "5\n")
        r = self.e.kit("test", "--cc", "chibicc", cwd=d)
        self.assertEqual(r.returncode, 0, r.all)
        self.assertIn("1 passed", r.all)

    # -- kit jit ------------------------------------------------------------------------
    def test_jit_runs_and_leaves_nothing_behind(self):
        before = shm_leftovers()
        f = self.e.write(os.path.join(self.e.work, "h.kol"), 'let x = 21\nprint("hello {x * 2}")\n')
        r = self.e.kit("jit", f)
        self.assertEqual((r.returncode, r.stdout.strip()), (0, "hello 42"), r.all)
        self.assertEqual(shm_leftovers(), before)
        self.assertEqual(sorted(os.listdir(self.e.work)), ["h.kol"])  # nothing written next to the source
        self.assertFalse([n for n in os.listdir("/tmp") if n.startswith("chibicc-")])

    def test_jit_uses_ram_scratch_directory(self):
        if not os.path.isdir("/dev/shm"):
            self.skipTest("no /dev/shm")
        f = self.e.write(os.path.join(self.e.work, "h.kol"), 'print("x")\n')
        r = self.e.kit("jit", "-v", f)
        self.assertIn("compiled in RAM (/dev/shm/kit-jit-", r.all)

    def test_jit_stdin_exit_code_and_args(self):
        f = self.e.write(os.path.join(self.e.work, "echo.kol"),
                         'use std.io\nuse std.os\nlet line = read_line()\nprint("got: " + line)\nexit_program(3)\n')
        r = self.e.kit("jit", f, "--", "a", "b", stdin="typed\n")
        self.assertEqual(r.returncode, 3, r.all)
        self.assertIn("got: typed", r.stdout)

    def test_jit_compile_error_is_reported_not_run(self):
        f = self.e.write(os.path.join(self.e.work, "bad.kol"), "let x = \n")
        r = self.e.kit("jit", f)
        self.assertEqual(r.returncode, 1)
        self.assertEqual(shm_leftovers(), [])

    def test_run_jit_in_project_with_dependency(self):
        reg_lib = os.path.join(self.e.tmp, "lib")
        self.e.write(os.path.join(reg_lib, "kol.toml"),
                     '[package]\nname = "greeter"\nversion = "1.0.0"\ndescription = "d"\nlicense = "MIT"\n')
        self.e.write(os.path.join(reg_lib, "src", "lib.kol"),
                     'fn greeter_hi() -> str\n    return "hi from dep"\nend\n')
        r = self.e.kit("publish", "-q", cwd=reg_lib)
        self.assertEqual(r.returncode, 0, r.all)
        d = self.project("use greeter\nprint(greeter_hi())\n", extra_toml="\n[dependencies]\ngreeter = \"1\"\n")
        r = self.e.kit("run", "--jit", "-q", cwd=d)
        self.assertEqual((r.returncode, r.stdout.strip()), (0, "hi from dep"), r.all)
        self.assertFalse(os.path.exists(os.path.join(d, "target")))     # nothing built on disk
        self.assertEqual(shm_leftovers(), [])

    def test_jit_concurrency_and_atomics_are_really_atomic(self):
        # 8 tasks hammer one atomic counter and shared refcounted strings.  With chibicc's own
        # (non-atomic) stdatomic macros this loses updates or crashes; with the CAS-based shim
        # every run must be exact.
        src = '''
task fn hammer(counter: atomic, shared: str, n: int) -> int
    mut total = 0
    for i in 0..n
        counter.add(1)
        let copy = shared + "x"
        total = total + copy.len()
    end
    return total
end

fn main()
    let counter = atomic(0)
    let shared = "refcounted-shared-string"
    let a, b, c, d, e, f, g, h = await all(
        hammer(counter, shared, 100000), hammer(counter, shared, 100000),
        hammer(counter, shared, 100000), hammer(counter, shared, 100000),
        hammer(counter, shared, 100000), hammer(counter, shared, 100000),
        hammer(counter, shared, 100000), hammer(counter, shared, 100000))
    print(counter.load())
    print(a + b + c + d + e + f + g + h)
end
'''
        f = self.e.write(os.path.join(self.e.work, "stress.kol"), src)
        for _ in range(3):
            r = self.e.kit("jit", "-q", f)
            self.assertEqual((r.returncode, r.stdout.split()), (0, ["800000", "20000000"]), r.all)

    def test_whole_test_corpus_matches_under_chibicc(self):
        """Every tests/*.kol that has a .expected file must print exactly that when
        compiled by chibicc (includes all the concurrency tests)."""
        failures = []
        total = 0
        for path in sorted(glob.glob(os.path.join(ROOT, "tests", "*.kol"))):
            expected = path[:-4] + ".expected"
            if not os.path.exists(expected):
                continue
            total += 1
            r = self.e.kit("jit", "-q", path, cwd=ROOT)
            want = open(expected).read().strip()
            if r.returncode != 0 or r.stdout.strip() != want:
                failures.append(f"{os.path.basename(path)}: rc={r.returncode} {r.all[-200:]}")
        self.assertGreater(total, 50)
        self.assertEqual(failures, [], "\n".join(failures))


if __name__ == "__main__":
    if NEEDS:
        print(f"note: chibicc tests skipped: {NEEDS}")
    unittest.main(verbosity=1, warnings="ignore")
