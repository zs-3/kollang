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
        expected_file = test_file.replace(".kol", ".expected")
        print(f"Running {test_file}...", end=" ")

        with open(test_file, "r", encoding="utf-8") as f:
            first_line = f.readline()

        if first_line.startswith("#!"):
            res = subprocess.run([sys.executable, test_file], capture_output=True, text=True)
            if res.returncode == 0:
                print("PASSED")
                passed += 1
            else:
                print("FAILED (Test Script Failed)")
                print(res.stdout + res.stderr)
                failed += 1
            continue

        res = subprocess.run([sys.executable, "kol.py", "run", test_file], capture_output=True, text=True)
        if res.returncode != 0:
            print("FAILED (Compilation/Runtime Error)")
            print(res.stderr)
            failed += 1
            continue

        actual_stdout = res.stdout.strip()

        if os.path.exists(expected_file):
            with open(expected_file, "r", encoding="utf-8") as f:
                expected_stdout = f.read().strip()

            if actual_stdout == expected_stdout:
                print("PASSED")
                passed += 1
            else:
                print("FAILED (Output Mismatch)")
                print(f"Expected:\n{expected_stdout}")
                print(f"Actual:\n{actual_stdout}")
                failed += 1
        else:
            print("PASSED (No .expected file)")
            passed += 1

    py_tests = ["tests/test_invalid_import.py", "tests/unit_feature_guards.py", "tests/test_cross_compile.py"]
    for py_test in py_tests:
        print(f"Running {py_test}...", end=" ")
        res = subprocess.run([sys.executable, py_test], capture_output=True, text=True)
        if res.returncode == 0:
            print("PASSED")
            passed += 1
        else:
            print("FAILED (Python Test Failed)")
            print(res.stdout + res.stderr)
            failed += 1

    total_tests = len(test_files) + len(py_tests)
    print(f"\nSummary: {passed} passed, {failed} failed out of {total_tests} tests.")
    if failed > 0:
        sys.exit(1)

if __name__ == "__main__":
    run_tests()
