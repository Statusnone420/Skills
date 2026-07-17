"""Bounded detection for recognized documentation navigation systems."""

import json
import os
from pathlib import Path

from .formats import is_navigation_manifest_path
from .paths import _is_reparse, normalize_repo_relative, safe_path


MAX_NAVIGATION_MANIFEST_BYTES = 256 * 1024


def unsupported_navigation_manifest(root, scope, map_path):
    """Return a recognized manifest when it exists without the requested map."""
    root = Path(root).absolute()
    scope_norm = normalize_repo_relative(scope, "scope")
    relative = "docs.json" if scope_norm == "." else f"{scope_norm}/docs.json"
    if not is_navigation_manifest_path(relative):
        return None
    try:
        candidate = safe_path(root / relative, root)
    except ValueError:
        return None
    if not os.path.lexists(candidate) or _is_reparse(candidate) or not candidate.is_file():
        return None
    if candidate.stat().st_size > MAX_NAVIGATION_MANIFEST_BYTES:
        return None
    try:
        payload = json.loads(candidate.read_text(encoding="utf-8", errors="strict"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None
    schema = payload.get("$schema") if isinstance(payload, dict) else None
    if not isinstance(schema, str) or "mintlify.com/docs.json" not in schema.casefold():
        return None

    map_norm = normalize_repo_relative(map_path, "map")
    try:
        map_candidate = safe_path(root / map_norm, root)
    except ValueError:
        return None
    if map_candidate.is_file() and not _is_reparse(map_candidate):
        return None
    return relative


__all__ = ("MAX_NAVIGATION_MANIFEST_BYTES", "unsupported_navigation_manifest")
