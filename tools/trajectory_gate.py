"""Validate sanitized, host-neutral Diátaxis Docs trajectory receipts."""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path

from trajectory_routes import (
    ALL_ACTION_KINDS,
    BROAD_RETRIEVAL_KINDS,
    CHECKER_PREFLIGHT_KINDS,
    DOCTOR_DISCOVERY_KIND,
    DOCTOR_DISCOVERY_TERMINAL_STATUSES,
    MAX_DOCS_ACTIONS,
    validate_route,
)

REQUIRED_ANSWERS = {
    "start",
    "trust",
    "current_truth",
    "generated_or_cold",
    "needs_attention",
    "deliberately_unloaded",
}
REQUIRED_TREE_FEATURES = {
    "real_paths",
    "hierarchy",
    "source_annotations",
    "hot_cold_labels",
    "cold_collapsed",
    "findings_inline",
}
MAX_RELEASE_RUNS = 12
ALLOWED_CAMPAIGN_COMMANDS = ("map", "context", "check", "doctor")
ALLOWED_CAMPAIGN_FIXTURES = ("mapped-repository", "missing-map-repository", "hostile-repository")
PUBLIC_SCHEMA_VERSION = 1
PUBLIC_VISIBILITY = "public-sanitized"
HEALTH_RUBRIC_VERSION = 2
TERMINAL_DOCTOR_OUTCOME_FIELDS_V1 = frozenset(
    {
        "status",
        "read_only",
        "files_changed",
        "findings",
        "answers",
        "reported_finding_count",
        "reported_findings",
        "findings_exhaustive",
        "scope",
    }
)
REPOSITORY_ACTION_KINDS = (
    ALL_ACTION_KINDS | BROAD_RETRIEVAL_KINDS | CHECKER_PREFLIGHT_KINDS
)
_ABSOLUTE_PATH = re.compile(
    r"(?i)(?:"
    r"\b[A-Z]:[\\/]"
    r"|(?<![A-Za-z0-9/:])(?:\\\\|//)[^\\/\s]+[\\/][^\\/\s]+"
    r"|(?<![A-Za-z0-9/:])/(?![/\s])[^\s]*"
    r"|(?<![A-Za-z0-9/:\\])\\(?![\\\s])[^\s]*"
    r"|\bfile://[^\s]+"
    r"|\b[A-Za-z][A-Za-z0-9+.-]*:(?=/[^\s/])[^\s]*"
    r")"
)
_SECRET_KEY = re.compile(r"(?i)(?:^|[_-])(?:api[_-]?key|token|secret|password|credential|private[_-]?key)(?:$|[_-])")
_SECRET_VALUE = re.compile(r"(?i)(?:\b(?:sk|rk|gh[opusr]|github_pat|xox[baprs]-)[a-z0-9_-]{8,}\b|bearer\s+[a-z0-9._-]{12,})")
_PRIVATE_KEY = re.compile(
    r"(?i)(?<![A-Za-z0-9])(?:hidden[\s_-]?reasoning|chain[\s_-]?of[\s_-]?thought|reasoning[\s_-]?content|session[\s_-]?id)(?![A-Za-z0-9])"
)
_PRIVATE_KEY_BLOCK = re.compile(r"(?i)-----BEGIN(?: [A-Z0-9]+)* PRIVATE KEY(?: BLOCK)?-----")
_RAW_EXIT = re.compile(r"(?i)\b(?:exit(?:ed)?(?:\s+with)?(?:\s+(?:code|status))?|return\s*code)\s*[:=]?\s*\d+\b")
_HEALTH_METER = re.compile(
    r"^Docs \[(?P<cells>[█░]{20})\] (?P<percentage>0|[1-9][0-9]?|100)%$"
)
_DOCTOR_FINDING_ID = re.compile(
    r"^DOC-(?P<prefix>[0-9A-F]{8}(?:[0-9A-F]{4})*)$"
)
_DOCTOR_FINGERPRINT = re.compile(r"^(?P<digest>[0-9a-f]{64})$")


def _walk(value, path=()):
    if isinstance(value, Mapping):
        for key, child in value.items():
            yield "key", str(key), path + (str(key),)
            yield from _walk(child, path + (str(key),))
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        for index, child in enumerate(value):
            yield from _walk(child, path + (str(index),))
    elif isinstance(value, str):
        yield "value", value, path


def _validate_public(receipt: Mapping) -> None:
    for kind, text, path in _walk(receipt):
        location = "/".join(path)
        if kind == "key" and (
            _SECRET_KEY.search(text) or _PRIVATE_KEY.search(text) or _PRIVATE_KEY_BLOCK.search(text)
        ):
            raise ValueError(f"public trajectory receipt contains a private key at {location}")
        if kind == "key" and _ABSOLUTE_PATH.search(text):
            raise ValueError(f"public trajectory receipt contains private material at {location}")
        if kind == "value" and (
            _ABSOLUTE_PATH.search(text)
            or _SECRET_VALUE.search(text)
            or _PRIVATE_KEY.search(text)
            or _PRIVATE_KEY_BLOCK.search(text)
        ):
            raise ValueError(f"public trajectory receipt contains private material at {location}")


def _validate_exact_json(value, name):
    def visit(item, path, active):
        item_type = type(item)
        if item is None or item_type in {str, int, bool}:
            return
        if item_type not in {dict, list}:
            raise ValueError(
                f"{name} must use exact JSON types at {'/'.join(path) or name}"
            )
        identity = id(item)
        if identity in active:
            raise ValueError(
                f"{name} must use exact JSON without cycles at {'/'.join(path) or name}"
            )
        active.add(identity)
        try:
            if item_type is list:
                for index, child in enumerate(item):
                    visit(child, path + (str(index),), active)
            else:
                for key, child in item.items():
                    if type(key) is not str:
                        raise ValueError(
                            f"{name} must use exact JSON string keys at {'/'.join(path) or name}"
                        )
                    visit(child, path + (key,), active)
        finally:
            active.remove(identity)

    visit(value, (), set())


def _require_mapping(value, name):
    if type(value) is not dict:
        raise ValueError(f"{name} must be an object")
    return value


def _validate_public_artifact(value, name):
    if type(value) is dict and (
        type(value.get("schema_version")) is not int
        or value.get("schema_version") != PUBLIC_SCHEMA_VERSION
    ):
        raise ValueError(f"unsupported public trajectory {name} schema")
    _validate_exact_json(value, name)
    artifact = _require_mapping(value, name)
    _validate_public(artifact)
    if artifact.get("visibility") != PUBLIC_VISIBILITY:
        raise ValueError(f"unsupported public trajectory {name} visibility")
    return artifact


def _positive_int(value, name):
    if type(value) is not int or value < 0:
        raise ValueError(f"{name} must be a non-negative integer")
    return value


def _string_array(value, name):
    if type(value) is not list or any(type(item) is not str for item in value):
        raise ValueError(f"{name} must be an array of strings")
    return value


def _normalize_scope_evidence(value):
    if not isinstance(value, str) or not value:
        return None
    normalized = value.replace("\\", "/")
    if normalized.startswith("/") or re.match(r"^[A-Za-z]:", normalized):
        return None
    parts = []
    for part in normalized.split("/"):
        if part in ("", "."):
            continue
        if part == "..":
            return None
        parts.append(part)
    return "/".join(parts) or "."


def _validate_allowlist(value, name, allowed):
    values = _string_array(value, name)
    if not values:
        raise ValueError(
            f"{name} must contain at least one value; allowed values: {', '.join(allowed)}"
        )
    unknown = sorted(set(values) - set(allowed))
    if unknown:
        raise ValueError(
            f"{name} contains unsupported value(s): {', '.join(unknown)}; "
            f"allowed values: {', '.join(allowed)}"
        )
    return values


def _reject_duplicate_json_keys(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _health_meter_matches(meter, percentage=None):
    if not isinstance(meter, str):
        return False
    match = _HEALTH_METER.fullmatch(meter)
    if match is None:
        return False
    meter_percentage = int(match.group("percentage"))
    if percentage is not None and meter_percentage != percentage:
        return False
    filled = meter_percentage // 5
    expected_cells = "█" * filled + "░" * (20 - filled)
    return match.group("cells") == expected_cells


def _validate_health_meter(
    presentation,
    checker_actions,
    command,
    errors,
    *,
    terminal_doctor_discovery=False,
):
    if command not in {"map", "check", "doctor"}:
        return
    if terminal_doctor_discovery:
        return
    meter = presentation.get("health_meter")
    if not isinstance(meter, str):
        errors.append("presentation.missing_health_meter")
        return
    if not _health_meter_matches(meter):
        errors.append("presentation.invalid_health_meter")
        return
    if not checker_actions:
        errors.append("presentation.missing_checker_health")
        return
    checker_health = checker_actions[0].get("health")
    if checker_health is None:
        errors.append("presentation.missing_checker_health")
        return
    if not isinstance(checker_health, Mapping):
        errors.append("presentation.invalid_checker_health")
        return
    rubric_version = checker_health.get("rubric_version")
    percentage = checker_health.get("percentage")
    checker_meter = checker_health.get("meter")
    if (
        type(rubric_version) is not int
        or rubric_version != HEALTH_RUBRIC_VERSION
        or type(percentage) is not int
        or not 0 <= percentage <= 100
        or not _health_meter_matches(checker_meter, percentage)
    ):
        errors.append("presentation.invalid_checker_health")
        return
    if meter != checker_meter:
        errors.append("presentation.health_meter_mismatch")


def _validate_exhaustive_scope(
    outcome,
    checker_actions,
    command,
    declared_scope,
    errors,
):
    if command not in {"check", "doctor"} or outcome.get("findings_exhaustive") is not True:
        return
    if declared_scope is None:
        errors.append("outcome.missing_findings_scope")
    if not checker_actions:
        errors.append("retrieval.missing_checker_scope")
        return
    checker_scope = _normalize_scope_evidence(checker_actions[0].get("scope"))
    if checker_scope is None:
        errors.append("retrieval.missing_checker_scope")
    elif declared_scope is not None and checker_scope != declared_scope:
        errors.append("retrieval.checker_scope_mismatch")


def _validate_doctor_scope(
    checker_actions,
    command,
    declared_scope,
    errors,
):
    if command != "doctor":
        return
    successful = [
        action
        for action in checker_actions
        if action.get("status") in {"clean", "findings"}
    ]
    if not successful:
        return
    if declared_scope is None and "outcome.missing_findings_scope" not in errors:
        errors.append("outcome.missing_findings_scope")
    for checker in successful:
        checker_scope = _normalize_scope_evidence(checker.get("scope"))
        if checker_scope is None:
            if "retrieval.missing_checker_scope" not in errors:
                errors.append("retrieval.missing_checker_scope")
        elif declared_scope is not None and checker_scope != declared_scope:
            if "retrieval.checker_scope_mismatch" not in errors:
                errors.append("retrieval.checker_scope_mismatch")


def _doctor_identity_set(value, error, errors):
    if not isinstance(value, list):
        errors.append(error)
        return None
    identities = set()
    ids = set()
    fingerprints = set()
    for item in value:
        if not isinstance(item, Mapping) or set(item) != {"id", "fingerprint"}:
            errors.append(error)
            return None
        finding_id = item["id"]
        fingerprint = item["fingerprint"]
        id_match = (
            _DOCTOR_FINDING_ID.fullmatch(finding_id)
            if isinstance(finding_id, str)
            else None
        )
        fingerprint_match = (
            _DOCTOR_FINGERPRINT.fullmatch(fingerprint)
            if isinstance(fingerprint, str)
            else None
        )
        if id_match is None or fingerprint_match is None:
            errors.append(error)
            return None
        prefix = id_match.group("prefix").lower()
        digest = fingerprint_match.group("digest")
        if len(prefix) > len(digest) or not digest.startswith(prefix):
            errors.append(error)
            return None
        identity = (finding_id, fingerprint)
        if finding_id in ids or fingerprint in fingerprints or identity in identities:
            errors.append(error)
            return None
        ids.add(finding_id)
        fingerprints.add(fingerprint)
        identities.add(identity)
    return identities


def _validate_doctor_finding_contract(outcome, checker_actions, command, errors):
    if command != "doctor":
        return
    successful = [
        action
        for action in checker_actions
        if action.get("status") in {"clean", "findings"}
    ]
    if not successful:
        return

    reported = _doctor_identity_set(
        outcome.get("reported_findings"),
        "outcome.invalid_reported_findings",
        errors,
    )
    reported_count = outcome.get("reported_finding_count")
    findings_count = outcome.get("findings")
    if (
        type(reported_count) is not int
        or reported_count < 0
        or (reported is not None and reported_count != len(reported))
    ):
        errors.append("outcome.reported_finding_count_mismatch")

    for checker in successful:
        compact = _doctor_identity_set(
            checker.get("compact_findings"),
            "retrieval.invalid_compact_findings",
            errors,
        )
        compact_count = checker.get("compact_finding_count")
        if (
            type(compact_count) is not int
            or compact_count < 0
            or (compact is not None and compact_count != len(compact))
        ):
            errors.append("retrieval.compact_finding_count_mismatch")
        if compact is not None and reported is not None and compact != reported:
            errors.append("outcome.reported_findings_mismatch")
        if (
            type(findings_count) is not int
            or findings_count < 0
            or (compact is not None and findings_count != len(compact))
            or (reported is not None and findings_count != len(reported))
        ):
            errors.append("outcome.finding_count_mismatch")
        if checker.get("status") == "clean" and compact:
            errors.append("retrieval.compact_finding_count_mismatch")
        if checker.get("status") == "findings" and compact == set():
            errors.append("retrieval.compact_finding_count_mismatch")


def _validate_terminal_doctor_discovery_contract(
    outcome,
    docs_actions,
    checker_actions,
    terminal_doctor_discovery,
    errors,
):
    if not terminal_doctor_discovery:
        return
    reported = _doctor_identity_set(
        outcome.get("reported_findings"),
        "outcome.invalid_reported_findings",
        errors,
    )
    discovery = docs_actions[0]
    invalid = bool(
        type(outcome) is not dict
        or set(outcome) != TERMINAL_DOCTOR_OUTCOME_FIELDS_V1
        or type(outcome.get("status")) is not str
        or outcome.get("status") != "incomplete"
        or outcome.get("read_only") is not True
        or type(outcome.get("files_changed")) is not int
        or outcome.get("files_changed") != 0
        or type(outcome.get("findings")) is not int
        or outcome.get("findings") != 0
        or type(outcome.get("answers")) is not list
        or any(type(answer) is not str for answer in outcome.get("answers", ()))
        or type(outcome.get("reported_finding_count")) is not int
        or outcome.get("reported_finding_count") != 0
        or type(outcome.get("reported_findings")) is not list
        or reported != set()
        or outcome.get("findings_exhaustive") is not False
        or type(outcome.get("scope")) is not str
        or _normalize_scope_evidence(outcome.get("scope")) != outcome.get("scope")
        or checker_actions
        or len(docs_actions) != 1
        or "compact_finding_count" in discovery
        or "compact_findings" in discovery
        or "compact_finding_count" in outcome
        or "compact_findings" in outcome
    )
    if invalid:
        errors.append("outcome.invalid_terminal_doctor_diagnosis")


def evaluate(receipt: Mapping) -> dict:
    """Return a deterministic PASS/FAIL result for a sanitized trajectory receipt."""
    receipt = _validate_public_artifact(receipt, "receipt")

    command = receipt.get("command")
    if not isinstance(command, str) or command not in MAX_DOCS_ACTIONS:
        raise ValueError("unsupported trajectory command")
    outcome = _require_mapping(receipt.get("outcome"), "outcome")
    retrieval = _require_mapping(receipt.get("retrieval"), "retrieval")
    usage = _require_mapping(receipt.get("usage"), "usage")
    presentation = _require_mapping(receipt.get("presentation"), "presentation")
    actions = retrieval.get("actions")
    if not isinstance(actions, list):
        raise ValueError("retrieval.actions must be an array")
    if any(not isinstance(item, Mapping) for item in actions):
        raise ValueError("retrieval.actions entries must be objects")
    for item in actions:
        for field in ("owner", "kind", "status"):
            value = item.get(field)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"action.{field} must be a non-empty string")

    errors = []
    warnings = []
    docs_actions = [item for item in actions if isinstance(item, Mapping) and item.get("owner") == "docs"]
    external_actions = [item for item in actions if isinstance(item, Mapping) and item.get("owner") != "docs"]
    external_repository_actions = [
        item
        for item in external_actions
        if "paths" in item or item.get("kind") in REPOSITORY_ACTION_KINDS
    ]
    checker_actions = [item for item in docs_actions if item.get("kind") == "checker"]
    declared_scope = _normalize_scope_evidence(outcome.get("scope"))
    terminal_doctor_discovery = bool(
        command == "doctor"
        and docs_actions
        and docs_actions[0].get("kind") == DOCTOR_DISCOVERY_KIND
        and docs_actions[0].get("status") in DOCTOR_DISCOVERY_TERMINAL_STATUSES
    )
    checker_runs = sum(
        _positive_int(item.get("count", 1), "action.count")
        for item in checker_actions
    )

    if terminal_doctor_discovery and outcome.get("status") != "incomplete":
        errors.append("outcome.discovery_not_incomplete")
    elif not terminal_doctor_discovery and outcome.get("status") != "complete":
        errors.append("outcome.incomplete")
    files_changed = _positive_int(outcome.get("files_changed"), "outcome.files_changed")
    if outcome.get("read_only") is not True or files_changed != 0:
        errors.append("safety.read_only_violation")
    answers = set(_string_array(outcome.get("answers", []), "outcome.answers"))
    if command == "map":
        for answer in sorted(REQUIRED_ANSWERS - answers):
            errors.append(f"outcome.missing_answer:{answer}")
    if command == "map":
        if presentation.get("tree") is not True:
            errors.append("presentation.missing_tree")
        tree_features = set(_string_array(presentation.get("tree_features", []), "presentation.tree_features"))
        for feature in sorted(REQUIRED_TREE_FEATURES - tree_features):
            errors.append(f"presentation.missing_tree_feature:{feature}")
    if presentation.get("plain_english") is not True:
        errors.append("presentation.not_plain_english")
    visible = "\n".join(_string_array(presentation.get("visible_diagnostics", []), "presentation.visible_diagnostics"))
    raw_exit_code_visible = presentation.get("raw_exit_code_visible", False)
    if not isinstance(raw_exit_code_visible, bool):
        raise ValueError("presentation.raw_exit_code_visible must be a boolean")
    if raw_exit_code_visible or _RAW_EXIT.search(visible):
        errors.append("presentation.raw_exit_code")
    _validate_health_meter(
        presentation,
        checker_actions,
        command,
        errors,
        terminal_doctor_discovery=terminal_doctor_discovery,
    )
    _validate_exhaustive_scope(
        outcome,
        checker_actions,
        command,
        declared_scope,
        errors,
    )
    _validate_doctor_scope(
        checker_actions,
        command,
        declared_scope,
        errors,
    )
    _validate_terminal_doctor_discovery_contract(
        outcome,
        docs_actions,
        checker_actions,
        terminal_doctor_discovery,
        errors,
    )
    _validate_doctor_finding_contract(outcome, checker_actions, command, errors)
    if external_repository_actions:
        errors.append("retrieval.external_repository_action")
    errors.extend(validate_route(command, docs_actions, scope=declared_scope))
    if any(item.get("status") == "failed-lookup" for item in external_actions):
        warnings.append("external.failed_lookup")

    responses = _positive_int(usage.get("responses"), "usage.responses")
    cumulative = _positive_int(usage.get("cumulative_input_tokens"), "usage.cumulative_input_tokens")
    cached = _positive_int(usage.get("cached_input_tokens"), "usage.cached_input_tokens")
    if responses == 0 or cached > cumulative:
        raise ValueError("invalid usage counters")
    metrics = {
        "docs_actions": len(docs_actions),
        "external_actions": len(external_actions),
        "checker_runs": checker_runs,
        "input_per_response": round(cumulative / responses),
        "uncached_input_tokens": cumulative - cached,
    }
    control = usage.get("paired_control")
    if control is None:
        warnings.append("usage.unpaired_host_baseline")
    else:
        control = _require_mapping(control, "usage.paired_control")
        control_responses = _positive_int(control.get("responses"), "paired_control.responses")
        control_input = _positive_int(control.get("cumulative_input_tokens"), "paired_control.cumulative_input_tokens")
        if control_responses == 0:
            raise ValueError("paired control must have at least one response")
        baseline = round(control_input / control_responses)
        metrics["paired_host_input_per_response"] = baseline
        metrics["input_per_response_delta"] = metrics["input_per_response"] - baseline

    return {
        "status": "FAIL" if errors else "PASS",
        "errors": errors,
        "warnings": warnings,
        "metrics": metrics,
    }


def validate_campaign(campaign: Mapping) -> None:
    campaign = _validate_public_artifact(campaign, "campaign")
    _validate_allowlist(campaign.get("commands"), "campaign.commands", ALLOWED_CAMPAIGN_COMMANDS)
    _validate_allowlist(campaign.get("fixtures"), "campaign.fixtures", ALLOWED_CAMPAIGN_FIXTURES)
    runs = _positive_int(campaign.get("max_runs"), "max_runs")
    if runs > MAX_RELEASE_RUNS:
        raise ValueError(f"release canaries have a maximum of {MAX_RELEASE_RUNS} runs")
    if campaign.get("approved") is not True:
        raise ValueError("release canaries require explicit approval")
    if campaign.get("retain_raw_traces") is not False:
        raise ValueError("public release canaries must not retain raw traces")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("receipt", type=Path)
    args = parser.parse_args(argv)
    try:
        receipt = json.loads(
            args.receipt.read_text(encoding="utf-8"),
            object_pairs_hook=_reject_duplicate_json_keys,
        )
        result = evaluate(receipt)
    except (OSError, json.JSONDecodeError, ValueError, OverflowError, RecursionError) as exc:
        print(json.dumps({"status": "INVALID", "error": str(exc)}, ensure_ascii=False))
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if result["status"] == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
