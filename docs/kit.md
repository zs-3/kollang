# Kit — the Kol package manager

Kit (`kit`) creates projects, installs libraries and builds them, so that
`use anylib` just works in Kol code.

This first version is **offline**: packages come from *directory registries*
on disk (by default `~/.kit/registry`). Online registries are the next step;
the registry layer is already an interface (`kitlib/registry.py`) so the
resolver, store, lockfile and compiler integration will not change.

```sh
./kit.py --help                               # or put it on your PATH:
ln -s "$PWD/kit.py" ~/.local/bin/kit
```

Requires Python 3.11+ (tested on 3.11, 3.12 and 3.13; an older-Python TOML
fallback parser is bundled and tested only by forcing it on 3.11+ with
`KIT_FORCE_MINITOML=1`) and a C compiler for building, same as `kol`.

## Five-minute tour

```sh
kit new --lib greeter && cd greeter     # a library: src/lib.kol, tests/
kit test                                # runs tests/*.kol
# edit kol.toml: add a description (publishing requires description + license)
kit publish                             # -> ~/.kit/registry

cd .. && kit new myapp && cd myapp
kit add greeter                         # edits kol.toml, writes kol.lock, installs
```

```kol
-- src/main.kol
use greeter
print(greeter_greet("Kit"))
```

```sh
kit run                                 # Hello, Kit!
```

A complete runnable pair lives in `examples/kit/` (`hello_app` depends on
`greeter` through a path dependency).

## Importing packages

| Code | Meaning |
|---|---|
| `use greeter` | the package's library (`src/lib.kol`) |
| `use greeter.polite` | module `src/polite.kol` (or `src/polite/lib.kol`) |
| `use greeter: (greeter_hello as hi)` | selective import with rename |
| `use std.math`, `use ./local` | unchanged: std library and relative files |

Resolution order for a non-relative path: file next to the importing file →
`std.*` → installed packages.

Rules (checked by the compiler, per importing file):

* A package may import **its own modules** and the packages **it declares** in
  `[dependencies]`. Transitive dependencies are private: if `app` uses `textx`
  and `textx` uses `mathx`, `app` cannot `use mathx` without adding it.
* `[dev-dependencies]` are visible only to `tests/`, `examples/`, `benches/`.
* Outside any project (a loose script), packages from `kit install -g` work.

### Two Kol limits that shape Kit

* **Imports are flat.** All functions share one namespace. By convention a
  library prefixes its public names with its package name
  (`greeter_hello`). If two imported modules still define the same name, the
  compiler now says so (`function 'x' is defined twice: also defined at
  file:line`) and you can rename one side: `use b: (x as x2)`.
* **One version per package.** Because of that, two versions of a package can
  never coexist in one program. The resolver picks exactly one version of
  each package, and reports a conflict (naming who requires what) when that
  is impossible, instead of silently duplicating.

## `kol.toml`

```toml
[package]
name = "greeter"            # lowercase letters, digits, _ ; must work as `use greeter`
version = "0.2.0"           # semver
description = "..."         # required to publish
license = "MIT"             # required to publish (or license-file)
authors = ["You <you@example.com>"]
keywords = ["text"]
kol = ">=0.1"               # compiler versions this package supports
exclude = ["notes/*"]       # extra files to leave out of the archive
publish = true              # false: `kit publish` refuses

[dependencies]
mathx = "^1.2"                                  # from a registry
local = { path = "../local" }                   # a directory, used in place
both  = { version = "1.0", path = "../both" }   # path now, version when published

[dev-dependencies]
testkit = "0.3"

[lib]                       # optional; default src/lib.kol if it exists
path = "src/lib.kol"

[[bin]]                     # optional; default src/main.kol, plus src/bin/*.kol
name = "tool"
path = "src/tool.kol"

[scripts]
lint = "kit check && kit test"     # `kit script lint`
```

Version requirements follow Cargo: `1.2.3` and `^1.2.3` (compatible),
`~1.2` (patch updates), `>=1.0, <2.0`, `=1.2.3`, `1.*`. Pre-releases are only
chosen when a requirement names one. Unknown keys produce a warning (with a
"did you mean" hint); `git`, `registry`, `optional`, `features` produce a clear
"not supported yet" error.

`.kitignore` (gitignore-style subset) and `exclude` control what is packaged.
`target/`, `.git/`, `.kit/`, `kol.lock` and editor junk are always excluded;
symlinks are refused.

## Commands

| Command | What it does |
|---|---|
| `kit new <name> [--lib]` / `kit init` | create a project / adopt a directory |
| `kit add <pkg>[@req] [--dev] [--path DIR]` | add dependency, resolve, install (rolls `kol.toml` back on failure) |
| `kit remove <pkg>` (`rm`) | remove a dependency |
| `kit install` (`i`) | resolve + install everything in `kol.toml` |
| `kit install <pkg>` | same as `add` inside a project; **global** install outside one |
| `kit install -g <pkg>` | install for use from any Kol file |
| `kit install --frozen` | CI mode: fail if `kol.lock` would change |
| `kit update [pkg…]` (`up`) | move to the newest allowed versions |
| `kit list`, `kit tree`, `kit why <pkg>` | inspect the resolved graph (`--json` available) |
| `kit search <text>`, `kit info <pkg>[@req]` | browse registries |
| `kit build [--release] [--bin n]`, `kit run`, `kit check`, `kit test [filter]`, `kit clean` | work on your code; output goes to `target/` |
| `kit script [name]` | run a `[scripts]` entry |
| `kit pack [--list]`, `kit publish [--registry n] [--dry-run]` | create the archive / publish it |
| `kit yank <pkg>@<ver> [--undo]` | hide a bad version from new resolutions |
| `kit registry list\|add\|remove\|init` | manage registries |
| `kit cache list\|clean\|verify` | inspect, prune (`--all`, `--dry-run`) and integrity-check the store |
| `kit doctor`, `kit version` | diagnostics |

Global flags: `-q/--quiet`, `-v/--verbose`, `--color auto|always|never`
(`NO_COLOR` is honoured). Status lines go to stderr, data (`list`, `tree`,
`info --json`) to stdout. Exit codes: `0` ok, `1` error, `2` usage, `101`
internal bug (`KIT_DEBUG=1` prints a traceback).

`kit test` compiles and runs every `.kol` file under `tests/`; a test passes
when it exits 0 and, if a `.expected` file sits beside it, prints exactly that.

## How it works

```
kol.toml ──resolve──▶ package set ──▶ kol.lock       (exact versions + sha256)
                          │
                          └──install──▶ ~/.kit/store/<name>-<ver>-<hash>/
                                           │
                                           └──link──▶ .kit/link.json ◀── read by the compiler
```

* **Resolver** (`kitlib/resolver.py`): backtracking search, one version per
  name, most-constrained package first, newest version first, locked version
  preferred. Skips yanked versions (unless already locked), pre-releases (unless
  asked for) and versions that need a newer `kol`. Failures say which
  requirements clash and which versions exist.
* **Lockfile** (`kol.lock`): deterministic TOML, rewritten only when its
  content changes. Commit it. Registry packages carry `sha256` checksums.
* **Store**: archives are verified against the registry index checksum
  *and* the checksum in `kol.lock`, unpacked to a temporary directory, checked
  (identity must match, no links/special files/absolute or `..` paths, size
  caps), then moved into place atomically. A tree hash is recorded so
  `kit cache verify` / `kit doctor` can detect later modification. Kit never
  executes anything from a package at install time.
* **Registries** (`~/.kit/config.toml`): an ordered list of directory
  registries; `KIT_REGISTRY=/dir` overrides it. The **first registry that
  contains a package name owns it** — versions are never merged across
  registries, which prevents dependency-confusion attacks. The on-disk layout
  (`index/<shard>/<name>` JSON lines + `packages/<name>/*.kitpkg`) mirrors
  cargo's sparse index so the same tree can later be served over HTTP.
* **Reproducible archives**: sorted entries, zeroed timestamps/owners, fixed
  gzip header → the same sources always give the same checksum.
* **Concurrency**: store and registry changes take file locks; writes are
  atomic (temp file + rename).

Environment: `KIT_HOME` (default `~/.kit`), `KIT_REGISTRY`, `KOL_HOME`
(location of the compiler checkout), `NO_COLOR`, `KIT_DEBUG`.

## Not in this version

* Online registries, authentication/ownership of package names, and git
  dependencies — directory registries have no publisher identity, so anyone
  with write access to a registry directory can publish to it. Checksums
  protect integrity (lockfile pins), not authorship.
* Features/optional dependencies, workspaces, `kit fmt`, a self-updater.
* Version ranges that need two versions of one package (impossible while
  imports are flat).
* Tested on Linux with CPython 3.11–3.13 only.
