"""Confined path handling and deterministic Markdown scope traversal."""

import fnmatch
import os
import re
import stat
import subprocess
from pathlib import Path, PureWindowsPath
from urllib.parse import parse_qsl, unquote, urlsplit


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
_MAX_GIT_PATH_BYTES = 8 * 1024 * 1024
_PRIVATE_SHARED_ROUTE = re.compile(
    r"(?i)(?<![A-Za-z0-9_.-])\.local(?:[\\/]|$)"
)
_WINDOWS_DRIVE_ABSOLUTE_ROUTE = re.compile(
    r"(?i)(?<![A-Za-z0-9_.\\/-])[A-Z]:[\\/]"
)
_WINDOWS_UNC_ROUTE = re.compile(
    r"(?i)(?<![A-Za-z0-9_.\\/-])\\{2,}[^\\/\s]+"
)
_FORWARD_UNC_OR_DOUBLE_ROOT_ROUTE = re.compile(
    r"(?i)(?<![A-Za-z0-9_.:\\/-])/{2,}[^\\/\s]+"
)
_WINDOWS_ROOTED_MULTISEGMENT_ROUTE = re.compile(
    r"(?i)(?<![A-Za-z0-9_.\\/-])\\(?!\\)(?:[^\W_]|[._-][^\W_])[^\\/\s]*[\\/](?:[^\W_]|[._-][^\W_])[^\\/\s]*"
)
_WINDOWS_ROOTED_FILENAME_ROUTE = re.compile(
    r"(?i)(?<![A-Za-z0-9_.\\/-])\\(?!\\)[^\\/\s]*\.[A-Za-z0-9][^\\/\s]*"
)
_WINDOWS_ROOTED_KNOWN_DIR_ROUTE = re.compile(
    r"(?i)(?<![A-Za-z0-9_.\\/-])\\(?!\\)(?:Users|Windows|ProgramData|Program Files|Documents and Settings|System Volume Information|\$Recycle\.Bin)(?=$|[\\/\s,.;:)\]])"
)
_FILE_URI_ROUTE = re.compile(
    r"(?i)(?<![A-Za-z0-9+.-])file:(?:/{1,}|\\{1,})"
)
_PUBLIC_HTTP_URL = re.compile(r"(?i)\bhttps?://[^\s<>]+")
_LOCAL_POSIX_URL_VALUE = re.compile(
    r"(?<![A-Za-z0-9_./-])/(?:home|root|etc|var|tmp|private|mnt|media|opt|usr|bin|sbin|dev|proc|sys|run|srv|boot|Users|Volumes|Applications|System|Library)/[^/?#&\s]+"
)
_FILESYSTEM_URL_KEYS = frozenset(
    {"dir", "directory", "file", "folder", "path", "root", "source"}
)
_POSIX_ABSOLUTE_ROUTE = re.compile(
    r"(?<![A-Za-z0-9_./-])/(?!/)(?:[^\s/\\]+/)*[^\s/\\]+"
)
_TRAVERSAL_ROUTE = re.compile(
    r"(?<![A-Za-z0-9_.-])\.\.(?:[\\/]|$)"
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
    if (
        candidate.is_absolute()
        or candidate.drive
        or PureWindowsPath(raw).drive
        or any(part == ".." for part in candidate.parts)
    ):
        raise ValueError(f"{name} must be repo-relative")
    normalized = Path(os.path.normpath(os.fspath(candidate)))
    if any(part == ".." for part in normalized.parts):
        raise ValueError(f"{name} must be repo-relative")
    return "." if os.fspath(normalized) in ("", ".") else normalized.as_posix()


def _http_url_exposes_route(value):
    try:
        parsed = urlsplit(value)
    except ValueError:
        return False
    for raw_component in (parsed.query, parsed.fragment):
        component = unquote(raw_component)
        if any(
            pattern.search(component) is not None
            for pattern in (
                _PRIVATE_SHARED_ROUTE,
                _FILE_URI_ROUTE,
                _WINDOWS_DRIVE_ABSOLUTE_ROUTE,
                _WINDOWS_UNC_ROUTE,
                _WINDOWS_ROOTED_MULTISEGMENT_ROUTE,
                _WINDOWS_ROOTED_FILENAME_ROUTE,
                _WINDOWS_ROOTED_KNOWN_DIR_ROUTE,
                _TRAVERSAL_ROUTE,
                _LOCAL_POSIX_URL_VALUE,
            )
        ):
            return True
        for key, item in parse_qsl(raw_component, keep_blank_values=True):
            if key.casefold() not in _FILESYSTEM_URL_KEYS:
                continue
            if any(
                pattern.search(item) is not None
                for pattern in (
                    _FORWARD_UNC_OR_DOUBLE_ROOT_ROUTE,
                    _POSIX_ABSOLUTE_ROUTE,
                )
            ):
                return True
    return False


def shared_text_exposes_route(value):
    """Return whether shared prose directly exposes a private or unsafe route."""
    if not isinstance(value, str):
        return False
    if any(
        pattern.search(value) is not None
        for pattern in (
            _PRIVATE_SHARED_ROUTE,
            _FILE_URI_ROUTE,
            _WINDOWS_DRIVE_ABSOLUTE_ROUTE,
            _WINDOWS_UNC_ROUTE,
            _WINDOWS_ROOTED_MULTISEGMENT_ROUTE,
            _WINDOWS_ROOTED_FILENAME_ROUTE,
            _WINDOWS_ROOTED_KNOWN_DIR_ROUTE,
            _TRAVERSAL_ROUTE,
        )
    ):
        return True
    if any(_http_url_exposes_route(match.group(0)) for match in _PUBLIC_HTTP_URL.finditer(value)):
        return True
    http_neutral_text = _PUBLIC_HTTP_URL.sub("", value)
    return any(
        pattern.search(http_neutral_text) is not None
        for pattern in (
            _FORWARD_UNC_OR_DOUBLE_ROOT_ROUTE,
            _POSIX_ABSOLUTE_ROUTE,
        )
    )


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


def tracked_markdown_scope(
    root: Path,
    scope: str,
    *,
    git_marker_present: bool | None = None,
    inventory_only: bool = False,
) -> list[str] | None:
    """Return physically present Git-tracked Markdown, or None outside Git.

    Tracked membership is the shared/private boundary. Ignore appearance never
    excludes a tracked file, while ignored and ordinary untracked files are
    both local-only. A requested root merely nested inside another Git
    worktree uses no-Git filesystem behavior instead of inheriting the
    parent's visibility rules. ``inventory_only`` is reserved for callers that
    have already validated the root and will validate every returned route
    through their own bounded filesystem-I/O layer.
    """
    root = Path(root).absolute()
    scope_norm = normalize_repo_relative(scope, "scope")

    if inventory_only:
        if git_marker_present is None:
            raise ValueError("inventory-only Git marker was not prevalidated")
        if not git_marker_present:
            return None

    def declared_git_repository():
        if git_marker_present is not None:
            return git_marker_present
        return os.path.lexists(root / ".git")

    try:
        top = subprocess.run(
            [
                "git",
                "-C",
                os.fspath(root),
                "rev-parse",
                "--show-prefix" if inventory_only else "--show-toplevel",
            ],
            capture_output=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        if declared_git_repository():
            raise OSError("Git visibility is unavailable") from exc
        return None
    if top.returncode != 0:
        if declared_git_repository():
            raise OSError("Git visibility is unavailable")
        return None
    if inventory_only:
        try:
            prefix = top.stdout.decode("utf-8", "strict").rstrip("\r\n")
        except UnicodeDecodeError as exc:
            raise ValueError("Git worktree prefix is invalid") from exc
        if prefix:
            raise ValueError("repository root does not match Git worktree root")
    else:
        try:
            top_path = Path(top.stdout.decode("utf-8", "strict").strip()).absolute()
        except (UnicodeDecodeError, ValueError) as exc:
            raise ValueError("Git worktree root is invalid") from exc
        root_identity = os.path.realpath(root)
        top_identity = os.path.realpath(top_path)
        if os.path.normcase(root_identity) != os.path.normcase(top_identity):
            if declared_git_repository():
                raise ValueError("repository root does not match Git worktree root")
            return None
    try:
        listed = subprocess.run(
            [
                "git",
                "-C",
                os.fspath(root),
                "ls-files",
                "-z",
                "--cached",
                "--",
                scope_norm,
            ],
            capture_output=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise OSError("Git tracked-path inventory is unavailable") from exc
    if listed.returncode != 0:
        raise OSError("Git tracked-path inventory failed")
    if len(listed.stdout) > _MAX_GIT_PATH_BYTES:
        raise ValueError("Git tracked-path inventory exceeds capacity")

    routes = []
    prefix = "" if scope_norm == "." else scope_norm + "/"
    for raw in listed.stdout.split(b"\0"):
        if not raw:
            continue
        try:
            relative = normalize_repo_relative(
                raw.decode("utf-8", "strict"),
                "tracked path",
            )
        except UnicodeDecodeError as exc:
            raise ValueError("Git tracked path is not UTF-8") from exc
        if scope_norm != "." and relative != scope_norm and not relative.startswith(prefix):
            continue
        if Path(relative).suffix.lower() != ".md" or _is_pruned_relative(relative):
            continue
        if inventory_only:
            routes.append(relative)
            continue
        path = safe_path(root / relative, root)
        if os.path.lexists(path) and (path.is_file() or _is_reparse(path)):
            routes.append(relative)
    return sorted(set(routes), key=lambda item: (item.casefold(), item))


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

    tracked = tracked_markdown_scope(root, scope_norm)
    if tracked is not None:
        files = []
        findings = []
        for relative in tracked:
            path = safe_path(root / relative, root)
            if _is_reparse(path):
                findings.append({"kind": "symlink", "path": relative})
            elif path.is_file():
                files.append(path)
        return files, findings

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
    "shared_text_exposes_route",
    "tracked_markdown_scope",
    "unique_relative_paths",
)
