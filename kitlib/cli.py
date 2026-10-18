"""Kit's command line: argument parsing, dispatch and error reporting."""
from __future__ import annotations

import argparse
import sys
import traceback
from typing import List, Optional

from . import KIT_VERSION
from .errors import KitError
from .ui import console

EXIT_INTERNAL = 101

EPILOG = """\
common commands:
  kit new <name>            create a project (--lib for a library)
  kit add <pkg>[@req]       add a dependency          (kit remove <pkg>)
  kit install               resolve + install everything in kol.toml
  kit run | build | test    compile and run your code
  kit publish               publish a library to a registry
  kit search <text>         find packages in your registries

Use `use <package>` (or `use <package>.<module>`) in Kol code to import
installed packages.  Run `kit <command> --help` for details.
"""


def _common() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(add_help=False)
    g = p.add_argument_group("output")
    g.add_argument("-q", "--quiet", action="store_true", default=argparse.SUPPRESS, help="only print errors and requested data")
    g.add_argument("-v", "--verbose", action="store_true", default=argparse.SUPPRESS, help="print extra detail")
    g.add_argument("--color", choices=["auto", "always", "never"], default=argparse.SUPPRESS, help="colour output (default: auto)")
    return p


def build_parser() -> argparse.ArgumentParser:
    common = _common()
    parser = argparse.ArgumentParser(
        prog="kit", parents=[common], formatter_class=argparse.RawDescriptionHelpFormatter,
        description="Kit - the package manager and project tool for Kol.", epilog=EPILOG)
    parser.add_argument("-V", "--version", action="store_true", help="print version and exit")
    sub = parser.add_subparsers(dest="command", metavar="<command>")

    def add(name: str, help_: str, **kw) -> argparse.ArgumentParser:
        return sub.add_parser(name, parents=[common], help=help_, description=help_, **kw)

    # -- project creation ----------------------------------------------------
    p = add("new", "create a new project in a new directory")
    p.add_argument("path")
    p.add_argument("--name", help="package name (default: directory name)")
    p.add_argument("--lib", action="store_true", help="create a library instead of an application")

    p = add("init", "turn the current directory into a project")
    p.add_argument("path", nargs="?", default=".")
    p.add_argument("--name")
    p.add_argument("--lib", action="store_true")

    # -- dependencies -----------------------------------------------------------
    p = add("add", "add dependencies to kol.toml and install them")
    p.add_argument("specs", nargs="*", metavar="PACKAGE[@REQ]|URL|FILE.kitpkg")
    p.add_argument("--dev", action="store_true", help="add to [dev-dependencies]")
    p.add_argument("--path", metavar="DIR", help="depend on a local package directory")

    p = add("remove", "remove dependencies", aliases=["rm"])
    p.add_argument("names", nargs="+", metavar="PACKAGE")

    p = add("install", "install dependencies (or add/globally install the given packages)", aliases=["i"])
    p.add_argument("specs", nargs="*", metavar="PACKAGE[@REQ]|URL|FILE.kitpkg")
    p.add_argument("-g", "--global", dest="global_", action="store_true", help="install for use in any Kol file, outside projects")
    p.add_argument("--frozen", "--locked", dest="frozen", action="store_true", help="fail if kol.lock would change (for CI)")

    p = add("update", "update dependencies to the newest allowed versions", aliases=["up"])
    p.add_argument("names", nargs="*", metavar="PACKAGE")

    # -- inspection ------------------------------------------------------------------
    p = add("list", "list resolved packages", aliases=["ls"])
    p.add_argument("--json", action="store_true")

    p = add("tree", "show the dependency tree")
    p.add_argument("--depth", type=int)
    p.add_argument("--json", action="store_true")

    p = add("why", "explain why a package is in the dependency graph")
    p.add_argument("name")

    p = add("search", "search the registries")
    p.add_argument("query", nargs="?", default="")
    p.add_argument("--limit", type=int, default=25)

    p = add("info", "show details of a registry package")
    p.add_argument("spec", metavar="PACKAGE[@REQ]")
    p.add_argument("--json", action="store_true")

    # -- build -------------------------------------------------------------------------------
    cc_help = "C compiler for the generated code (default: gcc, or $KIT_CC)"
    p = add("build", "compile the project")
    p.add_argument("--release", action="store_true", help="optimised build")
    p.add_argument("--bin", metavar="NAME")
    p.add_argument("--all", action="store_true", help="build every binary")
    p.add_argument("--cc", choices=["gcc", "chibicc"], help=cc_help)

    p = add("run", "build and run a binary")
    p.add_argument("--release", action="store_true")
    p.add_argument("--bin", metavar="NAME")
    p.add_argument("--cc", choices=["gcc", "chibicc"], help=cc_help)
    p.add_argument("--jit", action="store_true", help="compile with chibicc and run from memory; nothing is written to disk")
    p.add_argument("args", nargs="*", help="arguments for the program (after --)")

    p = add("jit", "compile and run one .kol file from memory (chibicc)")
    p.add_argument("file")
    p.add_argument("args", nargs="*", help="arguments for the program (after --)")

    p = add("chibicc", "install and inspect the chibicc compiler (setup, status, path)")
    p.add_argument("action", choices=["setup", "status", "path"])
    p.add_argument("--force", action="store_true", help="with setup: rebuild even if up to date")

    add("check", "check the code without producing a binary")

    p = add("test", "run the tests in tests/")
    p.add_argument("filter", nargs="?", help="only run tests whose path contains this text")
    p.add_argument("--release", action="store_true")
    p.add_argument("--cc", choices=["gcc", "chibicc"], help=cc_help)

    add("clean", "remove the target/ directory")

    p = add("script", "run a [scripts] entry from kol.toml (no name: list them)")
    p.add_argument("name", nargs="?")
    p.add_argument("args", nargs="*")

    # -- publishing -------------------------------------------------------------------------------
    p = add("pack", "create the package archive without publishing", aliases=["package"])
    p.add_argument("--list", action="store_true", help="list the files in the archive")
    p.add_argument("--no-verify", action="store_true", help="skip the syntax check")

    p = add("publish", "publish this library to a registry")
    p.add_argument("--registry", metavar="NAME")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--no-verify", action="store_true")

    p = add("yank", "mark a published version as yanked (avoided by new resolutions)")
    p.add_argument("spec", metavar="PACKAGE@VERSION")
    p.add_argument("--undo", action="store_true")

    p = add("registry", "manage registries (list, add, remove, init)")
    p.add_argument("action", choices=["list", "add", "remove", "init"])
    p.add_argument("name", nargs="?")
    p.add_argument("path", nargs="?")
    p.add_argument("--first", action="store_true", help="with add: give the registry the highest priority")

    p = add("cache", "inspect and clean the package store")
    p.add_argument("action", choices=["list", "clean", "verify"])
    p.add_argument("--all", action="store_true", help="with clean: remove every cached package")
    p.add_argument("--dry-run", action="store_true")

    add("doctor", "diagnose the Kit installation and project")
    add("version", "print the Kit and Kol versions")
    return parser


def _dispatch(args) -> int:
    from . import cmd_build, cmd_project, cmd_registry
    table = {
        "new": cmd_project.cmd_new, "init": cmd_project.cmd_init,
        "add": cmd_project.cmd_add, "remove": cmd_project.cmd_remove, "rm": cmd_project.cmd_remove,
        "install": cmd_project.cmd_install, "i": cmd_project.cmd_install,
        "update": cmd_project.cmd_update, "up": cmd_project.cmd_update,
        "list": cmd_registry.cmd_list, "ls": cmd_registry.cmd_list,
        "tree": cmd_registry.cmd_tree, "why": cmd_registry.cmd_why,
        "search": cmd_registry.cmd_search, "info": cmd_registry.cmd_info,
        "build": cmd_build.cmd_build, "run": cmd_build.cmd_run, "jit": cmd_build.cmd_jit, "chibicc": cmd_build.cmd_chibicc, "check": cmd_build.cmd_check,
        "test": cmd_build.cmd_test, "clean": cmd_build.cmd_clean, "script": cmd_build.cmd_script,
        "pack": cmd_registry.cmd_pack, "package": cmd_registry.cmd_pack,
        "publish": cmd_registry.cmd_publish, "yank": cmd_registry.cmd_yank,
        "registry": cmd_registry.cmd_registry, "cache": cmd_registry.cmd_cache,
        "doctor": cmd_build.cmd_doctor, "version": cmd_build.cmd_version,
    }
    return table[args.command](args)


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    argv = list(sys.argv[1:] if argv is None else argv)
    # `kit run -- a b c`: everything after `--` belongs to the program/script
    passthrough: List[str] = []
    if "--" in argv:
        i = argv.index("--")
        argv, passthrough = argv[:i], argv[i + 1:]
    args = parser.parse_args(argv)
    console.configure(quiet=getattr(args, "quiet", False), verbose=getattr(args, "verbose", False),
                      color=getattr(args, "color", "auto"))
    if getattr(args, "version", False):
        print(f"kit {KIT_VERSION}")
        return 0
    if not args.command:
        parser.print_help()
        return 0
    if args.command in ("run", "script", "jit"):
        args.args = list(getattr(args, "args", [])) + passthrough
    elif passthrough:
        parser.error("unexpected arguments after '--'")
    try:
        return _dispatch(args)
    except KitError as e:
        console.error(e.message, hint=e.hint, notes=e.notes)
        return e.code
    except Exception as e:  # compiler errors surface here with their own formatting
        fmt = getattr(e, "format", None)
        if fmt is not None and e.__class__.__name__ == "KolError":
            print(fmt(), file=sys.stderr)
            return 1
        console.error(f"internal error: {e.__class__.__name__}: {e}",
                      hint="this is a bug in Kit; re-run with KIT_DEBUG=1 for a traceback")
        import os
        if os.environ.get("KIT_DEBUG"):
            traceback.print_exc()
        return EXIT_INTERNAL
    except KeyboardInterrupt:
        print("", file=sys.stderr)
        return 130
