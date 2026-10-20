# Kol Bug Log

## Bug #1 — String Interpolation Lexing inside Functions
Description: String interpolation tokens within functions were failing tokenization due to string_mode_stack depth mismatch.
Reproduction: `fn greet(name: str) print("Hello {name}") end`
Fix: Updated lexer depth tracking in `_scan_string` and `_scan_string_continuation`.
Tests added: `tests/functions.kol`

## Bug #2 — Match Arm Multi-Line Parsing
Description: Parsing match statement arms with newlines before pattern triggered newline expectations.
Reproduction: `match x \n 200 -> print("OK") end`
Fix: Added `_skip_newlines()` inside match arm loop in `compiler/parser.py`.
Tests added: `tests/match.kol`

## Bug #3 — String concatenation use-after-free
Description: Temporaries produced in a concatenation chain were released before the combined result was copied.
Fix: Codegen now keeps intermediates alive until the final concat completes.
Tests added: `tests/spawn_await.kol`, `tests/test_concurrency_runtime.py` (shared-string stress)

## Bug #4 — Non-atomic string ARC and in-place append on shared strings
Description: String refcounts were plain integers and `kol_str_append` mutated buffers in place even when shared, which is unsafe once strings cross threads.
Fix: Refcounts become atomic once the first worker starts (`kol_mt`); append detaches (copy-on-write) when the buffer is shared.
Tests added: `tests/test_concurrency_runtime.py`

## Bug #5 — Missing KolError import in codegen
Description: Codegen error paths raised `KolError` without importing it, producing a Python NameError instead of a diagnostic.
Fix: Added `from compiler.errors import KolError`.

## Bug #6 — Channel close / zero-value semantics and void tasks
Description: The old channel runtime had no close, no bounded buffer and spawned one OS thread per call; void tasks were unsupported.
Fix: New `kol_conc.h` runtime: bounded ring-buffer channels with idempotent close (send on closed panics, receive on closed+drained yields the zero value), pooled tasks including void tasks.
Tests added: `tests/channel_buffered.kol`, `tests/select_basic.kol`, `tests/blocked_tasks.kol`

## Bug #7 — `--release` reads `[int]` elements as 32-bit `int`
Description: `for n in nums` and `nums[i]` on a parameter typed `[int]` generated `int* restrict` instead of `int64_t*` in release mode, so `[1,2,3]` summed to 3.
Fix: map `list_int` to `int64_t` in the for-loop and index paths in `compiler/codegen.py`.
Tests added: `tests/run_std_tests.py` runs every std test in debug and `--release`.

## Bug #8 — string concatenation freed its own result (also fixed independently as Bug #3)
Description: `kol_str_concat` appends in place when the left operand is uniquely owned; the generated code then released that operand, freeing the buffer the result still used. Chains such as `a + b + c` or `"x" + msg + "{n}"` returned garbage or crashed. Operands that only borrow storage (variables, elements, fields) were also released without being retained.
Fix: borrowed operands are retained/released in pairs, temporaries are released, and the left operand is skipped when its buffer was reused (`kol_arc_release_str_unless`).
Test: `tests/str_concat_chain.kol`.

## Bug #9 — `or` / `||` could not be used as logical OR
Description: `or` was parsed only as the optional-default operator, so `a == 1 or a == 2` failed to compile and `||` was a syntax error.
Fix: `x or y` is a short-circuit logical OR when `x` is a plain bool; it stays the default operator after a call to a `T!` / `T?` function. `||` is accepted as a synonym.
Test: `tests/logical_or.kol`.

## Bug #10 — `try` was ignored; postfix `?` and `return fallible()` did not work
Description: `try expr` parsed but generated nothing (a failing call carried on as if it succeeded); `expr?` was a syntax error; `return other_fallible(x)` did not compile.
Fix: `try expr` / `expr?` unwrap the value or return the error (compile error outside a `T!` function); returning a fallible call forwards its result.
Tests: `tests/try_operator.kol`.

## Bug #11 — fallible arrays, array return inference
Description: `fn f() -> [T]!` was unsupported and `let x = f()` for a function returning `[T]` produced invalid C.
Fix: array results are boxed in `KolResult` (`kol_result_ok_arr` / `kol_result_take_arr`); functions record element-typed list return types; `?` and `or` take the type of the underlying call.
Test: `tests/fallible_array.kol`.

## Bug #12 — arrays aliased on copy; string elements were not kept alive
Description: `mut b = a` shared storage with `a`; `prev = cur` between arrays left a dangling pointer (use-after-free); a callee changed its caller's array; pushing a string variable into an array did not retain it; `[]` annotated `[str]`/`[float]` used 8-byte slots; `pop()` returned nothing; replacing a `[str]` element leaked the old string.
Fix: arrays have value semantics (clone on copy from a variable/element/field, old value freed on reassignment, parameters cloned when returned); borrowed strings are retained when stored; empty typed literals use the annotated element size; `pop()` returns the element; `arr[i] = s` releases the overwritten string.
Tests: `tests/array_value_semantics.kol`, `tests/array_string_memory.kol`.

## Bug #13 — string literals broke the generated C
Description: only `"` and newline were escaped, so `\r`, `\t`, `\\` (backslash) and `??` sequences produced invalid C; interpolation text was not escaped at all.
Fix: one `_c_escape` routine used for literals and interpolation parts.
Test: `tests/str_escapes.kol`.

## Bug #14 — strings could not be ordered; `from` / `step` were reserved everywhere
Fix: `<`, `>`, `<=`, `>=` on strings use `kol_str_cmp`; `from` and `step` are contextual keywords and valid identifiers elsewhere.
Tests: `tests/str_compare.kol`, `tests/soft_keywords.kol`.

## Known limitations (not changed)
- Bitwise operators and cast expressions are still missing (`std.bits`, `to_float`, `to_int` cover them).
- Variables declared at the top level of a program are never freed (reclaimed at exit).
- `runtime/kol_conc.h` `kol_future_wait` reads `f->result` after waking; AddressSanitizer reports a heap-use-after-free there in `examples/concurrency_worker_pool.kol` (reproduces on `main` without the stdlib branch). Not changed here.

## Bug #15 — duplicate function names across modules produced a raw C redefinition error
Description: imports are flat, so two imported modules (or, with Kit, two packages) that define the same top-level function made gcc fail with `redefinition of '_kol_fn_name'`, giving no hint which files clashed or how to fix it.
Fix: `check_duplicate_functions` in `kol.py` reports `function 'x' is defined twice: also defined at file:line`, with a hint to import selectively with `use pkg: (name as other)` (which renames and was verified to work).
Tests: `tests/test_kit.py` (`CompilerIntegrationTests.test_duplicate_function_gets_clear_error`, `test_two_packages_with_clashing_names_explain_themselves`).

## Feature — Kit package manager (`kit.py`, `kitlib/`, `compiler/packages.py`)
`use <package>` / `use <package>.<module>` now resolves installed packages after local files and `std.*`. Imports from packages are checked per importing file: a package may only use packages it declares as dependencies, and dev-dependencies only from `tests/`, `examples/`, `benches/`. See `docs/kit.md`.
Tests: `tests/test_kit.py` (53+ cases, also run with the fallback TOML parser on Python 3.11 and 3.12).

## Feature — Kit: dependencies from `.kitpkg` files and URLs, optional chibicc
`kit add`/`kit install` accept a `.kitpkg` path or an http(s) URL (`#sha256=` supported); `kol.toml` gains `url`/`archive`/`checksum` dependency keys, locked as `url+…`/`archive+…` with sha256 and verified on every install. `kit jit`, `kit run --jit` and `--cc chibicc` compile with an optionally installed chibicc in RAM. Kol's runtime/concurrency/atomics were NOT changed (chibicc gets a Kit-side compat header).
Tests: `tests/test_kit_sources.py` (11 cases, local HTTP server), `tests/test_kit_chibicc.py` (17 cases).

## Bug #16 — returning a `str` parameter, field or element caused a double release (use-after-free)
Description: `fn f(s: str) -> str  return s end` returned the caller's own reference without retaining it, so the caller released it twice (its own variable and the returned value). Lists were already cloned in this case; `str` and handle objects were not. Found by AddressSanitizer on `std.url` (`url_strip_fragment`); it reproduces on the default branch.
Fix: `ReturnStmt` codegen retains a returned `str` / handle that is borrowed and not a local variable (`compiler/codegen.py`).
Test: `tests/return_borrowed_str.kol` (ASan-clean; fails with a heap-use-after-free if the fix is removed).

## Note — chibicc and the new runtime
The stdlib runtime now includes `<regex.h>`, which chibicc cannot parse. `kitlib/chibicc_compat.h` supplies the few declarations the runtime uses (x86-64 glibc layout); the runtime itself is unchanged.

## Known issue (unchanged, pre-existing)
`tests/concurrency_std_interop.kol` still reports a heap-use-after-free in `kol_future_wait` under ASan on the default branch (see the earlier note about `kol_future_wait`). Not touched here.
