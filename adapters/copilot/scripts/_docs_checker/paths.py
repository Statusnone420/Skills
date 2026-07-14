"""Confined path handling and deterministic Markdown scope traversal."""

import fnmatch
import os
import re
import stat
from pathlib import Path


# Directory names excluded from recursive documentation scans. Metadata,
# dependency, environment, and cache names are excluded at any depth. Broader
# generated/vendor/output names are excluded only as repository-root subtrees,
# so legitimate nested documentation such as docs/build remains inspectable.
# Explicit map and hot-path selections still use direct confined reads.
ANYWHERE_PRUNE_DIRS = (
    ".cache",
    ".git",
    ".hg",
    ".mypy_cache",
    ".next",
    ".nox",
    ".nuxt",
    ".pytest_cache",
    ".ruff_cache",
    ".svn",
    ".tox",
    ".venv",
    "__pycache__",
    "bower_components",
    "env",
    "node_modules",
    "venv",
)
REPOSITORY_ROOT_ONLY_PRUNE_DIRS = (
    "adapters",
    "build",
    "coverage",
    "dist",
    "htmlcov",
    "out",
    "output",
    "target",
    "vendor",
)
STANDARD_PRUNE_DIRS = ANYWHERE_PRUNE_DIRS + REPOSITORY_ROOT_ONLY_PRUNE_DIRS
_ANYWHERE_PRUNE_KEYS = frozenset(name.casefold() for name in ANYWHERE_PRUNE_DIRS)
_ROOT_ONLY_PRUNE_KEYS = frozenset(
    name.casefold() for name in REPOSITORY_ROOT_ONLY_PRUNE_DIRS
)


def _is_reparse(path):
    try:
        info = os.lstat(path)
    except OSError:
        return False
    if stat.S_ISLNK(info.st_mode):
        return True
    return bool(getattr(info, "st_file_attributes", 0) & 0x400)


def _assert_no_reparse_components(path):
    """Reject symlink/junction/reparse components before any filesystem use."""
    path = Path(path).absolute()
    parts = path.parts
    current = Path(parts[0])
    for part in parts[1:]:
        current = current / part
        if _is_reparse(current):
            raise ValueError("symlink or reparse path component")


def safe_path(path, root):
    """Resolve only paths whose existing components are non-symlink and root-confined."""
    raw = os.path.abspath(os.fspath(path))
    base = os.path.abspath(os.fspath(root))
    if os.path.commonpath((raw, base)) != base:
        raise ValueError("path escapes root")
    relative = os.path.relpath(raw, base)
    current = base
    _assert_no_reparse_components(base)
    if current != raw and _is_reparse(current):
        raise ValueError("symlink root")
    for part in relative.split(os.sep):
        current = os.path.join(current, part)
        if os.path.lexists(current) and _is_reparse(current):
            raise ValueError("symlink path")
    return Path(raw)


def _first_reparse_component(path, root):
    """Return the first confined reparse component without following it."""
    raw = os.path.abspath(os.fspath(path))
    base = os.path.abspath(os.fspath(root))
    if os.path.commonpath((raw, base)) != base:
        raise ValueError("path escapes root")
    _assert_no_reparse_components(base)
    relative = os.path.relpath(raw, base)
    if relative == ".":
        return None
    current = Path(base)
    for part in Path(relative).parts:
        current = current / part
        if os.path.lexists(current) and _is_reparse(current):
            return current
    return None


def normalize_repo_relative(value, name):
    """Return one normalized POSIX-style repository-relative path."""
    raw = os.fspath(value)
    candidate = Path(raw.replace("\\", os.sep).replace("/", os.sep))
    if candidate.is_absolute() or candidate.drive or any(part == ".." for part in candidate.parts):
        raise ValueError(f"{name} must be repo-relative")
    normalized = Path(os.path.normpath(os.fspath(candidate)))
    if any(part == ".." for part in normalized.parts):
        raise ValueError(f"{name} must be repo-relative")
    return "." if os.fspath(normalized) in ("", ".") else normalized.as_posix()


def _relative_posix(path, root):
    return Path(path).relative_to(root).as_posix()


def _path_identity(relative):
    return os.path.normcase(os.path.normpath(os.fspath(relative)))


def _is_pruned_relative(relative):
    parts = () if relative == "." else Path(relative).parts
    keys = tuple(part.casefold() for part in parts)
    return bool(
        any(key in _ANYWHERE_PRUNE_KEYS for key in keys)
        or (keys and keys[0] in _ROOT_ONLY_PRUNE_KEYS)
    )


def _raise_walk_error(error):
    raise error


def iter_markdown_scope(root: Path, scope: str, applied_prunes=None) -> tuple[list[Path], list[dict]]:
    """Return in-scope Markdown files and in-scope reparse findings only."""
    root = Path(root).absolute()
    scope_norm = normalize_repo_relative(scope, "scope")
    if _is_pruned_relative(scope_norm):
        raise ValueError("scope is inside a pruned tree")
    scope_path = safe_path(root / scope_norm, root)
    if scope_path.exists() and not scope_path.is_dir():
        raise ValueError("scope must be a directory")
    if not scope_path.exists():
        return [], []

    files = []
    findings = []
    for base, dirs, names in os.walk(
        scope_path,
        followlinks=False,
        onerror=_raise_walk_error,
    ):
        kept_dirs = []
        for name in sorted(dirs, key=lambda item: (item.casefold(), item)):
            path = Path(base) / name
            relative = _relative_posix(path, root)
            if _is_pruned_relative(relative):
                if applied_prunes is not None:
                    applied_prunes.append(relative)
                continue
            if _is_reparse(path):
                findings.append({"kind": "symlink", "path": relative})
            else:
                kept_dirs.append(name)
        dirs[:] = kept_dirs
        for name in sorted(names, key=lambda item: (item.casefold(), item)):
            path = Path(base) / name
            relative = _relative_posix(path, root)
            if _is_pruned_relative(relative):
                if applied_prunes is not None:
                    applied_prunes.append(relative)
                continue
            if _is_reparse(path):
                findings.append({"kind": "symlink", "path": relative})
            elif path.suffix.lower() == ".md":
                files.append(path)
    return files, findings


def prune_summary(applied_paths):
    return {
        "anywhere_names": list(ANYWHERE_PRUNE_DIRS),
        "repository_root_only_names": list(REPOSITORY_ROOT_ONLY_PRUNE_DIRS),
        "applied_paths": sorted(set(applied_paths), key=lambda item: (item.casefold(), item)),
    }


def unique_relative_paths(paths):
    unique = []
    seen = set()
    for relative in paths:
        normalized = normalize_repo_relative(relative, "path")
        key = _path_identity(normalized)
        if key not in seen:
            seen.add(key)
            unique.append(normalized)
    return unique


def route_matches_patterns(relative, patterns):
    """Match one normalized route against deterministic POSIX glob patterns."""
    relative = normalize_repo_relative(relative, "route")
    route_parts = tuple(relative.split("/"))
    for raw_pattern in patterns:
        pattern = normalize_repo_relative(raw_pattern, "cold pattern")
        pattern_parts = tuple(pattern.split("/"))
        memo = {}

        def matches(pattern_index, route_index):
            key = (pattern_index, route_index)
            if key in memo:
                return memo[key]
            if pattern_index == len(pattern_parts):
                result = route_index == len(route_parts)
            elif pattern_parts[pattern_index] == "**":
                result = matches(pattern_index + 1, route_index) or (
                    route_index < len(route_parts)
                    and matches(pattern_index, route_index + 1)
                )
            else:
                result = (
                    route_index < len(route_parts)
                    and fnmatch.fnmatchcase(
                        route_parts[route_index], pattern_parts[pattern_index]
                    )
                    and matches(pattern_index + 1, route_index + 1)
                )
            memo[key] = result
            return result

        if matches(0, 0):
            return True
    return False


__all__ = (
    "ANYWHERE_PRUNE_DIRS",
    "REPOSITORY_ROOT_ONLY_PRUNE_DIRS",
    "STANDARD_PRUNE_DIRS",
    "iter_markdown_scope",
    "normalize_repo_relative",
    "prune_summary",
    "route_matches_patterns",
    "safe_path",
    "unique_relative_paths",
)
