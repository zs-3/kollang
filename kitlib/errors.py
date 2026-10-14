"""Error type shared by every Kit module."""
from __future__ import annotations

from typing import List, Optional


class KitError(Exception):
    """A user-facing failure: printed as ``error: ...`` plus optional hints.

    Anything that is *not* a KitError is a bug in Kit and is reported with a
    traceback hint instead.
    """

    def __init__(self, message: str, hint: Optional[str] = None,
                 notes: Optional[List[str]] = None, code: int = 1):
        super().__init__(message)
        self.message = message
        self.hint = hint
        self.notes = notes or []
        self.code = code
