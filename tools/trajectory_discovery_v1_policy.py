"""Pure path, scope, candidate, and item policy for Task 5 v1 receipts."""

from __future__ import annotations

import re
from pathlib import Path

from trajectory_discovery_capture import (
    DOCUMENTATION_ROOT_NAMES,
    INIT_DISCOVERY_LIMITS,
    PACKAGE_CONTAINER_NAMES,
    REPOSITORY_ROOT_ONLY_PRUNE_DIRS,
    _prune_reason,
)


_DOC_ROOT_INDEX = {
    name.casefold(): index for index, name in enumerate(DOCUMENTATION_ROOT_NAMES)
}
_PACKAGE_CONTAINER_INDEX = {
    name.casefold(): index for index, name in enumerate(PACKAGE_CONTAINER_NAMES)
}
_WINDOWS_ABSOLUTE = re.compile(r"^[A-Za-z]:[\\/]")


def normalize_scope(value):
    if type(value) is not str or not value:
        return None
    normalized = value.replace("\\", "/")
    if (
        normalized.startswith("/")
        or _WINDOWS_ABSOLUTE.match(normalized)
        or normalized.lower().startswith("file://")
    ):
        return None
    parts = []
    for part in normalized.split("/"):
        if part in {"", "."}:
            continue
        if part == "..":
            return None
        parts.append(part)
    return "/".join(parts) or "."


def scope_contains(parent, child):
    return parent == "." or child == parent or child.startswith(parent + "/")


def root_only_overrides_for_scope(scope):
    if scope in {None, "."}:
        return ()
    first = scope.split("/", 1)[0]
    root_only = {name.casefold() for name in REPOSITORY_ROOT_ONLY_PRUNE_DIRS}
    return (first,) if first.casefold() in root_only else ()


def _is_nonnegative_int(value):
    return type(value) is int and value >= 0


def _is_exact_limits(value):
    return bool(
        type(value) is dict
        and set(value) == set(INIT_DISCOVERY_LIMITS)
        and all(
            type(value[name]) is type(expected) and value[name] == expected
            for name, expected in INIT_DISCOVERY_LIMITS.items()
        )
    )


def _normalized_discovery_path(
    value,
    jurisdiction_scope,
    root_only_overrides,
    *,
    markdown=False,
    allow_dot=False,
):
    normalized = normalize_scope(value)
    if (
        normalized is None
        or normalized != value
        or (normalized == "." and not allow_dot)
        or not scope_contains(jurisdiction_scope, normalized)
        or _prune_reason(normalized, root_only_overrides)
        or (markdown and Path(normalized).suffix.lower() != ".md")
    ):
        return None
    return normalized


def _sort_key(value):
    return value.casefold(), value


def _candidate_order_key(path, source):
    parts = path.split("/")
    doc_index = _DOC_ROOT_INDEX.get(parts[-1].casefold())
    if source == "explicit" and path:
        return (-1, _sort_key(path))
    if doc_index is None or parts[-1] != DOCUMENTATION_ROOT_NAMES[doc_index]:
        return None
    if source == "root" and len(parts) == 1 and doc_index is not None:
        return (0, doc_index)
    if (
        source == "direct-child"
        and len(parts) == 2
        and doc_index is not None
        and parts[0].casefold() not in _DOC_ROOT_INDEX
        and parts[0].casefold() not in _PACKAGE_CONTAINER_INDEX
    ):
        return (1, _sort_key(parts[0]), doc_index)
    if type(source) is not str or not source.startswith("container:"):
        return None
    container_name = source.split(":", 1)[1]
    container_index = _PACKAGE_CONTAINER_INDEX.get(container_name.casefold())
    if (
        container_index is None
        or source != f"container:{PACKAGE_CONTAINER_NAMES[container_index]}"
        or len(parts) != 3
        or parts[0].casefold() != container_name.casefold()
        or doc_index is None
    ):
        return None
    return (2, container_index, _sort_key(parts[1]), doc_index)


def _valid_discovery_candidates(value, jurisdiction_scope, root_only_overrides):
    if type(value) is not list:
        return None
    paths = []
    identities = set()
    previous_order = None
    for rank, candidate in enumerate(value, 1):
        if type(candidate) is not dict or set(candidate) != {"path", "source", "rank"}:
            return None
        path = _normalized_discovery_path(
            candidate["path"],
            jurisdiction_scope,
            root_only_overrides,
        )
        source = candidate["source"]
        if path is None or type(candidate["rank"]) is not int or candidate["rank"] != rank:
            return None
        order = _candidate_order_key(path, source)
        identity = path.casefold()
        if (
            order is None
            or (source == "explicit" and path != jurisdiction_scope)
            or (previous_order is not None and order <= previous_order)
            or identity in identities
        ):
            return None
        previous_order = order
        identities.add(identity)
        paths.append(path)
    return paths


def _valid_markdown_items(value, selected_scope, root_only_overrides):
    if type(value) is not list or selected_scope is None:
        return None
    items = []
    identities = set()
    for item in value:
        if type(item) is not dict or set(item) != {"path", "bytes"}:
            return None
        path = _normalized_discovery_path(
            item["path"],
            selected_scope,
            root_only_overrides,
            markdown=True,
        )
        if path is None or not _is_nonnegative_int(item["bytes"]):
            return None
        identity = path.casefold()
        if identity in identities:
            return None
        identities.add(identity)
        items.append({"path": path, "bytes": item["bytes"]})
    return items


__all__ = (
    "normalize_scope",
    "root_only_overrides_for_scope",
    "scope_contains",
)
