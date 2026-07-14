"""Validate sanitized Task 5 init-discovery receipts against canonical policy."""

from __future__ import annotations

import re

from trajectory_discovery_capture import (
    ANYWHERE_PRUNE_DIRS,
    DISCOVERY_RECEIPT_CHECKSUM_VERSION,
    DOCUMENTATION_ROOT_NAMES,
    DOCTOR_DISCOVERY_KIND,
    DOCTOR_DISCOVERY_RECEIPT_FIELDS,
    INIT_DISCOVERY_LIMITS,
    PACKAGE_CONTAINER_NAMES,
    REPOSITORY_ROOT_ONLY_PRUNE_DIRS,
    _canonical_receipt_checksum,
    _is_exact_json,
    _prune_reason,
    build_doctor_discovery_action,
)
from trajectory_discovery_v2_contract import validate_v2_action
from trajectory_discovery_v1_policy import (
    _DOC_ROOT_INDEX,
    _PACKAGE_CONTAINER_INDEX,
    _candidate_order_key,
    _is_exact_limits,
    _is_nonnegative_int,
    _normalized_discovery_path,
    _sort_key,
    _valid_discovery_candidates,
    _valid_markdown_items,
    normalize_scope,
    root_only_overrides_for_scope,
    scope_contains,
)

DOCTOR_DISCOVERY_STATUSES = {
    "ready", "choice-required", "stopped", "no-candidates", "batch-limited"
}
DOCTOR_DISCOVERY_TERMINAL_STATUSES = (
    DOCTOR_DISCOVERY_STATUSES - {"ready"}
) | {"adoption-preview"}
DOCTOR_DISCOVERY_PUBLIC_FIELDS = (
    DOCTOR_DISCOVERY_RECEIPT_FIELDS | {"owner", "kind", "receipt_checksum"}
)
DOCTOR_DISCOVERY_OBSERVED_FIELDS = frozenset(
    """metadata_phases scandir_calls raw_directory_entries metadata_operations
    selected_scope_max_depth containers candidate_roots reported_candidate_roots
    selected_markdown_paths selected_markdown_bytes""".split()
)
DOCTOR_DISCOVERY_CONTAINER_FIELDS = frozenset(
    """path observed_child_entries observed_child_entries_is_lower_bound
    considered_child_entries complete opened truncated limit_kind next_boundary""".split()
)
DOCTOR_DISCOVERY_SCOPE_METADATA_FIELDS = frozenset(
    """paths path_count bytes observed_path_count observed_bytes complete truncated
    next_boundary""".split()
)
DOCTOR_DISCOVERY_CONTENT_BATCH_FIELDS = frozenset(
    DOCTOR_DISCOVERY_SCOPE_METADATA_FIELDS
    - {"observed_path_count", "observed_bytes"}
    | {"blocked_by_metadata"}
)
DOCTOR_DISCOVERY_PHYSICAL_LIMIT_KINDS = frozenset(
    """child_entries_per_container scandir_calls raw_directory_entries
    metadata_operations selected_scope_depth""".split()
)
_RECEIPT_CHECKSUM = re.compile(r"^[0-9a-f]{64}$")


def _append(errors, error):
    if error not in errors:
        errors.append(error)


def _valid_container_observations(value, jurisdiction_scope, root_only_overrides):
    if type(value) is not list:
        return None
    allowed_limit_kinds = DOCTOR_DISCOVERY_PHYSICAL_LIMIT_KINDS - {"selected_scope_depth"}
    containers = []
    identities = set()
    for item in value:
        if type(item) is not dict or set(item) != DOCTOR_DISCOVERY_CONTAINER_FIELDS:
            return None
        path = _normalized_discovery_path(
            item["path"],
            jurisdiction_scope,
            root_only_overrides,
            allow_dot=True,
        )
        observed = item["observed_child_entries"]
        complete = item["complete"]
        opened = item["opened"]
        lower_bound = item["observed_child_entries_is_lower_bound"]
        considered = item["considered_child_entries"]
        limit_kind = item["limit_kind"]
        identity = None if path is None else path.casefold()
        if (
            path is None
            or identity in identities
            or not _is_nonnegative_int(observed)
            or not _is_nonnegative_int(considered)
            or type(complete) is not bool
            or type(opened) is not bool
            or type(lower_bound) is not bool
            or type(item["truncated"]) is not bool
            or complete == item["truncated"]
            or lower_bound != (not complete)
            or considered != (observed if complete else 0)
            or (not opened and (complete or observed != 0))
            or not (
                limit_kind is None
                or (
                    type(limit_kind) is str
                    and limit_kind in allowed_limit_kinds
                )
            )
            or (limit_kind is not None and complete)
            or item["next_boundary"] is not None
        ):
            return None
        identities.add(identity)
        containers.append(dict(item, path=path))
    return containers


def _valid_physical_limit(
    value,
    observed,
    containers,
    jurisdiction_scope,
    selected_scope,
    root_only_overrides,
):
    if type(value) is not dict or set(value) != {
        "kind",
        "limit",
        "observed",
        "observed_is_lower_bound",
        "container",
        "depth",
    }:
        return False
    kind = value["kind"]
    if type(kind) is not str or kind not in DOCTOR_DISCOVERY_PHYSICAL_LIMIT_KINDS:
        return False
    container = _normalized_discovery_path(
        value["container"],
        jurisdiction_scope,
        root_only_overrides,
        allow_dot=True,
    )
    limit = INIT_DISCOVERY_LIMITS[kind]
    depth = value["depth"]
    if (
        container is None
        or type(value["limit"]) is not int
        or value["limit"] != limit
        or not _is_nonnegative_int(value["observed"])
        or type(value["observed_is_lower_bound"]) is not bool
        or (depth is not None and not _is_nonnegative_int(depth))
    ):
        return False

    if kind == "selected_scope_depth":
        return bool(
            selected_scope is not None
            and value["observed"] == observed["selected_scope_max_depth"] == limit
            and value["observed_is_lower_bound"] is False
            and depth == limit + 1
            and scope_contains(selected_scope, container)
        )

    if selected_scope is None:
        if depth is not None:
            return False
    elif depth is None or depth > INIT_DISCOVERY_LIMITS["selected_scope_depth"]:
        return False

    matching = [item for item in containers if item["path"] == container]
    if kind == "child_entries_per_container":
        return bool(
            value["observed"] == limit + 1
            and value["observed_is_lower_bound"] is True
            and any(
                item["limit_kind"] == kind
                and item["observed_child_entries"] == value["observed"]
                for item in matching
            )
        )
    if kind == "raw_directory_entries":
        return bool(
            value["observed"] == observed[kind] == limit
            and value["observed_is_lower_bound"] is True
            and any(item["limit_kind"] == kind for item in matching)
        )
    if kind == "scandir_calls":
        return bool(
            value["observed"] == observed[kind] == limit
            and value["observed_is_lower_bound"] is False
            and any(item["limit_kind"] == kind and not item["opened"] for item in matching)
        )
    return bool(
        kind == "metadata_operations"
        and value["observed"] == observed[kind] == limit
        and value["observed_is_lower_bound"] is False
    )


def _valid_prune_evidence(value, exclusions, root_only_overrides):
    if type(value) is not dict or set(value) != {
        "anywhere_names",
        "repository_root_only_names",
        "applied_paths",
    }:
        return False
    if (
        value["anywhere_names"] != list(ANYWHERE_PRUNE_DIRS)
        or value["repository_root_only_names"]
        != list(REPOSITORY_ROOT_ONLY_PRUNE_DIRS)
        or type(value["applied_paths"]) is not list
    ):
        return False
    applied = value["applied_paths"]
    if any(type(path) is not str for path in applied):
        return False
    if applied != sorted(set(applied), key=lambda item: (item.casefold(), item)):
        return False
    applied_identities = {path.casefold() for path in applied}
    for path in applied:
        parts = path.split("/")
        if any(
            "/".join(parts[:index]).casefold() in applied_identities
            for index in range(1, len(parts))
        ):
            return False
    expected_exclusions = set()
    for path in applied:
        normalized = normalize_scope(path)
        reason = _prune_reason(normalized, root_only_overrides) if normalized == path else None
        if reason is None:
            return False
        expected_exclusions.add((path, reason))
    canonical_applied = sorted(
        (path for path, reason in exclusions if reason.endswith("prune")),
        key=lambda item: (item.casefold(), item),
    )
    return expected_exclusions == {
        (path, _prune_reason(path, root_only_overrides))
        for path in canonical_applied
    } and applied == canonical_applied


def _valid_exclusions(value, jurisdiction_scope, root_only_overrides):
    if type(value) is not list:
        return None
    result = set()
    normalized_items = []
    for item in value:
        if type(item) is not dict or set(item) != {"path", "reason"}:
            return None
        path = normalize_scope(item["path"])
        if (
            path != item["path"]
            or path is None
            or not scope_contains(jurisdiction_scope, path)
        ):
            path = None
        reason = item["reason"]
        canonical_prune = (
            _prune_reason(path, root_only_overrides) if path is not None else None
        )
        if (
            path is None
            or type(reason) is not str
            or reason not in {
                "anywhere-prune",
                "repository-root-only-prune",
                "unsafe-reparse",
                "not-directory",
            }
            or (reason.endswith("prune") and canonical_prune != reason)
            or (not reason.endswith("prune") and canonical_prune is not None)
            or (path, reason) in result
        ):
            return None
        result.add((path, reason))
        normalized_items.append({"path": path, "reason": reason})
    expected_order = sorted(
        normalized_items,
        key=lambda item: ((item["path"].casefold(), item["path"]), item["reason"]),
    )
    return result if normalized_items == expected_order else None


def _immediate_child(container, path):
    if path == container:
        return None
    if container == ".":
        relative = path
    elif path.startswith(container + "/"):
        relative = path[len(container) + 1 :]
    else:
        return None
    return relative.split("/", 1)[0] if relative else None


def _evidence_fits_container_counts(
    containers,
    candidate_paths,
    scope_items,
    exclusions,
):
    evidence_paths = [*candidate_paths]
    evidence_paths.extend(item["path"] for item in scope_items)
    evidence_paths.extend(path for path, _reason in exclusions)
    evidence_paths.extend(item["path"] for item in containers)
    for container in containers:
        if not container["complete"]:
            continue
        children = {
            child.casefold()
            for path in evidence_paths
            if (child := _immediate_child(container["path"], path)) is not None
        }
        if len(children) > container["considered_child_entries"]:
            return False
    return True


def _candidate_boundary_source(path):
    parts = path.split("/")
    doc_index = _DOC_ROOT_INDEX.get(parts[-1].casefold())
    if doc_index is None or parts[-1] != DOCUMENTATION_ROOT_NAMES[doc_index]:
        return None
    if len(parts) == 1:
        return "root"
    if (
        len(parts) == 2
        and parts[0].casefold() not in _DOC_ROOT_INDEX
        and parts[0].casefold() not in _PACKAGE_CONTAINER_INDEX
    ):
        return "direct-child"
    if (
        len(parts) == 3
        and parts[0].casefold() in _PACKAGE_CONTAINER_INDEX
    ):
        index = _PACKAGE_CONTAINER_INDEX[parts[0].casefold()]
        return f"container:{PACKAGE_CONTAINER_NAMES[index]}"
    return None


def _candidate_boundary_has_capacity(
    path,
    source,
    candidate_paths,
    containers,
    exclusions,
):
    parts = path.split("/")
    parent = parts[0] if source.startswith("container:") else "."
    matching = [
        item
        for item in containers
        if item["path"] == parent and item["complete"] and item["opened"]
    ]
    if len(matching) != 1:
        return False
    evidence_paths = [*candidate_paths, *(path for path, _reason in exclusions)]
    evidence_paths.extend(item["path"] for item in containers)
    evidence_paths.append(path)
    children = {
        child.casefold()
        for evidence_path in evidence_paths
        if (child := _immediate_child(parent, evidence_path)) is not None
    }
    return len(children) <= matching[0]["considered_child_entries"]


def _valid_candidate_boundary(
    value,
    candidates,
    candidate_paths,
    containers,
    exclusions,
    jurisdiction_scope,
    root_only_overrides,
):
    if type(value) is not list or len(value) != 1:
        return None
    boundary = value[0]
    if (
        type(boundary) is not dict
        or set(boundary) != {"kind", "path"}
        or type(boundary["kind"]) is not str
        or boundary["kind"] != "candidate-roots"
    ):
        return None
    path = _normalized_discovery_path(
        boundary["path"],
        jurisdiction_scope,
        root_only_overrides,
    )
    if path is None or path.casefold() in {item.casefold() for item in candidate_paths}:
        return None
    source = _candidate_boundary_source(path)
    order = None if source is None else _candidate_order_key(path, source)
    last_order = (
        None
        if not candidates
        else _candidate_order_key(candidates[-1]["path"], candidates[-1]["source"])
    )
    if (
        order is None
        or last_order is None
        or order <= last_order
        or not _candidate_boundary_has_capacity(
            path,
            source,
            candidate_paths,
            containers,
            exclusions,
        )
    ):
        return None
    return {"kind": "candidate-roots", "path": path}


def _expected_content_batch(scope_items, blocked):
    expected = {
        "paths": [],
        "path_count": 0,
        "bytes": 0,
        "complete": False,
        "truncated": False,
        "next_boundary": None,
        "blocked_by_metadata": blocked,
    }
    boundary_kind = None
    if blocked:
        return expected, boundary_kind
    expected["complete"] = True
    for item in scope_items:
        if expected["path_count"] >= INIT_DISCOVERY_LIMITS["content_files"]:
            expected.update(complete=False, truncated=True, next_boundary=item["path"])
            boundary_kind = "content-files"
            break
        if expected["bytes"] + item["bytes"] > INIT_DISCOVERY_LIMITS["content_bytes"]:
            expected.update(complete=False, truncated=True, next_boundary=item["path"])
            boundary_kind = "content-bytes"
            break
        expected["paths"].append(dict(item))
        expected["path_count"] += 1
        expected["bytes"] += item["bytes"]
    return expected, boundary_kind


def _matches_exact_boundary(value, expected):
    if expected is None:
        return type(value) is list and value == []
    return bool(
        type(value) is list
        and len(value) == 1
        and type(value[0]) is dict
        and set(value[0]) == {"kind", "path"}
        and type(value[0].get("kind")) is str
        and type(value[0].get("path")) is str
        and value[0] == expected
    )


def _empty_discovery_context():
    return dict(
        selected_scope=None, inspected_scope=None, normalized_scope=None,
        jurisdiction_scope=None, root_only_overrides=(), content_paths=frozenset(),
    )


def _validate_doctor_discovery_action_v1(action, errors):
    if (
        type(action) is not dict
        or set(action) != DOCTOR_DISCOVERY_PUBLIC_FIELDS
        or not _is_exact_json(action)
    ):
        _append(errors, "retrieval.invalid_doctor_init_discovery")
        return _empty_discovery_context()

    receipt_checksum = action.get("receipt_checksum")
    expected_checksum = _canonical_receipt_checksum(
        {
            field: action[field]
            for field in DOCTOR_DISCOVERY_RECEIPT_FIELDS
        }
    )
    invalid = bool(
        type(action.get("owner")) is not str
        or action.get("owner") != "docs"
        or type(action.get("kind")) is not str
        or action.get("kind") != DOCTOR_DISCOVERY_KIND
        or type(receipt_checksum) is not str
        or _RECEIPT_CHECKSUM.fullmatch(receipt_checksum) is None
        or receipt_checksum != expected_checksum
    )
    status = action.get("status")
    requested_scope = action.get("requested_scope")
    raw_normalized_scope = action.get("normalized_scope")
    normalized_scope = (
        None if raw_normalized_scope is None else normalize_scope(raw_normalized_scope)
    )
    jurisdiction_scope = normalize_scope(action.get("jurisdiction_scope"))
    if requested_scope is None:
        expected_normalized = None
        expected_jurisdiction = "."
    else:
        expected_normalized = normalize_scope(requested_scope)
        expected_jurisdiction = expected_normalized
    invalid = invalid or (
        type(action.get("schema_version")) is not int
        or action.get("schema_version") != 1
        or action.get("mode") != "init-discovery"
        or type(status) is not str
        or status not in DOCTOR_DISCOVERY_STATUSES
        or not (requested_scope is None or type(requested_scope) is str)
        or normalized_scope != expected_normalized
        or raw_normalized_scope != expected_normalized
        or jurisdiction_scope != expected_jurisdiction
        or action.get("jurisdiction_scope") != expected_jurisdiction
        or type(action.get("content_reads")) is not int
        or action.get("content_reads") != 0
        or action.get("scope_limited") is not True
        or action.get("repository_exhaustive") is not False
        or not _is_exact_limits(action.get("limits"))
    )
    if jurisdiction_scope is None:
        _append(errors, "retrieval.invalid_doctor_init_discovery")
        return None

    root_only_names = {name.casefold() for name in REPOSITORY_ROOT_ONLY_PRUNE_DIRS}
    expected_overrides = []
    if normalized_scope not in {None, "."}:
        first = normalized_scope.split("/", 1)[0]
        if first.casefold() in root_only_names:
            expected_overrides = [first]
    root_only_overrides = action.get("explicit_root_only_overrides")
    if root_only_overrides != expected_overrides:
        invalid = True
        root_only_overrides = expected_overrides

    candidate_paths = _valid_discovery_candidates(
        action.get("candidates"),
        jurisdiction_scope,
        root_only_overrides,
    )
    if candidate_paths is None:
        invalid = True
        candidate_paths = []
    recommended_scope = action.get("recommended_scope")
    if recommended_scope != (candidate_paths[0] if candidate_paths else None):
        invalid = True

    raw_selected_scope = action.get("selected_scope")
    selected_scope = (
        None
        if raw_selected_scope is None
        else _normalized_discovery_path(
            raw_selected_scope,
            jurisdiction_scope,
            root_only_overrides,
        )
    )
    raw_inspected_scope = action.get("inspected_scope")
    inspected_scope = (
        None
        if raw_inspected_scope is None
        else _normalized_discovery_path(
            raw_inspected_scope,
            jurisdiction_scope,
            root_only_overrides,
        )
    )
    if (
        (raw_selected_scope is not None and selected_scope is None)
        or inspected_scope != selected_scope
        or raw_inspected_scope != selected_scope
    ):
        invalid = True

    explicit_narrow = normalized_scope not in {None, "."}
    if explicit_narrow:
        if candidate_paths != [normalized_scope] or action.get("candidates", [{}])[0].get("source") != "explicit":
            invalid = True
    elif any(
        candidate.get("source") == "explicit"
        for candidate in action.get("candidates", ())
        if type(candidate) is dict
    ):
        invalid = True

    selection_reason = action.get("selection_reason")
    if selected_scope is not None:
        expected_reason = "explicit-scope" if explicit_narrow else "sole-candidate"
        if candidate_paths != [selected_scope] or selection_reason != expected_reason:
            invalid = True
    elif status == "stopped":
        if selection_reason != "discovery-truncated":
            invalid = True
    elif len(candidate_paths) > 1:
        if selection_reason != "choice-required":
            invalid = True
    elif candidate_paths or selection_reason != "no-candidates":
        invalid = True

    observed = action.get("observed")
    containers = None
    if type(observed) is not dict or set(observed) != DOCTOR_DISCOVERY_OBSERVED_FIELDS:
        invalid = True
        observed = {}
    else:
        counter_limits = {
            "metadata_phases": INIT_DISCOVERY_LIMITS["metadata_phases"],
            "scandir_calls": INIT_DISCOVERY_LIMITS["scandir_calls"],
            "raw_directory_entries": INIT_DISCOVERY_LIMITS["raw_directory_entries"],
            "metadata_operations": INIT_DISCOVERY_LIMITS["metadata_operations"],
            "selected_scope_max_depth": INIT_DISCOVERY_LIMITS["selected_scope_depth"],
            "candidate_roots": INIT_DISCOVERY_LIMITS["candidate_roots"] + 1,
            "reported_candidate_roots": INIT_DISCOVERY_LIMITS["candidate_roots"],
            "selected_markdown_paths": INIT_DISCOVERY_LIMITS["selected_markdown_paths"] + 1,
        }
        counters_valid = True
        for name, limit in counter_limits.items():
            if not _is_nonnegative_int(observed.get(name)) or observed[name] > limit:
                invalid = True
                counters_valid = False
        if not _is_nonnegative_int(observed.get("selected_markdown_bytes")):
            invalid = True
            counters_valid = False
        containers = _valid_container_observations(
            observed.get("containers"),
            jurisdiction_scope,
            root_only_overrides,
        )
        if containers is None:
            invalid = True
            containers = []
        if counters_valid and (
            observed.get("metadata_phases")
            != (1 if explicit_narrow or selected_scope is None else 2)
            or observed.get("scandir_calls")
            != sum(1 for item in containers if item["opened"])
            or observed.get("raw_directory_entries")
            != sum(item["observed_child_entries"] for item in containers)
            or observed.get("reported_candidate_roots") != len(candidate_paths)
            or (
                observed.get("candidate_roots") != len(candidate_paths)
                and not (
                    observed.get("candidate_roots")
                    == INIT_DISCOVERY_LIMITS["candidate_roots"] + 1
                    and len(candidate_paths)
                    == INIT_DISCOVERY_LIMITS["candidate_roots"]
                )
            )
            or observed.get("metadata_operations")
            < 1 + 2 * observed.get("scandir_calls")
        ):
            invalid = True

    scope_metadata = action.get("scope_metadata")
    scope_items = None
    if type(scope_metadata) is not dict or set(scope_metadata) != DOCTOR_DISCOVERY_SCOPE_METADATA_FIELDS:
        invalid = True
        scope_metadata = {}
    else:
        scope_items = _valid_markdown_items(
            scope_metadata.get("paths"),
            selected_scope,
            root_only_overrides,
        ) if selected_scope is not None else ([] if scope_metadata.get("paths") == [] else None)
        if scope_items is None:
            invalid = True
            scope_items = []
        metadata_next = scope_metadata.get("next_boundary")
        normalized_metadata_next = (
            None
            if metadata_next is None
            else _normalized_discovery_path(
                metadata_next,
                selected_scope,
                root_only_overrides,
                markdown=True,
            )
        ) if selected_scope is not None else None
        if (
            not _is_nonnegative_int(scope_metadata.get("path_count"))
            or not _is_nonnegative_int(scope_metadata.get("bytes"))
            or not _is_nonnegative_int(scope_metadata.get("observed_path_count"))
            or not _is_nonnegative_int(scope_metadata.get("observed_bytes"))
            or type(scope_metadata.get("complete")) is not bool
            or type(scope_metadata.get("truncated")) is not bool
            or scope_metadata.get("path_count") != len(scope_items)
            or scope_metadata.get("bytes") != sum(item["bytes"] for item in scope_items)
            or scope_metadata.get("observed_path_count") < len(scope_items)
            or scope_metadata.get("observed_bytes") < scope_metadata.get("bytes", 0)
            or scope_metadata.get("path_count") > INIT_DISCOVERY_LIMITS["selected_markdown_paths"]
            or scope_metadata.get("bytes") > INIT_DISCOVERY_LIMITS["selected_markdown_bytes"]
            or (metadata_next is not None and normalized_metadata_next != metadata_next)
            or (scope_metadata.get("truncated") is False and metadata_next is not None)
            or (scope_metadata.get("complete") and scope_metadata.get("truncated"))
            or (
                observed
                and (
                    observed.get("selected_markdown_paths")
                    != scope_metadata.get("observed_path_count")
                    or observed.get("selected_markdown_bytes")
                    != scope_metadata.get("observed_bytes")
                )
            )
        ):
            invalid = True
        if selected_scope is None:
            if dict(scope_metadata) != {
                "paths": [],
                "path_count": 0,
                "bytes": 0,
                "observed_path_count": 0,
                "observed_bytes": 0,
                "complete": False,
                "truncated": False,
                "next_boundary": None,
            }:
                invalid = True
        elif status in {"ready", "batch-limited"} and (
            scope_metadata.get("complete") is not True
            or scope_metadata.get("truncated") is not False
            or scope_metadata.get("observed_path_count") != len(scope_items)
            or scope_metadata.get("observed_bytes") != scope_metadata.get("bytes")
        ):
            invalid = True

    exclusions = _valid_exclusions(
        action.get("applied_exclusions"),
        jurisdiction_scope,
        root_only_overrides,
    )
    if exclusions is None:
        invalid = True
        exclusions = set()
    if not _valid_prune_evidence(action.get("prunes"), exclusions, root_only_overrides):
        invalid = True
    if not _evidence_fits_container_counts(
        containers or [],
        candidate_paths,
        scope_items or [],
        exclusions,
    ):
        invalid = True

    physical_limit = action.get("physical_limit")
    if physical_limit is not None and (
        not observed
        or containers is None
        or not _valid_physical_limit(
            physical_limit,
            observed,
            containers,
            jurisdiction_scope,
            selected_scope,
            root_only_overrides,
        )
    ):
        invalid = True

    batch = action.get("content_batch")
    blocked = bool(
        selected_scope is None
        or scope_metadata.get("truncated") is True
        or physical_limit is not None
    )
    expected_batch, content_boundary_kind = _expected_content_batch(scope_items or [], blocked)
    if (
        type(batch) is not dict
        or set(batch) != DOCTOR_DISCOVERY_CONTENT_BATCH_FIELDS
        or type(batch.get("paths")) is not list
        or not _is_nonnegative_int(batch.get("path_count"))
        or not _is_nonnegative_int(batch.get("bytes"))
        or type(batch.get("complete")) is not bool
        or type(batch.get("truncated")) is not bool
        or type(batch.get("blocked_by_metadata")) is not bool
        or not (
            batch.get("next_boundary") is None
            or type(batch.get("next_boundary")) is str
        )
        or dict(batch) != expected_batch
    ):
        invalid = True
        batch = expected_batch

    next_boundary = action.get("next_boundary")
    expected_boundary = None
    if physical_limit is not None and type(physical_limit) is dict:
        expected_boundary = {
            "kind": "physical-limit",
            "path": physical_limit.get("container"),
        }
    elif content_boundary_kind is not None:
        expected_boundary = {
            "kind": content_boundary_kind,
            "path": expected_batch["next_boundary"],
        }
    elif scope_metadata.get("next_boundary") is not None:
        if (
            scope_metadata.get("path_count")
            == INIT_DISCOVERY_LIMITS["selected_markdown_paths"]
            and scope_metadata.get("observed_path_count")
            == INIT_DISCOVERY_LIMITS["selected_markdown_paths"] + 1
            and scope_metadata.get("observed_bytes") == scope_metadata.get("bytes")
        ):
            boundary_kind = "selected-markdown-paths"
        elif (
            scope_metadata.get("observed_path_count")
            == scope_metadata.get("path_count", 0) + 1
            and scope_metadata.get("observed_bytes", 0)
            > INIT_DISCOVERY_LIMITS["selected_markdown_bytes"]
        ):
            boundary_kind = "selected-markdown-bytes"
        else:
            boundary_kind = None
            invalid = True
        expected_boundary = {
            "kind": boundary_kind,
            "path": scope_metadata.get("next_boundary"),
        }
    elif (
        observed
        and _is_nonnegative_int(observed.get("candidate_roots"))
        and observed.get("candidate_roots")
        == INIT_DISCOVERY_LIMITS["candidate_roots"] + 1
        and len(candidate_paths) == INIT_DISCOVERY_LIMITS["candidate_roots"]
    ):
        candidate_boundary = _valid_candidate_boundary(
            next_boundary,
            action.get("candidates"),
            candidate_paths,
            containers or [],
            exclusions,
            jurisdiction_scope,
            root_only_overrides,
        )
        if candidate_boundary is None:
            invalid = True
        else:
            expected_boundary = candidate_boundary
    elif status == "stopped":
        if (
            type(next_boundary) is not list
            or len(next_boundary) != 1
            or type(next_boundary[0]) is not dict
            or set(next_boundary[0]) != {"kind", "path"}
            or type(next_boundary[0].get("kind")) is not str
            or type(next_boundary[0].get("path")) is not str
        ):
            invalid = True
        else:
            boundary = next_boundary[0]
            boundary_path = boundary.get("path")
            matching_container = [item for item in containers or [] if item["path"] == boundary_path and not item["opened"]]
            expected_reason = {
                "unsafe-container": "unsafe-reparse",
                "invalid-container": "not-directory",
            }.get(boundary.get("kind"))
            if (
                boundary.get("kind") not in {"missing-container", "unsafe-container", "invalid-container"}
                or not matching_container
                or (expected_reason is not None and (boundary_path, expected_reason) not in exclusions)
            ):
                invalid = True
            else:
                expected_boundary = {
                    "kind": boundary["kind"],
                    "path": boundary_path,
                }

    if (
        not _matches_exact_boundary(next_boundary, expected_boundary)
        or action.get("truncated") is not bool(next_boundary)
    ):
        invalid = True

    expected_status = (
        "batch-limited"
        if expected_boundary is not None and expected_boundary.get("kind") in {"content-files", "content-bytes"}
        else "stopped"
        if expected_boundary is not None
        else "ready"
        if selected_scope is not None
        else "choice-required"
        if len(candidate_paths) > 1
        else "no-candidates"
    )
    expected_actions = {
        "ready": (False, None),
        "choice-required": (True, "choose-explicit-scope"),
        "no-candidates": (True, "provide-explicit-scope"),
        "stopped": (True, "narrow-scope-or-continuation"),
        "batch-limited": (
            True,
            "after-content-batch-choose-continuation-or-narrow-scope",
        ),
    }
    if (
        status != expected_status
        or type(action.get("requires_user_action")) is not bool
        or (action.get("requires_user_action"), action.get("user_action"))
        != expected_actions[expected_status]
    ):
        invalid = True

    if invalid:
        _append(errors, "retrieval.invalid_doctor_init_discovery")
    return {
        "selected_scope": selected_scope, "inspected_scope": inspected_scope,
        "normalized_scope": normalized_scope, "jurisdiction_scope": jurisdiction_scope,
        "root_only_overrides": tuple(root_only_overrides),
        "content_paths": frozenset(item["path"] for item in expected_batch["paths"]),
    }


def validate_doctor_discovery_action(action, errors):
    """Dispatch exact v1 and additive v2 receipts through canonical policy."""
    if type(action) is dict and action.get("schema_version") == 1:
        return _validate_doctor_discovery_action_v1(action, errors)
    return validate_v2_action(
        action, errors, append=_append, empty_context=_empty_discovery_context,
        candidate_order_key=_candidate_order_key,
        expected_content_batch=_expected_content_batch,
        normalize_scope=normalize_scope,
        normalized_discovery_path=_normalized_discovery_path,
        sort_key=_sort_key,
        validate_v1=_validate_doctor_discovery_action_v1,
    )

__all__ = (
    "ANYWHERE_PRUNE_DIRS", "DOCUMENTATION_ROOT_NAMES", "DOCTOR_DISCOVERY_KIND",
    "DISCOVERY_RECEIPT_CHECKSUM_VERSION", "DOCTOR_DISCOVERY_RECEIPT_FIELDS",
    "DOCTOR_DISCOVERY_STATUSES",
    "DOCTOR_DISCOVERY_TERMINAL_STATUSES", "INIT_DISCOVERY_LIMITS",
    "PACKAGE_CONTAINER_NAMES", "REPOSITORY_ROOT_ONLY_PRUNE_DIRS", "_prune_reason",
    "build_doctor_discovery_action", "normalize_scope", "root_only_overrides_for_scope",
    "scope_contains", "validate_doctor_discovery_action",
)
