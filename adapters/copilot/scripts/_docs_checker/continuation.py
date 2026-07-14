"""Versioned, state-free planning cursors for selected documentation corpora."""

import base64
import binascii
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
_TOKEN = re.compile(r"^[A-Za-z0-9_-]+$")
_MAX_TOKEN_LENGTH = 8192
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


def _unique_json_object(pairs):
    value = {}
    for key, item in pairs:
        if type(key) is not str or key in value:
            raise ValueError("content continuation token is invalid")
        value[key] = item
    return value


def encode_continuation_token(cursor: dict | None) -> str | None:
    if cursor is None:
        return None
    if not validate_continuation_cursor(cursor):
        raise ValueError("content continuation cursor is invalid")
    payload = json.dumps(
        cursor,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    ).encode("utf-8")
    return base64.urlsafe_b64encode(payload).decode("ascii").rstrip("=")


def decode_continuation_token(token: str) -> dict:
    try:
        if (
            type(token) is not str
            or not token
            or len(token) > _MAX_TOKEN_LENGTH
            or _TOKEN.fullmatch(token) is None
        ):
            raise ValueError
        padded = token + "=" * (-len(token) % 4)
        raw = base64.b64decode(
            padded.encode("ascii"),
            altchars=b"-_",
            validate=True,
        )
        cursor = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_unique_json_object,
            parse_constant=lambda _value: (_ for _ in ()).throw(ValueError()),
        )
        if not validate_continuation_cursor(cursor):
            raise ValueError
        return cursor
    except (UnicodeError, binascii.Error, json.JSONDecodeError, ValueError) as exc:
        raise ValueError("content continuation token is invalid") from exc


def corpus_fingerprint(evidence, *, content_identity=None):
    stable = []
    for item in evidence:
        entry = {
            "path": item["path"],
            "bytes": item["bytes"],
            "modified_ns": item["modified_ns"],
            "mode": item["mode"],
            "device": item["device"],
            "inode": item["inode"],
        }
        if content_identity is not None:
            entry["content_sha256"] = content_identity(item)
        stable.append(entry)
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


def _batch_number_for_start(paths, start, file_limit, byte_limit):
    """Return the deterministic ordinal for a slice beginning at ``start``."""
    index = 0
    number = 1
    while index < start:
        batch_count = 0
        batch_bytes = 0
        while index < len(paths) and batch_count < file_limit:
            item_bytes = paths[index]["bytes"]
            if batch_count and batch_bytes + item_bytes > byte_limit:
                break
            if not batch_count and item_bytes > byte_limit:
                raise ValueError("content continuation cannot cross an oversized document")
            batch_count += 1
            batch_bytes += item_bytes
            index += 1
        if batch_count == 0:
            raise ValueError("content continuation cannot make progress")
        if index > start:
            raise ValueError("content continuation does not begin at a batch boundary")
        number += 1
    if index != start:
        raise ValueError("content continuation does not begin at a batch boundary")
    return number


def _total_batches(paths, file_limit, byte_limit):
    """Return the exact batch count under the file and byte limits."""
    index = 0
    total = 0
    while index < len(paths):
        batch_count = 0
        batch_bytes = 0
        while index < len(paths) and batch_count < file_limit:
            item_bytes = paths[index]["bytes"]
            if batch_count and batch_bytes + item_bytes > byte_limit:
                break
            if not batch_count and item_bytes > byte_limit:
                raise ValueError("content continuation cannot cross an oversized document")
            batch_count += 1
            batch_bytes += item_bytes
            index += 1
        if batch_count == 0:
            raise ValueError("content continuation cannot make progress")
        total += 1
    return total or 1


def _continuation_for_contract(value, discovery_contract_version):
    if discovery_contract_version == DISCOVERY_CURSOR_CONTRACT_VERSION:
        return value
    return {
        key: value[key]
        for key in (
            "schema_version",
            "status",
            "batch",
            "cursor",
            "rejection",
            "fresh_preview_required",
        )
    }


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
    content_identity=None,
):
    """Plan one exact slice and return its next state-free cursor."""
    fingerprint = corpus_fingerprint(evidence)
    binding = repository_binding(repository_identity)
    try:
        total_batches = _total_batches(paths, file_limit, byte_limit)
    except ValueError:
        total_batches = None
    if (
        continuation is not None
        and content_identity is not None
        and validate_continuation_cursor(continuation)
        and continuation["next_index"] < len(evidence)
    ):
        fingerprint = corpus_fingerprint(
            evidence,
            content_identity=content_identity,
        )
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
            _continuation_for_contract({
                "schema_version": CONTINUATION_SCHEMA_VERSION,
                "status": "rejected",
                "batch": None,
                "cursor": None,
                "token": None,
                "total_batches": total_batches,
                "rejection": "stale-or-tampered",
                "fresh_preview_required": True,
            }, discovery_contract_version),
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

    if index < len(paths) and not batch_paths:
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
            _continuation_for_contract({
                "schema_version": CONTINUATION_SCHEMA_VERSION,
                "status": "blocked",
                "batch": None,
                "cursor": None,
                "token": None,
                "total_batches": total_batches,
                "rejection": None,
                "fresh_preview_required": False,
            }, discovery_contract_version),
        )

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
        token = None
        status = "complete"
    elif batch_paths:
        if content_identity is not None:
            fingerprint = corpus_fingerprint(
                evidence,
                content_identity=content_identity,
            )
        cursor = _build_cursor(
            selected_scope,
            index,
            batch_paths[-1]["path"],
            fingerprint,
            binding,
            discovery_contract_version,
        )
        token = (
            encode_continuation_token(cursor)
            if discovery_contract_version == DISCOVERY_CURSOR_CONTRACT_VERSION
            else None
        )
        status = "available"
    else:
        cursor = None
        token = None
        status = "blocked"
    return batch, _continuation_for_contract({
        "schema_version": CONTINUATION_SCHEMA_VERSION,
        "status": status,
        "batch": _batch_number_for_start(paths, start, file_limit, byte_limit),
        "cursor": cursor,
        "token": token,
        "total_batches": total_batches,
        "rejection": None,
        "fresh_preview_required": False,
    }, discovery_contract_version)


__all__ = (
    "CONTINUATION_ORDERING_VERSION",
    "CONTINUATION_POLICY_VERSION",
    "CONTINUATION_SCHEMA_VERSION",
    "DISCOVERY_CURSOR_CONTRACT_VERSION",
    "corpus_fingerprint",
    "decode_continuation_token",
    "encode_continuation_token",
    "plan_content_batch",
    "repository_binding",
    "validate_continuation_cursor",
)
