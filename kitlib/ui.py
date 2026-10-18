"""Terminal output: coloured status lines, warnings, errors.

Status/progress goes to stderr so that stdout stays clean for data
(``kit tree --json | jq`` etc.).  Colours follow NO_COLOR and --color.
"""
from __future__ import annotations

import os
import sys
from typing import Iterable, List, Optional

_RESET = "\033[0m"
_CODES = {
    "bold": "\033[1m",
    "dim": "\033[2m",
    "red": "\033[31m",
    "green": "\033[32m",
    "yellow": "\033[33m",
    "blue": "\033[34m",
    "cyan": "\033[36m",
}

STATUS_WIDTH = 12


class Console:
    def __init__(self) -> None:
        self.quiet = False
        self.verbose = False
        self._color_mode = "auto"  # auto | always | never

    # -- configuration -------------------------------------------------
    def configure(self, quiet: bool = False, verbose: bool = False,
                  color: str = "auto") -> None:
        self.quiet = quiet
        self.verbose = verbose
        self._color_mode = color

    def _use_color(self, stream) -> bool:
        if self._color_mode == "always":
            return True
        if self._color_mode == "never":
            return False
        if os.environ.get("NO_COLOR"):
            return False
        if os.environ.get("TERM") == "dumb":
            return False
        return hasattr(stream, "isatty") and stream.isatty()

    def paint(self, text: str, *styles: str, stream=None) -> str:
        stream = stream or sys.stderr
        if not self._use_color(stream) or not styles:
            return text
        prefix = "".join(_CODES[s] for s in styles)
        return f"{prefix}{text}{_RESET}"

    # -- output ----------------------------------------------------------
    def status(self, verb: str, message: str = "") -> None:
        """``   Installing greeter v1.2.0`` - verb right-aligned and green."""
        if self.quiet:
            return
        label = self.paint(verb.rjust(STATUS_WIDTH), "bold", "green")
        print(f"{label} {message}".rstrip(), file=sys.stderr)

    def info(self, message: str) -> None:
        if not self.quiet:
            print(message, file=sys.stderr)

    def detail(self, message: str) -> None:
        """Only shown with --verbose."""
        if self.verbose and not self.quiet:
            print(self.paint(message, "dim"), file=sys.stderr)

    def warn(self, message: str, hint: Optional[str] = None) -> None:
        if self.quiet:
            return
        tag = self.paint("warning:", "bold", "yellow")
        print(f"{tag} {message}", file=sys.stderr)
        if hint:
            print(f"  {self.paint('hint:', 'bold', 'cyan')} {hint}", file=sys.stderr)

    def error(self, message: str, hint: Optional[str] = None,
              notes: Optional[Iterable[str]] = None) -> None:
        tag = self.paint("error:", "bold", "red")
        lines = message.split("\n")
        print(f"{tag} {lines[0]}", file=sys.stderr)
        for extra in lines[1:]:
            print(f"       {extra}" if extra.strip() else "", file=sys.stderr)
        for note in notes or []:
            print(f"  {self.paint('note:', 'bold')} {note}", file=sys.stderr)
        if hint:
            print(f"  {self.paint('hint:', 'bold', 'cyan')} {hint}", file=sys.stderr)

    def out(self, text: str = "") -> None:
        """Data for the user / for pipes: stdout, never suppressed."""
        print(text)


console = Console()


# -- small formatting helpers -------------------------------------------

def human_size(n: int) -> str:
    size = float(n)
    for unit in ("B", "KiB", "MiB", "GiB"):
        if size < 1024 or unit == "GiB":
            return f"{int(size)} B" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{n} B"


def plural(n: int, word: str, plural_word: Optional[str] = None) -> str:
    if n == 1:
        return f"{n} {word}"
    return f"{n} {plural_word or word + 's'}"


def table(rows: List[List[str]], header: Optional[List[str]] = None) -> str:
    """Left-aligned plain-text table."""
    all_rows = ([header] if header else []) + rows
    if not all_rows:
        return ""
    widths = [max(len(r[i]) for r in all_rows) for i in range(len(all_rows[0]))]
    out = []
    for idx, row in enumerate(all_rows):
        out.append("  ".join(cell.ljust(widths[i]) for i, cell in enumerate(row)).rstrip())
        if header and idx == 0:
            out.append("  ".join("-" * w for w in widths))
    return "\n".join(out)
