"""Canonical data-only Init discovery receipt versions."""

from .continuation import validate_continuation_cursor
from .knowledge import validate_local_knowledge_receipt
from .paths import normalize_repo_relative
from .surfaces import validate_protected_surfaces


DISCOVERY_CONTRACT_V1 = 1
DISCOVERY_CONTRACT_V2 = 2
DISCOVERY_CONTRACT_VERSIONS = frozenset({DISCOVERY_CONTRACT_V1, DISCOVERY_CONTRACT_V2})

DISCOVERY_V1_FIELDS = frozenset(
    """schema_version mode status root requested_scope normalized_scope
    jurisdiction_scope candidates recommended_scope selected_scope inspected_scope
    selection_reason limits observed scope_metadata content_batch physical_limit
    prunes applied_exclusions explicit_root_only_overrides truncated next_boundary
    requires_user_action user_action scope_limited repository_exhaustive
    content_reads""".split()
)
DISCOVERY_V2_EXTENSION_FIELDS = frozenset(
    {
        "continuation",
        "completeness",
        "adoption_preview",
        "root_documents",
        "local_knowledge",
        "evidence_reads",
        "protected_surfaces",
    }
)
DISCOVERY_V2_FIELDS = DISCOVERY_V1_FIELDS | DISCOVERY_V2_EXTENSION_FIELDS


def discovery_fields(version):
    if type(version) is not int or version not in DISCOVERY_CONTRACT_VERSIONS:
        raise ValueError("unsupported discovery contract version")
    return DISCOVERY_V1_FIELDS if version == DISCOVERY_CONTRACT_V1 else DISCOVERY_V2_FIELDS


def project_discovery_result(result, version, *, absolute_root):
    """Project internal orchestration data onto one exact public contract."""
    fields = discovery_fields(version)
    projected = {key: result[key] for key in sorted(fields)}
    projected["schema_version"] = version
    projected["root"] = str(absolute_root) if version == DISCOVERY_CONTRACT_V1 else "."
    return projected


def _exact_nonnegative_int(value):
    return type(value) is int and value >= 0


def _safe_relative(value, *, allow_dot=False):
    if type(value) is not str:
        return False
    try:
        normalized = normalize_repo_relative(value, "receipt path")
    except (TypeError, ValueError):
        return False
    return normalized == value and (allow_dot or normalized != ".")


def _valid_continuation(value):
    if type(value) is not dict or set(value) != {
        "schema_version",
        "status",
        "batch",
        "cursor",
        "rejection",
        "fresh_preview_required",
    }:
        return False
    if (
        type(value["schema_version"]) is not int
        or value["schema_version"] != 1
        or value["status"] not in {"available", "blocked", "complete", "rejected"}
        or not (value["batch"] is None or _exact_nonnegative_int(value["batch"]))
        or not (value["rejection"] is None or type(value["rejection"]) is str)
        or type(value["fresh_preview_required"]) is not bool
    ):
        return False
    cursor = value["cursor"]
    if cursor is None:
        return value["status"] != "available"
    return bool(
        value["status"] == "available"
        and validate_continuation_cursor(cursor)
    )


def _valid_completeness(value):
    if type(value) is not dict or set(value) != {"status", "errors"}:
        return False
    if value["status"] not in {"complete", "incomplete"} or type(value["errors"]) is not list:
        return False
    for error in value["errors"]:
        if type(error) is not dict or set(error) != {
            "operation",
            "path",
            "phase",
            "depth",
            "blocks_completeness",
            "blocks_selection",
            "blocks_content_planning",
        }:
            return False
        if (
            error["operation"] not in {"lstat", "scandir", "direntry-stat"}
            or not _safe_relative(error["path"], allow_dot=True)
            or error["phase"] not in {"candidate", "scope"}
            or not (error["depth"] is None or _exact_nonnegative_int(error["depth"]))
            or any(
                type(error[field]) is not bool
                for field in (
                    "blocks_completeness",
                    "blocks_selection",
                    "blocks_content_planning",
                )
            )
        ):
            return False
    return not (value["status"] == "complete" and value["errors"])


def _valid_root_documents(value):
    if type(value) is not dict or set(value) != {"paths", "path_count", "bytes", "complete"}:
        return False
    paths = value["paths"]
    if type(paths) is not list or type(value["complete"]) is not bool:
        return False
    previous = None
    total = 0
    for item in paths:
        if type(item) is not dict or set(item) != {"path", "bytes"}:
            return False
        path = item["path"]
        if (
            not _safe_relative(path)
            or "/" in path
            or not _exact_nonnegative_int(item["bytes"])
            or (previous is not None and (path.casefold(), path) <= previous)
        ):
            return False
        previous = (path.casefold(), path)
        total += item["bytes"]
    return bool(
        _exact_nonnegative_int(value["path_count"])
        and value["path_count"] == len(paths)
        and _exact_nonnegative_int(value["bytes"])
        and value["bytes"] == total
    )


def _valid_content_batch(value):
    if type(value) is not dict or set(value) != {
        "paths",
        "path_count",
        "bytes",
        "complete",
        "truncated",
        "next_boundary",
        "blocked_by_metadata",
    }:
        return False
    if (
        type(value["paths"]) is not list
        or not _exact_nonnegative_int(value["path_count"])
        or value["path_count"] != len(value["paths"])
        or not _exact_nonnegative_int(value["bytes"])
        or type(value["complete"]) is not bool
        or type(value["truncated"]) is not bool
        or not (value["next_boundary"] is None or _safe_relative(value["next_boundary"]))
        or type(value["blocked_by_metadata"]) is not bool
    ):
        return False
    total = 0
    for item in value["paths"]:
        if (
            type(item) is not dict
            or set(item) != {"path", "bytes"}
            or not _safe_relative(item["path"])
            or not _exact_nonnegative_int(item["bytes"])
        ):
            return False
        total += item["bytes"]
    return total == value["bytes"]


def _contiguous_slice(batch_paths, scope_paths):
    if not batch_paths:
        return None
    width = len(batch_paths)
    for start in range(len(scope_paths) - width + 1):
        if scope_paths[start : start + width] == batch_paths:
            return start, start + width
    return None


def _valid_continuation_relation(value):
    continuation = value["continuation"]
    batch = value["content_batch"]
    scope = value["scope_metadata"]
    if not _valid_content_batch(batch) or type(scope) is not dict:
        return False
    scope_paths = scope.get("paths")
    if type(scope_paths) is not list:
        return False
    status = continuation["status"]
    if status == "available":
        cursor = continuation["cursor"]
        segment = _contiguous_slice(batch["paths"], scope_paths)
        next_index = cursor["next_index"]
        return bool(
            value["status"] == "batch-limited"
            and value["selected_scope"] == cursor["selected_scope"]
            and batch["complete"] is False
            and batch["truncated"] is True
            and batch["blocked_by_metadata"] is False
            and batch["next_boundary"] is not None
            and segment is not None
            and segment[1] == next_index
            and next_index < len(scope_paths)
            and cursor["after_path"] == scope_paths[next_index - 1].get("path")
            and batch["next_boundary"] == scope_paths[next_index].get("path")
            and value["requires_user_action"] is True
            and value["user_action"]
            == "after-content-batch-choose-continuation-or-narrow-scope"
        )
    if status == "complete":
        segment = _contiguous_slice(batch["paths"], scope_paths)
        return bool(
            continuation["cursor"] is None
            and _exact_nonnegative_int(continuation["batch"])
            and continuation["batch"] > 0
            and continuation["rejection"] is None
            and continuation["fresh_preview_required"] is False
            and batch["complete"] is True
            and batch["truncated"] is False
            and batch["blocked_by_metadata"] is False
            and batch["next_boundary"] is None
            and (not scope_paths or segment is not None and segment[1] == len(scope_paths))
            and value["status"] in {"ready", "adoption-preview"}
        )
    empty_blocked = bool(
        batch["paths"] == []
        and batch["path_count"] == 0
        and batch["bytes"] == 0
        and batch["complete"] is False
        and batch["truncated"] is False
        and batch["next_boundary"] is None
        and batch["blocked_by_metadata"] is True
    )
    if status == "blocked":
        return bool(
            continuation["cursor"] is None
            and continuation["batch"] is None
            and continuation["rejection"] is None
            and continuation["fresh_preview_required"] is False
            and empty_blocked
            and value["status"] in {"choice-required", "stopped"}
        )
    return bool(
        status == "rejected"
        and continuation["cursor"] is None
        and continuation["batch"] is None
        and continuation["rejection"] == "stale-or-tampered"
        and continuation["fresh_preview_required"] is True
        and empty_blocked
        and value["status"] == "stopped"
        and value["requires_user_action"] is True
        and value["user_action"] == "restart-fresh-discovery"
    )


def validate_v2_extensions(value):
    """Validate the additive v2 envelope without filesystem access."""
    if type(value) is not dict or not DISCOVERY_V2_EXTENSION_FIELDS <= set(value):
        return False
    adoption = value["adoption_preview"]
    local = value["local_knowledge"]
    evidence = value["evidence_reads"]
    protected = value["protected_surfaces"]
    return bool(
        _valid_continuation(value["continuation"])
        and _valid_continuation_relation(value)
        and _valid_completeness(value["completeness"])
        and _valid_root_documents(value["root_documents"])
        and type(adoption) is dict
        and set(adoption)
        == {"operation", "target", "text", "includes_local_details", "writes"}
        and adoption["operation"] == "propose-generic-orientation-hook"
        and adoption["target"] in {"AGENTS.md", "shared-map"}
        and type(adoption["text"]) is str
        and adoption["includes_local_details"] is False
        and adoption["writes"] == 0
        and validate_local_knowledge_receipt(local, evidence)
        and validate_protected_surfaces(protected)
    )


__all__ = (
    "DISCOVERY_CONTRACT_V1",
    "DISCOVERY_CONTRACT_V2",
    "DISCOVERY_CONTRACT_VERSIONS",
    "DISCOVERY_V1_FIELDS",
    "DISCOVERY_V2_EXTENSION_FIELDS",
    "DISCOVERY_V2_FIELDS",
    "discovery_fields",
    "project_discovery_result",
    "validate_local_knowledge_receipt",
    "validate_protected_surfaces",
    "validate_v2_extensions",
)
