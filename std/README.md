# Kol standard library

Import with `use std.<module>`. Imports are **flat**: every function lands in one
global namespace, so a name may only be defined by one module.

| Module | What it gives you |
|---|---|
| `core` | assertions (`assert_true`, `assert_eq_int/str/float/bool`), `abs_*`, `min/max/clamp`, `div_floor`, `mod_floor` |
| `math` | C math (`sqrt`, `pow`, trig, `log`, `floor`...), `to_float`/`to_int`/`round_int`, `isqrt`, `pow_mod`, `binomial`, `prime_factors`, `gcd`, `lcm` |
| `str` | char tests, `index_of`, `count_substr`, `str_compare`, `capitalize`, `title_case`, `center_str`, `str_chars/lines/words`, trimming, slicing |
| `array`, `list` | copy, slice, concat, range, unique, binary search, sorting for `[int]`, `[float]`, `[str]` |
| `stats` | `mean`, `median`, `variance`, `std_dev`, `mode_int`, `percentile` |
| `random` | seedable `rand_int`, `rand_float`, `shuffle_int`, `rand_alnum` |
| `bits`, `hash` | bitwise ops, `popcount`; FNV-1a, DJB2, CRC32 (not cryptographic) |
| `convert`, `format` | parsing (`str_to_float`, bases, roman), `format_fixed`, `format_thousands`, `format_bytes`, `format_duration` |
| `io`, `os`, `path` | stdin/stdout, files, env vars, exit, path helpers |
| `time` | clocks, sleep, UTC date parts, ISO formatting |
| `encoding`, `text` | hex, base64, URL encoding; `levenshtein`, `word_wrap`, case conversion |

Many functions bind to C helpers in `runtime/kol_runtime.h` through
`extern fn ... from "kol_runtime"`.

## Language notes

- Imports are flat: a function name may only be defined by one module.
- Arrays have **value semantics**: `let b = a`, `b = a` and passing results around
  never share storage, so functions here that return arrays return fresh ones and
  never modify their arguments.
- `or` is a logical OR on booleans; on a call that can fail (`T!`) or be none (`T?`)
  it supplies the default value.
- `try expr` / `expr?` unwrap a `T!` result or return its error from the enclosing
  `T!` function.
- There are no cast operators (use `to_float`, `to_int`) and no bitwise operators
  (use `std.bits`).
