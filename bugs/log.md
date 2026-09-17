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
