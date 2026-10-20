# Unreleased — standard library expansion

## Added

- `std.json`: JSON validation, string quoting/unquoting, raw object-field and array-element access, scalar getters, typed string/int/float/bool object and array builders, string-array accessors, and fallback-friendly getters.
- `std.regex`: POSIX extended regular-expression matching, find and replacement helpers.
- `std.csv`: CSV row/document parsing and encoding with quoted fields and escaped quotes.
- `std.url`: query encoding/decoding, scheme/host/hostname/port/path/query/fragment helpers, repeated query-value retrieval, and query append/remove/set operations that preserve fragments.
- `std.uuid` and `std.crypto`: UUID v4 plus SHA-256 and MD5 text digests.
- `std.datetime`, `std.cli`, `std.args`, `std.test`, `std.fs`, `std.process`, and `std.log`.
- `std.net`: synchronous TCP connect/listen/accept/send/receive/close, DNS lookup, and UDP bind/send/receive helpers.
- `std.http`: plain HTTP GET, response status/header/body helpers, response construction and server primitives.
- Backtick raw string literals and runtime command-line argument access (`args`, `arg_count`, `arg_at`).
- Compile-time `PLATFORM`/`ARCH` selection in `when` blocks, including `and`, `or`, and `not`; target mapping now follows `--target` during code generation.
- String methods `.index_of()` and `.last_index_of()`.
- Strict Base64 decoding: rejects malformed padding and non-canonical padding bits; added Base64URL encoding/decoding with unpadded support.
- Regression coverage for JSON typed builders, Base64URL edge cases, repeated URL query keys, query appending/removal/replacement, fragment preservation, and CSV quoted/multiline document round trips.
- Fixed nested scalar-array element sizing, literal inference, copy-on-push, and recursive cleanup in the C code generator/runtime. Added nested integer/float/bool array regression coverage.
- TCP/HTTP integration tests, argument/raw-string tests, target-mapping tests and expanded standard-library smoke tests.

## Fixed / improved

- `kol run file.kol arg1 arg2` now forwards arguments to the compiled program.
- Release builds no longer force `-march=native`, which could select instructions for the build host during cross-compilation.
- TCP connect timeouts now use nonblocking connect plus `poll` rather than relying on socket I/O timeouts to limit `connect()`.
- Added cleanup for temporary strings in the new JSON/HTTP helpers and stricter directory-creation checks.

## Limitations

- Regex is POSIX ERE, not PCRE, and has no linear-time guarantee.
- HTTP is plain HTTP/1.x only; TLS, redirects and chunked transfer decoding are not implemented.
- Networking is synchronous. Socket handles must be closed explicitly; UDP payloads and KOL strings are text-oriented and cannot preserve embedded NUL bytes.
- Process commands use the system shell and must not receive untrusted input.
- JSON helpers are lightweight accessors, not a full DOM; validate input first.
- The full legacy test suite and full debug-plus-release standard-library matrix did not finish within the available execution window. In this work session, all 23 standard-library test files passed in debug mode; eight focused standard-library tests passed in release mode. The broader compiler test runner also timed out, so its full result remains unverified.
