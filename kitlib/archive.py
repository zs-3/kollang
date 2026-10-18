"""Package archives (``.kitpkg`` = gzip tar), checksums and safe extraction.

Packing is deterministic - sorted entries, zeroed timestamps and owners,
normalised modes, fixed gzip header - so repacking identical sources yields an
identical checksum.  Extraction never trusts the archive: only regular files
and directories are accepted, names must stay inside the package directory,
and sizes are capped to defuse archive bombs.  Kit never executes anything
from a package at install time.
"""
from __future__ import annotations

import fnmatch
import gzip
import hashlib
import io
import os
import shutil
import stat
import tarfile
import zlib
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

from .errors import KitError
from .manifest import MANIFEST_NAME, Manifest

ARCHIVE_EXT = ".kitpkg"
IGNORE_FILE = ".kitignore"

MAX_FILES = 5000
MAX_ARCHIVE_BYTES = 10 * 1024 * 1024        # compressed, enforced when publishing
MAX_UNPACKED_BYTES = 100 * 1024 * 1024      # total, enforced when unpacking
MAX_MEMBER_BYTES = 50 * 1024 * 1024

DEFAULT_EXCLUDED_DIRS = {".git", ".hg", ".svn", ".kit", "target", "__pycache__", ".idea", ".vscode"}
DEFAULT_EXCLUDED_FILES = ["*.kitpkg", "*.pyc", ".DS_Store", "*~", "*.swp", "*.swo", "kol.lock", IGNORE_FILE]
ALWAYS_INCLUDED = [MANIFEST_NAME, "README*", "LICENSE*", "LICENCE*", "COPYING*", "NOTICE*"]


@dataclass
class PackResult:
    path: str
    checksum: str       # hex sha256 of the archive bytes
    size: int
    files: List[str]    # relative posix paths included


# -- hashing -----------------------------------------------------------------

def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def tree_hash(directory: str, skip: Tuple[str, ...] = (".kit-meta.json",)) -> str:
    """Stable hash of a directory's files (names, exec bits and contents)."""
    h = hashlib.sha256()
    for rel in sorted(_walk_files(directory)):
        if rel in skip:
            continue
        full = os.path.join(directory, rel)
        mode = os.lstat(full).st_mode
        h.update(rel.encode("utf-8") + b"\0")
        h.update(b"x" if mode & stat.S_IXUSR else b"-")
        h.update(sha256_file(full).encode("ascii") + b"\n")
    return h.hexdigest()


def _walk_files(directory: str) -> List[str]:
    out: List[str] = []
    for dirpath, dirnames, filenames in os.walk(directory):
        dirnames.sort()
        for fn in filenames:
            full = os.path.join(dirpath, fn)
            out.append(os.path.relpath(full, directory).replace(os.sep, "/"))
    return out


# -- ignore patterns ---------------------------------------------------------

def _load_patterns(root: str, extra: List[str]) -> List[str]:
    pats = list(extra)
    path = os.path.join(root, IGNORE_FILE)
    if os.path.isfile(path):
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#"):
                    pats.append(line)
    return pats


def _matches(rel: str, is_dir: bool, pattern: str) -> bool:
    dir_only = pattern.endswith("/")
    pat = pattern.rstrip("/")
    if dir_only and not is_dir:
        # a file under an ignored directory is handled when the walker prunes
        return False
    anchored = pat.startswith("/") or "/" in pat
    pat = pat.lstrip("/")
    if anchored:
        return fnmatch.fnmatchcase(rel, pat)
    return any(fnmatch.fnmatchcase(part, pat) for part in rel.split("/"))


def _is_always_included(rel: str) -> bool:
    base = rel.split("/")[-1]
    return "/" not in rel and any(fnmatch.fnmatchcase(base, p) for p in ALWAYS_INCLUDED)


def collect_files(root: str, manifest: Optional[Manifest] = None) -> List[str]:
    """Relative posix paths that belong in the package archive, sorted."""
    exclude = list(manifest.exclude) if manifest else []
    include = list(manifest.include) if manifest else []
    patterns = _load_patterns(root, exclude)
    found: List[str] = []
    for dirpath, dirnames, filenames in os.walk(root):
        rel_dir = os.path.relpath(dirpath, root).replace(os.sep, "/")
        rel_dir = "" if rel_dir == "." else rel_dir
        kept = []
        for d in sorted(dirnames):
            rel = f"{rel_dir}/{d}" if rel_dir else d
            full = os.path.join(dirpath, d)
            if os.path.islink(full):
                raise KitError(f"symlink '{rel}' cannot be packaged",
                               hint="replace it with a real file/directory or add it to `exclude` in kol.toml")
            if d in DEFAULT_EXCLUDED_DIRS or any(_matches(rel, True, p) for p in patterns):
                continue
            kept.append(d)
        dirnames[:] = kept
        for fn in sorted(filenames):
            rel = f"{rel_dir}/{fn}" if rel_dir else fn
            full = os.path.join(dirpath, fn)
            if os.path.islink(full):
                raise KitError(f"symlink '{rel}' cannot be packaged",
                               hint="replace it with a real file or add it to `exclude` in kol.toml")
            if not os.path.isfile(full):
                continue
            if _is_always_included(rel):
                found.append(rel)
                continue
            if any(fnmatch.fnmatchcase(fn, p) for p in DEFAULT_EXCLUDED_FILES):
                continue
            if any(_matches(rel, False, p) for p in patterns):
                continue
            if include and not any(_matches(rel, False, p) for p in include):
                continue
            found.append(rel)
    found = sorted(set(found))
    if len(found) > MAX_FILES:
        raise KitError(f"package has {len(found)} files; the limit is {MAX_FILES}",
                       hint="exclude generated files with `exclude` in kol.toml or a .kitignore file")
    return found


# -- packing -----------------------------------------------------------------

def archive_basename(name: str, version: str) -> str:
    return f"{name}-{version}"


def pack(root: str, manifest: Manifest, dest: str, files: Optional[List[str]] = None) -> PackResult:
    """Write a deterministic archive of ``root`` to ``dest``."""
    files = files if files is not None else collect_files(root, manifest)
    top = archive_basename(manifest.name, str(manifest.version))
    raw = io.BytesIO()
    with gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0, compresslevel=9) as gz:
        with tarfile.open(fileobj=gz, mode="w", format=tarfile.PAX_FORMAT) as tar:
            for rel in files:
                full = os.path.join(root, rel)
                st = os.stat(full)
                info = tarfile.TarInfo(f"{top}/{rel}")
                info.size = st.st_size
                info.mtime = 0
                info.uid = info.gid = 0
                info.uname = info.gname = ""
                info.mode = 0o755 if st.st_mode & stat.S_IXUSR else 0o644
                with open(full, "rb") as f:
                    tar.addfile(info, f)
    data = raw.getvalue()
    os.makedirs(os.path.dirname(os.path.abspath(dest)), exist_ok=True)
    with open(dest, "wb") as out:
        out.write(data)
    return PackResult(dest, sha256_bytes(data), len(data), files)


# -- reading a package's manifest without unpacking it -------------------------

MAX_MANIFEST_BYTES = 1024 * 1024


def read_manifest(archive_path: str):
    """Parse the ``kol.toml`` inside a ``.kitpkg`` without extracting anything.

    The archive must have the layout ``<name>-<version>/kol.toml`` that
    ``kit pack`` produces; a mismatch between that folder name and the manifest
    is rejected so an archive cannot pretend to be another package."""
    from . import manifest as manifest_mod
    found = None
    try:
        with tarfile.open(archive_path, mode="r:gz") as tar:
            for member in tar:
                parts = member.name.split("/")
                if len(parts) == 2 and parts[1] == manifest_mod.MANIFEST_NAME and member.isreg():
                    if member.size > MAX_MANIFEST_BYTES:
                        raise KitError(f"{archive_path}: {manifest_mod.MANIFEST_NAME} is unreasonably large")
                    handle = tar.extractfile(member)
                    found = (parts[0], handle.read().decode("utf-8") if handle else "")
                    break
    except (tarfile.TarError, EOFError, gzip.BadGzipFile, zlib.error, UnicodeDecodeError, OSError) as e:
        raise KitError(f"{archive_path} is not a valid Kit package archive ({e.__class__.__name__}: {e})",
                       hint="create packages with `kit pack`; they are gzip-compressed tar files (.kitpkg)")
    if found is None:
        raise KitError(f"{archive_path} is not a Kit package: no <name>-<version>/{manifest_mod.MANIFEST_NAME} inside",
                       hint="create packages with `kit pack`")
    top, text = found
    m = manifest_mod.parse(text, os.path.join(os.path.dirname(os.path.abspath(archive_path)), top, manifest_mod.MANIFEST_NAME))
    expected_top = archive_basename(m.name, str(m.version))
    if top != expected_top:
        raise KitError(f"{archive_path}: folder '{top}' does not match its manifest ({expected_top})",
                       hint="the archive was not created by `kit pack`")
    return m


# -- unpacking ---------------------------------------------------------------

def _safe_member_path(name: str, top: str) -> str:
    if not name or "\\" in name or name.startswith("/") or "\x00" in name:
        raise KitError(f"unsafe path in archive: {name!r}")
    parts = name.split("/")
    if parts[-1] == "":          # directory entries may end with a slash
        parts = parts[:-1]
    if not parts or any(p in ("", ".", "..") for p in parts):
        raise KitError(f"unsafe path in archive: {name!r}")
    if parts[0] != top:
        raise KitError(f"archive entry '{name}' is outside the package directory '{top}/'")
    return "/".join(p for p in parts[1:] if p)


def unpack(archive_path: str, dest: str, name: str, version: str) -> List[str]:
    """Safely extract ``archive_path`` into the (new, empty) directory ``dest``."""
    top = archive_basename(name, version)
    extracted: List[str] = []
    total = 0
    count = 0
    os.makedirs(dest, exist_ok=True)
    try:
        with tarfile.open(archive_path, mode="r:gz") as tar:
            for member in tar:
                count += 1
                if count > MAX_FILES * 2:
                    raise KitError("archive contains too many entries")
                rel = _safe_member_path(member.name, top)
                if member.isdir():
                    if rel:
                        os.makedirs(os.path.join(dest, rel), exist_ok=True)
                    continue
                if not member.isreg():
                    raise KitError(
                        f"archive entry '{member.name}' is not a regular file or directory "
                        f"(links and special files are not allowed)")
                if not rel:
                    raise KitError(f"unsafe path in archive: {member.name!r}")
                if member.size > MAX_MEMBER_BYTES:
                    raise KitError(f"archive entry '{member.name}' is too large")
                total += member.size
                if total > MAX_UNPACKED_BYTES:
                    raise KitError("archive expands to more than the allowed size")
                target = os.path.normpath(os.path.join(dest, rel))
                if not (target + os.sep).startswith(os.path.normpath(dest) + os.sep):
                    raise KitError(f"unsafe path in archive: {member.name!r}")
                os.makedirs(os.path.dirname(target), exist_ok=True)
                src = tar.extractfile(member)
                if src is None:
                    raise KitError(f"cannot read archive entry '{member.name}'")
                with open(target, "wb") as out:
                    shutil.copyfileobj(src, out)
                os.chmod(target, 0o755 if member.mode & stat.S_IXUSR else 0o644)
                extracted.append(rel)
    except (tarfile.TarError, EOFError, gzip.BadGzipFile, zlib.error) as e:
        raise KitError(f"corrupt package archive {archive_path}: {e}")
    except OSError as e:
        raise KitError(f"cannot extract {archive_path}: {e}")
    return sorted(extracted)
