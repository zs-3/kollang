#!/usr/bin/env python3
"""Run every tests/std/*.kol file in debug (`kol run`) and optimized
(`kol build --release`) mode; each must print '... PASSED' and no
'ASSERTION FAILED' line, and exit with status 0."""
import tempfile
import glob
import os
import subprocess
import sys

root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
failed = 0
files = sorted(glob.glob(os.path.join(root, "tests", "std", "*.kol")))
def check(res):
    out = res.stdout + res.stderr
    return res.returncode == 0 and "PASSED" in out and "ASSERTION FAILED" not in out, out


for path in files:
    rel = os.path.relpath(path, root)
    res = subprocess.run([sys.executable, os.path.join(root, "kol.py"), "run", path],
                         capture_output=True, text=True, errors="replace")
    ok, out = check(res)
    mode = "debug"
    if ok:
        with tempfile.TemporaryDirectory() as tmp:
            build = subprocess.run([sys.executable, os.path.join(root, "kol.py"), "build", "--release", path],
                                   capture_output=True, text=True, errors="replace", cwd=tmp)
            exe = os.path.join(tmp, os.path.splitext(os.path.basename(path))[0])
            if build.returncode != 0 or not os.path.exists(exe):
                ok, out, mode = False, build.stdout + build.stderr, "release build"
            else:
                run = subprocess.run([exe], capture_output=True, text=True, errors="replace")
                ok, out = check(run)
                mode = "release"
    print(("PASS " if ok else "FAIL ") + rel + ("" if ok else f"  [{mode}]"))
    if not ok:
        failed += 1
        print(out[-800:])
print(f"\n{len(files) - failed} passed, {failed} failed out of {len(files)} std test files")
sys.exit(1 if failed else 0)
