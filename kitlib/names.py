"""Package-name rules.

A package name is also the first segment of ``use <name>.<module>`` in Kol
source, so it has to lex as a plain Kol identifier and must not collide with
language keywords, built-in type names or the standard library.
"""
from __future__ import annotations

import re
from typing import Optional

_NAME_RE = re.compile(r"^[a-z][a-z0-9_]*$")
MAX_NAME_LEN = 64

# Names that would be confusing or ambiguous next to the language itself.
RESERVED = {
    "std", "kol", "kit", "core", "main", "test", "tests", "src", "lib",
    "self", "super", "int", "float", "str", "bool", "list", "map", "set",
    "void", "any", "none",
}


def _keywords() -> set:
    try:
        from compiler.lexer import KEYWORDS
        return {k.lower() for k in KEYWORDS}
    except Exception:  # compiler not importable (should not happen)
        return set()


def check_name(name: str) -> Optional[str]:
    """Return a human-readable problem with ``name`` or None if it is valid."""
    if not name:
        return "package name is empty"
    if len(name) > MAX_NAME_LEN:
        return f"package name is longer than {MAX_NAME_LEN} characters"
    if not _NAME_RE.match(name):
        return (f"invalid package name '{name}': use lowercase letters, digits "
                f"and underscores, starting with a letter (it must work as an "
                f"identifier in `use {name}`)")
    if name.endswith("_") or "__" in name:
        return f"invalid package name '{name}': no trailing or doubled underscores"
    if name in RESERVED:
        return f"'{name}' is a reserved name"
    if name in _keywords():
        return f"'{name}' is a Kol keyword and cannot be a package name"
    return None


def sanitize(raw: str) -> str:
    """Best-effort package name from a directory name (``My-App`` -> ``my_app``)."""
    s = re.sub(r"[^a-z0-9]+", "_", raw.lower()).strip("_")
    s = re.sub(r"_+", "_", s)
    if not s:
        s = "app"
    if not s[0].isalpha():
        s = "pkg_" + s
    return s[:MAX_NAME_LEN].rstrip("_") or "app"


def check_module_segment(seg: str) -> bool:
    """A single dotted segment after the package name in a ``use`` path."""
    return bool(re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", seg))
