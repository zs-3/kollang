#!/usr/bin/env python3
import sys
import os
import subprocess

def test_cross_compile():
    kol_file = "tests/hello.kol"
    target = "linux-x86_64"
    binary = "./hello-linux-x86_64"

    if os.path.exists(binary):
        os.remove(binary)

    cmd = [sys.executable, "kol.py", "build", kol_file, "--target", target]
    res = subprocess.run(cmd, capture_output=True, text=True)

    if res.returncode != 0:
        print(f"FAIL: 'kol build --target {target}' failed with output:\n{res.stdout}\n{res.stderr}")
        sys.exit(1)

    if not os.path.exists(binary):
        print(f"FAIL: Expected binary {binary} was not created!")
        sys.exit(1)

    run_res = subprocess.run([binary], capture_output=True, text=True)

    with open("tests/hello.expected", "r", encoding="utf-8") as f:
        expected = f.read()

    if run_res.stdout != expected:
        print(f"FAIL: Expected output:\n{expected}\nGot:\n{run_res.stdout}")
        if os.path.exists(binary):
            os.remove(binary)
        sys.exit(1)

    if os.path.exists(binary):
        os.remove(binary)
    if os.path.exists("hello.c"):
        os.remove("hello.c")

    print("PASS: test_cross_compile passed.")

if __name__ == "__main__":
    test_cross_compile()
