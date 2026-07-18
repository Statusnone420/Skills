"""Canonical policy for inert documentation text formats and metadata."""

import json
import re
from pathlib import Path


DOCUMENT_SUFFIXES = frozenset({".md", ".markdown", ".mdx"})
NAVIGATION_MANIFEST_NAMES = frozenset({"docs.json"})
MAX_FRONTMATTER_BYTES = 64 * 1024
FRONTMATTER_NAVIGATION_KEYS = frozenset({"hidden", "title"})
_FRONTMATTER_KEY = re.compile(r"^([A-Za-z][A-Za-z0-9_-]*)\s*:\s*(.*?)\s*$")


def _is_column_zero_frontmatter_delimiter(line, allowed):
    delimiter = line.rstrip("\r\n")
    return (
        not delimiter.startswith((" ", "\t"))
        and delimiter.rstrip(" \t") in allowed
    )


def is_document_path(value):
    """Return whether a path is supported as inert documentation text."""
    return Path(value).suffix.casefold() in DOCUMENT_SUFFIXES


def is_component_document_path(value):
    """Return whether a document uses the inert component-capable format."""
    return Path(value).suffix.casefold() == ".mdx"


def is_readme_document(value):
    path = Path(value)
    return path.stem.casefold() == "readme" and is_document_path(path)


def is_navigation_manifest_path(value):
    return Path(value).name.casefold() in NAVIGATION_MANIFEST_NAMES


def parse_frontmatter_scalars(text):
    """Read only bounded scalar frontmatter without evaluating YAML or MDX.

    The result deliberately exposes unresolved values instead of guessing at
    lists, mappings, aliases, blocks, or other YAML features.  This helper is
    shared by all inert document formats; it never imports a YAML/MDX runtime.
    """
    if not isinstance(text, str):
        return {"status": "unresolved", "values": {}, "unresolved": ["document"]}
    lines = text.removeprefix("\ufeff").splitlines(keepends=True)
    if not lines or not _is_column_zero_frontmatter_delimiter(lines[0], {"---"}):
        return {"status": "absent", "values": {}, "unresolved": []}
    region_bytes = 0
    closing = None
    for index, line in enumerate(lines):
        region_bytes += len(line.encode("utf-8", "strict"))
        if region_bytes > MAX_FRONTMATTER_BYTES:
            return {"status": "unresolved", "values": {}, "unresolved": ["size"]}
        if index and _is_column_zero_frontmatter_delimiter(line, {"---", "..."}):
            closing = index
            break
    if closing is None:
        return {"status": "unresolved", "values": {}, "unresolved": ["frontmatter"]}

    values = {}
    unresolved = []
    for line_number, line in enumerate(lines[1:closing], 2):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        match = _FRONTMATTER_KEY.fullmatch(line)
        if match is None:
            unresolved.append(f"line:{line_number}")
            continue
        key, raw_value = match.groups()
        if key in values:
            unresolved.append(key)
            continue
        if not raw_value or raw_value.startswith(("[", "{", "|", ">", "&", "*", "!")):
            unresolved.append(key)
            continue
        value = raw_value
        if value.startswith('"'):
            try:
                decoded = json.loads(value)
            except (TypeError, ValueError):
                unresolved.append(key)
                continue
            if not isinstance(decoded, str):
                unresolved.append(key)
                continue
            value = decoded
        elif value.startswith("'"):
            if not value.endswith("'") or len(value) < 2:
                unresolved.append(key)
                continue
            value = value[1:-1].replace("''", "'")
        elif value.casefold() in {"true", "false"}:
            value = value.casefold() == "true"
        elif value.casefold() in {"null", "~"}:
            value = None
        elif any(char in value for char in "{}[]"):
            unresolved.append(key)
            continue
        values[key] = value
    return {
        "status": "unresolved" if unresolved else "measured",
        "values": values,
        "unresolved": sorted(set(unresolved)),
    }


__all__ = (
    "DOCUMENT_SUFFIXES",
    "FRONTMATTER_NAVIGATION_KEYS",
    "MAX_FRONTMATTER_BYTES",
    "NAVIGATION_MANIFEST_NAMES",
    "is_component_document_path",
    "is_document_path",
    "is_navigation_manifest_path",
    "is_readme_document",
    "parse_frontmatter_scalars",
)
