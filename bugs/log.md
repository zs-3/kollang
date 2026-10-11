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

## Bug #3 — `--release` reads `[int]` elements as 32-bit `int`
Description: `for n in nums` and `nums[i]` on a parameter typed `[int]` generated `int* restrict` instead of `int64_t*` in release mode, so `[1,2,3]` summed to 3.
Fix: map `list_int` to `int64_t` in the for-loop and index paths in `compiler/codegen.py`.
Tests added: `tests/run_std_tests.py` runs every std test in debug and `--release`.

## Bug #4 — string concatenation freed its own result
Description: `kol_str_concat` appends in place when the left operand is uniquely owned; the generated code then released that operand, freeing the buffer the result still used. Chains such as `a + b + c` or `"x" + msg + "{n}"` returned garbage or crashed. Operands that only borrow storage (variables, elements, fields) were also released without being retained.
Fix: borrowed operands are retained/released in pairs, temporaries are released, and the left operand is skipped when its buffer was reused (`kol_arc_release_str_unless`).
Test: `tests/str_concat_chain.kol`.

## Bug #5 — `or` / `||` could not be used as logical OR
Description: `or` was parsed only as the optional-default operator, so `a == 1 or a == 2` failed to compile and `||` was a syntax error.
Fix: `x or y` is a short-circuit logical OR when `x` is a plain bool; it stays the default operator after a call to a `T!` / `T?` function. `||` is accepted as a synonym.
Test: `tests/logical_or.kol`.

## Bug #6 — `try` was ignored; postfix `?` and `return fallible()` did not work
Description: `try expr` parsed but generated nothing (a failing call carried on as if it succeeded); `expr?` was a syntax error; `return other_fallible(x)` did not compile.
Fix: `try expr` / `expr?` unwrap the value or return the error (compile error outside a `T!` function); returning a fallible call forwards its result.
Tests: `tests/try_operator.kol`.

## Bug #7 — fallible arrays, array return inference
Description: `fn f() -> [T]!` was unsupported and `let x = f()` for a function returning `[T]` produced invalid C.
Fix: array results are boxed in `KolResult` (`kol_result_ok_arr` / `kol_result_take_arr`); functions record element-typed list return types; `?` and `or` take the type of the underlying call.
Test: `tests/fallible_array.kol`.

## Bug #8 — arrays aliased on copy; string elements were not kept alive
Description: `mut b = a` shared storage with `a`; `prev = cur` between arrays left a dangling pointer (use-after-free); a callee changed its caller's array; pushing a string variable into an array did not retain it; `[]` annotated `[str]`/`[float]` used 8-byte slots; `pop()` returned nothing; replacing a `[str]` element leaked the old string.
Fix: arrays have value semantics (clone on copy from a variable/element/field, old value freed on reassignment, parameters cloned when returned); borrowed strings are retained when stored; empty typed literals use the annotated element size; `pop()` returns the element; `arr[i] = s` releases the overwritten string.
Tests: `tests/array_value_semantics.kol`, `tests/array_string_memory.kol`.

## Bug #9 — string literals broke the generated C
Description: only `"` and newline were escaped, so `\r`, `\t`, `\\` (backslash) and `??` sequences produced invalid C; interpolation text was not escaped at all.
Fix: one `_c_escape` routine used for literals and interpolation parts.
Test: `tests/str_escapes.kol`.

## Bug #10 — strings could not be ordered; `from` / `step` were reserved everywhere
Fix: `<`, `>`, `<=`, `>=` on strings use `kol_str_cmp`; `from` and `step` are contextual keywords and valid identifiers elsewhere.
Tests: `tests/str_compare.kol`, `tests/soft_keywords.kol`.

## Known limitations (not changed)
- Bitwise operators and cast expressions are still missing (`std.bits`, `to_float`, `to_int` cover them).
- Variables declared at the top level of a program are never freed (reclaimed at exit).
