# Kol standard library

Import with `use std.<module>`. Imports are **flat**: every function lands in one
global namespace, so a name may only be defined by one module.

| Module | What it gives you |
|---|---|
| `core` | assertions (`assert_true`, `assert_eq_int/str/float/bool`), `abs_*`, `min/max/clamp`, `div_floor`, `mod_floor` |
| `math` | C math (`sqrt`, `pow`, trig, `log`, `floor`...), `to_float`/`to_int`/`round_int`, `isqrt`, `pow_mod`, `binomial`, `prime_factors`, `gcd`, `lcm` |
| `str` | char tests, `index_of`, `count_substr`, `str_compare`, `capitalize`, `title_case`, `center_str`, `str_chars/lines/words`, trimming, slicing |
| `array`, `list` | copy, slice, concat, range, unique, binary search, sorting and sequence operations for `[int]`, `[float]`, `[str]` |
| `stats` | `mean`, `median`, `variance`, `std_dev`, `mode_int`, `percentile` |
| `random` | seedable `rand_int`, `rand_float`, `shuffle_int`, `rand_alnum` |
| `bits`, `hash` | bitwise ops, `popcount`; FNV-1a, DJB2, CRC32 (not cryptographic) |
| `convert`, `format` | parsing (`str_to_float`, bases, roman), `format_fixed`, `format_thousands`, `format_bytes`, `format_duration` |
| `io`, `os`, `path` | stdin/stdout, files, env vars, exit, path helpers |
| `time` | clocks, sleep, UTC date parts, ISO formatting |
| `encoding`, `text` | hex, strict Base64/Base64URL encoding and decoding, percent encoding; `levenshtein`, `word_wrap`, case conversion |
| `json` | JSON validation, string escaping, typed object/array accessors with fallbacks, typed string/int/float/bool builders, raw array element access and lightweight JSON building |
| `regex` | POSIX extended regex matching, first match and global replacement (not PCRE; no linear-time guarantee) |
| `csv` | quoted CSV rows/documents, escaped quotes, multiline fields, CRLF handling, header lookup and fallback-friendly document access |
| `url` | percent/query encoding, scheme/host/path extraction, query/fragment helpers, repeated query-value retrieval, query append/remove/set operations |
| `uuid`, `crypto` | UUID v4 and SHA-256/MD5 text digests |
| `datetime` | namespaced aliases for UTC timestamp formatting and date calculations |
| `cli`, `args` | argument parsing helpers and runtime command-line arguments |
| `test` | assertion helpers for standalone test programs; language-level `test` blocks remain the test runner |
| `ini` | read simple INI files with sections, key/value pairs, comments, and key enumeration |
| `validate` | ASCII-oriented checks for digits, decimal strings, identifiers, IPv4, ports, hex, and basic email shape |
| `terminal` | ANSI text styles, cursor movement, screen clearing, and `NO_COLOR`/`TERM` detection |
| `template` | single-pass `{key}` rendering and HTML escaping |
| `semver` | SemVer 2.0.0 validation, core extraction, and precedence comparison |
| `fs`, `process`, `log` | filesystem helpers, shell command execution/output, stderr logging |
| `net`, `http` | synchronous TCP/UDP sockets, DNS lookup and basic HTTP/1.x client/server primitives; plain HTTP only |

The `ini`, `validate`, `terminal`, `template`, and `semver` modules are implemented in Kol itself. Many other functions bind to C helpers in `runtime/kol_runtime.h` through
`extern fn ... from "kol_runtime"`.

## Language notes

- Imports are flat: a function name may only be defined by one module.
- Arrays have **value semantics**: `let b = a`, `b = a` and passing results around
  should not share mutable storage. Nested arrays of scalar values now use deep clone/free
  helpers; arrays of handles or custom structs still have more specialized ownership rules.
- `or` is a logical OR on booleans; on a call that can fail (`T!`) or be none (`T?`)
  it supplies the default value.
- `try expr` / `expr?` unwrap a `T!` result or return its error from the enclosing
  `T!` function.
- There are no cast operators (use `to_float`, `to_int`) and no bitwise operators
  (use `std.bits`).


## Portability and safety notes

- `std.regex` currently uses POSIX extended regular expressions on POSIX systems. It is not PCRE and does not promise linear-time matching. Windows currently returns no match from these regex helpers.
- `std.http` supports plain `http://` requests only; HTTPS/TLS, chunked-body decoding and redirect following are not implemented. `http_get_raw` returns the raw response, and server users build on the synchronous TCP primitives.
- `std.process` passes commands to the system shell. Never pass untrusted text into these functions without strict validation.
- `std.net` is synchronous; sockets are integer handles and must be closed on all code paths. The new network helpers are POSIX-backed in this snapshot.
- `sha256` and `md5` hash text strings (NUL-terminated KOL strings), not arbitrary binary buffers. MD5 is obsolete for security-sensitive uses; neither digest is a password hash.
- `uuid_v4` reads `/dev/urandom` on POSIX. Its fallback on platforms without that source is not suitable for security-sensitive identifiers.
- `json_get_raw` and `json_array_get_raw` return an empty string for missing/invalid lookups. Call `json_is_valid` before accessing untrusted JSON. The lightweight accessors are not a full typed JSON DOM. JSON builders that accept a raw encoded value expect that argument to already be valid JSON; prefer the typed `_string`, `_int`, `_float`, and `_bool` helpers for primitives.

- `std.validate` offers convenience checks only: email validation is deliberately approximate, and IPv4 validation rejects leading-zero octets. Always use a real protocol parser and application-specific policy for security-sensitive input.
- `std.ini` is intentionally small: it supports simple section/key-value files and full-line comments, but not multiline values, interpolation, or inline comments. A missing key and an empty value both return an empty string; use `ini_has_key` to distinguish them.
- `std.terminal` emits ANSI escape sequences. Check `term_supports_color()` before styling output intended for logs, pipes, or files.
- `std.template` inserts values literally in a single pass, so placeholder-like text inside values is not expanded recursively. HTML escaping is for text/attribute contexts only; it is not a JavaScript, CSS, URL, or SQL sanitizer.
- `std.semver` validates SemVer identifiers and compares standard precedence. It accepts an optional leading `v`/`V` as a convenience extension. Core major/minor/patch values are limited to the runtime integer range; their extraction helpers return `-1` for invalid input. Build metadata does not affect precedence.
