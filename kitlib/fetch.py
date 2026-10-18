"""Downloading package archives from URLs.

Rules, all enforced here:

* only ``http://`` and ``https://`` URLs; redirects are followed only to
  http(s), and a redirect from https to http is refused;
* a size cap (``MAX_DOWNLOAD_BYTES``) checked against Content-Length and while
  streaming, plus a connect/read timeout;
* every download is hashed; if a checksum is expected (from ``kol.toml`` or
  ``kol.lock``) a mismatch is an error and nothing is cached;
* the cache (``$KIT_HOME/downloads/<sha256>.kitpkg``) is keyed by content hash,
  so once a checksum is known the package is available offline, and a cached
  file is re-hashed before use.

Nothing downloaded is ever executed.
"""
from __future__ import annotations

import os
import re
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from typing import Optional, Tuple

from . import KIT_VERSION, archive, paths
from .errors import KitError
from .ui import console, human_size

MAX_DOWNLOAD_BYTES = 64 * 1024 * 1024
TIMEOUT_SECONDS = 30
_SHA_RE = re.compile(r"^[0-9a-f]{64}$")


def normalize_checksum(value: str) -> str:
    """``sha256:<hex>`` or bare ``<hex>`` -> lowercase hex; raises ValueError if malformed."""
    v = value.strip().lower()
    if v.startswith("sha256:"):
        v = v[len("sha256:"):]
    if not _SHA_RE.match(v):
        raise ValueError(f"'{value}' is not a sha256 checksum (expected 64 hex digits, optionally prefixed 'sha256:')")
    return v


def split_fragment(url: str) -> Tuple[str, Optional[str]]:
    """``https://x/y.kitpkg#sha256=<hex>`` -> (url without fragment, checksum or None)."""
    base, _, frag = url.partition("#")
    if not frag:
        return base, None
    key, _, value = frag.partition("=")
    if key.lower() != "sha256" or not value:
        raise KitError(f"unsupported URL fragment '#{frag}'", hint="use #sha256=<64 hex digits> or no fragment")
    try:
        return base, normalize_checksum(value)
    except ValueError as e:
        raise KitError(str(e))


def check_url(url: str) -> None:
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise KitError(f"unsupported package URL '{url}'", hint="only http:// and https:// URLs are supported")


class _SafeRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        target = newurl.strip().lower()
        if not target.startswith(("http://", "https://")):
            raise KitError(f"refusing to follow a redirect to a non-http URL ({newurl})")
        if req.full_url.lower().startswith("https://") and target.startswith("http://"):
            raise KitError(f"refusing a redirect from https to plain http ({newurl})")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _cached_path(sha: str) -> str:
    return os.path.join(paths.downloads_dir(), f"{sha}{archive.ARCHIVE_EXT}")


def _valid_cache(sha: str) -> Optional[str]:
    path = _cached_path(sha)
    if os.path.isfile(path):
        if archive.sha256_file(path) == sha:
            return path
        try:  # corrupt or tampered cache entry
            os.unlink(path)
        except OSError:
            pass
    return None


def download(url: str, expected: Optional[str] = None, expected_from: str = "kol.toml") -> Tuple[str, str]:
    """Return ``(local archive path, sha256 hex)`` for ``url``.

    With ``expected`` a valid cached copy is used without touching the network."""
    check_url(url)
    if expected:
        cached = _valid_cache(expected)
        if cached:
            console.detail(f"using cached download for {url}")
            return cached, expected

    if url.lower().startswith("http://") and not expected:
        console.warn(f"{url} is not https and has no checksum, so it cannot be verified",
                     hint="add `checksum = \"sha256:...\"` (or #sha256=... on the command line)")
    console.status("Downloading", url)
    opener = urllib.request.build_opener(_SafeRedirects)
    request = urllib.request.Request(url, headers={"User-Agent": f"kit/{KIT_VERSION}"})
    os.makedirs(paths.downloads_dir(), exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".dl-", dir=paths.downloads_dir())
    try:
        import hashlib
        digest = hashlib.sha256()
        size = 0
        try:
            with opener.open(request, timeout=TIMEOUT_SECONDS) as resp, os.fdopen(fd, "wb") as out:
                length = resp.headers.get("Content-Length")
                if length and length.isdigit() and int(length) > MAX_DOWNLOAD_BYTES:
                    raise KitError(f"{url} is {human_size(int(length))}; the limit is {human_size(MAX_DOWNLOAD_BYTES)}")
                while True:
                    chunk = resp.read(1 << 16)
                    if not chunk:
                        break
                    size += len(chunk)
                    if size > MAX_DOWNLOAD_BYTES:
                        raise KitError(f"download of {url} exceeded the {human_size(MAX_DOWNLOAD_BYTES)} limit")
                    digest.update(chunk)
                    out.write(chunk)
        except urllib.error.HTTPError as e:
            raise KitError(f"could not download {url}: HTTP {e.code} {e.reason}",
                           hint="check the URL; the package must be a .kitpkg archive")
        except urllib.error.URLError as e:
            raise KitError(f"could not download {url}: {e.reason}",
                           hint="check the URL and your network connection. Once a package is downloaded "
                                "with its checksum recorded, Kit installs it offline.")
        except (TimeoutError, OSError) as e:
            raise KitError(f"could not download {url}: {e}")
        sha = digest.hexdigest()
        if expected and sha != expected:
            raise KitError(
                f"checksum mismatch for {url}\n"
                f"  {expected_from} expects  sha256:{expected}\n"
                f"  downloaded file is      sha256:{sha}",
                hint="the file at this URL changed, or was tampered with. Nothing was installed. "
                     "If you trust the new file, update the checksum.")
        final = _cached_path(sha)
        os.replace(tmp, final)
        tmp = ""
        console.detail(f"downloaded {human_size(size)}, sha256:{sha}")
        return final, sha
    finally:
        if tmp:
            try:
                os.unlink(tmp)
            except OSError:
                pass
