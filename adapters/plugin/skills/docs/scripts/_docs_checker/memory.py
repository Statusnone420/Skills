"""Strict, bounded, read-only operational-memory inspection."""

import hashlib
import json
import os
import re
import unicodedata
from collections.abc import Mapping, Sequence
from pathlib import Path

from .identity import (
    _FINDING_ID,
    _IDENTITY_PATH_FIELDS,
    _IDENTITY_PATH_LIST_FIELDS,
    _normalize_event_id,
    _normalize_fingerprint,
    _require_mapping,
    _require_sequence,
    _require_string,
    event_fingerprint,
    finding_fingerprint,
    finding_id,
    slug,
)
from .paths import _assert_no_reparse_components, normalize_repo_relative, safe_path


STATE_SCHEMA_VERSION = 1
STATE_DIRECTORY = ".diataxis"
STATE_FILE = "state.json"
FINDINGS_FILE = "findings.json"
EVENTS_FILE = "events.jsonl"
MAX_STATE_BYTES = 32 * 1024
MAX_FINDINGS_BYTES = 256 * 1024
MAX_EVENTS_BYTES = 256 * 1024
MAX_MANIFEST_BYTES = 256 * 1024
MAX_PROTECTED_INTENT_BYTES = 256 * 1024
MAX_PROTECTED_INTENT_TOTAL_BYTES = 1024 * 1024
MAX_PROTECTED_INTENTS = 64
MAX_CONTROL_ENTRIES = 256
MAX_JSON_DEPTH = 128
PRIORITIES = {"P0", "P1", "P2"}
FINDING_STATUSES = {"Proposed", "Approved", "Applied", "Parked"}

_MERGE_MARKER = re.compile(r"(?m)^(?:<<<<<<< |=======\s*$|>>>>>>> )")
_DIGEST = re.compile(r"^sha256-(?:text|bytes):[0-9a-f]{64}$")
_MANIFEST_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")
_SEMVER = re.compile(r"^(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)$")
_TRANSACTION_ID = re.compile(r"^TXN-[0-9A-F]{16}$")
_TRANSACTION_DIGEST = re.compile(r"^sha256:(?:[0-9a-f]{64}|ABSENT)$")
_LOCAL_MAP_PATH = ".diataxis/local-map.json"


class _OperationalMemoryIssue(ValueError):
    def __init__(self, kind, priority, path, detail):
        super().__init__(detail)
        self.kind = kind
        self.priority = priority
        self.path = path
        self.detail = detail


class _StrictJSONError(ValueError):
    pass


def _require_exact_keys(value, keys, name):
    actual = set(value)
    expected = set(keys)
    if actual != expected:
        raise ValueError(f"{name} has invalid fields")


def _require_int(value, name, *, minimum=None, maximum=None):
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError(f"{name} must be an integer")
    if minimum is not None and value < minimum:
        raise ValueError(f"{name} is below its minimum")
    if maximum is not None and value > maximum:
        raise ValueError(f"{name} is above its maximum")
    return value


def _normalize_checked_path(value, name, root):
    normalized = normalize_repo_relative(_require_string(value, name), name)
    safe_path(Path(root) / normalized, root)
    return normalized


def _normalize_checked_route(value, name, root):
    raw = _require_string(value, name)
    path, separator, anchor = raw.partition("#")
    if re.match(r"^[A-Za-z][A-Za-z0-9+.-]*:", path):
        raise ValueError(f"{name} must be repo-relative")
    normalized = _normalize_checked_path(path, name, root)
    if not separator:
        return normalized
    anchor = unicodedata.normalize("NFC", anchor.strip())
    if not anchor:
        raise ValueError(f"{name} anchor must not be empty")
    return f"{normalized}#{anchor}"


def _normalize_checked_pattern(value, name, root):
    normalized = normalize_repo_relative(_require_string(value, name), name)
    prefix = []
    for part in normalized.split("/"):
        if any(marker in part for marker in "*?["):
            break
        prefix.append(part)
    safe_path(Path(root).joinpath(*prefix), root)
    return normalized


def _normalize_digest(value, name):
    normalized = _require_string(value, name).lower()
    if not _DIGEST.fullmatch(normalized):
        raise ValueError(f"{name} must be a normalized SHA-256 digest")
    return normalized


def _operational_control(root):
    root = Path(root).absolute()
    _assert_no_reparse_components(root)
    candidate = root / STATE_DIRECTORY
    if not os.path.lexists(candidate):
        return None
    control = safe_path(candidate, root)
    if not control.is_dir():
        raise ValueError(f"{STATE_DIRECTORY} must be a directory")
    return control


def _operational_file(root, filename):
    control = _operational_control(root)
    if control is None:
        return None
    path = safe_path(control / filename, root)
    relative = f"{STATE_DIRECTORY}/{filename}"
    if not path.exists():
        raise _OperationalMemoryIssue(
            "state-conflict", "P0", relative, "required operational file is missing"
        )
    if not path.is_file():
        raise _OperationalMemoryIssue(
            "state-conflict", "P0", relative, "operational path must be a regular file"
        )
    return path


def _read_operational_file(root, filename, capacity):
    path = _operational_file(root, filename)
    if path is None:
        return None
    relative = f"{STATE_DIRECTORY}/{filename}"
    data = _read_bounded_bytes(path, capacity, relative)
    return _decode_operational_bytes(data, relative)


def _read_bounded_bytes(path, capacity, relative):
    try:
        with Path(path).open("rb") as handle:
            data = handle.read(capacity + 1)
    except OSError as exc:
        raise _OperationalMemoryIssue(
            "state-conflict", "P0", relative, "operational file cannot be read"
        ) from exc
    if len(data) > capacity:
        raise _OperationalMemoryIssue(
            "memory-capacity", "P1", relative, "operational file exceeds its capacity"
        )
    return data


def _decode_operational_bytes(data, relative):
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise _OperationalMemoryIssue(
            "state-conflict", "P0", relative, "operational file is not UTF-8"
        ) from exc
    if _MERGE_MARKER.search(text):
        raise _OperationalMemoryIssue(
            "state-conflict", "P0", relative, "operational file contains merge markers"
        )
    return text


def _strict_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise _StrictJSONError("JSON object contains a duplicate key")
        result[key] = value
    return result


def _reject_json_constant(value):
    raise _StrictJSONError(f"non-standard JSON constant {value} is not allowed")


def _strict_json_loads(text, name):
    try:
        value = json.loads(
            text,
            object_pairs_hook=_strict_object,
            parse_constant=_reject_json_constant,
        )
    except (ValueError, RecursionError, OverflowError) as exc:
        raise ValueError(f"{name} is malformed JSON") from exc
    _validate_json_nesting(value, name)
    return value


def _validate_json_nesting(value, name):
    stack = [(True, value, 1)]
    active = set()
    while stack:
        entering, current, depth = stack.pop()
        if not entering:
            active.remove(id(current))
            continue
        if depth > MAX_JSON_DEPTH:
            raise ValueError(f"{name} exceeds maximum JSON nesting")
        if isinstance(current, Mapping):
            identity = id(current)
            if identity in active:
                raise ValueError(f"{name} contains a cyclic object")
            active.add(identity)
            stack.append((False, current, depth))
            stack.extend((True, item, depth + 1) for item in current.values())
        elif isinstance(current, list):
            identity = id(current)
            if identity in active:
                raise ValueError(f"{name} contains a cyclic array")
            active.add(identity)
            stack.append((False, current, depth))
            stack.extend((True, item, depth + 1) for item in current)


def validate_operational_state(state: Mapping, root: Path) -> dict:
    """Validate and normalize committed operational state without writing it."""
    state = _require_mapping(state, "operational state")
    _require_exact_keys(
        state,
        {
            "schema_version",
            "initialized",
            "rubric",
            "cold_paths",
            "verified_documents",
            "protected_intent",
            "last_completed_event",
        },
        "operational state",
    )
    if _require_int(state["schema_version"], "schema_version") != STATE_SCHEMA_VERSION:
        raise ValueError("unsupported operational state schema version")

    initialized = _require_mapping(state["initialized"], "initialized")
    _require_exact_keys(
        initialized,
        {"completed", "skill_version", "map", "hot_paths"},
        "initialized",
    )
    if initialized["completed"] is not True:
        raise ValueError("initialized.completed must be true")
    skill_version = _require_string(initialized["skill_version"], "initialized.skill_version")
    if not _SEMVER.fullmatch(skill_version):
        raise ValueError("initialized.skill_version must be semantic versioning")
    map_path = _normalize_checked_path(initialized["map"], "initialized.map", root)
    hot_paths = [
        _normalize_checked_path(path, "initialized.hot_paths", root)
        for path in _require_sequence(initialized["hot_paths"], "initialized.hot_paths")
    ]

    rubric = _require_mapping(state["rubric"], "rubric")
    _require_exact_keys(
        rubric,
        {"version", "last_verified_score", "last_verified_status"},
        "rubric",
    )
    rubric_version = _require_int(rubric["version"], "rubric.version", minimum=1)
    score = _require_int(
        rubric["last_verified_score"],
        "rubric.last_verified_score",
        minimum=0,
        maximum=100,
    )
    rubric_status = _require_string(
        rubric["last_verified_status"], "rubric.last_verified_status"
    )

    cold_paths = [
        _normalize_checked_pattern(path, "cold_paths", root)
        for path in _require_sequence(state["cold_paths"], "cold_paths")
    ]

    verified_documents = []
    for index, record in enumerate(
        _require_sequence(state["verified_documents"], "verified_documents")
    ):
        name = f"verified_documents[{index}]"
        record = _require_mapping(record, name)
        _require_exact_keys(
            record,
            {"document", "digest", "sources", "verified_event"},
            name,
        )
        sources = []
        for source_index, source in enumerate(
            _require_sequence(record["sources"], f"{name}.sources")
        ):
            source_name = f"{name}.sources[{source_index}]"
            source = _require_mapping(source, source_name)
            _require_exact_keys(source, {"path", "digest"}, source_name)
            sources.append(
                {
                    "path": _normalize_checked_path(source["path"], f"{source_name}.path", root),
                    "digest": _normalize_digest(source["digest"], f"{source_name}.digest"),
                }
            )
        verified_documents.append(
            {
                "document": _normalize_checked_path(record["document"], f"{name}.document", root),
                "digest": _normalize_digest(record["digest"], f"{name}.digest"),
                "sources": sources,
                "verified_event": _normalize_event_id(
                    record["verified_event"], f"{name}.verified_event"
                ),
            }
        )

    protected_records = _require_sequence(state["protected_intent"], "protected_intent")
    if len(protected_records) > MAX_PROTECTED_INTENTS:
        raise ValueError("protected_intent exceeds its route capacity")
    protected_intent = []
    for index, record in enumerate(protected_records):
        name = f"protected_intent[{index}]"
        record = _require_mapping(record, name)
        _require_exact_keys(
            record,
            {"id", "intent_key", "source", "preserve", "status"},
            name,
        )
        intent_id = _require_string(record["id"], f"{name}.id")
        if not re.fullmatch(r"INTENT-[0-9]+", intent_id):
            raise ValueError(f"{name}.id is invalid")
        source_path, separator, anchor = _require_string(
            record["source"], f"{name}.source"
        ).partition("#")
        if not separator or not anchor:
            raise ValueError(f"{name}.source must include a Markdown anchor")
        normalized_source = _normalize_checked_path(source_path, f"{name}.source", root)
        if record["preserve"] is not True:
            raise ValueError(f"{name}.preserve must be true")
        protected_intent.append(
            {
                "id": intent_id,
                "intent_key": _require_string(record["intent_key"], f"{name}.intent_key"),
                "source": f"{normalized_source}#{anchor}",
                "preserve": True,
                "status": _require_string(record["status"], f"{name}.status"),
            }
        )

    return {
        "schema_version": STATE_SCHEMA_VERSION,
        "initialized": {
            "completed": True,
            "skill_version": skill_version,
            "map": map_path,
            "hot_paths": hot_paths,
        },
        "rubric": {
            "version": rubric_version,
            "last_verified_score": score,
            "last_verified_status": rubric_status,
        },
        "cold_paths": cold_paths,
        "verified_documents": verified_documents,
        "protected_intent": protected_intent,
        "last_completed_event": _normalize_event_id(
            state["last_completed_event"], "last_completed_event"
        ),
    }


def load_operational_state(root: Path) -> dict | None:
    text = _read_operational_file(root, STATE_FILE, MAX_STATE_BYTES)
    if text is None:
        return None
    state = _strict_json_loads(text, "operational state")
    return validate_operational_state(state, Path(root).absolute())


def _validate_finding_evidence(evidence, root, name):
    normalized = []
    for index, item in enumerate(_require_sequence(evidence, name)):
        item_name = f"{name}[{index}]"
        item = dict(_require_mapping(item, item_name))
        for key in sorted(_IDENTITY_PATH_FIELDS):
            if key in item:
                item[key] = _normalize_checked_route(item[key], f"{item_name}.{key}", root)
        for key in sorted(_IDENTITY_PATH_LIST_FIELDS):
            if key in item:
                item[key] = [
                    _normalize_checked_route(path, f"{item_name}.{key}", root)
                    for path in _require_sequence(item[key], f"{item_name}.{key}")
                ]
        try:
            json.dumps(item, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{item_name} is not JSON serializable") from exc
        normalized.append(item)
    return normalized


def validate_operational_findings(payload, root: Path) -> dict:
    """Validate and normalize a findings payload without filesystem mutation."""
    payload = _require_mapping(payload, "operational findings")
    _require_exact_keys(payload, {"schema_version", "findings"}, "operational findings")
    if _require_int(payload["schema_version"], "findings schema_version") != STATE_SCHEMA_VERSION:
        raise ValueError("unsupported operational findings schema version")

    normalized = []
    identifiers = {}
    for index, record in enumerate(_require_sequence(payload["findings"], "findings")):
        name = f"findings[{index}]"
        record = _require_mapping(record, name)
        required = {
            "id",
            "fingerprint",
            "priority",
            "status",
            "summary",
            "why",
            "evidence",
            "recommended_action",
        }
        if not required.issubset(record):
            raise ValueError(f"{name} is missing required fields")
        identifier = _require_string(record["id"], f"{name}.id").upper()
        id_match = _FINDING_ID.fullmatch(identifier)
        if not id_match:
            raise ValueError(f"{name}.id is invalid")
        fingerprint = _normalize_fingerprint(record["fingerprint"])
        if not fingerprint.startswith(id_match.group(1).lower()):
            raise ValueError(f"{name}.id does not match its fingerprint")
        previous = identifiers.get(identifier)
        if previous is not None and previous != fingerprint:
            raise ValueError("duplicate finding ID has conflicting fingerprints")
        identifiers[identifier] = fingerprint
        priority = _require_string(record["priority"], f"{name}.priority")
        status = _require_string(record["status"], f"{name}.status")
        if priority not in PRIORITIES:
            raise ValueError(f"{name}.priority is invalid")
        if status not in FINDING_STATUSES:
            raise ValueError(f"{name}.status is invalid")
        normalized_record = dict(record)
        normalized_record.update(
            {
                "id": identifier,
                "fingerprint": fingerprint,
                "priority": priority,
                "status": status,
                "summary": _require_string(record["summary"], f"{name}.summary"),
                "why": _require_string(record["why"], f"{name}.why"),
                "evidence": _validate_finding_evidence(record["evidence"], root, f"{name}.evidence"),
                "recommended_action": _require_string(
                    record["recommended_action"], f"{name}.recommended_action"
                ),
            }
        )
        normalized.append(normalized_record)
    return {"schema_version": STATE_SCHEMA_VERSION, "findings": normalized}


def load_operational_findings(root: Path) -> dict:
    text = _read_operational_file(root, FINDINGS_FILE, MAX_FINDINGS_BYTES)
    if text is None:
        return {"schema_version": STATE_SCHEMA_VERSION, "findings": []}
    payload = _strict_json_loads(text, "operational findings")
    return validate_operational_findings(payload, Path(root).absolute())


def _memory_finding(kind, priority, path, detail, identity=None):
    evidence = {"path": path}
    if identity:
        evidence.update(identity)
    fingerprint = finding_fingerprint(kind, [evidence])
    return {
        "id": finding_id(fingerprint, {}),
        "fingerprint": fingerprint,
        "kind": kind,
        "priority": priority,
        "path": path,
        "detail": detail,
    }


def _sanitized_memory_detail(error, subject):
    """Classify public inspection failures without serializing exception text."""
    if isinstance(error, OSError):
        return f"{subject} is unavailable"
    if isinstance(error, UnicodeError):
        return f"{subject} has invalid text encoding"
    if isinstance(error, (RecursionError, OverflowError)):
        return f"{subject} exceeds safe structural limits"
    return f"{subject} is invalid"


def _canonical_operational_bytes(value):
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


def operational_state_digest(state):
    """Hash state semantics while excluding event closeout pointers."""
    normalized = json.loads(_canonical_operational_bytes(state))
    normalized.pop("last_completed_event", None)
    for record in normalized.get("verified_documents", []):
        record.pop("verified_event", None)
    return "sha256:" + hashlib.sha256(_canonical_operational_bytes(normalized)).hexdigest()


def operational_findings_digest(findings):
    return "sha256:" + hashlib.sha256(_canonical_operational_bytes(findings)).hexdigest()


def _markdown_heading_anchors(text):
    anchors = set()
    counts = {}
    for line in text.splitlines():
        match = re.match(r"^#{1,6}\s+(.+?)\s*#*\s*$", line)
        if not match:
            continue
        base = slug(match.group(1))
        count = counts.get(base, 0)
        counts[base] = count + 1
        anchors.add(base if count == 0 else f"{base}-{count}")
    return anchors


def _inspect_control_plane_files(control):
    findings = []
    observed = {"manifests": set(), "local_map_present": False}
    pending = [(Path(control), STATE_DIRECTORY)]
    inspected_entries = 0
    while pending:
        directory, relative = pending.pop(0)
        try:
            with os.scandir(directory) as iterator:
                entries = []
                for entry in iterator:
                    inspected_entries += 1
                    if inspected_entries > MAX_CONTROL_ENTRIES:
                        findings.append(
                            _memory_finding(
                                "state-conflict",
                                "P0",
                                relative,
                                "operational control entries exceed safe inspection capacity",
                            )
                        )
                        return findings, observed
                    entries.append(entry)
        except OSError as exc:
            findings.append(
                _memory_finding(
                    "state-conflict",
                    "P0",
                    relative,
                    _sanitized_memory_detail(exc, "operational control entries"),
                )
            )
            continue
        for entry in sorted(entries, key=lambda item: (item.name.casefold(), item.name)):
            if entry.name.startswith(".docs-txn-"):
                findings.append(
                    _memory_finding(
                        "state-conflict",
                        "P0",
                        relative,
                        "orphan reserved transaction temporary requires recovery",
                    )
                )
                continue
            try:
                if relative == STATE_DIRECTORY and entry.name == "local-map.json":
                    observed["local_map_present"] = entry.is_file(follow_symlinks=False)
                elif relative == STATE_DIRECTORY and entry.name == "manifests":
                    if entry.is_dir(follow_symlinks=False) and not entry.is_symlink():
                        pending.append(
                            (Path(entry.path), f"{STATE_DIRECTORY}/manifests")
                        )
                elif (
                    relative == f"{STATE_DIRECTORY}/manifests"
                    and entry.name.endswith(".json")
                    and entry.is_file(follow_symlinks=False)
                ):
                    observed["manifests"].add(
                        f"{STATE_DIRECTORY}/manifests/{entry.name}"
                    )
            except OSError as exc:
                findings.append(
                    _memory_finding(
                        "state-conflict",
                        "P0",
                        relative,
                        _sanitized_memory_detail(exc, "operational control entry"),
                    )
                )
    return findings, observed


def _orphan_control_artifact_findings(root, events, observed):
    findings = []
    referenced_manifests = {
        event["manifest"]["path"]
        for event in events
        if isinstance(event.get("manifest"), Mapping)
        and isinstance(event["manifest"].get("path"), str)
    }
    if observed["manifests"] - referenced_manifests:
        findings.append(
            _memory_finding(
                "state-conflict",
                "P0",
                f"{STATE_DIRECTORY}/manifests",
                "unreferenced manifest requires recovery",
            )
        )

    local_references = [
        event["local_map_digest"]
        for event in events
        if isinstance(event.get("local_map_digest"), str)
    ]
    if observed["local_map_present"] and not local_references:
        findings.append(
            _memory_finding(
                "state-conflict",
                "P0",
                _LOCAL_MAP_PATH,
                "unreferenced local map requires recovery",
            )
        )
    elif local_references:
        valid = observed["local_map_present"]
        if valid:
            try:
                data = _read_bounded_bytes(
                    safe_path(Path(root) / _LOCAL_MAP_PATH, root),
                    64 * 1024,
                    _LOCAL_MAP_PATH,
                )
                valid = (
                    "sha256:" + hashlib.sha256(data).hexdigest()
                    == local_references[-1]
                )
            except (OSError, ValueError, _OperationalMemoryIssue):
                valid = False
        if not valid:
            findings.append(
                _memory_finding(
                    "state-conflict",
                    "P0",
                    _LOCAL_MAP_PATH,
                    "local map does not match its verified event reference",
                )
            )
    return findings


def _inspect_protected_intent_sources(root, state):
    findings = []
    remaining = MAX_PROTECTED_INTENT_TOTAL_BYTES
    for record in state["protected_intent"]:
        source, _, anchor = record["source"].partition("#")
        try:
            path = safe_path(Path(root) / source, root)
            capacity = min(MAX_PROTECTED_INTENT_BYTES, remaining)
            if capacity <= 0:
                raise _OperationalMemoryIssue(
                    "memory-capacity",
                    "P1",
                    source,
                    "protected-intent inspection reached its byte capacity",
                )
            data = _read_bounded_bytes(path, capacity, source)
            remaining -= len(data)
            text = _decode_operational_bytes(data, source)
            available = slug(anchor) in _markdown_heading_anchors(text)
        except (OSError, UnicodeError, ValueError, _OperationalMemoryIssue):
            available = False
        if not available:
            findings.append(
                _memory_finding(
                    "protected-intent-missing",
                    "P0",
                    record["source"],
                    "maintained protected-intent anchor is unavailable",
                    {"intent_key": record["intent_key"]},
                )
            )
    return findings


def _transaction_integrity_findings(root, state, findings_payload, events):
    if not events:
        return []
    latest = events[-1]
    transaction_fields = {
        "transaction_id",
        "starting_digests",
        "state_semantic_digest",
        "findings_digest",
        "transaction_targets",
    }
    if not transaction_fields.intersection(latest):
        return []
    valid = transaction_fields.issubset(latest)
    try:
        transaction_id = latest["transaction_id"]
        starting = latest["starting_digests"]
        targets = latest["transaction_targets"]
        if not isinstance(transaction_id, str) or not _TRANSACTION_ID.fullmatch(transaction_id):
            valid = False
        if not isinstance(starting, Mapping) or not isinstance(targets, list):
            valid = False
        else:
            normalized_targets = set(targets)
            if len(normalized_targets) != len(targets) or normalized_targets != set(starting):
                valid = False
            for digest in starting.values():
                if not isinstance(digest, str) or not _TRANSACTION_DIGEST.fullmatch(digest):
                    valid = False
        fixed = {
            f"{STATE_DIRECTORY}/{STATE_FILE}",
            f"{STATE_DIRECTORY}/{FINDINGS_FILE}",
            f"{STATE_DIRECTORY}/{EVENTS_FILE}",
        }
        if not fixed.issubset(set(targets)):
            valid = False
        if latest["state_semantic_digest"] != operational_state_digest(state):
            valid = False
        if latest["findings_digest"] != operational_findings_digest(findings_payload):
            valid = False
        if "local_map_digest" in latest:
            local_path = safe_path(Path(root) / _LOCAL_MAP_PATH, root)
            local_bytes = _read_bounded_bytes(
                local_path,
                64 * 1024,
                _LOCAL_MAP_PATH,
            )
            actual_local_digest = "sha256:" + hashlib.sha256(local_bytes).hexdigest()
            if latest["local_map_digest"] != actual_local_digest:
                valid = False
        if state["last_completed_event"] != latest["event_id"]:
            valid = False
        if any(
            record["verified_event"] != latest["event_id"]
            for record in state["verified_documents"]
        ):
            valid = False
    except (KeyError, TypeError, ValueError, RecursionError, OverflowError):
        valid = False
    if valid:
        return []
    return [
        _memory_finding(
            "state-conflict",
            "P0",
            STATE_DIRECTORY,
            "event transaction binding does not match the installed control set",
            {"event_id": latest.get("event_id", "EVT-UNKNOWN")},
        )
    ]


def _normalize_manifest(manifest, root, name):
    manifest = _require_mapping(manifest, name)
    if "path" not in manifest or "digest" not in manifest:
        raise ValueError(f"{name} must include path and digest")
    manifest_prefix = f"{STATE_DIRECTORY}/manifests/"
    try:
        normalized_path = _normalize_checked_path(manifest["path"], f"{name}.path", root)
        if not normalized_path.startswith(manifest_prefix):
            raise ValueError(f"{name}.path must remain under {manifest_prefix}")
        path = safe_path(Path(root) / normalized_path, root)
    except (OSError, ValueError) as exc:
        raise _OperationalMemoryIssue(
            "state-conflict", "P0", f"{STATE_DIRECTORY}/manifests", "manifest path is unsafe"
        ) from exc
    digest = _require_string(manifest["digest"], f"{name}.digest").lower()
    if not _MANIFEST_DIGEST.fullmatch(digest):
        raise ValueError(f"{name}.digest must be a SHA-256 digest")
    if not path.exists() or not path.is_file():
        raise _OperationalMemoryIssue(
            "state-conflict", "P0", normalized_path, "referenced manifest is missing"
        )
    data = _read_bounded_bytes(path, MAX_MANIFEST_BYTES, normalized_path)
    text = _decode_operational_bytes(data, normalized_path)
    if "sha256:" + hashlib.sha256(data).hexdigest() != digest:
        raise _OperationalMemoryIssue(
            "state-conflict", "P0", normalized_path, "manifest digest does not match content"
        )
    try:
        content = _strict_json_loads(text, "manifest")
    except ValueError as exc:
        raise _OperationalMemoryIssue(
            "state-conflict", "P0", normalized_path, "manifest is malformed JSON"
        ) from exc
    if not isinstance(content, (Mapping, list)):
        raise _OperationalMemoryIssue(
            "state-conflict", "P0", normalized_path, "manifest must contain a JSON object or array"
        )
    normalized = dict(manifest)
    normalized.update({"path": normalized_path, "digest": digest})
    return normalized


def validate_operational_events(events: Sequence[Mapping]) -> list[dict]:
    findings = []
    try:
        events = _require_sequence(events, "operational events")
    except ValueError as exc:
        return [
            _memory_finding(
                "state-conflict",
                "P0",
                f"{STATE_DIRECTORY}/{EVENTS_FILE}",
                _sanitized_memory_detail(exc, "operational events"),
            )
        ]
    identifiers = {}
    for index, event in enumerate(events):
        if not isinstance(event, Mapping):
            findings.append(
                _memory_finding(
                    "state-conflict",
                    "P0",
                    f"{STATE_DIRECTORY}/{EVENTS_FILE}",
                    f"event {index + 1} is not an object",
                )
            )
            continue
        try:
            event_id = _normalize_event_id(event.get("event_id"), f"events[{index}].event_id")
            _require_string(event.get("kind"), f"events[{index}].kind")
            _validate_json_nesting(event, f"events[{index}]")
            canonical = event_fingerprint(event)
            if not canonical.startswith(event_id.removeprefix("EVT-").lower()):
                findings.append(
                    _memory_finding(
                        "state-conflict",
                        "P0",
                        f"{STATE_DIRECTORY}/{EVENTS_FILE}",
                        "event_id does not match semantic content",
                    )
                )
                continue
        except (TypeError, ValueError, RecursionError, OverflowError) as exc:
            findings.append(
                _memory_finding(
                    "state-conflict",
                    "P0",
                    f"{STATE_DIRECTORY}/{EVENTS_FILE}",
                    _sanitized_memory_detail(exc, "operational event"),
                )
            )
            continue
        previous = identifiers.get(event_id)
        if previous is not None and previous != canonical:
            findings.append(
                _memory_finding(
                    "state-conflict",
                    "P0",
                    f"{STATE_DIRECTORY}/{EVENTS_FILE}",
                    "duplicate event ID has conflicting payloads",
                    {"event_id": event_id},
                )
            )
        else:
            identifiers[event_id] = canonical
    return findings


def load_operational_events(root: Path) -> list[dict]:
    text = _read_operational_file(root, EVENTS_FILE, MAX_EVENTS_BYTES)
    if text is None:
        return []
    events = []
    for line_number, line in enumerate(text.splitlines(), 1):
        if not line.strip():
            continue
        event = _strict_json_loads(line, f"operational event line {line_number}")
        if not isinstance(event, Mapping):
            raise ValueError(f"operational event line {line_number} must be an object")
        normalized = dict(event)
        normalized["event_id"] = _normalize_event_id(
            event.get("event_id"), f"events[{line_number - 1}].event_id"
        )
        _require_string(event.get("kind"), f"events[{line_number - 1}].kind")
        if "changed_paths" in event:
            normalized["changed_paths"] = [
                _normalize_checked_path(path, "event changed_paths", root)
                for path in _require_sequence(event["changed_paths"], "event changed_paths")
            ]
        if "manifest" in event:
            normalized["manifest"] = _normalize_manifest(
                event["manifest"], root, "event manifest"
            )
        events.append(normalized)
    return events


def inspect_operational_memory(root):
    """Inspect committed operational memory without modifying it."""
    try:
        control = _operational_control(root)
    except (OSError, ValueError) as exc:
        return [
            _memory_finding(
                "state-conflict",
                "P0",
                STATE_DIRECTORY,
                _sanitized_memory_detail(exc, "operational control"),
            )
        ]
    if control is None:
        return []

    findings, observed_control = _inspect_control_plane_files(control)
    state = None
    findings_payload = None
    events = None
    for filename, loader in (
        (STATE_FILE, load_operational_state),
        (FINDINGS_FILE, load_operational_findings),
        (EVENTS_FILE, load_operational_events),
    ):
        try:
            loaded = loader(root)
            if filename == STATE_FILE:
                state = loaded
            elif filename == FINDINGS_FILE:
                findings_payload = loaded
            elif filename == EVENTS_FILE:
                events = loaded
        except _OperationalMemoryIssue as exc:
            findings.append(_memory_finding(exc.kind, exc.priority, exc.path, exc.detail))
        except (OSError, UnicodeError, ValueError) as exc:
            findings.append(
                _memory_finding(
                    "state-conflict",
                    "P0",
                    f"{STATE_DIRECTORY}/{filename}",
                    _sanitized_memory_detail(exc, "operational file"),
                )
            )

    if events is not None:
        findings.extend(validate_operational_events(events))
        findings.extend(
            _orphan_control_artifact_findings(root, events, observed_control)
        )
    if state is not None:
        findings.extend(_inspect_protected_intent_sources(root, state))
    if state is not None and events is not None:
        event_ids = {event["event_id"] for event in events}
        referenced_ids = {state["last_completed_event"]}
        referenced_ids.update(
            record["verified_event"] for record in state["verified_documents"]
        )
        for event_id in sorted(referenced_ids - event_ids):
            findings.append(
                _memory_finding(
                    "state-conflict",
                    "P0",
                    f"{STATE_DIRECTORY}/{STATE_FILE}",
                    "state references an event absent from operational history",
                    {"event_id": event_id},
                )
            )
    if state is not None and findings_payload is not None and events is not None:
        findings.extend(
            _transaction_integrity_findings(root, state, findings_payload, events)
        )
    return findings


_operational_memory_findings = inspect_operational_memory


__all__ = (
    "EVENTS_FILE",
    "FINDINGS_FILE",
    "FINDING_STATUSES",
    "MAX_EVENTS_BYTES",
    "MAX_FINDINGS_BYTES",
    "MAX_JSON_DEPTH",
    "MAX_MANIFEST_BYTES",
    "MAX_STATE_BYTES",
    "PRIORITIES",
    "STATE_DIRECTORY",
    "STATE_FILE",
    "STATE_SCHEMA_VERSION",
    "inspect_operational_memory",
    "load_operational_events",
    "load_operational_findings",
    "load_operational_state",
    "operational_findings_digest",
    "operational_state_digest",
    "validate_operational_events",
    "validate_operational_findings",
    "validate_operational_state",
)
