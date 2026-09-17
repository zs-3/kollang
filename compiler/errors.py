# compiler/errors.py
from dataclasses import dataclass
from typing import Optional

@dataclass
class SourceLocation:
    filename: str
    line: int
    column: int

class KolError(Exception):
    def __init__(self, message: str, location: Optional[SourceLocation] = None, hint: Optional[str] = None):
        super().__init__(message)
        self.message = message
        self.location = location
        self.hint = hint

    def format(self, source_code: Optional[str] = None) -> str:
        loc_str = f" in {self.location.filename} line {self.location.line}:{self.location.column}" if self.location else ""
        res = f"Error{loc_str}:\n  {self.message}"
        if self.location and source_code:
            lines = source_code.splitlines()
            if 0 <= self.location.line - 1 < len(lines):
                line_str = lines[self.location.line - 1]
                res += f"\n    {line_str}"
                indent = " " * (self.location.column - 1)
                res += f"\n    {indent}^"
        if self.hint:
            res += f"\nHint: {self.hint}"
        return res
