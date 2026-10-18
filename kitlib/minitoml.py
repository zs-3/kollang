"""A small TOML reader used when the stdlib ``tomllib`` (Python 3.11+) is absent.

It covers what Kit's files need: comments, bare/quoted/dotted keys, tables,
arrays of tables, basic/literal/multi-line strings, integers, floats, booleans,
arrays and inline tables.  Date/time values are rejected (Kit never uses them).
The unit tests cross-check this parser against ``tomllib`` on every fixture.
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Tuple


class TomlError(ValueError):
    pass


_BARE_KEY = re.compile(r"[A-Za-z0-9_-]+")
_NUMBER = re.compile(
    r"[+-]?(?:inf|nan)"
    r"|0x[0-9A-Fa-f_]+|0o[0-7_]+|0b[01_]+"
    r"|[+-]?\d[\d_]*(?:\.\d[\d_]*)?(?:[eE][+-]?\d[\d_]*)?"
)
_DATE_LIKE = re.compile(r"\d{4}-\d{2}-\d{2}|\d{2}:\d{2}:\d{2}")
_ESCAPES = {"b": "\b", "t": "\t", "n": "\n", "f": "\f", "r": "\r", '"': '"', "\\": "\\"}


class _Parser:
    def __init__(self, text: str):
        self.s = text
        self.i = 0
        self.n = len(text)

    # -- helpers ----------------------------------------------------------
    def err(self, msg: str) -> TomlError:
        line = self.s.count("\n", 0, self.i) + 1
        return TomlError(f"line {line}: {msg}")

    def peek(self, k: int = 0) -> str:
        j = self.i + k
        return self.s[j] if j < self.n else ""

    def starts(self, lit: str) -> bool:
        return self.s.startswith(lit, self.i)

    def skip_ws(self) -> None:
        while self.i < self.n and self.s[self.i] in " \t":
            self.i += 1

    def skip_comment(self) -> None:
        if self.peek() == "#":
            while self.i < self.n and self.s[self.i] != "\n":
                self.i += 1

    def skip_blank(self) -> None:
        """Whitespace, newlines and comments between statements."""
        while self.i < self.n:
            c = self.s[self.i]
            if c in " \t\r\n":
                self.i += 1
            elif c == "#":
                self.skip_comment()
            else:
                break

    def end_of_line(self) -> None:
        self.skip_ws()
        self.skip_comment()
        if self.i < self.n and self.s[self.i] not in "\r\n":
            raise self.err("unexpected characters after value")

    # -- keys -------------------------------------------------------------
    def key_part(self) -> str:
        c = self.peek()
        if c == '"':
            return self.basic_string()
        if c == "'":
            return self.literal_string()
        m = _BARE_KEY.match(self.s, self.i)
        if not m:
            raise self.err("expected a key")
        self.i = m.end()
        return m.group(0)

    def key_path(self) -> List[str]:
        parts = [self.key_part()]
        while True:
            self.skip_ws()
            if self.peek() == ".":
                self.i += 1
                self.skip_ws()
                parts.append(self.key_part())
            else:
                return parts

    # -- strings ----------------------------------------------------------
    def basic_string(self) -> str:
        if self.starts('"""'):
            return self.multiline_basic()
        self.i += 1
        out: List[str] = []
        while True:
            if self.i >= self.n or self.s[self.i] == "\n":
                raise self.err("unterminated string")
            c = self.s[self.i]
            if c == '"':
                self.i += 1
                return "".join(out)
            if c == "\\":
                out.append(self.escape())
            else:
                out.append(c)
                self.i += 1

    def escape(self) -> str:
        self.i += 1  # backslash
        c = self.peek()
        if c in _ESCAPES:
            self.i += 1
            return _ESCAPES[c]
        if c in "uU":
            width = 4 if c == "u" else 8
            digits = self.s[self.i + 1:self.i + 1 + width]
            if len(digits) != width or not re.fullmatch(r"[0-9A-Fa-f]+", digits):
                raise self.err("invalid unicode escape")
            self.i += 1 + width
            try:
                return chr(int(digits, 16))
            except ValueError:
                raise self.err("invalid unicode escape")
        raise self.err(f"invalid escape '\\{c}'")

    def multiline_basic(self) -> str:
        self.i += 3
        if self.peek() == "\r" and self.peek(1) == "\n":
            self.i += 2
        elif self.peek() == "\n":
            self.i += 1
        out: List[str] = []
        while True:
            if self.i >= self.n:
                raise self.err("unterminated multi-line string")
            if self.starts('"""'):
                # up to two quote characters may directly precede the closer
                run = self._extra_quotes()
                if run > 5:
                    raise self.err("too many quotes in multi-line string")
                out.append('"' * (run - 3))
                self.i += run
                return "".join(out)
            c = self.s[self.i]
            if c == "\\":
                # line-ending backslash: swallow following whitespace/newlines
                j = self.i + 1
                while j < self.n and self.s[j] in " \t":
                    j += 1
                if j < self.n and self.s[j] in "\r\n":
                    while j < self.n and self.s[j] in " \t\r\n":
                        j += 1
                    self.i = j
                    continue
                out.append(self.escape())
            else:
                out.append(c)
                self.i += 1

    def _extra_quotes(self) -> int:
        j, k = self.i, 0
        while j < self.n and self.s[j] == '"':
            j += 1
            k += 1
        return k

    def literal_string(self) -> str:
        if self.starts("'''"):
            self.i += 3
            if self.peek() == "\r" and self.peek(1) == "\n":
                self.i += 2
            elif self.peek() == "\n":
                self.i += 1
            end = self.s.find("'''", self.i)
            if end < 0:
                raise self.err("unterminated multi-line literal string")
            text = self.s[self.i:end]
            self.i = end + 3
            return text
        self.i += 1
        end = self.i
        while end < self.n and self.s[end] not in "'\n":
            end += 1
        if end >= self.n or self.s[end] != "'":
            raise self.err("unterminated literal string")
        text = self.s[self.i:end]
        self.i = end + 1
        return text

    # -- values -----------------------------------------------------------
    def value(self) -> Any:
        c = self.peek()
        if c == '"':
            return self.basic_string()
        if c == "'":
            return self.literal_string()
        if c == "[":
            return self.array()
        if c == "{":
            return self.inline_table()
        for lit, val in (("true", True), ("false", False)):
            if self.starts(lit) and not re.match(r"[A-Za-z0-9_-]", self.peek(len(lit))):
                self.i += len(lit)
                return val
        if _DATE_LIKE.match(self.s, self.i):
            raise self.err("date/time values are not supported by Kit")
        m = _NUMBER.match(self.s, self.i)
        if m:
            text = m.group(0)
            self.i = m.end()
            clean = text.replace("_", "")
            low = clean.lstrip("+-")
            if low in ("inf", "nan"):
                return float(clean)
            if low[:2] in ("0x", "0o", "0b"):
                return int(clean, 0)
            if re.fullmatch(r"[+-]?\d+", clean):
                return int(clean)
            return float(clean)
        raise self.err("invalid value")

    def array(self) -> List[Any]:
        self.i += 1
        items: List[Any] = []
        while True:
            self.skip_blank()
            if self.peek() == "]":
                self.i += 1
                return items
            items.append(self.value())
            self.skip_blank()
            if self.peek() == ",":
                self.i += 1
            elif self.peek() == "]":
                continue
            else:
                raise self.err("expected ',' or ']' in array")

    def inline_table(self) -> Dict[str, Any]:
        self.i += 1
        table: Dict[str, Any] = {}
        self.skip_ws()
        if self.peek() == "}":
            self.i += 1
            return table
        while True:
            self.skip_ws()
            path = self.key_path()
            self.skip_ws()
            if self.peek() != "=":
                raise self.err("expected '=' in inline table")
            self.i += 1
            self.skip_ws()
            val = self.value()
            self.assign(table, path, val)
            self.skip_ws()
            if self.peek() == ",":
                self.i += 1
                continue
            if self.peek() == "}":
                self.i += 1
                return table
            raise self.err("expected ',' or '}' in inline table")

    # -- structure --------------------------------------------------------
    def assign(self, table: Dict[str, Any], path: List[str], val: Any) -> None:
        node = table
        for part in path[:-1]:
            nxt = node.setdefault(part, {})
            if isinstance(nxt, list):
                nxt = nxt[-1]
            if not isinstance(nxt, dict):
                raise self.err(f"key '{part}' is not a table")
            node = nxt
        if path[-1] in node:
            raise self.err(f"duplicate key '{path[-1]}'")
        node[path[-1]] = val

    def descend(self, root: Dict[str, Any], path: List[str]) -> Dict[str, Any]:
        node = root
        for part in path:
            nxt = node.setdefault(part, {})
            if isinstance(nxt, list):
                if not nxt:
                    raise self.err(f"'{part}' is an empty array")
                nxt = nxt[-1]
            if not isinstance(nxt, dict):
                raise self.err(f"'{part}' is not a table")
            node = nxt
        return node

    def parse(self) -> Dict[str, Any]:
        root: Dict[str, Any] = {}
        current = root
        defined: set = set()
        while True:
            self.skip_blank()
            if self.i >= self.n:
                return root
            if self.peek() == "[":
                is_array = self.peek(1) == "["
                self.i += 2 if is_array else 1
                self.skip_ws()
                path = self.key_path()
                self.skip_ws()
                closer = "]]" if is_array else "]"
                if not self.starts(closer):
                    raise self.err(f"expected '{closer}'")
                self.i += len(closer)
                self.end_of_line()
                if is_array:
                    parent = self.descend(root, path[:-1])
                    arr = parent.setdefault(path[-1], [])
                    if not isinstance(arr, list):
                        raise self.err(f"'{path[-1]}' is not an array of tables")
                    new: Dict[str, Any] = {}
                    arr.append(new)
                    current = new
                else:
                    key = tuple(path)
                    if key in defined:
                        raise self.err(f"table [{'.'.join(path)}] defined twice")
                    defined.add(key)
                    current = self.descend(root, path)
            else:
                path = self.key_path()
                self.skip_ws()
                if self.peek() != "=":
                    raise self.err("expected '=' after key")
                self.i += 1
                self.skip_ws()
                val = self.value()
                self.end_of_line()
                self.assign(current, path, val)


def loads(text: str) -> Dict[str, Any]:
    return _Parser(text).parse()
