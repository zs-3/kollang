#!/usr/bin/env python3
import os, sys
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from compiler.codegen import Codegen
cases = {
    "linux-x86_64": ("linux", "x86_64"),
    "linux-arm64": ("linux", "arm64"),
    "macos-x86_64": ("macos", "x86_64"),
    "macos-arm64": ("macos", "arm64"),
}
for target, expected in cases.items():
    c = Codegen("test.kol", target=target)
    got = (c._host_platform(), c._host_arch())
    if got != expected:
        print(f"FAIL {target}: {got} != {expected}")
        sys.exit(1)
print("PASS: compile-time platform/architecture target mapping")
