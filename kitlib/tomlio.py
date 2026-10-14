"""TOML reading (stdlib ``tomllib`` or the bundled fallback) and writing helpers."""
from __future__ import annotations

import os
from typing import Any, Dict

from . import minitoml
from .minitoml import TomlError

try:  # Python 3.11+
    import tomllib as _tomllib
except ImportError:  # pragma: no cover - exercised on older interpreters
    _tomllib = None


def backend() -> str:
    if _tomllib is not None and not os.environ.get("KIT_FORCE_MINITOML"):
        return "tomllib"
    return "minitoml"


def loads(text: str) -> Dict[str, Any]:
    if backend() == "tomllib":
        try:
            return _tomllib.loads(text)
        except _tomllib.TOMLDecodeError as e:
            raise TomlError(str(e)) from None
    return minitoml.loads(text)


# ---------------------------------------------------------------------------
# Writing
# ---------------------------------------------------------------------------

def quote(s: str) -> str:
    """A TOML basic string literal."""
    out = ['"']
    for ch in s:
        if ch == '"':
            out.append('\\"')
        elif ch == "\\":
            out.append("\\\\")
        elif ch == "\n":
            out.append("\\n")
        elif ch == "\r":
            out.append("\\r")
        elif ch == "\t":
            out.append("\\t")
        elif ord(ch) < 0x20 or ord(ch) == 0x7F:
            out.append("\\u%04x" % ord(ch))
        else:
            out.append(ch)
    out.append('"')
    return "".join(out)


def dump_value(v: Any) -> str:
    """Serialise a scalar, list or (inline) dict."""
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, int):
        return str(v)
    if isinstance(v, float):
        return repr(v)
    if isinstance(v, str):
        return quote(v)
    if isinstance(v, (list, tuple)):
        return "[" + ", ".join(dump_value(x) for x in v) + "]"
    if isinstance(v, dict):
        if not v:
            return "{}"
        items = ", ".join(f"{dump_key(k)} = {dump_value(x)}" for k, x in v.items())
        return "{ " + items + " }"
    raise TypeError(f"cannot serialise {type(v).__name__} to TOML")


def dump_key(k: str) -> str:
    import re
    return k if re.fullmatch(r"[A-Za-z0-9_-]+", k) else quote(k)
