"""Pure lifecycle authorization, transition, event, and disposition policy."""

import copy
import hashlib
import json
import re
import unicodedata
from collections.abc import Mapping, Sequence

from .formats import is_document_path
from .identity import event_fingerprint, event_id
from .knowledge import LOCAL_MAP_LIFECYCLE_SCHEMA_VERSION
from .memory import (
    MAX_FINDINGS_BYTES,
    MAX_MANIFEST_BYTES,
    PRIORITIES,
    normalize_corpus_v3,
    normalize_document_results_v3,
    operational_findings_digest,
    operational_state_digest,
)
from .paths import normalize_repo_relative, shared_text_exposes_route


READ_ONLY_COMMANDS = frozenset({"doctor", "check", "map", "context", "audit", "classify"})
MUTATING_COMMANDS = frozenset({"init", "write", "update", "fix", "migrate", "cleanup"})
DISPOSITIONS = frozenset(
    {"RETAIN", "MIGRATED", "DEDUPLICATED", "ARCHIVED", "DISCARDED"}
)
INLINE_MANIFEST_BYTES = 32 * 1024
INLINE_MANIFEST_ITEMS = 100
LEGACY_DISPOSITION_SCHEMA_VERSION = 1
INIT_DISPOSITION_SCHEMA_VERSION = 3
TRANSACTION_PREFIX = ".docs-txn-"
TRANSACTION_SCHEMA_VERSION = 3
TRANSACTION_POLICY_VERSION = "init-closeout-v3"
LOCAL_MAP_SCHEMA_VERSION = LOCAL_MAP_LIFECYCLE_SCHEMA_VERSION

_HEX_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")
_TRANSACTION_DIGEST = re.compile(r"^sha256:(?:[0-9a-f]{64}|ABSENT)$")
_CONTENT_DIGEST = re.compile(r"^sha256-(?:text|bytes):[0-9a-f]{64}$")
_TRANSACTION_ID = re.compile(r"^TXN-[0-9A-F]{16}$")
_EVENT_ID = re.compile(r"^EVT-[0-9A-F]{8}(?:[0-9A-F]{4})*$")
_HEX_IDENTITY = re.compile(r"^[0-9a-f]{64}$")
_SECTION_V3_FIELDS = frozenset(
    {
        "kind",
        "level",
        "heading_path",
        "occurrence",
        "start_byte",
        "end_byte",
        "raw_span_digest",
    }
)


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


def _normalize_init_disposition_reason(value):
    reason = _text(value, "disposition reason")
    if len(reason.encode("utf-8")) > 512:
        raise ValueError("disposition reason exceeds capacity")
    if shared_text_exposes_route(reason):
        raise ValueError("disposition reason exposes a private or unsafe route")
    return reason


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


def _enforce_init_manifest_capacity(encoded):
    if not isinstance(encoded, bytes):
        raise ValueError("initialization manifest bytes are invalid")
    if len(encoded) > MAX_MANIFEST_BYTES:
        raise ValueError("initialization manifest exceeds capacity")
    return encoded


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


def _reject_init_local_route(route, command):
    path = route.partition("#")[0]
    if command == "init" and path.split("/", 1)[0].casefold() == ".local":
        raise ValueError("initialization disposition must not expose local-only routes")


def _normalize_init_recovery_v3(value):
    recovery = dict(_mapping(value, "disposition recovery"))
    kind = recovery.get("kind")
    expected = {
        "git": {"kind", "commit", "blob", "digest"},
        "archive": {"kind", "mode", "path", "digest"},
        "hard-delete-request": {"kind"},
        "accepted-hard-delete": {
            "kind",
            "discard_set_id",
            "acceptance_digest",
        },
    }.get(kind)
    if expected is None or set(recovery) != expected:
        raise ValueError("disposition recovery is invalid")
    if kind == "git":
        object_id = re.compile(r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$")
        if (
            object_id.fullmatch(_text(recovery["commit"], "recovery commit")) is None
            or object_id.fullmatch(_text(recovery["blob"], "recovery blob")) is None
        ):
            raise ValueError("disposition Git recovery is invalid")
        if _HEX_DIGEST.fullmatch(_text(recovery["digest"], "recovery digest")) is None:
            raise ValueError("disposition recovery digest is invalid")
    elif kind == "archive":
        if recovery["mode"] not in {"existing", "planned"}:
            raise ValueError("disposition archive recovery mode is invalid")
        recovery["path"] = _normalize_route(recovery["path"], "recovery path")
        _reject_init_local_route(recovery["path"], "init")
        if _HEX_DIGEST.fullmatch(_text(recovery["digest"], "recovery digest")) is None:
            raise ValueError("disposition recovery digest is invalid")
    elif kind == "accepted-hard-delete":
        if re.fullmatch(r"DISCARD-[0-9A-F]{16}", _text(recovery["discard_set_id"], "discard set")) is None:
            raise ValueError("accepted hard-delete set is invalid")
        if _HEX_DIGEST.fullmatch(_text(recovery["acceptance_digest"], "acceptance digest")) is None:
            raise ValueError("accepted hard-delete digest is invalid")
    return recovery


def _normalize_section_heading_v3(value):
    if type(value) is not str:
        raise ValueError("section heading path is invalid")
    normalized = " ".join(unicodedata.normalize("NFC", value).split()).casefold()
    if value != normalized:
        raise ValueError("section heading path is not normalized")
    return value


def _normalize_section_v3(value, path):
    if type(value) is not dict or set(value) != _SECTION_V3_FIELDS:
        raise ValueError("initialization section fields are invalid")
    section = dict(value)
    if section["kind"] != "atx-section-v1":
        raise ValueError("initialization section kind is invalid")
    level = section["level"]
    occurrence = section["occurrence"]
    start = section["start_byte"]
    end = section["end_byte"]
    if type(level) is not int or not 1 <= level <= 6:
        raise ValueError("initialization section level is invalid")
    heading_path = section["heading_path"]
    if (
        type(heading_path) is not list
        or not heading_path
        or len(heading_path) > level
    ):
        raise ValueError("initialization section heading path is invalid")
    section["heading_path"] = [
        _normalize_section_heading_v3(component) for component in heading_path
    ]
    if type(occurrence) is not int or occurrence < 1:
        raise ValueError("initialization section occurrence is invalid")
    if (
        type(start) is not int
        or type(end) is not int
        or start < 0
        or end <= start
    ):
        raise ValueError("initialization section byte span is invalid")
    raw_span_digest = section["raw_span_digest"]
    if (
        type(raw_span_digest) is not str
        or _HEX_DIGEST.fullmatch(raw_span_digest) is None
    ):
        raise ValueError("initialization section span digest is invalid")
    expected_item_id = (
        "SEC-"
        + hashlib.sha256(
            _canonical_bytes({"path": path, "section": section})
        ).hexdigest()[:24].upper()
    )
    return section, expected_item_id


def _normalize_section_markdown_route_v3(value, name):
    if type(value) is not str or "#" in value:
        raise ValueError(f"{name} is invalid")
    route = normalize_repo_relative(value, name)
    first = route.split("/", 1)[0].casefold()
    if (
        route != value
        or not is_document_path(route)
        or first in {".diataxis", ".local"}
        or route.casefold() == "agents.md"
    ):
        raise ValueError(f"{name} is invalid")
    return route


def _normalize_init_section_disposition_v3(item, path, section):
    common = {
        "item_id",
        "path",
        "section",
        "disposition",
        "reason",
        "source_digest",
        "recovery",
    }
    outcome = item.get("disposition")
    if type(outcome) is not str:
        raise ValueError("initialization section disposition is invalid")
    fields = {
        "MIGRATED": common | {"target"},
        "DEDUPLICATED": common | {"target", "target_digest"},
        "ARCHIVED": common | {"target"},
        "DISCARDED": common,
    }.get(outcome)
    if fields is None or set(item) != fields:
        raise ValueError("initialization section disposition fields are invalid")
    path = _normalize_section_markdown_route_v3(path, "disposition path")
    section, expected_item_id = _normalize_section_v3(section, path)
    item_id = item.get("item_id")
    if type(item_id) is not str or item_id != expected_item_id:
        raise ValueError("section disposition item identity is inconsistent")
    reason = _normalize_init_disposition_reason(item["reason"])
    source_digest = item["source_digest"]
    if type(source_digest) is not str or _HEX_DIGEST.fullmatch(source_digest) is None:
        raise ValueError("disposition source digest is invalid")
    raw_recovery = item["recovery"]
    if type(raw_recovery) is not dict:
        raise ValueError("section disposition recovery is invalid")
    recovery = _normalize_init_recovery_v3(raw_recovery)
    if recovery["kind"] not in {"git", "archive"}:
        raise ValueError("section disposition recovery must be Git or archive")
    if recovery["kind"] == "archive":
        if raw_recovery["path"] != recovery["path"]:
            raise ValueError("section recovery path is not canonical")
        recovery["path"] = _normalize_section_markdown_route_v3(
            recovery["path"], "section recovery path"
        )
        if recovery["path"].casefold() == path.casefold():
            raise ValueError("section recovery path must differ from its source")
    normalized = {
        **item,
        "item_id": item_id,
        "path": path,
        "section": section,
        "disposition": outcome,
        "reason": reason,
        "source_digest": source_digest,
        "recovery": recovery,
    }
    if "target" in fields:
        target = _normalize_section_markdown_route_v3(
            item["target"], "section disposition target"
        )
        if target.casefold() == path.casefold():
            raise ValueError("section disposition target must differ from its source")
        normalized["target"] = target
    if "target_digest" in fields:
        target_digest = item["target_digest"]
        if type(target_digest) is not str or _HEX_DIGEST.fullmatch(target_digest) is None:
            raise ValueError("section disposition target digest is invalid")
        normalized["target_digest"] = target_digest
    return normalized


def _normalize_init_disposition_v3(raw, index):
    item = dict(_mapping(raw, f"dispositions[{index}]"))
    common = {
        "item_id",
        "path",
        "section",
        "disposition",
        "reason",
        "source_digest",
    }
    outcome = item.get("disposition")
    fields = {
        "RETAIN": common,
        "MIGRATED": common | {"target", "recovery"},
        "DEDUPLICATED": common | {"target", "target_digest", "recovery"},
        "ARCHIVED": common | {"target", "recovery"},
        "DISCARDED": common | {"recovery"},
    }.get(outcome)
    if fields is None or set(item) != fields:
        raise ValueError("initialization disposition fields are invalid")
    path = normalize_repo_relative(item["path"], "disposition path")
    _reject_init_local_route(path, "init")
    section = dict(_mapping(item["section"], "disposition section"))
    if section.get("kind") == "atx-section-v1":
        if type(item["path"]) is not str or item["path"] != path:
            raise ValueError("section disposition path is not canonical")
        if type(item["section"]) is not dict:
            raise ValueError("initialization section fields are invalid")
        return _normalize_init_section_disposition_v3(item, path, section)
    if section != {"kind": "whole-file"}:
        raise ValueError("initialization base disposition section is invalid")
    item_id = _text(item["item_id"], "disposition item id")
    if item_id != f"{path}#<whole-file>":
        raise ValueError("disposition item identity is inconsistent")
    source_digest = _text(item["source_digest"], "disposition source digest")
    if _HEX_DIGEST.fullmatch(source_digest) is None:
        raise ValueError("disposition source digest is invalid")
    normalized = {
        **item,
        "path": path,
        "section": section,
        "reason": _normalize_init_disposition_reason(item["reason"]),
        "source_digest": source_digest,
    }
    if "target" in fields:
        normalized["target"] = _normalize_route(item["target"], "disposition target")
        _reject_init_local_route(normalized["target"], "init")
    if "target_digest" in fields:
        target_digest = _text(item["target_digest"], "target digest")
        if _HEX_DIGEST.fullmatch(target_digest) is None:
            raise ValueError("disposition target digest is invalid")
        normalized["target_digest"] = target_digest
    if "recovery" in fields:
        normalized["recovery"] = _normalize_init_recovery_v3(item["recovery"])
    return normalized


def _validate_init_section_disposition_set_v3(dispositions, git_available):
    bases = {}
    sections = {}
    section_targets = set()
    for item in dispositions:
        path_key = item["path"].casefold()
        if item["section"] == {"kind": "whole-file"}:
            bases.setdefault(path_key, []).append(item)
            continue
        sections.setdefault(path_key, []).append(item)

    for path_key, items in sections.items():
        matching_bases = bases.get(path_key, [])
        if (
            len(matching_bases) != 1
            or matching_bases[0]["path"] != items[0]["path"]
            or matching_bases[0]["disposition"] != "RETAIN"
        ):
            raise ValueError("section dispositions require one whole-file RETAIN base")
        base = matching_bases[0]
        expected_source_digest = base["source_digest"]
        expected_recovery = items[0]["recovery"]
        if expected_recovery["digest"] != expected_source_digest:
            raise ValueError("section recovery does not preserve the complete source")
        if not git_available and expected_recovery["kind"] != "archive":
            raise ValueError("no-Git section changes require archive recovery")
        ordered = sorted(
            items,
            key=lambda item: (
                item["section"]["start_byte"],
                item["section"]["end_byte"],
                item["item_id"],
            ),
        )
        prior_end = None
        for item in ordered:
            if item["source_digest"] != expected_source_digest:
                raise ValueError("section source digest does not match its whole file")
            if item["recovery"] != expected_recovery:
                raise ValueError("section recovery mismatch")
            start = item["section"]["start_byte"]
            if prior_end is not None and start < prior_end:
                raise ValueError("section disposition spans overlap")
            prior_end = item["section"]["end_byte"]
            if item["disposition"] in {"MIGRATED", "ARCHIVED"}:
                target_key = item["target"].casefold()
                if target_key in section_targets or target_key in bases:
                    raise ValueError("section disposition target is not unique and absent")
                section_targets.add(target_key)


def _normalize_disposition(raw, index, command):
    if command == "init":
        return _normalize_init_disposition_v3(raw, index)
    item = dict(_mapping(raw, f"dispositions[{index}]"))
    common = {
        "item_id",
        "path",
        "section",
        "disposition",
        "reason",
        "source_digest",
    }
    if not common.issubset(item):
        raise ValueError("disposition is missing required fields")
    path = normalize_repo_relative(item["path"], "disposition path")
    _reject_init_local_route(path, command)
    section = _text(item["section"], "disposition section")
    item_id = _text(item["item_id"], "disposition item_id")
    if item_id != f"{path}#{section}":
        raise ValueError("disposition item identity is inconsistent")
    outcome = _text(item["disposition"], "disposition outcome").upper()
    if outcome not in DISPOSITIONS:
        raise ValueError("disposition outcome is invalid")
    target_outcomes = {"MIGRATED", "DEDUPLICATED", "ARCHIVED"}
    if command == "init" and "target" in item and outcome not in target_outcomes:
        raise ValueError("disposition target is invalid for outcome")
    source_digest = _text(item["source_digest"], "disposition source digest").lower()
    if not _HEX_DIGEST.fullmatch(source_digest):
        raise ValueError("disposition source digest is invalid")
    normalized = {
        **item,
        "item_id": item_id,
        "path": path,
        "section": section,
        "disposition": outcome,
        "reason": _text(item["reason"], "disposition reason"),
        "source_digest": source_digest,
    }
    if outcome == "RETAIN":
        if command != "init":
            raise ValueError("RETAIN is allowed only for initialization")
        if set(item) != common:
            raise ValueError("RETAIN must not include recovery or target fields")
        return normalized

    if "recovery" not in item:
        raise ValueError("disposition is missing required fields")
    recovery = dict(_mapping(item["recovery"], "disposition recovery"))
    if set(recovery) != {"kind", "path", "digest"}:
        raise ValueError("disposition recovery is invalid")
    recovery_path = _normalize_route(recovery["path"], "disposition recovery path")
    _reject_init_local_route(recovery_path, command)
    recovery_digest = _text(recovery["digest"], "disposition recovery digest").lower()
    if not _HEX_DIGEST.fullmatch(recovery_digest):
        raise ValueError("disposition recovery digest is invalid")
    normalized["recovery"] = {
        "kind": _text(recovery["kind"], "disposition recovery kind"),
        "path": recovery_path,
        "digest": recovery_digest,
    }
    if outcome in target_outcomes:
        target = _normalize_route(item.get("target"), "disposition target")
        _reject_init_local_route(target, command)
        normalized["target"] = target
    return normalized


def prepare_dispositions(
    event_id_value,
    dispositions,
    *,
    removed_items,
    git_available,
    hard_delete_approval=None,
    transaction_id=None,
    command=None,
    approval_bindings=(),
    corpus_transition=None,
    document_results=None,
):
    """Canonicalize complete dispositions and choose inline or external storage."""
    command = None if command is None else _text(command, "lifecycle command").lower()
    normalized = [
        _normalize_disposition(item, index, command)
        for index, item in enumerate(_sequence(dispositions, "dispositions"))
    ]
    identifiers = [item["item_id"] for item in normalized]
    if len(identifiers) != len(set(identifiers)):
        raise ValueError("disposition item identities must be unique")
    if command == "init":
        _validate_init_section_disposition_set_v3(normalized, git_available)
    expected = list(_sequence(removed_items, "removed items"))
    removed_identifiers = {
        item["item_id"] for item in normalized if item["disposition"] != "RETAIN"
    }
    if len(expected) != len(set(expected)) or set(expected) != removed_identifiers:
        raise ValueError("every removed item must have exactly one disposition")

    discarded = sorted(
        item["item_id"]
        for item in normalized
        if item["disposition"] == "DISCARDED"
        and (
            command != "init"
            or item["section"] == {"kind": "whole-file"}
        )
    )
    hard_delete = False
    if not git_available and discarded:
        if hard_delete_approval is None:
            for item in normalized:
                if (
                    item["disposition"] == "DISCARDED"
                    and (
                        command != "init"
                        or item["section"] == {"kind": "whole-file"}
                    )
                ):
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
    if command == "init":
        schema_version = INIT_DISPOSITION_SCHEMA_VERSION
        approval_identity = _approval_identity(approval_bindings)
        corpus_transition = dict(_mapping(corpus_transition, "corpus transition"))
        if set(corpus_transition) != {"starting", "result"}:
            raise ValueError("initialization corpus transition is invalid")
        corpus_transition = {
            "starting": normalize_corpus_v3(
                corpus_transition["starting"], name="starting corpus"
            ),
            "result": normalize_corpus_v3(
                corpus_transition["result"], name="result corpus"
            ),
        }
        document_results = normalize_document_results_v3(document_results)
        payload = {
            "schema_version": schema_version,
            "approval_identity": approval_identity,
            "corpus_transition": copy.deepcopy(corpus_transition),
            "dispositions": normalized,
            "document_results": copy.deepcopy(document_results),
        }
    else:
        schema_version = LEGACY_DISPOSITION_SCHEMA_VERSION
        approval_identity = None
        payload = {
            "schema_version": schema_version,
            "transaction_id": txid,
            "dispositions": normalized,
        }
    encoded = _canonical_bytes(payload)
    if command == "init":
        _enforce_init_manifest_capacity(encoded)
    digest = _sha256(encoded)
    result = {
        "storage": "external"
        if command == "init"
        else "inline"
        if len(encoded) <= INLINE_MANIFEST_BYTES
        else "external",
        "canonical_bytes": len(encoded),
        "digest": digest,
        "manifest_identity": digest.removeprefix("sha256:"),
        "schema_version": schema_version,
        "transaction_id": txid,
        "dispositions": normalized,
    }
    if approval_identity is not None:
        result["approval_identity"] = approval_identity
        result["corpus_transition"] = copy.deepcopy(corpus_transition)
        result["document_results"] = copy.deepcopy(document_results)
        result["document_results_digest"] = _sha256(
            _canonical_bytes(document_results)
        )
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
        normalized.append(
            {
                "id": _text(value["id"], "approval id"),
                "fingerprint": _text(
                    value["fingerprint"], "approval fingerprint"
                ).lower(),
            }
        )
    normalized.sort(key=lambda value: value["id"])
    if len({item["id"] for item in normalized}) != len(normalized):
        raise ValueError("approval IDs must be unique")
    return normalized


def _approval_identity(approvals):
    return hashlib.sha256(_canonical_bytes(_normalized_approvals(approvals))).hexdigest()


def transaction_identity(installed_result_semantics):
    """Bind the complete nonvolatile semantics of one proposed installed result."""
    payload = dict(_mapping(installed_result_semantics, "installed result semantics"))
    return "TXN-" + transaction_digest(payload).removeprefix("sha256:")[:16].upper()


def transaction_digest(installed_result_semantics):
    payload = dict(_mapping(installed_result_semantics, "installed result semantics"))
    return "sha256:" + hashlib.sha256(_canonical_bytes(payload)).hexdigest()


def _normalize_transaction_operations_v3(
    document_operations,
    control_operations,
    write_boundary,
):
    """Validate the disjoint document and control authorization unions."""
    write_boundary = normalize_repo_relative(write_boundary, "write boundary")
    documents = []
    controls = []
    identities = set()
    aggregate_result_bytes = 0
    destructive = 0
    raw_documents = list(_sequence(document_operations, "document operations"))
    raw_controls = list(_sequence(control_operations, "control operations"))
    if len(raw_documents) > 64:
        raise ValueError("document operations exceed capacity")

    def digest(value, name):
        value = _text(value, name)
        if not _TRANSACTION_DIGEST.fullmatch(value):
            raise ValueError(f"{name} is invalid")
        return value

    for index, raw in enumerate(raw_documents):
        name = f"document operations[{index}]"
        item = dict(_mapping(raw, name))
        operation = _text(item.get("operation"), f"{name}.operation")
        common = {
            "operation",
            "path",
            "role",
            "starting_digest",
            "result_digest",
            "source_item_ids",
            "recovery_binding",
        }
        expected = common | ({"result_bytes"} if operation in {"CREATE", "REPLACE"} else set())
        if operation not in {"CREATE", "REPLACE", "DELETE"} or set(item) != expected:
            raise ValueError(f"{name} fields are invalid")
        path = normalize_repo_relative(item["path"], f"{name}.path")
        path_key = path.casefold()
        boundary_key = write_boundary.casefold()
        if (
            not is_document_path(path)
            or path_key.split("/", 1)[0] in {".diataxis", ".local"}
            or path_key == "agents.md"
            or (
                boundary_key != "."
                and path_key != boundary_key
                and not path_key.startswith(boundary_key + "/")
            )
            or path_key in identities
        ):
            raise ValueError(f"{name}.path is invalid")
        identities.add(path_key)
        role = _text(item["role"], f"{name}.role")
        if role not in {"document-result", "recovery-archive", "document-source"}:
            raise ValueError(f"{name}.role is invalid")
        starting = digest(item["starting_digest"], f"{name}.starting_digest")
        result = digest(item["result_digest"], f"{name}.result_digest")
        source_ids = list(_sequence(item["source_item_ids"], f"{name}.source_item_ids"))
        if (
            len(source_ids) > 16
            or any(not isinstance(value, str) or not value for value in source_ids)
            or source_ids != sorted(source_ids)
            or len(source_ids) != len(set(source_ids))
        ):
            raise ValueError(f"{name}.source_item_ids are invalid")
        recovery_binding = item["recovery_binding"]
        if operation == "CREATE":
            if starting != "sha256:ABSENT" or result == "sha256:ABSENT" or recovery_binding is not None:
                raise ValueError(f"{name} CREATE binding is invalid")
        else:
            destructive += 1
            if (
                starting == "sha256:ABSENT"
                or not isinstance(recovery_binding, str)
                or not _HEX_DIGEST.fullmatch(recovery_binding)
            ):
                raise ValueError(f"{name} destructive recovery binding is invalid")
            if operation == "REPLACE" and result == "sha256:ABSENT":
                raise ValueError(f"{name} REPLACE result is invalid")
            if operation == "DELETE" and result != "sha256:ABSENT":
                raise ValueError(f"{name} DELETE result is invalid")
        normalized = {
            "operation": operation,
            "path": path,
            "role": role,
            "starting_digest": starting,
            "result_digest": result,
        }
        if operation in {"CREATE", "REPLACE"}:
            result_bytes = item["result_bytes"]
            if not isinstance(result_bytes, bytes) or len(result_bytes) > 2 * 1024 * 1024:
                raise ValueError(f"{name}.result_bytes are invalid")
            if _sha256(result_bytes) != result:
                raise ValueError(f"{name}.result_digest does not match bytes")
            aggregate_result_bytes += len(result_bytes)
            normalized["result_bytes"] = result_bytes
        normalized["source_item_ids"] = source_ids
        normalized["recovery_binding"] = recovery_binding
        documents.append(normalized)
    if destructive > 32 or aggregate_result_bytes > 4 * 1024 * 1024:
        raise ValueError("document operations exceed destructive or byte capacity")

    fixed_control_roles = {
        ".diataxis/state.json": "state",
        ".diataxis/findings.json": "findings",
        ".diataxis/events.jsonl": "event",
        ".diataxis/local-map.json": "local-map",
        ".gitignore": "gitignore",
        "AGENTS.md": "agents",
    }
    for index, raw in enumerate(raw_controls):
        name = f"control operations[{index}]"
        item = dict(_mapping(raw, name))
        if set(item) != {
            "operation",
            "path",
            "role",
            "starting_digest",
            "result_digest",
        } or item.get("operation") != "CONTROL_REPLACE":
            raise ValueError(f"{name} fields are invalid")
        path = normalize_repo_relative(item["path"], f"{name}.path")
        role = _text(item["role"], f"{name}.role")
        expected_role = (
            "manifest"
            if path.startswith(".diataxis/manifests/") and path.endswith(".json")
            else fixed_control_roles.get(path)
        )
        path_key = path.casefold()
        if expected_role != role or path_key in identities:
            raise ValueError(f"{name} role or path is invalid")
        identities.add(path_key)
        starting = digest(item["starting_digest"], f"{name}.starting_digest")
        result = digest(item["result_digest"], f"{name}.result_digest")
        if result == "sha256:ABSENT":
            raise ValueError(f"{name}.result_digest is invalid")
        controls.append(
            {
                "operation": "CONTROL_REPLACE",
                "path": path,
                "role": role,
                "starting_digest": starting,
                "result_digest": result,
            }
        )
    return {
        "document_operations": documents,
        "control_operations": controls,
    }


def init_event_fingerprint(event):
    """Hash every Init event field except its ID and derived manifest path."""
    semantic = copy.deepcopy(dict(_mapping(event, "initialization event")))
    semantic.pop("event_id", None)
    manifest = semantic.get("manifest")
    if manifest is not None:
        manifest = dict(_mapping(manifest, "initialization event manifest"))
        semantic["manifest"] = {"digest": manifest.get("digest")}
    return hashlib.sha256(_canonical_bytes(semantic)).hexdigest()


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
    target_roles=None,
    replacement_order=(),
    approval_bindings=(),
    local_map_digest=None,
    local_map_schema_version=None,
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
    is_init = result.get("kind") == "init"
    if transaction_targets or is_init:
        result["transaction_targets"] = sorted(transaction_targets)
    if approval_bindings or is_init:
        result["approval_bindings"] = _normalized_approvals(approval_bindings)
    if is_init:
        if not isinstance(target_roles, Mapping):
            raise ValueError("initialization target roles are required")
        result["target_roles"] = dict(sorted(target_roles.items()))
        result["replacement_order"] = list(
            _sequence(replacement_order, "replacement order")
        )
    for field, digest in (("protected_preview_digest", protected_preview_digest),):
        if digest is not None:
            digest = _text(digest, field).lower()
            if not _HEX_DIGEST.fullmatch(digest):
                raise ValueError(f"{field} is invalid")
            result[field] = digest
    if local_map_digest is not None:
        local_map_digest = _text(local_map_digest, "local_map_digest").lower()
        if (
            not _HEX_DIGEST.fullmatch(local_map_digest)
            or local_map_schema_version != LOCAL_MAP_SCHEMA_VERSION
        ):
            raise ValueError("local map event binding is invalid")
        result["local_map_digest"] = local_map_digest
        result["local_map_schema_version"] = local_map_schema_version
    elif local_map_schema_version is not None:
        raise ValueError("local map schema requires its digest")
    recurrences = []
    for raw in _sequence(recurring_findings, "recurring findings"):
        record = dict(_mapping(raw, "recurring finding"))
        if set(record) != {"id", "fingerprint", "prior_event"}:
            raise ValueError("recurring finding fields are invalid")
        recurrences.append(record)
    if recurrences and is_init:
        raise ValueError("initialization event does not permit recurrence metadata")
    if recurrences:
        result["recurrences"] = sorted(recurrences, key=lambda item: item["id"])
    if dispositions is not None:
        dispositions = dict(_mapping(dispositions, "dispositions"))
        if is_init:
            approval_identity = dispositions.get("approval_identity")
            manifest_identity = dispositions.get("manifest_identity")
            if (
                dispositions.get("schema_version") != INIT_DISPOSITION_SCHEMA_VERSION
                or dispositions.get("storage") != "external"
                or not isinstance(approval_identity, str)
                or not _HEX_IDENTITY.fullmatch(approval_identity)
                or approval_identity != _approval_identity(result.get("approval_bindings", ()))
                or not isinstance(manifest_identity, str)
                or not _HEX_IDENTITY.fullmatch(manifest_identity)
                or dispositions["digest"] != f"sha256:{manifest_identity}"
            ):
                raise ValueError("initialization disposition identity is invalid")
            result.update(
                {
                    "manifest_digest": dispositions["digest"],
                    "manifest_schema_version": INIT_DISPOSITION_SCHEMA_VERSION,
                    "manifest_identity": manifest_identity,
                    "approval_identity": approval_identity,
                    "corpus_transition": copy.deepcopy(
                        dispositions["corpus_transition"]
                    ),
                    "corpus_transition_digest": _sha256(
                        _canonical_bytes(dispositions["corpus_transition"])
                    ),
                    "document_results_digest": dispositions[
                        "document_results_digest"
                    ],
                }
            )
            acceptance_digests = {
                item.get("recovery", {}).get("acceptance_digest")
                for item in dispositions["dispositions"]
                if item.get("recovery", {}).get("kind") == "accepted-hard-delete"
            }
            if None in acceptance_digests or len(acceptance_digests) > 1:
                raise ValueError("hard-delete acceptance binding is invalid")
            if acceptance_digests:
                result["hard_delete_acceptance_digest"] = next(
                    iter(acceptance_digests)
                )
            result["manifest"] = {"path": "$EVENT_ID", "digest": dispositions["digest"]}
        else:
            result["disposition_digest"] = dispositions["digest"]
            if dispositions["storage"] == "inline":
                result["dispositions"] = copy.deepcopy(dispositions["dispositions"])
            if dispositions.get("no_git_hard_delete_accepted"):
                result["no_git_hard_delete_accepted"] = True
                result["discarded_ids"] = list(dispositions["discarded_ids"])
            if dispositions["storage"] == "external":
                result["manifest_digest"] = dispositions["digest"]

    fingerprint = init_event_fingerprint(result) if is_init else event_fingerprint(result)
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
    "INIT_DISPOSITION_SCHEMA_VERSION",
    "INLINE_MANIFEST_BYTES",
    "INLINE_MANIFEST_ITEMS",
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
