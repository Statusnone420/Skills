"""Strict, bounded, read-only operational-memory inspection."""

import hashlib
import json
import os
import re
import unicodedata
from collections.abc import Mapping, Sequence
from pathlib import Path

from .formats import is_document_path
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
from .paths import (
    _assert_no_reparse_components,
    normalize_repo_relative,
    safe_path,
    shared_text_exposes_route,
)


STATE_SCHEMA_VERSION = 3
FINDINGS_SCHEMA_VERSION = 1
STATE_DIRECTORY = ".diataxis"
STATE_FILE = "state.json"
FINDINGS_FILE = "findings.json"
EVENTS_FILE = "events.jsonl"
MAX_STATE_BYTES = 32 * 1024
MAX_FINDINGS_BYTES = 256 * 1024
MAX_EVENTS_BYTES = 256 * 1024
MAX_MANIFEST_BYTES = 1024 * 1024
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
_MANIFEST_IDENTITY = re.compile(r"^[0-9a-f]{64}$")
_INIT_MANIFEST_SCHEMA_VERSION = 3
_LOCAL_MAP_PATH = ".diataxis/local-map.json"
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


def _require_shared_text_safe(value, name):
    """Reject private or unsafe route text from shared persisted free-form data."""
    pending = [(value, name)]
    while pending:
        current, current_name = pending.pop()
        if isinstance(current, str):
            if shared_text_exposes_route(current):
                raise ValueError(f"{current_name} exposes a private or unsafe route")
        elif isinstance(current, Mapping):
            for key, item in current.items():
                if isinstance(key, str) and shared_text_exposes_route(key):
                    raise ValueError(
                        f"{current_name} key exposes a private or unsafe route"
                    )
                pending.append((item, f"{current_name}.{key}"))
        elif isinstance(current, list):
            pending.extend(
                (item, f"{current_name}[{index}]")
                for index, item in enumerate(current)
            )


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


def _normalize_operational_state_common(state: Mapping, root: Path) -> dict:
    """Validate and normalize the state fields shared by the v3 contract."""
    state = _require_mapping(state, "operational state")

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
        intent_key = _require_string(record["intent_key"], f"{name}.intent_key")
        source = f"{normalized_source}#{anchor}"
        status = _require_string(record["status"], f"{name}.status")
        _require_shared_text_safe(intent_key, f"{name}.intent_key")
        _require_shared_text_safe(source, f"{name}.source")
        _require_shared_text_safe(status, f"{name}.status")
        protected_intent.append(
            {
                "id": intent_id,
                "intent_key": intent_key,
                "source": source,
                "preserve": True,
                "status": status,
            }
        )

    return {
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


def _require_plain_json(value, name):
    pending = [(value, name)]
    while pending:
        current, current_name = pending.pop()
        if type(current) is dict:
            for key, item in current.items():
                if type(key) is not str:
                    raise ValueError(f"{current_name} keys must be strings")
                pending.append((item, f"{current_name}.{key}"))
        elif type(current) is list:
            pending.extend(
                (item, f"{current_name}[{index}]")
                for index, item in enumerate(current)
            )
        elif current is None or type(current) in {str, int, bool}:
            continue
        else:
            raise ValueError(f"{current_name} must use exact JSON value types")


def _normalize_shared_path(value, name, root):
    normalized = _normalize_checked_path(value, name, root)
    if normalized.split("/", 1)[0].casefold() == ".local":
        raise ValueError(f"{name} must not expose local-only routes")
    return normalized


def _normalize_shared_route(value, name, root):
    normalized = _normalize_checked_route(value, name, root)
    path = normalized.partition("#")[0]
    if path.split("/", 1)[0].casefold() == ".local":
        raise ValueError(f"{name} must not expose local-only routes")
    return normalized


def _route_is_within_scope(route, scope):
    route_key = "/".join(os.path.normcase(route).split(os.sep))
    scope_key = "/".join(os.path.normcase(scope).split(os.sep))
    return scope_key == "." or route_key == scope_key or route_key.startswith(scope_key + "/")


def _normalize_scope(value, root):
    value = _require_mapping(value, "scope")
    _require_exact_keys(value, {"selected", "inspected"}, "scope")
    selected = _normalize_shared_path(value["selected"], "scope.selected", root)
    inspected = _normalize_shared_path(value["inspected"], "scope.inspected", root)
    if os.path.normcase(selected) != os.path.normcase(inspected):
        raise ValueError("scope.inspected must equal the complete selected scope")
    return {"selected": selected, "inspected": inspected}


def _normalize_structural_scores(value):
    value = _require_mapping(value, "structural_scores")
    _require_exact_keys(value, {"before", "after"}, "structural_scores")
    return {
        "before": _require_int(
            value["before"], "structural_scores.before", minimum=0, maximum=100
        ),
        "after": _require_int(
            value["after"], "structural_scores.after", minimum=0, maximum=100
        ),
    }


def _normalize_byte_observation(value, name, root):
    value = _require_mapping(value, name)
    _require_exact_keys(value, {"value", "unit", "provenance"}, name)
    measured = _require_int(value["value"], f"{name}.value", minimum=0)
    if type(value["unit"]) is not str or value["unit"] != "bytes":
        raise ValueError(f"{name}.unit must be bytes")
    provenance = []
    identities = set()
    for index, raw in enumerate(_require_sequence(value["provenance"], f"{name}.provenance")):
        item_name = f"{name}.provenance[{index}]"
        raw = _require_mapping(raw, item_name)
        _require_exact_keys(raw, {"route", "bytes", "source"}, item_name)
        route = _normalize_shared_path(raw["route"], f"{item_name}.route", root)
        identity = os.path.normcase(route)
        if identity in identities:
            raise ValueError(f"{name}.provenance routes must be unique")
        identities.add(identity)
        byte_count = _require_int(raw["bytes"], f"{item_name}.bytes", minimum=0)
        if type(raw["source"]) is not str or raw["source"] != "filesystem-stat":
            raise ValueError(f"{item_name}.source must be filesystem-stat")
        provenance.append(
            {"route": route, "bytes": byte_count, "source": "filesystem-stat"}
        )
    if sum(item["bytes"] for item in provenance) != measured:
        raise ValueError(f"{name}.value must equal its route byte provenance")
    provenance.sort(key=lambda item: (item["route"].casefold(), item["route"]))
    return {"value": measured, "unit": "bytes", "provenance": provenance}


def _normalize_hot_path_bytes(value, root):
    value = _require_mapping(value, "hot_path_bytes")
    _require_exact_keys(value, {"before", "after"}, "hot_path_bytes")
    return {
        "before": _normalize_byte_observation(value["before"], "hot_path_bytes.before", root),
        "after": _normalize_byte_observation(value["after"], "hot_path_bytes.after", root),
    }


def _normalize_trust_coverage(value, root):
    value = _require_mapping(value, "trust_coverage")
    _require_exact_keys(
        value,
        {"status", "numerator", "denominator", "routes"},
        "trust_coverage",
    )
    routes = []
    identities = set()
    for index, raw in enumerate(_require_sequence(value["routes"], "trust_coverage.routes")):
        name = f"trust_coverage.routes[{index}]"
        raw = _require_mapping(raw, name)
        _require_exact_keys(raw, {"route", "verified", "freshness", "sources"}, name)
        route = _normalize_shared_path(raw["route"], f"{name}.route", root)
        identity = os.path.normcase(route)
        if identity in identities:
            raise ValueError("trust_coverage routes must be unique")
        identities.add(identity)
        if type(raw["verified"]) is not bool:
            raise ValueError(f"{name}.verified must be a boolean")
        freshness = raw["freshness"]
        if type(freshness) is not str or freshness not in {"fresh", "stale", "unverified"}:
            raise ValueError(f"{name}.freshness is invalid")
        if raw["verified"] is not (freshness == "fresh"):
            raise ValueError(f"{name}.verified must match freshness")
        sources = list(_require_sequence(raw["sources"], f"{name}.sources"))
        if (
            not sources
            or any(type(source) is not str or source not in _TRUST_SOURCES for source in sources)
            or len(sources) != len(set(sources))
        ):
            raise ValueError(f"{name}.sources are invalid")
        routes.append(
            {
                "route": route,
                "verified": raw["verified"],
                "freshness": freshness,
                "sources": sorted(sources),
            }
        )
    routes.sort(key=lambda item: (item["route"].casefold(), item["route"]))
    numerator = _require_int(value["numerator"], "trust_coverage.numerator", minimum=0)
    denominator = _require_int(
        value["denominator"], "trust_coverage.denominator", minimum=0
    )
    if denominator != len(routes) or numerator != sum(item["verified"] for item in routes):
        raise ValueError("trust_coverage counts do not match routes")
    expected_status = (
        "unverified"
        if denominator == 0
        else "verified"
        if numerator == denominator
        else "partial"
    )
    if type(value["status"]) is not str or value["status"] != expected_status:
        raise ValueError("trust_coverage.status does not match its counts")
    return {
        "status": expected_status,
        "numerator": numerator,
        "denominator": denominator,
        "routes": routes,
    }


def _normalize_manifest_identity(value):
    if type(value) is not str or not _MANIFEST_IDENTITY.fullmatch(value):
        raise ValueError("manifest_identity must be lowercase 64-hex")
    return value


def _validate_route_bindings(state):
    def identity(route):
        return os.path.normcase(os.path.normpath(route))

    current_routes = {
        identity(state["initialized"]["map"]),
        *(identity(route) for route in state["initialized"]["hot_paths"]),
    }
    after_routes = {
        identity(item["route"])
        for item in state["hot_path_bytes"]["after"]["provenance"]
    }
    if after_routes != current_routes:
        raise ValueError(
            "hot_path_bytes.after provenance must equal map and current routes"
        )

    required_sources = {}

    def declare(route, source):
        required_sources.setdefault(identity(route), set()).add(source)

    for route in state["initialized"]["hot_paths"]:
        declare(route, "state:initialized-hot-path")
    for record in state["verified_documents"]:
        declare(record["document"], "state:verified-document")
        for source in record["sources"]:
            declare(source["path"], "state:verified-source")

    trust_routes = {
        identity(item["route"]): item for item in state["trust_coverage"]["routes"]
    }
    if not set(required_sources).issubset(trust_routes):
        raise ValueError("trust_coverage omits state-derived routes")
    for route_identity, item in trust_routes.items():
        claimed_state_sources = {
            source for source in item["sources"] if source.startswith("state:")
        }
        expected_state_sources = required_sources.get(route_identity, set())
        if (
            not expected_state_sources.issubset(item["sources"])
            or not claimed_state_sources.issubset(expected_state_sources)
        ):
            raise ValueError("trust_coverage state provenance does not match its route")


def _normalize_shared_contract_path(value, name, root):
    if root is None:
        normalized = normalize_repo_relative(value, name)
        if normalized.split("/", 1)[0].casefold() == ".local":
            raise ValueError(f"{name} must not expose local-only routes")
        return normalized
    return _normalize_shared_path(value, name, root)


def normalize_corpus_v3(value, root=None, name="result corpus"):
    value = _require_mapping(value, name)
    _require_exact_keys(
        value,
        {
            "coverage_version",
            "coverage_mode",
            "ordering_version",
            "selected_scope",
            "write_boundary",
            "path_count",
            "paths_digest",
        },
        name,
    )
    if value["coverage_version"] != "init-corpus-v1":
        raise ValueError(f"{name}.coverage_version is invalid")
    if value["ordering_version"] != "repo-relative-casefold-v1":
        raise ValueError(f"{name}.ordering_version is invalid")
    mode = _require_string(value["coverage_mode"], f"{name}.coverage_mode")
    if mode not in {"selected-scope-exact", "empty-adoption"}:
        raise ValueError(f"{name}.coverage_mode is invalid")
    selected = _normalize_shared_contract_path(
        value["selected_scope"], f"{name}.selected_scope", root
    )
    boundary = _normalize_shared_contract_path(
        value["write_boundary"], f"{name}.write_boundary", root
    )
    expected_boundary = "." if mode == "empty-adoption" else selected
    if boundary != expected_boundary or (mode == "empty-adoption" and selected != "."):
        raise ValueError(f"{name}.write_boundary is invalid")
    path_count = _require_int(
        value["path_count"], f"{name}.path_count", minimum=0, maximum=256
    )
    digest = _require_string(value["paths_digest"], f"{name}.paths_digest").lower()
    if not _MANIFEST_DIGEST.fullmatch(digest):
        raise ValueError(f"{name}.paths_digest is invalid")
    return {
        "coverage_version": "init-corpus-v1",
        "coverage_mode": mode,
        "ordering_version": "repo-relative-casefold-v1",
        "selected_scope": selected,
        "write_boundary": boundary,
        "path_count": path_count,
        "paths_digest": digest,
    }


def normalize_document_results_v3(value, root=None):
    results = []
    identities = set()
    for index, raw in enumerate(_require_sequence(value, "document results")):
        name = f"document results[{index}]"
        raw = _require_mapping(raw, name)
        _require_exact_keys(
            raw,
            {
                "path",
                "operation",
                "role",
                "starting_digest",
                "result_digest",
                "bytes",
                "source_item_ids",
            },
            name,
        )
        path = _normalize_shared_contract_path(raw["path"], f"{name}.path", root)
        identity = os.path.normcase(path)
        if identity in identities:
            raise ValueError("document result paths must be unique")
        identities.add(identity)
        operation = _require_string(raw["operation"], f"{name}.operation")
        role = _require_string(raw["role"], f"{name}.role")
        if operation not in {"CREATE", "REPLACE", "DELETE"}:
            raise ValueError(f"{name}.operation is invalid")
        if role not in {"document-result", "recovery-archive", "document-source"}:
            raise ValueError(f"{name}.role is invalid")
        starting = _require_string(
            raw["starting_digest"], f"{name}.starting_digest"
        )
        result = _require_string(raw["result_digest"], f"{name}.result_digest")
        starting = starting if starting == "sha256:ABSENT" else starting.lower()
        result = result if result == "sha256:ABSENT" else result.lower()
        if not _TRANSACTION_DIGEST.fullmatch(starting) or not _TRANSACTION_DIGEST.fullmatch(result):
            raise ValueError(f"{name} digests are invalid")
        byte_count = _require_int(raw["bytes"], f"{name}.bytes", minimum=0)
        source_item_ids = [
            _require_string(item, f"{name}.source_item_ids")
            for item in _require_sequence(
                raw["source_item_ids"], f"{name}.source_item_ids"
            )
        ]
        if (
            len(source_item_ids) > 16
            or len(source_item_ids) != len(set(source_item_ids))
            or source_item_ids != sorted(source_item_ids)
        ):
            raise ValueError(f"{name}.source_item_ids are invalid")
        if operation == "CREATE" and (
            starting != "sha256:ABSENT" or result == "sha256:ABSENT"
        ):
            raise ValueError(f"{name} CREATE digests are invalid")
        if operation == "REPLACE" and "sha256:ABSENT" in {starting, result}:
            raise ValueError(f"{name} REPLACE digests are invalid")
        if operation == "DELETE" and (
            starting == "sha256:ABSENT"
            or result != "sha256:ABSENT"
            or byte_count != 0
        ):
            raise ValueError(f"{name} DELETE result is invalid")
        results.append(
            {
                "path": path,
                "operation": operation,
                "role": role,
                "starting_digest": starting,
                "result_digest": result,
                "bytes": byte_count,
                "source_item_ids": source_item_ids,
            }
        )
    ordered = sorted(results, key=lambda item: (item["path"].casefold(), item["path"]))
    if results != ordered:
        raise ValueError("document results must be path-ordered")
    return results


def normalize_dispositions_v3(value, root=None):
    normalized = []
    identities = set()
    records = _require_sequence(value, "dispositions")
    if len(records) > 256:
        raise ValueError("dispositions exceed capacity")
    common = {
        "item_id",
        "path",
        "section",
        "disposition",
        "reason",
        "source_digest",
    }
    for index, raw in enumerate(records):
        name = f"dispositions[{index}]"
        raw = _require_mapping(raw, name)
        section = _require_mapping(raw.get("section"), f"{name}.section")
        whole_file = dict(section) == {"kind": "whole-file"}
        variants = (
            {
                "RETAIN": set(),
                "MIGRATED": {"target", "recovery"},
                "DEDUPLICATED": {"target", "target_digest", "recovery"},
                "ARCHIVED": {"target", "recovery"},
                "DISCARDED": {"recovery"},
            }
            if whole_file
            else {
                "MIGRATED": {"target", "recovery"},
                "DEDUPLICATED": {"target", "target_digest", "recovery"},
                "ARCHIVED": {"target", "recovery"},
                "DISCARDED": {"recovery"},
            }
        )
        outcome = _require_string(raw.get("disposition"), f"{name}.disposition")
        if outcome not in variants:
            raise ValueError(f"{name}.disposition is invalid")
        _require_exact_keys(raw, common | variants[outcome], name)
        path = _normalize_shared_contract_path(raw["path"], f"{name}.path", root)
        if not is_document_path(path):
            raise ValueError(f"{name}.path must be Markdown")
        item_id = _require_string(raw["item_id"], f"{name}.item_id")
        if whole_file:
            if item_id != f"{path}#<whole-file>":
                raise ValueError(f"{name}.item_id is invalid")
            persisted_section = {"kind": "whole-file"}
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
            _require_exact_keys(section, section_fields, f"{name}.section")
            heading_path = section["heading_path"]
            if (
                section["kind"] != "atx-section-v1"
                or type(section["level"]) is not int
                or not 1 <= section["level"] <= 6
                or not isinstance(heading_path, list)
                or not heading_path
                or len(heading_path) > section["level"]
                or type(section["occurrence"]) is not int
                or section["occurrence"] < 1
                or type(section["start_byte"]) is not int
                or section["start_byte"] < 0
                or type(section["end_byte"]) is not int
                or section["end_byte"] <= section["start_byte"]
                or not isinstance(section["raw_span_digest"], str)
                or not _MANIFEST_DIGEST.fullmatch(section["raw_span_digest"])
            ):
                raise ValueError(f"{name}.section is invalid")
            for heading in heading_path:
                if not isinstance(heading, str):
                    raise ValueError(f"{name}.section heading is invalid")
                normalized_heading = " ".join(
                    unicodedata.normalize("NFC", heading).split()
                ).casefold()
                if not normalized_heading or normalized_heading != heading:
                    raise ValueError(f"{name}.section heading is invalid")
            persisted_section = dict(section)
            expected_id = "SEC-" + hashlib.sha256(
                _canonical_operational_bytes(
                    {"path": path, "section": persisted_section}
                )
            ).hexdigest()[:24].upper()
            if item_id != expected_id:
                raise ValueError(f"{name}.item_id is invalid")
        if item_id in identities:
            raise ValueError(f"{name}.item_id is invalid")
        identities.add(item_id)
        reason = _require_string(raw["reason"], f"{name}.reason")
        if len(reason.encode("utf-8")) > 512:
            raise ValueError(f"{name}.reason exceeds capacity")
        _require_shared_text_safe(reason, f"{name}.reason")
        source_digest = _require_string(
            raw["source_digest"], f"{name}.source_digest"
        ).lower()
        if not _MANIFEST_DIGEST.fullmatch(source_digest):
            raise ValueError(f"{name}.source_digest is invalid")
        item = {
            "item_id": item_id,
            "path": path,
            "section": persisted_section,
            "disposition": outcome,
            "reason": reason,
            "source_digest": source_digest,
        }
        if "target" in raw:
            target = _normalize_shared_contract_path(
                raw["target"], f"{name}.target", root
            )
            if not is_document_path(target):
                raise ValueError(f"{name}.target must be Markdown")
            item["target"] = target
        if "target_digest" in raw:
            target_digest = _require_string(
                raw["target_digest"], f"{name}.target_digest"
            ).lower()
            if not _MANIFEST_DIGEST.fullmatch(target_digest):
                raise ValueError(f"{name}.target_digest is invalid")
            item["target_digest"] = target_digest
        if "recovery" in raw:
            recovery = _require_mapping(raw["recovery"], f"{name}.recovery")
            kind = _require_string(recovery.get("kind"), f"{name}.recovery.kind")
            if kind == "git":
                _require_exact_keys(
                    recovery, {"kind", "commit", "blob", "digest"}, f"{name}.recovery"
                )
                object_id = re.compile(r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$")
                if not object_id.fullmatch(recovery["commit"]) or not object_id.fullmatch(
                    recovery["blob"]
                ):
                    raise ValueError(f"{name}.recovery Git identity is invalid")
                persisted_recovery = dict(recovery)
            elif kind == "archive":
                _require_exact_keys(
                    recovery, {"kind", "mode", "path", "digest"}, f"{name}.recovery"
                )
                if recovery["mode"] not in {"existing", "planned"}:
                    raise ValueError(f"{name}.recovery.mode is invalid")
                persisted_recovery = {
                    **dict(recovery),
                    "path": _normalize_shared_contract_path(
                        recovery["path"], f"{name}.recovery.path", root
                    ),
                }
            elif kind == "accepted-hard-delete":
                if not whole_file:
                    raise ValueError(f"{name}.recovery.kind is invalid")
                _require_exact_keys(
                    recovery,
                    {"kind", "discard_set_id", "acceptance_digest"},
                    f"{name}.recovery",
                )
                if not re.fullmatch(r"DISCARD-[0-9A-F]{16}", recovery["discard_set_id"]):
                    raise ValueError(f"{name}.recovery.discard_set_id is invalid")
                persisted_recovery = dict(recovery)
            else:
                raise ValueError(f"{name}.recovery.kind is invalid")
            digest = persisted_recovery.get("digest") or persisted_recovery.get(
                "acceptance_digest"
            )
            if not isinstance(digest, str) or not _MANIFEST_DIGEST.fullmatch(digest):
                raise ValueError(f"{name}.recovery digest is invalid")
            item["recovery"] = persisted_recovery
        normalized.append(item)
    ordered = sorted(normalized, key=lambda item: item["item_id"])
    if normalized != ordered:
        raise ValueError("dispositions must be item-ordered")
    return normalized


def _validate_operational_state_v3(state: Mapping, root: Path) -> dict:
    _require_plain_json(state, "operational state")
    common_keys = {
        "schema_version",
        "initialized",
        "rubric",
        "cold_paths",
        "verified_documents",
        "protected_intent",
        "last_completed_event",
    }
    v3_keys = {
        "scope",
        "structural_scores",
        "hot_path_bytes",
        "trust_coverage",
        "initialization",
    }
    _require_exact_keys(state, common_keys | v3_keys, "operational state")
    if _require_int(state["schema_version"], "schema_version") != STATE_SCHEMA_VERSION:
        raise ValueError("unsupported operational state schema version")
    normalized = _normalize_operational_state_common(state, root)
    normalized["schema_version"] = STATE_SCHEMA_VERSION

    scope = _normalize_scope(state["scope"], root)
    initialized = normalized["initialized"]
    initialized["map"] = _normalize_shared_path(
        initialized["map"], "initialized.map", root
    )
    if not _route_is_within_scope(initialized["map"], scope["inspected"]):
        raise ValueError("initialized.map must remain within inspected scope")
    hot_paths = []
    hot_identities = set()
    for index, route in enumerate(initialized["hot_paths"]):
        route = _normalize_shared_path(route, f"initialized.hot_paths[{index}]", root)
        identity = os.path.normcase(route)
        if identity in hot_identities:
            raise ValueError("initialized.hot_paths must be unique")
        if not _route_is_within_scope(route, scope["inspected"]):
            raise ValueError("initialized.hot_paths must remain within inspected scope")
        hot_identities.add(identity)
        hot_paths.append(route)
    initialized["hot_paths"] = sorted(
        hot_paths, key=lambda route: (route.casefold(), route)
    )

    normalized["cold_paths"] = sorted(normalized["cold_paths"])
    for pattern in normalized["cold_paths"]:
        prefix = pattern.split("*", 1)[0].rstrip("/")
        if prefix:
            _normalize_shared_path(prefix, "cold_paths", root)
    document_identities = set()
    for record in normalized["verified_documents"]:
        record["document"] = _normalize_shared_path(
            record["document"], "verified_documents.document", root
        )
        document_identity = os.path.normcase(record["document"])
        if document_identity in document_identities:
            raise ValueError("verified_documents routes must be unique")
        if not _route_is_within_scope(record["document"], scope["inspected"]):
            raise ValueError("verified documents must remain within inspected scope")
        document_identities.add(document_identity)
        record["sources"].sort(key=lambda source: (source["path"].casefold(), source["path"]))
        source_identities = set()
        for source in record["sources"]:
            source["path"] = _normalize_shared_path(
                source["path"], "verified_documents.sources.path", root
            )
            source_identity = os.path.normcase(source["path"])
            if source_identity in source_identities:
                raise ValueError("verified document source routes must be unique")
            source_identities.add(source_identity)
        record["sources"].sort(
            key=lambda source: (source["path"].casefold(), source["path"])
        )
    normalized["verified_documents"].sort(
        key=lambda record: (record["document"].casefold(), record["document"])
    )
    protected_ids = set()
    for record in normalized["protected_intent"]:
        if record["id"] in protected_ids:
            raise ValueError("protected_intent IDs must be unique")
        protected_ids.add(record["id"])
        record["source"] = _normalize_shared_route(
            record["source"], "protected_intent.source", root
        )
    normalized["protected_intent"].sort(key=lambda record: record["id"])

    normalized.update(
        {
            "scope": scope,
            "structural_scores": _normalize_structural_scores(
                state["structural_scores"]
            ),
            "hot_path_bytes": _normalize_hot_path_bytes(
                state["hot_path_bytes"], root
            ),
            "trust_coverage": _normalize_trust_coverage(
                state["trust_coverage"], root
            ),
        }
    )
    initialization = _require_mapping(state["initialization"], "initialization")
    _require_exact_keys(
        initialization,
        {"manifest_identity", "result_corpus", "document_results_digest"},
        "initialization",
    )
    document_results_digest = _require_string(
        initialization["document_results_digest"],
        "initialization.document_results_digest",
    ).lower()
    if not _MANIFEST_DIGEST.fullmatch(document_results_digest):
        raise ValueError("initialization.document_results_digest is invalid")
    normalized["initialization"] = {
        "manifest_identity": _normalize_manifest_identity(
            initialization["manifest_identity"]
        ),
        "result_corpus": normalize_corpus_v3(
            initialization["result_corpus"], root
        ),
        "document_results_digest": document_results_digest,
    }
    if (
        normalized["initialization"]["result_corpus"]["selected_scope"]
        != scope["selected"]
    ):
        raise ValueError("initialization result corpus must match selected scope")
    _validate_route_bindings(normalized)
    if normalized["rubric"]["last_verified_status"] not in {
        "healthy",
        "needs-attention",
    }:
        raise ValueError("rubric.last_verified_status is invalid for state v3")
    return normalized


def validate_operational_state(state: Mapping, root: Path) -> dict:
    """Validate the exact schema-3 operational state without writing it."""
    state = _require_mapping(state, "operational state")
    version = _require_int(state.get("schema_version"), "schema_version")
    if version == STATE_SCHEMA_VERSION:
        return _validate_operational_state_v3(state, root)
    raise ValueError("unsupported operational state schema version")


def build_initialization_state(
    root,
    *,
    skill_version,
    selected_scope,
    inspected_scope,
    map_path,
    current_truth_routes,
    rubric_version,
    score_before,
    score_after,
    rubric_status,
    cold_paths,
    verified_documents,
    protected_intent,
    hot_path_bytes,
    trust_coverage,
    manifest_identity,
    result_corpus,
    document_results_digest,
    last_completed_event,
):
    """Build deterministic schema-3 state from verified initialization evidence."""
    state = {
        "schema_version": STATE_SCHEMA_VERSION,
        "initialized": {
            "completed": True,
            "skill_version": skill_version,
            "map": map_path,
            "hot_paths": current_truth_routes,
        },
        "rubric": {
            "version": rubric_version,
            "last_verified_score": score_after,
            "last_verified_status": rubric_status,
        },
        "cold_paths": cold_paths,
        "verified_documents": verified_documents,
        "protected_intent": protected_intent,
        "last_completed_event": last_completed_event,
        "scope": {"selected": selected_scope, "inspected": inspected_scope},
        "structural_scores": {"before": score_before, "after": score_after},
        "hot_path_bytes": hot_path_bytes,
        "trust_coverage": trust_coverage,
        "initialization": {
            "manifest_identity": manifest_identity,
            "result_corpus": result_corpus,
            "document_results_digest": document_results_digest,
        },
    }
    normalized = validate_operational_state(state, Path(root).absolute())
    if len(_canonical_operational_bytes(normalized)) > MAX_STATE_BYTES:
        raise ValueError("operational state exceeds capacity")
    return normalized


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
    if _require_int(payload["schema_version"], "findings schema_version") != FINDINGS_SCHEMA_VERSION:
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
        for field, value in record.items():
            if field not in {"id", "fingerprint", "priority", "status"}:
                _require_shared_text_safe(field, f"{name}.field")
                _require_shared_text_safe(value, f"{name}.{field}")
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
    return {"schema_version": FINDINGS_SCHEMA_VERSION, "findings": normalized}


def load_operational_findings(root: Path) -> dict:
    text = _read_operational_file(root, FINDINGS_FILE, MAX_FINDINGS_BYTES)
    if text is None:
        return {"schema_version": FINDINGS_SCHEMA_VERSION, "findings": []}
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
                elif relative == STATE_DIRECTORY and entry.name == "recovery":
                    findings.append(
                        _memory_finding(
                            "state-conflict",
                            "P0",
                            f"{STATE_DIRECTORY}/recovery",
                            "incomplete initialization recovery requires Doctor reconciliation",
                        )
                    )
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


_INIT_CONTROL_ROLES = {
    f"{STATE_DIRECTORY}/{STATE_FILE}": "state",
    f"{STATE_DIRECTORY}/{FINDINGS_FILE}": "findings",
    f"{STATE_DIRECTORY}/{EVENTS_FILE}": "event",
    "manifest": "manifest",
    ".gitignore": "gitignore",
    "AGENTS.md": "agents",
    _LOCAL_MAP_PATH: "local-map",
}
_INIT_REQUIRED_CONTROLS = frozenset(
    {
        f"{STATE_DIRECTORY}/{STATE_FILE}",
        f"{STATE_DIRECTORY}/{FINDINGS_FILE}",
        f"{STATE_DIRECTORY}/{EVENTS_FILE}",
        "manifest",
    }
)


def _validate_init_transaction_bindings(event, document_results):
    """Cross-bind Init's logical transaction plane to its body-free manifest."""
    targets = list(
        _require_sequence(event.get("transaction_targets"), "transaction targets")
    )
    starting = _require_mapping(event.get("starting_digests"), "starting digests")
    roles = _require_mapping(event.get("target_roles"), "target roles")
    order = list(_require_sequence(event.get("replacement_order"), "replacement order"))
    if any(type(target) is not str for target in targets):
        raise ValueError("transaction target paths are invalid")
    target_identities = [os.path.normcase(os.path.normpath(target)) for target in targets]
    if len(target_identities) != len(set(target_identities)):
        raise ValueError("transaction target paths must be unique")

    documents = {result["path"]: result for result in document_results}
    control_targets = [target for target in targets if target not in documents]
    if (
        not _INIT_REQUIRED_CONTROLS.issubset(control_targets)
        or any(target not in _INIT_CONTROL_ROLES for target in control_targets)
    ):
        raise ValueError("initialization control targets are invalid")
    expected_targets = sorted([*control_targets, *documents])
    expected_roles = {
        **{target: _INIT_CONTROL_ROLES[target] for target in control_targets},
        **{path: result["role"] for path, result in documents.items()},
    }
    if targets != expected_targets or dict(roles) != dict(sorted(expected_roles.items())):
        raise ValueError("initialization transaction targets do not match the manifest")
    if set(starting) != set(expected_targets):
        raise ValueError("initialization starting digests do not match transaction targets")
    for target, digest in starting.items():
        if type(digest) is not str or not _TRANSACTION_DIGEST.fullmatch(digest):
            raise ValueError("initialization starting digest is invalid")
        if target in documents and digest != documents[target]["starting_digest"]:
            raise ValueError("document starting digest does not match the manifest")

    for result in documents.values():
        operation = result["operation"]
        role = result["role"]
        if (
            (role == "recovery-archive" and operation != "CREATE")
            or (role == "document-source" and operation not in {"REPLACE", "DELETE"})
            or (role == "document-result" and operation not in {"CREATE", "REPLACE"})
        ):
            raise ValueError("document operation role does not match its operation")

    event_path = f"{STATE_DIRECTORY}/{EVENTS_FILE}"
    manifest_controls = [target for target in control_targets if target == "manifest"]
    protected = [
        target for target in (".gitignore", "AGENTS.md") if target in control_targets
    ]
    fixed_middle = [
        f"{STATE_DIRECTORY}/{STATE_FILE}",
        f"{STATE_DIRECTORY}/{FINDINGS_FILE}",
        *protected,
    ]
    other_middle = sorted(
        set(control_targets)
        - set(manifest_controls)
        - set(fixed_middle)
        - {event_path}
    )
    recovery_creates = [
        result["path"]
        for result in document_results
        if result["operation"] == "CREATE" and result["role"] == "recovery-archive"
    ]
    document_upserts = [
        result["path"]
        for result in document_results
        if result["operation"] in {"CREATE", "REPLACE"}
        and result["role"] != "recovery-archive"
    ]
    document_deletes = [
        result["path"]
        for result in document_results
        if result["operation"] == "DELETE"
    ]
    expected_order = [
        *manifest_controls,
        *recovery_creates,
        *document_upserts,
        *fixed_middle,
        *other_middle,
        *document_deletes,
        event_path,
    ]
    if order != expected_order:
        raise ValueError("initialization replacement order does not match the manifest")


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
        is_init = latest.get("kind") == "init"
        if is_init and (
            latest.get("transaction_schema_version") != 3
            or latest.get("transaction_policy_version") != "init-closeout-v3"
        ):
            valid = False
        fixed = {
            f"{STATE_DIRECTORY}/{STATE_FILE}",
            f"{STATE_DIRECTORY}/{FINDINGS_FILE}",
            f"{STATE_DIRECTORY}/{EVENTS_FILE}",
        }
        if not fixed.issubset(set(targets)):
            valid = False
        if is_init:
            roles = latest.get("target_roles")
            if (
                not isinstance(roles, Mapping)
                or set(roles) != set(targets)
                or any(
                    roles[target] != _INIT_CONTROL_ROLES[target]
                    for target in targets
                    if target in _INIT_CONTROL_ROLES
                )
                or any(
                    target not in _INIT_CONTROL_ROLES
                    and roles[target]
                    not in {"document-result", "recovery-archive", "document-source"}
                    for target in targets
                )
            ):
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


def _initialization_binding_findings(root, state, events):
    if state.get("schema_version") != STATE_SCHEMA_VERSION:
        return []
    initialization = state.get("initialization", {})
    identity = initialization.get("manifest_identity")
    candidates = [
        event
        for event in events
        if event.get("kind") == "init"
        and event.get("manifest_identity") == identity
    ]
    valid = len(candidates) == 1
    try:
        event = candidates[0]
        manifest = _require_mapping(event.get("manifest"), "event manifest")
        expected_path = f"{STATE_DIRECTORY}/manifests/{event['event_id']}.json"
        _require_exact_keys(manifest, {"path", "digest"}, "event manifest")
        if (
            manifest.get("path") != expected_path
            or manifest.get("digest") != f"sha256:{identity}"
            or event.get("manifest_digest") != f"sha256:{identity}"
            or event.get("manifest_schema_version") != _INIT_MANIFEST_SCHEMA_VERSION
            or "manifest" not in event.get("transaction_targets", [])
        ):
            valid = False
        data = _read_bounded_bytes(
            safe_path(Path(root) / expected_path, root),
            MAX_MANIFEST_BYTES,
            expected_path,
        )
        payload = _strict_json_loads(
            _decode_operational_bytes(data, expected_path),
            "initialization manifest",
        )
        if (
            not isinstance(payload, Mapping)
            or set(payload)
            != {
                "schema_version",
                "approval_identity",
                "corpus_transition",
                "dispositions",
                "document_results",
            }
            or payload.get("schema_version") != _INIT_MANIFEST_SCHEMA_VERSION
            or _canonical_operational_bytes(payload) != data
            or hashlib.sha256(data).hexdigest() != identity
        ):
            valid = False
        approvals = event.get("approval_bindings", [])
        approval_identity = hashlib.sha256(
            _canonical_operational_bytes(approvals)
        ).hexdigest()
        transition = payload["corpus_transition"]
        if not isinstance(transition, Mapping) or set(transition) != {"starting", "result"}:
            valid = False
        normalized_transition = {
            "starting": normalize_corpus_v3(
                transition["starting"], name="manifest starting corpus"
            ),
            "result": normalize_corpus_v3(
                transition["result"], name="manifest result corpus"
            ),
        }
        normalized_dispositions = normalize_dispositions_v3(payload["dispositions"])
        normalized_results = normalize_document_results_v3(
            payload["document_results"], root=root
        )
        _validate_init_transaction_bindings(event, normalized_results)
        hard_delete_digests = {
            item["recovery"]["acceptance_digest"]
            for item in normalized_dispositions
            if item.get("recovery", {}).get("kind") == "accepted-hard-delete"
        }
        expected_hard_delete_digest = (
            next(iter(hard_delete_digests))
            if len(hard_delete_digests) == 1
            else None
        )
        document_results_digest = "sha256:" + hashlib.sha256(
            _canonical_operational_bytes(normalized_results)
        ).hexdigest()
        transition_digest = "sha256:" + hashlib.sha256(
            _canonical_operational_bytes(normalized_transition)
        ).hexdigest()
        if (
            payload["approval_identity"] != approval_identity
            or event.get("approval_identity") != approval_identity
            or transition != normalized_transition
            or payload["dispositions"] != normalized_dispositions
            or payload["document_results"] != normalized_results
            or event.get("corpus_transition") != normalized_transition
            or event.get("corpus_transition_digest") != transition_digest
            or initialization.get("result_corpus") != normalized_transition["result"]
            or event.get("document_results_digest") != document_results_digest
            or initialization.get("document_results_digest")
            != document_results_digest
            or len(hard_delete_digests) > 1
            or event.get("hard_delete_acceptance_digest")
            != expected_hard_delete_digest
        ):
            valid = False
    except (IndexError, KeyError, TypeError, ValueError, OSError):
        valid = False
    if valid:
        return []
    return [
        _memory_finding(
            "state-conflict",
            "P0",
            STATE_DIRECTORY,
            "initialization manifest binding does not match verified state",
        )
    ]


def _normalize_manifest(manifest, root, name):
    manifest = _require_mapping(manifest, name)
    _require_exact_keys(manifest, {"path", "digest"}, name)
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


_INIT_EVENT_FIELDS = frozenset(
    {
        "event_id",
        "kind",
        "completed_at",
        "skill_version",
        "approved_ids",
        "score_before",
        "score_after",
        "reason",
        "summary",
        "worktree_kind",
        "repository_identity",
        "worktree_identity",
        "worktree_state_identity",
        "changed_paths",
        "transaction_id",
        "transaction_schema_version",
        "transaction_policy_version",
        "starting_digests",
        "state_semantic_digest",
        "findings_digest",
        "transaction_targets",
        "target_roles",
        "replacement_order",
        "approval_bindings",
        "selected_boundary",
        "visibility",
        "manifest",
        "manifest_digest",
        "manifest_schema_version",
        "manifest_identity",
        "approval_identity",
        "corpus_transition",
        "corpus_transition_digest",
        "document_results_digest",
    }
)
_INIT_EVENT_CONDITIONAL_FIELDS = frozenset(
    {
        "local_map_digest",
        "local_map_schema_version",
        "protected_preview_digest",
        "hard_delete_acceptance_digest",
    }
)


def _init_event_fingerprint_v3(event):
    semantic = json.loads(_canonical_operational_bytes(event))
    semantic.pop("event_id", None)
    manifest = semantic.get("manifest")
    if isinstance(manifest, Mapping):
        semantic["manifest"] = {"digest": manifest.get("digest")}
    return hashlib.sha256(_canonical_operational_bytes(semantic)).hexdigest()


def _validate_init_event_v3(event, name):
    actual = set(event)
    if not _INIT_EVENT_FIELDS.issubset(actual) or actual - (
        _INIT_EVENT_FIELDS | _INIT_EVENT_CONDITIONAL_FIELDS
    ):
        raise ValueError(f"{name} has invalid fields")
    has_local_digest = "local_map_digest" in event
    has_local_schema = "local_map_schema_version" in event
    if has_local_digest is not has_local_schema:
        raise ValueError(f"{name} local map binding is incomplete")
    if event.get("kind") != "init":
        raise ValueError(f"{name}.kind is invalid")
    if event.get("transaction_schema_version") != 3:
        raise ValueError(f"{name}.transaction_schema_version is invalid")
    if event.get("transaction_policy_version") != "init-closeout-v3":
        raise ValueError(f"{name}.transaction_policy_version is invalid")
    if event.get("manifest_schema_version") != 3:
        raise ValueError(f"{name}.manifest_schema_version is invalid")
    if event.get("worktree_kind") not in {"git", "filesystem"}:
        raise ValueError(f"{name}.worktree_kind is invalid")
    for field in (
        "repository_identity",
        "worktree_identity",
        "worktree_state_identity",
        "manifest_identity",
        "approval_identity",
    ):
        if not isinstance(event.get(field), str) or not _MANIFEST_IDENTITY.fullmatch(
            event[field]
        ):
            raise ValueError(f"{name}.{field} is invalid")
    for field in (
        "manifest_digest",
        "corpus_transition_digest",
        "document_results_digest",
        "state_semantic_digest",
        "findings_digest",
        "protected_preview_digest",
        "hard_delete_acceptance_digest",
        "local_map_digest",
    ):
        if field in event and (
            not isinstance(event[field], str)
            or not _MANIFEST_DIGEST.fullmatch(event[field])
        ):
            raise ValueError(f"{name}.{field} is invalid")
    if event["manifest_digest"] != f"sha256:{event['manifest_identity']}":
        raise ValueError(f"{name} manifest identity is inconsistent")
    manifest = _require_mapping(event["manifest"], f"{name}.manifest")
    _require_exact_keys(manifest, {"path", "digest"}, f"{name}.manifest")
    expected_manifest_path = (
        f"{STATE_DIRECTORY}/manifests/{event['event_id']}.json"
    )
    if (
        manifest.get("path") != expected_manifest_path
        or manifest.get("digest") != event["manifest_digest"]
    ):
        raise ValueError(f"{name}.manifest is invalid")
    transition = _require_mapping(event["corpus_transition"], f"{name}.corpus_transition")
    _require_exact_keys(transition, {"starting", "result"}, f"{name}.corpus_transition")
    normalized_transition = {
        "starting": normalize_corpus_v3(
            transition["starting"], name=f"{name}.corpus_transition.starting"
        ),
        "result": normalize_corpus_v3(
            transition["result"], name=f"{name}.corpus_transition.result"
        ),
    }
    if transition != normalized_transition or event["corpus_transition_digest"] != (
        "sha256:"
        + hashlib.sha256(_canonical_operational_bytes(normalized_transition)).hexdigest()
    ):
        raise ValueError(f"{name}.corpus_transition is invalid")
    approvals = _require_sequence(event["approval_bindings"], f"{name}.approval_bindings")
    normalized_approvals = []
    for index, item in enumerate(approvals):
        item = _require_mapping(item, f"{name}.approval_bindings[{index}]")
        _require_exact_keys(item, {"id", "fingerprint"}, f"{name}.approval_bindings[{index}]")
        normalized_approvals.append(dict(item))
    if normalized_approvals != sorted(normalized_approvals, key=lambda item: item["id"]):
        raise ValueError(f"{name}.approval_bindings are invalid")
    approval_identity = hashlib.sha256(
        _canonical_operational_bytes(normalized_approvals)
    ).hexdigest()
    if event["approval_identity"] != approval_identity:
        raise ValueError(f"{name}.approval_identity is invalid")
    targets = list(_require_sequence(event["transaction_targets"], f"{name}.transaction_targets"))
    roles = _require_mapping(event["target_roles"], f"{name}.target_roles")
    order = list(_require_sequence(event["replacement_order"], f"{name}.replacement_order"))
    if (
        targets != sorted(targets)
        or len(targets) != len(set(targets))
        or set(roles) != set(targets)
        or len(order) != len(targets)
        or set(order) != set(targets)
    ):
        raise ValueError(f"{name} transaction targets are invalid")
    if has_local_schema and event["local_map_schema_version"] != 2:
        raise ValueError(f"{name}.local_map_schema_version is invalid")
    fingerprint = _init_event_fingerprint_v3(event)
    if event["event_id"] != "EVT-" + fingerprint[:8].upper():
        raise ValueError("event_id does not match semantic content")
    return fingerprint


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
            for field in ("reason", "summary"):
                if field in event:
                    _require_shared_text_safe(
                        event[field], f"events[{index}].{field}"
                    )
            canonical = (
                _validate_init_event_v3(event, f"events[{index}]")
                if event.get("kind") == "init"
                else event_fingerprint(event)
            )
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


def inspect_operational_memory(root, *, inspect_protected_intent=True):
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
    if state is not None and inspect_protected_intent:
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
        findings.extend(_initialization_binding_findings(root, state, events))
        findings.extend(
            _transaction_integrity_findings(root, state, findings_payload, events)
        )
    return findings


_operational_memory_findings = inspect_operational_memory


__all__ = (
    "EVENTS_FILE",
    "FINDINGS_FILE",
    "FINDINGS_SCHEMA_VERSION",
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
    "build_initialization_state",
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
