# Kol Programming Language

Kol is a lightweight systems programming language designed for simple syntax, native performance through a C99 backend, and a memory-conscious runtime.

## Features
- **Easy**: Reads like BASIC/pseudocode, beginner friendly.
- **Fast**: Compiles directly to native machine code via C99 backend.
- **Memory-conscious**: Automatic Reference Counting (ARC), small-string optimization (SSO), and arena allocators. As with any systems language, runtime safety depends on the correctness of compiler and runtime code.

## Quick Start

```bash
# Compile and run immediately
./kol.py run tests/hello.kol

# Build optimized binary
./kol.py build --release tests/hello.kol
```

## Syntax Example

```kol
fn add(a: int, b: int) -> int
    return a + b
end

let result = add(10, 20)
print("Result: {result}")
```

## Standard library

The `std/` modules cover core utilities, collections, strings, numeric helpers, encoding, statistics, JSON utilities, regular expressions, CSV, URL handling, UUIDs, SHA-256/MD5 digests, date/time helpers, CLI argument parsing, filesystem and process operations, logging, TCP/UDP networking and basic HTTP/1.x primitives. The expanded helpers add INI reading, input validation, ANSI terminal styling, single-pass templates, SemVer validation/comparison, strict Base64URL handling, typed JSON builders, and query-parameter append/remove/set helpers. See [std/README.md](std/README.md) for APIs and portability/security caveats.

KOL also supports raw string literals with backticks and command-line arguments via `use std.args` (`args()`, `arg_count()`, `arg_at(index)`).

```kol
use std.json
use std.args

print(json_quote("hello"))
for arg in args()
    print(arg)
end
```

## Concurrency

Kol has real, thread-pool based concurrency: `spawn`/`await` tasks, buffered
channels, `select` (with `timeout`/`else`), mutexes, atomics, and runtime
deadlock detection.

```kol
fn square(n: int) -> int
    return n * n
end

let f = spawn square(12)
print(await f)

let ch = channel(int, 8)
spawn producer(ch)
for v in ch
    print(v)
end
```

See [docs/concurrency.md](docs/concurrency.md) for the full guide,
`examples/concurrency_*.kol` for runnable programs and
`benchmarks/concurrency/` for the benchmark suite against C, Go and Nim.

## Packages with Kit

`kit` is Kol's package manager and project tool (see [docs/kit.md](docs/kit.md)).

```bash
./kit.py new myapp && cd myapp      # create a project
kit add greeter                     # add a library from your registry
kit add ./tool-1.0.0.kitpkg            # ...or from a .kitpkg file or https:// URL (checksum pinned)
kit run                             # build and run
```

```kol
use greeter                         -- then use its functions directly
print(greeter_hello("Kol"))
```

This version works offline (directory registries); online support is next.

## Running Tests

```bash
./kol.py run tests/hello.kol
./kol.py run tests/variables.kol
./kol.py run tests/functions.kol
./kol.py run tests/loops.kol
./kol.py run tests/conditionals.kol
./kol.py run tests/types.kol
./kol.py run tests/errors.kol
./kol.py run tests/match.kol
./kol.py run tests/collections.kol
./kol.py run tests/memory.kol
./kol.py run tests/interfaces.kol
./kol.py run tests/defer.kol
./kol.py run tests/arena.kol
./kol.py run tests/pipe.kol
./kol.py run tests/generics.kol
./kol.py run tests/fibonacci.kol
./kol.py run tests/stress.kol
./kol.py run tests/std_new.kol
./kol.py run tests/crypto_vectors.kol
```
