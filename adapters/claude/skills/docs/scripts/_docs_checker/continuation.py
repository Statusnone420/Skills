"""Versioned, state-free planning cursors for selected documentation corpora."""

import hashlib
import hmac
import json
import re

from .paths import normalize_repo_relative


CONTINUATION_SCHEMA_VERSION = 1
CONTINUATION_POLICY_VERSION = "init-content-v1"
CONTINUATION_ORDERING_VERSION = "repo-relative-casefold-v1"
DISCOVERY_CURSOR_CONTRACT_VERSION = 2
_DIGEST = re.compile(r"^[0-9a-f]{64}$")
_CURSOR_FIELDS = frozenset(
    {
        "schema_version",
        "discovery_contract_version",
        "policy_version",
        "ordering_version",
        "selected_scope",
        "next_index",
        "after_path",
        "corpus_fingerprint",
        "repository_binding",
        "checksum",
    }
)


def _canonical_digest(value):
    encoded = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def corpus_fingerprint(evidence):
    stable = [
        {
            "path": item["path"],
            "bytes": item["bytes"],
            "modified_ns": item["modified_ns"],
            "mode": item["mode"],
            "device": item["device"],
            "inode": item["inode"],
        }
        for item in evidence
    ]
    return _canonical_digest(
        {
            "ordering_version": CONTINUATION_ORDERING_VERSION,
            "paths": stable,
        }
    )


def repository_binding(evidence):
    """Bind a cursor to one confined root without exposing its path."""
    if type(evidence) is not dict or set(evidence) != {"device", "inode", "mode"}:
        raise ValueError("repository identity evidence is invalid")
    if any(type(evidence[field]) is not int for field in evidence):
        raise ValueError("repository identity evidence is invalid")
    return _canonical_digest(
        {
            "contract": "init-repository-binding-v1",
            "identity": evidence,
        }
    )


def _cursor_checksum(cursor):
    unsigned = {key: value for key, value in cursor.items() if key != "checksum"}
    return _canonical_digest(
        {
            "contract": "init-content-continuation",
            "cursor": unsigned,
        }
    )


def _build_cursor(
    selected_scope,
    next_index,
    after_path,
    fingerprint,
    binding,
    discovery_contract_version,
):
    cursor = {
        "schema_version": CONTINUATION_SCHEMA_VERSION,
        "discovery_contract_version": discovery_contract_version,
        "policy_version": CONTINUATION_POLICY_VERSION,
        "ordering_version": CONTINUATION_ORDERING_VERSION,
        "selected_scope": selected_scope,
        "next_index": next_index,
        "after_path": after_path,
        "corpus_fingerprint": fingerprint,
        "repository_binding": binding,
    }
    cursor["checksum"] = _cursor_checksum(cursor)
    return cursor


def _validated_start(
    cursor,
    selected_scope,
    paths,
    fingerprint,
    binding,
    discovery_contract_version,
):
    if cursor is None:
        return 0
    if not validate_continuation_cursor(cursor):
        return None
    cursor_scope = normalize_repo_relative(cursor["selected_scope"], "cursor scope")
    next_index = cursor.get("next_index")
    if (
        cursor.get("discovery_contract_version") != discovery_contract_version
        or cursor_scope != selected_scope
        or not 1 <= next_index < len(paths)
        or cursor.get("after_path") != paths[next_index - 1]["path"]
        or cursor.get("corpus_fingerprint") != fingerprint
        or cursor.get("repository_binding") != binding
    ):
        return None
    return next_index


def validate_continuation_cursor(cursor):
    """Validate an exact cursor envelope and its coherence checksum."""
    if type(cursor) is not dict or set(cursor) != _CURSOR_FIELDS:
        return False
    if (
        type(cursor["schema_version"]) is not int
        or cursor["schema_version"] != CONTINUATION_SCHEMA_VERSION
        or type(cursor["discovery_contract_version"]) is not int
        or cursor["discovery_contract_version"] != DISCOVERY_CURSOR_CONTRACT_VERSION
        or type(cursor["policy_version"]) is not str
        or cursor["policy_version"] != CONTINUATION_POLICY_VERSION
        or type(cursor["ordering_version"]) is not str
        or cursor["ordering_version"] != CONTINUATION_ORDERING_VERSION
        or type(cursor["selected_scope"]) is not str
        or type(cursor["next_index"]) is not int
        or cursor["next_index"] <= 0
        or type(cursor["after_path"]) is not str
        or type(cursor["corpus_fingerprint"]) is not str
        or _DIGEST.fullmatch(cursor["corpus_fingerprint"]) is None
        or type(cursor["repository_binding"]) is not str
        or _DIGEST.fullmatch(cursor["repository_binding"]) is None
        or type(cursor["checksum"]) is not str
        or _DIGEST.fullmatch(cursor["checksum"]) is None
    ):
        return False
    try:
        scope = normalize_repo_relative(cursor["selected_scope"], "cursor scope")
        after = normalize_repo_relative(cursor["after_path"], "cursor boundary")
    except (TypeError, ValueError):
        return False
    return bool(
        scope == cursor["selected_scope"]
        and after == cursor["after_path"]
        and after != "."
        and hmac.compare_digest(cursor["checksum"], _cursor_checksum(cursor))
    )


def plan_content_batch(
    paths,
    evidence,
    selected_scope,
    *,
    continuation=None,
    discovery_contract_version,
    repository_identity,
    file_limit,
    byte_limit,
):
    """Plan one exact slice and return its next state-free cursor."""
    fingerprint = corpus_fingerprint(evidence)
    binding = repository_binding(repository_identity)
    start = _validated_start(
        continuation,
        selected_scope,
        paths,
        fingerprint,
        binding,
        discovery_contract_version,
    )
    if start is None:
        return (
            {
                "paths": [],
                "path_count": 0,
                "bytes": 0,
                "complete": False,
                "truncated": False,
                "next_boundary": None,
                "blocked_by_metadata": True,
            },
            {
                "schema_version": CONTINUATION_SCHEMA_VERSION,
                "status": "rejected",
                "batch": None,
                "cursor": None,
                "rejection": "stale-or-tampered",
                "fresh_preview_required": True,
            },
        )

    batch_paths = []
    batch_bytes = 0
    index = start
    while index < len(paths) and len(batch_paths) < file_limit:
        item = paths[index]
        if batch_paths and batch_bytes + item["bytes"] > byte_limit:
            break
        if not batch_paths and item["bytes"] > byte_limit:
            break
        batch_paths.append(dict(item))
        batch_bytes += item["bytes"]
        index += 1

    complete = index == len(paths)
    next_boundary = None if complete else paths[index]["path"]
    batch = {
        "paths": batch_paths,
        "path_count": len(batch_paths),
        "bytes": batch_bytes,
        "complete": complete,
        "truncated": not complete,
        "next_boundary": next_boundary,
        "blocked_by_metadata": False,
    }
    if complete:
        cursor = None
        status = "complete"
    elif batch_paths:
        cursor = _build_cursor(
            selected_scope,
            index,
            batch_paths[-1]["path"],
            fingerprint,
            binding,
            discovery_contract_version,
        )
        status = "available"
    else:
        cursor = None
        status = "blocked"
    return batch, {
        "schema_version": CONTINUATION_SCHEMA_VERSION,
        "status": status,
        "batch": 1 + (start // file_limit),
        "cursor": cursor,
        "rejection": None,
        "fresh_preview_required": False,
    }


__all__ = (
    "CONTINUATION_ORDERING_VERSION",
    "CONTINUATION_POLICY_VERSION",
    "CONTINUATION_SCHEMA_VERSION",
    "DISCOVERY_CURSOR_CONTRACT_VERSION",
    "corpus_fingerprint",
    "plan_content_batch",
    "repository_binding",
    "validate_continuation_cursor",
)
