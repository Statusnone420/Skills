"""Bounded detection for recognized documentation navigation systems."""

import json
import os
from pathlib import Path

from .formats import is_document_path, is_navigation_manifest_path
from .paths import _is_reparse, normalize_repo_relative, safe_path


MAX_NAVIGATION_MANIFEST_BYTES = 256 * 1024
MINTLIFY_SCHEMA_URLS = frozenset(
    {
        "https://mintlify.com/docs.json",
        "https://mintlify.com/schema.json",
    }
)


def _recognized_mintlify_manifest(root, relative):
    if not is_navigation_manifest_path(relative):
        return False
    try:
        candidate = safe_path(root / relative, root)
    except ValueError:
        return False
    if not os.path.lexists(candidate) or _is_reparse(candidate) or not candidate.is_file():
        return False
    if candidate.stat().st_size > MAX_NAVIGATION_MANIFEST_BYTES:
        return False
    try:
        payload = json.loads(candidate.read_text(encoding="utf-8", errors="strict"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return False
    schema = payload.get("$schema") if isinstance(payload, dict) else None
    return isinstance(schema, str) and schema.casefold().rstrip("/") in MINTLIFY_SCHEMA_URLS


def _manifest_candidates(scope):
    if scope == ".":
        return ("docs.json", "docs/docs.json")
    parts = scope.split("/")
    return tuple(
        [
            f"{'/'.join(parts[:depth])}/docs.json"
            for depth in range(len(parts), 0, -1)
        ]
        + ["docs.json"]
    )


def unsupported_navigation_manifest(root, scope, map_path):
    """Return a recognized manifest when it exists without the requested map."""
    root = Path(root).absolute()
    scope_norm = normalize_repo_relative(scope, "scope")
    relative = next(
        (
            candidate
            for candidate in _manifest_candidates(scope_norm)
            if _recognized_mintlify_manifest(root, candidate)
        ),
        None,
    )
    if relative is None:
        return None

    map_norm = normalize_repo_relative(map_path, "map")
    try:
        map_candidate = safe_path(root / map_norm, root)
    except ValueError:
        return None
    if (
        is_document_path(map_norm)
        and map_candidate.is_file()
        and not _is_reparse(map_candidate)
    ):
        return None
    return relative


__all__ = (
    "MAX_NAVIGATION_MANIFEST_BYTES",
    "MINTLIFY_SCHEMA_URLS",
    "unsupported_navigation_manifest",
)
