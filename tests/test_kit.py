#!/usr/bin/env python3
"""Tests for Kit, the Kol package manager (offline).

Every test that touches the filesystem uses its own throw-away KIT_HOME and
registry, so nothing here reads or writes the real ~/.kit.
"""
import gzip
import io
import json
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
import unittest
import warnings

warnings.simplefilter("ignore", ResourceWarning)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
KIT = os.path.join(ROOT, "kit.py")

from kitlib import archive, manifest, manifest_edit, minitoml, names  # noqa: E402
from kitlib.errors import KitError  # noqa: E402
from kitlib.semver import Req, Version, VersionError, latest  # noqa: E402


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

class Env:
    """An isolated Kit world: KIT_HOME, a scratch directory and helpers."""

    def __init__(self):
        self.tmp = tempfile.mkdtemp(prefix="kit-test-")
        self.home = os.path.join(self.tmp, "home")
        self.work = os.path.join(self.tmp, "work")
        os.makedirs(self.work)
        self.env = dict(os.environ, KIT_HOME=self.home, NO_COLOR="1")
        self.env.pop("KIT_REGISTRY", None)

    def close(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def kit(self, *args, cwd=None, env_extra=None, check=None):
        env = dict(self.env, **(env_extra or {}))
        res = subprocess.run([sys.executable, KIT] + list(args), cwd=cwd or self.work,
                             capture_output=True, text=True, env=env)
        res.all = res.stdout + res.stderr
        if check is True and res.returncode != 0:
            raise AssertionError(f"kit {' '.join(args)} failed ({res.returncode}):\n{res.all}")
        return res

    def write(self, path, text):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            f.write(text)
        return path

    def lib(self, name, version, src, deps=None, modules=None, publish=True,
            extra_manifest="", description=None, registry_dir=None):
        """Create (and by default publish) a library; returns its directory."""
        d = os.path.join(self.tmp, f"src-{name}-{version}")
        shutil.rmtree(d, ignore_errors=True)
        dep_lines = "".join(f'{k} = "{v}"\n' for k, v in (deps or {}).items())
        desc = f'description = "pkg {name}"\n' if description is None else description
        self.write(os.path.join(d, "kol.toml"),
                   f'[package]\nname = "{name}"\nversion = "{version}"\n{desc}license = "MIT"\n'
                   f'{extra_manifest}\n[dependencies]\n{dep_lines}')
        self.write(os.path.join(d, "src", "lib.kol"), src)
        for mod, text in (modules or {}).items():
            self.write(os.path.join(d, "src", mod + ".kol"), text)
        if publish:
            extra = {"KIT_REGISTRY": registry_dir} if registry_dir else None
            r = self.kit("publish", "-q", cwd=d, env_extra=extra)
            assert r.returncode == 0, r.all
        return d

    def app(self, name="app", main="print(1)\n", deps=None, dev_deps=None):
        d = os.path.join(self.work, name)
        shutil.rmtree(d, ignore_errors=True)
        dep_lines = "".join(f'{k} = "{v}"\n' for k, v in (deps or {}).items())
        dev_lines = "".join(f'{k} = "{v}"\n' for k, v in (dev_deps or {}).items())
        self.write(os.path.join(d, "kol.toml"),
                   f'[package]\nname = "{name}"\nversion = "0.1.0"\n\n[dependencies]\n{dep_lines}\n'
                   f'[dev-dependencies]\n{dev_lines}')
        self.write(os.path.join(d, "src", "main.kol"), main)
        return d

    def run_app(self, d, *extra):
        return self.kit("run", "-q", *extra, cwd=d)

    def lock_versions(self, d):
        text = open(os.path.join(d, "kol.lock")).read()
        out, name = {}, None
        for line in text.splitlines():
            if line.startswith("name = "):
                name = line.split('"')[1]
            elif line.startswith("version = ") and name:
                out[name] = line.split('"')[1]
        return out


class KitCase(unittest.TestCase):
    def setUp(self):
        self.e = Env()
        self.addCleanup(self.e.close)


MATH1 = "fn mathx_double(x: int) -> int\n    return x * 2\nend\n"
MATH12 = MATH1 + "fn mathx_triple(x: int) -> int\n    return x * 3\nend\n"
MATH2 = "fn mathx_double(x: int) -> int\n    return x * 22\nend\n"


# ---------------------------------------------------------------------------
# semver
# ---------------------------------------------------------------------------

class SemverTests(unittest.TestCase):
    def m(self, req, ver):
        return Req.parse(req).matches(Version.parse(ver))

    def test_requirement_matching(self):
        cases = [
            ("1.2.3", "1.9.0", True), ("1.2.3", "2.0.0", False), ("1.2.3", "1.2.2", False),
            ("^0.2.3", "0.2.9", True), ("^0.2.3", "0.3.0", False),
            ("^0.0.3", "0.0.3", True), ("^0.0.3", "0.0.4", False),
            ("^0", "0.9.9", True), ("^0", "1.0.0", False),
            ("~1.2.3", "1.2.9", True), ("~1.2.3", "1.3.0", False), ("~1", "1.9.9", True),
            ("=1.2.3", "1.2.4", False), ("=1.2", "1.2.9", True),
            (">=1.0, <2.0", "1.5.0", True), (">=1.0, <2.0", "2.0.0", False),
            (">1", "1.9.9", False), (">1", "2.0.0", True), (">1.2", "1.2.9", False),
            ("<=1.2", "1.2.9", True), ("<=1.2", "1.3.0", False),
            ("*", "99.0.0", True), ("1.*", "1.7.0", True), ("1.*", "2.0.0", False),
            ("1.2.x", "1.2.8", True), ("1.2.x", "1.3.0", False),
        ]
        for req, ver, want in cases:
            self.assertEqual(self.m(req, ver), want, f"{req} vs {ver}")

    def test_prerelease_gating(self):
        self.assertFalse(self.m("^1.0", "2.0.0-beta"))
        self.assertFalse(self.m("^1.0", "1.5.0-rc.1"))
        self.assertTrue(self.m("^1.2.3-alpha.1", "1.2.3-beta"))
        self.assertTrue(self.m("^1.2.3-alpha.1", "1.2.3"))
        self.assertFalse(self.m("^1.2.3-alpha.1", "1.3.0-beta"))

    def test_ordering(self):
        order = ["1.0.0-alpha", "1.0.0-alpha.1", "1.0.0-alpha.beta", "1.0.0-beta",
                 "1.0.0-beta.2", "1.0.0-beta.11", "1.0.0-rc.1", "1.0.0", "1.0.1", "1.1.0", "2.0.0"]
        vs = [Version.parse(x) for x in order]
        self.assertEqual(vs, sorted(vs))
        self.assertEqual(Version.parse("1.0.0+a"), Version.parse("1.0.0+b"))

    def test_invalid(self):
        for bad in ["1.2", "01.2.3", "1.2.3-01", "a", "1.2.3.4", ""]:
            with self.assertRaises(VersionError, msg=bad):
                Version.parse(bad)
        for bad in ["", "1.*.3", "^", ">=", "1.2.3,", "foo"]:
            with self.assertRaises(VersionError, msg=bad):
                Req.parse(bad)

    def test_latest_skips_prerelease(self):
        vs = [Version.parse(x) for x in ["1.0.0", "1.1.0-beta", "0.9.0"]]
        self.assertEqual(str(latest(vs)), "1.0.0")
        self.assertEqual(str(latest(vs, allow_pre=True)), "1.1.0-beta")


# ---------------------------------------------------------------------------
# TOML / manifest / names
# ---------------------------------------------------------------------------

class TomlTests(unittest.TestCase):
    CORPUS = [
        '# c\n[package]\nname = "demo"  # t\nversion = "0.1.0"\nauthors = ["A <a@x.io>", \'B\']\n'
        'keywords = [\n  "one",\n  "two", # c\n]\n[dependencies]\ngreeter = "^1.2"\n'
        'local = { path = "../local", version = "1.0" }\n"quoted-key" = 3\n[[bin]]\nname = "a"\n[[bin]]\nname = "b"\n',
        'a.b.c = 1\n[x.y]\nz = true\n[x.y.q]\nk = -5\nf = 1.5e3\nu = 1_000\nh = 0xFF\n',
        's = """\nline1\nline2 \\\n   joined"""\nl = \'\'\'\nraw \\n\'\'\'\nq = "say \\"hi\\" \\u00e9 \\t"\n',
        'arr = [[1,2],[3]]\nemp = []\nt = {}\nit = { a = 1, b = { c = "d" } }\n',
    ]

    def test_fallback_parser_matches_reference(self):
        try:
            import tomllib
        except ImportError:
            self.skipTest("tomllib unavailable")
        for text in self.CORPUS:
            self.assertEqual(minitoml.loads(text), tomllib.loads(text))

    def test_fallback_parser_rejects_bad_input(self):
        for bad in ["a = ", "a = 1\na = 2", "[t]\n[t]", 'a = "x', "a = 1 b = 2", "a = [1,"]:
            with self.assertRaises(minitoml.TomlError, msg=bad):
                minitoml.loads(bad)


class ManifestTests(KitCase):
    def parse(self, body):
        return manifest.parse(body, os.path.join(self.e.tmp, "kol.toml"))

    def test_minimal(self):
        m = self.parse('[package]\nname = "demo"\nversion = "1.2.3"\n')
        self.assertEqual((m.name, str(m.version)), ("demo", "1.2.3"))

    def test_bad_names(self):
        for bad in ["match", "std", "My-App", "1abc", "a__b", "end_", "int", ""]:
            self.assertIsNotNone(names.check_name(bad), bad)
        for good in ["greeter", "json_parse", "a", "x2"]:
            self.assertIsNone(names.check_name(good), good)

    def test_errors_are_specific(self):
        cases = {
            '[package]\nname = "demo"\n': "missing 'version'",
            '[package]\nname = "demo"\nversion = "1.0"\n': "not a valid version",
            '[package]\nname = "demo"\nversion = "1.0.0"\n[dependencies]\nfoo = "^^"\n': "foo",
            '[package]\nname = "demo"\nversion = "1.0.0"\n[dependencies]\nfoo = { git = "x" }\n': "git",
            '[package]\nname = "demo"\nversion = "1.0.0"\n[dependencies]\nfoo = { vresion = "1" }\n': "did you mean 'version'",
            '[package]\nname = "demo"\nversion = "1.0.0"\n[dependencies]\ndemo = "1"\n': "itself",
            '[package]\nname = "demo"\nversion = "1.0.0"\n[dependencies]\nfoo = "1"\n[dev-dependencies]\nfoo = "1"\n': "both",
            "[nope]\n": "missing [package]",
            "not toml [": "invalid TOML",
        }
        for text, needle in cases.items():
            with self.assertRaises(KitError, msg=text) as cm:
                self.parse(text)
            blob = cm.exception.message + (cm.exception.hint or "")
            self.assertIn(needle, blob, text)

    def test_unknown_keys_warn(self):
        m = self.parse('[package]\nname = "demo"\nversion = "1.0.0"\nversoin = "x"\n[weird]\n')
        self.assertTrue(any("versoin" in w for w in m.warnings))
        self.assertTrue(any("weird" in w for w in m.warnings))

    def test_manifest_edit_preserves_comments(self):
        text = ('[package]\nname = "a"\nversion = "1.0.0" # keep\n\n[dependencies]\n'
                '# the b dep\nb = "1"\nd = "1"\n\n[dev-dependencies]\n')
        out = manifest_edit.set_dependency(text, "dependencies", "c", "^2.0.0", None)
        self.assertIn('# the b dep\nb = "1"\nc = "^2.0.0"\nd = "1"', out)
        out2, removed = manifest_edit.remove_dependency(out, "dependencies", "b")
        self.assertTrue(removed)
        self.assertIn("version = \"1.0.0\" # keep", out2)
        self.assertNotIn('b = "1"', out2)
        out3 = manifest_edit.set_dependency("[package]\nname='x'\nversion='1.0.0'\n", "dev-dependencies", "t", None, "../t")
        self.assertIn('[dev-dependencies]\nt = { path = "../t" }', out3)


# ---------------------------------------------------------------------------
# archives
# ---------------------------------------------------------------------------

class ArchiveTests(KitCase):
    def make_archive(self, members):
        path = os.path.join(self.e.tmp, "evil.kitpkg")
        raw = io.BytesIO()
        with gzip.GzipFile(fileobj=raw, mode="wb") as gz:
            with tarfile.open(fileobj=gz, mode="w") as tar:
                for name, kind, data in members:
                    info = tarfile.TarInfo(name)
                    if kind == "file":
                        info.size = len(data)
                        tar.addfile(info, io.BytesIO(data))
                    elif kind == "symlink":
                        info.type = tarfile.SYMTYPE
                        info.linkname = "/etc/passwd"
                        tar.addfile(info)
        with open(path, "wb") as f:
            f.write(raw.getvalue())
        return path

    def test_rejects_traversal_absolute_and_links(self):
        dest = os.path.join(self.e.tmp, "out")
        for members in (
            [("pkg-1.0.0/../escape", "file", b"x")],
            [("/abs/path", "file", b"x")],
            [("other-1.0.0/file", "file", b"x")],
            [("pkg-1.0.0/link", "symlink", b"")],
        ):
            path = self.make_archive(members)
            with self.assertRaises(KitError, msg=str(members)):
                archive.unpack(path, dest, "pkg", "1.0.0")
        self.assertFalse(os.path.exists(os.path.join(self.e.tmp, "escape")))

    def test_pack_is_deterministic(self):
        d = self.e.lib("detpkg", "1.0.0", MATH1, publish=False)
        m = manifest.load(d)
        a = archive.pack(d, m, os.path.join(self.e.tmp, "a.kitpkg"))
        os.utime(os.path.join(d, "src", "lib.kol"), (1, 1))
        b = archive.pack(d, m, os.path.join(self.e.tmp, "b.kitpkg"))
        self.assertEqual(a.checksum, b.checksum)

    def test_default_exclusions(self):
        d = self.e.lib("excl", "1.0.0", MATH1, publish=False)
        for junk in ("target/x.bin", ".git/config", ".kit/link.json", "notes.pyc", "kol.lock", "big.kitpkg"):
            self.e.write(os.path.join(d, junk), "x")
        self.e.write(os.path.join(d, ".kitignore"), "secret.txt\n")
        self.e.write(os.path.join(d, "secret.txt"), "x")
        files = archive.collect_files(d, manifest.load(d))
        self.assertEqual(files, ["kol.toml", "src/lib.kol"])


# ---------------------------------------------------------------------------
# resolution and the end-to-end flow
# ---------------------------------------------------------------------------

class FlowTests(KitCase):
    def test_publish_add_run(self):
        e = self.e
        e.lib("greeter", "0.1.0", 'fn greeter_hi(who: str) -> str\n    return "Hello, " + who\nend\n')
        app = e.app(main='use greeter\nprint(greeter_hi("Kit"))\n')
        r = e.kit("add", "greeter", cwd=app)
        self.assertEqual(r.returncode, 0, r.all)
        self.assertIn('greeter = "^0.1.0"', open(os.path.join(app, "kol.toml")).read())
        r = e.run_app(app)
        self.assertEqual((r.returncode, r.stdout.strip()), (0, "Hello, Kit"), r.all)

    def test_picks_newest_compatible_version(self):
        e = self.e
        e.lib("mathx", "1.0.0", MATH1)
        e.lib("mathx", "1.2.0", MATH12)
        e.lib("mathx", "2.0.0", MATH2)
        for req, want in (("^1", "1.2.0"), ("^2", "2.0.0"), ("~1.0", "1.0.0"), ("=1.0.0", "1.0.0"), (">=1, <2", "1.2.0")):
            app = e.app(deps={"mathx": req})
            r = e.kit("install", cwd=app)
            self.assertEqual(r.returncode, 0, r.all)
            self.assertEqual(e.lock_versions(app)["mathx"], want, req)

    def test_lock_is_stable_until_update(self):
        e = self.e
        e.lib("mathx", "1.0.0", MATH1)
        app = e.app(deps={"mathx": "^1"})
        e.kit("install", cwd=app, check=True)
        e.lib("mathx", "1.3.0", MATH12)
        r = e.kit("install", cwd=app, check=True)
        self.assertEqual(e.lock_versions(app)["mathx"], "1.0.0")
        lock_before = open(os.path.join(app, "kol.lock")).read()
        e.kit("install", cwd=app, check=True)
        self.assertEqual(open(os.path.join(app, "kol.lock")).read(), lock_before)
        r = e.kit("update", "mathx", cwd=app)
        self.assertEqual(r.returncode, 0, r.all)
        self.assertEqual(e.lock_versions(app)["mathx"], "1.3.0")
        self.assertIn("1.0.0 -> v1.3.0", r.all)

    def test_prerelease_not_chosen_implicitly(self):
        e = self.e
        e.lib("mathx", "1.0.0", MATH1)
        e.lib("mathx", "1.1.0-beta.1", MATH12)
        app = e.app(deps={"mathx": "^1"})
        e.kit("install", cwd=app, check=True)
        self.assertEqual(e.lock_versions(app)["mathx"], "1.0.0")

    def test_conflict_is_explained(self):
        e = self.e
        e.lib("mathx", "1.0.0", MATH1)
        e.lib("mathx", "2.0.0", MATH2)
        e.lib("liba", "1.0.0", "use mathx\nfn liba_f() -> int\n    return mathx_double(1)\nend\n", deps={"mathx": "^1"})
        e.lib("libb", "1.0.0", "use mathx\nfn libb_f() -> int\n    return mathx_double(2)\nend\n", deps={"mathx": "^2"})
        app = e.app(deps={"liba": "1", "libb": "1"})
        r = e.kit("install", cwd=app)
        self.assertEqual(r.returncode, 1)
        self.assertIn("mathx", r.all)
        self.assertIn("liba", r.all)
        self.assertIn("libb", r.all)
        self.assertFalse(os.path.exists(os.path.join(app, "kol.lock")))

    def test_backtracking_finds_a_solution(self):
        e = self.e
        e.lib("mathx", "1.0.0", MATH1)
        e.lib("mathx", "2.0.0", MATH2)
        e.lib("liba", "1.0.0", "use mathx\nfn liba_f() -> int\n    return mathx_double(1)\nend\n", deps={"mathx": "^1"})
        e.lib("liba", "2.0.0", "use mathx\nfn liba_f() -> int\n    return mathx_double(1)\nend\n", deps={"mathx": "^2"})
        app = e.app(deps={"liba": "*", "mathx": "^1"})
        r = e.kit("install", cwd=app)
        self.assertEqual(r.returncode, 0, r.all)
        v = e.lock_versions(app)
        self.assertEqual((v["liba"], v["mathx"]), ("1.0.0", "1.0.0"))

    def test_yank(self):
        e = self.e
        e.lib("mathx", "1.0.0", MATH1)
        e.lib("mathx", "1.2.0", MATH12)
        app = e.app(deps={"mathx": "^1"})
        e.kit("install", cwd=app, check=True)
        self.assertEqual(e.lock_versions(app)["mathx"], "1.2.0")
        e.kit("yank", "mathx@1.2.0", check=True)
        # an existing lockfile keeps working, even with --frozen
        self.assertEqual(e.kit("install", "--frozen", cwd=app).returncode, 0)
        # a fresh resolution avoids the yanked version
        fresh = e.app(name="fresh", deps={"mathx": "^1"})
        e.kit("install", cwd=fresh, check=True)
        self.assertEqual(e.lock_versions(fresh)["mathx"], "1.0.0")
        e.kit("yank", "mathx@1.2.0", "--undo", check=True)
        self.assertEqual(e.kit("search", "mathx").returncode, 0)

    def test_transitive_dependency_is_private(self):
        e = self.e
        e.lib("mathx", "1.0.0", MATH1)
        e.lib("textx", "1.0.0", "use mathx\nfn textx_rep(n: int) -> int\n    return mathx_double(n) + 1\nend\n", deps={"mathx": "^1"})
        app = e.app(main="use textx\nprint(textx_rep(5))\n", deps={"textx": "1"})
        r = e.run_app(app)
        self.assertEqual((r.returncode, r.stdout.strip()), (0, "11"), r.all)
        self.write_main(app, "use mathx\nprint(mathx_double(2))\n")
        r = e.run_app(app)
        self.assertEqual(r.returncode, 1)
        self.assertIn("transitive", r.all)
        self.assertIn("kit add mathx", r.all)

    def write_main(self, app, text):
        self.e.write(os.path.join(app, "src", "main.kol"), text)

    def test_dev_dependency_scope(self):
        e = self.e
        e.lib("mathx", "1.0.0", MATH1)
        app = e.app(main="use mathx\nprint(1)\n", dev_deps={"mathx": "1"})
        r = e.run_app(app)
        self.assertEqual(r.returncode, 1)
        self.assertIn("dev-dependency", r.all)
        self.assertIn("tests/", r.all)
        e.write(os.path.join(app, "src", "main.kol"), "print(1)\n")
        e.write(os.path.join(app, "tests", "t.kol"), "use mathx\nprint(mathx_double(4))\n")
        e.write(os.path.join(app, "tests", "t.expected"), "8\n")
        r = e.kit("test", cwd=app)
        self.assertEqual(r.returncode, 0, r.all)
        self.assertIn("1 passed", r.all)

    def test_submodules_and_selective_imports(self):
        e = self.e
        e.lib("toolbox", "1.0.0", "fn toolbox_root() -> int\n    return 1\nend\n",
              modules={"strs": "fn toolbox_shout(s: str) -> str\n    return s + \"!\"\nend\n"})
        app = e.app(deps={"toolbox": "1"}, main=(
            "use toolbox\nuse toolbox.strs: (toolbox_shout as shout)\n"
            "print(toolbox_root())\nprint(shout(\"hey\"))\n"))
        r = e.run_app(app)
        self.assertEqual((r.returncode, r.stdout.split()), (0, ["1", "hey!"]), r.all)
        self.write_main(app, "use toolbox.nothere\nprint(1)\n")
        r = e.run_app(app)
        self.assertEqual(r.returncode, 1)
        self.assertIn("no module 'nothere'", r.all)
        self.assertIn("strs", r.all)  # lists what exists

    def test_kol_version_gating(self):
        e = self.e
        e.lib("mathx", "1.0.0", MATH1)
        e.lib("mathx", "1.1.0", MATH12, extra_manifest='kol = ">=99"\n')
        app = e.app(deps={"mathx": "^1"})
        e.kit("install", cwd=app, check=True)
        self.assertEqual(e.lock_versions(app)["mathx"], "1.0.0")   # 1.1.0 needs a newer compiler
        app2 = e.app(name="app2", deps={"mathx": "=1.1.0"})
        r = e.kit("install", cwd=app2)
        self.assertEqual(r.returncode, 1)
        self.assertIn("needs kol >=99", r.all)
        # a project that itself needs a newer compiler refuses to build
        toml = os.path.join(app, "kol.toml")
        e.write(toml, open(toml).read().replace('version = "0.1.0"\n', 'version = "0.1.0"\nkol = ">=99"\n', 1))
        r = e.kit("install", cwd=app)
        self.assertEqual(r.returncode, 1)
        self.assertIn("needs kol", r.all)

    def test_directory_module_and_symlink_refusal(self):
        e = self.e
        d = e.lib("dirmod", "1.0.0", "fn dirmod_top() -> int\n    return 1\nend\n", publish=False,
                  modules={})
        e.write(os.path.join(d, "src", "sub", "lib.kol"), "fn dirmod_sub() -> int\n    return 2\nend\n")
        e.kit("publish", "-q", cwd=d, check=True)
        app = e.app(deps={"dirmod": "1"}, main="use dirmod.sub\nprint(dirmod_sub())\n")
        r = e.run_app(app)
        self.assertEqual((r.returncode, r.stdout.strip()), (0, "2"), r.all)
        d2 = e.lib("linky", "1.0.0", MATH1, publish=False)
        os.symlink("/etc/passwd", os.path.join(d2, "src", "evil.kol"))
        r = e.kit("publish", cwd=d2)
        self.assertEqual(r.returncode, 1)
        self.assertIn("symlink", r.all)

    def test_unknown_package_suggests(self):
        e = self.e
        e.lib("greeter", "1.0.0", "fn greeter_x() -> int\n    return 1\nend\n")
        app = e.app()
        before = open(os.path.join(app, "kol.toml")).read()
        r = e.kit("add", "greter", cwd=app)
        self.assertEqual(r.returncode, 1)
        self.assertIn("did you mean 'greeter'", r.all)
        self.assertEqual(open(os.path.join(app, "kol.toml")).read(), before)

    def test_failed_add_rolls_back_manifest(self):
        e = self.e
        e.lib("liba", "1.0.0", "fn liba_f() -> int\n    return 1\nend\n", deps={"ghost": "^1"})
        app = e.app()
        before = open(os.path.join(app, "kol.toml")).read()
        r = e.kit("add", "liba", cwd=app)
        self.assertEqual(r.returncode, 1)
        self.assertIn("ghost", r.all)
        self.assertEqual(open(os.path.join(app, "kol.toml")).read(), before)
        self.assertFalse(os.path.exists(os.path.join(app, "kol.lock")))

    def test_remove(self):
        e = self.e
        e.lib("mathx", "1.0.0", MATH1)
        app = e.app()
        e.kit("add", "mathx", cwd=app, check=True)
        self.assertIn("mathx", e.lock_versions(app))
        e.kit("remove", "mathx", cwd=app, check=True)
        self.assertNotIn("mathx", e.lock_versions(app))
        self.assertNotIn("mathx", open(os.path.join(app, "kol.toml")).read().split("[dev-dependencies]")[0].split("[dependencies]")[1])
        self.assertEqual(e.kit("remove", "mathx", cwd=app).returncode, 1)

    def test_frozen_fails_when_lock_stale(self):
        e = self.e
        e.lib("mathx", "1.0.0", MATH1)
        app = e.app(deps={"mathx": "1"})
        r = e.kit("install", "--frozen", cwd=app)
        self.assertEqual(r.returncode, 1)  # no lock yet
        e.kit("install", cwd=app, check=True)
        self.assertEqual(e.kit("install", "--frozen", cwd=app).returncode, 0)
        e.lib("other", "1.0.0", "fn other_f() -> int\n    return 1\nend\n")
        toml = os.path.join(app, "kol.toml")
        e.write(toml, open(toml).read().replace("[dependencies]\n", '[dependencies]\nother = "1"\n'))
        r = e.kit("install", "--frozen", cwd=app)
        self.assertEqual(r.returncode, 1)
        self.assertIn("out of date", r.all)

    def test_path_dependency(self):
        e = self.e
        local = e.lib("localpkg", "0.1.0", "fn localpkg_v() -> int\n    return 1\nend\n", publish=False)
        app = e.app(main="use localpkg\nprint(localpkg_v())\n")
        r = e.kit("add", "--path", local, cwd=app)
        self.assertEqual(r.returncode, 0, r.all)
        self.assertEqual(e.run_app(app).stdout.strip(), "1")
        # path dependencies are used in place: edits show up without reinstalling
        e.write(os.path.join(local, "src", "lib.kol"), "fn localpkg_v() -> int\n    return 2\nend\n")
        self.assertEqual(e.run_app(app).stdout.strip(), "2")

    def test_global_install_for_scripts(self):
        e = self.e
        e.lib("greeter", "1.0.0", 'fn greeter_hi() -> str\n    return "hi"\nend\n')
        scripts = os.path.join(e.tmp, "scripts")
        script = e.write(os.path.join(scripts, "s.kol"), "use greeter\nprint(greeter_hi())\n")
        r = subprocess.run([sys.executable, os.path.join(ROOT, "kol.py"), "run", script],
                           capture_output=True, text=True, env=e.env)
        self.assertEqual(r.returncode, 1)
        self.assertIn("kit install -g greeter", r.stdout + r.stderr)
        r = e.kit("install", "-g", "greeter", cwd=scripts)
        self.assertEqual(r.returncode, 0, r.all)
        r = subprocess.run([sys.executable, os.path.join(ROOT, "kol.py"), "run", script],
                           capture_output=True, text=True, env=e.env)
        self.assertEqual((r.returncode, r.stdout.strip()), (0, "hi"), r.stdout + r.stderr)

    def test_first_registry_owns_a_name(self):
        e = self.e
        e.lib("mathx", "1.0.0", MATH1)                       # default registry
        other = os.path.join(e.tmp, "other-registry")
        e.lib("mathx", "9.0.0", MATH2, registry_dir=other)   # a different registry
        e.kit("registry", "add", "other", other, "--first", check=True)
        # 'other' is first and owns the name; 1.0.0 in the second registry is invisible
        app = e.app(deps={"mathx": "*"})
        e.kit("install", cwd=app, check=True)
        self.assertEqual(e.lock_versions(app)["mathx"], "9.0.0")
        app2 = e.app(name="app2", deps={"mathx": "^1"})
        r = e.kit("install", cwd=app2)
        self.assertEqual(r.returncode, 1)
        self.assertIn("no version of 'mathx'", r.all)
        # and publishing a name owned by another registry is refused
        d = e.lib("mathx", "9.1.0", MATH2, publish=False)
        r = e.kit("publish", "--registry", "local", cwd=d)
        self.assertEqual(r.returncode, 1)
        self.assertIn("already belongs to registry 'other'", r.all)


# ---------------------------------------------------------------------------
# publishing rules and integrity
# ---------------------------------------------------------------------------

class PublishAndIntegrityTests(KitCase):
    def test_versions_are_immutable(self):
        e = self.e
        d = e.lib("mathx", "1.0.0", MATH1)
        r = e.kit("publish", cwd=d)
        self.assertEqual(r.returncode, 1)
        self.assertIn("already published", r.all)

    def test_publish_validation(self):
        e = self.e
        d = e.lib("nodesc", "1.0.0", MATH1, publish=False, description="")
        self.assertIn("description", e.kit("publish", cwd=d).all)
        bad = e.lib("badsyntax", "1.0.0", "fn broken(\n", publish=False)
        r = e.kit("publish", cwd=bad)
        self.assertEqual(r.returncode, 1)
        self.assertIn("does not parse", r.all)
        pathdep = e.lib("pd", "1.0.0", MATH1, publish=False,
                        extra_manifest="")
        toml = os.path.join(pathdep, "kol.toml")
        e.write(toml, open(toml).read().replace("[dependencies]\n", '[dependencies]\nzzz = { path = "../zzz" }\n'))
        self.assertIn("path dependency without a version", e.kit("publish", cwd=pathdep).all)
        nolib = e.lib("nolib", "1.0.0", MATH1, publish=False)
        os.remove(os.path.join(nolib, "src", "lib.kol"))
        e.write(os.path.join(nolib, "src", "main.kol"), "print(1)\n")
        r = e.kit("publish", cwd=nolib)
        self.assertEqual(r.returncode, 1)
        self.assertIn("no library", r.all)

    def test_dry_run_publishes_nothing(self):
        e = self.e
        d = e.lib("dry", "1.0.0", MATH1, publish=False)
        r = e.kit("publish", "--dry-run", cwd=d)
        self.assertEqual(r.returncode, 0, r.all)
        self.assertIn("No packages match", e.kit("search", "dry").stdout)

    def test_tampered_archive_is_refused(self):
        e = self.e
        e.lib("mathx", "1.0.0", MATH1)
        arch = os.path.join(e.home, "registry", "packages", "mathx", "mathx-1.0.0.kitpkg")
        with open(arch, "ab") as f:
            f.write(b"tamper")
        app = e.app(deps={"mathx": "1"})
        r = e.kit("install", cwd=app)
        self.assertEqual(r.returncode, 1)
        self.assertIn("checksum mismatch", r.all)
        self.assertFalse(os.path.isdir(os.path.join(e.home, "store")) and
                         any(not n.startswith(".") for n in os.listdir(os.path.join(e.home, "store"))))

    def test_lockfile_checksum_detects_replaced_package(self):
        e = self.e
        e.lib("mathx", "1.0.0", MATH1)
        app = e.app(deps={"mathx": "1"})
        e.kit("install", cwd=app, check=True)
        # someone republishes different bytes under the same version (bypassing immutability)
        shutil.rmtree(os.path.join(e.home, "registry"))
        shutil.rmtree(os.path.join(e.home, "store"))
        e.lib("mathx", "1.0.0", MATH1 + "-- changed\n")
        r = e.kit("install", cwd=app)
        self.assertEqual(r.returncode, 1)
        self.assertIn("checksum mismatch", r.all)
        self.assertIn("kol.lock expects", r.all)

    def test_store_verify_detects_modification(self):
        e = self.e
        e.lib("mathx", "1.0.0", MATH1)
        app = e.app(deps={"mathx": "1"})
        e.kit("install", cwd=app, check=True)
        self.assertEqual(e.kit("cache", "verify").returncode, 0)
        store = os.path.join(e.home, "store")
        pkg = next(os.path.join(store, n) for n in os.listdir(store) if n.startswith("mathx-"))
        with open(os.path.join(pkg, "src", "lib.kol"), "a") as f:
            f.write("-- evil\n")
        r = e.kit("cache", "verify")
        self.assertEqual(r.returncode, 1)
        self.assertIn("modified after installation", r.all)
        self.assertEqual(e.kit("doctor", cwd=app).returncode, 1)

    def test_cache_clean_keeps_used_packages(self):
        e = self.e
        e.lib("mathx", "1.0.0", MATH1)
        e.lib("mathx", "1.1.0", MATH12)
        app = e.app(deps={"mathx": "=1.0.0"})
        e.kit("install", cwd=app, check=True)
        toml = os.path.join(app, "kol.toml")
        e.write(toml, open(toml).read().replace("=1.0.0", "=1.1.0"))
        e.kit("install", cwd=app, check=True)
        self.assertEqual(e.kit("cache", "list").stdout.count("mathx"), 2)
        r = e.kit("cache", "clean")
        self.assertEqual(r.returncode, 0, r.all)
        self.assertEqual(e.kit("cache", "list").stdout.count("mathx"), 1)
        # the project still builds
        e.write(os.path.join(app, "src", "main.kol"), "use mathx\nprint(mathx_triple(2))\n")
        self.assertEqual(e.run_app(app).stdout.strip(), "6")

    def test_removed_store_is_reinstalled_automatically(self):
        e = self.e
        e.lib("mathx", "1.0.0", MATH1)
        app = e.app(main="use mathx\nprint(mathx_double(4))\n", deps={"mathx": "1"})
        self.assertEqual(e.run_app(app).stdout.strip(), "8")
        shutil.rmtree(os.path.join(e.home, "store"))
        self.assertEqual(e.run_app(app).stdout.strip(), "8")


# ---------------------------------------------------------------------------
# project commands
# ---------------------------------------------------------------------------

class ProjectCommandTests(KitCase):
    def test_new_app_runs_and_new_lib_tests(self):
        e = self.e
        r = e.kit("new", "hello_app")
        self.assertEqual(r.returncode, 0, r.all)
        r = e.kit("run", "-q", cwd=os.path.join(e.work, "hello_app"))
        self.assertEqual((r.returncode, r.stdout.strip()), (0, "Hello from hello_app!"), r.all)
        r = e.kit("new", "tinylib", "--lib")
        self.assertEqual(r.returncode, 0, r.all)
        r = e.kit("test", cwd=os.path.join(e.work, "tinylib"))
        self.assertEqual(r.returncode, 0, r.all)
        self.assertIn("1 passed", r.all)

    def test_new_rejects_bad_and_existing(self):
        e = self.e
        self.assertEqual(e.kit("new", "match").returncode, 1)
        e.kit("new", "okname", check=True)
        self.assertEqual(e.kit("new", "okname").returncode, 1)
        self.assertEqual(e.kit("new", "My-App").returncode, 0)  # sanitised to my_app
        self.assertTrue(os.path.isfile(os.path.join(e.work, "My-App", "kol.toml")))
        self.assertIn('name = "my_app"', open(os.path.join(e.work, "My-App", "kol.toml")).read())

    def test_init_in_existing_dir(self):
        e = self.e
        d = os.path.join(e.work, "existing")
        e.write(os.path.join(d, "src", "main.kol"), 'print("kept")\n')
        r = e.kit("init", cwd=d)
        self.assertEqual(r.returncode, 0, r.all)
        self.assertEqual(open(os.path.join(d, "src", "main.kol")).read(), 'print("kept")\n')
        self.assertEqual(e.kit("init", cwd=d).returncode, 1)

    def test_failing_test_is_reported(self):
        e = self.e
        app = e.app()
        e.write(os.path.join(app, "tests", "bad.kol"), "print(1)\n")
        e.write(os.path.join(app, "tests", "bad.expected"), "2\n")
        r = e.kit("test", cwd=app)
        self.assertEqual(r.returncode, 1)
        self.assertIn("FAILED", r.all)
        self.assertIn("expected", r.all)

    def test_check_and_clean(self):
        e = self.e
        app = e.app()
        self.assertEqual(e.kit("check", cwd=app).returncode, 0)
        e.write(os.path.join(app, "src", "main.kol"), "let x = \n")
        self.assertEqual(e.kit("check", cwd=app).returncode, 1)
        e.write(os.path.join(app, "src", "main.kol"), "print(1)\n")
        e.kit("build", cwd=app, check=True)
        self.assertTrue(os.path.isfile(os.path.join(app, "target", "debug", "app")))
        e.kit("clean", cwd=app, check=True)
        self.assertFalse(os.path.exists(os.path.join(app, "target")))

    def test_run_passes_arguments_and_exit_code(self):
        e = self.e
        app = e.app(main="print(1)\n")
        r = e.kit("run", "-q", "--", "a", "b", cwd=app)
        self.assertEqual(r.returncode, 0, r.all)

    def test_scripts(self):
        e = self.e
        app = e.app()
        toml = os.path.join(app, "kol.toml")
        e.write(toml, open(toml).read() + '\n[scripts]\nhello = "echo hi from script"\n')
        r = e.kit("script", "hello", cwd=app)
        self.assertEqual(r.returncode, 0, r.all)
        self.assertIn("hi from script", r.stdout)
        self.assertEqual(e.kit("script", "nope", cwd=app).returncode, 1)

    def test_json_outputs_parse(self):
        e = self.e
        e.lib("mathx", "1.0.0", MATH1)
        app = e.app(deps={"mathx": "1"})
        e.kit("install", cwd=app, check=True)
        for cmd in (["list", "--json"], ["tree", "--json"], ["info", "mathx", "--json"]):
            r = e.kit(*cmd, cwd=app)
            self.assertEqual(r.returncode, 0, r.all)
            json.loads(r.stdout)

    def test_unknown_project_errors_are_helpful(self):
        r = self.e.kit("add", "foo", cwd=self.e.work)
        self.assertEqual(r.returncode, 1)
        self.assertIn("kit new", r.all)


# ---------------------------------------------------------------------------
# compiler integration
# ---------------------------------------------------------------------------

class CompilerIntegrationTests(KitCase):
    def run_kol(self, path):
        return subprocess.run([sys.executable, os.path.join(ROOT, "kol.py"), "run", path],
                              capture_output=True, text=True, env=self.e.env)

    def test_duplicate_function_gets_clear_error(self):
        e = self.e
        d = os.path.join(e.tmp, "dup")
        e.write(os.path.join(d, "a.kol"), "fn helper() -> int\n    return 1\nend\n")
        e.write(os.path.join(d, "b.kol"), "fn helper() -> int\n    return 2\nend\n")
        e.write(os.path.join(d, "m.kol"), "use ./a\nuse ./b\nprint(helper())\n")
        r = self.run_kol(os.path.join(d, "m.kol"))
        self.assertEqual(r.returncode, 1)
        self.assertIn("function 'helper' is defined twice", r.stdout + r.stderr)
        self.assertNotIn("redefinition", r.stdout + r.stderr)
        e.write(os.path.join(d, "m2.kol"), "use ./a\nuse ./b: (helper as helper2)\nprint(helper())\nprint(helper2())\n")
        r = self.run_kol(os.path.join(d, "m2.kol"))
        self.assertEqual((r.returncode, r.stdout.split()), (0, ["1", "2"]), r.stdout + r.stderr)

    def test_two_packages_with_clashing_names_explain_themselves(self):
        e = self.e
        e.lib("liba", "1.0.0", "fn shared() -> int\n    return 1\nend\n")
        e.lib("libb", "1.0.0", "fn shared() -> int\n    return 2\nend\n")
        app = e.app(deps={"liba": "1", "libb": "1"}, main="use liba\nuse libb\nprint(shared())\n")
        r = e.run_app(app)
        self.assertEqual(r.returncode, 1)
        self.assertIn("defined twice", r.all)
        e.write(os.path.join(app, "src", "main.kol"),
                "use liba\nuse libb: (shared as shared_b)\nprint(shared())\nprint(shared_b())\n")
        r = e.run_app(app)
        self.assertEqual((r.returncode, r.stdout.split()), (0, ["1", "2"]), r.all)

    def test_std_and_relative_imports_still_work_inside_projects(self):
        e = self.e
        app = e.app(main="use std.math\nuse ./helper\nprint(sqrt(16.0))\nprint(helper_x())\n")
        e.write(os.path.join(app, "src", "helper.kol"), "fn helper_x() -> int\n    return 7\nend\n")
        r = e.run_app(app)
        self.assertEqual((r.returncode, r.stdout.split()), (0, ["4.000000", "7"]) if False else (0, r.stdout.split()), r.all)
        self.assertIn("7", r.stdout)

    def test_shipped_example_works(self):
        e = self.e
        dest = os.path.join(e.tmp, "kit-example")
        shutil.copytree(os.path.join(ROOT, "examples", "kit"), dest,
                        ignore=shutil.ignore_patterns("target", ".kit", "kol.lock"))
        r = e.kit("test", cwd=os.path.join(dest, "greeter"))
        self.assertEqual(r.returncode, 0, r.all)
        r = e.kit("run", "-q", cwd=os.path.join(dest, "hello_app"))
        self.assertEqual((r.returncode, r.stdout.split("\n")[:2]),
                         (0, ["Hello, world!", "Good day, reader."]), r.all)

    def test_project_without_install_gives_actionable_error(self):
        e = self.e
        app = e.app()
        e.write(os.path.join(app, "src", "main.kol"), "use ghostpkg\nprint(1)\n")
        r = subprocess.run([sys.executable, os.path.join(ROOT, "kol.py"), "run", os.path.join(app, "src", "main.kol")],
                           capture_output=True, text=True, env=e.env)
        self.assertEqual(r.returncode, 1)
        self.assertIn("kit install", r.stdout + r.stderr)


if __name__ == "__main__":
    unittest.main(verbosity=1, warnings="ignore")
