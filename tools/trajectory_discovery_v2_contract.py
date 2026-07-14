"""Additive Task 5.1 receipt dispatch over the approved Task 5 contract."""

from trajectory_discovery_capture import (
    DOCTOR_DISCOVERY_KIND,
    DOCTOR_DISCOVERY_RECEIPT_FIELDS,
    DOCTOR_DISCOVERY_RECEIPT_FIELDS_V2,
    INIT_DISCOVERY_LIMITS,
    REPOSITORY_ROOT_ONLY_PRUNE_DIRS,
    _canonical_receipt_checksum,
    _is_exact_json,
    validate_v2_extensions,
)


_PUBLIC_FIELDS_V2 = (
    DOCTOR_DISCOVERY_RECEIPT_FIELDS_V2 | {"owner", "kind", "receipt_checksum"}
)

_EMPTY_SCOPE_COMPLETE = {
    "paths": [],
    "path_count": 0,
    "bytes": 0,
    "observed_path_count": 0,
    "observed_bytes": 0,
    "complete": True,
    "truncated": False,
    "next_boundary": None,
}
_EMPTY_BATCH_COMPLETE = {
    "paths": [],
    "path_count": 0,
    "bytes": 0,
    "complete": True,
    "truncated": False,
    "next_boundary": None,
    "blocked_by_metadata": False,
}


def _context_from_action(action, validated_context):
    """Return the original v2 content window after shared validation passes."""
    return {
        "selected_scope": action["selected_scope"],
        "inspected_scope": action["inspected_scope"],
        "normalized_scope": action["normalized_scope"],
        "jurisdiction_scope": action["jurisdiction_scope"],
        "root_only_overrides": validated_context["root_only_overrides"],
        "content_paths": frozenset(
            item["path"] for item in action["content_batch"]["paths"]
        ),
    }


def _valid_root_document_scope(action, normalize_scope):
    """Validate the v2 root-document selection that has no v1 equivalent."""
    requested_scope = action["requested_scope"]
    explicit_root = requested_scope is not None
    root_documents = action["root_documents"]
    root_paths = root_documents["paths"]
    expected_candidates = (
        [{"path": ".", "source": "explicit", "rank": 1}]
        if explicit_root
        else []
    )
    expected_scope = {
        "paths": root_paths,
        "path_count": root_documents["path_count"],
        "bytes": root_documents["bytes"],
        "observed_path_count": root_documents["path_count"],
        "observed_bytes": root_documents["bytes"],
        "complete": True,
        "truncated": False,
        "next_boundary": None,
    }
    observed = action["observed"]
    local = action["local_knowledge"]
    if type(observed) is not dict:
        return False
    return bool(
        root_documents["complete"] is True
        and bool(root_paths)
        and (
            requested_scope is None
            or (
                type(requested_scope) is str
                and normalize_scope(requested_scope) == "."
            )
        )
        and action["normalized_scope"] == ("." if explicit_root else None)
        and action["jurisdiction_scope"] == "."
        and action["candidates"] == expected_candidates
        and action["recommended_scope"] == ("." if explicit_root else None)
        and action["selected_scope"] == "."
        and action["inspected_scope"] == "."
        and action["selection_reason"]
        == ("explicit-scope" if explicit_root else "sole-root-document-scope")
        and action["scope_metadata"] == expected_scope
        and action["physical_limit"] is None
        and action["completeness"] == {"status": "complete", "errors": []}
        and action["explicit_root_only_overrides"] == []
        and observed.get("metadata_phases") == 2
        and observed.get("candidate_roots") == len(expected_candidates)
        and observed.get("reported_candidate_roots") == len(expected_candidates)
        and observed.get("selected_markdown_paths") == root_documents["path_count"]
        and observed.get("selected_markdown_bytes") == root_documents["bytes"]
        and local["candidates"] == []
        and local["selected_visibility"] == "shared"
    )


def _valid_local_candidate_choice(action):
    """Validate local-only candidates without exposing them to the frozen v1 lane."""
    candidates = action["candidates"]
    local = action["local_knowledge"]
    local_candidates = local["candidates"]
    if (
        type(candidates) is not list
        or not all(
            type(item) is dict and set(item) == {"path", "source", "rank"}
            for item in candidates
        )
        or type(action["observed"]) is not dict
    ):
        return False
    local_paths = [item["path"] for item in local_candidates]
    surfaced_local_paths = [
        item["path"]
        for item in candidates
        if item["source"] == "local-conventional"
    ]
    observed = action["observed"]
    return bool(
        bool(local_paths)
        and surfaced_local_paths == local_paths
        and action["requested_scope"] is None
        and action["normalized_scope"] is None
        and action["jurisdiction_scope"] == "."
        and action["recommended_scope"] == candidates[0]["path"]
        and action["selected_scope"] is None
        and action["inspected_scope"] is None
        and action["selection_reason"] == "choice-required"
        and action["status"] == "choice-required"
        and action["scope_metadata"]
        == {**_EMPTY_SCOPE_COMPLETE, "complete": False}
        and action["content_batch"]
        == {**_EMPTY_BATCH_COMPLETE, "complete": False, "blocked_by_metadata": True}
        and action["continuation"]
        == {
            "schema_version": 1,
            "status": "blocked",
            "batch": None,
            "cursor": None,
            "rejection": None,
            "fresh_preview_required": False,
        }
        and action["physical_limit"] is None
        and action["truncated"] is False
        and action["next_boundary"] == []
        and action["requires_user_action"] is True
        and action["user_action"] == "choose-explicit-scope"
        and action["completeness"] == {"status": "complete", "errors": []}
        and action["explicit_root_only_overrides"] == []
        and observed.get("metadata_phases") == 1
        and observed.get("candidate_roots") == len(candidates)
        and observed.get("reported_candidate_roots") == len(candidates)
        and observed.get("selected_markdown_paths") == 0
        and observed.get("selected_markdown_bytes") == 0
        and local["selected_visibility"] is None
    )


def _valid_v2_local_relation(action):
    """Bind local-only evidence to surfaced candidates and selected visibility."""
    candidates = action.get("candidates")
    local = action.get("local_knowledge")
    if type(candidates) is not list or type(local) is not dict:
        return False
    if not all(type(item) is dict for item in candidates):
        return False
    local_candidates = local.get("candidates")
    if type(local_candidates) is not list:
        return False
    selected_scope = action.get("selected_scope")
    expected_visibility = (
        "local-only"
        if type(selected_scope) is str
        and (selected_scope == ".local" or selected_scope.startswith(".local/"))
        else "shared"
        if selected_scope is not None
        else None
    )
    return bool(
        [
            item.get("path")
            for item in candidates
            if item.get("source") == "local-conventional"
        ]
        == [item.get("path") for item in local_candidates]
        and local.get("selected_visibility") == expected_visibility
    )


def _v2_candidate_order_key(path, source, candidate_order_key, sort_key):
    """Extend the frozen v1 ordering with the v2 local-only candidate lane."""
    if source == "local-conventional":
        if path != ".local" and not path.startswith(".local/"):
            return None
        return (1, sort_key(path))
    order = candidate_order_key(path, source)
    if order is None:
        return None
    lane = {-1: -1, 0: 0, 1: 2, 2: 3}.get(order[0])
    return None if lane is None else (lane, *order[1:])


def _valid_v2_candidate_envelope(
    action,
    normalize_scope,
    normalized_discovery_path,
    candidate_order_key,
    sort_key,
):
    """Validate candidate paths and ordering before any compatibility projection."""
    requested_scope = action.get("requested_scope")
    normalized_scope = action.get("normalized_scope")
    jurisdiction_scope = action.get("jurisdiction_scope")
    expected_normalized = (
        None if requested_scope is None else normalize_scope(requested_scope)
    )
    expected_jurisdiction = "." if requested_scope is None else expected_normalized
    if (
        not (requested_scope is None or type(requested_scope) is str)
        or normalized_scope != expected_normalized
        or jurisdiction_scope != expected_jurisdiction
        or expected_jurisdiction is None
    ):
        return False

    root_only_names = {name.casefold() for name in REPOSITORY_ROOT_ONLY_PRUNE_DIRS}
    expected_overrides = []
    if normalized_scope not in {None, "."}:
        first = normalized_scope.split("/", 1)[0]
        if first.casefold() in root_only_names:
            expected_overrides = [first]
    if action.get("explicit_root_only_overrides") != expected_overrides:
        return False

    candidates = action.get("candidates")
    if type(candidates) is not list:
        return False
    identities = set()
    previous_order = None
    paths = []
    for rank, candidate in enumerate(candidates, 1):
        if type(candidate) is not dict or set(candidate) != {"path", "source", "rank"}:
            return False
        source = candidate["source"]
        allow_dot = source == "explicit" and candidate["path"] == "."
        path = normalized_discovery_path(
            candidate["path"],
            jurisdiction_scope,
            expected_overrides,
            allow_dot=allow_dot,
        )
        order = (
            None
            if path is None
            else _v2_candidate_order_key(
                path,
                source,
                candidate_order_key,
                sort_key,
            )
        )
        identity = None if path is None else path.casefold()
        if (
            path is None
            or type(candidate["rank"]) is not int
            or candidate["rank"] != rank
            or order is None
            or (source == "explicit" and path != jurisdiction_scope)
            or (previous_order is not None and order <= previous_order)
            or identity in identities
        ):
            return False
        previous_order = order
        identities.add(identity)
        paths.append(path)
    return action.get("recommended_scope") == (paths[0] if paths else None)


def _content_segment_start(batch_paths, scope_paths):
    if not batch_paths:
        return 0 if not scope_paths else None
    width = len(batch_paths)
    for start in range(len(scope_paths) - width + 1):
        if scope_paths[start : start + width] == batch_paths:
            return start
    return None


def _valid_v2_content_window(action, expected_content_batch):
    """Validate the actual continuation window with the canonical v1 limits."""
    continuation = action.get("continuation")
    batch = action.get("content_batch")
    scope = action.get("scope_metadata")
    if (
        type(continuation) is not dict
        or type(batch) is not dict
        or type(scope) is not dict
        or type(batch.get("paths")) is not list
        or type(scope.get("paths")) is not list
    ):
        return False
    continuation_status = continuation.get("status")
    if continuation_status in {"blocked", "rejected"}:
        expected_batch, _boundary_kind = expected_content_batch([], True)
        if dict(batch) != expected_batch:
            return False
        if continuation_status == "rejected":
            return bool(
                action.get("status") == "stopped"
                and action.get("truncated") is False
                and action.get("next_boundary") == []
                and action.get("requires_user_action") is True
                and action.get("user_action") == "restart-fresh-discovery"
            )
        return True
    if continuation_status not in {"available", "complete"}:
        return False

    batch_paths = batch["paths"]
    scope_paths = scope["paths"]
    start = _content_segment_start(batch_paths, scope_paths)
    if start is None:
        return False
    expected_batch, boundary_kind = expected_content_batch(scope_paths[start:], False)
    expected_number = 1 + start // INIT_DISCOVERY_LIMITS["content_files"]
    if dict(batch) != expected_batch or continuation.get("batch") != expected_number:
        return False
    if continuation_status == "available":
        expected_boundary = [{
            "kind": boundary_kind,
            "path": expected_batch["next_boundary"],
        }]
        return bool(
            boundary_kind is not None
            and action.get("status") == "batch-limited"
            and action.get("truncated") is True
            and action.get("next_boundary") == expected_boundary
            and action.get("requires_user_action") is True
            and action.get("user_action")
            == "after-content-batch-choose-continuation-or-narrow-scope"
        )
    return bool(
        boundary_kind is None
        and action.get("status") == "ready"
        and action.get("truncated") is False
        and action.get("next_boundary") == []
        and action.get("requires_user_action") is False
        and action.get("user_action") is None
    )


def _v1_compatibility_action(action, profile, expected_content_batch):
    """Project only v2-only semantics while retaining the approved v1 proof core."""
    projected = {
        field: action[field]
        for field in DOCTOR_DISCOVERY_RECEIPT_FIELDS
    }
    projected["schema_version"] = 1
    if profile in {"root-documents", "local-candidates"}:
        projected.update(
            {
                "status": "no-candidates",
                "requested_scope": None,
                "normalized_scope": None,
                "jurisdiction_scope": ".",
                "candidates": [],
                "recommended_scope": None,
                "selected_scope": None,
                "inspected_scope": None,
                "selection_reason": "no-candidates",
                "scope_metadata": {
                    "paths": [],
                    "path_count": 0,
                    "bytes": 0,
                    "observed_path_count": 0,
                    "observed_bytes": 0,
                    "complete": False,
                    "truncated": False,
                    "next_boundary": None,
                },
                "content_batch": {
                    "paths": [],
                    "path_count": 0,
                    "bytes": 0,
                    "complete": False,
                    "truncated": False,
                    "next_boundary": None,
                    "blocked_by_metadata": True,
                },
                "physical_limit": None,
                "explicit_root_only_overrides": [],
                "truncated": False,
                "next_boundary": [],
                "requires_user_action": True,
                "user_action": "provide-explicit-scope",
            }
        )
        projected["observed"] = {
            **projected["observed"],
            "metadata_phases": 1,
            "candidate_roots": 0,
            "reported_candidate_roots": 0,
            "selected_markdown_paths": 0,
            "selected_markdown_bytes": 0,
        }
    elif action["continuation"]["status"] == "rejected" or (
        action["continuation"]["status"] in {"available", "complete"}
        and action["continuation"]["batch"] != 1
    ):
        expected_batch, boundary_kind = expected_content_batch(
            action["scope_metadata"]["paths"],
            False,
        )
        expected_boundary = (
            []
            if boundary_kind is None
            else [{"kind": boundary_kind, "path": expected_batch["next_boundary"]}]
        )
        projected.update(
            {
                "status": "ready" if boundary_kind is None else "batch-limited",
                "content_batch": expected_batch,
                "truncated": boundary_kind is not None,
                "next_boundary": expected_boundary,
                "requires_user_action": boundary_kind is not None,
                "user_action": (
                    "after-content-batch-choose-continuation-or-narrow-scope"
                    if boundary_kind is not None
                    else None
                ),
            }
        )
    return {
        "owner": action["owner"],
        "kind": DOCTOR_DISCOVERY_KIND,
        **projected,
        "receipt_checksum": _canonical_receipt_checksum(projected),
    }


def _validate_v2_projection(
    action,
    errors,
    profile,
    *,
    append,
    candidate_order_key,
    expected_content_batch,
    normalize_scope,
    normalized_discovery_path,
    sort_key,
    validate_v1,
):
    invalid = bool(
        not _valid_v2_candidate_envelope(
            action,
            normalize_scope,
            normalized_discovery_path,
            candidate_order_key,
            sort_key,
        )
        or not _valid_v2_local_relation(action)
        or not _valid_v2_content_window(action, expected_content_batch)
    )
    try:
        action_v1 = _v1_compatibility_action(
            action,
            profile,
            expected_content_batch,
        )
    except (KeyError, TypeError):
        action_v1 = None
        invalid = True
    v1_errors = []
    context = validate_v1(action_v1, v1_errors)
    if invalid or v1_errors:
        append(errors, "retrieval.invalid_doctor_init_discovery")
    return context


def _validate_adoption_preview(
    action,
    payload_v1,
    normalize_scope,
    validate_v1,
):
    """Validate the v2-only empty-root terminal via the approved v1 core."""
    requested_scope = action["requested_scope"]
    explicit_root = requested_scope is not None
    expected_selection_reason = (
        "explicit-scope" if explicit_root else "no-maintained-documentation"
    )
    expected_candidates = (
        [{"path": ".", "source": "explicit", "rank": 1}]
        if explicit_root
        else []
    )
    observed = action["observed"]
    if (
        not (requested_scope is None or type(requested_scope) is str)
        or (explicit_root and normalize_scope(requested_scope) != ".")
        or action["normalized_scope"] != ("." if explicit_root else None)
        or action["jurisdiction_scope"] != "."
        or action["candidates"] != expected_candidates
        or action["recommended_scope"] != ("." if explicit_root else None)
        or action["selected_scope"] != "."
        or action["inspected_scope"] != "."
        or action["selection_reason"] != expected_selection_reason
        or action["scope_metadata"] != _EMPTY_SCOPE_COMPLETE
        or action["content_batch"] != _EMPTY_BATCH_COMPLETE
        or action["physical_limit"] is not None
        or action["truncated"] is not False
        or action["next_boundary"] != []
        or action["requires_user_action"] is not True
        or action["user_action"] != "review-no-doc-adoption-preview"
        or action["completeness"] != {"status": "complete", "errors": []}
        or action["root_documents"]
        != {"paths": [], "path_count": 0, "bytes": 0, "complete": True}
        or action["continuation"]
        != {
            "schema_version": 1,
            "status": "complete",
            "batch": 1,
            "cursor": None,
            "rejection": None,
            "fresh_preview_required": False,
        }
        or type(observed) is not dict
        or observed.get("metadata_phases") != 2
        or observed.get("candidate_roots") != len(expected_candidates)
        or observed.get("reported_candidate_roots") != len(expected_candidates)
        or observed.get("selected_markdown_paths") != 0
        or observed.get("selected_markdown_bytes") != 0
        or action["explicit_root_only_overrides"] != []
        or not _valid_v2_local_relation(action)
    ):
        return None

    projected = dict(payload_v1)
    projected.update(
        {
            "schema_version": 1,
            "status": "no-candidates",
            "requested_scope": None,
            "normalized_scope": None,
            "jurisdiction_scope": ".",
            "candidates": [],
            "recommended_scope": None,
            "selected_scope": None,
            "inspected_scope": None,
            "selection_reason": "no-candidates",
            "scope_metadata": {
                **_EMPTY_SCOPE_COMPLETE,
                "complete": False,
            },
            "content_batch": {
                **_EMPTY_BATCH_COMPLETE,
                "complete": False,
                "blocked_by_metadata": True,
            },
            "requires_user_action": True,
            "user_action": "provide-explicit-scope",
        }
    )
    projected["observed"] = {
        **projected["observed"],
        "metadata_phases": 1,
        "candidate_roots": 0,
        "reported_candidate_roots": 0,
    }
    action_v1 = {
        "owner": action["owner"],
        "kind": DOCTOR_DISCOVERY_KIND,
        **projected,
        "receipt_checksum": _canonical_receipt_checksum(projected),
    }
    projected_errors = []
    validate_v1(action_v1, projected_errors)
    if projected_errors:
        return None
    return {
        "selected_scope": ".",
        "inspected_scope": ".",
        "normalized_scope": action["normalized_scope"],
        "jurisdiction_scope": ".",
        "root_only_overrides": (),
        "content_paths": frozenset(),
    }


def validate_v2_action(
    action,
    errors,
    *,
    append,
    candidate_order_key,
    empty_context,
    expected_content_batch,
    normalize_scope,
    normalized_discovery_path,
    sort_key,
    validate_v1,
):
    """Validate v2 additions, then reuse the exact approved v1 semantic core."""
    if (
        type(action) is not dict
        or type(action.get("schema_version")) is not int
        or action.get("schema_version") != 2
        or set(action) != _PUBLIC_FIELDS_V2
        or not _is_exact_json(action)
        or not validate_v2_extensions(action)
    ):
        append(errors, "retrieval.invalid_doctor_init_discovery")
        return empty_context()

    payload_v2 = {
        field: action[field]
        for field in DOCTOR_DISCOVERY_RECEIPT_FIELDS_V2
    }
    if action.get("receipt_checksum") != _canonical_receipt_checksum(payload_v2):
        append(errors, "retrieval.invalid_doctor_init_discovery")
        return empty_context()

    payload_v1 = {
        field: action[field]
        for field in DOCTOR_DISCOVERY_RECEIPT_FIELDS
    }
    if action["status"] == "adoption-preview":
        context = _validate_adoption_preview(
            action,
            payload_v1,
            normalize_scope,
            validate_v1,
        )
        if context is None:
            append(errors, "retrieval.invalid_doctor_init_discovery")
            return empty_context()
        return context

    profile = "shared"
    if action["selected_scope"] == "." and action["root_documents"]["paths"]:
        profile = "root-documents"
        valid_profile = _valid_root_document_scope(action, normalize_scope)
    elif type(action["candidates"]) is list and any(
        type(candidate) is dict
        and candidate.get("source") == "local-conventional"
        for candidate in action["candidates"]
    ):
        profile = "local-candidates"
        valid_profile = _valid_local_candidate_choice(action)
    else:
        valid_profile = True
    if not valid_profile:
        append(errors, "retrieval.invalid_doctor_init_discovery")
        return empty_context()

    projection_errors = []
    context = _validate_v2_projection(
        action,
        projection_errors,
        profile,
        append=append,
        candidate_order_key=candidate_order_key,
        expected_content_batch=expected_content_batch,
        normalize_scope=normalize_scope,
        normalized_discovery_path=normalized_discovery_path,
        sort_key=sort_key,
        validate_v1=validate_v1,
    )
    if projection_errors:
        append(errors, "retrieval.invalid_doctor_init_discovery")
        return empty_context()
    return _context_from_action(action, context)


__all__ = ("validate_v2_action",)
