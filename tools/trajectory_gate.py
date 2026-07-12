"""Validate sanitized, host-neutral Diátaxis Docs trajectory receipts."""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path

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
MAX_DOCS_ACTIONS = {"map": 4, "check": 3, "context": 4, "doctor": 8}
MAX_RELEASE_RUNS = 12
CHECKER_SUCCESS_STATUSES = {"clean", "findings"}
BROAD_RETRIEVAL_KINDS = {"repo-wide-search", "inventory", "name-only-inventory", "recursive-inventory"}
_ABSOLUTE_PATH = re.compile(
    r"(?i)(?:"
    r"\b[A-Z]:[\\/]"
    r"|(?<![A-Za-z0-9/:])(?:\\\\|//)[^\\/\s]+[\\/][^\\/\s]+"
    r"|(?<![A-Za-z0-9/:])/(?![/\s])[^\s]*"
    r"|\bfile:///[^\s]+"
    r"|\b[A-Za-z][A-Za-z0-9+.-]*:(?=/[^\s/])[^\s]*"
    r")"
)
_SECRET_KEY = re.compile(r"(?i)(?:^|[_-])(?:api[_-]?key|token|secret|password|credential|private[_-]?key)(?:$|[_-])")
_SECRET_VALUE = re.compile(r"(?i)(?:\b(?:sk|rk|ghp|github_pat|xox[baprs]-)[a-z0-9_-]{8,}\b|bearer\s+[a-z0-9._-]{12,})")
_PRIVATE_KEY = re.compile(r"(?i)(?:hidden[_-]?reasoning|chain[_-]?of[_-]?thought|reasoning[_-]?content|session[_-]?id)")
_RAW_EXIT = re.compile(r"(?i)\b(?:exit(?:ed)?(?:\s+with)?(?:\s+(?:code|status))?|return\s+code)\s*[:=]?\s*[1-9]\d*\b")


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
        if kind == "key" and (_SECRET_KEY.search(text) or _PRIVATE_KEY.search(text)):
            raise ValueError(f"public trajectory receipt contains a private key at {location}")
        if kind == "key" and _ABSOLUTE_PATH.search(text):
            raise ValueError(f"public trajectory receipt contains private material at {location}")
        if kind == "value" and (_ABSOLUTE_PATH.search(text) or _SECRET_VALUE.search(text)):
            raise ValueError(f"public trajectory receipt contains private material at {location}")


def _require_mapping(value, name):
    if not isinstance(value, Mapping):
        raise ValueError(f"{name} must be an object")
    return value


def _positive_int(value, name):
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise ValueError(f"{name} must be a non-negative integer")
    return value


def _string_array(value, name):
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise ValueError(f"{name} must be an array of strings")
    return value


def evaluate(receipt: Mapping) -> dict:
    """Return a deterministic PASS/FAIL result for a sanitized trajectory receipt."""
    _require_mapping(receipt, "receipt")
    _validate_public(receipt)
    if receipt.get("schema_version") != 1 or receipt.get("visibility") != "public-sanitized":
        raise ValueError("unsupported public trajectory receipt schema")

    command = receipt.get("command")
    if command not in MAX_DOCS_ACTIONS:
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

    errors = []
    warnings = []
    docs_actions = [item for item in actions if isinstance(item, Mapping) and item.get("owner") == "docs"]
    external_actions = [item for item in actions if isinstance(item, Mapping) and item.get("owner") != "docs"]
    checker_actions = [item for item in docs_actions if item.get("kind") == "checker"]
    checker_runs = sum(
        _positive_int(item.get("count", 1), "action.count")
        for item in checker_actions
    )
    checker_failed = any(
        not isinstance(item.get("status"), str) or item.get("status") not in CHECKER_SUCCESS_STATUSES
        for item in checker_actions
    )

    if outcome.get("read_only") is not True or outcome.get("files_changed") != 0:
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
    if presentation.get("raw_exit_code_visible") is True or _RAW_EXIT.search(visible):
        errors.append("presentation.raw_exit_code")
    docs_action_budget = MAX_DOCS_ACTIONS[command]
    if command == "map" and not any(
        item.get("kind") == "read-map" and item.get("status") == "missing"
        for item in docs_actions
    ):
        docs_action_budget = 3
    if len(docs_actions) > docs_action_budget:
        errors.append("retrieval.docs_action_budget")
    if command == "map" and not any(item.get("kind") == "read-map" for item in docs_actions):
        errors.append("retrieval.missing_map_read")
    if command in {"context", "map", "check"} and any(
        isinstance(item.get("kind"), str) and item.get("kind") in BROAD_RETRIEVAL_KINDS
        for item in docs_actions
    ):
        errors.append("retrieval.broad_action")
    if checker_runs > 1:
        errors.append("retrieval.repeated_checker")
    if command in {"map", "check"} and checker_runs == 0:
        errors.append("retrieval.missing_checker")
    if command in {"map", "check"} and checker_failed:
        errors.append("retrieval.checker_failed")
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
    _require_mapping(campaign, "campaign")
    _validate_public(campaign)
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
        receipt = json.loads(args.receipt.read_text(encoding="utf-8"))
        result = evaluate(receipt)
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        print(json.dumps({"status": "INVALID", "error": str(exc)}, ensure_ascii=False))
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if result["status"] == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
