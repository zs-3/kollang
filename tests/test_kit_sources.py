#!/usr/bin/env python3
"""Tests for Kit dependencies that come from a .kitpkg file or a URL."""
import functools
import glob
import hashlib
import http.server
import os
import sys
import threading
import unittest
import warnings

warnings.simplefilter("ignore", ResourceWarning)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_kit import KitCase, MATH1  # noqa: E402

GREET = 'fn greeter_hi(who: str) -> str\n    return "Hello, " + who\nend\n'
NOPROXY = {"NO_PROXY": "127.0.0.1,localhost", "no_proxy": "127.0.0.1,localhost",
           "HTTP_PROXY": "", "http_proxy": "", "HTTPS_PROXY": "", "https_proxy": ""}


class _Quiet(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *a):
        pass


class SourceCase(KitCase):
    def pack(self, name, version, src, **kw):
        d = self.e.lib(name, version, src, publish=False, **kw)
        r = self.e.kit("pack", "-q", cwd=d)
        self.assertEqual(r.returncode, 0, r.all)
        found = glob.glob(os.path.join(d, "**", f"{name}-{version}.kitpkg"), recursive=True)
        self.assertTrue(found, r.all)
        return found[0]

    def serve(self, directory):
        handler = functools.partial(_Quiet, directory=directory)
        srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        self.addCleanup(srv.shutdown)
        self.addCleanup(srv.server_close)
        return f"http://127.0.0.1:{srv.server_address[1]}"

    def kit(self, *a, **kw):
        kw["env_extra"] = dict(NOPROXY, **(kw.get("env_extra") or {}))
        return self.e.kit(*a, **kw)

    def toml(self, app):
        return open(os.path.join(app, "kol.toml")).read()


class ArchiveFileTests(SourceCase):
    def test_add_kitpkg_file_and_run(self):
        pk = self.pack("greeter", "0.1.0", GREET)
        app = self.e.app(main='use greeter\nprint(greeter_hi("Kit"))\n')
        r = self.kit("add", pk, cwd=app)
        self.assertEqual(r.returncode, 0, r.all)
        t = self.toml(app)
        self.assertIn("archive = ", t)
        self.assertIn('checksum = "sha256:', t)
        r = self.e.run_app(app)
        self.assertEqual((r.returncode, r.stdout.strip()), (0, "Hello, Kit"), r.all)
        lock = open(os.path.join(app, "kol.lock")).read()
        self.assertIn('source = "archive+', lock)

    def test_install_without_project_is_global(self):
        pk = self.pack("greeter", "0.1.0", GREET)
        r = self.kit("install", pk, cwd=self.e.work)
        self.assertEqual(r.returncode, 0, r.all)
        self.assertIn("greeter", r.all)

    def test_tampered_archive_is_refused(self):
        pk = self.pack("greeter", "0.1.0", GREET)
        app = self.e.app(main='use greeter\nprint(greeter_hi("x"))\n')
        self.kit("add", pk, cwd=app, check=True)
        other = self.pack("greeter", "0.1.0", GREET + "\n-- changed\n")
        os.replace(other, pk)
        r = self.kit("install", cwd=app)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("checksum mismatch", r.all)

    def test_missing_and_garbage_archives(self):
        app = self.e.app()
        r = self.kit("add", os.path.join(self.e.work, "nope.kitpkg"), cwd=app)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("does not exist", r.all)
        bad = self.e.write(os.path.join(self.e.work, "bad.kitpkg"), "not an archive")
        r = self.kit("add", bad, cwd=app)
        self.assertNotEqual(r.returncode, 0)
        self.assertNotIn("Traceback", r.all)
        self.assertNotIn("archive", self.toml(app).split("[dev-dependencies]")[0].split("[dependencies]")[1])

    def test_publish_rejects_archive_dependency(self):
        pk = self.pack("greeter", "0.1.0", GREET)
        d = self.e.lib("outer", "0.1.0", MATH1, publish=False)
        self.kit("add", pk, cwd=d, check=True)
        r = self.kit("publish", "-q", cwd=d)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("cannot use", r.all)


class UrlTests(SourceCase):
    def setUp(self):
        super().setUp()
        self.pk = self.pack("greeter", "0.1.0", GREET)
        self.base = self.serve(os.path.dirname(self.pk))
        self.url = f"{self.base}/greeter-0.1.0.kitpkg"
        self.sha = hashlib.sha256(open(self.pk, "rb").read()).hexdigest()

    def test_add_url_pins_checksum_and_runs(self):
        app = self.e.app(main='use greeter\nprint(greeter_hi("Web"))\n')
        r = self.kit("add", self.url, cwd=app)
        self.assertEqual(r.returncode, 0, r.all)
        t = self.toml(app)
        self.assertIn(f'url = "{self.url}"', t)
        self.assertIn(f'checksum = "sha256:{self.sha}"', t)
        r = self.e.run_app(app)
        self.assertEqual((r.returncode, r.stdout.strip()), (0, "Hello, Web"), r.all)
        self.assertIn(f'source = "url+{self.url}"', open(os.path.join(app, "kol.lock")).read())

    def test_fragment_checksum_must_match(self):
        app = self.e.app()
        r = self.kit("add", f"{self.url}#sha256={'0' * 64}", cwd=app)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("checksum mismatch", r.all)
        self.assertNotIn("url", self.toml(app).split("[dependencies]")[1])
        r = self.kit("add", f"{self.url}#sha256={self.sha}", cwd=app)
        self.assertEqual(r.returncode, 0, r.all)

    def test_reinstall_uses_cache_offline(self):
        app = self.e.app(main='use greeter\nprint(greeter_hi("c"))\n')
        self.kit("add", self.url, cwd=app, check=True)
        self.kit("cache", "clean", "--all", cwd=app)  # store emptied; download cache removed too
        r = self.kit("install", cwd=app)
        self.assertEqual(r.returncode, 0, r.all)

    def test_server_swaps_file_after_pinning(self):
        app = self.e.app()
        self.kit("add", self.url, cwd=app, check=True)
        self.kit("cache", "clean", "--all", cwd=app)
        evil = self.pack("greeter", "0.1.0", GREET + "\n-- evil\n")
        os.replace(evil, self.pk)
        r = self.kit("install", cwd=app)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("checksum mismatch", r.all)

    def test_404_and_bad_scheme(self):
        app = self.e.app()
        r = self.kit("add", f"{self.base}/missing-1.0.0.kitpkg", cwd=app)
        self.assertNotEqual(r.returncode, 0)
        self.assertNotIn("Traceback", r.all)
        r = self.kit("add", "ftp://example.com/x.kitpkg", cwd=app)
        self.assertNotEqual(r.returncode, 0)
        self.assertNotIn("Traceback", r.all)

    def test_manifest_validation(self):
        app = self.e.app()
        t = self.toml(app).replace("[dependencies]\n", '[dependencies]\ng = { url = "ftp://x/g.kitpkg" }\n')
        self.e.write(os.path.join(app, "kol.toml"), t)
        r = self.kit("install", cwd=app)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("url", r.all)


if __name__ == "__main__":
    unittest.main(verbosity=1, warnings="ignore")
