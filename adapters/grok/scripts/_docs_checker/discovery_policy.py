"""Canonical data-only policy for bounded Init discovery."""

from pathlib import Path

from .paths import ANYWHERE_PRUNE_DIRS, REPOSITORY_ROOT_ONLY_PRUNE_DIRS


DOCUMENTATION_ROOT_NAMES = ("docs", "documentation", "wiki")
PACKAGE_CONTAINER_NAMES = ("packages", "apps", "services", "modules", "components")
INIT_DISCOVERY_LIMITS = {
    "basis": "v1-operational-heuristic",
    "metadata_phases": 2,
    "child_entries_per_container": 128,
    "scandir_calls": 256,
    "raw_directory_entries": 4096,
    "metadata_operations": 8192,
    "selected_scope_depth": 16,
    "candidate_roots": 64,
    "selected_markdown_paths": 256,
    "selected_markdown_bytes": 2 * 1024 * 1024,
    "content_files": 12,
    "content_bytes": 256 * 1024,
}

_ANYWHERE_PRUNE_KEYS = frozenset(name.casefold() for name in ANYWHERE_PRUNE_DIRS)
_ROOT_ONLY_PRUNE_KEYS = frozenset(
    name.casefold() for name in REPOSITORY_ROOT_ONLY_PRUNE_DIRS
)


def sort_key(value):
    return value.casefold(), value


def join_relative(parent, name):
    return name if parent == "." else f"{parent}/{name}"


def prune_reason(relative, root_only_overrides=()):
    parts = () if relative == "." else tuple(Path(relative).parts)
    keys = tuple(part.casefold() for part in parts)
    if any(key in _ANYWHERE_PRUNE_KEYS for key in keys):
        return "anywhere-prune"
    override_keys = {item.casefold() for item in root_only_overrides}
    if keys and keys[0] in _ROOT_ONLY_PRUNE_KEYS and keys[0] not in override_keys:
        return "repository-root-only-prune"
    return None


# Compatibility aliases used by the frozen Task 5/6 contracts.
_join_relative = join_relative
_prune_reason = prune_reason
_sort_key = sort_key


__all__ = (
    "DOCUMENTATION_ROOT_NAMES",
    "INIT_DISCOVERY_LIMITS",
    "PACKAGE_CONTAINER_NAMES",
    "join_relative",
    "prune_reason",
    "sort_key",
)
