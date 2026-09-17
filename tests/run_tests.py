#!/usr/bin/env python3
import sys
import os
import glob
import subprocess

def run_tests():
    test_files = glob.glob("tests/*.kol")
    passed = 0
    failed = 0

    for test_file in sorted(test_files):
        print(f"Running {test_file}...", end=" ")
        res = subprocess.run([sys.executable, "kol.py", "run", test_file], capture_output=True, text=True)
        if res.returncode == 0:
            print("PASSED")
            passed += 1
        else:
            print("FAILED")
            print(res.stderr)
            failed += 1

    print(f"\nSummary: {passed} passed, {failed} failed out of {len(test_files)} tests.")
    if failed > 0:
        sys.exit(1)

if __name__ == "__main__":
    run_tests()
