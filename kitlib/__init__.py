"""Kit - the package manager and project tool for the Kol language.

Kit is offline-first: packages come from directory registries on disk
(``$KIT_HOME/registry`` by default).  The registry layer is abstracted so that
network registries can be added without touching the resolver, store or linker.
"""

KIT_VERSION = "0.1.0"

# Version of the on-disk formats Kit writes.  Bump when they change
# incompatibly; Kit refuses files written by a newer format.
LOCKFILE_VERSION = 1
INDEX_VERSION = 1
