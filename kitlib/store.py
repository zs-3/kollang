"""The package store: verified, unpacked registry packages.

    $KIT_HOME/store/<name>-<version>-<checksum[:12]>/

The directory name includes part of the archive checksum, so two different
builds of the same version can never share a directory.  Installation is
atomic (unpack to a temp dir, then rename) and verified: the archive must hash
to the checksum recorded in the registry index, and the unpacked ``kol.toml``
must agree with the package name and version we asked for.
"""
from __future__ import annotations

import json
import os
import shutil
import tempfile
from typing import Dict, List, Optional

from . import archive, manifest as manifest_mod, paths
from .errors import KitError
from .registry import IndexEntry, Registry
from .semver import Version

META_FILE = ".kit-meta.json"


def package_dir(name: str, version: Version, checksum: str) -> str:
    return os.path.join(paths.store_dir(), f"{name}-{version}-{checksum[:12]}")


def is_installed(name: str, version: Version, checksum: str) -> bool:
    d = package_dir(name, version, checksum)
    return os.path.isfile(os.path.join(d, META_FILE))


def install(registry: Registry, entry: IndexEntry, expected_checksum: Optional[str] = None) -> str:
    """Make sure ``entry`` is unpacked in the store; return its directory."""
    checksum = entry.checksum
    if expected_checksum and expected_checksum != checksum:
        raise KitError(
            f"checksum mismatch for {entry.name} v{entry.version}\n"
            f"  kol.lock expects  sha256:{expected_checksum}\n"
            f"  registry now has  sha256:{checksum}",
            hint="the package contents changed after it was locked; this can mean tampering. "
                 "If you trust the new contents, delete the entry from kol.lock (or run `kit update "
                 f"{entry.name}`)")
    dest = package_dir(entry.name, entry.version, checksum)
    if is_installed(entry.name, entry.version, checksum):
        return dest

    with paths.home_lock():
        if is_installed(entry.name, entry.version, checksum):
            return dest
        archive_path = registry.fetch(entry)
        actual = archive.sha256_file(archive_path)
        if actual != checksum:
            raise KitError(
                f"checksum mismatch for {entry.name} v{entry.version}: the archive does not match "
                f"the registry index\n  index:   sha256:{checksum}\n  archive: sha256:{actual}",
                hint=f"the archive at {archive_path} is corrupt or was modified; refusing to install it")
        os.makedirs(paths.store_dir(), exist_ok=True)
        tmp = tempfile.mkdtemp(prefix=".unpack-", dir=paths.store_dir())
        try:
            files = archive.unpack(archive_path, tmp, entry.name, str(entry.version))
            mpath = os.path.join(tmp, manifest_mod.MANIFEST_NAME)
            if not os.path.isfile(mpath):
                raise KitError(f"{entry.name} v{entry.version} has no {manifest_mod.MANIFEST_NAME} inside its archive")
            m = manifest_mod.load(tmp)
            if m.name != entry.name or m.version != entry.version:
                raise KitError(
                    f"archive identity mismatch: the registry says {entry.name} v{entry.version} "
                    f"but the archive's kol.toml says {m.name} v{m.version}")
            paths.write_json(os.path.join(tmp, META_FILE), {
                "name": entry.name, "version": str(entry.version),
                "checksum": checksum, "registry": entry.registry, "files": len(files),
                "tree": archive.tree_hash(tmp),
            })
            if os.path.exists(dest):
                shutil.rmtree(dest)
            os.replace(tmp, dest)
        except BaseException:
            shutil.rmtree(tmp, ignore_errors=True)
            raise
    return dest


def verify(directory: str) -> Optional[str]:
    """Return a problem description for a store directory, or None if it is intact."""
    meta = paths.read_json(os.path.join(directory, META_FILE))
    if not meta:
        return "missing metadata"
    if not os.path.isfile(os.path.join(directory, manifest_mod.MANIFEST_NAME)):
        return f"missing {manifest_mod.MANIFEST_NAME}"
    want = meta.get("tree")
    if want and archive.tree_hash(directory) != want:
        return "files were modified after installation"
    return None


def list_installed() -> List[dict]:
    out = []
    base = paths.store_dir()
    if not os.path.isdir(base):
        return out
    for entry in sorted(os.listdir(base)):
        d = os.path.join(base, entry)
        meta = paths.read_json(os.path.join(d, META_FILE))
        if os.path.isdir(d) and meta:
            size = sum(os.path.getsize(os.path.join(dp, f))
                       for dp, _dn, fs in os.walk(d) for f in fs)
            out.append({"dir": d, "size": size, **meta})
    return out


def remove(directory: str) -> None:
    shutil.rmtree(directory, ignore_errors=True)
