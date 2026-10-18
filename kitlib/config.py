"""Kit configuration: the ordered list of package registries.

``$KIT_HOME/config.toml``::

    [registries.local]
    path = "/home/me/.kit/registry"

    [registries.team]
    path = "/mnt/share/kol-registry"

Registries are searched in the order listed.  The first registry that contains
a package *name* owns that name: versions are never merged across registries,
which closes the "dependency confusion" hole where a higher version of an
internal package in a public registry would silently win.

``KIT_REGISTRY=/some/dir`` replaces the whole list with a single directory
registry (handy for CI and tests).
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass
from typing import List

from . import paths, tomlio
from .errors import KitError

_REG_NAME = re.compile(r"^[a-z][a-z0-9_-]{0,31}$")


@dataclass
class RegistryConfig:
    name: str
    path: str


def check_registry_name(name: str) -> None:
    if not _REG_NAME.match(name):
        raise KitError(f"invalid registry name '{name}'",
                       hint="use lowercase letters, digits, '-' and '_' (max 32 characters)")


def default_registries() -> List[RegistryConfig]:
    return [RegistryConfig("local", paths.default_registry_dir())]


def load_registries() -> List[RegistryConfig]:
    env = os.environ.get("KIT_REGISTRY")
    if env:
        return [RegistryConfig("env", os.path.abspath(os.path.expanduser(env)))]
    cfg = paths.config_file()
    if not os.path.isfile(cfg):
        return default_registries()
    with open(cfg, "r", encoding="utf-8") as f:
        text = f.read()
    try:
        data = tomlio.loads(text)
    except tomlio.TomlError as e:
        raise KitError(f"{cfg}: invalid TOML: {e}")
    table = data.get("registries")
    if table is None:
        return default_registries()
    if not isinstance(table, dict):
        raise KitError(f"{cfg}: [registries] must be a table")
    result: List[RegistryConfig] = []
    for name, spec in table.items():
        check_registry_name(name)
        if not isinstance(spec, dict):
            raise KitError(f"{cfg}: [registries.{name}] must be a table")
        if "url" in spec:
            raise KitError(
                f"{cfg}: registry '{name}' uses 'url', but online registries are not "
                f"supported by this offline version of Kit",
                hint="use a directory registry: path = \"/path/to/registry\"")
        path = spec.get("path")
        if not isinstance(path, str) or not path:
            raise KitError(f"{cfg}: [registries.{name}] needs a 'path'")
        result.append(RegistryConfig(name, os.path.abspath(os.path.expanduser(path))))
    return result or default_registries()


def save_registries(regs: List[RegistryConfig]) -> None:
    lines = [
        "# Kit configuration. Registries are searched in the order listed;",
        "# the first registry containing a package name owns that name.",
        "",
    ]
    for r in regs:
        lines.append(f"[registries.{r.name}]")
        lines.append(f"path = {tomlio.quote(r.path)}")
        lines.append("")
    paths.atomic_write(paths.config_file(), "\n".join(lines))
