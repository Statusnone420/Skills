"""Complete, dependency-free validators for documentation retrieval routes."""

from __future__ import annotations

import re
from collections.abc import Mapping

MAX_DOCS_ACTIONS = {"map": 4, "check": 4, "context": 4, "doctor": 8}
MAX_COMBINED_READ_PATHS = 3
MAP_ACTION_KINDS = {"read-map", "bounded-probe", "combined-read", "checker"}
DOCTOR_POSTCHECK_KIND = "post-check-read"
ALL_ACTION_KINDS = MAP_ACTION_KINDS | {DOCTOR_POSTCHECK_KIND}
MAP_FALLBACK_ROOT_PATHS = {"README.md", "STATE.md", "PRODUCT.md", "DESIGN.md", "PLAN.md"}
BROAD_RETRIEVAL_KINDS = {
    "repo-wide-search",
    "inventory",
    "name-only-inventory",
    "recursive-inventory",
}
CHECKER_PREFLIGHT_KINDS = {"preflight", "availability-probe"}
CHECKER_SUCCESS_STATUSES = {"clean", "findings"}
CONTEXT_ACTION_POLICY = {
    "read-map": {
        "counts_paths": True,
        "statuses": {"complete", "missing"},
        "status_error": "retrieval.context_action_failed",
    },
    "bounded-probe": {
        "counts_paths": True,
        "statuses": {"complete", "missing"},
        "status_error": "retrieval.context_action_failed",
    },
    "combined-read": {
        "counts_paths": True,
        "statuses": {"complete", "missing"},
        "status_error": "retrieval.context_action_failed",
    },
    "checker": {
        "counts_paths": False,
        "statuses": CHECKER_SUCCESS_STATUSES,
        "status_error": "retrieval.checker_failed",
        "exactly_one_count": True,
    },
}
CONTEXT_ACTION_KINDS = frozenset(CONTEXT_ACTION_POLICY)
_WINDOWS_ABSOLUTE = re.compile(r"^[A-Za-z]:[\\/]")


def _append(errors, error):
    if error not in errors:
        errors.append(error)


def _normalize_actions(actions):
    return [dict(action) for action in actions if isinstance(action, Mapping)]


def _is_safe_relative_path(path):
    if not isinstance(path, str) or not path:
        return False
    normalized = path.replace("\\", "/")
    if normalized.startswith("/") or _WINDOWS_ABSOLUTE.match(normalized):
        return False
    if normalized.lower().startswith("file://"):
        return False
    parts = normalized.split("/")
    return all(part not in {"", ".", ".."} for part in parts)


def _is_allowed_map_path(path):
    if not _is_safe_relative_path(path):
        return False
    normalized = path.replace("\\", "/")
    if normalized in MAP_FALLBACK_ROOT_PATHS:
        return True
    if not normalized.startswith("docs/") or normalized.count("/") != 1:
        return False
    return "." in normalized.rsplit("/", 1)[1]


def _valid_path_list(action):
    paths = action.get("paths")
    return isinstance(paths, list) and all(isinstance(path, str) for path in paths)


def _common_errors(actions, allowed_kinds):
    errors = []
    for action in actions:
        kind = action.get("kind")
        if not isinstance(kind, str) or kind not in allowed_kinds:
            _append(errors, f"retrieval.unknown_action_kind:{kind}")
        if isinstance(kind, str) and kind in BROAD_RETRIEVAL_KINDS:
            _append(errors, "retrieval.broad_action")
        if isinstance(kind, str) and kind in CHECKER_PREFLIGHT_KINDS:
            _append(errors, "retrieval.preflight_action")
        if "paths" in action and not _valid_path_list(action):
            _append(errors, "retrieval.invalid_action_paths")
    return errors


def _validate_path_categories(actions, *, allowed, reject_empty=False):
    errors = []
    for action in actions:
        if "paths" not in action or not _valid_path_list(action):
            continue
        paths = action["paths"]
        if reject_empty and not paths:
            _append(errors, "retrieval.invalid_action_paths")
        if any(not allowed(path) for path in paths):
            _append(errors, "retrieval.forbidden_path")
    return errors


def _checker_runs(actions):
    total = 0
    indexes = []
    for index, action in enumerate(actions):
        if action.get("kind") != "checker":
            continue
        indexes.append(index)
        count = action.get("count", 1)
        if isinstance(count, int) and not isinstance(count, bool) and count >= 0:
            total += count
    return total, indexes


def _is_exactly_one_count(value):
    return isinstance(value, int) and not isinstance(value, bool) and value == 1


def _validate_checker_boundary(actions, *, allow_postcheck=False):
    errors = []
    runs, indexes = _checker_runs(actions)
    if runs > 1:
        _append(errors, "retrieval.repeated_checker")
    if not indexes or runs == 0:
        _append(errors, "retrieval.missing_checker")
        return errors, indexes[-1] if indexes else None
    if any(actions[index].get("status") not in CHECKER_SUCCESS_STATUSES for index in indexes):
        _append(errors, "retrieval.checker_failed")
    checker_index = indexes[-1]
    if not allow_postcheck and checker_index != len(actions) - 1:
        _append(errors, "retrieval.checker_not_final")
    return errors, checker_index


def _first_map_state(actions, errors):
    map_indexes = [index for index, action in enumerate(actions) if action.get("kind") == "read-map"]
    if not map_indexes:
        _append(errors, "retrieval.missing_map_read")
        return None
    if map_indexes[0] != 0:
        _append(errors, "retrieval.map_read_not_first")
        return None
    first = actions[0]
    if first.get("paths") != ["docs/README.md"] or first.get("status") not in {"missing", "complete"}:
        _append(errors, "retrieval.invalid_map_read")
        return None
    return first.get("status")


def _validate_mapped_route(actions, errors, *, precheck=False):
    kinds = [action.get("kind") for action in actions]
    if len(actions) > 3:
        _append(errors, "retrieval.doctor_precheck_budget" if precheck else "retrieval.docs_action_budget")
    if any(kind in {"bounded-probe", "combined-read"} for kind in kinds):
        _append(errors, "retrieval.invalid_map_route")
    if kinds not in (["read-map", "checker"], ["read-map", "read-map", "checker"]):
        _append(errors, "retrieval.invalid_map_route")
    if len(actions) >= 2 and actions[1].get("kind") == "read-map":
        hot = actions[1]
        if hot.get("status") != "complete":
            _append(errors, "retrieval.mapped_read_failed")
        if hot.get("paths") == actions[0].get("paths"):
            _append(errors, "retrieval.duplicate_map_read")
        if not _valid_path_list(hot) or not hot.get("paths"):
            _append(errors, "retrieval.invalid_action_paths")
        elif any(not _is_allowed_map_path(path) for path in hot["paths"]):
            _append(errors, "retrieval.forbidden_path")


def _validate_missing_route(actions, errors, *, precheck=False):
    kinds = [action.get("kind") for action in actions]
    if len(actions) > 4:
        _append(errors, "retrieval.doctor_precheck_budget" if precheck else "retrieval.docs_action_budget")
    if "combined-read" not in kinds:
        _append(errors, "retrieval.missing_combined_read")
    if kinds != ["read-map", "bounded-probe", "combined-read", "checker"]:
        _append(errors, "retrieval.invalid_map_route")
    if "bounded-probe" not in kinds or "combined-read" not in kinds:
        return
    probe_index = kinds.index("bounded-probe")
    combined_index = kinds.index("combined-read")
    if probe_index > combined_index:
        _append(errors, "retrieval.invalid_map_route")
    for action in actions:
        if action.get("kind") not in {"bounded-probe", "combined-read"}:
            continue
        if action.get("status") != "complete":
            _append(errors, "retrieval.fallback_action_failed")
        if not _valid_path_list(action) or not action.get("paths"):
            _append(errors, "retrieval.empty_fallback_paths")
        elif action.get("kind") == "combined-read" and len(action["paths"]) > MAX_COMBINED_READ_PATHS:
            _append(errors, "retrieval.action_path_budget")


def validate_map_or_check_route(actions, command, *, precheck=False):
    """Validate a complete map/check orientation and checker route."""
    if command not in {"map", "check", "doctor"}:
        raise ValueError("unsupported trajectory command")
    actions = _normalize_actions(actions)
    errors = _common_errors(actions, MAP_ACTION_KINDS)
    errors.extend(_validate_path_categories(actions, allowed=_is_allowed_map_path, reject_empty=False))
    state = _first_map_state(actions, errors)
    checker_errors, _ = _validate_checker_boundary(actions)
    errors.extend(checker_errors)
    if state == "complete":
        _validate_mapped_route(actions, errors, precheck=precheck)
    elif state == "missing":
        _validate_missing_route(actions, errors, precheck=precheck)
    elif actions and actions[0].get("kind") == "read-map":
        _append(errors, "retrieval.invalid_map_route")
    return _dedupe(errors)


def _dedupe(errors):
    result = []
    for error in errors:
        _append(result, error)
    return result


def _validate_context_aggregate(actions, errors):
    path_references = 0
    checker_actions = []
    for action in actions:
        kind = action.get("kind")
        policy = CONTEXT_ACTION_POLICY.get(kind)
        if policy is None:
            continue
        if policy["counts_paths"]:
            if not _valid_path_list(action) or not action.get("paths"):
                _append(errors, "retrieval.invalid_action_paths")
            else:
                path_references += len(action["paths"])
        else:
            if "paths" in action:
                _append(errors, "retrieval.invalid_action_paths")
            checker_actions.append(action)
        if action.get("status") not in policy["statuses"]:
            _append(errors, policy["status_error"])
        if policy.get("exactly_one_count") and not _is_exactly_one_count(
            action.get("count", 1)
        ):
            _append(errors, "retrieval.invalid_checker_count")

    if path_references == 0:
        _append(errors, "retrieval.missing_context_evidence")
    if path_references > MAX_DOCS_ACTIONS["context"]:
        _append(errors, "retrieval.context_file_budget")

    checker_runs, _ = _checker_runs(checker_actions)
    if len(checker_actions) > 1 or checker_runs > 1:
        _append(errors, "retrieval.repeated_checker")


def validate_context_route(actions):
    """Validate bounded context reads without requiring a checker."""
    actions = _normalize_actions(actions)
    errors = _common_errors(actions, CONTEXT_ACTION_KINDS)
    if len(actions) > MAX_DOCS_ACTIONS["context"]:
        _append(errors, "retrieval.docs_action_budget")
    errors.extend(_validate_path_categories(actions, allowed=_is_safe_relative_path, reject_empty=True))
    _validate_context_aggregate(actions, errors)
    return _dedupe(errors)


def _validate_doctor_postcheck(actions, errors):
    total_files = 0
    group_files = {}
    groups = set()
    for action in actions:
        if action.get("kind") != DOCTOR_POSTCHECK_KIND:
            _append(errors, f"retrieval.unknown_action_kind:{action.get('kind')}")
            continue
        if action.get("status") != "complete":
            _append(errors, "retrieval.doctor_postcheck_failed")
        if not _valid_path_list(action) or not action.get("paths"):
            _append(errors, "retrieval.invalid_action_paths")
            continue
        if any(not _is_allowed_map_path(path) for path in action["paths"]):
            _append(errors, "retrieval.forbidden_path")
        group = action.get("group", "default")
        if not isinstance(group, str) or not group:
            _append(errors, "retrieval.doctor_postcheck_group")
            continue
        groups.add(group)
        group_files[group] = group_files.get(group, 0) + len(action["paths"])
        total_files += len(action["paths"])
    if len(groups) > 2:
        _append(errors, "retrieval.doctor_postcheck_group_budget")
    if any(count > 2 for count in group_files.values()) or total_files > 4:
        _append(errors, "retrieval.doctor_postcheck_file_budget")


def validate_doctor_route(actions):
    """Validate the shared pre-check route and bounded Doctor post-check phase."""
    actions = _normalize_actions(actions)
    errors = _common_errors(actions, ALL_ACTION_KINDS)
    if len(actions) > MAX_DOCS_ACTIONS["doctor"]:
        _append(errors, "retrieval.docs_action_budget")
    first_postcheck = next(
        (index for index, action in enumerate(actions) if action.get("kind") == DOCTOR_POSTCHECK_KIND),
        None,
    )
    if first_postcheck is None:
        errors.extend(validate_map_or_check_route(actions, "doctor", precheck=True))
        return _dedupe(errors)
    prefix = actions[:first_postcheck]
    postcheck = actions[first_postcheck:]
    errors.extend(validate_map_or_check_route(prefix, "doctor", precheck=True))
    _validate_doctor_postcheck(postcheck, errors)
    return _dedupe(errors)


def validate_route(command, actions):
    """Dispatch to the complete validator for one public command route."""
    if command in {"map", "check"}:
        return validate_map_or_check_route(actions, command)
    if command == "context":
        return validate_context_route(actions)
    if command == "doctor":
        return validate_doctor_route(actions)
    raise ValueError("unsupported trajectory command")
