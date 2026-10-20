#!/usr/bin/env python3
import os, subprocess, sys
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
res = subprocess.run([sys.executable, "kol.py", "run", "tests/args_raw.kol", "one", "two"], cwd=ROOT, capture_output=True, text=True, timeout=20)
expected = "2\none\nraw {text} \\n without escapes\n"
if res.returncode != 0 or res.stdout != expected:
    print("FAIL: command-line arguments/raw strings")
    print("stdout:", repr(res.stdout), "stderr:", res.stderr)
    sys.exit(1)
main_res = subprocess.run([sys.executable, "kol.py", "run", "tests/args_main.kol", "main-arg"], cwd=ROOT, capture_output=True, text=True, timeout=20)
if main_res.returncode != 0 or main_res.stdout != "1\nmain-arg\n":
    print("FAIL: explicit main() arguments", repr(main_res.stdout), main_res.stderr)
    sys.exit(1)
print("PASS: command-line arguments and raw strings")
