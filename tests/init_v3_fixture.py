import base64
import copy
import hashlib


def sha256_digest(data):
    return "sha256:" + hashlib.sha256(data).hexdigest()


def whole_file_disposition(
    path="docs/README.md",
    data=b"# Documentation\n",
    *,
    disposition="RETAIN",
    **variant,
):
    item = {
        "item_id": f"{path}#<whole-file>",
        "path": path,
        "section": {"kind": "whole-file"},
        "disposition": disposition,
        "reason": "Approval-bound whole-file disposition.",
        "source_digest": sha256_digest(data),
    }
    item.update(variant)
    return item


def document_change(
    operation="CREATE",
    path="docs/new.md",
    data=b"# New document\n",
    *,
    source_item_ids=None,
):
    change = {
        "operation": operation,
        "path": path,
        "reason": "Approval-bound documentation change.",
        "source_item_ids": [] if source_item_ids is None else source_item_ids,
    }
    if operation in {"CREATE", "REPLACE"}:
        change["content_base64"] = base64.b64encode(data).decode("ascii")
    return change


def evidence_v3(*, dispositions=None):
    map_bytes = len(b"# Documentation\n")
    return {
        "skill_version": "0.3.0",
        "selected_scope": "docs",
        "inspected_scope": "docs",
        "map_path": "docs/README.md",
        "current_truth_routes": [],
        "rubric_version": 3,
        "score_before": 83,
        "score_after": 83,
        "rubric_status": "needs-attention",
        "cold_paths": [],
        "verified_documents": [],
        "protected_intent": [],
        "hot_path_bytes": {
            "before": {
                "value": map_bytes,
                "unit": "bytes",
                "provenance": [
                    {
                        "route": "docs/README.md",
                        "bytes": map_bytes,
                        "source": "filesystem-stat",
                    }
                ],
            },
            "after": {
                "value": map_bytes,
                "unit": "bytes",
                "provenance": [
                    {
                        "route": "docs/README.md",
                        "bytes": map_bytes,
                        "source": "filesystem-stat",
                    }
                ],
            },
        },
        "trust_coverage": {
            "status": "unverified",
            "numerator": 0,
            "denominator": 0,
            "routes": [],
        },
        "findings": {"schema_version": 1, "findings": []},
        "dispositions": copy.deepcopy(
            dispositions
            if dispositions is not None
            else [whole_file_disposition()]
        ),
        "local_map": None,
        "event": {
            "kind": "init",
            "completed_at": "2026-07-15T12:00:00Z",
            "skill_version": "0.3.0",
            "approved_ids": [],
            "score_before": 83,
            "score_after": 83,
            "reason": "Adopt the complete verified documentation corpus.",
            "summary": "Initialize operational documentation state.",
        },
        "approvals": [],
        "source_changes": {
            "agents_orientation": False,
            "local_map_ignore": False,
        },
    }


def empty_adoption_evidence_v3(
    map_path="README.md",
    map_bytes=len(b"# Adopted documentation\n"),
):
    evidence = evidence_v3(dispositions=[])
    evidence.update(
        {
            "selected_scope": ".",
            "inspected_scope": ".",
            "map_path": map_path,
            "current_truth_routes": [map_path],
            "cold_paths": [],
            "hot_path_bytes": {
                "before": {"value": 0, "unit": "bytes", "provenance": []},
                "after": {
                    "value": map_bytes,
                    "unit": "bytes",
                    "provenance": [
                        {
                            "route": map_path,
                            "bytes": map_bytes,
                            "source": "filesystem-stat",
                        }
                    ],
                },
            },
            "trust_coverage": {
                "status": "verified",
                "numerator": 1,
                "denominator": 1,
                "routes": [
                    {
                        "route": map_path,
                        "verified": True,
                        "freshness": "fresh",
                        "sources": ["state:initialized-hot-path"],
                    }
                ],
            },
        }
    )
    return evidence


def request_v3(
    operation="preview",
    *,
    evidence=None,
    document_changes=None,
    hard_delete_acceptance=None,
    approval=None,
):
    request = {
        "schema_version": 3,
        "operation": operation,
        "evidence": copy.deepcopy(evidence if evidence is not None else evidence_v3()),
        "document_changes": copy.deepcopy(
            [] if document_changes is None else document_changes
        ),
        "hard_delete_acceptance": copy.deepcopy(hard_delete_acceptance),
    }
    if operation == "apply":
        request["approval"] = (
            approval
            if approval is not None
            else "Approve $docs init preview INIT-ABCDEF012345 with manifest "
            + "a" * 64
        )
    return request
