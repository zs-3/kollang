# Standard-library expansion

This change expands Kol's standard library with five pure-Kol modules, fills gaps in float/string array operations, and fixes a method code-generation crash.

## Added modules

- `std.ini`: read simple INI sections and key/value pairs, test whether a key/section exists, and enumerate keys.
- `std.validate`: ASCII-oriented helpers for digits, hex, decimal strings, identifiers, IPv4 addresses, ports, and basic email shape.
- `std.terminal`: ANSI styles, cursor movement, screen/line clearing, and `NO_COLOR`/`TERM` detection.
- `std.template`: single-pass `{key}` interpolation using parallel key/value arrays; unknown placeholders remain unchanged; includes HTML escaping.
- `std.semver`: SemVer identifier validation, major/minor/patch extraction, and precedence comparison including prerelease identifiers. Build metadata is ignored for precedence.
- `std.array`: completes common `[float]` sequence operations and adds `[str]` count/equality/sorted/take/drop/insert/remove/descending-sort helpers.

See [`../std/README.md`](../std/README.md) for module caveats and usage notes. Imports are flat, so the new functions intentionally use distinctive prefixes to reduce collisions.

## Compiler fix

`compiler/codegen.py` had an `is_main` reference inside `_gen_method_decl`, where that variable is not defined. It caused a Python `NameError` when compiling programs with type methods or interface implementations. The invalid check was removed from method generation while preserving argument initialization in top-level `main` generation.

## Validation performed

- All 78 `tests/*.kol` programs passed in debug mode. `grade_calc.kol` and `interfaces_real.kol`, which exposed the compiler bug, were rerun and passed after the fix.
- All 21 `tests/std/*.kol` programs passed in debug mode, including the expanded array tests.
- Release builds and executions passed for the extended module integration test, the array expansion test, and the five new module tests.
- Four Python harness tests passed (`test_invalid_import.py`, `unit_feature_guards.py`, `test_cross_compile.py`, `test_concurrency_runtime.py`). The remaining Python harness tests were not completed within the execution window, so the complete Python harness suite is not claimed as passing.

## Known boundaries

These additions do not make Kol's standard library equivalent in breadth to mature Python, Go, or Rust libraries. In particular, the existing HTTP module remains plain-HTTP only, networking and regex portability are limited by the current runtime, process commands use a shell, and validation helpers are not security policy. The next high-value work is a platform/runtime audit and stronger structured HTTP/JSON APIs, not adding untested module names.


### URL query parameter improvements

`std.url` now exposes `url_has_query_param(url, key)` to distinguish a missing parameter from an empty value, `url_query_param_or(url, key, fallback)` for defaults, and `url_query_param_count(url, key)` for repeated keys. These helpers preserve the existing first-value behavior of `url_query_param`.


### Typed JSON accessors

`std.json` now includes typed array accessors (`json_array_get_int`, `json_array_get_float`, `json_array_get_bool`) and fallback variants for arrays and object fields. Integer helpers reject booleans, nulls, decimal forms, and exponent forms rather than silently truncating them. Boolean fallback helpers distinguish a valid `false` value from a missing or non-boolean field. These helpers are convenience accessors, not a replacement for schema validation.
