"""Deterministic, bounded initialization preview and approved closeout.

The public script accepts UTF-8 JSON on stdin. A preview request has exactly
``schema_version``, ``operation``, and ``evidence``. An apply request adds the
exact ``approval`` emitted by preview. Evidence contains verified structured
facts, never prebuilt operational bytes or filesystem target paths. This module
constructs state through :func:`build_initialization_state`, then delegates all
transaction semantics to ``prepare_verified_closeout`` and
``apply_verified_closeout``.
"""

import base64
import binascii
import copy
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import threading
import unicodedata

from .formats import is_document_path, is_navigation_manifest_path
from .discovery import (
    CorpusValidationError,
    derive_result_corpus,
    scan_selected_document_corpus,
    validate_corpus_coverage,
)
from .lifecycle import prepare_dispositions
from .lifecycle_io import (
    INIT_AGENTS_ORIENTATION,
    INIT_LOCAL_MAP_IGNORE,
    _prepare_init_source_targets,
    apply_verified_closeout,
    prepare_verified_closeout,
)
from .knowledge import inspect_local_map, validate_local_map
from .memory import (
    MAX_FINDINGS_BYTES,
    STATE_DIRECTORY,
    STATE_SCHEMA_VERSION,
    _is_benign_initialization_residue,
    build_initialization_state,
    inspect_operational_memory,
    load_operational_events,
    load_operational_findings,
    load_operational_state,
)
from .paths import normalize_repo_relative, safe_path, shared_text_exposes_route


REQUEST_SCHEMA_VERSION = 3
ALREADY_INITIALIZED_MESSAGE = (
    "This repository is already initialized. "
    "Run $docs doctor to diagnose or improve it."
)
STATE_CONFLICT_MESSAGE = (
    "Initialization state requires diagnosis. Run $docs doctor before retrying $docs init."
)
MAX_REQUEST_BYTES = 8 * 1024 * 1024
MAX_SOURCE_FILE_BYTES = 2 * 1024 * 1024
MAX_SOURCE_TOTAL_BYTES = 4 * 1024 * 1024
MAX_DOCUMENT_OPERATIONS = 64
MAX_DESTRUCTIVE_OPERATIONS = 32
MAX_DISPOSITIONS = 256
MAX_SOURCE_ITEM_IDS = 16
MAX_REASON_BYTES = 512
MAX_GIT_STATUS_BYTES = 1024 * 1024
MAX_EVENT_TEXT_BYTES = 4096
_STATE_FIELDS = frozenset(
    {
        "skill_version",
        "selected_scope",
        "inspected_scope",
        "map_path",
        "current_truth_routes",
        "rubric_version",
        "score_before",
        "score_after",
        "rubric_status",
        "cold_paths",
        "verified_documents",
        "protected_intent",
        "hot_path_bytes",
        "trust_coverage",
    }
)
_EVIDENCE_FIELDS = _STATE_FIELDS | {
    "findings",
    "dispositions",
    "local_map",
    "event",
    "approvals",
    "source_changes",
}
_NAVIGATION_EVIDENCE_FIELDS = frozenset(
    {
        "status",
        "provider",
        "scope",
        "provider_root",
        "authority",
        "entry",
        "navigated_pages",
        "hidden_pages",
        "redirects",
        "unsupported_features",
        "contexts",
        "findings",
        "limits",
        "orientation",
        "manifest_digest",
    }
)
MAX_NAVIGATION_EVIDENCE_BYTES = 512 * 1024
_EVENT_FIELDS = frozenset(
    {
        "kind",
        "completed_at",
        "skill_version",
        "approved_ids",
        "score_before",
        "score_after",
        "reason",
        "summary",
    }
)
_COMPLETED_AT = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z$")
_SOURCE_CHANGE_FIELDS = frozenset(
    {"agents_orientation", "local_map_ignore"}
)
_SEMVER = re.compile(r"^(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)$")
_RAW_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")
_CONTENT_DIGEST = re.compile(r"^sha256-(?:text|bytes):[0-9a-f]{64}$")
_EVENT_ID = re.compile(r"^EVT-[0-9A-F]{8,}$")
_GIT_OBJECT_ID = re.compile(r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$")
_DISCARD_SET_ID = re.compile(r"^DISCARD-[0-9A-F]{16}$")
_SECTION_ITEM_ID = re.compile(r"^SEC-[0-9A-F]{24}$")
_WINDOWS_SHORT_COMPONENT = re.compile(r"^.+~[1-9][0-9]*(?:\..*)?$", re.IGNORECASE)
_WINDOWS_DEVICE_NAMES = frozenset(
    {"con", "prn", "aux", "nul"}
    | {f"com{index}" for index in range(1, 10)}
    | {f"lpt{index}" for index in range(1, 10)}
)
_APPROVAL = re.compile(
    r"^Approve \$docs init preview INIT-[0-9A-F]{12} with manifest [0-9a-f]{64}$"
)
_TRUST_SOURCES = frozenset(
    {
        "configured:hot-path",
        "map:authoritative",
        "map:current",
        "state:initialized-hot-path",
        "state:verified-document",
        "state:verified-source",
    }
)


class InitCloseoutError(ValueError):
    """One bounded public failure with a stable process classification."""

    def __init__(self, status, classification, boundary, **details):
        super().__init__(classification)
        self.status = status
        self.classification = classification
        self.boundary = boundary
        self.details = details


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
        raise InitCloseoutError(
            "invalid-request", "invalid-json-value", "request-validation"
        ) from exc


def _require_exact_mapping(value, fields, name):
    if type(value) is not dict or set(value) != set(fields):
        raise InitCloseoutError(
            "invalid-request", f"invalid-{name}-fields", "request-validation"
        )
    return value


def _invalid(classification, boundary="request-validation"):
    raise InitCloseoutError("invalid-request", classification, boundary)


def _capacity(classification="capacity-exceeded"):
    raise InitCloseoutError("invalid-request", classification, "request-validation")


def _require_string(value, name, *, maximum=MAX_EVENT_TEXT_BYTES):
    if (
        type(value) is not str
        or not value
        or value != value.strip()
        or len(value.encode("utf-8")) > maximum
        or any(ord(character) < 32 or ord(character) == 127 for character in value)
    ):
        _invalid(f"invalid-{name}")
    return value


def _require_integer(value, name, *, minimum=None, maximum=None):
    if type(value) is not int:
        _invalid(f"invalid-{name}")
    if minimum is not None and value < minimum:
        _invalid(f"invalid-{name}")
    if maximum is not None and value > maximum:
        _invalid(f"invalid-{name}")
    return value


def _path_identity(path):
    return path.casefold()


def _within_boundary(path, boundary):
    path_key = _path_identity(path)
    boundary_key = _path_identity(boundary)
    return (
        boundary_key == "."
        or path_key == boundary_key
        or path_key.startswith(boundary_key + "/")
    )


def _normalize_shared_path_v3(
    value,
    name,
    *,
    boundary=None,
    markdown=False,
    allow_root=False,
):
    if type(value) is not str or "\\" in value:
        _invalid(f"invalid-{name}")
    try:
        normalized = normalize_repo_relative(value, name)
    except (TypeError, ValueError) as exc:
        raise InitCloseoutError(
            "invalid-request", f"invalid-{name}", "request-validation"
        ) from exc
    if normalized != value or (normalized == "." and not allow_root):
        _invalid(f"invalid-{name}")
    parts = () if normalized == "." else normalized.split("/")
    if any(part.casefold() in {".local", ".diataxis"} for part in parts):
        _invalid(f"invalid-{name}")
    if any(
        part.rstrip(" .") != part
        or ":" in part
        or _WINDOWS_SHORT_COMPONENT.fullmatch(part) is not None
        or part.casefold().split(".", 1)[0] in _WINDOWS_DEVICE_NAMES
        for part in parts
    ):
        _invalid(f"invalid-{name}")
    if boundary is not None and not _within_boundary(normalized, boundary):
        _invalid(f"invalid-{name}")
    if markdown and not is_document_path(normalized):
        _invalid(f"invalid-{name}")
    return normalized


def _normalize_sorted_strings(
    value,
    name,
    *,
    maximum=MAX_DISPOSITIONS,
    normalizer=None,
):
    if type(value) is not list or len(value) > maximum:
        _capacity() if type(value) is list else _invalid(f"invalid-{name}")
    normalized = []
    identities = set()
    for index, item in enumerate(value):
        item = (
            normalizer(item, f"{name}[{index}]")
            if normalizer is not None
            else _require_string(item, f"{name}[{index}]")
        )
        identity = item.casefold()
        if identity in identities:
            _invalid(f"duplicate-{name}")
        identities.add(identity)
        normalized.append(item)
    expected = sorted(normalized, key=lambda item: (item.casefold(), item))
    if normalized != expected:
        _invalid(f"unsorted-{name}")
    return normalized


def _normalize_digest(value, name, pattern=_RAW_DIGEST):
    if type(value) is not str or pattern.fullmatch(value) is None:
        _invalid(f"invalid-{name}")
    return value


def _safe_persisted_text(value, name, *, maximum=MAX_EVENT_TEXT_BYTES):
    value = _require_string(value, name, maximum=maximum)
    if shared_text_exposes_route(value):
        _invalid(f"invalid-{name}")
    return value


def _normalize_recovery_v3(value, write_boundary=None, *, allow_accepted=False):
    if type(value) is not dict or type(value.get("kind")) is not str:
        _invalid("invalid-recovery")
    kind = value["kind"]
    if kind == "git":
        _require_exact_mapping(
            value,
            {"kind", "commit", "blob", "digest"},
            "recovery-git",
        )
        if (
            type(value["commit"]) is not str
            or _GIT_OBJECT_ID.fullmatch(value["commit"]) is None
            or type(value["blob"]) is not str
            or _GIT_OBJECT_ID.fullmatch(value["blob"]) is None
        ):
            _invalid("invalid-recovery-git")
        _normalize_digest(value["digest"], "recovery-digest")
    elif kind == "archive":
        _require_exact_mapping(
            value,
            {"kind", "mode", "path", "digest"},
            "recovery-archive",
        )
        if value["mode"] not in {"existing", "planned"} or type(value["mode"]) is not str:
            _invalid("invalid-recovery-archive")
        _normalize_shared_path_v3(
            value["path"],
            "recovery-path",
            boundary=write_boundary,
            markdown=True,
        )
        _normalize_digest(value["digest"], "recovery-digest")
    elif kind == "hard-delete-request":
        _require_exact_mapping(value, {"kind"}, "recovery-hard-delete-request")
    elif kind == "accepted-hard-delete" and allow_accepted:
        _require_exact_mapping(
            value,
            {"kind", "discard_set_id", "acceptance_digest"},
            "recovery-accepted-hard-delete",
        )
        if (
            type(value["discard_set_id"]) is not str
            or _DISCARD_SET_ID.fullmatch(value["discard_set_id"]) is None
        ):
            _invalid("invalid-discard-set-id")
        _normalize_digest(value["acceptance_digest"], "acceptance-digest")
    else:
        _invalid("invalid-recovery-kind")
    return copy.deepcopy(value)


def _normalize_disposition_v3(value, write_boundary):
    if type(value) is not dict or type(value.get("disposition")) is not str:
        _invalid("invalid-disposition")
    disposition = value["disposition"]
    common = {
        "item_id",
        "path",
        "section",
        "disposition",
        "reason",
        "source_digest",
    }
    if type(value.get("section")) is not dict:
        _invalid("invalid-disposition-section")
    section = value["section"]
    whole_file = section == {"kind": "whole-file"}
    if not whole_file and disposition == "RETAIN":
        _invalid("section-retain-forbidden", "disposition-matrix")
    section_common = common | {"recovery"}
    variants = (
        {
            "RETAIN": common,
            "UNRESOLVED": common,
            "MIGRATED": common | {"target", "recovery"},
            "DEDUPLICATED": common | {"target", "target_digest", "recovery"},
            "ARCHIVED": common | {"target", "recovery"},
            "DISCARDED": common | {"recovery"},
        }
        if whole_file
        else {
            "UNRESOLVED": section_common,
            "MIGRATED": section_common | {"target"},
            "DEDUPLICATED": section_common | {"target", "target_digest"},
            "ARCHIVED": section_common | {"target"},
            "DISCARDED": section_common,
        }
    )
    fields = variants.get(disposition)
    if fields is None:
        _invalid("invalid-disposition-kind")
    _require_exact_mapping(value, fields, "disposition")
    path = _normalize_shared_path_v3(
        value["path"],
        "disposition-path",
        boundary=write_boundary,
        markdown=True,
    )
    if whole_file:
        if type(value["item_id"]) is not str or value["item_id"] != f"{path}#<whole-file>":
            _invalid("invalid-disposition-item-id")
    else:
        section_fields = {
            "kind",
            "level",
            "heading_path",
            "occurrence",
            "start_byte",
            "end_byte",
            "raw_span_digest",
        }
        if set(section) != section_fields:
            _invalid("invalid-section-fields", "disposition-matrix")
        if section["kind"] != "atx-section-v1" or type(section["kind"]) is not str:
            _invalid("invalid-disposition-section")
        if type(section["level"]) is not int or not 1 <= section["level"] <= 6:
            _invalid("invalid-disposition-section")
        heading_path = section["heading_path"]
        if (
            type(heading_path) is not list
            or not heading_path
            or len(heading_path) > section["level"]
        ):
            _invalid("invalid-disposition-section")
        for heading in heading_path:
            if type(heading) is not str:
                _invalid("invalid-disposition-section")
            normalized_heading = " ".join(
                unicodedata.normalize("NFC", heading).split()
            ).casefold()
            if not normalized_heading or heading != normalized_heading:
                _invalid("invalid-disposition-section")
        if (
            type(section["occurrence"]) is not int
            or section["occurrence"] < 1
            or type(section["start_byte"]) is not int
            or section["start_byte"] < 0
            or type(section["end_byte"]) is not int
            or section["end_byte"] <= section["start_byte"]
        ):
            _invalid("invalid-section-offsets", "disposition-matrix")
        _normalize_digest(section["raw_span_digest"], "section-span-digest")
        expected_item_id = "SEC-" + hashlib.sha256(
            _canonical_bytes({"path": path, "section": section})
        ).hexdigest()[:24].upper()
        if type(value["item_id"]) is not str or value["item_id"] != expected_item_id:
            _invalid("invalid-disposition-item-id")
    _safe_persisted_text(
        value["reason"],
        "disposition-reason",
        maximum=MAX_REASON_BYTES,
    )
    _normalize_digest(value["source_digest"], "disposition-source-digest")
    if "target" in fields:
        _normalize_shared_path_v3(
            value["target"],
            "disposition-target",
            boundary=write_boundary,
            markdown=True,
        )
    if "target_digest" in fields:
        _normalize_digest(value["target_digest"], "disposition-target-digest")
    if "recovery" in fields:
        if (
            not whole_file
            and type(value["recovery"]) is dict
            and value["recovery"].get("kind")
            in {"hard-delete-request", "accepted-hard-delete"}
        ):
            _invalid("section-hard-delete-forbidden", "recovery-verification")
        recovery = _normalize_recovery_v3(value["recovery"], write_boundary)
        if not whole_file and recovery["kind"] not in {"git", "archive"}:
            _invalid("invalid-section-recovery")
    return copy.deepcopy(value)


def _normalize_source_item_ids(value, name):
    values = _normalize_sorted_strings(
        value,
        name,
        maximum=MAX_SOURCE_ITEM_IDS,
    )
    if any("#" not in item and _SECTION_ITEM_ID.fullmatch(item) is None for item in values):
        _invalid(f"invalid-{name}")
    return values


def _normalize_document_change_v3(value, write_boundary):
    if type(value) is not dict or type(value.get("operation")) is not str:
        _invalid("invalid-document-change")
    operation = value["operation"]
    common = {"operation", "path", "reason", "source_item_ids"}
    fields = (
        common | {"content_base64"}
        if operation in {"CREATE", "REPLACE"}
        else common
        if operation == "DELETE"
        else None
    )
    if fields is None:
        _invalid("invalid-document-operation")
    _require_exact_mapping(value, fields, "document-change")
    _normalize_shared_path_v3(
        value["path"],
        "document-change-path",
        boundary=write_boundary,
        markdown=True,
    )
    _require_string(value["reason"], "document-change-reason", maximum=MAX_REASON_BYTES)
    source_ids = _normalize_source_item_ids(
        value["source_item_ids"],
        "source-item-ids",
    )
    if operation != "CREATE" and not source_ids:
        _invalid("missing-source-item-ids")
    normalized = copy.deepcopy(value)
    result = {"public": normalized}
    if operation in {"CREATE", "REPLACE"}:
        encoded = value["content_base64"]
        if type(encoded) is not str or any(character.isspace() for character in encoded):
            _invalid("invalid-content-base64")
        try:
            decoded = base64.b64decode(encoded, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise InitCloseoutError(
                "invalid-request", "invalid-content-base64", "request-validation"
            ) from exc
        if base64.b64encode(decoded).decode("ascii") != encoded:
            _invalid("invalid-content-base64")
        if len(decoded) > MAX_SOURCE_FILE_BYTES:
            _capacity()
        try:
            decoded.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise InitCloseoutError(
                "invalid-request", "document-content-not-utf8", "request-validation"
            ) from exc
        result["result_bytes"] = decoded
    return result


def _normalize_hard_delete_acceptance_v3(value):
    if value is None:
        return None
    _require_exact_mapping(
        value,
        {"discard_set_id", "acceptance"},
        "hard-delete-acceptance",
    )
    discard_set_id = value["discard_set_id"]
    if type(discard_set_id) is not str or _DISCARD_SET_ID.fullmatch(discard_set_id) is None:
        _invalid("invalid-discard-set-id")
    expected = (
        "Approve hard deletion of discard set "
        f"{discard_set_id}; I accept that no repository recovery is available."
    )
    if type(value["acceptance"]) is not str or value["acceptance"] != expected:
        _invalid("invalid-hard-delete-acceptance")
    return copy.deepcopy(value)


def _normalize_public_event_v3(event, evidence):
    _require_exact_mapping(event, _EVENT_FIELDS, "event")
    if (
        type(event["kind"]) is not str
        or event["kind"] != "init"
        or type(event["completed_at"]) is not str
        or _COMPLETED_AT.fullmatch(event["completed_at"]) is None
        or type(event["skill_version"]) is not str
        or event["skill_version"] != evidence["skill_version"]
        or type(event["score_before"]) is not int
        or type(event["score_after"]) is not int
        or event["score_before"] != evidence["score_before"]
        or event["score_after"] != evidence["score_after"]
    ):
        _invalid("invalid-event-values")
    approved_ids = _normalize_sorted_strings(
        event["approved_ids"],
        "event-approved-ids",
    )
    _safe_persisted_text(event["reason"], "event-reason")
    _safe_persisted_text(event["summary"], "event-summary")
    return {**copy.deepcopy(event), "approved_ids": approved_ids}


def _normalize_hot_path_endpoint(value, name):
    _require_exact_mapping(value, {"value", "unit", "provenance"}, name)
    measured = _require_integer(value["value"], f"{name}-value", minimum=0)
    if type(value["unit"]) is not str or value["unit"] != "bytes":
        _invalid(f"invalid-{name}-unit")
    if type(value["provenance"]) is not list or len(value["provenance"]) > MAX_DISPOSITIONS:
        _invalid(f"invalid-{name}-provenance")
    provenance = []
    identities = set()
    for index, raw in enumerate(value["provenance"]):
        item_name = f"{name}-provenance-{index}"
        _require_exact_mapping(raw, {"route", "bytes", "source"}, item_name)
        route = _normalize_shared_path_v3(raw["route"], f"{item_name}-route")
        identity = _path_identity(route)
        if identity in identities:
            _invalid(f"duplicate-{name}-provenance")
        identities.add(identity)
        byte_count = _require_integer(raw["bytes"], f"{item_name}-bytes", minimum=0)
        if type(raw["source"]) is not str or raw["source"] != "filesystem-stat":
            _invalid(f"invalid-{item_name}-source")
        provenance.append({"route": route, "bytes": byte_count, "source": "filesystem-stat"})
    if sum(item["bytes"] for item in provenance) != measured:
        _invalid(f"invalid-{name}-total")
    if provenance != sorted(
        provenance,
        key=lambda item: (item["route"].casefold(), item["route"]),
    ):
        _invalid(f"unsorted-{name}-provenance")
    return {"value": measured, "unit": "bytes", "provenance": provenance}


def _normalize_trust_coverage_v3(value):
    _require_exact_mapping(
        value,
        {"status", "numerator", "denominator", "routes"},
        "trust-coverage",
    )
    if type(value["routes"]) is not list or len(value["routes"]) > MAX_DISPOSITIONS:
        _invalid("invalid-trust-routes")
    routes = []
    identities = set()
    for index, raw in enumerate(value["routes"]):
        name = f"trust-route-{index}"
        _require_exact_mapping(raw, {"route", "verified", "freshness", "sources"}, name)
        route = _normalize_shared_path_v3(raw["route"], f"{name}-path")
        identity = _path_identity(route)
        if identity in identities:
            _invalid("duplicate-trust-route")
        identities.add(identity)
        if type(raw["verified"]) is not bool:
            _invalid(f"invalid-{name}-verified")
        if type(raw["freshness"]) is not str or raw["freshness"] not in {
            "fresh",
            "stale",
            "unverified",
        }:
            _invalid(f"invalid-{name}-freshness")
        if raw["verified"] is not (raw["freshness"] == "fresh"):
            _invalid(f"invalid-{name}-freshness")
        sources = _normalize_sorted_strings(raw["sources"], f"{name}-sources", maximum=16)
        if not sources or any(source not in _TRUST_SOURCES for source in sources):
            _invalid(f"invalid-{name}-sources")
        routes.append({**copy.deepcopy(raw), "route": route, "sources": sources})
    if routes != sorted(routes, key=lambda item: (item["route"].casefold(), item["route"])):
        _invalid("unsorted-trust-routes")
    numerator = _require_integer(value["numerator"], "trust-numerator", minimum=0)
    denominator = _require_integer(value["denominator"], "trust-denominator", minimum=0)
    if denominator != len(routes) or numerator != sum(item["verified"] for item in routes):
        _invalid("invalid-trust-counts")
    expected = (
        "unverified"
        if denominator == 0
        else "verified"
        if numerator == denominator
        else "partial"
    )
    if type(value["status"]) is not str or value["status"] != expected:
        _invalid("invalid-trust-status")
    return {"status": expected, "numerator": numerator, "denominator": denominator, "routes": routes}


def _normalize_navigation_evidence_v3(value, selected_scope):
    _require_exact_mapping(value, _NAVIGATION_EVIDENCE_FIELDS, "navigation-evidence")
    if value["status"] != "measured":
        _invalid("invalid-navigation-evidence-status")
    if value["provider"] not in {"markdown-map", "mintlify"}:
        _invalid("invalid-navigation-evidence-provider")
    scope = _normalize_shared_path_v3(
        value["scope"], "navigation-evidence-scope", allow_root=True
    )
    if scope != selected_scope:
        _invalid("navigation-evidence-scope-mismatch")
    provider_root = value["provider_root"]
    if value["provider"] == "markdown-map" and provider_root is None:
        provider_root = None
    else:
        provider_root = _normalize_shared_path_v3(
            provider_root, "navigation-evidence-root", allow_root=True
        )
    authority = _normalize_shared_path_v3(
        value["authority"], "navigation-evidence-authority"
    )
    if value["provider"] == "mintlify":
        if not is_navigation_manifest_path(authority):
            _invalid("invalid-navigation-evidence-authority")
    elif not is_document_path(authority):
        _invalid("invalid-navigation-evidence-authority")
    entry = value["entry"]
    if entry is not None:
        entry = _normalize_shared_path_v3(
            entry,
            "navigation-evidence-entry",
            boundary=selected_scope,
            markdown=True,
        )

    def path_list(raw, name):
        if type(raw) is not list or len(raw) > 10_000:
            _invalid(f"invalid-{name}")
        result = []
        identities = set()
        for index, item in enumerate(raw):
            route = _normalize_shared_path_v3(
                item,
                f"{name}-{index}",
                boundary=selected_scope,
                markdown=True,
            )
            identity = _path_identity(route)
            if identity in identities:
                _invalid(f"duplicate-{name}")
            identities.add(identity)
            result.append(route)
        return result

    navigated_pages = path_list(value["navigated_pages"], "navigation-evidence-navigated")
    hidden_pages = path_list(value["hidden_pages"], "navigation-evidence-hidden")
    if set(map(_path_identity, navigated_pages)) & set(map(_path_identity, hidden_pages)):
        _invalid("navigation-evidence-visibility-overlap")

    redirects = value["redirects"]
    if type(redirects) is not list or len(redirects) > 2_048:
        _invalid("invalid-navigation-evidence-redirects")
    normalized_redirects = []
    for index, redirect in enumerate(redirects):
        name = f"navigation-evidence-redirect-{index}"
        _require_exact_mapping(redirect, {"source", "destination"}, name)
        source = _require_string(redirect["source"], f"{name}-source", maximum=16 * 1024)
        destination = _require_string(
            redirect["destination"], f"{name}-destination", maximum=16 * 1024
        )
        normalized_redirects.append({"source": source, "destination": destination})
    if normalized_redirects != sorted(
        normalized_redirects, key=lambda item: (item["source"].casefold(), item["source"])
    ):
        _invalid("unsorted-navigation-evidence-redirects")

    unsupported_features = _normalize_sorted_strings(
        value["unsupported_features"],
        "navigation-evidence-features",
        maximum=64,
    )
    contexts = value["contexts"]
    limits = value["limits"]
    findings = value["findings"]
    if type(contexts) is not dict or type(limits) is not dict or type(findings) is not list:
        _invalid("invalid-navigation-evidence-shape")
    if len(_canonical_bytes({"contexts": contexts, "limits": limits, "findings": findings})) > MAX_NAVIGATION_EVIDENCE_BYTES:
        _capacity("navigation-evidence-capacity")
    orientation = value["orientation"]
    if orientation is not None:
        _require_exact_mapping(orientation, {"path", "separate"}, "navigation-evidence-orientation")
        orientation_path = _normalize_shared_path_v3(
            orientation["path"], "navigation-evidence-orientation-path", markdown=True
        )
        if orientation["separate"] is not True:
            _invalid("invalid-navigation-evidence-orientation")
        orientation = {"path": orientation_path, "separate": True}
    manifest_digest = value["manifest_digest"]
    if manifest_digest is not None:
        _normalize_digest(manifest_digest, "navigation-evidence-manifest-digest")
    return {
        "status": "measured",
        "provider": value["provider"],
        "scope": scope,
        "provider_root": provider_root,
        "authority": authority,
        "entry": entry,
        "navigated_pages": navigated_pages,
        "hidden_pages": hidden_pages,
        "redirects": normalized_redirects,
        "unsupported_features": unsupported_features,
        "contexts": copy.deepcopy(contexts),
        "findings": copy.deepcopy(findings),
        "limits": copy.deepcopy(limits),
        "orientation": orientation,
        "manifest_digest": manifest_digest,
    }


def _normalize_evidence_v3(evidence):
    if type(evidence) is not dict or set(evidence) not in {
        _EVIDENCE_FIELDS,
        _EVIDENCE_FIELDS | {"navigation_evidence"},
    }:
        _invalid("invalid-evidence-fields")
    skill_version = evidence["skill_version"]
    if type(skill_version) is not str or _SEMVER.fullmatch(skill_version) is None:
        _invalid("invalid-skill-version")
    selected_scope = _normalize_shared_path_v3(
        evidence["selected_scope"],
        "selected-scope",
        allow_root=True,
    )
    inspected_scope = _normalize_shared_path_v3(
        evidence["inspected_scope"],
        "inspected-scope",
        allow_root=True,
    )
    if selected_scope.casefold() != inspected_scope.casefold() or selected_scope != inspected_scope:
        _invalid("inspected-scope-mismatch")
    map_path = _normalize_shared_path_v3(
        evidence["map_path"],
        "map-path",
        boundary=selected_scope,
        markdown=True,
    )
    current_truth_routes = _normalize_sorted_strings(
        evidence["current_truth_routes"],
        "current-truth-routes",
        normalizer=lambda item, name: _normalize_shared_path_v3(
            item,
            name,
            boundary=selected_scope,
        ),
    )
    cold_paths = _normalize_sorted_strings(
        evidence["cold_paths"],
        "cold-paths",
        normalizer=lambda item, name: _normalize_shared_path_v3(
            item,
            name,
            boundary=selected_scope,
        ),
    )
    rubric_version = _require_integer(evidence["rubric_version"], "rubric-version", minimum=1)
    score_before = _require_integer(evidence["score_before"], "score-before", minimum=0, maximum=100)
    score_after = _require_integer(evidence["score_after"], "score-after", minimum=0, maximum=100)
    if type(evidence["rubric_status"]) is not str or evidence["rubric_status"] not in {
        "healthy",
        "needs-attention",
    }:
        _invalid("invalid-rubric-status")

    if type(evidence["verified_documents"]) is not list or len(evidence["verified_documents"]) > MAX_DISPOSITIONS:
        _invalid("invalid-verified-documents")
    verified_documents = []
    document_identities = set()
    for index, raw in enumerate(evidence["verified_documents"]):
        name = f"verified-document-{index}"
        _require_exact_mapping(raw, {"document", "digest", "sources", "verified_event"}, name)
        document = _normalize_shared_path_v3(
            raw["document"],
            f"{name}-path",
            boundary=selected_scope,
            markdown=True,
        )
        identity = _path_identity(document)
        if identity in document_identities:
            _invalid("duplicate-verified-document")
        document_identities.add(identity)
        _normalize_digest(raw["digest"], f"{name}-digest", _CONTENT_DIGEST)
        if type(raw["sources"]) is not list or len(raw["sources"]) > MAX_DISPOSITIONS:
            _invalid(f"invalid-{name}-sources")
        sources = []
        source_identities = set()
        for source_index, source in enumerate(raw["sources"]):
            source_name = f"{name}-source-{source_index}"
            _require_exact_mapping(source, {"path", "digest"}, source_name)
            source_path = _normalize_shared_path_v3(source["path"], f"{source_name}-path")
            source_identity = _path_identity(source_path)
            if source_identity in source_identities:
                _invalid(f"duplicate-{name}-source")
            source_identities.add(source_identity)
            _normalize_digest(source["digest"], f"{source_name}-digest", _CONTENT_DIGEST)
            sources.append(copy.deepcopy(source))
        if sources != sorted(sources, key=lambda item: (item["path"].casefold(), item["path"])):
            _invalid(f"unsorted-{name}-sources")
        if type(raw["verified_event"]) is not str or _EVENT_ID.fullmatch(raw["verified_event"]) is None:
            _invalid(f"invalid-{name}-event")
        verified_documents.append({**copy.deepcopy(raw), "sources": sources})
    if verified_documents != sorted(
        verified_documents,
        key=lambda item: (item["document"].casefold(), item["document"]),
    ):
        _invalid("unsorted-verified-documents")

    if type(evidence["protected_intent"]) is not list or len(evidence["protected_intent"]) > 64:
        _invalid("invalid-protected-intent")
    protected_intent = []
    protected_ids = set()
    for index, raw in enumerate(evidence["protected_intent"]):
        name = f"protected-intent-{index}"
        _require_exact_mapping(raw, {"id", "intent_key", "source", "preserve", "status"}, name)
        intent_id = _require_string(raw["id"], f"{name}-id")
        if re.fullmatch(r"INTENT-[0-9]+", intent_id) is None or intent_id in protected_ids:
            _invalid(f"invalid-{name}-id")
        protected_ids.add(intent_id)
        _require_string(raw["intent_key"], f"{name}-key")
        source, separator, anchor = raw["source"].partition("#") if type(raw["source"]) is str else ("", "", "")
        _normalize_shared_path_v3(source, f"{name}-source", boundary=selected_scope, markdown=True)
        if not separator or not anchor or anchor != anchor.strip():
            _invalid(f"invalid-{name}-source")
        if raw["preserve"] is not True:
            _invalid(f"invalid-{name}-preserve")
        _require_string(raw["status"], f"{name}-status")
        protected_intent.append(copy.deepcopy(raw))
    if protected_intent != sorted(protected_intent, key=lambda item: item["id"]):
        _invalid("unsorted-protected-intent")

    _require_exact_mapping(evidence["hot_path_bytes"], {"before", "after"}, "hot-path-bytes")
    hot_path_bytes = {
        "before": _normalize_hot_path_endpoint(evidence["hot_path_bytes"]["before"], "hot-path-before"),
        "after": _normalize_hot_path_endpoint(evidence["hot_path_bytes"]["after"], "hot-path-after"),
    }
    trust_coverage = _normalize_trust_coverage_v3(evidence["trust_coverage"])

    findings = evidence["findings"]
    _require_exact_mapping(findings, {"schema_version", "findings"}, "findings")
    if type(findings["schema_version"]) is not int or findings["schema_version"] != 1 or type(findings["findings"]) is not list:
        _invalid("invalid-findings")
    if len(_canonical_bytes(findings)) > MAX_FINDINGS_BYTES:
        _capacity()

    dispositions_raw = evidence["dispositions"]
    if type(dispositions_raw) is not list:
        _invalid("invalid-dispositions")
    if len(dispositions_raw) > MAX_DISPOSITIONS:
        _capacity()
    dispositions = [
        _normalize_disposition_v3(item, selected_scope)
        for item in dispositions_raw
    ]
    disposition_ids = [item["item_id"] for item in dispositions]
    if len({item.casefold() for item in disposition_ids}) != len(disposition_ids):
        _invalid("duplicate-disposition")
    if dispositions != sorted(
        dispositions,
        key=lambda item: (
            item["path"].casefold(),
            item["path"],
            0 if item["section"] == {"kind": "whole-file"} else 1,
            item["item_id"],
        ),
    ):
        _invalid("unsorted-dispositions")

    local_map = evidence["local_map"]
    if local_map is not None:
        try:
            local_map = validate_local_map(copy.deepcopy(local_map))
        except (TypeError, ValueError) as exc:
            raise InitCloseoutError(
                "invalid-request", "invalid-local-map", "request-validation"
            ) from exc

    if type(evidence["approvals"]) is not list or len(evidence["approvals"]) > MAX_DISPOSITIONS:
        _invalid("invalid-approvals")
    approvals = []
    approval_ids = set()
    for index, raw in enumerate(evidence["approvals"]):
        name = f"approval-{index}"
        _require_exact_mapping(raw, {"id", "fingerprint"}, name)
        identifier = _require_string(raw["id"], f"{name}-id")
        if identifier.casefold() in approval_ids:
            _invalid("duplicate-approval")
        approval_ids.add(identifier.casefold())
        if type(raw["fingerprint"]) is not str or re.fullmatch(r"[0-9a-f]{64}", raw["fingerprint"]) is None:
            _invalid(f"invalid-{name}-fingerprint")
        approvals.append(copy.deepcopy(raw))
    if approvals != sorted(approvals, key=lambda item: (item["id"].casefold(), item["id"])):
        _invalid("unsorted-approvals")

    changes = _require_exact_mapping(
        evidence["source_changes"],
        _SOURCE_CHANGE_FIELDS,
        "source-changes",
    )
    if any(type(value) is not bool for value in changes.values()):
        _invalid("invalid-source-changes")
    if changes["local_map_ignore"] is not (local_map is not None):
        _invalid("invalid-local-map-policy")

    normalized = {
        "skill_version": skill_version,
        "selected_scope": selected_scope,
        "inspected_scope": inspected_scope,
        "map_path": map_path,
        "current_truth_routes": current_truth_routes,
        "rubric_version": rubric_version,
        "score_before": score_before,
        "score_after": score_after,
        "rubric_status": evidence["rubric_status"],
        "cold_paths": cold_paths,
        "verified_documents": verified_documents,
        "protected_intent": protected_intent,
        "hot_path_bytes": hot_path_bytes,
        "trust_coverage": trust_coverage,
        "findings": copy.deepcopy(findings),
        "dispositions": dispositions,
        "local_map": copy.deepcopy(local_map),
        "event": None,
        "approvals": approvals,
        "source_changes": copy.deepcopy(changes),
    }
    normalized["event"] = _normalize_public_event_v3(evidence["event"], normalized)
    if "navigation_evidence" in evidence:
        normalized["navigation_evidence"] = _normalize_navigation_evidence_v3(
            evidence["navigation_evidence"], selected_scope
        )
    return normalized


def validate_public_request(request, operation):
    expected = {
        "schema_version",
        "operation",
        "evidence",
        "document_changes",
        "hard_delete_acceptance",
    }
    if operation == "apply":
        expected.add("approval")
    _require_exact_mapping(request, expected, "request")
    if (
        operation not in {"preview", "apply"}
        or type(request["schema_version"]) is not int
        or request["schema_version"] != REQUEST_SCHEMA_VERSION
        or type(request["operation"]) is not str
        or request["operation"] != operation
    ):
        _invalid("invalid-request-contract")
    evidence = _normalize_evidence_v3(request["evidence"])
    raw_changes = request["document_changes"]
    if type(raw_changes) is not list:
        _invalid("invalid-document-changes")
    if len(raw_changes) > MAX_DOCUMENT_OPERATIONS:
        _capacity()
    changes = []
    identities = set()
    result_bytes = 0
    destructive = 0
    for raw in raw_changes:
        normalized = _normalize_document_change_v3(raw, evidence["selected_scope"])
        public = normalized["public"]
        identity = _path_identity(public["path"])
        if identity in identities:
            _invalid("duplicate-document-operation")
        identities.add(identity)
        destructive += public["operation"] in {"REPLACE", "DELETE"}
        result_bytes += len(normalized.get("result_bytes", b""))
        changes.append(public)
    if destructive > MAX_DESTRUCTIVE_OPERATIONS:
        _capacity()
    if result_bytes > MAX_SOURCE_TOTAL_BYTES:
        _capacity()
    hard_delete_acceptance = _normalize_hard_delete_acceptance_v3(
        request["hard_delete_acceptance"]
    )
    normalized_request = {
        "schema_version": REQUEST_SCHEMA_VERSION,
        "operation": operation,
        "evidence": evidence,
        "document_changes": changes,
        "hard_delete_acceptance": hard_delete_acceptance,
    }
    if operation == "apply":
        approval = request["approval"]
        if type(approval) is not str or _APPROVAL.fullmatch(approval) is None:
            _invalid("invalid-approval")
        normalized_request["approval"] = approval
    if len(_canonical_bytes(normalized_request)) > MAX_REQUEST_BYTES:
        _capacity("request-capacity")
    return normalized_request


def _run_git(root, *arguments):
    try:
        completed = subprocess.run(
            ["git", "-C", os.fspath(root), *arguments],
            capture_output=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise InitCloseoutError(
            "invalid-request", "git-worktree-unavailable", "worktree-revalidation"
        ) from exc
    if completed.returncode != 0:
        raise InitCloseoutError(
            "invalid-request", "git-worktree-unavailable", "worktree-revalidation"
        )
    return completed.stdout.rstrip(b"\r\n")


def git_stable_identity_evidence(root):
    """Return stable repository/worktree hashes without enumerating worktree state."""
    top = Path(os.fsdecode(_run_git(root, "rev-parse", "--show-toplevel"))).absolute()
    if os.path.normcase(os.path.realpath(top)) != os.path.normcase(os.path.realpath(root)):
        raise InitCloseoutError(
            "invalid-request", "git-root-mismatch", "worktree-revalidation"
        )
    common = _run_git(root, "rev-parse", "--path-format=absolute", "--git-common-dir")
    git_dir = _run_git(root, "rev-parse", "--path-format=absolute", "--git-dir")
    repository_identity = hashlib.sha256(
        b"git-common-dir\0" + os.path.normcase(os.path.realpath(os.fsdecode(common))).encode("utf-8")
    ).hexdigest()
    worktree_identity = hashlib.sha256(
        b"git-worktree\0"
        + os.path.normcase(os.path.realpath(root)).encode("utf-8")
        + b"\0"
        + os.path.normcase(os.path.realpath(os.fsdecode(git_dir))).encode("utf-8")
    ).hexdigest()
    return {
        "repository_identity": repository_identity,
        "worktree_identity": worktree_identity,
    }


def git_identity_evidence(root):
    """Return stable hashes plus approval-time HEAD/status identity."""
    stable = git_stable_identity_evidence(root)
    head = _run_git(root, "rev-parse", "HEAD")
    status = _run_git_bounded_binary(
        root,
        ["status", "--porcelain=v1", "-z", "--untracked-files=all"],
        MAX_GIT_STATUS_BYTES,
        unavailable_classification="git-worktree-unavailable",
        capacity_classification="git-status-capacity",
        boundary="worktree-revalidation",
    )
    worktree_state_identity = hashlib.sha256(
        b"git-worktree\0" + head + b"\0" + hashlib.sha256(status).digest()
    ).hexdigest()
    return {
        **stable,
        "worktree_state_identity": worktree_state_identity,
    }


def _filesystem_control_starting_digests(root):
    digests = {}
    total = 0
    for relative in (
        ".diataxis/state.json",
        ".diataxis/findings.json",
        ".diataxis/events.jsonl",
        ".diataxis/local-map.json",
        "AGENTS.md",
        ".gitignore",
    ):
        target = safe_path(Path(root) / relative, root)
        if not os.path.lexists(target):
            digests[relative] = "sha256:ABSENT"
            continue
        try:
            if not target.is_file():
                raise OSError("control route is not a regular file")
            with target.open("rb") as stream:
                data = stream.read(MAX_SOURCE_FILE_BYTES + 1)
        except OSError as exc:
            raise InitCloseoutError(
                "invalid-request",
                "worktree-state-unavailable",
                "worktree-revalidation",
            ) from exc
        if len(data) > MAX_SOURCE_FILE_BYTES:
            _capacity("worktree-state-capacity")
        total += len(data)
        if total > MAX_SOURCE_TOTAL_BYTES:
            _capacity("worktree-state-capacity")
        digests[relative] = "sha256:" + hashlib.sha256(data).hexdigest()
    return digests


def filesystem_identity_evidence(root, starting_corpus, dispositions):
    real_root = os.path.normcase(os.path.realpath(root))
    encoded_root = real_root.encode("utf-8")
    repository_identity = hashlib.sha256(
        b"filesystem-repository\0" + encoded_root
    ).hexdigest()
    worktree_identity = hashlib.sha256(
        b"filesystem-worktree\0" + encoded_root
    ).hexdigest()
    source_digests = dict(
        sorted((item["path"], item["source_digest"]) for item in dispositions)
    )
    worktree_state_identity = hashlib.sha256(
        _canonical_bytes(
            {
                "starting_corpus": starting_corpus,
                "source_digests": source_digests,
                "control_starting_digests": _filesystem_control_starting_digests(
                    root
                ),
            }
        )
    ).hexdigest()
    return {
        "worktree_kind": "filesystem",
        "repository_identity": repository_identity,
        "worktree_identity": worktree_identity,
        "worktree_state_identity": worktree_state_identity,
    }


def _worktree_evidence(root, starting_corpus=None, dispositions=None):
    if _git_available_v3(root):
        return {"worktree_kind": "git", **git_identity_evidence(root)}
    current = Path(root).absolute()
    while True:
        if os.path.lexists(current / ".git"):
            raise InitCloseoutError(
                "invalid-request", "git-worktree-unavailable", "worktree-revalidation"
            )
        if current.parent == current:
            break
        current = current.parent
    if starting_corpus is None or dispositions is None:
        raise InitCloseoutError(
            "invalid-request", "worktree-state-unavailable", "worktree-revalidation"
        )
    return filesystem_identity_evidence(root, starting_corpus, dispositions)


def _initialization_preflight_result(status, *, state=None):
    initialized = state.get("initialized", {}) if isinstance(state, dict) else {}
    rubric = state.get("rubric") if isinstance(state, dict) else None
    scores = state.get("structural_scores", {}) if isinstance(state, dict) else {}
    scope = state.get("scope") if isinstance(state, dict) else None
    return {
        "schema_version": REQUEST_SCHEMA_VERSION,
        "mode": "init-preflight",
        "status": status,
        "message": (
            ALREADY_INITIALIZED_MESSAGE
            if status == "already-initialized"
            else STATE_CONFLICT_MESSAGE
        ),
        "user_action": None if status == "already-initialized" else "run-doctor",
        "map": initialized.get("map"),
        "baseline": rubric,
        "structural_score": scores.get("after"),
        "scope": scope,
        "candidate_traversal": 0,
        "content_reads": 0,
        "writes": 0,
    }


def inspect_initialization_preflight(root, *, control_present=False):
    """Return a bounded terminal Init result when operational state exists.

    The absent-control case returns ``None`` so ordinary discovery can proceed.
    Existing control is inspected without opening documentation bodies; an
    incomplete or conflicting control plane fails closed to Doctor.
    """
    root = Path(root).absolute()
    control = root / STATE_DIRECTORY
    if not control_present and not os.path.lexists(control):
        return None

    state = None
    try:
        if _is_benign_initialization_residue(root):
            return None
        memory_findings = inspect_operational_memory(
            root, inspect_protected_intent=False
        )
        state = load_operational_state(root)
        findings = load_operational_findings(root)
        events = load_operational_events(root)
        if (
            state is None
            or state.get("schema_version") != STATE_SCHEMA_VERSION
            or state.get("initialized", {}).get("completed") is not True
            or not events
            or any(item.get("priority") in {"P0", "P1"} for item in memory_findings)
            or any(item.get("priority") == "P0" for item in findings["findings"])
        ):
            return _initialization_preflight_result("state-conflict", state=state)

        local_map_required = any(
            isinstance(event.get("local_map_digest"), str) for event in events
        )
        if local_map_required:
            identity = git_stable_identity_evidence(root)
            local_map = inspect_local_map(
                root,
                repository_identity=identity["repository_identity"],
                worktree_identity=identity["worktree_identity"],
            )
            if (
                local_map.get("status") != "present-uninspected"
                or local_map.get("binding") != "matched"
            ):
                return _initialization_preflight_result(
                    "state-conflict", state=state
                )
    except (InitCloseoutError, KeyError, OSError, TypeError, UnicodeError, ValueError):
        return _initialization_preflight_result("state-conflict", state=state)

    return _initialization_preflight_result("already-initialized", state=state)


def _verify_disposition_sources(root, selected_scope, dispositions):
    selected_scope = normalize_repo_relative(selected_scope, "selected scope")
    observed = {}
    total = 0
    for item in dispositions:
        path = normalize_repo_relative(item["path"], "disposition path")
        path_key = os.path.normcase(path).replace("\\", "/")
        scope_key = os.path.normcase(selected_scope).replace("\\", "/")
        if scope_key != "." and path_key != scope_key and not path_key.startswith(scope_key + "/"):
            raise InitCloseoutError(
                "invalid-request", "disposition-outside-scope", "evidence-revalidation"
            )
        if path_key.split("/", 1)[0].casefold() == ".local":
            raise InitCloseoutError(
                "invalid-request", "private-route-in-shared-manifest", "evidence-revalidation"
            )
        if path not in observed:
            target = safe_path(Path(root) / path, root)
            try:
                with target.open("rb") as handle:
                    data = handle.read(MAX_SOURCE_FILE_BYTES + 1)
            except OSError as exc:
                raise InitCloseoutError(
                    "stale-preview", "source-unavailable", "evidence-revalidation"
                ) from exc
            if len(data) > MAX_SOURCE_FILE_BYTES:
                raise InitCloseoutError(
                    "invalid-request", "source-file-capacity", "evidence-revalidation"
                )
            total += len(data)
            if total > MAX_SOURCE_TOTAL_BYTES:
                raise InitCloseoutError(
                    "invalid-request", "source-corpus-capacity", "evidence-revalidation"
                )
            observed[path] = "sha256:" + hashlib.sha256(data).hexdigest()
        if item["source_digest"] != observed[path]:
            raise InitCloseoutError(
                "stale-preview", "source-digest-mismatch", "evidence-revalidation"
            )
    return {
        "files": len(observed),
        "bytes": total,
        "digest": hashlib.sha256(_canonical_bytes(observed)).hexdigest(),
    }


def _read_document_bytes_v3(root, relative, *, missing_ok=False):
    target = safe_path(Path(root) / relative, root)
    if not os.path.lexists(target):
        if missing_ok:
            return None
        raise InitCloseoutError(
            "stale-preview", "source-unavailable", "document-revalidation"
        )
    try:
        if not target.is_file():
            raise OSError("document route is not a regular file")
        with target.open("rb") as stream:
            data = stream.read(MAX_SOURCE_FILE_BYTES + 1)
    except OSError as exc:
        raise InitCloseoutError(
            "stale-preview", "source-unavailable", "document-revalidation"
        ) from exc
    if len(data) > MAX_SOURCE_FILE_BYTES:
        _capacity()
    return data


def _git_available_v3(root):
    try:
        completed = subprocess.run(
            ["git", "-C", os.fspath(root), "rev-parse", "--is-inside-work-tree"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return completed.returncode == 0


def _run_git_bounded_binary(
    root,
    arguments,
    capacity,
    *,
    unavailable_classification="recovery-unavailable",
    capacity_classification="request-capacity",
    boundary="recovery-verification",
):
    try:
        process = subprocess.Popen(
            ["git", "-C", os.fspath(root), *arguments],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        )
    except OSError as exc:
        raise InitCloseoutError(
            "invalid-request", unavailable_classification, boundary
        ) from exc
    timer = threading.Timer(5, process.kill)
    timer.daemon = True
    timer.start()
    try:
        output = process.stdout.read(capacity + 1)
        if len(output) > capacity:
            process.kill()
            process.wait()
            _capacity(capacity_classification)
        returncode = process.wait()
    finally:
        timer.cancel()
        if process.stdout is not None:
            process.stdout.close()
    if returncode != 0:
        raise InitCloseoutError(
            "invalid-request", unavailable_classification, boundary
        )
    return output


def _verify_git_recovery_v3(root, item, source_bytes):
    recovery = item["recovery"]
    if not _git_available_v3(root):
        raise InitCloseoutError(
            "invalid-request", "recovery-unavailable", "recovery-verification"
        )
    blob_bytes = _run_git_bounded_binary(
        root,
        ["cat-file", "blob", recovery["blob"]],
        MAX_SOURCE_FILE_BYTES,
    )
    actual_digest = "sha256:" + hashlib.sha256(source_bytes).hexdigest()
    if (
        blob_bytes != source_bytes
        or recovery["digest"] != actual_digest
        or "sha256:" + hashlib.sha256(blob_bytes).hexdigest() != recovery["digest"]
    ):
        raise InitCloseoutError(
            "invalid-request", "recovery-mismatch", "recovery-verification"
        )
    tree = _run_git_bounded_binary(
        root,
        ["ls-tree", "-z", recovery["commit"], "--", item["path"]],
        1024 * 1024,
    )
    entries = [entry for entry in tree.split(b"\0") if entry]
    try:
        header, tree_path = entries[0].split(b"\t", 1)
        mode, object_type, object_id = header.split(b" ", 2)
    except (IndexError, ValueError) as exc:
        raise InitCloseoutError(
            "invalid-request", "recovery-mismatch", "recovery-verification"
        ) from exc
    if (
        len(entries) != 1
        or not mode
        or object_type != b"blob"
        or object_id.decode("ascii", "strict") != recovery["blob"]
        or tree_path != item["path"].encode("utf-8")
    ):
        raise InitCloseoutError(
            "invalid-request", "recovery-mismatch", "recovery-verification"
        )


def _document_result_v3(operation, path, role, source_item_ids, starting, result):
    return {
        "path": path,
        "operation": operation,
        "role": role,
        "starting_digest": (
            "sha256:ABSENT"
            if starting is None
            else "sha256:" + hashlib.sha256(starting).hexdigest()
        ),
        "result_digest": (
            "sha256:ABSENT"
            if result is None
            else "sha256:" + hashlib.sha256(result).hexdigest()
        ),
        "bytes": 0 if result is None else len(result),
        "source_item_ids": list(source_item_ids),
    }


def derive_document_transition_v3(
    root,
    starting_scan,
    dispositions,
    document_changes,
    hard_delete_acceptance=None,
):
    """Derive exact document actions from whole-file dispositions and verify recovery."""
    root = Path(root).absolute()
    safe_path(root, root)
    if (
        type(starting_scan) is not dict
        or starting_scan.get("complete") is not True
        or type(starting_scan.get("corpus")) is not dict
    ):
        raise InitCloseoutError(
            "invalid-request", "incomplete-corpus", "disposition-matrix"
        )
    boundary = starting_scan["corpus"]["write_boundary"]
    normalized_dispositions = [
        _normalize_disposition_v3(item, boundary) for item in dispositions
    ]
    base_by_identity = {
        _path_identity(item["path"]): item
        for item in normalized_dispositions
        if item["section"] == {"kind": "whole-file"}
    }
    section_ids = set()
    for item in normalized_dispositions:
        if item["section"] == {"kind": "whole-file"}:
            continue
        if item["item_id"] in section_ids:
            raise InitCloseoutError(
                "invalid-request",
                "duplicate-section-item-id",
                "disposition-matrix",
            )
        section_ids.add(item["item_id"])
        base = base_by_identity.get(_path_identity(item["path"]))
        if base is None:
            raise InitCloseoutError(
                "invalid-request", "section-base-required", "disposition-matrix"
            )
        if base["disposition"] != "RETAIN":
            raise InitCloseoutError(
                "invalid-request",
                "section-base-not-retained",
                "disposition-matrix",
            )
    try:
        normalized_dispositions = list(
            validate_corpus_coverage(starting_scan, normalized_dispositions)
        )
    except CorpusValidationError as exc:
        raise InitCloseoutError(
            "invalid-request", exc.classification, "disposition-matrix"
        ) from exc
    if any(item["disposition"] == "UNRESOLVED" for item in normalized_dispositions):
        raise InitCloseoutError(
            "requires-user-action",
            "unresolved-disposition",
            "disposition-matrix",
        )

    normalized_changes = []
    change_identities = set()
    for raw in document_changes:
        normalized = _normalize_document_change_v3(raw, boundary)
        public = normalized["public"]
        identity = _path_identity(public["path"])
        if identity in change_identities:
            raise InitCloseoutError(
                "invalid-request", "duplicate-document-operation", "disposition-matrix"
            )
        change_identities.add(identity)
        normalized_changes.append(normalized)
    try:
        derive_result_corpus(
            starting_scan,
            [item["public"] for item in normalized_changes],
        )
    except (CorpusValidationError, TypeError, ValueError) as exc:
        raise InitCloseoutError(
            "invalid-request", "invalid-document-transition", "disposition-matrix"
        ) from exc

    source_bytes = {}
    source_total = 0
    item_by_id = {}
    item_by_path = {}
    section_items_by_path = {}
    for item in normalized_dispositions:
        if item["path"] not in source_bytes:
            data = _read_document_bytes_v3(root, item["path"])
            source_total += len(data)
            if source_total > MAX_SOURCE_TOTAL_BYTES:
                _capacity()
            source_bytes[item["path"]] = data
        else:
            data = source_bytes[item["path"]]
        digest = "sha256:" + hashlib.sha256(data).hexdigest()
        if digest != item["source_digest"]:
            raise InitCloseoutError(
                "stale-preview", "source-digest-mismatch", "document-revalidation"
            )
        item_by_id[item["item_id"]] = item
        identity = _path_identity(item["path"])
        if item["section"] == {"kind": "whole-file"}:
            item_by_path[identity] = item
        else:
            section_items_by_path.setdefault(identity, []).append(item)

    source_path_by_identity = {
        _path_identity(path): path for path in source_bytes
    }

    def read_source_or_route(path, *, missing_ok=False):
        source_path = source_path_by_identity.get(_path_identity(path))
        if source_path is not None:
            if source_path != path:
                raise InitCloseoutError(
                    "invalid-request",
                    "document-path-spelling-mismatch",
                    "document-revalidation",
                )
            return source_bytes[source_path]
        return _read_document_bytes_v3(root, path, missing_ok=missing_ok)

    changes_by_path = {
        _path_identity(item["public"]["path"]): item for item in normalized_changes
    }
    archive_targets = {}
    for item in normalized_dispositions:
        recovery = item.get("recovery")
        if not recovery or recovery["kind"] != "archive":
            continue
        source_identity = _path_identity(item["path"])
        target_identity = _path_identity(recovery["path"])
        prior = archive_targets.get(target_identity)
        if source_identity == target_identity or (
            prior is not None
            and (
                _path_identity(prior["path"]) != source_identity
                or prior["recovery"] != recovery
            )
        ):
            raise InitCloseoutError(
                "invalid-request",
                "archive-recovery-collision",
                "recovery-verification",
            )
        archive_targets[target_identity] = item
    for target_identity, item in archive_targets.items():
        if (
            item["recovery"]["mode"] == "existing"
            and target_identity in changes_by_path
        ):
            raise InitCloseoutError(
                "invalid-request",
                "archive-recovery-collision",
                "recovery-verification",
            )
    expected = {}

    def expect(
        path,
        operation,
        source_item_ids,
        role,
        required_bytes=None,
        mismatch="archive-bytes-mismatch",
    ):
        identity = _path_identity(path)
        value = {
            "path": path,
            "operation": operation,
            "source_item_ids": list(source_item_ids),
            "role": role,
            "required_bytes": required_bytes,
            "mismatch": mismatch,
        }
        if identity in expected and expected[identity] != value:
            raise InitCloseoutError(
                "invalid-request", "document-operation-collision", "disposition-matrix"
            )
        expected[identity] = value

    empty_adoption_path = starting_scan.get("empty_adoption_path")
    if (
        starting_scan["corpus"].get("coverage_mode") == "empty-adoption"
        and not starting_scan.get("paths")
        and not normalized_dispositions
        and empty_adoption_path is not None
    ):
        empty_adoption_path = _normalize_shared_path_v3(
            empty_adoption_path,
            "empty-adoption-path",
            boundary=boundary,
            markdown=True,
        )
        adoption_change = changes_by_path.get(_path_identity(empty_adoption_path))
        if (
            adoption_change is not None
            and adoption_change["public"]["path"] == empty_adoption_path
            and adoption_change["public"]["operation"] == "CREATE"
            and adoption_change["public"]["source_item_ids"] == []
        ):
            expect(
                empty_adoption_path,
                "CREATE",
                [],
                "document-result",
            )

    def verify_archive_recovery(item, data):
        recovery = item["recovery"]
        if recovery["digest"] != item["source_digest"]:
            raise InitCloseoutError(
                "invalid-request", "recovery-mismatch", "recovery-verification"
            )
        archive = read_source_or_route(
            recovery["path"],
            missing_ok=True,
        )
        if archive is None:
            if recovery["mode"] != "planned":
                raise InitCloseoutError(
                    "invalid-request", "recovery-mismatch", "recovery-verification"
                )
            expect(
                recovery["path"],
                "CREATE",
                [item["item_id"]],
                "recovery-archive",
                required_bytes=data,
            )
        elif recovery["mode"] != "existing" or archive != data:
            raise InitCloseoutError(
                "invalid-request", "recovery-mismatch", "recovery-verification"
            )

    git_available = _git_available_v3(root)
    if not git_available:
        for item in normalized_dispositions:
            if (
                item["section"] == {"kind": "whole-file"}
                and item["disposition"] == "DISCARDED"
                and item["recovery"]["kind"] == "archive"
            ):
                item["disposition"] = "ARCHIVED"
                item["target"] = item["recovery"]["path"]

    hard_delete_items = [
        item
        for item in normalized_dispositions
        if item["disposition"] == "DISCARDED"
        and item["recovery"]["kind"] == "hard-delete-request"
    ]
    if hard_delete_acceptance is not None and not hard_delete_items:
        raise InitCloseoutError(
            "invalid-request",
            "unexpected-hard-delete-acceptance",
            "recovery-verification",
        )
    if hard_delete_items:
        if git_available:
            raise InitCloseoutError(
                "invalid-request", "recovery-mismatch", "recovery-verification"
            )
        discard_payload = sorted(
            (
                {
                    "item_id": item["item_id"],
                    "source_digest": item["source_digest"],
                }
                for item in hard_delete_items
            ),
            key=lambda value: value["item_id"],
        )
        discard_set_id = "DISCARD-" + hashlib.sha256(
            _canonical_bytes(discard_payload)
        ).hexdigest()[:16].upper()
        acceptance = (
            "Approve hard deletion of discard set "
            f"{discard_set_id}; I accept that no repository recovery is available."
        )
        if hard_delete_acceptance is None:
            raise InitCloseoutError(
                "risk-acceptance-required",
                "no-recovery-hard-delete",
                "recovery-verification",
                discard_set_id=discard_set_id,
                acceptance=acceptance,
            )
        if hard_delete_acceptance != {
            "discard_set_id": discard_set_id,
            "acceptance": acceptance,
        }:
            raise InitCloseoutError(
                "invalid-request",
                "hard-delete-acceptance-mismatch",
                "recovery-verification",
            )
        filesystem_identity = filesystem_identity_evidence(
            root,
            starting_scan["corpus"],
            normalized_dispositions,
        )
        acceptance_digest = "sha256:" + hashlib.sha256(
            _canonical_bytes(
                {
                    "acceptance": acceptance,
                    "repository_identity": filesystem_identity[
                        "repository_identity"
                    ],
                    "worktree_identity": filesystem_identity["worktree_identity"],
                    "worktree_state_identity": filesystem_identity[
                        "worktree_state_identity"
                    ],
                    "starting_corpus": starting_scan["corpus"],
                }
            )
        ).hexdigest()
        for item in hard_delete_items:
            item["recovery"] = {
                "kind": "accepted-hard-delete",
                "discard_set_id": discard_set_id,
                "acceptance_digest": acceptance_digest,
            }

    if section_items_by_path:
        from .scan import parse_atx_sections

        for source_identity, section_items in section_items_by_path.items():
            base = item_by_path[source_identity]
            source_path = base["path"]
            data = source_bytes[source_path]
            try:
                parsed_sections = parse_atx_sections(data)
            except (TypeError, UnicodeError, ValueError) as exc:
                raise InitCloseoutError(
                    "invalid-request",
                    getattr(exc, "classification", "malformed-section-source"),
                    "section-revalidation",
                ) from exc

            selected_sections = []
            for item in section_items:
                section = item["section"]
                if section not in parsed_sections:
                    matching_span = [
                        candidate
                        for candidate in parsed_sections
                        if candidate["start_byte"] == section["start_byte"]
                        and candidate["end_byte"] == section["end_byte"]
                        and candidate["raw_span_digest"]
                        == section["raw_span_digest"]
                    ]
                    ambiguous = any(
                        candidate["level"] == section["level"]
                        and candidate["heading_path"] == section["heading_path"]
                        and candidate["occurrence"] != section["occurrence"]
                        for candidate in matching_span
                    )
                    raise InitCloseoutError(
                        "invalid-request",
                        (
                            "ambiguous-section-identity"
                            if ambiguous
                            else "stale-section-span"
                        ),
                        "section-revalidation",
                    )
                selected_sections.append(section)

            ordered_sections = sorted(
                selected_sections,
                key=lambda section: (section["start_byte"], section["end_byte"]),
            )
            for prior, current in zip(ordered_sections, ordered_sections[1:]):
                if current["start_byte"] < prior["end_byte"]:
                    raise InitCloseoutError(
                        "invalid-request",
                        "overlapping-section-spans",
                        "section-revalidation",
                    )

            source_ids = sorted(item["item_id"] for item in section_items)
            recovery = section_items[0]["recovery"]
            if (
                any(item["recovery"] != recovery for item in section_items)
                or any(item["source_digest"] != base["source_digest"] for item in section_items)
                or recovery.get("digest") != base["source_digest"]
            ):
                raise InitCloseoutError(
                    "invalid-request", "recovery-mismatch", "recovery-verification"
                )
            if recovery["kind"] == "git":
                _verify_git_recovery_v3(root, section_items[0], data)
            elif recovery["kind"] == "archive":
                archive = read_source_or_route(
                    recovery["path"],
                    missing_ok=True,
                )
                if archive is None:
                    if recovery["mode"] != "planned":
                        raise InitCloseoutError(
                            "invalid-request",
                            "recovery-mismatch",
                            "recovery-verification",
                        )
                    expect(
                        recovery["path"],
                        "CREATE",
                        source_ids,
                        "recovery-archive",
                        required_bytes=data,
                    )
                elif recovery["mode"] != "existing" or archive != data:
                    raise InitCloseoutError(
                        "invalid-request",
                        "recovery-mismatch",
                        "recovery-verification",
                    )
            else:
                raise InitCloseoutError(
                    "invalid-request",
                    "section-hard-delete-forbidden",
                    "recovery-verification",
                )

            result = data
            for section in reversed(ordered_sections):
                result = (
                    result[: section["start_byte"]]
                    + result[section["end_byte"] :]
                )
            expect(
                source_path,
                "REPLACE",
                source_ids,
                "document-source",
                required_bytes=result,
                mismatch="section-result-mismatch",
            )

            for item in section_items:
                outcome = item["disposition"]
                if outcome in {"MIGRATED", "ARCHIVED"}:
                    target = item["target"]
                    if _path_identity(target) == source_identity:
                        raise InitCloseoutError(
                            "invalid-request",
                            "section-target-collision",
                            "disposition-matrix",
                        )
                    if read_source_or_route(target, missing_ok=True) is not None:
                        raise InitCloseoutError(
                            "invalid-request",
                            "section-target-not-absent",
                            "disposition-matrix",
                        )
                    section = item["section"]
                    expect(
                        target,
                        "CREATE",
                        [item["item_id"]],
                        "document-result",
                        required_bytes=(
                            data[section["start_byte"] : section["end_byte"]]
                            if outcome == "ARCHIVED"
                            else None
                        ),
                    )
                elif outcome == "DEDUPLICATED":
                    target_identity = _path_identity(item["target"])
                    if target_identity == source_identity:
                        raise InitCloseoutError(
                            "invalid-request",
                            "section-target-collision",
                            "disposition-matrix",
                        )
                    target_data = read_source_or_route(item["target"])
                    target_digest = (
                        "sha256:" + hashlib.sha256(target_data).hexdigest()
                    )
                    if target_digest != item["target_digest"]:
                        raise InitCloseoutError(
                            "stale-preview",
                            "dedup-target-mismatch",
                            "disposition-matrix",
                        )
                    target_base = item_by_path.get(target_identity)
                    if (
                        target_base is not None
                        and (
                            target_base["disposition"] != "RETAIN"
                            or target_identity in section_items_by_path
                        )
                    ):
                        raise InitCloseoutError(
                            "invalid-request",
                            "dedup-target-not-retained",
                            "disposition-matrix",
                        )
                    if target_identity in changes_by_path:
                        raise InitCloseoutError(
                            "invalid-request",
                            "dedup-target-mutated",
                            "disposition-matrix",
                        )

    migrations = {}
    for item in normalized_dispositions:
        if (
            item["section"] == {"kind": "whole-file"}
            and item["disposition"] == "MIGRATED"
        ):
            migrations.setdefault(_path_identity(item["target"]), []).append(item)

    for target_identity, group in migrations.items():
        target = group[0]["target"]
        if any(item["target"] != target for item in group):
            raise InitCloseoutError(
                "invalid-request", "migration-target-collision", "disposition-matrix"
            )
        group_ids = sorted(item["item_id"] for item in group)
        existing_target = item_by_path.get(target_identity)
        if existing_target is None:
            expect(target, "CREATE", group_ids, "document-result")
        else:
            if existing_target not in group:
                raise InitCloseoutError(
                    "invalid-request", "migration-target-not-in-group", "disposition-matrix"
                )
            expect(target, "REPLACE", group_ids, "document-result")
        for item in group:
            if item["recovery"]["kind"] == "git":
                _verify_git_recovery_v3(root, item, source_bytes[item["path"]])
            elif item["recovery"]["kind"] == "archive":
                verify_archive_recovery(item, source_bytes[item["path"]])
            else:
                raise InitCloseoutError(
                    "invalid-request", "recovery-mismatch", "recovery-verification"
                )
            if _path_identity(item["path"]) != target_identity:
                expect(
                    item["path"],
                    "DELETE",
                    [item["item_id"]],
                    "document-source",
                )

    for item in normalized_dispositions:
        if item["section"] != {"kind": "whole-file"}:
            continue
        outcome = item["disposition"]
        if outcome in {"MIGRATED", "UNRESOLVED"}:
            continue
        data = source_bytes[item["path"]]
        uses = [
            change
            for change in normalized_changes
            if item["item_id"] in change["public"]["source_item_ids"]
        ]
        if outcome == "RETAIN":
            identity = _path_identity(item["path"])
            if uses or (
                identity in changes_by_path and identity not in section_items_by_path
            ):
                raise InitCloseoutError(
                    "invalid-request", "retain-authorizes-no-bytes", "disposition-matrix"
                )
            continue
        if outcome == "DEDUPLICATED":
            target_data = read_source_or_route(item["target"])
            if (
                "sha256:" + hashlib.sha256(target_data).hexdigest()
                != item["target_digest"]
            ):
                raise InitCloseoutError(
                    "stale-preview", "dedup-target-mismatch", "disposition-matrix"
                )
            target_item = item_by_path.get(_path_identity(item["target"]))
            if target_item is not None and target_item["disposition"] != "RETAIN":
                raise InitCloseoutError(
                    "invalid-request", "dedup-target-not-retained", "disposition-matrix"
                )
            if _path_identity(item["target"]) in changes_by_path:
                raise InitCloseoutError(
                    "invalid-request", "dedup-target-mutated", "disposition-matrix"
                )
            if item["recovery"]["kind"] == "git":
                _verify_git_recovery_v3(root, item, data)
            elif item["recovery"]["kind"] == "archive":
                verify_archive_recovery(item, data)
            else:
                raise InitCloseoutError(
                    "invalid-request", "recovery-mismatch", "recovery-verification"
                )
            expect(item["path"], "DELETE", [item["item_id"]], "document-source")
            continue
        if outcome == "ARCHIVED" or (
            outcome == "DISCARDED" and item["recovery"]["kind"] == "archive"
        ):
            recovery = item["recovery"]
            target = item.get("target", recovery["path"])
            if recovery["path"] != target or recovery["digest"] != item["source_digest"]:
                raise InitCloseoutError(
                    "invalid-request", "recovery-mismatch", "recovery-verification"
                )
            target_data = read_source_or_route(target, missing_ok=True)
            if target_data is None:
                if recovery["mode"] != "planned":
                    raise InitCloseoutError(
                        "invalid-request", "recovery-mismatch", "recovery-verification"
                    )
                expect(
                    target,
                    "CREATE",
                    [item["item_id"]],
                    "recovery-archive",
                    required_bytes=data,
                )
            elif recovery["mode"] != "existing" or target_data != data:
                raise InitCloseoutError(
                    "invalid-request", "recovery-mismatch", "recovery-verification"
                )
            expect(item["path"], "DELETE", [item["item_id"]], "document-source")
            continue
        if outcome == "DISCARDED":
            recovery = item["recovery"]
            if recovery["kind"] == "git":
                _verify_git_recovery_v3(root, item, data)
            elif recovery["kind"] == "accepted-hard-delete":
                pass
            else:
                raise InitCloseoutError(
                    "invalid-request", "recovery-mismatch", "recovery-verification"
                )
            expect(item["path"], "DELETE", [item["item_id"]], "document-source")

    if set(expected) != set(changes_by_path):
        raise InitCloseoutError(
            "invalid-request", "orphan-document-operation", "disposition-matrix"
        )

    operations = []
    document_results = []
    for identity, requirement in sorted(
        expected.items(),
        key=lambda pair: (pair[1]["path"].casefold(), pair[1]["path"]),
    ):
        normalized = changes_by_path[identity]
        public = normalized["public"]
        if (
            public["path"] != requirement["path"]
            or public["operation"] != requirement["operation"]
            or public["source_item_ids"] != requirement["source_item_ids"]
        ):
            raise InitCloseoutError(
                "invalid-request", "document-operation-mismatch", "disposition-matrix"
            )
        result_bytes = normalized.get("result_bytes")
        if (
            requirement["required_bytes"] is not None
            and result_bytes != requirement["required_bytes"]
        ):
            raise InitCloseoutError(
                "invalid-request", requirement["mismatch"], "disposition-matrix"
            )
        if public["operation"] == "CREATE":
            starting = None
        elif _path_identity(public["path"]) in source_path_by_identity:
            starting = read_source_or_route(public["path"])
        else:
            starting = read_source_or_route(public["path"])
        result = None if public["operation"] == "DELETE" else result_bytes
        document_result = _document_result_v3(
            public["operation"],
            public["path"],
            requirement["role"],
            public["source_item_ids"],
            starting,
            result,
        )
        document_results.append(document_result)
        recovery_binding = None
        if public["operation"] != "CREATE":
            recovery_objects = []
            source_items = []
            for item_id in public["source_item_ids"]:
                source_item = item_by_id.get(item_id)
                recovery = None if source_item is None else source_item.get("recovery")
                if recovery is None:
                    raise InitCloseoutError(
                        "invalid-request",
                        "recovery-mismatch",
                        "transaction-preparation",
                    )
                source_items.append(source_item)
                recovery_objects.append(
                    {"item_id": item_id, "recovery": copy.deepcopy(recovery)}
                )
            section_recovery = bool(source_items) and all(
                item["section"] != {"kind": "whole-file"}
                for item in source_items
            )
            if section_recovery and all(
                item["recovery"] == source_items[0]["recovery"]
                for item in source_items
            ):
                recovery_payload = source_items[0]["recovery"]
            else:
                recovery_payload = (
                    recovery_objects[0]["recovery"]
                    if len(recovery_objects) == 1
                    else recovery_objects
                )
            recovery_binding = "sha256:" + hashlib.sha256(
                _canonical_bytes(recovery_payload)
            ).hexdigest()
        operation = {
            "operation": public["operation"],
            "path": public["path"],
            "role": requirement["role"],
            "starting_digest": document_result["starting_digest"],
            "result_digest": document_result["result_digest"],
        }
        if result is not None:
            operation["result_bytes"] = result
        operation["source_item_ids"] = list(public["source_item_ids"])
        operation["recovery_binding"] = recovery_binding
        operations.append(operation)

    source_digests = {
        path: "sha256:" + hashlib.sha256(data).hexdigest()
        for path, data in sorted(
            source_bytes.items(),
            key=lambda item: (item[0].casefold(), item[0]),
        )
    }
    return {
        "dispositions": normalized_dispositions,
        "document_results": document_results,
        "operations": operations,
        "changed_paths": [item["path"] for item in document_results],
        "source_byte_counts": {
            path: len(data)
            for path, data in sorted(
                source_bytes.items(),
                key=lambda item: (item[0].casefold(), item[0]),
            )
        },
        "source_receipt": {
            "files": len(source_bytes),
            "bytes": source_total,
            "digest": hashlib.sha256(_canonical_bytes(source_digests)).hexdigest(),
        },
    }


def _bind_result_state_evidence_v3(
    evidence,
    starting_scan,
    transition,
    *,
    failure_status,
):
    """Require route and byte claims to equal the derived post-transition state."""
    starting_paths = {
        _path_identity(path): path for path in starting_scan["paths"]
    }
    starting_bytes = {
        _path_identity(path): byte_count
        for path, byte_count in transition["source_byte_counts"].items()
    }
    if set(starting_paths) != set(starting_bytes):
        raise InitCloseoutError(
            failure_status,
            "hot-path-before-mismatch",
            "evidence-revalidation",
        )

    result_paths = dict(starting_paths)
    result_bytes = dict(starting_bytes)
    for operation in transition["operations"]:
        path = operation["path"]
        identity = _path_identity(path)
        if operation["operation"] == "DELETE":
            result_paths.pop(identity, None)
            result_bytes.pop(identity, None)
        else:
            data = operation.get("result_bytes")
            if type(data) is not bytes:
                raise InitCloseoutError(
                    failure_status,
                    "hot-path-after-mismatch",
                    "evidence-revalidation",
                )
            result_paths[identity] = path
            result_bytes[identity] = len(data)

    map_path = evidence["map_path"]
    if result_paths.get(_path_identity(map_path)) != map_path:
        raise InitCloseoutError(
            failure_status,
            "map-not-in-result-corpus",
            "evidence-revalidation",
        )
    for route in evidence["current_truth_routes"]:
        if result_paths.get(_path_identity(route)) != route:
            raise InitCloseoutError(
                failure_status,
                "current-truth-not-in-result-corpus",
                "evidence-revalidation",
            )

    declared_routes = {}
    for route in (map_path, *evidence["current_truth_routes"]):
        declared_routes.setdefault(_path_identity(route), route)

    def observation(routes, byte_counts):
        provenance = [
            {
                "route": route,
                "bytes": byte_counts[_path_identity(route)],
                "source": "filesystem-stat",
            }
            for route in sorted(routes, key=lambda item: (item.casefold(), item))
        ]
        return {
            "value": sum(item["bytes"] for item in provenance),
            "unit": "bytes",
            "provenance": provenance,
        }

    before_routes = [
        route
        for identity, route in declared_routes.items()
        if starting_paths.get(identity) == route
    ]
    expected_before = observation(before_routes, starting_bytes)
    expected_after = observation(declared_routes.values(), result_bytes)
    if evidence["hot_path_bytes"]["before"] != expected_before:
        raise InitCloseoutError(
            failure_status,
            "hot-path-before-mismatch",
            "evidence-revalidation",
        )
    if evidence["hot_path_bytes"]["after"] != expected_after:
        raise InitCloseoutError(
            failure_status,
            "hot-path-after-mismatch",
            "evidence-revalidation",
        )


def prepare_initialization_closeout(root, request):
    """Reconstruct one complete zero-write initialization closeout plan."""
    root = Path(root).absolute()
    safe_path(root, root)
    if type(request) is not dict or request.get("operation") not in {"preview", "apply"}:
        _invalid("invalid-request-contract")
    operation = request["operation"]
    request = validate_public_request(request, operation)
    evidence = request["evidence"]
    changes = evidence["source_changes"]

    coverage_mode = (
        "empty-adoption"
        if evidence["selected_scope"] == "." and not evidence["dispositions"]
        else "selected-scope-exact"
    )
    starting_scan = scan_selected_document_corpus(
        root,
        evidence["selected_scope"],
        coverage_mode,
    )
    failure_status = "stale-preview" if operation == "apply" else "invalid-request"
    if starting_scan.get("complete") is not True:
        boundary = starting_scan.get("boundary") or {}
        raise InitCloseoutError(
            failure_status,
            boundary.get("classification", "incomplete-corpus"),
            "corpus-revalidation",
        )
    if coverage_mode == "empty-adoption":
        starting_scan = copy.deepcopy(starting_scan)
        starting_scan["empty_adoption_path"] = evidence["map_path"]
    try:
        dispositions = list(
            validate_corpus_coverage(starting_scan, evidence["dispositions"])
        )
    except CorpusValidationError as exc:
        raise InitCloseoutError(
            failure_status,
            exc.classification,
            "corpus-revalidation",
        ) from exc
    transition = derive_document_transition_v3(
        root,
        starting_scan,
        dispositions,
        request["document_changes"],
        request["hard_delete_acceptance"],
    )
    dispositions = transition["dispositions"]
    document_results = transition["document_results"]
    corpus_transition = {
        "starting": copy.deepcopy(starting_scan["corpus"]),
        "result": derive_result_corpus(starting_scan, transition["operations"]),
    }
    _bind_result_state_evidence_v3(
        evidence,
        starting_scan,
        transition,
        failure_status=failure_status,
    )
    approvals = copy.deepcopy(evidence["approvals"])
    removed_items = [
        item["item_id"]
        for item in dispositions
        if item["disposition"] != "RETAIN"
    ]
    manifest = prepare_dispositions(
        None,
        dispositions,
        removed_items=removed_items,
        git_available=True,
        command="init",
        approval_bindings=approvals,
        corpus_transition=corpus_transition,
        document_results=document_results,
    )
    source_receipt = copy.deepcopy(transition["source_receipt"])
    worktree = _worktree_evidence(
        root,
        corpus_transition["starting"],
        manifest["dispositions"],
    )
    local_map = copy.deepcopy(evidence["local_map"])
    if local_map is not None and (
        type(local_map) is not dict
        or local_map.get("repository_identity") != worktree["repository_identity"]
        or local_map.get("worktree_identity") != worktree["worktree_identity"]
    ):
        raise InitCloseoutError(
            "invalid-request",
            "local-map-identity-mismatch",
            "worktree-revalidation",
        )
    state_inputs = {key: copy.deepcopy(evidence[key]) for key in _STATE_FIELDS}
    state = build_initialization_state(
        root,
        **state_inputs,
        manifest_identity=manifest["manifest_identity"],
        result_corpus=corpus_transition["result"],
        document_results_digest=manifest["document_results_digest"],
        last_completed_event="EVT-00000000",
    )
    event = copy.deepcopy(evidence["event"])
    event.update(worktree)
    source_change_names = []
    if changes["agents_orientation"]:
        source_change_names.append(INIT_AGENTS_ORIENTATION)
    if changes["local_map_ignore"]:
        source_change_names.append(INIT_LOCAL_MAP_IGNORE)
    source_targets = _prepare_init_source_targets(root, source_change_names)
    changed_paths = {
        ".diataxis/state.json",
        ".diataxis/findings.json",
        ".diataxis/events.jsonl",
        "manifest",
        *source_targets,
        *transition["changed_paths"],
    }
    if local_map is not None:
        changed_paths.add(".diataxis/local-map.json")
    event["changed_paths"] = sorted(changed_paths)
    plan = prepare_verified_closeout(
        root,
        command="init",
        state=state,
        findings=copy.deepcopy(evidence["findings"]),
        event=event,
        approvals=approvals,
        dispositions=manifest["dispositions"],
        removed_items=removed_items,
        local_map=local_map,
        selected_boundary=evidence["selected_scope"],
        init_source_changes=source_change_names,
        corpus_transition=corpus_transition,
        document_results=document_results,
        document_operations=transition["operations"],
    )
    if plan.get("status") != "approval-required":
        raise InitCloseoutError(
            "invalid-request",
            plan.get("reason", "closeout-preparation-failed"),
            "transaction-preparation",
        )
    actual_changed_paths = {
        "manifest"
        if relative.startswith(".diataxis/manifests/")
        else relative
        for relative in plan["targets"]
    }
    actual_changed_paths.update(
        operation["path"] for operation in plan["document_operations"]
    )
    if actual_changed_paths != set(plan["event"]["changed_paths"]):
        raise InitCloseoutError(
            "invalid-request",
            "derived-event-target-mismatch",
            "transaction-preparation",
        )
    preview_id = "INIT-" + plan["transaction_id"].removeprefix("TXN-")[:12]
    manifest_sha256 = manifest["manifest_identity"]
    approval = (
        f"Approve $docs init preview {preview_id} with manifest {manifest_sha256}"
    )
    disposition_summary = {
        outcome: sum(
            1 for item in dispositions if item["disposition"] == outcome
        )
        for outcome in (
            "RETAIN",
            "MIGRATED",
            "DEDUPLICATED",
            "ARCHIVED",
            "DISCARDED",
        )
    }
    disposition_summary = {
        outcome: count for outcome, count in disposition_summary.items() if count
    }
    return {
        "operation": operation,
        "plan": plan,
        "preview_id": preview_id,
        "manifest_sha256": manifest_sha256,
        "approval": approval,
        "source_receipt": source_receipt,
        "worktree": worktree,
        "dispositions": manifest["dispositions"],
        "selected_scope": evidence["selected_scope"],
        "local_map_present": local_map is not None,
        "corpus_transition": corpus_transition,
        "disposition_summary": disposition_summary,
        "document_change_count": len(document_results),
    }


def preview_response(prepared):
    plan = prepared["plan"]
    return {
        "schema_version": REQUEST_SCHEMA_VERSION,
        "status": "approval-required",
        "writes": 0,
        "preview_id": prepared["preview_id"],
        "manifest_sha256": prepared["manifest_sha256"],
        "transaction_id": plan["transaction_id"],
        "approval": prepared["approval"],
        "selected_scope": prepared["selected_scope"],
        "corpus_transition": copy.deepcopy(prepared["corpus_transition"]),
        "disposition_summary": copy.deepcopy(prepared["disposition_summary"]),
        "document_change_count": prepared["document_change_count"],
        "source_files_revalidated": prepared["source_receipt"]["files"],
        "successful_event_recorded": False,
    }


def _failure_response(
    prepared,
    *,
    status,
    classification,
    boundary,
    rollback_required=False,
    rollback_complete=True,
    rollback_outcomes=None,
    writes=0,
    partial_state="none",
):
    if rollback_outcomes is None:
        outcome = (
            "complete"
            if rollback_required and rollback_complete
            else "incomplete"
            if rollback_required
            else "not-required"
        )
        rollback_outcomes = {
            "documents": outcome,
            "controls": outcome,
            "cleanup": outcome,
        }
    response = {
        "schema_version": REQUEST_SCHEMA_VERSION,
        "status": status,
        "classification": classification,
        "boundary": boundary,
        "writes": writes,
        "partial_state": partial_state,
        "rollback": {
            "required": rollback_required,
            "complete": rollback_complete,
            "documents": rollback_outcomes["documents"],
            "controls": rollback_outcomes["controls"],
            "cleanup": rollback_outcomes["cleanup"],
        },
        "successful_event_recorded": False,
    }
    if prepared is not None:
        response.update(
            {
                "preview_id": prepared["preview_id"],
                "manifest_sha256": prepared["manifest_sha256"],
            }
        )
    return response


def _verify_retain_preapply(root, prepared):
    try:
        memory_findings = inspect_operational_memory(root)
        source_receipt = _verify_disposition_sources(
            root, prepared["selected_scope"], prepared["dispositions"]
        )
        worktree = _worktree_evidence(
            root,
            prepared["corpus_transition"]["starting"],
            prepared["dispositions"],
        )
    except (InitCloseoutError, OSError, TypeError, ValueError):
        return False
    return (
        source_receipt == prepared["source_receipt"]
        and worktree == prepared["worktree"]
        and not any(finding.get("priority") == "P0" for finding in memory_findings)
    )


def _normalize_apply_failure(prepared, result):
    status = result.get("status", "closeout-failed")
    if status == "verification-failed":
        return _failure_response(
            prepared,
            status=status,
            classification="retain-evidence-revalidation-failed",
            boundary="pre-apply-verification",
        )
    if status == "stale-target":
        return _failure_response(
            prepared,
            status=status,
            classification="stale-target",
            boundary="compare-before-write",
        )
    rollback_required = status == "closeout-failed"
    rollback = result.get("rollback")
    rollback_complete = True
    rollback_outcomes = None
    if rollback_required:
        rollback_complete = result.get("control_plane_rolled_back", True)
        if type(rollback) is dict:
            if type(rollback.get("complete")) is bool:
                rollback_complete = rollback["complete"]
            outcomes = rollback.get("outcomes")
            if (
                type(outcomes) is dict
                and set(outcomes) == {"documents", "controls", "cleanup"}
                and all(
                    value
                    in {
                        "not-required",
                        "not-run",
                        "complete",
                        "incomplete",
                        "unknown",
                    }
                    for value in outcomes.values()
                )
            ):
                rollback_outcomes = copy.deepcopy(outcomes)
    return _failure_response(
        prepared,
        status=status,
        classification=result.get("classification", "closeout-failed"),
        boundary=result.get("boundary", "transaction-application"),
        rollback_required=rollback_required,
        rollback_complete=rollback_complete,
        rollback_outcomes=rollback_outcomes,
        writes=0 if rollback_complete else "unknown",
        partial_state="none" if rollback_complete else "possible",
    )


def apply_response(root, prepared, approval):
    if approval != prepared["approval"]:
        return _failure_response(
            prepared,
            status="stale-preview",
            classification="approval-revalidation-mismatch",
            boundary="approval-revalidation",
        )
    plan = prepared["plan"]

    def verify_supported_retain_closeout():
        return _verify_retain_preapply(root, prepared)

    result = apply_verified_closeout(
        root,
        plan,
        approved_transaction=plan["transaction_id"],
        verification=verify_supported_retain_closeout,
    )
    if result.get("status") == "closeout-committed-cleanup-incomplete":
        return {
            "schema_version": REQUEST_SCHEMA_VERSION,
            "status": "closeout-committed-cleanup-incomplete",
            "preview_id": prepared["preview_id"],
            "manifest_sha256": prepared["manifest_sha256"],
            "transaction_id": result["transaction_id"],
            "event_id": result["event_id"],
            "corpus_transition": copy.deepcopy(prepared["corpus_transition"]),
            "verification": {
                "exact_installed_bytes": True,
                "event_last": True,
                "result_corpus": True,
                "local_map_ignored": (
                    True if prepared["local_map_present"] else "not-applicable"
                ),
            },
            "rollback": {
                "required": False,
                "complete": True,
                "documents": "not-required",
                "controls": "not-required",
                "cleanup": "incomplete",
            },
            "recovery": {
                "action": "finalize",
                "journal_digest": result["journal_digest"],
                "reconciled_state_digest": result["reconciled_state_digest"],
            },
            "writes": "committed",
            "partial_state": "committed",
            "user_action": "run-doctor",
            "successful_event_recorded": True,
        }
    if result.get("status") != "applied":
        return _normalize_apply_failure(prepared, result)

    return {
        "schema_version": REQUEST_SCHEMA_VERSION,
        "status": "applied",
        "preview_id": prepared["preview_id"],
        "manifest_sha256": prepared["manifest_sha256"],
        "transaction_id": result["transaction_id"],
        "event_id": result["event_id"],
        "corpus_transition": copy.deepcopy(prepared["corpus_transition"]),
        "verification": {
            # apply_verified_closeout verifies all staged and installed bytes,
            # the complete transaction set, Git ignore protection, and the
            # event-last order before it can return ``applied``.
            "exact_installed_bytes": True,
            "event_last": True,
            "result_corpus": True,
            "local_map_ignored": (
                True if prepared["local_map_present"] else "not-applicable"
            ),
        },
        "rollback": {
            "required": False,
            "complete": True,
            "documents": "not-required",
            "controls": "not-required",
            "cleanup": "not-required",
        },
        "successful_event_recorded": True,
    }


__all__ = (
    "ALREADY_INITIALIZED_MESSAGE",
    "InitCloseoutError",
    "MAX_REQUEST_BYTES",
    "REQUEST_SCHEMA_VERSION",
    "apply_response",
    "git_identity_evidence",
    "git_stable_identity_evidence",
    "inspect_initialization_preflight",
    "prepare_initialization_closeout",
    "preview_response",
    "validate_public_request",
)
