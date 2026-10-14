"""Semantic versions and version requirements (Cargo-style syntax).

Versions follow semver 2.0.0 precedence rules.  Requirements accept:

    1.2.3  ^1.2.3   caret (a bare version means caret)
    ~1.2   =1.2.3   >=1.0, <2.0   >1  <=2.1
    *  1.*  1.2.x   wildcards

Pre-release versions only satisfy a requirement that itself names a
pre-release of the same major.minor.patch (so ``^1.0`` never silently picks
``2.0.0-beta`` or ``1.5.0-rc.1``).
"""
from __future__ import annotations

import re
from typing import Iterable, List, Optional, Tuple

_IDENT = r"[0-9A-Za-z-]+"
_VERSION_RE = re.compile(
    r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)"
    rf"(?:-({_IDENT}(?:\.{_IDENT})*))?"
    rf"(?:\+({_IDENT}(?:\.{_IDENT})*))?$"
)


class VersionError(ValueError):
    pass


class Version:
    __slots__ = ("major", "minor", "patch", "pre", "build", "_key")

    def __init__(self, major: int, minor: int, patch: int,
                 pre: Tuple[str, ...] = (), build: Tuple[str, ...] = ()):
        self.major = major
        self.minor = minor
        self.patch = patch
        self.pre = tuple(pre)
        self.build = tuple(build)
        # Precedence key: a release sorts above any of its pre-releases.
        # Numeric pre-release identifiers sort below alphanumeric ones.
        pre_key = tuple(
            (0, int(p), "") if p.isdigit() else (1, 0, p) for p in self.pre
        )
        self._key = (major, minor, patch, 0 if self.pre else 1, pre_key)

    # -- parsing ---------------------------------------------------------
    @classmethod
    def parse(cls, text: str) -> "Version":
        text = text.strip()
        m = _VERSION_RE.match(text)
        if not m:
            raise VersionError(
                f"'{text}' is not a valid version (expected MAJOR.MINOR.PATCH, e.g. 1.4.0)"
            )
        pre = tuple(m.group(4).split(".")) if m.group(4) else ()
        for ident in pre:
            if ident.isdigit() and len(ident) > 1 and ident.startswith("0"):
                raise VersionError(
                    f"'{text}' is not a valid version (numeric pre-release "
                    f"identifier '{ident}' has a leading zero)"
                )
        build = tuple(m.group(5).split(".")) if m.group(5) else ()
        return cls(int(m.group(1)), int(m.group(2)), int(m.group(3)), pre, build)

    # -- ordering (build metadata is ignored, per the spec) ---------------
    def __eq__(self, other) -> bool:
        return isinstance(other, Version) and self._key == other._key

    def __lt__(self, other: "Version") -> bool:
        return self._key < other._key

    def __le__(self, other: "Version") -> bool:
        return self._key <= other._key

    def __gt__(self, other: "Version") -> bool:
        return self._key > other._key

    def __ge__(self, other: "Version") -> bool:
        return self._key >= other._key

    def __hash__(self) -> int:
        return hash(self._key)

    def __str__(self) -> str:
        s = f"{self.major}.{self.minor}.{self.patch}"
        if self.pre:
            s += "-" + ".".join(self.pre)
        if self.build:
            s += "+" + ".".join(self.build)
        return s

    def __repr__(self) -> str:
        return f"Version({self})"

    @property
    def is_prerelease(self) -> bool:
        return bool(self.pre)

    @property
    def core(self) -> "Version":
        """major.minor.patch without pre-release / build."""
        return Version(self.major, self.minor, self.patch)

    def triple(self) -> Tuple[int, int, int]:
        return (self.major, self.minor, self.patch)

    def bump(self, part: str) -> "Version":
        if part == "major":
            return Version(self.major + 1, 0, 0)
        if part == "minor":
            return Version(self.major, self.minor + 1, 0)
        if part == "patch":
            return Version(self.major, self.minor, self.patch + 1)
        raise VersionError(f"unknown version part '{part}'")


# ---------------------------------------------------------------------------
# Requirements
# ---------------------------------------------------------------------------

_PARTIAL_RE = re.compile(
    r"^(?P<major>\d+|[*xX])"
    r"(?:\.(?P<minor>\d+|[*xX])"
    r"(?:\.(?P<patch>\d+|[*xX])"
    rf"(?:-(?P<pre>{_IDENT}(?:\.{_IDENT})*))?"
    r")?)?$"
)


class _Partial:
    """A possibly incomplete version such as ``1``, ``1.2``, ``1.2.*``."""

    def __init__(self, major: Optional[int], minor: Optional[int],
                 patch: Optional[int], pre: Tuple[str, ...]):
        self.major = major
        self.minor = minor
        self.patch = patch
        self.pre = pre


def _parse_partial(text: str, whole: str) -> _Partial:
    m = _PARTIAL_RE.match(text)
    if not m:
        raise VersionError(
            f"invalid version requirement '{whole}': '{text}' is not a version "
            f"(examples: 1.2.3, 1.2, 1.*, ^0.4.1, ~2.0)"
        )

    def part(g: str) -> Optional[int]:
        v = m.group(g)
        if v is None or v in "*xX":
            return None
        return int(v)

    major, minor, patch = part("major"), part("minor"), part("patch")
    # A wildcard hides everything to its right: "1.*.3" is not meaningful.
    if major is None and (minor is not None or patch is not None):
        raise VersionError(f"invalid version requirement '{whole}': wildcard before a number")
    if minor is None and patch is not None:
        raise VersionError(f"invalid version requirement '{whole}': wildcard before a number")
    pre = tuple(m.group("pre").split(".")) if m.group("pre") else ()
    if pre and patch is None:
        raise VersionError(f"invalid version requirement '{whole}': pre-release needs a full version")
    return _Partial(major, minor, patch, pre)


# A normalised comparator: (operator, Version). Operators: = > >= < <=
_Cmp = Tuple[str, Version]


def _v(major: int, minor: int = 0, patch: int = 0, pre: Tuple[str, ...] = ()) -> Version:
    return Version(major, minor, patch, pre)


def _expand(op: str, p: _Partial) -> List[_Cmp]:
    """Translate one ``op partial`` into basic comparators."""
    ma, mi, pa = p.major, p.minor, p.patch

    if ma is None:  # "*"
        return []

    lower = _v(ma, mi or 0, pa or 0, p.pre)

    if op == "^":
        if ma > 0:
            upper = _v(ma + 1)
        elif mi is None:
            upper = _v(1)
        elif mi > 0:
            upper = _v(0, mi + 1)
        elif pa is None:
            upper = _v(0, 1)
        else:
            upper = _v(0, 0, pa + 1)
        return [(">=", lower), ("<", upper)]

    if op == "~":
        if mi is None:
            upper = _v(ma + 1)
        else:
            upper = _v(ma, mi + 1)
        return [(">=", lower), ("<", upper)]

    if op == "":  # wildcard form such as "1.*" or "1.2.x"
        if mi is None:
            upper = _v(ma + 1)
        elif pa is None:
            upper = _v(ma, mi + 1)
        else:
            return [("=", lower)]
        return [(">=", lower), ("<", upper)]

    if op == "=":
        if pa is not None:
            return [("=", lower)]
        if mi is not None:
            return [(">=", lower), ("<", _v(ma, mi + 1))]
        return [(">=", lower), ("<", _v(ma + 1))]

    if op == ">":
        if pa is not None:
            return [(">", lower)]
        if mi is not None:
            return [(">=", _v(ma, mi + 1))]
        return [(">=", _v(ma + 1))]

    if op == ">=":
        return [(">=", lower)]

    if op == "<":
        return [("<", lower)]

    if op == "<=":
        if pa is not None:
            return [("<=", lower)]
        if mi is not None:
            return [("<", _v(ma, mi + 1))]
        return [("<", _v(ma + 1))]

    raise VersionError(f"unknown operator '{op}'")


_OP_RE = re.compile(r"^(\^|~|=|>=|<=|>|<)?\s*(.*)$")


class Req:
    """A set of comparators that must all match (comma = AND)."""

    def __init__(self, text: str, comparators: List[_Cmp]):
        self.text = text
        self.comparators = comparators

    @classmethod
    def parse(cls, text: str) -> "Req":
        raw = text.strip()
        if not raw:
            raise VersionError("empty version requirement (use \"*\" to accept any version)")
        comparators: List[_Cmp] = []
        for piece in raw.split(","):
            piece = piece.strip()
            if not piece:
                raise VersionError(f"invalid version requirement '{raw}': empty comparator")
            m = _OP_RE.match(piece)
            op, rest = m.group(1) or "", m.group(2).strip()
            partial = _parse_partial(rest, raw)
            if op == "":
                # Bare version -> caret; bare wildcard form stays a wildcard.
                has_wild = ("*" in rest) or ("x" in rest.lower().split("-")[0].split("."))
                op = "" if has_wild else "^"
            comparators.extend(_expand(op, partial))
        return cls(raw, comparators)

    @classmethod
    def any(cls) -> "Req":
        return cls("*", [])

    @classmethod
    def caret(cls, v: Version) -> "Req":
        return cls.parse(f"^{v.core if not v.pre else v}")

    def matches(self, v: Version) -> bool:
        if v.pre:
            enabled = any(
                c.pre and c.triple() == v.triple() for _, c in self.comparators
            )
            if not enabled:
                return False
        for op, c in self.comparators:
            if op == "=":
                if not v == c:
                    return False
            elif op == ">":
                if not v > c:
                    return False
            elif op == ">=":
                if not v >= c:
                    return False
            elif op == "<":
                if not v < c:
                    return False
            elif op == "<=":
                if not v <= c:
                    return False
        return True

    def __str__(self) -> str:
        return self.text

    def __repr__(self) -> str:
        return f"Req({self.text!r})"

    def __eq__(self, other) -> bool:
        return isinstance(other, Req) and self.text == other.text

    def __hash__(self) -> int:
        return hash(self.text)


def parse_version(text: str) -> Version:
    return Version.parse(text)


def parse_req(text: str) -> Req:
    return Req.parse(text)


def latest(versions: Iterable[Version], req: Optional[Req] = None,
           allow_pre: bool = False) -> Optional[Version]:
    best: Optional[Version] = None
    for v in versions:
        if v.is_prerelease and not allow_pre and (req is None or not req.matches(v)):
            continue
        if req is not None and not req.matches(v):
            continue
        if best is None or v > best:
            best = v
    return best
