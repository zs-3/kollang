#!/usr/bin/env python3
"""Runtime and compile-time checks for Kol's concurrency features that a plain
.kol/.expected pair cannot express: exit codes, stderr, environment variables."""
import os
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
KOL = os.path.join(ROOT, "kol.py")

failures = []


def run_source(src, env=None, timeout=60):
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "prog.kol")
        with open(path, "w", encoding="utf-8") as f:
            f.write(src)
        e = dict(os.environ)
        e.update(env or {})
        return subprocess.run([sys.executable, KOL, "run", path], capture_output=True, text=True, env=e, timeout=timeout)


def check(name, cond, detail=""):
    if not cond:
        failures.append(f"{name}: {detail}")


# --- deadlock detection ------------------------------------------------------
r = run_source("""
fn main()
    let ch = channel(int)
    print("before")
    let v = ch.receive()
    print("after")
end
""")
check("deadlock main", r.returncode == 2 and "deadlock" in r.stderr and r.stdout.strip() == "before",
      f"rc={r.returncode} out={r.stdout!r} err={r.stderr!r}")

r = run_source("""
task fn left(a: channel(int), b: channel(int)) -> int
    let x = a.receive()
    b.send(x)
    return x
end

fn main()
    let a = channel(int)
    let b = channel(int)
    let l = spawn left(a, b)
    let r = spawn left(b, a)
    print(await l)
end
""")
check("deadlock tasks", r.returncode == 2 and "deadlock" in r.stderr, f"rc={r.returncode} err={r.stderr!r}")

# a sleeping task is not a deadlock
r = run_source("""
task fn slow(ch: channel(int))
    sleep(1200)
    ch.send(7)
end

fn main()
    let ch = channel(int)
    let t = spawn slow(ch)
    print(ch.receive())
    await t
end
""")
check("no false deadlock", r.returncode == 0 and r.stdout.strip() == "7", f"rc={r.returncode} out={r.stdout!r} err={r.stderr!r}")

# --- panics -----------------------------------------------------------------
r = run_source("""
fn main()
    let ch = channel(int, 2)
    ch.close()
    ch.send(1)
end
""")
check("send on closed", r.returncode == 1 and "send on closed channel" in r.stderr, f"rc={r.returncode} err={r.stderr!r}")

r = run_source("""
fn main()
    let m = mutex()
    m.unlock()
end
""")
check("unlock unlocked", r.returncode == 1 and "unlock of an unlocked mutex" in r.stderr, f"rc={r.returncode} err={r.stderr!r}")

# --- compile-time errors ----------------------------------------------------
def expect_compile_error(name, src, fragment):
    r = run_source(src)
    check(name, r.returncode != 0 and fragment in (r.stdout + r.stderr), f"rc={r.returncode} out={r.stdout!r} err={r.stderr!r}")

expect_compile_error("spawn plain fn", """
fn plain(n: int) -> int
    return n
end
fn main()
    let f = spawn plain(1)
end
""", "task fn")

expect_compile_error("spawn needs call", """
fn main()
    let f = spawn 5
end
""", "spawn")

expect_compile_error("capacity zero", """
fn main()
    let ch = channel(int, 0)
end
""", "capacity must be at least 1")

expect_compile_error("bad select case", """
fn main()
    let ch = channel(int)
    select
        case ch.len()
            print("x")
    end
end
""", "select case")

expect_compile_error("select else and timeout", """
fn main()
    let ch = channel(int)
    select
        case v = ch.receive()
            print("x")
        case timeout(5)
            print("t")
        else
            print("e")
    end
end
""", "cannot have both")

expect_compile_error("wrong arity", """
task fn f(a: int, b: int) -> int
    return a + b
end
fn main()
    let x = spawn f(1, 2, 3)
end
""", "takes 2 argument")

# --- single worker: blocked tasks must still make progress -------------------
for name in ("blocked_tasks", "futures_fanout", "pool_workers"):
    with open(os.path.join(ROOT, "tests", name + ".kol"), encoding="utf-8") as f:
        src = f.read()
    with open(os.path.join(ROOT, "tests", name + ".expected"), encoding="utf-8") as f:
        expected = f.read().strip()
    r = run_source(src, env={"KOL_THREADS": "1"})
    check(f"KOL_THREADS=1 {name}", r.returncode == 0 and r.stdout.strip() == expected,
          f"rc={r.returncode} out={r.stdout!r} err={r.stderr!r}")

# --- strings shared between tasks (atomic reference counts) ------------------
r = run_source("""
task fn tag(s: str, n: int) -> int
    mut total = 0
    for i in 0..n
        let t = s + "-" + "x"
        total = total + t.len()
    end
    return total
end

fn main()
    let shared = "a heap allocated string shared by many tasks"
    mut fs: [future(int)] = []
    for i in 0..16
        fs.push(spawn tag(shared, 20000))
    end
    mut sum = 0
    for f in fs
        sum = sum + await f
    end
    print(sum)
    print(shared)
end
""")
check("shared strings", r.returncode == 0 and r.stdout.split("\n")[0] == str(16 * 20000 * 46) and
      r.stdout.split("\n")[1] == "a heap allocated string shared by many tasks", f"rc={r.returncode} out={r.stdout!r} err={r.stderr!r}")

if failures:
    print("FAILED:\n  " + "\n  ".join(failures))
    sys.exit(1)
print("ok")
