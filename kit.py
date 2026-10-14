#!/usr/bin/env python3
"""kit - the package manager and project tool for the Kol language."""
import os
import sys

# Resolve symlinks so `kit` works when installed as a link on PATH.
_here = os.path.dirname(os.path.realpath(__file__))
sys.path.insert(0, _here)

from kitlib.cli import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
