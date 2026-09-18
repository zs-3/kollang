#!/usr/bin/env python3
import time
import subprocess
import os

def run_benchmarks():
    print("Running real benchmark suite...")

    # --- 1. Fibonacci 30 ---
    # Python
    py_fib_code = "def fib(n):\n    return n if n <= 1 else fib(n-1) + fib(n-2)\nprint(fib(30))\n"
    with open("/tmp/bench_fib.py", "w") as f:
        f.write(py_fib_code)
    t0 = time.perf_counter()
    res_py_fib = subprocess.run(["python3", "/tmp/bench_fib.py"], capture_output=True, text=True)
    t_py_fib = time.perf_counter() - t0

    # C
    c_fib_code = '#include <stdio.h>\nlong fib(long n) { return n <= 1 ? n : fib(n-1) + fib(n-2); }\nint main(void) { printf("%ld\\n", fib(30)); return 0; }\n'
    with open("/tmp/bench_fib.c", "w") as f:
        f.write(c_fib_code)
    subprocess.run(["gcc", "-O2", "-o", "/tmp/bench_fib_c", "/tmp/bench_fib.c"])
    t0 = time.perf_counter()
    res_c_fib = subprocess.run(["/tmp/bench_fib_c"], capture_output=True, text=True)
    t_c_fib = time.perf_counter() - t0

    # Kol
    kol_fib_code = "fn fibonacci(n: int) -> int\n    if n <= 1\n        return n\n    end\n    return fibonacci(n - 1) + fibonacci(n - 2)\nend\nprint(fibonacci(30))\n"
    with open("/tmp/bench_fib.kol", "w") as f:
        f.write(kol_fib_code)
    subprocess.run(["python3", "kol.py", "build", "--release", "/tmp/bench_fib.kol"])
    t0 = time.perf_counter()
    res_kol_fib = subprocess.run(["./bench_fib"], capture_output=True, text=True)
    t_kol_fib = time.perf_counter() - t0


    # --- 2. Loop 10 Million ---
    # Python
    py_loop_code = "s = 0\nfor i in range(10000000):\n    s += i\nprint(s)\n"
    with open("/tmp/bench_loop.py", "w") as f:
        f.write(py_loop_code)
    t0 = time.perf_counter()
    res_py_loop = subprocess.run(["python3", "/tmp/bench_loop.py"], capture_output=True, text=True)
    t_py_loop = time.perf_counter() - t0

    # C
    c_loop_code = '#include <stdio.h>\nint main(void) { long s = 0; for (long i = 0; i < 10000000; i++) s += i; printf("%ld\\n", s); return 0; }\n'
    with open("/tmp/bench_loop.c", "w") as f:
        f.write(c_loop_code)
    subprocess.run(["gcc", "-O2", "-o", "/tmp/bench_loop_c", "/tmp/bench_loop.c"])
    t0 = time.perf_counter()
    res_c_loop = subprocess.run(["/tmp/bench_loop_c"], capture_output=True, text=True)
    t_c_loop = time.perf_counter() - t0

    # Kol
    kol_loop_code = "mut s = 0\nfor i in 0..10000000\n    s = s + i\nend\nprint(s)\n"
    with open("/tmp/bench_loop.kol", "w") as f:
        f.write(kol_loop_code)
    subprocess.run(["python3", "kol.py", "build", "--release", "/tmp/bench_loop.kol"])
    t0 = time.perf_counter()
    res_kol_loop = subprocess.run(["./bench_loop"], capture_output=True, text=True)
    t_kol_loop = time.perf_counter() - t0


    # --- 3. String Build ---
    # Python
    py_str_code = "s = ''\nfor i in range(1000):\n    s += 'x'\nprint(len(s))\n"
    with open("/tmp/bench_str.py", "w") as f:
        f.write(py_str_code)
    t0 = time.perf_counter()
    res_py_str = subprocess.run(["python3", "/tmp/bench_str.py"], capture_output=True, text=True)
    t_py_str = time.perf_counter() - t0

    # C
    c_str_code = '#include <stdio.h>\n#include <stdlib.h>\n#include <string.h>\nint main(void) { char* s = malloc(1001); s[0] = \'\\0\'; for (int i = 0; i < 1000; i++) strcat(s, "x"); printf("%zu\\n", strlen(s)); free(s); return 0; }\n'
    with open("/tmp/bench_str.c", "w") as f:
        f.write(c_str_code)
    subprocess.run(["gcc", "-O2", "-o", "/tmp/bench_str_c", "/tmp/bench_str.c"])
    t0 = time.perf_counter()
    res_c_str = subprocess.run(["/tmp/bench_str_c"], capture_output=True, text=True)
    t_c_str = time.perf_counter() - t0

    # Kol
    kol_str_code = "mut s = \"\"\nfor i in 0..1000\n    s = s + \"x\"\nend\nprint(s)\n"
    with open("/tmp/bench_str.kol", "w") as f:
        f.write(kol_str_code)
    subprocess.run(["python3", "kol.py", "build", "--release", "/tmp/bench_str.kol"])
    t0 = time.perf_counter()
    res_kol_str = subprocess.run(["./bench_str"], capture_output=True, text=True)
    t_kol_str = time.perf_counter() - t0

    # Cleanup temporary built binaries
    for tmp_bin in ["bench_fib", "bench_loop", "bench_str", "bench_fib.c", "bench_loop.c", "bench_str.c"]:
        if os.path.exists(tmp_bin):
            os.remove(tmp_bin)

    ratio_fib = f"{t_kol_fib / max(t_c_fib, 0.000001):.2f}x"
    ratio_loop = f"{t_kol_loop / max(t_c_loop, 0.000001):.2f}x"
    ratio_str = f"{t_kol_str / max(t_c_str, 0.000001):.2f}x"

    results_md = f"""## Real Benchmark Results — {time.strftime("%Y-%m-%d")}
All times in seconds. Lower is better.

| Benchmark | Kol | Python | C | Kol/C ratio |
|---|---|---|---|---|
| fibonacci_30 | {t_kol_fib:.4f}s | {t_py_fib:.4f}s | {t_c_fib:.4f}s | {ratio_fib} |
| loop_10million | {t_kol_loop:.4f}s | {t_py_loop:.4f}s | {t_c_loop:.4f}s | {ratio_loop} |
| string_build | {t_kol_str:.4f}s | {t_py_str:.4f}s | {t_c_str:.4f}s | {ratio_str} |
"""

    with open("benchmarks/results.md", "w") as f:
        f.write(results_md)

    print("Benchmarks completed successfully. Results written to benchmarks/results.md:\n")
    print(results_md)

if __name__ == "__main__":
    run_benchmarks()
