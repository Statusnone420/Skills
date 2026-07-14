"""Pure lifecycle authorization, transition, event, and disposition policy."""

import copy
import hashlib
import json
import re
from collections.abc import Mapping, Sequence

from .identity import event_fingerprint, event_id
from .knowledge import LOCAL_MAP_LIFECYCLE_SCHEMA_VERSION
from .memory import (
    MAX_FINDINGS_BYTES,
    PRIORITIES,
    operational_findings_digest,
    operational_state_digest,
)
from .paths import normalize_repo_relative


READ_ONLY_COMMANDS = frozenset({"doctor", "check", "map", "context", "audit", "classify"})
MUTATING_COMMANDS = frozenset({"init", "write", "update", "fix", "migrate", "cleanup"})
DISPOSITIONS = frozenset({"MIGRATED", "DEDUPLICATED", "ARCHIVED", "DISCARDED"})
INLINE_MANIFEST_BYTES = 32 * 1024
TRANSACTION_PREFIX = ".docs-txn-"
TRANSACTION_SCHEMA_VERSION = 2
TRANSACTION_POLICY_VERSION = "verified-closeout-v2"
LOCAL_MAP_SCHEMA_VERSION = LOCAL_MAP_LIFECYCLE_SCHEMA_VERSION

_HEX_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")
_CONTENT_DIGEST = re.compile(r"^sha256-(?:text|bytes):[0-9a-f]{64}$")
_TRANSACTION_ID = re.compile(r"^TXN-[0-9A-F]{16}$")
_EVENT_ID = re.compile(r"^EVT-[0-9A-F]{8}(?:[0-9A-F]{4})*$")


def _sequence(value, name):
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        raise ValueError(f"{name} must be an array")
    return value


def _mapping(value, name):
    if not isinstance(value, Mapping):
        raise ValueError(f"{name} must be an object")
    return value


def _text(value, name):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")
    return value


def _canonical_bytes(value):
    try:
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
    except (TypeError, ValueError, RecursionError, OverflowError) as exc:
        raise ValueError("lifecycle payload is not canonical JSON") from exc


def _sha256(data):
    return "sha256:" + hashlib.sha256(data).hexdigest()


def _normalize_route(value, name):
    path, separator, anchor = _text(value, name).partition("#")
    normalized = normalize_repo_relative(path, name)
    if separator:
        if not anchor:
            raise ValueError(f"{name} anchor must not be empty")
        return f"{normalized}#{anchor}"
    return normalized


def _normalize_transaction_id(value):
    normalized = _text(value, "transaction ID").upper()
    if not _TRANSACTION_ID.fullmatch(normalized):
        raise ValueError("transaction ID is invalid")
    return normalized


def transition_finding(
    finding,
    target_status,
    *,
    priority=None,
    revalidation_invalidated=False,
    recurrence_fingerprint=None,
    prior_event=None,
    evidence_changed=False,
):
    """Apply exactly one allowed lifecycle transition without changing identity."""
    result = copy.deepcopy(dict(_mapping(finding, "finding")))
    current = _text(result.get("status"), "finding status")
    target = _text(target_status, "target status")
    priority_changed = priority is not None and priority != result.get("priority")
    if priority is not None:
        if priority not in PRIORITIES:
            raise ValueError("priority is invalid")
        result["priority"] = priority

    allowed = False
    if (current, target) in {("Proposed", "Approved"), ("Proposed", "Parked"), ("Approved", "Applied")}:
        allowed = True
    elif (current, target) == ("Approved", "Proposed"):
        allowed = revalidation_invalidated is True
    elif (current, target) == ("Applied", "Proposed"):
        allowed = (
            isinstance(recurrence_fingerprint, str)
            and recurrence_fingerprint == result.get("fingerprint")
            and isinstance(prior_event, str)
            and _EVENT_ID.fullmatch(prior_event) is not None
        )
        if allowed:
            result["prior_event"] = prior_event
    elif (current, target) == ("Parked", "Proposed"):
        allowed = evidence_changed is True or priority_changed
    if not allowed:
        raise ValueError(f"invalid finding transition {current} -> {target}")
    result["status"] = target
    return result


def select_persisted_findings(findings):
    """Return only lifecycle records which cannot be safely recomputed."""
    persisted = []
    for raw in _sequence(findings, "findings"):
        record = copy.deepcopy(dict(_mapping(raw, "finding")))
        status = record.get("status")
        priority = record.get("priority")
        origin = record.get("origin", "semantic")
        if status == "Applied":
            continue
        if origin == "deterministic":
            keep = status in {"Approved", "Parked"}
        else:
            keep = priority in {"P0", "P1"} or status in {"Approved", "Parked"}
        if keep:
            persisted.append(record)
    return sorted(persisted, key=lambda item: item["id"])


def preview_memory_compaction(state, findings, *, obsolete_ids=()):
    """Preview bounded compaction while preserving active and protected truth."""
    state = copy.deepcopy(dict(_mapping(state, "state")))
    findings = copy.deepcopy(dict(_mapping(findings, "findings payload")))
    records = list(_sequence(findings.get("findings"), "findings"))
    obsolete = set(_sequence(obsolete_ids, "obsolete IDs"))
    encoded = _canonical_bytes(findings)
    retained = []
    archive = []
    for record in records:
        identifier = record.get("id")
        protected = record.get("priority") in {"P0", "P1"} and record.get("status") != "Applied"
        if identifier in obsolete and not protected:
            archive.append(identifier)
        else:
            retained.append(identifier)
    return {
        "status": "memory-capacity" if len(encoded) > MAX_FINDINGS_BYTES else "within-capacity",
        "writes": 0,
        "measured_bytes": len(encoded),
        "capacity_bytes": MAX_FINDINGS_BYTES,
        "retained_finding_ids": sorted(retained),
        "archive_candidate_ids": sorted(archive),
        "protected_intent": state.get("protected_intent", []),
        "verified_documents": state.get("verified_documents", []),
    }


def _normalize_disposition(raw, index):
    item = dict(_mapping(raw, f"dispositions[{index}]"))
    required = {
        "item_id",
        "path",
        "section",
        "disposition",
        "reason",
        "source_digest",
        "recovery",
    }
    if not required.issubset(item):
        raise ValueError("disposition is missing required fields")
    path = normalize_repo_relative(item["path"], "disposition path")
    section = _text(item["section"], "disposition section")
    item_id = _text(item["item_id"], "disposition item_id")
    if item_id != f"{path}#{section}":
        raise ValueError("disposition item identity is inconsistent")
    outcome = _text(item["disposition"], "disposition outcome").upper()
    if outcome not in DISPOSITIONS:
        raise ValueError("disposition outcome is invalid")
    source_digest = _text(item["source_digest"], "disposition source digest").lower()
    if not _HEX_DIGEST.fullmatch(source_digest):
        raise ValueError("disposition source digest is invalid")
    recovery = dict(_mapping(item["recovery"], "disposition recovery"))
    if set(recovery) != {"kind", "path", "digest"}:
        raise ValueError("disposition recovery is invalid")
    recovery_path = _normalize_route(recovery["path"], "disposition recovery path")
    recovery_digest = _text(recovery["digest"], "disposition recovery digest").lower()
    if not _HEX_DIGEST.fullmatch(recovery_digest):
        raise ValueError("disposition recovery digest is invalid")
    normalized = {
        **item,
        "item_id": item_id,
        "path": path,
        "section": section,
        "disposition": outcome,
        "reason": _text(item["reason"], "disposition reason"),
        "source_digest": source_digest,
        "recovery": {
            "kind": _text(recovery["kind"], "disposition recovery kind"),
            "path": recovery_path,
            "digest": recovery_digest,
        },
    }
    if outcome in {"MIGRATED", "DEDUPLICATED", "ARCHIVED"}:
        normalized["target"] = _normalize_route(item.get("target"), "disposition target")
    return normalized


def prepare_dispositions(
    event_id_value,
    dispositions,
    *,
    removed_items,
    git_available,
    hard_delete_approval=None,
    transaction_id=None,
):
    """Canonicalize complete dispositions and choose inline or external storage."""
    normalized = [_normalize_disposition(item, index) for index, item in enumerate(_sequence(dispositions, "dispositions"))]
    identifiers = [item["item_id"] for item in normalized]
    if len(identifiers) != len(set(identifiers)):
        raise ValueError("disposition item identities must be unique")
    expected = list(_sequence(removed_items, "removed items"))
    if len(expected) != len(set(expected)) or set(expected) != set(identifiers):
        raise ValueError("every removed item must have exactly one disposition")

    discarded = sorted(item["item_id"] for item in normalized if item["disposition"] == "DISCARDED")
    hard_delete = False
    if not git_available and discarded:
        if hard_delete_approval is None:
            for item in normalized:
                if item["disposition"] == "DISCARDED":
                    item["disposition"] = "ARCHIVED"
                    item["target"] = item["recovery"]["path"]
            discarded = []
        else:
            approval = dict(_mapping(hard_delete_approval, "hard-delete approval"))
            if set(approval) != {"accepted", "discarded_ids"} or approval["accepted"] is not True:
                raise ValueError("hard-delete approval is invalid")
            approved = list(_sequence(approval["discarded_ids"], "discarded IDs"))
            if approved != discarded:
                raise ValueError("hard-delete approval does not match exact discarded IDs")
            hard_delete = True

    normalized.sort(key=lambda item: item["item_id"])
    txid = _normalize_transaction_id(
        transaction_id
        or ("TXN-" + hashlib.sha256(_canonical_bytes(normalized)).hexdigest()[:16])
    )
    payload = {
        "schema_version": 1,
        "transaction_id": txid,
        "dispositions": normalized,
    }
    encoded = _canonical_bytes(payload)
    result = {
        "storage": "inline" if len(encoded) <= INLINE_MANIFEST_BYTES else "external",
        "canonical_bytes": len(encoded),
        "digest": _sha256(encoded),
        "transaction_id": txid,
        "dispositions": normalized,
    }
    if hard_delete:
        result.update(
            {
                "no_git_hard_delete_accepted": True,
                "discarded_ids": discarded,
            }
        )
    if result["storage"] == "external":
        result["bytes"] = encoded.decode("utf-8")
        if event_id_value is not None:
            result["path"] = f".diataxis/manifests/{event_id_value}.json"
    return result


def _normalized_approvals(approvals):
    normalized = []
    for raw in _sequence(approvals, "approvals"):
        value = dict(_mapping(raw, "approval"))
        if set(value) != {"id", "fingerprint"}:
            raise ValueError("approval fields are invalid")
        normalized.append({"id": _text(value["id"], "approval id"), "fingerprint": _text(value["fingerprint"], "approval fingerprint").lower()})
    normalized.sort(key=lambda value: value["id"])
    if len({item["id"] for item in normalized}) != len(normalized):
        raise ValueError("approval IDs must be unique")
    return normalized


def transaction_identity(installed_result_semantics):
    """Bind the complete nonvolatile semantics of one proposed installed result."""
    payload = dict(_mapping(installed_result_semantics, "installed result semantics"))
    return "TXN-" + hashlib.sha256(_canonical_bytes(payload)).hexdigest()[:16].upper()


def build_verified_event(
    event,
    *,
    transaction_id,
    dispositions=None,
    recurring_findings=(),
    starting_digests=None,
    state_semantic_digest=None,
    findings_digest=None,
    transaction_targets=(),
    approval_bindings=(),
    local_map_digest=None,
    protected_preview_digest=None,
):
    """Build one compact event whose ID binds all nonvolatile closeout semantics."""
    transaction_id = _normalize_transaction_id(transaction_id)
    result = copy.deepcopy(dict(_mapping(event, "event")))
    result.pop("event_id", None)
    result["transaction_id"] = transaction_id
    if starting_digests is not None:
        result["starting_digests"] = dict(sorted(starting_digests.items()))
    if state_semantic_digest is not None:
        result["state_semantic_digest"] = state_semantic_digest
    if findings_digest is not None:
        result["findings_digest"] = findings_digest
    if transaction_targets:
        result["transaction_targets"] = sorted(transaction_targets)
    if approval_bindings:
        result["approval_bindings"] = _normalized_approvals(approval_bindings)
    for field, digest in (
        ("local_map_digest", local_map_digest),
        ("protected_preview_digest", protected_preview_digest),
    ):
        if digest is not None:
            digest = _text(digest, field).lower()
            if not _HEX_DIGEST.fullmatch(digest):
                raise ValueError(f"{field} is invalid")
            result[field] = digest
    recurrences = []
    for raw in _sequence(recurring_findings, "recurring findings"):
        record = dict(_mapping(raw, "recurring finding"))
        if set(record) != {"id", "fingerprint", "prior_event"}:
            raise ValueError("recurring finding fields are invalid")
        recurrences.append(record)
    if recurrences:
        result["recurrences"] = sorted(recurrences, key=lambda item: item["id"])
    if dispositions is not None:
        dispositions = dict(_mapping(dispositions, "dispositions"))
        result["disposition_digest"] = dispositions["digest"]
        if dispositions["storage"] == "inline":
            result["dispositions"] = copy.deepcopy(dispositions["dispositions"])
        if dispositions.get("no_git_hard_delete_accepted"):
            result["no_git_hard_delete_accepted"] = True
            result["discarded_ids"] = list(dispositions["discarded_ids"])
        if dispositions["storage"] == "external":
            # The digest is semantic; the path is derived from the eventual EVT ID.
            result["manifest_digest"] = dispositions["digest"]

    fingerprint = event_fingerprint(result)
    identifier = event_id(fingerprint)
    result["event_id"] = identifier
    if dispositions is not None and dispositions["storage"] == "external":
        result["manifest"] = {
            "path": f".diataxis/manifests/{identifier}.json",
            "digest": dispositions["digest"],
        }
    return result


def state_semantic_digest(state):
    return operational_state_digest(dict(_mapping(state, "state")))


def findings_digest(findings):
    return operational_findings_digest(findings)


__all__ = (
    "INLINE_MANIFEST_BYTES",
    "LOCAL_MAP_SCHEMA_VERSION",
    "MUTATING_COMMANDS",
    "READ_ONLY_COMMANDS",
    "TRANSACTION_PREFIX",
    "TRANSACTION_POLICY_VERSION",
    "TRANSACTION_SCHEMA_VERSION",
    "build_verified_event",
    "findings_digest",
    "prepare_dispositions",
    "preview_memory_compaction",
    "select_persisted_findings",
    "state_semantic_digest",
    "transaction_identity",
    "transition_finding",
)
