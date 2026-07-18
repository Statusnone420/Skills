"""Read-only contracts for intentionally local repository knowledge."""

import json
import re
from pathlib import Path

from .formats import is_document_path
from .paths import _is_pruned_relative, normalize_repo_relative, safe_path


LOCAL_MAP_PATH = ".diataxis/local-map.json"
LOCAL_MAP_SCHEMA_VERSION = 1
LOCAL_MAP_LIFECYCLE_SCHEMA_VERSION = 2
LOCAL_MAP_MAX_BYTES = 64 * 1024
LOCAL_MAP_PREVIEW = {
    "operation": "propose-local-map",
    "path": LOCAL_MAP_PATH,
    "visibility": "local-only",
    "ignored_by_default": True,
    "writes": 0,
}

_DIGEST = re.compile(r"^[0-9a-f]{64}$")
_CONTENT_DIGEST = re.compile(r"^sha256-(?:text|bytes):[0-9a-f]{64}$")
_KIND = re.compile(r"^[a-z][a-z0-9-]{0,63}$")
_DOCUMENT_TOKENS = frozenset(
    {
        "architecture",
        "campaign",
        "decision",
        "decisions",
        "design",
        "docs",
        "documentation",
        "kickoff",
        "plan",
        "product",
        "roadmap",
        "spec",
        "specification",
        "wiki",
    }
)
_DOCUMENT_FILE_STEMS = _DOCUMENT_TOKENS | {
    "readme",
    "state",
}
_LOCAL_NOISE_KEYS = frozenset(
    {
        ".credentials",
        ".env",
        ".secrets",
        "cache",
        "caches",
        "credentials",
        "dependencies",
        "generated",
        "secrets",
        "tmp",
        "temp",
    }
)
_ROUTE_FIELDS = frozenset(
    {
        "route",
        "visibility",
        "kind",
        "topics",
        "aliases",
        "authority",
        "status",
        "preservation",
        "last_verified_system",
        "last_verified_rubric",
    }
)
_LIFECYCLE_ROUTE_FIELDS = _ROUTE_FIELDS | {"content_digest"}
_MAP_FIELDS = frozenset(
    {"schema_version", "repository_identity", "worktree_identity", "routes"}
)
_LOCAL_CANDIDATE_FIELDS = frozenset(
    {"path", "visibility", "source", "evidence"}
)
_LOCAL_CANDIDATE_EVIDENCE = frozenset(
    {"documentation-shaped-directory", "documentation-shaped-file-metadata"}
)
_EVIDENCE_READ_SOURCES = frozenset({".gitignore", LOCAL_MAP_PATH})


def _tokens(value):
    return tuple(token for token in re.split(r"[^a-z0-9]+", value.casefold()) if token)


def local_directory_evidence(name):
    return "documentation-shaped-directory" if _DOCUMENT_TOKENS.intersection(_tokens(name)) else None


def local_document_file_evidence(name):
    path = Path(name)
    return bool(
        is_document_path(path)
        and path.stem.casefold() in _DOCUMENT_FILE_STEMS
    )


def local_prune_reason(relative):
    parts = Path(relative).parts
    return "local-sensitive-prune" if any(part.casefold() in _LOCAL_NOISE_KEYS for part in parts) else None


def local_knowledge_preview(candidates, selected_scope):
    selected_visibility = (
        "local-only"
        if selected_scope is not None
        and (selected_scope == ".local" or selected_scope.startswith(".local/"))
        else "shared"
        if selected_scope is not None
        else None
    )
    return {
        "status": "present-uninspected" if candidates or selected_visibility == "local-only" else "optional-map-uninspected",
        "candidates": [dict(candidate) for candidate in candidates],
        "selected_visibility": selected_visibility,
        "absence_claim_allowed": False,
        "shared_health_impact": False,
        "map_preview": dict(LOCAL_MAP_PREVIEW),
    }


def validate_local_knowledge_receipt(local, evidence_reads):
    """Validate the exact Init local lane without filesystem access."""
    if type(local) is not dict or set(local) != {
        "status",
        "candidates",
        "selected_visibility",
        "absence_claim_allowed",
        "shared_health_impact",
        "map_preview",
    }:
        return False
    if (
        local["status"] not in {"present-uninspected", "optional-map-uninspected"}
        or type(local["candidates"]) is not list
        or local["selected_visibility"] not in {None, "shared", "local-only"}
        or local["absence_claim_allowed"] is not False
        or local["shared_health_impact"] is not False
        or local["map_preview"] != LOCAL_MAP_PREVIEW
    ):
        return False
    previous = None
    for candidate in local["candidates"]:
        if type(candidate) is not dict or set(candidate) != _LOCAL_CANDIDATE_FIELDS:
            return False
        path = candidate["path"]
        try:
            normalized = normalize_repo_relative(path, "local candidate")
        except (TypeError, ValueError):
            return False
        order = (path.casefold(), path) if type(path) is str else None
        if (
            normalized != path
            or (path != ".local" and not path.startswith(".local/"))
            or (previous is not None and order <= previous)
            or candidate["visibility"] != "local-only"
            or candidate["source"] != "conventional-local-root"
            or candidate["evidence"] not in _LOCAL_CANDIDATE_EVIDENCE
        ):
            return False
        previous = order
    expected_status = (
        "present-uninspected"
        if local["candidates"] or local["selected_visibility"] == "local-only"
        else "optional-map-uninspected"
    )
    if local["status"] != expected_status:
        return False

    if type(evidence_reads) is not dict or set(evidence_reads) != {
        "count",
        "bytes",
        "byte_limit",
        "sources",
    }:
        return False
    count = evidence_reads["count"]
    size = evidence_reads["bytes"]
    limit = evidence_reads["byte_limit"]
    sources = evidence_reads["sources"]
    return bool(
        type(count) is int
        and count >= 0
        and type(size) is int
        and size >= 0
        and type(limit) is int
        and limit >= 0
        and size <= limit
        and type(sources) is list
        and count == len(sources)
        and len(sources) == len(set(sources))
        and all(type(source) is str and source in _EVIDENCE_READ_SOURCES for source in sources)
    )


def _reject_constant(value):
    raise ValueError(f"invalid constant {value}")


def _strict_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate key")
        result[key] = value
    return result


def _read_bounded(path, limit):
    try:
        with open(path, "rb") as handle:
            payload = handle.read(limit + 1)
    except OSError as error:
        raise ValueError("local map cannot be read") from error
    if len(payload) > limit:
        raise ValueError("local map exceeds bounded size")
    return payload


def _strict_json(payload):
    try:
        text = payload.decode("utf-8")
        return json.loads(
            text,
            object_pairs_hook=_strict_object,
            parse_constant=_reject_constant,
        )
    except (UnicodeError, json.JSONDecodeError, ValueError) as error:
        raise ValueError("local map is invalid JSON") from error


def _checked_strings(value, field):
    if (
        type(value) is not list
        or len(value) > 32
        or any(type(item) is not str or not item or len(item) > 128 for item in value)
    ):
        raise ValueError(f"local map {field} is invalid")
    identities = [item.casefold() for item in value]
    if len(identities) != len(set(identities)):
        raise ValueError(f"local map {field} must be unique")
    return list(value)


def _validate_route(value, schema_version):
    expected_fields = (
        _LIFECYCLE_ROUTE_FIELDS
        if schema_version == LOCAL_MAP_LIFECYCLE_SCHEMA_VERSION
        else _ROUTE_FIELDS
    )
    if type(value) is not dict or set(value) != expected_fields:
        raise ValueError("local map route fields are invalid")
    try:
        route = normalize_repo_relative(value["route"], "local map route")
    except (TypeError, ValueError) as error:
        raise ValueError("local map route is unsafe") from error
    if route == "." or _is_pruned_relative(route) or local_prune_reason(route):
        raise ValueError("local map route is unsafe")
    if (
        type(value["route"]) is not str
        or route != value["route"]
        or value["visibility"] != "local-only"
        or type(value["kind"]) is not str
        or _KIND.fullmatch(value["kind"]) is None
        or value["authority"] not in {"authoritative", "supplemental", "reference"}
        or value["status"] not in {"current", "draft", "stale", "conflicting"}
        or value["preservation"] != "preserve-local-only"
        or type(value["last_verified_system"]) is not str
        or not value["last_verified_system"]
        or len(value["last_verified_system"]) > 64
        or type(value["last_verified_rubric"]) is not str
        or not value["last_verified_rubric"]
        or len(value["last_verified_rubric"]) > 64
        or (
            schema_version == LOCAL_MAP_LIFECYCLE_SCHEMA_VERSION
            and (
                type(value["content_digest"]) is not str
                or _CONTENT_DIGEST.fullmatch(value["content_digest"]) is None
            )
        )
    ):
        raise ValueError("local map route contract is invalid")
    return {
        **value,
        "topics": _checked_strings(value["topics"], "topics"),
        "aliases": _checked_strings(value["aliases"], "aliases"),
    }


def _validate_map(value):
    if type(value) is not dict or set(value) != _MAP_FIELDS:
        raise ValueError("local map fields are invalid")
    if (
        type(value["schema_version"]) is not int
        or value["schema_version"]
        not in {LOCAL_MAP_SCHEMA_VERSION, LOCAL_MAP_LIFECYCLE_SCHEMA_VERSION}
        or type(value["repository_identity"]) is not str
        or _DIGEST.fullmatch(value["repository_identity"]) is None
        or type(value["worktree_identity"]) is not str
        or _DIGEST.fullmatch(value["worktree_identity"]) is None
        or type(value["routes"]) is not list
        or len(value["routes"]) > 64
    ):
        raise ValueError("local map contract is invalid")
    routes = [
        _validate_route(route, value["schema_version"])
        for route in value["routes"]
    ]
    identities = [route["route"].casefold() for route in routes]
    if len(identities) != len(set(identities)):
        raise ValueError("local map routes must be unique")
    return {**value, "routes": routes}


def validate_local_map(value):
    """Validate either frozen discovery metadata or lifecycle-hashed local routes."""
    return _validate_map(value)


def _conflicts(routes):
    by_topic = {}
    conflicts = set()
    for route in routes:
        if route["authority"] != "authoritative" or route["status"] != "current":
            continue
        for topic in (*route["topics"], *route["aliases"]):
            key = topic.casefold()
            previous = by_topic.get(key)
            if previous is not None and previous["route"] != route["route"]:
                conflicts.update((previous["route"], route["route"]))
            by_topic[key] = route
    return sorted(conflicts, key=lambda item: (item.casefold(), item))


def inspect_local_map(
    root,
    *,
    repository_identity=None,
    worktree_identity=None,
    declared=True,
):
    """Validate the optional local map and optionally verify route hashes."""
    root = Path(root).absolute()
    map_path = safe_path(root / LOCAL_MAP_PATH, root)
    if not map_path.exists():
        return {
            "schema_version": LOCAL_MAP_SCHEMA_VERSION,
            "path": LOCAL_MAP_PATH,
            "status": "declared-local-knowledge-unavailable" if declared else "not-declared",
            "binding": "unavailable",
            "routes": [],
            "conflicts": [],
            "absence_claim_allowed": False,
            "shared_health_impact": False,
            "content_reads": 0,
            "evidence_reads": {
                "count": 0,
                "bytes": 0,
                "byte_limit": LOCAL_MAP_MAX_BYTES,
                "sources": [],
            },
        }
    payload = _read_bounded(map_path, LOCAL_MAP_MAX_BYTES)
    local_map = validate_local_map(_strict_json(payload))
    expected = (repository_identity, worktree_identity)
    actual = (local_map["repository_identity"], local_map["worktree_identity"])
    if all(item is None for item in expected):
        binding = "unverified"
    elif any(type(item) is not str or _DIGEST.fullmatch(item) is None for item in expected):
        raise ValueError("local map expected identity is invalid")
    else:
        binding = "matched" if expected == actual else "mismatch"

    routes = [dict(route) for route in local_map["routes"]]

    conflicts = _conflicts(local_map["routes"])
    if binding == "mismatch":
        status = "binding-mismatch"
    elif conflicts:
        status = "conflicting"
    else:
        status = "present-uninspected"
    return {
        "schema_version": local_map["schema_version"],
        "path": LOCAL_MAP_PATH,
        "status": status,
        "binding": binding,
        "routes": routes,
        "conflicts": conflicts,
        "absence_claim_allowed": False,
        "shared_health_impact": False,
        "content_reads": 0,
        "evidence_reads": {
            "count": 1,
            "bytes": len(payload),
            "byte_limit": LOCAL_MAP_MAX_BYTES,
            "sources": [LOCAL_MAP_PATH],
        },
    }


def route_local_knowledge(local_map, query, *, inspected_routes=()):
    """Return local routing facts without making an unearned absence claim."""
    if type(query) is not str or not query.strip():
        raise ValueError("local knowledge query must be a non-empty string")
    status = local_map.get("status") if type(local_map) is dict else None
    if status in {"declared-local-knowledge-unavailable", "not-declared"}:
        return {
            "status": status,
            "routes": [],
            "uninspected_routes": [],
            "absence_claim_allowed": False,
            "shared_health_impact": False,
        }
    inspected = {
        normalize_repo_relative(route, "inspected local route")
        for route in inspected_routes
    }
    declared = {
        normalize_repo_relative(route["route"], "declared local route")
        for route in local_map.get("routes", ())
    }
    key = query.strip().casefold()
    matches = [
        route
        for route in local_map.get("routes", [])
        if key in {item.casefold() for item in (*route["topics"], *route["aliases"])}
    ]
    paths = [route["route"] for route in matches]
    uninspected = [
        route["route"]
        for route in matches
        if route["route"] not in inspected
    ]
    route_status = (
        "conflicting"
        if any(path in local_map.get("conflicts", ()) for path in paths)
        else "present-uninspected"
        if uninspected
        else "present-inspected"
        if matches
        else "no-local-route"
    )
    return {
        "status": route_status,
        "routes": paths,
        "uninspected_routes": uninspected,
        "absence_claim_allowed": bool(
            not matches
            and status == "present-uninspected"
            and inspected == declared
            and not local_map.get("conflicts")
        ),
        "shared_health_impact": False,
    }


__all__ = (
    "LOCAL_MAP_MAX_BYTES",
    "LOCAL_MAP_PATH",
    "LOCAL_MAP_PREVIEW",
    "LOCAL_MAP_SCHEMA_VERSION",
    "LOCAL_MAP_LIFECYCLE_SCHEMA_VERSION",
    "inspect_local_map",
    "local_directory_evidence",
    "local_document_file_evidence",
    "local_knowledge_preview",
    "local_prune_reason",
    "route_local_knowledge",
    "validate_local_knowledge_receipt",
    "validate_local_map",
)
