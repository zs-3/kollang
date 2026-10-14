"""Edit ``kol.toml`` as text so comments, ordering and formatting survive.

Only ``[dependencies]`` / ``[dev-dependencies]`` entries are touched.
"""
from __future__ import annotations

import re
from typing import List, Optional, Tuple

from . import tomlio

_HEADER = re.compile(r"^\s*\[\s*([^\[\]]+?)\s*\]\s*(#.*)?$")


def _sections(lines: List[str]) -> List[Tuple[str, int]]:
    """(table name, line index of its header) for every ``[table]`` header."""
    out = []
    for i, line in enumerate(lines):
        if line.lstrip().startswith("[["):
            out.append(("[[" + line.strip().strip("[]").strip() + "]]", i))
            continue
        m = _HEADER.match(line)
        if m:
            out.append((m.group(1).strip(), i))
    return out


def _span(lines: List[str], table: str) -> Optional[Tuple[int, int]]:
    """Line range (header+1, end) of ``[table]``, or None."""
    secs = _sections(lines)
    for idx, (name, start) in enumerate(secs):
        if name == table:
            end = secs[idx + 1][1] if idx + 1 < len(secs) else len(lines)
            return start + 1, end
    return None


def format_dep(name: str, req: Optional[str], path: Optional[str]) -> str:
    if path is not None:
        if req:
            return f"{name} = {{ version = {tomlio.quote(req)}, path = {tomlio.quote(path)} }}"
        return f"{name} = {{ path = {tomlio.quote(path)} }}"
    return f"{name} = {tomlio.quote(req or '*')}"


def _key_of(line: str) -> Optional[str]:
    m = re.match(r'^\s*("?)([A-Za-z0-9_-]+)\1\s*=', line)
    return m.group(2) if m else None


def set_dependency(text: str, table: str, name: str, req: Optional[str], path: Optional[str]) -> str:
    """Add or replace ``name`` in ``[table]``; creates the table if needed."""
    lines = text.split("\n")
    new_line = format_dep(name, req, path)
    span = _span(lines, table)
    if span is None:
        while lines and lines[-1] == "":
            lines.pop()
        if lines:
            lines.append("")
        lines.append(f"[{table}]")
        lines.append(new_line)
        lines.append("")
        return "\n".join(lines)
    start, end = span
    for i in range(start, end):
        if _key_of(lines[i]) == name:
            lines[i] = new_line
            return "\n".join(lines)
    # insert after the last non-blank, non-comment-only line in the table
    insert_at = start
    for i in range(start, end):
        if lines[i].strip() and not lines[i].lstrip().startswith("#"):
            insert_at = i + 1
    # keep alphabetical order when the table already is sorted
    keys = [(_key_of(lines[i]), i) for i in range(start, end) if _key_of(lines[i])]
    names = [k for k, _ in keys]
    if names == sorted(names) and keys:
        for k, i in keys:
            if k > name:
                insert_at = i
                break
    lines.insert(insert_at, new_line)
    return "\n".join(lines)


def remove_dependency(text: str, table: str, name: str) -> Tuple[str, bool]:
    lines = text.split("\n")
    span = _span(lines, table)
    if span is None:
        return text, False
    start, end = span
    for i in range(start, end):
        if _key_of(lines[i]) == name:
            del lines[i]
            return "\n".join(lines), True
    return text, False


def tables_with(text: str, name: str) -> List[str]:
    """Which of the dependency tables currently contain ``name``."""
    lines = text.split("\n")
    found = []
    for table in ("dependencies", "dev-dependencies"):
        span = _span(lines, table)
        if span and any(_key_of(lines[i]) == name for i in range(*span)):
            found.append(table)
    return found


def set_version(text: str, new_version: str) -> str:
    """Rewrite ``version = "..."`` inside ``[package]``."""
    lines = text.split("\n")
    span = _span(lines, "package")
    if span is None:
        raise ValueError("no [package] table")
    start, end = span
    for i in range(start, end):
        if _key_of(lines[i]) == "version":
            comment = ""
            m = re.search(r'(\s+#.*)$', lines[i])
            if m:
                comment = m.group(1)
            lines[i] = f"version = {tomlio.quote(new_version)}{comment}"
            return "\n".join(lines)
    raise ValueError("no version key in [package]")
