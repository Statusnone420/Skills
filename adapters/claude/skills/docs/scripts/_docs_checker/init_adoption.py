"""Engine-owned, deterministic first-run adoption for Diataxis Docs."""

import copy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

from .discovery import discover_init_scope, scan_selected_document_corpus
from .health import HEALTH_RUBRIC_VERSION, health_summary
from .init_closeout import (
    InitCloseoutError,
    apply_response,
    prepare_initialization_closeout,
    preview_response,
    validate_public_request,
)
from .scan import discover_markdown, scan_documents


SKILL_VERSION = "0.1.1"


def canonical_request_bytes(value):
    """Serialize one adoption receipt deterministically."""
    return (
        json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=False,
        )
        + "\n"
    ).encode("utf-8")


def _completed_at_now():
    return datetime.now(timezone.utc).replace(microsecond=0).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )


def _raw_digest(path):
    return "sha256:" + hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _select_scope(root, explicit_scope=None):
    discovery = discover_init_scope(root, explicit_scope=explicit_scope)
    selected_scope = discovery.get("selected_scope")
    if selected_scope is None:
        selection_reason = discovery.get("selection_reason")
        if selection_reason == "choice-required":
            raise InitCloseoutError(
                "waiting",
                "scope-choice-required",
                "discovery",
            )
        raise InitCloseoutError(
            "waiting",
            "discovery-incomplete",
            "discovery",
        )
    return selected_scope


def _map_path(paths, selected_scope):
    preferred = (
        "README.md"
        if selected_scope == "."
        else f"{selected_scope}/README.md"
    )
    by_identity = {path.casefold(): path for path in paths}
    return by_identity.get(preferred.casefold(), paths[0])


def _structural_health(root, selected_scope, map_path):
    scoped, findings, applied_prunes = discover_markdown(root, selected_scope)
    findings, _, measurements = scan_documents(
        root,
        map_path,
        [map_path],
        scoped,
        findings,
        applied_prunes,
    )
    return health_summary(measurements, findings=findings)


def build_adoption_request(
    root,
    *,
    explicit_scope=None,
    completed_at=None,
):
    """Build one strict schema-3, all-unchanged adoption request."""
    root = Path(root).absolute()
    selected_scope = _select_scope(root, explicit_scope)
    corpus = scan_selected_document_corpus(
        root,
        selected_scope,
        "selected-scope-exact",
    )
    if corpus.get("complete") is not True:
        boundary = corpus.get("boundary") or {}
        raise InitCloseoutError(
            "waiting",
            boundary.get("classification", "incomplete-corpus"),
            "corpus-scan",
        )
    paths = corpus["paths"]
    if not paths:
        raise InitCloseoutError(
            "waiting",
            "shared-documentation-required",
            "corpus-scan",
        )

    map_path = _map_path(paths, selected_scope)
    health_scope = selected_scope
    if selected_scope != ".":
        scope_depth = len(Path(selected_scope).parts)
        health_scope = Path(*Path(paths[0]).parts[:scope_depth]).as_posix()
    health = _structural_health(root, health_scope, map_path)
    dispositions = [
        {
            "item_id": f"{relative}#<whole-file>",
            "path": relative,
            "section": {"kind": "whole-file"},
            "disposition": "RETAIN",
            "reason": "Init will leave this tracked document unchanged.",
            "source_digest": _raw_digest(root / relative),
        }
        for relative in paths
    ]
    map_bytes = (root / map_path).stat().st_size
    hot_path = {
        "value": map_bytes,
        "unit": "bytes",
        "provenance": [
            {
                "route": map_path,
                "bytes": map_bytes,
                "source": "filesystem-stat",
            }
        ],
    }
    evidence = {
        "skill_version": SKILL_VERSION,
        "selected_scope": selected_scope,
        "inspected_scope": selected_scope,
        "map_path": map_path,
        "current_truth_routes": [],
        "rubric_version": HEALTH_RUBRIC_VERSION,
        "score_before": health["percentage"],
        "score_after": health["percentage"],
        "rubric_status": health["structure_status"],
        "cold_paths": [],
        "verified_documents": [],
        "protected_intent": [],
        "hot_path_bytes": {
            "before": copy.deepcopy(hot_path),
            "after": copy.deepcopy(hot_path),
        },
        "trust_coverage": {
            "status": "unverified",
            "numerator": 0,
            "denominator": 0,
            "routes": [],
        },
        "findings": {"schema_version": 1, "findings": []},
        "dispositions": dispositions,
        "local_map": None,
        "event": {
            "kind": "init",
            "completed_at": completed_at or _completed_at_now(),
            "skill_version": SKILL_VERSION,
            "approved_ids": [],
            "score_before": health["percentage"],
            "score_after": health["percentage"],
            "reason": "Adopt the complete tracked documentation corpus.",
            "summary": "Initialize documentation memory without changing existing documents.",
        },
        "approvals": [],
        "source_changes": {
            "agents_orientation": False,
            "local_map_ignore": False,
        },
    }
    request = validate_public_request(
        {
            "schema_version": 3,
            "operation": "preview",
            "evidence": evidence,
            "document_changes": [],
            "hard_delete_acceptance": None,
        },
        "preview",
    )
    return request, health


def adoption_preview(root, *, explicit_scope=None, completed_at=None):
    request, health = build_adoption_request(
        root,
        explicit_scope=explicit_scope,
        completed_at=completed_at,
    )
    prepared = prepare_initialization_closeout(root, request)
    response = preview_response(prepared)
    response.update(
        {
            "handling_summary": {
                "left_unchanged": prepared["disposition_summary"].get(
                    "RETAIN", 0
                )
            },
            "score_receipt": {
                "percentage": health["percentage"],
                "status": health["structure_status"],
                "categories": copy.deepcopy(health["categories"]),
            },
            "operational_targets": sorted(prepared["plan"]["targets"]),
            "milestones": [
                "discovery",
                "evidence complete",
                "preview ready",
                "waiting for exact approval",
            ],
        }
    )
    return request, response


def adoption_apply(root, receipt_request, approval):
    receipt_request = validate_public_request(receipt_request, "preview")
    evidence = receipt_request["evidence"]
    fresh_request, _ = build_adoption_request(
        root,
        explicit_scope=evidence["selected_scope"],
        completed_at=evidence["event"]["completed_at"],
    )
    if canonical_request_bytes(fresh_request) != canonical_request_bytes(
        receipt_request
    ):
        raise InitCloseoutError(
            "stale-preview",
            "adoption-receipt-drift",
            "approval-revalidation",
        )
    request = copy.deepcopy(receipt_request)
    request.update(operation="apply", approval=approval)
    request = validate_public_request(request, "apply")
    prepared = prepare_initialization_closeout(root, request)
    response = apply_response(root, prepared, approval)
    if response.get("status") == "applied":
        response["milestones"] = [
            "approval revalidation",
            "apply/staging",
            "verification",
            "completed",
        ]
    return response


__all__ = (
    "SKILL_VERSION",
    "adoption_apply",
    "adoption_preview",
    "build_adoption_request",
    "canonical_request_bytes",
)
