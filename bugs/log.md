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
