#!/usr/bin/env python3
import sys
import os
import subprocess

def test_invalid_import():
    tmp_kol = "/tmp/test_invalid_import.kol"
    with open(tmp_kol, "w") as f:
        f.write("use std.totally_fake_module\nprint(1)\n")

    cmd = [sys.executable, "kol.py", "check", tmp_kol]
    res = subprocess.run(cmd, capture_output=True, text=True)

    if os.path.exists(tmp_kol):
        os.remove(tmp_kol)

    if res.returncode == 0:
        print("FAIL: Expected invalid import to fail, but it succeeded!")
        sys.exit(1)

    output = res.stdout + res.stderr
    print("Error output from invalid import check:\n", output)

    if "Cannot resolve import 'std.totally_fake_module'" not in output:
        print("FAIL: Output does not mention exact bad import path 'std.totally_fake_module'")
        sys.exit(1)

    if "std/totally_fake_module.kol" not in output:
        print("FAIL: Output does not mention attempted path 'std/totally_fake_module.kol'")
        sys.exit(1)

    print("PASS: Invalid import correctly raised KolError with details.")

if __name__ == "__main__":
    test_invalid_import()
