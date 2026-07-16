"""Transactional filesystem I/O for approved, verified lifecycle closeout."""

import copy
import errno
import hashlib
import json
import os
import re
import stat
import subprocess
import time
import unicodedata
from collections.abc import Mapping, Sequence
from itertools import islice
from pathlib import Path

from . import paths as _paths
from .discovery import scan_selected_document_corpus
from .identity import event_fingerprint, event_id, finding_fingerprint, finding_id, slug
from .knowledge import (
    LOCAL_MAP_MAX_BYTES,
    LOCAL_MAP_PATH,
    local_prune_reason,
    validate_local_map,
)
from .lifecycle import (
    INIT_DISPOSITION_SCHEMA_VERSION,
    LOCAL_MAP_SCHEMA_VERSION,
    MUTATING_COMMANDS,
    READ_ONLY_COMMANDS,
    TRANSACTION_PREFIX,
    TRANSACTION_POLICY_VERSION,
    TRANSACTION_SCHEMA_VERSION,
    build_verified_event,
    findings_digest,
    init_event_fingerprint,
    _normalize_transaction_operations_v3,
    prepare_dispositions,
    state_semantic_digest,
    transaction_identity,
    transaction_digest,
)
from .memory import (
    EVENTS_FILE,
    FINDINGS_FILE,
    MAX_EVENTS_BYTES,
    MAX_FINDINGS_BYTES,
    MAX_MANIFEST_BYTES,
    MAX_PROTECTED_INTENTS,
    MAX_PROTECTED_INTENT_BYTES,
    MAX_PROTECTED_INTENT_TOTAL_BYTES,
    MAX_STATE_BYTES,
    STATE_DIRECTORY,
    STATE_FILE,
    STATE_SCHEMA_VERSION,
    _markdown_heading_anchors,
    validate_operational_events,
    validate_operational_findings,
    validate_operational_state,
)
from .paths import _is_pruned_relative, normalize_repo_relative, safe_path
from .surfaces import validate_protected_disposition_preview


INTENT_SOURCE_MAX_BYTES = 256 * 1024
INIT_SOURCE_MAX_BYTES = 256 * 1024
INIT_AGENTS_ORIENTATION = "agents-orientation"
INIT_LOCAL_MAP_IGNORE = "local-map-ignore"
INIT_SOURCE_CHANGES = frozenset(
    {INIT_AGENTS_ORIENTATION, INIT_LOCAL_MAP_IGNORE}
)
INIT_ORIENTATION_TEXT = (
    "Repository knowledge starts at the shared documentation map. Before "
    "declaring a plan, decision, preference, or repository context absent, "
    "inspect the declared local knowledge map when present."
)
INIT_LOCAL_MAP_IGNORE_RULE = ".diataxis/local-map.json"
INIT_RECOVERY_JOURNAL_MAX_BYTES = 1024 * 1024
INIT_RECOVERY_BACKUP_MAX_BYTES = 8 * 1024 * 1024
INIT_RECOVERY_BACKUP_MAX_FILES = 64
INIT_RECOVERY_RESULT_MAX_FILES = 80
INIT_RECOVERY_DOCUMENT_RESULT_MAX_BYTES = 4 * 1024 * 1024
INIT_RETAINED_PROBE_MAX_BYTES = 4 * 1024 * 1024
INIT_RECOVERY_TERMINAL_MAX_BYTES = 256 * 1024
INIT_RECOVERY_TERMINAL_VERSION = "init-terminal-v1"
INIT_RECOVERY_IGNORE_NAME = ".gitignore"
INIT_RECOVERY_IGNORE_BYTES = b"*\n"
_ABSENT_DIGEST = "sha256:ABSENT"
_SHA256 = re.compile(r"^sha256:[0-9a-f]{64}$")
_FINDING_ID = re.compile(r"^DOC-([0-9A-F]{8}(?:[0-9A-F]{4})*)$")
_FINGERPRINT = re.compile(r"^[0-9a-f]{64}$")
_TEMP_NAME = re.compile(r"^\.docs-txn-[0-9A-F]{16}-.+\.tmp$")
_HEX_IDENTITY = re.compile(r"^[0-9a-f]{64}$")
_V3_TRANSACTION_ID = re.compile(r"^TXN-[0-9A-F]{16}$")
_V3_CLEANUP_TOMBSTONE = re.compile(
    r"^(TXN-[0-9A-F]{16})\.(cleanup|finalize)$"
)


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
        raise ValueError("transaction payload is not canonical JSON") from exc


def _sha256(data):
    return "sha256:" + hashlib.sha256(data).hexdigest()


def _approval_identity(approvals):
    normalized = sorted(
        ({"id": item["id"], "fingerprint": item["fingerprint"]} for item in approvals),
        key=lambda item: item["id"],
    )
    return hashlib.sha256(_canonical_bytes(normalized)).hexdigest()


def _route_within_scope(route, scope):
    route_key = os.path.normcase(route).replace("\\", "/")
    scope_key = os.path.normcase(scope).replace("\\", "/")
    return scope_key == "." or route_key == scope_key or route_key.startswith(scope_key + "/")


def _validate_init_scope(state, selected_boundary, dispositions):
    scope = state["scope"]
    if selected_boundary != scope["selected"]:
        raise ValueError("initialization boundary must equal selected state scope")
    inspected = scope["inspected"]
    for index, item in enumerate(dispositions):
        if not isinstance(item, Mapping):
            raise ValueError(f"dispositions[{index}] must be an object")
        routes = [("path", item.get("path"))]
        if "target" in item:
            routes.append(("target", item["target"]))
        if "recovery" in item:
            recovery = item["recovery"]
            if not isinstance(recovery, Mapping):
                raise ValueError(f"dispositions[{index}].recovery must be an object")
            if "path" in recovery:
                routes.append(("recovery.path", recovery["path"]))
        for field, value in routes:
            route_path = value.partition("#")[0] if isinstance(value, str) else value
            path = normalize_repo_relative(
                route_path, f"dispositions[{index}].{field}"
            )
            if path.split("/", 1)[0].casefold() == ".local":
                raise ValueError(
                    "initialization disposition must not expose local-only routes"
                )
            if not _route_within_scope(path, inspected):
                raise ValueError(
                    "initialization disposition is outside inspected state scope"
                )


def _target_capacity(relative):
    if relative == f"{STATE_DIRECTORY}/{STATE_FILE}":
        return MAX_STATE_BYTES
    if relative == f"{STATE_DIRECTORY}/{FINDINGS_FILE}":
        return MAX_FINDINGS_BYTES
    if relative == f"{STATE_DIRECTORY}/{EVENTS_FILE}":
        return MAX_EVENTS_BYTES
    if relative == LOCAL_MAP_PATH:
        return LOCAL_MAP_MAX_BYTES
    if relative.startswith(f"{STATE_DIRECTORY}/manifests/"):
        return MAX_MANIFEST_BYTES
    if relative in {"AGENTS.md", ".gitignore"}:
        return INIT_SOURCE_MAX_BYTES
    raise ValueError("transaction target is not recognized")


def _normalize_init_source_changes(changes, command):
    if not isinstance(changes, Sequence) or isinstance(
        changes, (str, bytes, bytearray)
    ):
        raise ValueError("initialization source changes must be an array")
    normalized = list(changes)
    if (
        any(type(item) is not str or item not in INIT_SOURCE_CHANGES for item in normalized)
        or len(normalized) != len(set(normalized))
        or (normalized and command != "init")
    ):
        raise ValueError("initialization source changes are invalid")
    return sorted(normalized)


def _append_utf8_paragraph(data, paragraph):
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError("initialization source target must be UTF-8") from exc
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    if paragraph in normalized:
        return data
    separator = b""
    if data:
        separator = b"\n" if data.endswith((b"\n", b"\r")) else b"\n\n"
    return data + separator + paragraph.encode("utf-8") + b"\n"


def _append_ignore_rule(data):
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError(".gitignore must be UTF-8") from exc
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    if INIT_LOCAL_MAP_IGNORE_RULE in lines:
        return data
    separator = b"" if not data or data.endswith((b"\n", b"\r")) else b"\n"
    return data + separator + INIT_LOCAL_MAP_IGNORE_RULE.encode("utf-8") + b"\n"


def _prepare_init_source_policy(root, changes):
    targets = {}
    bindings = {}
    for change in changes:
        relative = "AGENTS.md" if change == INIT_AGENTS_ORIENTATION else ".gitignore"
        path = safe_path(Path(root) / relative, root)
        original_bytes = _read_bounded_target(path, _target_capacity(relative))
        original = original_bytes or b""
        proposed = (
            _append_utf8_paragraph(original, INIT_ORIENTATION_TEXT)
            if change == INIT_AGENTS_ORIENTATION
            else _append_ignore_rule(original)
        )
        if len(proposed) > _target_capacity(relative):
            raise ValueError("initialization source target exceeds capacity")
        if proposed != original:
            targets[relative] = proposed
        bindings[relative] = {
            "starting_digest": (
                _ABSENT_DIGEST if original_bytes is None else _sha256(original_bytes)
            ),
            "target_digest": _sha256(proposed),
        }
    return targets, bindings


def _prepare_init_source_targets(root, changes):
    return _prepare_init_source_policy(root, changes)[0]


def _read_bounded_target(path, capacity):
    try:
        with Path(path).open("rb") as handle:
            data = handle.read(capacity + 1)
    except FileNotFoundError:
        return None
    if len(data) > capacity:
        raise ValueError("transaction target exceeds its capacity")
    return data


def _path_digest(path, capacity):
    data = _read_bounded_target(path, capacity)
    return _ABSENT_DIGEST if data is None else _sha256(data)


def _stable_path_digest_v3(root, relative, capacity):
    """Return one exact digest only when the confined file stayed stable while read."""
    target = safe_path(Path(root) / relative, root)
    try:
        with target.open("rb") as handle:
            before = os.fstat(handle.fileno())
            if not stat.S_ISREG(before.st_mode):
                raise ValueError("retained source is not a regular file")
            data = handle.read(capacity + 1)
            after = os.fstat(handle.fileno())
        pathname = target.stat()
    except OSError as exc:
        raise ValueError("retained source became unavailable") from exc
    if len(data) > capacity:
        raise ValueError("retained source exceeds capacity")
    stable = lambda value: (
        value.st_dev,
        value.st_ino,
        value.st_mode,
        value.st_size,
        value.st_mtime_ns,
    )
    if stable(before) != stable(after) or stable(pathname) != stable(after):
        raise ValueError("retained source changed while it was read")
    return _sha256(data), len(data)


def _capture_start(root, relative_paths):
    captured = {}
    for relative in relative_paths:
        normalized = normalize_repo_relative(relative, "transaction target")
        path = safe_path(Path(root) / normalized, root)
        captured[normalized] = _path_digest(path, _target_capacity(normalized))
    return captured


def _normalize_approvals(approvals, approved_ids):
    normalized = []
    for raw in approvals:
        if not isinstance(raw, Mapping) or set(raw) != {"id", "fingerprint"}:
            raise ValueError("approval fields are invalid")
        identifier = raw["id"]
        fingerprint = raw["fingerprint"]
        if not isinstance(identifier, str) or not isinstance(fingerprint, str):
            raise ValueError("approval is invalid")
        identifier = identifier.upper()
        fingerprint = fingerprint.lower()
        match = _FINDING_ID.fullmatch(identifier)
        if (
            match is None
            or _FINGERPRINT.fullmatch(fingerprint) is None
            or not fingerprint.startswith(match.group(1).lower())
        ):
            raise ValueError("approval identity does not match fingerprint")
        normalized.append({"id": identifier, "fingerprint": fingerprint})
    normalized.sort(key=lambda item: item["id"])
    if len({item["id"] for item in normalized}) != len(normalized):
        raise ValueError("approval IDs must be unique")
    if [item["id"] for item in normalized] != sorted(approved_ids):
        raise ValueError("approvals do not match event approved IDs")
    return normalized


def _validate_findings_payload(findings, root):
    normalized = validate_operational_findings(copy.deepcopy(findings), root)
    if len(_canonical_bytes(normalized)) > MAX_FINDINGS_BYTES:
        raise ValueError("findings payload exceeds capacity")
    return normalized


def _content_digest(data):
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        return "sha256-bytes:" + hashlib.sha256(data).hexdigest()
    normalized = unicodedata.normalize("NFC", text.replace("\r\n", "\n").replace("\r", "\n"))
    return "sha256-text:" + hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def verify_local_route_hashes(root, routes, *, selected_scope, byte_limit):
    """Read only exactly authorized local routes and attach stable content hashes."""
    root = Path(root).absolute()
    scope = normalize_repo_relative(selected_scope, "selected local scope")
    if scope == "." or _is_pruned_relative(scope) or local_prune_reason(scope):
        raise ValueError("selected local scope must remain local-only")
    if not isinstance(byte_limit, int) or isinstance(byte_limit, bool) or byte_limit <= 0:
        raise ValueError("local content byte limit is invalid")
    if not isinstance(routes, Sequence) or isinstance(routes, (str, bytes, bytearray)):
        raise ValueError("local routes must be an array")
    if len(routes) > 64:
        raise ValueError("local route count exceeds capacity")
    verified = []
    consumed = 0
    for raw in routes:
        if not isinstance(raw, Mapping):
            raise ValueError("local route is invalid")
        route = copy.deepcopy(dict(raw))
        relative = normalize_repo_relative(route.get("route"), "local route")
        if route.get("visibility") != "local-only" or not (
            relative == scope or relative.startswith(scope.rstrip("/") + "/")
        ):
            raise ValueError("local route is outside selected scope")
        path = safe_path(root / relative, root)
        if not path.is_file():
            raise ValueError("local route is unavailable")
        remaining = byte_limit - consumed
        if remaining <= 0:
            raise ValueError("local content byte limit exceeded")
        with path.open("rb") as handle:
            data = handle.read(remaining + 1)
        if len(data) > remaining:
            raise ValueError("local content byte limit exceeded")
        consumed += len(data)
        route["route"] = relative
        route["content_digest"] = _content_digest(data)
        verified.append(route)
    return {
        "routes": verified,
        "content_reads": len(verified),
        "content_bytes": consumed,
        "byte_limit": byte_limit,
    }


def _validate_local_map(local_map):
    try:
        normalized = validate_local_map(local_map)
    except (TypeError, ValueError) as exc:
        raise ValueError("local map lifecycle contract is invalid") from exc
    if normalized["schema_version"] != LOCAL_MAP_SCHEMA_VERSION:
        raise ValueError("local map lifecycle schema is invalid")
    encoded = _canonical_bytes(normalized)
    if len(encoded) > LOCAL_MAP_MAX_BYTES:
        raise ValueError("local map exceeds capacity")
    return encoded


def _normalize_git_root(value):
    text = os.fspath(value).strip()
    if os.name == "nt":
        if text.startswith("//?/"):
            text = text[4:]
        elif text.startswith("\\\\?\\"):
            text = text[4:]
        elif (
            len(text) >= 3
            and text[0] == "/"
            and text[2] == "/"
            and text[1].isalpha()
        ):
            text = f"{text[1].upper()}:{text[2:]}"
    return os.path.normcase(os.path.realpath(os.path.abspath(text)))


def _run_git_probe(root, *arguments):
    root_text = os.fspath(root)
    return subprocess.run(
        ["git", "-C", root_text, *arguments],
        capture_output=True,
        text=True,
        timeout=5,
    )


def _git_ignore_status(root):
    try:
        top = _run_git_probe(root, "rev-parse", "--show-toplevel")
    except (OSError, subprocess.SubprocessError):
        return "no-git"
    if top.returncode != 0:
        return "no-git"
    try:
        selected = _normalize_git_root(root)
        discovered = _normalize_git_root(top.stdout)
    except (OSError, ValueError):
        return "no-git"
    if selected != discovered:
        return "no-git"
    try:
        tracked = _run_git_probe(
            root,
            "ls-files",
            "--error-unmatch",
            "--",
            LOCAL_MAP_PATH,
        )
        if tracked.returncode == 0:
            return "not-ignored"
        if tracked.returncode != 1:
            return "not-ignored"
        ignored = _run_git_probe(
            root,
            "check-ignore",
            "-q",
            "--",
            LOCAL_MAP_PATH,
        )
    except (OSError, subprocess.SubprocessError):
        return "not-ignored"
    return "ignored" if ignored.returncode == 0 else "not-ignored"


def _git_local_map_is_untracked(root):
    try:
        top = _run_git_probe(root, "rev-parse", "--show-toplevel")
        if top.returncode != 0 or _normalize_git_root(root) != _normalize_git_root(top.stdout):
            return False
        tracked = _run_git_probe(
            root,
            "ls-files",
            "--error-unmatch",
            "--",
            LOCAL_MAP_PATH,
        )
    except (OSError, ValueError, subprocess.SubprocessError):
        return False
    return tracked.returncode == 1


def _target_role(relative, manifest_path=None):
    roles = {
        f"{STATE_DIRECTORY}/{STATE_FILE}": "state",
        f"{STATE_DIRECTORY}/{FINDINGS_FILE}": "findings",
        f"{STATE_DIRECTORY}/{EVENTS_FILE}": "event",
        LOCAL_MAP_PATH: "local-map",
        "AGENTS.md": "agents",
        ".gitignore": "gitignore",
    }
    if relative in roles:
        return roles[relative]
    if relative == "manifest":
        return "manifest"
    if manifest_path is not None and relative == manifest_path:
        return "manifest"
    raise ValueError("transaction target role is invalid")


def _replacement_order(targets):
    event_relative = f"{STATE_DIRECTORY}/{EVENTS_FILE}"
    state_relative = f"{STATE_DIRECTORY}/{STATE_FILE}"
    findings_relative = f"{STATE_DIRECTORY}/{FINDINGS_FILE}"
    target_set = set(targets)
    if not {state_relative, findings_relative, event_relative}.issubset(target_set):
        raise ValueError("transaction target set is incomplete")
    remaining = target_set - {state_relative, findings_relative, event_relative}
    manifest_first = sorted(
        relative
        for relative in remaining
        if relative == "manifest"
        or relative.startswith(f"{STATE_DIRECTORY}/manifests/")
    )
    remaining -= set(manifest_first)
    protected_first = [
        relative for relative in (".gitignore", "AGENTS.md") if relative in remaining
    ]
    middle = protected_first + sorted(remaining - set(protected_first))
    return [*manifest_first, state_relative, findings_relative, *middle, event_relative]


def _replacement_order_v3(document_operations, control_order):
    event_path = f"{STATE_DIRECTORY}/{EVENTS_FILE}"
    manifest_controls = [
        path
        for path in control_order
        if path == "manifest" or path.startswith(f"{STATE_DIRECTORY}/manifests/")
    ]
    event_controls = [path for path in control_order if path == event_path]
    middle_controls = [
        path
        for path in control_order
        if path not in set(manifest_controls) | set(event_controls)
    ]
    recovery_creates = [
        operation["path"]
        for operation in document_operations
        if operation["operation"] == "CREATE"
        and operation["role"] == "recovery-archive"
    ]
    document_upserts = [
        operation["path"]
        for operation in document_operations
        if operation["operation"] in {"CREATE", "REPLACE"}
        and operation["role"] != "recovery-archive"
    ]
    document_deletes = [
        operation["path"]
        for operation in document_operations
        if operation["operation"] == "DELETE"
    ]
    return [
        *manifest_controls,
        *recovery_creates,
        *document_upserts,
        *middle_controls,
        *document_deletes,
        *event_controls,
    ]


def _normalized_manifest_label(relative, manifest_path):
    return "manifest" if manifest_path is not None and relative == manifest_path else relative


def _event_authorization_digest(event):
    semantic = copy.deepcopy(dict(event))
    for field in (
        "transaction_id",
        "event_id",
        "manifest",
        "starting_digests",
        "state_semantic_digest",
        "findings_digest",
        "transaction_targets",
        "local_map_digest",
        "protected_preview_digest",
        "disposition_digest",
        "manifest_digest",
    ):
        semantic.pop(field, None)
    return "sha256:" + event_fingerprint(semantic)


def _transaction_identity_semantics_v3(semantics):
    """Sentinelize control digests whose serialized bytes contain the TXN ID."""
    stable = copy.deepcopy(dict(semantics))
    controls = []
    for operation in stable.get("control_operations", ()):
        operation = copy.deepcopy(operation)
        if operation.get("role") == "state":
            operation["result_digest"] = stable["state_semantic_digest"]
        elif operation.get("role") == "manifest":
            operation["result_digest"] = stable["disposition"]["semantic_digest"]
        elif operation.get("role") == "event":
            operation["result_digest"] = transaction_digest(
                {
                    "event_prefix_digest": stable["event_prefix_digest"],
                    "event_semantic_digest": stable["event_semantic_digest"],
                }
            )
        controls.append(operation)
    stable["control_operations"] = controls
    return stable


def _validated_init_manifest(payload, event):
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
        or payload.get("schema_version") != INIT_DISPOSITION_SCHEMA_VERSION
        or event.get("kind") != "init"
    ):
        raise ValueError("initialization manifest fields are invalid")
    approvals = event.get("approval_bindings", [])
    approval_identity = _approval_identity(approvals)
    manifest_identity = event.get("manifest_identity")
    if (
        payload.get("approval_identity") != approval_identity
        or event.get("manifest_schema_version") != INIT_DISPOSITION_SCHEMA_VERSION
        or event.get("approval_identity") != approval_identity
        or not isinstance(manifest_identity, str)
        or _HEX_IDENTITY.fullmatch(manifest_identity) is None
    ):
        raise ValueError("initialization manifest identity is invalid")
    dispositions = payload.get("dispositions")
    if not isinstance(dispositions, list):
        raise ValueError("initialization manifest dispositions are invalid")
    removed_items = [
        item.get("item_id")
        for item in dispositions
        if isinstance(item, Mapping) and item.get("disposition") != "RETAIN"
    ]
    normalized = prepare_dispositions(
        None,
        dispositions,
        removed_items=removed_items,
        git_available=True,
        command="init",
        approval_bindings=approvals,
        corpus_transition=payload["corpus_transition"],
        document_results=payload["document_results"],
    )
    canonical_payload = {
        "schema_version": normalized["schema_version"],
        "approval_identity": normalized["approval_identity"],
        "corpus_transition": normalized["corpus_transition"],
        "dispositions": normalized["dispositions"],
        "document_results": normalized["document_results"],
    }
    if (
        canonical_payload != payload
        or normalized["manifest_identity"] != manifest_identity
        or event.get("corpus_transition") != normalized["corpus_transition"]
        or event.get("corpus_transition_digest")
        != _sha256(_canonical_bytes(normalized["corpus_transition"]))
        or event.get("document_results_digest")
        != normalized["document_results_digest"]
    ):
        raise ValueError("initialization manifest semantics do not match")
    return normalized


def _disposition_authorization(event, targets):
    manifest = event.get("manifest")
    if manifest is not None:
        manifest_path = normalize_repo_relative(manifest.get("path"), "event manifest")
        expected_path = f"{STATE_DIRECTORY}/manifests/{event['event_id']}.json"
        if manifest_path != expected_path or manifest_path not in targets:
            raise ValueError("transaction manifest path is not derived from its event")
        manifest_bytes = targets[manifest_path]
        payload = json.loads(manifest_bytes)
        if (
            not isinstance(payload, Mapping)
            or _canonical_bytes(payload) != manifest_bytes
            or _sha256(manifest_bytes) != event.get("manifest_digest")
            or event.get("manifest_digest") != manifest.get("digest")
        ):
            raise ValueError("transaction manifest semantics do not match")
        if payload.get("schema_version") == INIT_DISPOSITION_SCHEMA_VERSION:
            normalized_manifest = _validated_init_manifest(payload, event)
            if event.get("manifest_digest") != f"sha256:{normalized_manifest['manifest_identity']}":
                raise ValueError("initialization manifest digest does not match identity")
            return {
                "storage": "external",
                "schema_version": INIT_DISPOSITION_SCHEMA_VERSION,
                "manifest_identity": normalized_manifest["manifest_identity"],
                "approval_identity": normalized_manifest["approval_identity"],
                "corpus_transition": normalized_manifest["corpus_transition"],
                "document_results_digest": normalized_manifest[
                    "document_results_digest"
                ],
                "semantic_digest": _sha256(manifest_bytes),
            }, manifest_path
        if event.get("kind") == "init":
            raise ValueError("initialization requires a schema-3 manifest")
        if payload.get("transaction_id") != event.get("transaction_id"):
            raise ValueError("transaction manifest semantics do not match")
        normalized = copy.deepcopy(dict(payload))
        normalized["transaction_id"] = "$TRANSACTION_ID"
        return {
            "storage": "external",
            "semantic_digest": _sha256(_canonical_bytes(normalized)),
        }, manifest_path
    if "dispositions" in event:
        if event.get("kind") == "init":
            raise ValueError("inline initialization manifests are forbidden")
        if event.get("disposition_schema_version") == INIT_DISPOSITION_SCHEMA_VERSION:
            payload = {
                "schema_version": INIT_DISPOSITION_SCHEMA_VERSION,
                "approval_identity": event.get("disposition_approval_identity"),
                "dispositions": copy.deepcopy(event["dispositions"]),
            }
            normalized_manifest = _validated_init_manifest(payload, event)
        else:
            payload = {
                "schema_version": 1,
                "transaction_id": event.get("transaction_id"),
                "dispositions": copy.deepcopy(event["dispositions"]),
            }
            normalized_manifest = None
        encoded = _canonical_bytes(payload)
        if _sha256(encoded) != event.get("disposition_digest"):
            raise ValueError("inline disposition semantics do not match")
        if normalized_manifest is not None:
            return {
                "storage": "inline",
                "schema_version": INIT_DISPOSITION_SCHEMA_VERSION,
                "manifest_identity": normalized_manifest["manifest_identity"],
                "approval_identity": normalized_manifest["approval_identity"],
                "semantic_digest": _sha256(encoded),
            }, None
        payload["transaction_id"] = "$TRANSACTION_ID"
        return {
            "storage": "inline",
            "semantic_digest": _sha256(_canonical_bytes(payload)),
        }, None
    if any(
        field in event
        for field in (
            "disposition_digest",
            "manifest_digest",
            "disposition_schema_version",
            "disposition_approval_identity",
            "disposition_manifest_identity",
        )
    ):
        raise ValueError("transaction disposition fields are incomplete")
    if event.get("kind") == "init":
        raise ValueError("initialization requires an external manifest")
    return None, None


def _retained_source_probes_v3(dispositions, document_operations):
    """Return exact untouched whole-file RETAIN digests in canonical path order."""
    changed = {
        os.path.normcase(
            normalize_repo_relative(operation["path"], "document operation path")
        )
        for operation in document_operations
    }
    probes = {}
    for item in dispositions:
        if (
            item.get("disposition") != "RETAIN"
            or item.get("section") != {"kind": "whole-file"}
        ):
            continue
        path = normalize_repo_relative(item.get("path"), "retained source path")
        identity = os.path.normcase(path)
        if identity in changed:
            continue
        digest = item.get("source_digest")
        if not isinstance(digest, str) or _SHA256.fullmatch(digest) is None:
            raise ValueError("retained source digest is invalid")
        prior = probes.get(identity)
        probe = {"path": path, "digest": digest}
        if prior is not None and prior != probe:
            raise ValueError("retained source probe identity is ambiguous")
        probes[identity] = probe
    return sorted(
        probes.values(),
        key=lambda probe: (probe["path"].casefold(), probe["path"]),
    )


def _plan_authorization_semantics(plan, root):
    if not isinstance(plan, Mapping):
        raise ValueError("transaction plan is invalid")
    targets = plan.get("targets")
    event = plan.get("event")
    if not isinstance(targets, Mapping) or not isinstance(event, Mapping):
        raise ValueError("transaction installed result is invalid")
    if any(not isinstance(data, bytes) for data in targets.values()):
        raise ValueError("transaction targets must contain exact bytes")
    transaction_id = event.get("transaction_id")
    if transaction_id != plan.get("transaction_id"):
        raise ValueError("transaction event identity does not match")
    fingerprint = (
        init_event_fingerprint(event)
        if event.get("kind") == "init"
        else event_fingerprint(event)
    )
    if event.get("event_id") != event_id(fingerprint):
        raise ValueError("transaction event ID does not match semantic content")

    init_source_changes = _normalize_init_source_changes(
        plan.get("init_source_changes", ()), event.get("kind")
    )
    expected_source_targets, expected_source_bindings = _prepare_init_source_policy(
        root, init_source_changes
    )
    source_paths = {"AGENTS.md", ".gitignore"}
    actual_source_targets = {
        relative: data for relative, data in targets.items() if relative in source_paths
    }
    if actual_source_targets != expected_source_targets:
        raise ValueError("initialization source targets do not match fixed policy")
    if plan.get("init_source_bindings", {}) != expected_source_bindings:
        raise ValueError("initialization source bindings do not match fixed policy")

    state_relative = f"{STATE_DIRECTORY}/{STATE_FILE}"
    findings_relative = f"{STATE_DIRECTORY}/{FINDINGS_FILE}"
    events_relative = f"{STATE_DIRECTORY}/{EVENTS_FILE}"
    state = json.loads(targets[state_relative])
    findings = json.loads(targets[findings_relative])
    validated_state = validate_operational_state(state, root)
    validated_findings = validate_operational_findings(findings, root)
    if (
        _canonical_bytes(validated_state) != targets[state_relative]
        or _canonical_bytes(validated_findings) != targets[findings_relative]
    ):
        raise ValueError("authorized operational targets are not canonical")
    if state_semantic_digest(validated_state) != event.get("state_semantic_digest"):
        raise ValueError("authorized state semantics do not match event")
    if findings_digest(validated_findings) != event.get("findings_digest"):
        raise ValueError("authorized finding semantics do not match event")
    if validated_state["last_completed_event"] != event["event_id"] or any(
        record["verified_event"] != event["event_id"]
        for record in validated_state["verified_documents"]
    ):
        raise ValueError("authorized state closeout pointers do not match event")

    event_bytes = _canonical_bytes(event)
    installed_events = targets[events_relative]
    if not isinstance(installed_events, bytes) or not installed_events.endswith(event_bytes):
        raise ValueError("authorized event is not the final installed event")
    event_prefix_digest = _sha256(installed_events[: -len(event_bytes)])

    disposition, manifest_path = _disposition_authorization(event, targets)
    if event.get("kind") == "init":
        if (
            validated_state["schema_version"] != STATE_SCHEMA_VERSION
            or disposition is None
            or disposition.get("schema_version") != INIT_DISPOSITION_SCHEMA_VERSION
            or disposition.get("manifest_identity")
            != validated_state["initialization"]["manifest_identity"]
            or disposition.get("corpus_transition", {}).get("result")
            != validated_state["initialization"]["result_corpus"]
            or disposition.get("document_results_digest")
            != validated_state["initialization"]["document_results_digest"]
            or event.get("score_before")
            != validated_state["structural_scores"]["before"]
            or event.get("score_after")
            != validated_state["structural_scores"]["after"]
        ):
            raise ValueError("initialization state and manifest evidence do not match")
    normalized_operations = _normalize_transaction_operations_v3(
        plan.get("document_operations", ()),
        plan.get("control_operations", ()),
        plan.get("selected_boundary"),
    )
    if (
        normalized_operations["document_operations"]
        != plan.get("document_operations")
        or normalized_operations["control_operations"]
        != plan.get("control_operations")
    ):
        raise ValueError("transaction operation unions are not canonical")
    expected_control_roles = {
        relative: _target_role(relative, manifest_path)
        for relative in targets
    }
    expected_roles = dict(expected_control_roles)
    expected_roles.update(
        {
            operation["path"]: operation["role"]
            for operation in normalized_operations["document_operations"]
        }
    )
    expected_control_order = _replacement_order(targets)
    expected_order = _replacement_order_v3(
        normalized_operations["document_operations"],
        expected_control_order,
    )
    if plan.get("target_roles") != expected_roles:
        raise ValueError("transaction target roles do not match")
    if plan.get("replacement_order") != expected_order:
        raise ValueError("transaction replacement order does not match")
    for operation in normalized_operations["document_operations"]:
        if plan.get("starting_digests", {}).get(operation["path"]) != operation[
            "starting_digest"
        ]:
            raise ValueError("document operation start is not authorization-bound")
    expected_control_operations = [
        {
            "operation": "CONTROL_REPLACE",
            "path": relative,
            "role": expected_control_roles[relative],
            "starting_digest": plan["starting_digests"][relative],
            "result_digest": _sha256(targets[relative]),
        }
        for relative in expected_control_order
    ]
    if normalized_operations["control_operations"] != expected_control_operations:
        raise ValueError("control operations do not match installed targets")
    retained_source_probes = []
    if event.get("kind") == "init":
        manifest_payload = json.loads(targets[manifest_path])
        retained_source_probes = _retained_source_probes_v3(
            manifest_payload["dispositions"],
            normalized_operations["document_operations"],
        )
        if plan.get("retained_source_probes") != retained_source_probes:
            raise ValueError("retained source probes do not match the manifest")
    authorized_corpus_transition = copy.deepcopy(plan.get("corpus_transition"))
    if (
        event.get("kind") == "init"
        and (
            authorized_corpus_transition != event.get("corpus_transition")
            or disposition.get("corpus_transition") != authorized_corpus_transition
        )
    ):
        raise ValueError("corpus transition authorization does not match")

    protected_preview = plan.get("protected_preview")
    if protected_preview is not None:
        if not validate_protected_disposition_preview(protected_preview):
            raise ValueError("protected surface preview is invalid")
        protected_digest = _sha256(_canonical_bytes(protected_preview))
        if protected_digest != event.get("protected_preview_digest"):
            raise ValueError("protected surface evidence does not match event")
    else:
        protected_digest = None
        if "protected_preview_digest" in event:
            raise ValueError("protected surface digest has no evidence")

    local_map_digest = None
    local_schema_version = None
    if LOCAL_MAP_PATH in targets:
        local_bytes = targets[LOCAL_MAP_PATH]
        local_map = validate_local_map(json.loads(local_bytes))
        if _canonical_bytes(local_map) != local_bytes:
            raise ValueError("authorized local map is not canonical")
        local_map_digest = _sha256(local_bytes)
        local_schema_version = local_map["schema_version"]
        if local_map_digest != event.get("local_map_digest"):
            raise ValueError("authorized local map does not match event")
        if any(route["visibility"] != "local-only" for route in local_map["routes"]):
            raise ValueError("authorized local map visibility is invalid")
    elif "local_map_digest" in event:
        raise ValueError("local map digest has no installed target")

    normalized_starting = {
        _normalized_manifest_label(relative, manifest_path): digest
        for relative, digest in plan.get("starting_digests", {}).items()
    }
    normalized_roles = {
        _normalized_manifest_label(relative, manifest_path): role
        for relative, role in expected_roles.items()
    }
    normalized_order = [
        _normalized_manifest_label(relative, manifest_path)
        for relative in expected_order
    ]
    normalized_targets = sorted(normalized_roles)
    if (
        event.get("starting_digests") != dict(sorted(normalized_starting.items()))
        or event.get("transaction_targets") != normalized_targets
    ):
        raise ValueError("transaction event operation bindings do not match")
    if event.get("kind") == "init" and (
        event.get("target_roles") != dict(sorted(normalized_roles.items()))
        or event.get("replacement_order") != normalized_order
    ):
        raise ValueError("initialization event operation bindings do not match")
    approvals = plan.get("approvals")
    if approvals != event.get("approval_bindings", []):
        raise ValueError("transaction approval evidence does not match event")
    if plan.get("command") != event.get("kind"):
        raise ValueError("transaction command does not match event")
    selected_boundary = normalize_repo_relative(
        plan.get("selected_boundary"),
        "transaction selected boundary",
    )
    visibility = plan.get("visibility")
    expected_visibility = ["shared", "local-only"] if LOCAL_MAP_PATH in targets else ["shared"]
    if visibility != expected_visibility:
        raise ValueError("transaction visibility does not match installed targets")
    if (
        plan.get("transaction_schema_version") != TRANSACTION_SCHEMA_VERSION
        or plan.get("transaction_policy_version") != TRANSACTION_POLICY_VERSION
        or event.get("transaction_schema_version") != TRANSACTION_SCHEMA_VERSION
        or event.get("transaction_policy_version") != TRANSACTION_POLICY_VERSION
        or event.get("selected_boundary") != selected_boundary
        or event.get("visibility") != visibility
    ):
        raise ValueError("transaction policy binding does not match")

    semantics = {
        "transaction_schema_version": TRANSACTION_SCHEMA_VERSION,
        "transaction_policy_version": TRANSACTION_POLICY_VERSION,
        "command": plan.get("command"),
        "approvals": copy.deepcopy(approvals),
        "starting_digests": dict(sorted(normalized_starting.items())),
        "selected_boundary": selected_boundary,
        "visibility": list(visibility),
        "target_roles": dict(sorted(normalized_roles.items())),
        "replacement_order": normalized_order,
        "state_semantic_digest": state_semantic_digest(validated_state),
        "state_schema_version": validated_state["schema_version"],
        "findings_digest": findings_digest(validated_findings),
        "findings_schema_version": validated_findings["schema_version"],
        "event_semantic_digest": _event_authorization_digest(event),
        "event_prefix_digest": event_prefix_digest,
        "disposition": disposition,
        "local_map_digest": local_map_digest,
        "local_map_schema_version": local_schema_version,
        "protected_preview_digest": protected_digest,
        "corpus_transition": authorized_corpus_transition,
        "document_operations": [
            {
                key: copy.deepcopy(value)
                for key, value in operation.items()
                if key != "result_bytes"
            }
            for operation in normalized_operations["document_operations"]
        ],
        "control_operations": [
            {
                **copy.deepcopy(operation),
                "path": _normalized_manifest_label(
                    operation["path"], manifest_path
                ),
            }
            for operation in normalized_operations["control_operations"]
        ],
    }
    if init_source_changes:
        semantics["init_source_changes"] = init_source_changes
        semantics["init_source_bindings"] = expected_source_bindings
    if event.get("kind") == "init":
        semantics["retained_source_probes"] = copy.deepcopy(
            retained_source_probes
        )
    return semantics


_AUTHORIZATION_PROJECTION_FIELDS_V3 = {
    "transaction_schema_version",
    "transaction_policy_version",
    "command",
    "approvals",
    "starting_digests",
    "selected_boundary",
    "visibility",
    "target_roles",
    "replacement_order",
    "state_semantic_digest",
    "state_schema_version",
    "findings_digest",
    "findings_schema_version",
    "event_semantic_digest",
    "event_prefix_digest",
    "disposition",
    "local_map_digest",
    "local_map_schema_version",
    "protected_preview_digest",
    "corpus_transition",
    "document_operations",
    "control_operations",
    "retained_source_probes",
}
_AUTHORIZATION_PROJECTION_SOURCE_FIELDS_V3 = {
    "init_source_changes",
    "init_source_bindings",
}


def _authorization_entry_label_v3(entry, manifest_path):
    return "manifest" if entry["path"] == manifest_path else entry["path"]


def _validate_authorization_projection_binding_v3(
    projection,
    entries,
    transaction_id,
    transaction_digest_value,
):
    """Validate the body-free authorization preimage and bind every journal entry."""
    if not isinstance(projection, Mapping):
        raise ValueError("initialization recovery authorization is invalid")
    fields = set(projection)
    if fields != _AUTHORIZATION_PROJECTION_FIELDS_V3 and fields != (
        _AUTHORIZATION_PROJECTION_FIELDS_V3
        | _AUTHORIZATION_PROJECTION_SOURCE_FIELDS_V3
    ):
        raise ValueError("initialization recovery authorization fields are invalid")
    encoded = _canonical_bytes(projection)
    if len(encoded) > INIT_RECOVERY_JOURNAL_MAX_BYTES:
        raise ValueError("initialization recovery authorization exceeds capacity")
    identity_projection = _transaction_identity_semantics_v3(projection)
    if (
        transaction_identity(identity_projection) != transaction_id
        or transaction_digest(identity_projection) != transaction_digest_value
    ):
        raise ValueError("initialization recovery authorization identity is invalid")
    if (
        projection["transaction_schema_version"] != TRANSACTION_SCHEMA_VERSION
        or projection["transaction_policy_version"]
        != TRANSACTION_POLICY_VERSION
        or projection["command"] != "init"
        or not isinstance(projection["approvals"], list)
        or not isinstance(projection["visibility"], list)
        or not isinstance(projection["document_operations"], list)
        or not isinstance(projection["control_operations"], list)
    ):
        raise ValueError("initialization recovery authorization header is invalid")
    selected_boundary = normalize_repo_relative(
        projection["selected_boundary"],
        "recovery authorization selected boundary",
    )
    if selected_boundary != projection["selected_boundary"]:
        raise ValueError("initialization recovery authorization boundary is invalid")

    manifest_entries = [entry for entry in entries if entry["role"] == "manifest"]
    if len(manifest_entries) != 1:
        raise ValueError("initialization recovery authorization manifest is invalid")
    manifest_path = manifest_entries[0]["path"]
    starting = {
        _authorization_entry_label_v3(entry, manifest_path): entry["start"]["digest"]
        for entry in entries
    }
    roles = {
        _authorization_entry_label_v3(entry, manifest_path): entry["role"]
        for entry in entries
    }
    order = [
        _authorization_entry_label_v3(entry, manifest_path) for entry in entries
    ]
    if (
        projection["starting_digests"] != dict(sorted(starting.items()))
        or projection["target_roles"] != dict(sorted(roles.items()))
        or projection["replacement_order"] != order
    ):
        raise ValueError("initialization recovery authorization entry set is invalid")

    document_entries = {
        entry["path"]: entry for entry in entries if entry["plane"] == "document"
    }
    document_operations = projection["document_operations"]
    if len(document_operations) != len(document_entries):
        raise ValueError("initialization recovery document authorization is invalid")
    seen_documents = set()
    for operation in document_operations:
        if not isinstance(operation, Mapping) or set(operation) != {
            "operation",
            "path",
            "role",
            "starting_digest",
            "result_digest",
            "source_item_ids",
            "recovery_binding",
        }:
            raise ValueError("initialization recovery document authorization is invalid")
        path = normalize_repo_relative(
            operation["path"],
            "recovery document authorization path",
        )
        entry = document_entries.get(path)
        if (
            path != operation["path"]
            or path in seen_documents
            or entry is None
            or operation["operation"] != entry["operation"]
            or operation["role"] != entry["role"]
            or operation["starting_digest"] != entry["start"]["digest"]
            or operation["result_digest"] != entry["result"]["digest"]
            or not isinstance(operation["source_item_ids"], list)
            or any(not isinstance(item, str) for item in operation["source_item_ids"])
            or (
                operation["recovery_binding"] is not None
                and (
                    not isinstance(operation["recovery_binding"], str)
                    or _SHA256.fullmatch(operation["recovery_binding"]) is None
                )
            )
        ):
            raise ValueError("initialization recovery document authorization is invalid")
        seen_documents.add(path)

    control_entries = [entry for entry in entries if entry["plane"] == "control"]
    expected_controls = [
        {
            "operation": "CONTROL_REPLACE",
            "path": _authorization_entry_label_v3(entry, manifest_path),
            "role": entry["role"],
            "starting_digest": entry["start"]["digest"],
            "result_digest": entry["result"]["digest"],
        }
        for entry in control_entries
    ]
    if projection["control_operations"] != expected_controls:
        raise ValueError("initialization recovery control authorization is invalid")

    probes = projection["retained_source_probes"]
    if not isinstance(probes, list) or len(probes) > 512:
        raise ValueError("initialization recovery retained probes are invalid")
    probe_paths = set()
    for probe in probes:
        if not isinstance(probe, Mapping) or set(probe) != {"path", "digest"}:
            raise ValueError("initialization recovery retained probe is invalid")
        path = normalize_repo_relative(probe["path"], "recovery retained probe")
        identity = path.casefold()
        if (
            path != probe["path"]
            or identity in probe_paths
            or not isinstance(probe["digest"], str)
            or _SHA256.fullmatch(probe["digest"]) is None
        ):
            raise ValueError("initialization recovery retained probe is invalid")
        probe_paths.add(identity)

    source_entries = {
        entry["path"]: entry
        for entry in control_entries
        if entry["role"] in {"agents", "gitignore"}
    }
    if fields & _AUTHORIZATION_PROJECTION_SOURCE_FIELDS_V3:
        changes = _normalize_init_source_changes(
            projection["init_source_changes"],
            "init",
        )
        if changes != projection["init_source_changes"]:
            raise ValueError("initialization recovery source authorization is invalid")
        bindings = projection["init_source_bindings"]
        if not isinstance(bindings, Mapping) or set(bindings) != set(source_entries):
            raise ValueError("initialization recovery source authorization is invalid")
        for path, binding in bindings.items():
            entry = source_entries[path]
            if binding != {
                "starting_digest": entry["start"]["digest"],
                "target_digest": entry["result"]["digest"],
            }:
                raise ValueError("initialization recovery source authorization is invalid")
    elif source_entries:
        raise ValueError("initialization recovery source authorization is missing")
    return copy.deepcopy(dict(projection))


def _validate_recovered_authorization_v3(
    root,
    journal,
    recovered_bodies,
    staged_events,
):
    """Cross-bind staged control semantics to the authenticated projection."""
    projection = journal["authorization_projection"]
    entries = journal["entries"]
    controls = {entry["role"]: entry for entry in entries if entry["plane"] == "control"}
    targets = {
        entry["path"]: recovered_bodies[entry["result"]["staged"]]
        for entry in controls.values()
    }
    event = staged_events[-1]
    event_bytes = targets[controls["event"]["path"]]
    event_line = _canonical_bytes(event)
    if (
        not event_bytes.endswith(event_line)
        or _sha256(event_bytes[: -len(event_line)])
        != projection["event_prefix_digest"]
        or _event_authorization_digest(event)
        != projection["event_semantic_digest"]
        or event.get("transaction_id") != journal["transaction_id"]
    ):
        raise ValueError("initialization recovery event authorization is invalid")

    state_bytes = targets[controls["state"]["path"]]
    findings_bytes = targets[controls["findings"]["path"]]
    state = validate_operational_state(json.loads(state_bytes), root)
    findings = validate_operational_findings(json.loads(findings_bytes), root)
    if (
        _canonical_bytes(state) != state_bytes
        or _canonical_bytes(findings) != findings_bytes
        or state_semantic_digest(state) != projection["state_semantic_digest"]
        or findings_digest(findings) != projection["findings_digest"]
        or state["schema_version"] != projection["state_schema_version"]
        or findings["schema_version"] != projection["findings_schema_version"]
        or event.get("state_semantic_digest")
        != projection["state_semantic_digest"]
        or event.get("findings_digest") != projection["findings_digest"]
        or state["last_completed_event"] != event["event_id"]
        or any(
            record["verified_event"] != event["event_id"]
            for record in state["verified_documents"]
        )
    ):
        raise ValueError("initialization recovery state authorization is invalid")

    disposition, manifest_path = _disposition_authorization(event, targets)
    if (
        disposition != projection["disposition"]
        or controls["manifest"]["path"] != manifest_path
        or projection["corpus_transition"] != event.get("corpus_transition")
        or disposition.get("corpus_transition") != projection["corpus_transition"]
        or disposition.get("manifest_identity")
        != state["initialization"]["manifest_identity"]
        or disposition.get("corpus_transition", {}).get("result")
        != state["initialization"]["result_corpus"]
        or disposition.get("document_results_digest")
        != state["initialization"]["document_results_digest"]
    ):
        raise ValueError("initialization recovery manifest authorization is invalid")

    if (
        event.get("starting_digests") != projection["starting_digests"]
        or event.get("transaction_targets") != sorted(projection["target_roles"])
        or event.get("target_roles") != projection["target_roles"]
        or event.get("replacement_order") != projection["replacement_order"]
        or event.get("approval_bindings", []) != projection["approvals"]
        or event.get("selected_boundary") != projection["selected_boundary"]
        or event.get("visibility") != projection["visibility"]
        or event.get("protected_preview_digest")
        != projection["protected_preview_digest"]
    ):
        raise ValueError("initialization recovery event operation binding is invalid")

    local_entry = controls.get("local-map")
    if local_entry is None:
        if (
            projection["local_map_digest"] is not None
            or projection["local_map_schema_version"] is not None
            or "local_map_digest" in event
        ):
            raise ValueError("initialization recovery local map authorization is invalid")
    else:
        local_bytes = targets[local_entry["path"]]
        local_map = validate_local_map(json.loads(local_bytes))
        if (
            _canonical_bytes(local_map) != local_bytes
            or _sha256(local_bytes) != projection["local_map_digest"]
            or local_map["schema_version"] != projection["local_map_schema_version"]
            or event.get("local_map_digest") != projection["local_map_digest"]
            or any(route["visibility"] != "local-only" for route in local_map["routes"])
        ):
            raise ValueError("initialization recovery local map authorization is invalid")


def _prepare_plan(
    root,
    *,
    command,
    state,
    findings,
    event,
    approvals,
    dispositions=(),
    removed_items=(),
    local_map=None,
    protected_preview=None,
    recurring_findings=(),
    hard_delete_approval=None,
    corpus_transition=None,
    document_results=(),
    document_operations=(),
    control_operations=None,
    base_events_bytes=None,
    selected_boundary=".",
    init_source_changes=(),
    _transaction_id=None,
    _captured_starting=None,
):
    root = Path(root).absolute()
    safe_path(root, root)
    selected_boundary = normalize_repo_relative(
        selected_boundary,
        "transaction selected boundary",
    )
    approvals = list(approvals)
    dispositions = list(dispositions)
    document_results = list(document_results)
    document_operations = list(document_operations)
    removed_items = list(removed_items)
    recurring_findings = list(recurring_findings)
    init_source_changes = _normalize_init_source_changes(init_source_changes, command)
    if command in READ_ONLY_COMMANDS:
        raise ValueError(f"read-only command cannot close operational memory: {command}")
    if command not in MUTATING_COMMANDS:
        raise ValueError("lifecycle command is invalid")
    if protected_preview is not None:
        if not validate_protected_disposition_preview(protected_preview):
            raise ValueError("protected surface preview is invalid")
        if protected_preview["status"] != "allowed-preview":
            return {
                "status": "requires_user_action",
                "reason": "protected-surface-authorization-required",
                "writes": 0,
            }

    proposed_state = validate_operational_state(copy.deepcopy(state), root)
    proposed_findings = _validate_findings_payload(findings, root)
    event_input = copy.deepcopy(dict(event))
    approved_ids = event_input.get("approved_ids", [])
    normalized_approvals = _normalize_approvals(approvals, approved_ids)
    if event_input.get("kind") != command:
        raise ValueError("event kind does not match lifecycle command")
    if command == "init":
        if proposed_state["schema_version"] != STATE_SCHEMA_VERSION:
            raise ValueError("initialization requires complete state schema v3")
        if (
            event_input.get("score_before")
            != proposed_state["structural_scores"]["before"]
            or event_input.get("score_after")
            != proposed_state["structural_scores"]["after"]
        ):
            raise ValueError("initialization structural scores do not match state evidence")
        _validate_init_scope(proposed_state, selected_boundary, dispositions)
    elif event_input.get("score_after") is not None:
        proposed_state["rubric"]["last_verified_score"] = event_input["score_after"]

    control = safe_path(root / STATE_DIRECTORY, root)
    fixed_targets = [
        f"{STATE_DIRECTORY}/{STATE_FILE}",
        f"{STATE_DIRECTORY}/{FINDINGS_FILE}",
        f"{STATE_DIRECTORY}/{EVENTS_FILE}",
    ]
    init_source_targets, init_source_bindings = _prepare_init_source_policy(
        root, init_source_changes
    )
    fixed_targets.extend(init_source_targets)
    if local_map is not None:
        local_bytes = _validate_local_map(local_map)
        git_status = _git_ignore_status(root)
        planned_ignore = (
            INIT_LOCAL_MAP_IGNORE in init_source_changes
            and ".gitignore" in init_source_targets
            and git_status == "not-ignored"
            and _git_local_map_is_untracked(root)
        )
        if git_status != "ignored" and not planned_ignore:
            return {
                "status": "requires_user_action",
                "reason": "local-map-path-not-ignored"
                if git_status == "not-ignored"
                else "local-map-git-protection-unavailable",
                "git_ignore_protected": False,
                "writes": 0,
            }
        fixed_targets.append(LOCAL_MAP_PATH)
    else:
        local_bytes = None

    normalized_document_operations = _normalize_transaction_operations_v3(
        document_operations,
        (),
        selected_boundary,
    )["document_operations"]
    fixed_target_identities = {relative.casefold() for relative in fixed_targets}
    for operation in normalized_document_operations:
        if operation["path"].casefold() in fixed_target_identities:
            raise ValueError("document and control transaction paths overlap")
    document_operations = normalized_document_operations

    expected_start_paths = {
        *fixed_targets,
        *(operation["path"] for operation in document_operations),
    }
    if _captured_starting is None:
        starting = _capture_start(root, fixed_targets)
        for operation in document_operations:
            actual = _path_digest(
                safe_path(root / operation["path"], root),
                2 * 1024 * 1024,
            )
            if actual != operation["starting_digest"]:
                raise ValueError("document operation starting digest changed")
            starting[operation["path"]] = actual
        captured_starting = copy.deepcopy(starting)
    else:
        if (
            not isinstance(_captured_starting, Mapping)
            or set(_captured_starting) != expected_start_paths
            or any(not isinstance(value, str) for value in _captured_starting.values())
        ):
            raise ValueError("captured transaction starts are invalid")
        starting = copy.deepcopy(dict(_captured_starting))
        for operation in document_operations:
            if starting[operation["path"]] != operation["starting_digest"]:
                raise ValueError("document operation starting digest changed")
        captured_starting = copy.deepcopy(starting)
    local_map_digest = _sha256(local_bytes) if local_bytes is not None else None
    protected_preview_digest = (
        _sha256(_canonical_bytes(protected_preview))
        if protected_preview is not None
        else None
    )
    txid = _transaction_id or ("TXN-" + "0" * 16)
    prepared_dispositions = None
    if command == "init" or dispositions or removed_items:
        git_available = _git_ignore_status(root) != "no-git"
        prepared_dispositions = prepare_dispositions(
            None,
            dispositions,
            removed_items=removed_items,
            git_available=git_available,
            hard_delete_approval=hard_delete_approval,
            transaction_id=txid,
            command=command,
            approval_bindings=normalized_approvals,
            corpus_transition=corpus_transition,
            document_results=document_results,
        )
    if command == "init":
        if prepared_dispositions is None:
            raise ValueError("initialization requires a complete disposition manifest")
        if (
            proposed_state["initialization"]["manifest_identity"]
            != prepared_dispositions["manifest_identity"]
        ):
            raise ValueError("initialization state does not match disposition manifest")

    event_input.update(
        {
            "transaction_schema_version": TRANSACTION_SCHEMA_VERSION,
            "transaction_policy_version": TRANSACTION_POLICY_VERSION,
            "selected_boundary": selected_boundary,
            "visibility": ["shared", "local-only"]
            if local_bytes is not None
            else ["shared"],
        }
    )
    state_digest = state_semantic_digest(proposed_state)
    stored_findings_digest = findings_digest(proposed_findings)
    control_target_labels = list(fixed_targets)
    if prepared_dispositions is not None and prepared_dispositions["storage"] == "external":
        control_target_labels.insert(-1, "manifest")
        starting["manifest"] = _ABSENT_DIGEST
    transaction_targets = [
        *control_target_labels,
        *(operation["path"] for operation in document_operations),
    ]
    logical_target_roles = {
        relative: _target_role(relative)
        for relative in control_target_labels
    }
    logical_target_roles.update(
        {operation["path"]: operation["role"] for operation in document_operations}
    )
    logical_control_order = _replacement_order(control_target_labels)
    logical_replacement_order = _replacement_order_v3(
        document_operations,
        logical_control_order,
    )
    built_event = build_verified_event(
        event_input,
        transaction_id=txid,
        dispositions=prepared_dispositions,
        recurring_findings=recurring_findings,
        starting_digests=starting,
        state_semantic_digest=state_digest,
        findings_digest=stored_findings_digest,
        transaction_targets=transaction_targets,
        target_roles=logical_target_roles,
        replacement_order=logical_replacement_order,
        approval_bindings=normalized_approvals,
        local_map_digest=local_map_digest,
        local_map_schema_version=(
            LOCAL_MAP_SCHEMA_VERSION if local_map_digest is not None else None
        ),
        protected_preview_digest=protected_preview_digest,
    )
    proposed_state["last_completed_event"] = built_event["event_id"]
    for record in proposed_state["verified_documents"]:
        record["verified_event"] = built_event["event_id"]

    if base_events_bytes is None:
        base_events_bytes = (
            _read_bounded_target(control / EVENTS_FILE, MAX_EVENTS_BYTES) or b""
        )
    event_line = _canonical_bytes(built_event)
    events_bytes = base_events_bytes + event_line
    if len(events_bytes) > MAX_EVENTS_BYTES:
        raise ValueError("events payload exceeds capacity")
    state_bytes = _canonical_bytes(proposed_state)
    findings_bytes = _canonical_bytes(proposed_findings)
    if len(state_bytes) > MAX_STATE_BYTES or len(findings_bytes) > MAX_FINDINGS_BYTES:
        raise ValueError("operational payload exceeds capacity")

    targets = {
        f"{STATE_DIRECTORY}/{STATE_FILE}": state_bytes,
        f"{STATE_DIRECTORY}/{FINDINGS_FILE}": findings_bytes,
    }
    targets.update(init_source_targets)
    if prepared_dispositions is not None and prepared_dispositions["storage"] == "external":
        manifest_path = built_event["manifest"]["path"]
        manifest_bytes = prepared_dispositions["bytes"].encode("utf-8")
        if len(manifest_bytes) > MAX_MANIFEST_BYTES:
            raise ValueError("disposition manifest exceeds capacity")
        targets[manifest_path] = manifest_bytes
        manifest_start = (
            _ABSENT_DIGEST
            if _transaction_id is None
            else _path_digest(
                safe_path(root / manifest_path, root),
                MAX_MANIFEST_BYTES,
            )
        )
        if manifest_start != _ABSENT_DIGEST:
            raise ValueError("disposition manifest event target already exists")
        starting[manifest_path] = manifest_start
        starting.pop("manifest", None)
    if local_bytes is not None:
        targets[LOCAL_MAP_PATH] = local_bytes
    targets[f"{STATE_DIRECTORY}/{EVENTS_FILE}"] = events_bytes

    control_replacement_order = _replacement_order(targets)
    replacement_order = _replacement_order_v3(
        document_operations,
        control_replacement_order,
    )
    manifest_path = (
        built_event.get("manifest", {}).get("path")
        if isinstance(built_event.get("manifest"), Mapping)
        else None
    )
    plan = {
        "status": "approval-required",
        "writes": 0,
        "transaction_id": txid,
        "transaction_schema_version": TRANSACTION_SCHEMA_VERSION,
        "transaction_policy_version": TRANSACTION_POLICY_VERSION,
        "command": command,
        "approvals": copy.deepcopy(normalized_approvals),
        "selected_boundary": selected_boundary,
        "visibility": ["shared", "local-only"]
        if local_bytes is not None
        else ["shared"],
        "starting_digests": starting,
        "targets": targets,
        "target_roles": {
            relative: _target_role(relative, manifest_path)
            for relative in targets
        },
        "replacement_order": replacement_order,
        "event": built_event,
        "protected_preview": copy.deepcopy(protected_preview),
    }
    plan["target_roles"].update(
        {operation["path"]: operation["role"] for operation in document_operations}
    )
    derived_control_operations = [
        {
            "operation": "CONTROL_REPLACE",
            "path": relative,
            "role": plan["target_roles"][relative],
            "starting_digest": starting[relative],
            "result_digest": _sha256(targets[relative]),
        }
        for relative in control_replacement_order
    ]
    normalized_operations = _normalize_transaction_operations_v3(
        document_operations,
        derived_control_operations,
        selected_boundary,
    )
    if control_operations is not None:
        supplied = _normalize_transaction_operations_v3(
            document_operations,
            control_operations,
            selected_boundary,
        )["control_operations"]
        if supplied != normalized_operations["control_operations"]:
            raise ValueError("control operations do not match derived targets")
    plan.update(normalized_operations)
    plan["corpus_transition"] = copy.deepcopy(corpus_transition)
    if command == "init":
        plan["retained_source_probes"] = _retained_source_probes_v3(
            prepared_dispositions["dispositions"],
            document_operations,
        )
    if init_source_changes:
        plan["init_source_changes"] = init_source_changes
        plan["init_source_bindings"] = init_source_bindings
    authorization_semantics = _plan_authorization_semantics(plan, root)
    identity_semantics = _transaction_identity_semantics_v3(
        authorization_semantics
    )
    expected_transaction = transaction_identity(identity_semantics)
    expected_transaction_digest = transaction_digest(identity_semantics)
    if _transaction_id is None:
        return _prepare_plan(
            root,
            command=command,
            state=state,
            findings=findings,
            event=event_input,
            approvals=approvals,
            dispositions=dispositions,
            removed_items=removed_items,
            local_map=local_map,
            protected_preview=protected_preview,
            recurring_findings=recurring_findings,
            hard_delete_approval=hard_delete_approval,
            corpus_transition=corpus_transition,
            document_results=document_results,
            document_operations=document_operations,
            control_operations=control_operations,
            base_events_bytes=base_events_bytes,
            selected_boundary=selected_boundary,
            init_source_changes=init_source_changes,
            _transaction_id=expected_transaction,
            _captured_starting=captured_starting,
        )
    if expected_transaction != _transaction_id:
        raise ValueError("transaction identity did not converge")
    plan["transaction_digest"] = expected_transaction_digest
    plan.update(_build_recovery_models_v3(root, plan))
    return plan


def prepare_verified_closeout(
    root,
    *,
    command,
    state,
    findings,
    event,
    approvals,
    dispositions=(),
    removed_items=(),
    local_map=None,
    protected_preview=None,
    recurring_findings=(),
    hard_delete_approval=None,
    corpus_transition=None,
    document_results=(),
    document_operations=(),
    control_operations=None,
    selected_boundary=".",
    init_source_changes=(),
):
    """Build one zero-write closeout plan bound to current target digests."""
    return _prepare_plan(
        root,
        command=command,
        state=state,
        findings=findings,
        event=event,
        approvals=approvals,
        dispositions=dispositions,
        removed_items=removed_items,
        local_map=local_map,
        protected_preview=protected_preview,
        recurring_findings=recurring_findings,
        hard_delete_approval=hard_delete_approval,
        corpus_transition=corpus_transition,
        document_results=document_results,
        document_operations=document_operations,
        control_operations=control_operations,
        selected_boundary=selected_boundary,
        init_source_changes=init_source_changes,
    )


def _operation_capacity_v3(operation):
    return (
        2 * 1024 * 1024
        if operation["plane"] == "document"
        else _target_capacity(operation["path"])
    )


def _operation_start_v3(root, operation, index, recovery_files):
    relative = operation["path"]
    target = safe_path(Path(root) / relative, root)
    expected = operation["starting_digest"]
    if not os.path.lexists(target):
        if expected != _ABSENT_DIGEST:
            raise ValueError("journal start state does not match authorized operation")
        return {
            "kind": "absent",
            "digest": _ABSENT_DIGEST,
            "bytes": 0,
            "mode": None,
            "mtime_ns": None,
            "backup": None,
        }
    capacity = _operation_capacity_v3(operation)
    try:
        with target.open("rb") as handle:
            before = os.fstat(handle.fileno())
            if not stat.S_ISREG(before.st_mode):
                raise ValueError("journal start target is not a regular file")
            data = handle.read(capacity + 1)
            after = os.fstat(handle.fileno())
    except OSError as exc:
        raise ValueError("journal file start became unavailable") from exc
    if len(data) > capacity:
        raise ValueError("journal file start exceeds capacity")
    stable = lambda value: (
        value.st_dev,
        value.st_ino,
        value.st_mode,
        value.st_size,
        value.st_mtime_ns,
    )
    if stable(before) != stable(after) or stable(target.stat()) != stable(after):
        raise ValueError("journal file start changed while it was read")
    actual = _sha256(data)
    if actual != expected:
        raise ValueError("journal start state does not match authorized operation")
    metadata = after
    backup = f"backups/{index:04d}.bin"
    recovery_files[backup] = data
    return {
        "kind": "file",
        "digest": actual,
        "bytes": len(data),
        "mode": stat.S_IMODE(metadata.st_mode),
        "mtime_ns": metadata.st_mtime_ns,
        "backup": backup,
    }


def _operation_result_v3(plan, operation, index, recovery_files):
    if operation["result_digest"] == _ABSENT_DIGEST:
        return {
            "kind": "absent",
            "digest": _ABSENT_DIGEST,
            "bytes": 0,
            "staged": None,
        }
    if operation["plane"] == "document":
        data = operation.get("result_bytes")
    else:
        data = plan["targets"].get(operation["path"])
    if not isinstance(data, bytes) or _sha256(data) != operation["result_digest"]:
        raise ValueError("journal result bytes do not match authorized digest")
    staged = f"results/{index:04d}.bin"
    recovery_files[staged] = data
    return {
        "kind": "file",
        "digest": operation["result_digest"],
        "bytes": len(data),
        "staged": staged,
    }


def _parent_facts_v3(root, paths):
    root = Path(root).absolute()
    relatives = set()
    for relative in paths:
        parent = Path(relative).parent
        while True:
            normalized = "." if os.fspath(parent) in {"", "."} else parent.as_posix()
            relatives.add(normalized)
            if normalized == ".":
                break
            parent = parent.parent
    facts = []
    for relative in sorted(relatives, key=lambda value: (value.casefold(), value)):
        target = root if relative == "." else safe_path(root / relative, root)
        if not os.path.lexists(target):
            facts.append(
                {
                    "path": relative,
                    "starting_kind": "absent",
                    "device": None,
                    "inode": None,
                }
            )
            continue
        if not target.is_dir() or target.is_symlink():
            raise ValueError("transaction parent is not a stable directory")
        metadata = target.stat()
        if metadata.st_dev <= 0 or metadata.st_ino <= 0:
            raise ValueError("transaction parent identity is unavailable")
        facts.append(
            {
                "path": relative,
                "starting_kind": "directory",
                "device": metadata.st_dev,
                "inode": metadata.st_ino,
            }
        )
    return facts


def _validate_recovery_capacity_v3(recovery_files, journal_bytes):
    if not isinstance(recovery_files, Mapping) or not isinstance(
        journal_bytes, Mapping
    ):
        raise ValueError("initialization recovery capacity input is invalid")
    if set(journal_bytes) != {"preparing", "prepared"} or any(
        not isinstance(data, bytes) for data in journal_bytes.values()
    ):
        raise ValueError("initialization recovery journal bytes are invalid")
    backups = []
    results = []
    for relative, data in recovery_files.items():
        if not isinstance(relative, str) or not isinstance(data, bytes):
            raise ValueError("initialization recovery file is invalid")
        if re.fullmatch(r"backups/\d{4}\.bin", relative):
            backups.append(data)
        elif re.fullmatch(r"results/\d{4}\.bin", relative):
            results.append(data)
        else:
            raise ValueError("initialization recovery file path is invalid")
    if (
        len(backups) > INIT_RECOVERY_BACKUP_MAX_FILES
        or len(results) > INIT_RECOVERY_RESULT_MAX_FILES
        or sum(map(len, backups)) > INIT_RECOVERY_BACKUP_MAX_BYTES
        or any(
            len(data) > INIT_RECOVERY_JOURNAL_MAX_BYTES
            for data in journal_bytes.values()
        )
    ):
        raise ValueError("initialization recovery files exceed capacity")
    return True


def _build_recovery_models_v3(root, plan):
    authorization_projection = _plan_authorization_semantics(plan, root)
    identity_projection = _transaction_identity_semantics_v3(
        authorization_projection
    )
    if (
        transaction_identity(identity_projection) != plan["transaction_id"]
        or transaction_digest(identity_projection) != plan["transaction_digest"]
    ):
        raise ValueError("recovery authorization projection does not match transaction")
    document_operations = [
        {"plane": "document", **copy.deepcopy(operation)}
        for operation in plan["document_operations"]
    ]
    control_operations = [
        {"plane": "control", **copy.deepcopy(operation)}
        for operation in plan["control_operations"]
    ]
    operations_by_path = {
        operation["path"]: operation
        for operation in [*document_operations, *control_operations]
    }
    if set(operations_by_path) != set(plan["replacement_order"]):
        raise ValueError("journal operation order does not match authorization")
    operations = [
        operations_by_path[relative] for relative in plan["replacement_order"]
    ]
    recovery_files = {}
    entries = []
    for index, operation in enumerate(operations):
        start = _operation_start_v3(root, operation, index, recovery_files)
        result = _operation_result_v3(plan, operation, index, recovery_files)
        entries.append(
            {
                "index": index,
                "plane": operation["plane"],
                "operation": operation["operation"],
                "path": operation["path"],
                "role": operation["role"],
                "start": start,
                "result": result,
                "status": "pending",
            }
        )
    event_entries = [entry for entry in entries if entry["role"] == "event"]
    if len(event_entries) != 1:
        raise ValueError("initialization recovery requires one event commit entry")
    event_entry = event_entries[0]
    base = {
        "schema_version": 3,
        "journal_version": "init-recovery-v1",
        "transaction_id": plan["transaction_id"],
        "transaction_digest": plan["transaction_digest"],
        "authorization_projection": copy.deepcopy(authorization_projection),
        "control_directory_preexisted": (
            Path(root, STATE_DIRECTORY).is_dir()
            and not Path(root, STATE_DIRECTORY).is_symlink()
        ),
        "recovery_container_preexisted": (
            Path(root, STATE_DIRECTORY, "recovery").is_dir()
            and not Path(root, STATE_DIRECTORY, "recovery").is_symlink()
        ),
        "created_parent_identities": {},
        "parent_facts": _parent_facts_v3(
            root, [operation["path"] for operation in operations]
        ),
        "entries": entries,
        "event_commit": {
            "path": event_entry["path"],
            "starting_digest": event_entry["start"]["digest"],
            "result_digest": event_entry["result"]["digest"],
        },
    }
    models = {
        phase: {**copy.deepcopy(base), "phase": phase}
        for phase in ("preparing", "prepared")
    }
    journal_bytes = {
        phase: _canonical_bytes(model) for phase, model in models.items()
    }
    _validate_recovery_capacity_v3(recovery_files, journal_bytes)
    return {
        "journal_models": models,
        "journal_bytes": journal_bytes,
        "recovery_files": recovery_files,
    }


def _write_flushed_file_v3(path, data, *, exclusive):
    flags = os.O_WRONLY | os.O_CREAT | (os.O_EXCL if exclusive else os.O_TRUNC)
    if hasattr(os, "O_BINARY"):
        flags |= os.O_BINARY
    descriptor = os.open(path, flags, 0o600)
    try:
        view = memoryview(data)
        while view:
            written = os.write(descriptor, view)
            if written <= 0:
                raise OSError("recovery write made no progress")
            view = view[written:]
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _verify_exact_file_v3(path, expected):
    with Path(path).open("rb") as handle:
        observed = handle.read(len(expected) + 1)
    if observed != expected:
        raise OSError("recovery file verification failed")


def _verify_recovery_ignore_v3(
    recovery_root,
    *,
    allow_partial=False,
    cleanup_pin=None,
):
    recovery_root = Path(recovery_root)
    if isinstance(cleanup_pin, Mapping) and cleanup_pin.get("platform") == "posix":
        no_follow = getattr(os, "O_NOFOLLOW", None)
        descriptor = cleanup_pin.get("fd")
        if no_follow is None or type(descriptor) is not int:
            raise ValueError("initialization recovery ignore guard pin is unavailable")
        flags = os.O_RDONLY | no_follow
        if hasattr(os, "O_CLOEXEC"):
            flags |= os.O_CLOEXEC
        try:
            guard_descriptor = os.open(
                INIT_RECOVERY_IGNORE_NAME,
                flags,
                dir_fd=descriptor,
            )
        except OSError as exc:
            raise ValueError("initialization recovery ignore guard is unavailable") from exc
        try:
            before = os.fstat(guard_descriptor)
            if not stat.S_ISREG(before.st_mode):
                raise ValueError("initialization recovery ignore guard is unsafe")
            observed = bytearray()
            limit = len(INIT_RECOVERY_IGNORE_BYTES) + 1
            while len(observed) < limit:
                chunk = os.read(guard_descriptor, limit - len(observed))
                if not chunk:
                    break
                observed.extend(chunk)
            after = os.fstat(guard_descriptor)
            if (
                (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
                != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)
                or after.st_size != len(observed)
            ):
                raise ValueError("initialization recovery ignore guard changed while reading")
            observed = bytes(observed)
        finally:
            os.close(guard_descriptor)
        if observed == INIT_RECOVERY_IGNORE_BYTES:
            return
        if allow_partial and INIT_RECOVERY_IGNORE_BYTES.startswith(observed):
            return
        raise ValueError("initialization recovery ignore guard is invalid")
    path = safe_path(recovery_root / INIT_RECOVERY_IGNORE_NAME, recovery_root)
    metadata = os.lstat(path)
    if not stat.S_ISREG(metadata.st_mode) or _paths._is_reparse(path):
        raise ValueError("initialization recovery ignore guard is unsafe")
    with path.open("rb") as handle:
        observed = handle.read(len(INIT_RECOVERY_IGNORE_BYTES) + 1)
    if observed == INIT_RECOVERY_IGNORE_BYTES:
        return
    if allow_partial and INIT_RECOVERY_IGNORE_BYTES.startswith(observed):
        return
    raise ValueError("initialization recovery ignore guard is invalid")


def _verify_recovery_git_protection_v3(root, recovery_root, plan):
    _verify_recovery_ignore_v3(recovery_root)
    if "local-only" not in plan.get("visibility", ()):
        return
    relative = Path(recovery_root).relative_to(root).as_posix()
    for candidate in (
        f"{relative}/{INIT_RECOVERY_IGNORE_NAME}",
        f"{relative}/journal.json",
        f"{relative}/backups/0000.bin",
        f"{relative}/results/0000.bin",
    ):
        try:
            ignored = _run_git_probe(
                root,
                "check-ignore",
                "-q",
                "--no-index",
                "--",
                candidate,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise ValueError("initialization recovery Git protection is unavailable") from exc
        if ignored.returncode != 0:
            raise ValueError("initialization recovery path is not Git ignored")


def _revalidate_parent_facts_v3(root, plan, recovery_root):
    _revalidate_recorded_parent_facts_v3(
        root,
        plan["journal_models"]["prepared"]["parent_facts"],
        recovery_root,
        control_directory_preexisted=plan["journal_models"]["prepared"][
            "control_directory_preexisted"
        ],
    )


def _revalidate_recorded_parent_facts_v3(
    root,
    parent_facts,
    recovery_root,
    *,
    control_directory_preexisted,
    created_directories=None,
):
    root = Path(root).absolute()
    recovery_root = safe_path(recovery_root, root)
    recovery_device = recovery_root.stat().st_dev
    created_directories = created_directories or {}
    for fact in parent_facts:
        relative = fact["path"]
        target = root if relative == "." else safe_path(root / relative, root)
        if fact["starting_kind"] == "directory":
            if not target.is_dir() or target.is_symlink():
                raise ValueError("transaction parent changed during recovery preparation")
            metadata = target.stat()
            if (
                metadata.st_dev != fact["device"]
                or metadata.st_ino != fact["inode"]
                or metadata.st_dev != recovery_device
            ):
                raise ValueError("transaction parent identity changed during recovery preparation")
            continue
        if not os.path.lexists(target):
            continue
        identity = created_directories.get(relative)
        if not target.is_dir() or target.is_symlink():
            raise ValueError("absent transaction parent changed during recovery preparation")
        metadata = target.stat()
        created_match = (
            isinstance(identity, Mapping)
            and metadata.st_dev == identity.get("device")
            and metadata.st_ino == identity.get("inode")
        )
        if (
            metadata.st_dev != recovery_device
            or not created_match
        ):
            raise ValueError("absent transaction parent changed during recovery preparation")


def _revalidate_recovery_starts_v3(root, plan, recovery_root, journal=None):
    recovery_device = safe_path(recovery_root, root).stat().st_dev
    operations = {
        operation["path"]: {"plane": plane, **operation}
        for plane, field in (
            ("document", "document_operations"),
            ("control", "control_operations"),
        )
        for operation in plan[field]
    }
    for entry in plan["journal_models"]["prepared"]["entries"]:
        observed = _operation_start_v3(
            root,
            operations[entry["path"]],
            entry["index"],
            {},
        )
        if observed != entry["start"]:
            raise ValueError("authorized target changed during recovery preparation")
        if (
            observed["kind"] == "file"
            and safe_path(Path(root) / entry["path"], root).stat().st_dev
            != recovery_device
        ):
            raise OSError(
                errno.EXDEV,
                "recovery and existing targets are on different devices",
            )
    effective = (
        journal
        if journal is not None
        else plan["journal_models"]["prepared"]
    )
    _revalidate_recorded_parent_facts_v3(
        root,
        effective["parent_facts"],
        recovery_root,
        control_directory_preexisted=effective["control_directory_preexisted"],
        created_directories=effective.get("created_parent_identities", {}),
    )


def _prepare_recovery_area_v3(root, plan):
    """Durably prepare body files and a restart journal before target mutation."""
    root = Path(root).absolute()
    _validate_plan_authorization(root, plan)
    expected_models = _build_recovery_models_v3(root, plan)
    for field in ("journal_models", "journal_bytes", "recovery_files"):
        if plan.get(field) != expected_models[field]:
            raise ValueError("recovery preparation model does not match authorization")
    recovery_root = safe_path(
        root / STATE_DIRECTORY / "recovery" / plan["transaction_id"],
        root,
    )
    if os.path.lexists(recovery_root):
        raise FileExistsError("transaction recovery area already exists")
    backups = recovery_root / "backups"
    results = recovery_root / "results"
    runtime_models = None
    preparation_parent_identities = {}

    def ensure_preparation_parent(path, preexisted):
        path = safe_path(path, root)
        if preexisted:
            if (
                not path.is_dir()
                or path.is_symlink()
                or _paths._is_reparse(path)
            ):
                raise ValueError("initialization recovery parent is unsafe")
            return
        if os.path.lexists(path):
            raise ValueError("initialization recovery parent changed before preparation")
        try:
            path.mkdir()
        except BaseException:
            if (
                os.path.lexists(path)
                and path.is_dir()
                and not path.is_symlink()
                and not _paths._is_reparse(path)
            ):
                metadata = path.stat()
                preparation_parent_identities[
                    path.relative_to(root).as_posix()
                ] = {
                    "device": metadata.st_dev,
                    "inode": metadata.st_ino,
                }
            raise
        metadata = path.stat()
        preparation_parent_identities[path.relative_to(root).as_posix()] = {
            "device": metadata.st_dev,
            "inode": metadata.st_ino,
        }
        _directory_fsync(path.parent)

    try:
        preparing_model = plan["journal_models"]["preparing"]
        control_root = safe_path(root / STATE_DIRECTORY, root)
        recovery_parent = recovery_root.parent
        if (
            preparing_model["recovery_container_preexisted"]
            and not preparing_model["control_directory_preexisted"]
        ):
            raise ValueError("initialization recovery parent history is invalid")
        ensure_preparation_parent(
            control_root,
            preparing_model["control_directory_preexisted"],
        )
        ensure_preparation_parent(
            recovery_parent,
            preparing_model["recovery_container_preexisted"],
        )
        runtime_created = {}
        for fact in preparing_model["parent_facts"]:
            if fact["starting_kind"] != "absent":
                continue
            target = safe_path(root / fact["path"], root)
            if not os.path.lexists(target):
                continue
            if not target.is_dir() or target.is_symlink():
                raise ValueError("created transaction parent is not a stable directory")
            metadata = target.stat()
            runtime_created[fact["path"]] = {
                "device": metadata.st_dev,
                "inode": metadata.st_ino,
            }
        runtime_models = {
            phase: {
                **copy.deepcopy(plan["journal_models"][phase]),
                "created_parent_identities": copy.deepcopy(runtime_created),
            }
            for phase in ("preparing", "prepared")
        }
        runtime_journal_bytes = {
            phase: _canonical_bytes(runtime_models[phase])
            for phase in ("preparing", "prepared")
        }
        _validate_recovery_capacity_v3(plan["recovery_files"], runtime_journal_bytes)
        recovery_root.mkdir()
        recovery_device = recovery_root.stat().st_dev
        for entry in plan["journal_models"]["preparing"]["entries"]:
            if entry["start"]["kind"] != "file":
                continue
            target = safe_path(root / entry["path"], root)
            if target.stat().st_dev != recovery_device:
                raise OSError(
                    errno.EXDEV,
                    "recovery and existing targets are on different devices",
                )
        for fact in plan["journal_models"]["preparing"]["parent_facts"]:
            if (
                fact["starting_kind"] == "directory"
                and fact["device"] != recovery_device
            ):
                raise OSError(
                    errno.EXDEV,
                    "recovery and target parents are on different devices",
                )
        ignore_guard = recovery_root / INIT_RECOVERY_IGNORE_NAME
        _write_flushed_file_v3(
            ignore_guard,
            INIT_RECOVERY_IGNORE_BYTES,
            exclusive=True,
        )
        _verify_recovery_ignore_v3(recovery_root)
        _directory_fsync(recovery_root)
        _verify_recovery_git_protection_v3(root, recovery_root, plan)
        backups.mkdir()
        results.mkdir()
        _directory_fsync(recovery_root)
        _verify_recovery_ignore_v3(recovery_root)
        journal = recovery_root / "journal.json"
        _write_active_journal_v3(recovery_root, runtime_models["preparing"])
        _verify_exact_file_v3(journal, runtime_journal_bytes["preparing"])
        for relative, data in sorted(plan["recovery_files"].items()):
            _verify_recovery_ignore_v3(recovery_root)
            target = recovery_root / Path(relative)
            _write_flushed_file_v3(target, data, exclusive=True)
            _verify_exact_file_v3(target, data)
            _verify_recovery_ignore_v3(recovery_root)
        _write_active_journal_v3(recovery_root, runtime_models["prepared"])
        _verify_exact_file_v3(journal, runtime_journal_bytes["prepared"])
        _verify_recovery_ignore_v3(recovery_root)
        for directory in (
            backups,
            results,
            recovery_root,
            recovery_root.parent,
            recovery_root.parent.parent,
            root,
        ):
            _directory_fsync(directory)
        _revalidate_recovery_starts_v3(
            root,
            plan,
            recovery_root,
            runtime_models["prepared"],
        )
        return {
            "path": recovery_root,
            "journal": copy.deepcopy(runtime_models["prepared"]),
            "journal_digest": _sha256(runtime_journal_bytes["prepared"]),
        }
    except (KeyboardInterrupt, OSError, ValueError) as error:
        rollback = _abort_failed_preparation_v3(
            root,
            recovery_root,
            plan,
            runtime_models["prepared"] if runtime_models is not None else None,
            preparation_parent_identities,
        )
        if rollback["complete"] or isinstance(error, KeyboardInterrupt):
            raise
        raise _V3PreparationFailure(error, rollback) from error


def _verify_staged(relative, data, staged, root):
    if Path(staged).read_bytes() != data:
        raise OSError("staged bytes differ")
    if relative.endswith(f"/{STATE_FILE}"):
        validate_operational_state(json.loads(data), root)
    elif relative.endswith(f"/{FINDINGS_FILE}"):
        validate_operational_findings(json.loads(data), root)
    elif relative.endswith(f"/{EVENTS_FILE}"):
        events = [json.loads(line) for line in data.splitlines() if line.strip()]
        if validate_operational_events(events):
            raise ValueError("staged events fail semantic validation")


def _validate_plan_authorization(root, plan):
    semantics = _plan_authorization_semantics(plan, root)
    identity_semantics = _transaction_identity_semantics_v3(semantics)
    expected = transaction_identity(identity_semantics)
    if expected != plan.get("transaction_id"):
        raise ValueError("transaction authorization identity does not match")
    if transaction_digest(identity_semantics) != plan.get("transaction_digest"):
        raise ValueError("transaction authorization digest does not match")


def _verify_transaction_set(root, plan, staged):
    state_relative = f"{STATE_DIRECTORY}/{STATE_FILE}"
    findings_relative = f"{STATE_DIRECTORY}/{FINDINGS_FILE}"
    events_relative = f"{STATE_DIRECTORY}/{EVENTS_FILE}"
    state = validate_operational_state(
        json.loads(Path(staged[state_relative]).read_bytes()),
        root,
    )
    findings = validate_operational_findings(
        json.loads(Path(staged[findings_relative]).read_bytes()),
        root,
    )
    events = [
        json.loads(line)
        for line in Path(staged[events_relative]).read_bytes().splitlines()
        if line.strip()
    ]
    if not events or events[-1] != plan["event"]:
        raise ValueError("transaction success event is not last")
    event = events[-1]
    if event.get("transaction_id") != plan["transaction_id"]:
        raise ValueError("transaction event identity does not match")
    if event.get("state_semantic_digest") != state_semantic_digest(state):
        raise ValueError("transaction state digest does not match")
    if event.get("findings_digest") != findings_digest(findings):
        raise ValueError("transaction findings digest does not match")

    expected_targets = {state_relative, findings_relative, events_relative}
    if event.get("kind") == "init":
        expected_targets.update(
            relative
            for relative in ("AGENTS.md", ".gitignore")
            if relative in plan["targets"]
        )
    manifest = event.get("manifest")
    if manifest is not None:
        manifest_relative = normalize_repo_relative(manifest.get("path"), "event manifest")
        expected_targets.add(manifest_relative)
        manifest_bytes = Path(staged[manifest_relative]).read_bytes()
        if _sha256(manifest_bytes) != event.get("manifest_digest"):
            raise ValueError("transaction manifest digest does not match")
        if _canonical_bytes(json.loads(manifest_bytes)) != manifest_bytes:
            raise ValueError("transaction manifest is not canonical")
    if "local_map_digest" in event:
        expected_targets.add(LOCAL_MAP_PATH)
        local_bytes = Path(staged[LOCAL_MAP_PATH]).read_bytes()
        if _sha256(local_bytes) != event["local_map_digest"]:
            raise ValueError("transaction local map digest does not match")
        validate_local_map(json.loads(local_bytes))
    if set(plan["targets"]) != expected_targets or set(staged) != expected_targets:
        raise ValueError("transaction target set does not match")

    event_target_labels = set(expected_targets)
    plan_start_labels = dict(plan["starting_digests"])
    if manifest is not None:
        event_target_labels.remove(manifest_relative)
        event_target_labels.add("manifest")
        manifest_start = plan_start_labels.pop(manifest_relative)
        plan_start_labels["manifest"] = manifest_start
    if set(event.get("transaction_targets", ())) != event_target_labels:
        raise ValueError("transaction target labels do not match")
    if event.get("starting_digests") != dict(sorted(plan_start_labels.items())):
        raise ValueError("transaction starting digests do not match")


def _record_created_parents(parent, created_directories):
    missing = []
    current = Path(parent)
    while not current.exists():
        missing.append(current)
        current = current.parent
    Path(parent).mkdir(parents=True, exist_ok=True)
    if created_directories is not None:
        created_directories.update(missing)


def _remove_empty_created_directories(created_directories):
    for directory in sorted(
        created_directories,
        key=lambda path: len(path.parts),
        reverse=True,
    ):
        try:
            directory.rmdir()
        except OSError:
            pass


def _stage_bytes(target, data, transaction_id, created_directories=None):
    target = Path(target)
    _record_created_parents(target.parent, created_directories)
    name = f"{TRANSACTION_PREFIX}{transaction_id.removeprefix('TXN-')}-{target.name}.tmp"
    staged = target.parent / name
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_BINARY"):
        flags |= os.O_BINARY
    descriptor = None
    try:
        descriptor = os.open(staged, flags, 0o600)
        view = memoryview(data)
        while view:
            written = os.write(descriptor, view)
            if written <= 0:
                raise OSError("staged write made no progress")
            view = view[written:]
        os.fsync(descriptor)
        os.close(descriptor)
        descriptor = None
    except BaseException:
        if descriptor is not None:
            try:
                os.close(descriptor)
            except OSError:
                pass
        try:
            staged.unlink()
        except OSError:
            pass
        raise
    return staged


def _directory_fsync(directory):
    if os.name == "nt" or not hasattr(os, "O_DIRECTORY"):
        return
    descriptor = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _restore_target(target, original, mtime, mode, transaction_id):
    target = Path(target)
    if original is None:
        try:
            target.unlink()
        except FileNotFoundError:
            pass
        return
    staged = _stage_bytes(target, original, transaction_id)
    os.replace(staged, target)
    if mode is not None:
        os.chmod(target, mode)
    if mtime is not None:
        os.utime(target, ns=(mtime, mtime))


def _create_recovery_marker(control, transaction_id, created_directories):
    marker_target = Path(control) / "recovery"
    marker = _stage_bytes(
        marker_target,
        _canonical_bytes(
            {
                "schema_version": 1,
                "transaction_id": transaction_id,
                "purpose": "cross-file-recovery",
            }
        ),
        transaction_id,
        created_directories,
    )
    try:
        _directory_fsync(marker.parent)
    except BaseException:
        # The marker is not installed until the first replacement.  If its
        # durability barrier fails, remove this uninstalled staging artifact
        # while preserving the original exception.  If removal itself fails,
        # the reserved name remains visible to read-only memory inspection as
        # a P0 recovery condition.
        try:
            marker.unlink()
        except OSError:
            pass
        raise
    return marker


def _clear_recovery_marker(marker):
    if marker is None:
        return
    try:
        Path(marker).unlink()
    except FileNotFoundError:
        pass
    _directory_fsync(Path(marker).parent)


def _classify_os_error(error):
    if isinstance(error, ValueError):
        return "transaction-semantic-verification-failure"
    if getattr(error, "errno", None) == errno.EXDEV:
        return "cross-device-atomic-replace-unavailable"
    if getattr(error, "winerror", None) in {32, 33}:
        return "target-sharing-violation"
    return "transaction-io-failure"


def _recovery_root_v3(root, transaction_id):
    if not isinstance(transaction_id, str) or not _V3_TRANSACTION_ID.fullmatch(
        transaction_id
    ):
        raise ValueError("initialization recovery transaction ID is invalid")
    return safe_path(
        Path(root) / STATE_DIRECTORY / "recovery" / transaction_id,
        Path(root).absolute(),
    )


def _entry_capacity_v3(entry):
    return (
        2 * 1024 * 1024
        if entry["plane"] == "document"
        else _target_capacity(entry["path"])
    )


def _live_entry_state_v3(root, entry):
    target = safe_path(Path(root) / entry["path"], root)
    if not os.path.lexists(target):
        return {
            "kind": "absent",
            "digest": _ABSENT_DIGEST,
            "bytes": 0,
            "mode": None,
            "mtime_ns": None,
        }
    third = {
        "kind": "other",
        "digest": "sha256:THIRD-STATE",
        "bytes": 0,
        "mode": None,
        "mtime_ns": None,
    }
    if not target.is_file():
        return third
    capacity = _entry_capacity_v3(entry)
    try:
        with target.open("rb") as handle:
            before = os.fstat(handle.fileno())
            if not stat.S_ISREG(before.st_mode):
                return third
            data = handle.read(capacity + 1)
            after = os.fstat(handle.fileno())
        pathname = target.stat()
    except OSError:
        return third
    stable = lambda value: (
        value.st_dev,
        value.st_ino,
        value.st_mode,
        value.st_size,
        value.st_mtime_ns,
    )
    if stable(before) != stable(after) or stable(pathname) != stable(after):
        return third
    if len(data) > capacity:
        return {
            "kind": "oversize",
            "digest": "sha256:THIRD-STATE",
            "bytes": len(data),
            "mode": None,
            "mtime_ns": None,
        }
    return {
        "kind": "file",
        "digest": _sha256(data),
        "bytes": len(data),
        "mode": stat.S_IMODE(after.st_mode),
        "mtime_ns": after.st_mtime_ns,
    }


def _classify_live_entry_v3(root, entry):
    live = _live_entry_state_v3(root, entry)
    start = entry["start"]
    result = entry["result"]
    start_match = (
        live["kind"] == start["kind"]
        and live["digest"] == start["digest"]
        and live["bytes"] == start["bytes"]
        and (
            start["kind"] == "absent"
            or (
                live["mode"] == start["mode"]
                and live["mtime_ns"] == start["mtime_ns"]
            )
        )
    )
    result_match = (
        live["kind"] == result["kind"]
        and live["digest"] == result["digest"]
        and live["bytes"] == result["bytes"]
    )
    if start_match and result_match:
        classification = (
            "result" if entry.get("status") == "installed" else "start"
        )
    elif start_match:
        classification = "start"
    elif result_match:
        classification = "result"
    else:
        classification = "third"
    return live, classification


def _reconciled_journal_v3(root, journal):
    records = []
    counts = {"documents": 0, "controls": 0, "cleanup": 1}
    event_recorded = False
    for entry in journal["entries"]:
        live, classification = _classify_live_entry_v3(root, entry)
        counts["documents" if entry["plane"] == "document" else "controls"] += 1
        records.append(
            {
                "path": entry["path"],
                "plane": entry["plane"],
                "start_digest": entry["start"]["digest"],
                "result_digest": entry["result"]["digest"],
                "live_digest": live["digest"],
                "classification": classification,
            }
        )
        if entry["role"] == "event":
            event_recorded = classification == "result"
    records.sort(key=lambda item: (item["path"].casefold(), item["path"]))
    return {
        "records": records,
        "digest": _sha256(_canonical_bytes(records)),
        "counts": counts,
        "successful_event_recorded": event_recorded,
        "third_state": any(
            record["classification"] == "third" for record in records
        ),
    }


def _write_active_journal_v3(recovery_root, journal):
    recovery_root = Path(recovery_root)
    _verify_recovery_ignore_v3(recovery_root)
    data = _canonical_bytes(journal)
    if len(data) > INIT_RECOVERY_JOURNAL_MAX_BYTES:
        raise ValueError("initialization recovery journal exceeds capacity")
    temporary = recovery_root / "journal.next"
    if os.path.lexists(temporary):
        if not temporary.is_file() or temporary.is_symlink():
            raise ValueError("initialization recovery journal staging path is unsafe")
        temporary.unlink()
    _write_flushed_file_v3(temporary, data, exclusive=True)
    _verify_exact_file_v3(temporary, data)
    _verify_recovery_ignore_v3(recovery_root)
    os.replace(temporary, recovery_root / "journal.json")
    _directory_fsync(recovery_root)
    _verify_recovery_ignore_v3(recovery_root)
    return _sha256(data)


def _terminal_entry_v3(entry):
    return {
        "index": entry["index"],
        "plane": entry["plane"],
        "operation": entry["operation"],
        "path": entry["path"],
        "role": entry["role"],
        "start": {
            field: copy.deepcopy(entry["start"][field])
            for field in ("kind", "digest", "bytes", "mode", "mtime_ns")
        },
        "result": {
            field: copy.deepcopy(entry["result"][field])
            for field in ("kind", "digest", "bytes")
        },
    }


def _terminal_model_v3(root, recovery_root, journal, journal_digest):
    root = Path(root).absolute()
    recovery_root = safe_path(recovery_root, root)
    recovery_parent = safe_path(recovery_root.parent, root)
    parent_metadata = recovery_parent.stat()
    root_metadata = recovery_root.stat()
    return {
        "schema_version": 3,
        "marker_version": INIT_RECOVERY_TERMINAL_VERSION,
        "transaction_id": journal["transaction_id"],
        "transaction_digest": journal["transaction_digest"],
        "authorization_projection": copy.deepcopy(
            journal["authorization_projection"]
        ),
        "recovery_container_preexisted": journal[
            "recovery_container_preexisted"
        ],
        "journal_digest": journal_digest,
        "recovery_parent_identity": {
            "path": recovery_parent.relative_to(root).as_posix(),
            "device": parent_metadata.st_dev,
            "inode": parent_metadata.st_ino,
        },
        "recovery_root_identity": {
            "device": root_metadata.st_dev,
            "inode": root_metadata.st_ino,
        },
        "parent_facts": copy.deepcopy(journal["parent_facts"]),
        "created_parent_identities": copy.deepcopy(
            journal["created_parent_identities"]
        ),
        "entries": [_terminal_entry_v3(entry) for entry in journal["entries"]],
        "event_commit": copy.deepcopy(journal["event_commit"]),
    }


def _write_terminal_marker_v3(root, recovery_root, journal, journal_digest):
    _verify_recovery_ignore_v3(recovery_root)
    marker = _terminal_model_v3(root, recovery_root, journal, journal_digest)
    data = _canonical_bytes(marker)
    if len(data) > INIT_RECOVERY_TERMINAL_MAX_BYTES:
        raise ValueError("initialization terminal marker exceeds capacity")
    path = safe_path(Path(recovery_root) / "terminal.json", recovery_root)
    _write_flushed_file_v3(path, data, exclusive=True)
    _verify_exact_file_v3(path, data)
    _verify_recovery_ignore_v3(recovery_root)
    _directory_fsync(recovery_root)
    _verify_recovery_ignore_v3(recovery_root)
    return marker, _sha256(data)


def _validate_terminal_structure_v3(marker, transaction_id):
    if not isinstance(marker, Mapping) or set(marker) != {
        "schema_version",
        "marker_version",
        "transaction_id",
        "transaction_digest",
        "authorization_projection",
        "recovery_container_preexisted",
        "journal_digest",
        "recovery_parent_identity",
        "recovery_root_identity",
        "parent_facts",
        "created_parent_identities",
        "entries",
        "event_commit",
    }:
        raise ValueError("initialization terminal marker fields are invalid")
    if (
        marker["schema_version"] != 3
        or marker["marker_version"] != INIT_RECOVERY_TERMINAL_VERSION
        or marker["transaction_id"] != transaction_id
        or _V3_TRANSACTION_ID.fullmatch(marker["transaction_id"]) is None
        or _SHA256.fullmatch(marker["transaction_digest"]) is None
        or _SHA256.fullmatch(marker["journal_digest"]) is None
        or type(marker["recovery_container_preexisted"]) is not bool
    ):
        raise ValueError("initialization terminal marker header is invalid")
    for name in ("recovery_parent_identity", "recovery_root_identity"):
        identity = marker[name]
        expected = (
            {"path", "device", "inode"}
            if name == "recovery_parent_identity"
            else {"device", "inode"}
        )
        if (
            not isinstance(identity, Mapping)
            or set(identity) != expected
            or type(identity["device"]) is not int
            or type(identity["inode"]) is not int
            or identity["device"] <= 0
            or identity["inode"] <= 0
        ):
            raise ValueError("initialization terminal directory identity is invalid")
    if marker["recovery_parent_identity"]["path"] != (
        f"{STATE_DIRECTORY}/recovery"
    ):
        raise ValueError("initialization terminal recovery parent is invalid")
    entries = marker["entries"]
    if not isinstance(entries, list) or not entries or len(entries) > 80:
        raise ValueError("initialization terminal entries are invalid")
    for index, entry in enumerate(entries):
        if not isinstance(entry, Mapping) or set(entry) != {
            "index",
            "plane",
            "operation",
            "path",
            "role",
            "start",
            "result",
        }:
            raise ValueError("initialization terminal entry is invalid")
        if entry["index"] != index:
            raise ValueError("initialization terminal order is invalid")
        if set(entry["start"]) != {
            "kind",
            "digest",
            "bytes",
            "mode",
            "mtime_ns",
        } or set(entry["result"]) != {"kind", "digest", "bytes"}:
            raise ValueError("initialization terminal state is invalid")
        normalize_repo_relative(entry["path"], "terminal entry path")
        for state_value in (entry["start"], entry["result"]):
            if (
                state_value["kind"] not in {"absent", "file"}
                or type(state_value["bytes"]) is not int
                or state_value["bytes"] < 0
                or (
                    state_value["digest"] != _ABSENT_DIGEST
                    and _SHA256.fullmatch(state_value["digest"]) is None
                )
            ):
                raise ValueError("initialization terminal state binding is invalid")
    event_entries = [entry for entry in entries if entry["role"] == "event"]
    if len(event_entries) != 1:
        raise ValueError("initialization terminal event is invalid")
    event = event_entries[0]
    if marker["event_commit"] != {
        "path": event["path"],
        "starting_digest": event["start"]["digest"],
        "result_digest": event["result"]["digest"],
    }:
        raise ValueError("initialization terminal event binding is invalid")
    _validate_authorization_projection_binding_v3(
        marker["authorization_projection"],
        entries,
        marker["transaction_id"],
        marker["transaction_digest"],
    )
    return copy.deepcopy(dict(marker))


def _load_terminal_marker_v3(root, recovery_root):
    root = Path(root).absolute()
    recovery_root = safe_path(recovery_root, root)
    _verify_recovery_ignore_v3(recovery_root)
    path = safe_path(recovery_root / "terminal.json", recovery_root)
    with path.open("rb") as handle:
        data = handle.read(INIT_RECOVERY_TERMINAL_MAX_BYTES + 1)
    if len(data) > INIT_RECOVERY_TERMINAL_MAX_BYTES:
        raise ValueError("initialization terminal marker exceeds capacity")
    try:
        marker = json.loads(data)
    except (UnicodeError, ValueError, RecursionError) as exc:
        raise ValueError("initialization terminal marker is malformed") from exc
    if _canonical_bytes(marker) != data:
        raise ValueError("initialization terminal marker is not canonical")
    transaction_match = _V3_TRANSACTION_ID.fullmatch(recovery_root.name)
    tombstone_match = _V3_CLEANUP_TOMBSTONE.fullmatch(recovery_root.name)
    transaction_id = (
        transaction_match.group(0)
        if transaction_match is not None
        else tombstone_match.group(1) if tombstone_match is not None else None
    )
    marker = _validate_terminal_structure_v3(marker, transaction_id)
    parent = recovery_root.parent.stat()
    current = recovery_root.stat()
    if (
        parent.st_dev != marker["recovery_parent_identity"]["device"]
        or parent.st_ino != marker["recovery_parent_identity"]["inode"]
        or current.st_dev != marker["recovery_root_identity"]["device"]
        or current.st_ino != marker["recovery_root_identity"]["inode"]
    ):
        raise ValueError("initialization terminal directory identity changed")
    journal_path = recovery_root / "journal.json"
    if os.path.lexists(journal_path):
        with journal_path.open("rb") as handle:
            journal_bytes = handle.read(INIT_RECOVERY_JOURNAL_MAX_BYTES + 1)
        if len(journal_bytes) > INIT_RECOVERY_JOURNAL_MAX_BYTES:
            raise ValueError("initialization terminal journal exceeds capacity")
        try:
            journal = json.loads(journal_bytes)
        except (UnicodeError, ValueError, RecursionError) as exc:
            raise ValueError("initialization terminal journal is malformed") from exc
        if (
            not isinstance(journal, Mapping)
            or _canonical_bytes(journal) != journal_bytes
        ):
            raise ValueError("initialization terminal journal is not canonical")
        if marker["journal_digest"] != _sha256(journal_bytes):
            raise ValueError("initialization terminal journal digest is invalid")
        try:
            matches = (
                journal["transaction_id"] == marker["transaction_id"]
                and journal["transaction_digest"] == marker["transaction_digest"]
                and journal["authorization_projection"]
                == marker["authorization_projection"]
                and journal["recovery_container_preexisted"]
                == marker["recovery_container_preexisted"]
                and journal["parent_facts"] == marker["parent_facts"]
                and journal["created_parent_identities"]
                == marker["created_parent_identities"]
                and [_terminal_entry_v3(entry) for entry in journal["entries"]]
                == marker["entries"]
                and journal["event_commit"] == marker["event_commit"]
            )
        except (KeyError, TypeError) as exc:
            raise ValueError("initialization terminal journal binding is invalid") from exc
        if not matches:
            raise ValueError("initialization terminal journal binding is invalid")
    return marker, data


def _reconciled_terminal_v3(root, recovery_root, marker):
    _revalidate_recorded_parent_facts_v3(
        root,
        marker["parent_facts"],
        recovery_root,
        control_directory_preexisted=True,
        created_directories=marker["created_parent_identities"],
    )
    journal_like = {
        "entries": [
            {**copy.deepcopy(entry), "status": "pending"}
            for entry in marker["entries"]
        ]
    }
    return _reconciled_journal_v3(root, journal_like)


def _read_live_file_v3(root, relative, maximum_bytes):
    target = safe_path(Path(root) / relative, root)
    with target.open("rb") as handle:
        data = handle.read(maximum_bytes + 1)
    if len(data) > maximum_bytes:
        raise ValueError("live initialization evidence exceeds capacity")
    return data


def _live_init_event_v3(root, transaction_id):
    events_bytes = _read_live_file_v3(
        root,
        f"{STATE_DIRECTORY}/{EVENTS_FILE}",
        MAX_EVENTS_BYTES,
    )
    try:
        events = [json.loads(line) for line in events_bytes.splitlines() if line.strip()]
    except (UnicodeError, ValueError, RecursionError) as exc:
        raise ValueError("live initialization event evidence is malformed") from exc
    if (
        not events
        or b"".join(_canonical_bytes(event) for event in events) != events_bytes
        or validate_operational_events(events)
    ):
        raise ValueError("live initialization event evidence is invalid")
    event = events[-1]
    if event.get("kind") != "init" or event.get("transaction_id") != transaction_id:
        raise ValueError("live initialization event does not match transaction")
    return event


def _validate_live_init_commit_v3(root, transaction_id):
    root = Path(root).absolute()
    event = _live_init_event_v3(root, transaction_id)
    state_bytes = _read_live_file_v3(
        root,
        f"{STATE_DIRECTORY}/{STATE_FILE}",
        MAX_STATE_BYTES,
    )
    findings_bytes = _read_live_file_v3(
        root,
        f"{STATE_DIRECTORY}/{FINDINGS_FILE}",
        MAX_FINDINGS_BYTES,
    )
    try:
        state = validate_operational_state(json.loads(state_bytes), root)
        findings = validate_operational_findings(json.loads(findings_bytes), root)
    except (UnicodeError, ValueError, RecursionError) as exc:
        raise ValueError("live initialization operational state is invalid") from exc
    if (
        _canonical_bytes(state) != state_bytes
        or _canonical_bytes(findings) != findings_bytes
        or state.get("last_completed_event") != event.get("event_id")
        or state_semantic_digest(state) != event.get("state_semantic_digest")
        or findings_digest(findings) != event.get("findings_digest")
    ):
        raise ValueError("live initialization state does not match event")
    manifest_relative = normalize_repo_relative(
        event.get("manifest", {}).get("path"),
        "live initialization manifest",
    )
    if manifest_relative != f"{STATE_DIRECTORY}/manifests/{event['event_id']}.json":
        raise ValueError("live initialization manifest path is invalid")
    manifest_bytes = _read_live_file_v3(root, manifest_relative, MAX_MANIFEST_BYTES)
    try:
        manifest = json.loads(manifest_bytes)
    except (UnicodeError, ValueError, RecursionError) as exc:
        raise ValueError("live initialization manifest is malformed") from exc
    if (
        _canonical_bytes(manifest) != manifest_bytes
        or _sha256(manifest_bytes) != event.get("manifest_digest")
        or event.get("manifest", {}).get("digest") != event.get("manifest_digest")
    ):
        raise ValueError("live initialization manifest does not match event")
    normalized_manifest = _validated_init_manifest(manifest, event)
    if (
        normalized_manifest["manifest_identity"]
        != state["initialization"]["manifest_identity"]
        or normalized_manifest["document_results_digest"]
        != state["initialization"]["document_results_digest"]
        or normalized_manifest["corpus_transition"]["result"]
        != state["initialization"]["result_corpus"]
    ):
        raise ValueError("live initialization manifest does not match state")
    result_paths = {
        os.path.normcase(item["path"])
        for item in normalized_manifest["document_results"]
    }
    for result in normalized_manifest["document_results"]:
        path = normalize_repo_relative(result["path"], "live document result")
        target = safe_path(root / path, root)
        if result["result_digest"] == _ABSENT_DIGEST:
            if os.path.lexists(target):
                raise ValueError("live deleted document result is present")
            continue
        data = _read_live_file_v3(root, path, 2 * 1024 * 1024)
        if len(data) != result["bytes"] or _sha256(data) != result["result_digest"]:
            raise ValueError("live document result does not match manifest")
    for disposition in normalized_manifest["dispositions"]:
        if disposition["disposition"] != "RETAIN":
            continue
        path = normalize_repo_relative(disposition["path"], "live retained document")
        if os.path.normcase(path) in result_paths:
            continue
        digest, _ = _stable_path_digest_v3(root, path, 2 * 1024 * 1024)
        if digest != disposition["source_digest"]:
            raise ValueError("live retained document does not match manifest")
    expected_corpus = normalized_manifest["corpus_transition"]["result"]
    observed_corpus = scan_selected_document_corpus(
        root,
        expected_corpus["selected_scope"],
        expected_corpus["coverage_mode"],
    )
    if (
        observed_corpus.get("complete") is not True
        or observed_corpus.get("content_reads") != 0
        or observed_corpus.get("corpus") != expected_corpus
    ):
        raise ValueError("live initialization result corpus does not match")
    return event


def _validate_no_live_init_commit_v3(root, transaction_id):
    root = Path(root).absolute()
    events_path = safe_path(root / STATE_DIRECTORY / EVENTS_FILE, root)
    if os.path.lexists(events_path):
        data = _read_live_file_v3(
            root,
            f"{STATE_DIRECTORY}/{EVENTS_FILE}",
            MAX_EVENTS_BYTES,
        )
        try:
            events = [json.loads(line) for line in data.splitlines() if line.strip()]
        except (UnicodeError, ValueError, RecursionError) as exc:
            raise ValueError("live cleanup event evidence is malformed") from exc
        if (
            b"".join(_canonical_bytes(event) for event in events) != data
            or validate_operational_events(events)
            or any(event.get("transaction_id") == transaction_id for event in events)
        ):
            raise ValueError("successful initialization event exists for cleanup")
    return True


def _read_recovery_body_v3(
    recovery_root,
    pointer,
    expected_bytes,
    expected_digest,
    maximum_bytes,
):
    if not isinstance(pointer, str) or re.fullmatch(
        r"(?:backups|results)/\d{4}\.bin", pointer
    ) is None:
        raise ValueError("initialization recovery body pointer is invalid")
    if (
        type(expected_bytes) is not int
        or expected_bytes < 0
        or type(maximum_bytes) is not int
        or maximum_bytes < 0
        or expected_bytes > maximum_bytes
    ):
        raise ValueError("initialization recovery body exceeds capacity")
    path = safe_path(Path(recovery_root) / pointer, recovery_root)
    with path.open("rb") as handle:
        data = handle.read(expected_bytes + 1)
    if len(data) != expected_bytes or _sha256(data) != expected_digest:
        raise ValueError("initialization recovery body does not match journal")
    return data


def _ensure_parent_directories_v3(root, parent, identity_records):
    root = Path(root).absolute()
    parent = safe_path(parent, root)
    missing = []
    current = parent
    while not os.path.lexists(current):
        missing.append(current)
        current = current.parent
    if not current.is_dir() or current.is_symlink():
        raise ValueError("transaction target parent is not a directory")
    parent.mkdir(parents=True, exist_ok=True)
    changed = False
    for directory in reversed(missing):
        directory = safe_path(directory, root)
        metadata = directory.stat()
        identity_records[directory.relative_to(root).as_posix()] = {
            "device": metadata.st_dev,
            "inode": metadata.st_ino,
        }
        changed = True
        _directory_fsync(directory.parent)
    return changed


def _remove_empty_created_directories_v3(root, identity_records):
    root = Path(root).absolute()
    complete = True
    for relative, identity in sorted(
        identity_records.items(),
        key=lambda item: len(Path(item[0]).parts),
        reverse=True,
    ):
        try:
            directory = safe_path(root / relative, root)
            if not os.path.lexists(directory):
                continue
            if not directory.is_dir() or directory.is_symlink():
                complete = False
                continue
            metadata = directory.stat()
            if (
                metadata.st_dev != identity.get("device")
                or metadata.st_ino != identity.get("inode")
            ):
                complete = False
                continue
            directory.rmdir()
            _directory_fsync(directory.parent)
        except OSError:
            complete = False
    return complete


class _V3CleanupFailure(OSError):
    def __init__(self, error, *, writes, action, recovery_root):
        super().__init__(str(error))
        self.error = error
        self.writes = writes
        self.action = action
        self.recovery_root = Path(recovery_root)
        self.winerror = getattr(error, "winerror", None)


class _V3PreparationFailure(OSError):
    def __init__(self, error, rollback):
        super().__init__(str(error))
        self.error = error
        self.rollback = rollback
        self.winerror = getattr(error, "winerror", None)


def _cleanup_mutation_v3(operation, path, *, recovery_root):
    for attempt in range(1, 4):
        try:
            confined = safe_path(path, recovery_root)
            operation(confined)
            return True
        except FileNotFoundError:
            return False
        except OSError as error:
            if getattr(error, "winerror", None) in {32, 33} and attempt < 3:
                time.sleep(0.1)
                continue
            raise
    raise AssertionError("bounded cleanup attempts exhausted")


def _cleanup_child_name_v3(name):
    if (
        not isinstance(name, str)
        or name in {"", ".", ".."}
        or Path(name).name != name
        or "/" in name
        or "\\" in name
    ):
        raise ValueError("initialization recovery cleanup child is invalid")
    return name


def _windows_cleanup_api_v3():
    import ctypes
    from ctypes import wintypes

    class FileAttributeTagInfo(ctypes.Structure):
        _fields_ = (
            ("file_attributes", wintypes.DWORD),
            ("reparse_tag", wintypes.DWORD),
        )

    class FileId128(ctypes.Structure):
        _fields_ = (("identifier", ctypes.c_ubyte * 16),)

    class FileIdInfo(ctypes.Structure):
        _fields_ = (
            ("volume_serial_number", ctypes.c_ulonglong),
            ("file_id", FileId128),
        )

    class FileDispositionInfo(ctypes.Structure):
        _fields_ = (("delete_file", wintypes.BOOL),)

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateFileW.argtypes = (
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.LPVOID,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.HANDLE,
    )
    kernel32.CreateFileW.restype = wintypes.HANDLE
    kernel32.GetFileInformationByHandleEx.argtypes = (
        wintypes.HANDLE,
        ctypes.c_int,
        wintypes.LPVOID,
        wintypes.DWORD,
    )
    kernel32.GetFileInformationByHandleEx.restype = wintypes.BOOL
    kernel32.SetFileInformationByHandle.argtypes = (
        wintypes.HANDLE,
        ctypes.c_int,
        wintypes.LPVOID,
        wintypes.DWORD,
    )
    kernel32.SetFileInformationByHandle.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
    kernel32.CloseHandle.restype = wintypes.BOOL
    return {
        "ctypes": ctypes,
        "wintypes": wintypes,
        "kernel32": kernel32,
        "attribute_type": FileAttributeTagInfo,
        "identity_type": FileIdInfo,
        "disposition_type": FileDispositionInfo,
    }


def _windows_error_v3(api, message):
    code = api["ctypes"].get_last_error()
    error = OSError(code, message)
    error.winerror = code
    return error


def _windows_open_cleanup_pin_v3(path, *, directory, share_delete):
    api = _windows_cleanup_api_v3()
    desired_access = 0x00000080 | 0x00010000  # FILE_READ_ATTRIBUTES | DELETE
    share = 0x00000001 | 0x00000002
    if share_delete:
        share |= 0x00000004
    flags = 0x00200000 | (0x02000000 if directory else 0)
    handle = api["kernel32"].CreateFileW(
        os.fspath(Path(path).absolute()),
        desired_access,
        share,
        None,
        3,
        flags,
        None,
    )
    invalid = api["wintypes"].HANDLE(-1).value
    if handle in (None, invalid):
        raise _windows_error_v3(api, "cleanup object could not be pinned")
    try:
        attributes = api["attribute_type"]()
        if not api["kernel32"].GetFileInformationByHandleEx(
            handle,
            9,
            api["ctypes"].byref(attributes),
            api["ctypes"].sizeof(attributes),
        ):
            raise _windows_error_v3(api, "cleanup object attributes are unavailable")
        is_directory = bool(attributes.file_attributes & 0x10)
        is_reparse = bool(attributes.file_attributes & 0x400)
        if is_reparse or is_directory is not directory:
            raise ValueError("initialization recovery cleanup object is unsafe")
        identity = api["identity_type"]()
        if not api["kernel32"].GetFileInformationByHandleEx(
            handle,
            18,
            api["ctypes"].byref(identity),
            api["ctypes"].sizeof(identity),
        ):
            raise _windows_error_v3(api, "cleanup object identity is unavailable")
        return {
            "platform": "windows",
            "api": api,
            "handle": handle,
            "path": Path(path).absolute(),
            "identity": (
                identity.volume_serial_number,
                bytes(identity.file_id.identifier),
            ),
            "directory": directory,
        }
    except BaseException:
        api["kernel32"].CloseHandle(handle)
        raise


def _windows_close_cleanup_pin_v3(pin):
    handle = pin.get("handle")
    if handle is not None:
        pin["api"]["kernel32"].CloseHandle(handle)
        pin["handle"] = None


def _windows_dispose_cleanup_pin_v3(pin):
    api = pin["api"]
    disposition = api["disposition_type"](True)
    if not api["kernel32"].SetFileInformationByHandle(
        pin["handle"],
        4,
        api["ctypes"].byref(disposition),
        api["ctypes"].sizeof(disposition),
    ):
        raise _windows_error_v3(api, "cleanup object could not be deleted")
    _windows_close_cleanup_pin_v3(pin)


def _posix_cleanup_supported_v3():
    required = (os.open, os.stat, os.unlink, os.rmdir, os.rename)
    return (
        hasattr(os, "O_DIRECTORY")
        and hasattr(os, "O_NOFOLLOW")
        and all(operation in os.supports_dir_fd for operation in required)
    )


def _posix_open_cleanup_pin_v3(path=None, *, parent_fd=None, name=None):
    if not _posix_cleanup_supported_v3():
        raise OSError(errno.ENOTSUP, "anchored recovery cleanup is unavailable")
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    if hasattr(os, "O_CLOEXEC"):
        flags |= os.O_CLOEXEC
    descriptor = (
        os.open(path, flags)
        if parent_fd is None
        else os.open(_cleanup_child_name_v3(name), flags, dir_fd=parent_fd)
    )
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISDIR(metadata.st_mode):
            raise ValueError("initialization recovery cleanup object is unsafe")
        return {
            "platform": "posix",
            "fd": descriptor,
            "path": Path(path).absolute() if path is not None else None,
            "identity": (metadata.st_dev, metadata.st_ino),
            "directory": True,
        }
    except BaseException:
        os.close(descriptor)
        raise


def _open_cleanup_tree_v3(expected_parent, recovery_root, action):
    expected_parent = Path(expected_parent).absolute()
    recovery_root = Path(recovery_root).absolute()
    if action not in {"cleanup", "finalize"} or recovery_root.parent != expected_parent:
        raise ValueError("initialization recovery cleanup path is invalid")
    transaction = _V3_TRANSACTION_ID.fullmatch(recovery_root.name)
    tombstone_match = _V3_CLEANUP_TOMBSTONE.fullmatch(recovery_root.name)
    if transaction is None and (
        tombstone_match is None or tombstone_match.group(2) != action
    ):
        raise ValueError("initialization recovery cleanup action is invalid")
    if _paths._is_reparse(expected_parent) or _paths._is_reparse(recovery_root):
        raise ValueError("initialization recovery cleanup path is unsafe")
    _validate_cleanup_container_v3(expected_parent, recovery_root)
    _cleanup_layout_v3(recovery_root)
    tombstone = (
        expected_parent / f"{recovery_root.name}.{action}"
        if transaction is not None
        else recovery_root
    )
    tree = {
        "platform": "windows" if os.name == "nt" else "posix",
        "path": tombstone,
        "container_parent": None,
        "parent": None,
        "root": None,
        "children": {},
        "renamed": False,
    }
    try:
        if os.name == "nt":
            # Load and exercise the required APIs before the first mutation.
            container_parent_pin = _windows_open_cleanup_pin_v3(
                expected_parent.parent,
                directory=True,
                share_delete=False,
            )
            tree["container_parent"] = container_parent_pin
            parent_pin = _windows_open_cleanup_pin_v3(
                expected_parent,
                directory=True,
                share_delete=False,
            )
            identity_pin = _windows_open_cleanup_pin_v3(
                recovery_root,
                directory=True,
                share_delete=True,
            )
            tree["parent"] = parent_pin
            if transaction is not None:
                if os.path.lexists(tombstone):
                    raise ValueError("initialization recovery cleanup tombstone already exists")
                os.replace(recovery_root, tombstone)
                tree["renamed"] = True
            original_identity = identity_pin["identity"]
            _windows_close_cleanup_pin_v3(identity_pin)
            root_pin = _windows_open_cleanup_pin_v3(
                tombstone,
                directory=True,
                share_delete=False,
            )
            tree["root"] = root_pin
            if root_pin["identity"] != original_identity:
                raise ValueError("initialization recovery root changed during rename")
            for name in ("backups", "results"):
                child = tombstone / name
                if os.path.lexists(child):
                    tree["children"][name] = _windows_open_cleanup_pin_v3(
                        child,
                        directory=True,
                        share_delete=False,
                    )
        else:
            container_parent_pin = _posix_open_cleanup_pin_v3(expected_parent.parent)
            tree["container_parent"] = container_parent_pin
            parent_pin = _posix_open_cleanup_pin_v3(
                parent_fd=container_parent_pin["fd"],
                name=expected_parent.name,
            )
            parent_pin["path"] = expected_parent
            tree["parent"] = parent_pin
            root_pin = _posix_open_cleanup_pin_v3(
                parent_fd=parent_pin["fd"],
                name=recovery_root.name,
            )
            tree["root"] = root_pin
            for name in ("backups", "results"):
                try:
                    tree["children"][name] = _posix_open_cleanup_pin_v3(
                        parent_fd=root_pin["fd"],
                        name=name,
                    )
                except FileNotFoundError:
                    pass
            if transaction is not None:
                if os.path.lexists(tombstone):
                    raise ValueError("initialization recovery cleanup tombstone already exists")
                os.rename(
                    recovery_root.name,
                    tombstone.name,
                    src_dir_fd=parent_pin["fd"],
                    dst_dir_fd=parent_pin["fd"],
                )
                tree["renamed"] = True
            root_pin["path"] = tombstone
            for name, child in tree["children"].items():
                child["path"] = tombstone / name
        return tree
    except BaseException:
        _close_cleanup_tree_v3(tree)
        raise


def _list_cleanup_entries_v3(pin, limit):
    if type(limit) is not int or limit < 0:
        raise ValueError("initialization recovery cleanup limit is invalid")
    if pin["platform"] == "posix":
        entries = list(islice(os.scandir(pin["fd"]), limit + 1))
        names = [entry.name for entry in entries]
    else:
        names = [
            path.name
            for path in islice(Path(pin["path"]).iterdir(), limit + 1)
        ]
    if len(names) > limit:
        raise ValueError("initialization recovery cleanup layout exceeds capacity")
    if len({name.casefold() for name in names}) != len(names):
        raise ValueError("initialization recovery cleanup identity is ambiguous")
    return sorted(names, key=lambda value: (value.casefold(), value))


def _lstat_cleanup_child_v3(pin, name):
    name = _cleanup_child_name_v3(name)
    if pin["platform"] == "posix":
        return os.stat(name, dir_fd=pin["fd"], follow_symlinks=False)
    path = Path(pin["path"]) / name
    metadata = os.lstat(path)
    if _paths._is_reparse(path):
        raise ValueError("initialization recovery cleanup child is a reparse point")
    return metadata


def _unlink_cleanup_child_v3(pin, name):
    name = _cleanup_child_name_v3(name)
    metadata = _lstat_cleanup_child_v3(pin, name)
    if not stat.S_ISREG(metadata.st_mode):
        raise ValueError("initialization recovery cleanup child is not a file")
    if pin["platform"] == "posix":
        os.unlink(name, dir_fd=pin["fd"])
        return
    file_pin = _windows_open_cleanup_pin_v3(
        Path(pin["path"]) / name,
        directory=False,
        share_delete=False,
    )
    try:
        _windows_dispose_cleanup_pin_v3(file_pin)
    finally:
        _windows_close_cleanup_pin_v3(file_pin)


def _remove_pinned_directory_v3(parent_pin, name, child_pin):
    name = _cleanup_child_name_v3(name)
    if parent_pin["platform"] == "posix":
        current = os.stat(name, dir_fd=parent_pin["fd"], follow_symlinks=False)
        pinned = os.fstat(child_pin["fd"])
        if (
            not stat.S_ISDIR(current.st_mode)
            or (current.st_dev, current.st_ino) != (pinned.st_dev, pinned.st_ino)
        ):
            raise ValueError("initialization recovery cleanup directory changed")
        os.rmdir(name, dir_fd=parent_pin["fd"])
        os.close(child_pin["fd"])
        child_pin["fd"] = None
        return
    _windows_dispose_cleanup_pin_v3(child_pin)


def _close_cleanup_tree_v3(tree):
    if not isinstance(tree, Mapping):
        return
    pins = [
        *tree.get("children", {}).values(),
        tree.get("root"),
        tree.get("parent"),
        tree.get("container_parent"),
    ]
    for pin in pins:
        if not isinstance(pin, Mapping):
            continue
        if pin.get("platform") == "windows":
            _windows_close_cleanup_pin_v3(pin)
        else:
            descriptor = pin.get("fd")
            if descriptor is not None:
                try:
                    os.close(descriptor)
                except OSError:
                    pass
                pin["fd"] = None


def _cleanup_layout_v3(recovery_root):
    recovery_root = Path(recovery_root)
    files = []
    directories = []
    children = list(islice(recovery_root.iterdir(), 7))
    if len(children) > 6:
        raise ValueError("initialization recovery cleanup layout exceeds capacity")
    guard = next(
        (child for child in children if child.name == INIT_RECOVERY_IGNORE_NAME),
        None,
    )
    other_children = [child for child in children if child is not guard]
    if guard is None:
        if other_children:
            raise ValueError("initialization recovery ignore guard is missing")
    else:
        _verify_recovery_ignore_v3(
            recovery_root,
            allow_partial=not other_children,
        )
    for discovered_child in children:
        child = safe_path(discovered_child, recovery_root)
        if child.name == INIT_RECOVERY_IGNORE_NAME:
            files.append(child)
            continue
        if child.name in {"backups", "results"}:
            if not child.is_dir():
                raise ValueError("initialization recovery cleanup layout is invalid")
            capacity = (
                INIT_RECOVERY_BACKUP_MAX_FILES
                if child.name == "backups"
                else INIT_RECOVERY_RESULT_MAX_FILES
            ) + 2
            nested = list(islice(child.iterdir(), capacity + 1))
            if len(nested) > capacity:
                raise ValueError("initialization recovery cleanup layout exceeds capacity")
            for discovered_item in nested:
                item = safe_path(discovered_item, recovery_root)
                if (
                    not item.is_file()
                    or re.fullmatch(r"\d{4}\.(?:bin|install|restore)", item.name)
                    is None
                ):
                    raise ValueError("initialization recovery cleanup body is invalid")
                files.append(item)
            directories.append(child)
            continue
        if (
            child.name not in {"journal.json", "journal.next", "terminal.json"}
            or not child.is_file()
        ):
            raise ValueError("initialization recovery cleanup artifact is invalid")
        files.append(child)
    files.sort(
        key=lambda path: (
            path.name == INIT_RECOVERY_IGNORE_NAME,
            path.name == "terminal.json",
            path.name == "journal.json",
            path.relative_to(recovery_root).as_posix(),
        )
    )
    directories.sort(key=lambda path: path.name)
    return files, directories


def _validate_preparation_abort_layout_v3(recovery_root, plan):
    """Accept only the bounded subset that preparation itself could have created."""
    recovery_root = Path(recovery_root)
    expected_bodies = set(plan["recovery_files"])
    children = list(islice(recovery_root.iterdir(), 5))
    if len(children) > 4:
        raise ValueError("initialization preparation abort layout exceeds capacity")
    names = [child.name for child in children]
    if len({name.casefold() for name in names}) != len(names):
        raise ValueError("initialization preparation abort layout is ambiguous")
    other_names = [name for name in names if name != INIT_RECOVERY_IGNORE_NAME]
    if INIT_RECOVERY_IGNORE_NAME not in names:
        if other_names:
            raise ValueError("initialization recovery ignore guard is missing")
    else:
        _verify_recovery_ignore_v3(
            recovery_root,
            allow_partial=not other_names,
        )
    for child in children:
        child = safe_path(child, recovery_root)
        if child.name == INIT_RECOVERY_IGNORE_NAME:
            continue
        if child.name in {"journal.json", "journal.next"}:
            if not child.is_file() or child.is_symlink() or _paths._is_reparse(child):
                raise ValueError("initialization preparation journal is unsafe")
            continue
        if child.name not in {"backups", "results"}:
            raise ValueError("initialization preparation abort artifact is invalid")
        if not child.is_dir() or child.is_symlink() or _paths._is_reparse(child):
            raise ValueError("initialization preparation abort directory is unsafe")
        expected_names = {
            Path(relative).name
            for relative in expected_bodies
            if Path(relative).parent.as_posix() == child.name
        }
        nested = list(islice(child.iterdir(), len(expected_names) + 1))
        if len(nested) > len(expected_names):
            raise ValueError("initialization preparation abort body set exceeds capacity")
        for item in nested:
            item = safe_path(item, recovery_root)
            if (
                item.name not in expected_names
                or not item.is_file()
                or item.is_symlink()
                or _paths._is_reparse(item)
            ):
                raise ValueError("initialization preparation abort body is unsafe")


def _validate_cleanup_container_v3(expected_parent, recovery_root):
    found = False
    for index, discovered in enumerate(expected_parent.iterdir(), start=1):
        if index > 1:
            raise ValueError("initialization recovery cleanup container is ambiguous")
        sibling = safe_path(discovered, expected_parent)
        if sibling.name != recovery_root.name:
            if sibling.name.casefold() == recovery_root.name.casefold():
                raise ValueError(
                    "initialization recovery cleanup path has a case-insensitive collision"
                )
            raise ValueError("initialization recovery cleanup container is ambiguous")
        found = True
    if not found:
        raise ValueError("initialization recovery cleanup path is unavailable")


def _cleanup_call_v3(operation):
    for attempt in range(1, 4):
        try:
            operation()
            return True
        except FileNotFoundError:
            return False
        except OSError as error:
            if getattr(error, "winerror", None) in {32, 33} and attempt < 3:
                time.sleep(0.1)
                continue
            raise
    raise AssertionError("bounded cleanup attempts exhausted")


def _validate_terminal_action_v3(root, recovery_root, marker, action):
    reconciliation = _reconciled_terminal_v3(root, recovery_root, marker)
    expected = "result" if action == "finalize" else "start"
    if reconciliation["third_state"] or any(
        record["classification"] != expected
        for record in reconciliation["records"]
    ):
        raise ValueError("initialization terminal live state does not match action")
    if reconciliation["successful_event_recorded"] is not (action == "finalize"):
        raise ValueError("initialization terminal event state does not match action")
    if action == "finalize":
        _validate_live_init_commit_v3(root, marker["transaction_id"])
    else:
        _validate_no_live_init_commit_v3(root, marker["transaction_id"])
    return reconciliation


def _cleanup_recovery_area_v3(
    root,
    recovery_root,
    *,
    action="cleanup",
    preparation_abort=None,
):
    root = Path(root).absolute()
    recovery_root = safe_path(recovery_root, root)
    expected_parent = safe_path(root / STATE_DIRECTORY / "recovery", root)
    if action not in {"cleanup", "finalize"} or recovery_root.parent != expected_parent:
        raise ValueError("initialization recovery cleanup path is invalid")
    if not os.path.lexists(recovery_root):
        return 0
    if not recovery_root.is_dir() or recovery_root.is_symlink():
        raise ValueError("initialization recovery cleanup path is unsafe")
    transaction_match = _V3_TRANSACTION_ID.fullmatch(recovery_root.name)
    tombstone_match = _V3_CLEANUP_TOMBSTONE.fullmatch(recovery_root.name)
    if transaction_match is None and tombstone_match is None:
        raise ValueError("initialization recovery cleanup path is invalid")
    if tombstone_match is not None and tombstone_match.group(2) != action:
        raise ValueError("initialization recovery cleanup action is invalid")
    _validate_cleanup_container_v3(expected_parent, recovery_root)
    _cleanup_layout_v3(recovery_root)
    marker = None
    recovery_container_preexisted = False
    preparation_plan = None
    preparation_journal = None
    marker_path = recovery_root / "terminal.json"
    if preparation_abort is not None:
        if (
            action != "cleanup"
            or not isinstance(preparation_abort, Mapping)
            or set(preparation_abort) != {"plan", "journal"}
        ):
            raise ValueError("initialization preparation abort input is invalid")
        preparation_plan = preparation_abort["plan"]
        preparation_journal = preparation_abort["journal"]
        _validate_plan_authorization(root, preparation_plan)
        if (
            not isinstance(preparation_journal, Mapping)
            or preparation_journal.get("transaction_id") != recovery_root.name
            or preparation_journal.get("phase") not in {"preparing", "prepared"}
        ):
            raise ValueError("initialization preparation abort journal is invalid")
        _validate_preparation_abort_layout_v3(recovery_root, preparation_plan)
        recovery_container_preexisted = preparation_journal[
            "recovery_container_preexisted"
        ]
    elif os.path.lexists(marker_path):
        marker, _ = _load_terminal_marker_v3(root, recovery_root)
        _validate_terminal_action_v3(root, recovery_root, marker, action)
        recovery_container_preexisted = marker[
            "recovery_container_preexisted"
        ]
    elif os.path.lexists(recovery_root / "journal.json"):
        journal, _ = _load_journal_v3(root, recovery_root)
        recovery_container_preexisted = journal[
            "recovery_container_preexisted"
        ]
        reconciliation = _reconciled_journal_v3(root, journal)
        if (
            action != "cleanup"
            or reconciliation["third_state"]
            or reconciliation["successful_event_recorded"]
            or any(
                record["classification"] != "start"
                for record in reconciliation["records"]
            )
        ):
            raise ValueError("initialization journal live state does not permit cleanup")
        _validate_no_live_init_commit_v3(root, journal["transaction_id"])
    elif tombstone_match is not None:
        _markerless_tombstone_reconciliation_v3(
            root,
            recovery_root,
            tombstone_match.group(1),
            action,
        )
        # Once a tombstone has lost its journal/terminal, the original parent
        # provenance is unknowable. Preserve the recovery container rather than
        # risk deleting a directory that predated the transaction.
        recovery_container_preexisted = True
    elif action == "finalize":
        raise ValueError("initialization finalize evidence is unavailable")
    else:
        recovery_container_preexisted = True
    writes = 0
    tree = None
    try:
        tree = _open_cleanup_tree_v3(expected_parent, recovery_root, action)
        if tree["renamed"]:
            writes += 1
        recovery_root = tree["path"]
        _cleanup_layout_v3(recovery_root)
        opened_root_names = _list_cleanup_entries_v3(tree["root"], 6)
        if opened_root_names:
            _verify_recovery_ignore_v3(
                recovery_root,
                allow_partial=opened_root_names == [INIT_RECOVERY_IGNORE_NAME],
                cleanup_pin=tree["root"],
            )

        for name, pin in sorted(tree["children"].items()):
            capacity = (
                INIT_RECOVERY_BACKUP_MAX_FILES
                if name == "backups"
                else INIT_RECOVERY_RESULT_MAX_FILES
            ) + 2
            for child_name in _list_cleanup_entries_v3(pin, capacity):
                if re.fullmatch(
                    r"\d{4}\.(?:bin|install|restore)", child_name
                ) is None:
                    raise ValueError("initialization recovery cleanup body is invalid")
                if (
                    preparation_plan is not None
                    and f"{name}/{child_name}"
                    not in preparation_plan["recovery_files"]
                ):
                    raise ValueError("initialization preparation abort body is invalid")
                _verify_recovery_ignore_v3(
                    recovery_root,
                    cleanup_pin=tree["root"],
                )
                if _cleanup_call_v3(
                    lambda pin=pin, child_name=child_name: _unlink_cleanup_child_v3(
                        pin,
                        child_name,
                    )
                ):
                    writes += 1

        root_names = _list_cleanup_entries_v3(tree["root"], 6)
        allowed_root = (
            {
                INIT_RECOVERY_IGNORE_NAME,
                "backups",
                "results",
                "journal.json",
                "journal.next",
            }
            if preparation_plan is not None
            else {
                INIT_RECOVERY_IGNORE_NAME,
                "backups",
                "results",
                "journal.json",
                "journal.next",
                "terminal.json",
            }
        )
        if any(name not in allowed_root for name in root_names):
            raise ValueError("initialization recovery cleanup artifact is invalid")
        for name in ("journal.next", "journal.json"):
            if name in root_names:
                _verify_recovery_ignore_v3(
                    recovery_root,
                    cleanup_pin=tree["root"],
                )
                if _cleanup_call_v3(
                    lambda name=name: _unlink_cleanup_child_v3(tree["root"], name)
                ):
                    writes += 1

        if marker is not None:
            _verify_recovery_ignore_v3(
                recovery_root,
                cleanup_pin=tree["root"],
            )
            marker, _ = _load_terminal_marker_v3(root, recovery_root)
            _validate_terminal_action_v3(root, recovery_root, marker, action)
            if _cleanup_call_v3(
                lambda: _unlink_cleanup_child_v3(tree["root"], "terminal.json")
            ):
                writes += 1
        elif preparation_plan is None:
            transaction_id = (
                transaction_match.group(0)
                if transaction_match is not None
                else tombstone_match.group(1)
            )
            _markerless_tombstone_reconciliation_v3(
                root,
                recovery_root,
                transaction_id,
                action,
            )

        for name in ("backups", "results"):
            pin = tree["children"].get(name)
            if pin is not None:
                _verify_recovery_ignore_v3(
                    recovery_root,
                    cleanup_pin=tree["root"],
                )
                if _cleanup_call_v3(
                    lambda name=name, pin=pin: _remove_pinned_directory_v3(
                        tree["root"],
                        name,
                        pin,
                    )
                ):
                    writes += 1

        remaining = _list_cleanup_entries_v3(tree["root"], 1)
        if any(name != INIT_RECOVERY_IGNORE_NAME for name in remaining):
            raise ValueError("initialization recovery cleanup artifact remains")
        if INIT_RECOVERY_IGNORE_NAME in remaining:
            _verify_recovery_ignore_v3(
                recovery_root,
                allow_partial=len(root_names) == 1,
                cleanup_pin=tree["root"],
            )
            if _cleanup_call_v3(
                lambda: _unlink_cleanup_child_v3(
                    tree["root"],
                    INIT_RECOVERY_IGNORE_NAME,
                )
            ):
                writes += 1
        if _list_cleanup_entries_v3(tree["root"], 0):
            raise ValueError("initialization recovery cleanup root is not empty")

        if _cleanup_call_v3(
            lambda: _remove_pinned_directory_v3(
                tree["parent"],
                recovery_root.name,
                tree["root"],
            )
        ):
            writes += 1
        if not recovery_container_preexisted:
            if _cleanup_call_v3(
                lambda: _remove_pinned_directory_v3(
                    tree["container_parent"],
                    expected_parent.name,
                    tree["parent"],
                )
            ):
                writes += 1
        return writes
    except (OSError, ValueError) as error:
        if tree is not None and tree.get("renamed") and writes == 0:
            writes = 1
        raise _V3CleanupFailure(
            error,
            writes=writes,
            action=action,
            recovery_root=recovery_root,
        ) from error
    finally:
        _close_cleanup_tree_v3(tree)


def _abort_failed_preparation_v3(
    root,
    recovery_root,
    plan,
    runtime_journal,
    preparation_parent_identities,
):
    base = {
        "writes": 0,
        "outcomes": {
            "documents": "not-required",
            "controls": "not-required",
            "cleanup": "incomplete",
        },
    }

    preparing_model = plan["journal_models"]["preparing"]

    def parents_restored():
        for path, preexisted in (
            (
                safe_path(Path(root) / STATE_DIRECTORY, root),
                preparing_model["control_directory_preexisted"],
            ),
            (
                safe_path(Path(root) / STATE_DIRECTORY / "recovery", root),
                preparing_model["recovery_container_preexisted"],
            ),
        ):
            exists = os.path.lexists(path)
            if preexisted:
                if (
                    not exists
                    or not path.is_dir()
                    or path.is_symlink()
                    or _paths._is_reparse(path)
                ):
                    return False
            elif exists:
                return False
        return True

    if not isinstance(preparation_parent_identities, Mapping):
        return {
            **base,
            "complete": False,
            "classification": "transaction-semantic-verification-failure",
            "boundary": "preparation-abort",
        }
    if not os.path.lexists(recovery_root):
        parents_complete = _remove_empty_created_directories_v3(
            root,
            preparation_parent_identities,
        )
        parents_complete = parents_complete and parents_restored()
        return {
            **base,
            "complete": parents_complete,
            "classification": "transaction-io-failure",
            "boundary": "preparation-abort",
            "outcomes": {
                "documents": "not-required",
                "controls": "not-required",
                "cleanup": "complete" if parents_complete else "incomplete",
            },
        }
    if runtime_journal is None:
        return {
            **base,
            "complete": False,
            "classification": "transaction-io-failure",
            "boundary": "preparation-abort",
        }
    try:
        writes = _cleanup_recovery_area_v3(
            root,
            recovery_root,
            action="cleanup",
            preparation_abort={"plan": plan, "journal": runtime_journal},
        )
        parents_complete = _remove_empty_created_directories_v3(
            root,
            preparation_parent_identities,
        )
        complete = (
            parents_complete
            and parents_restored()
            and not os.path.lexists(recovery_root)
        )
        return {
            "complete": complete,
            "writes": writes,
            "outcomes": {
                "documents": "not-required",
                "controls": "not-required",
                "cleanup": "complete" if complete else "incomplete",
            },
        }
    except (OSError, ValueError) as error:
        underlying = getattr(error, "error", error)
        return {
            **base,
            "writes": getattr(error, "writes", 0),
            "complete": False,
            "classification": _classify_os_error(underlying),
            "boundary": "preparation-abort",
        }


class _V3InstallFailure(Exception):
    def __init__(self, error, boundary, third_state=False):
        super().__init__(str(error))
        self.error = error
        self.boundary = boundary
        self.third_state = third_state


def _install_entry_once_v3(
    root,
    plan,
    recovery_root,
    entry,
    created_directories,
    parent_facts,
    control_directory_preexisted,
    journal,
):
    _verify_recovery_ignore_v3(recovery_root)
    target = safe_path(Path(root) / entry["path"], root)
    parents_created = _ensure_parent_directories_v3(
        root,
        target.parent,
        created_directories,
    )
    if parents_created:
        _write_active_journal_v3(recovery_root, journal)
    if entry["result"]["kind"] == "absent":
        _revalidate_recorded_parent_facts_v3(
            root,
            parent_facts,
            recovery_root,
            control_directory_preexisted=control_directory_preexisted,
            created_directories=created_directories,
        )
        _, classification = _classify_live_entry_v3(root, entry)
        if classification != "start":
            raise ValueError("target changed at install mutation boundary")
        _verify_recovery_ignore_v3(recovery_root)
        os.unlink(target)
    else:
        result = entry["result"]
        data = _read_recovery_body_v3(
            recovery_root,
            result["staged"],
            result["bytes"],
            result["digest"],
            _entry_capacity_v3(entry),
        )
        temporary = Path(recovery_root) / "results" / f"{entry['index']:04d}.install"
        if os.path.lexists(temporary):
            if not temporary.is_file() or temporary.is_symlink():
                raise ValueError("transaction install staging path is unsafe")
            temporary.unlink()
        _write_flushed_file_v3(temporary, data, exclusive=True)
        _verify_exact_file_v3(temporary, data)
        _revalidate_recorded_parent_facts_v3(
            root,
            parent_facts,
            recovery_root,
            control_directory_preexisted=control_directory_preexisted,
            created_directories=created_directories,
        )
        if entry["role"] == "event":
            _verify_pre_event_v3(root, plan, journal)
        _, classification = _classify_live_entry_v3(root, entry)
        if classification != "start":
            raise ValueError("target changed at install mutation boundary")
        _verify_recovery_ignore_v3(recovery_root)
        os.replace(temporary, target)
    _directory_fsync(target.parent)


def _install_entry_v3(
    root,
    plan,
    recovery_root,
    entry,
    created_directories,
    parent_facts,
    control_directory_preexisted,
    journal,
):
    boundary = f"install:{entry['path']}"
    for attempt in range(1, 4):
        _verify_recovery_ignore_v3(recovery_root)
        _revalidate_recorded_parent_facts_v3(
            root,
            parent_facts,
            recovery_root,
            control_directory_preexisted=control_directory_preexisted,
            created_directories=created_directories,
        )
        _, before = _classify_live_entry_v3(root, entry)
        if before != "start":
            raise _V3InstallFailure(
                ValueError("target changed immediately before install"),
                f"compare:{entry['path']}",
                third_state=before == "third",
            )
        try:
            _install_entry_once_v3(
                root,
                plan,
                recovery_root,
                entry,
                created_directories,
                parent_facts,
                control_directory_preexisted,
                journal,
            )
        except BaseException as error:
            _, after = _classify_live_entry_v3(root, entry)
            if after == "result":
                return
            retryable = (
                isinstance(error, OSError)
                and getattr(error, "winerror", None) in {32, 33}
                and after == "start"
                and attempt < 3
            )
            if retryable:
                time.sleep(0.1)
                continue
            if isinstance(error, KeyboardInterrupt):
                raise
            raise _V3InstallFailure(
                error,
                boundary,
                third_state=after == "third",
            ) from error
        _, after = _classify_live_entry_v3(root, entry)
        if after != "result":
            raise _V3InstallFailure(
                OSError("installed transaction bytes differ"),
                f"verify:{entry['path']}",
                third_state=after == "third",
            )
        return
    raise AssertionError("bounded transaction attempts exhausted")


def _verify_retained_source_probes_v3(root, plan):
    total = 0
    for probe in plan.get("retained_source_probes", ()):
        actual, byte_count = _stable_path_digest_v3(
            root,
            probe["path"],
            2 * 1024 * 1024,
        )
        total += byte_count
        if total > INIT_RETAINED_PROBE_MAX_BYTES:
            raise ValueError("retained source corpus exceeds capacity")
        if actual != probe["digest"]:
            raise ValueError("retained source changed before event commit")


def _verify_pre_event_v3(root, plan, journal):
    event_entries = [entry for entry in journal["entries"] if entry["role"] == "event"]
    if len(event_entries) != 1 or journal["entries"][-1] != event_entries[0]:
        raise ValueError("initialization event is not the final operation")
    event_entry = event_entries[0]
    for entry in journal["entries"]:
        _, classification = _classify_live_entry_v3(root, entry)
        expected = "start" if entry is event_entry else "result"
        if classification != expected:
            raise ValueError("pre-event transaction verification failed")
    if any(
        entry["status"] != ("pending" if entry is event_entry else "installed")
        for entry in journal["entries"]
    ):
        raise ValueError("pre-event journal status does not match installed results")
    if plan.get("corpus_transition") != plan["event"].get("corpus_transition"):
        raise ValueError("pre-event result corpus binding does not match")
    expected_corpus = plan["corpus_transition"]["result"]
    observed_corpus = scan_selected_document_corpus(
        root,
        expected_corpus["selected_scope"],
        expected_corpus["coverage_mode"],
    )
    if (
        observed_corpus.get("complete") is not True
        or observed_corpus.get("content_reads") != 0
        or observed_corpus.get("corpus") != expected_corpus
    ):
        raise ValueError("pre-event result corpus does not match installed documents")
    if LOCAL_MAP_PATH in plan["targets"] and _git_ignore_status(root) != "ignored":
        raise ValueError("installed local map is not ignored")
    _verify_retained_source_probes_v3(root, plan)


def _rollback_recovery_v3(root, recovery_root, journal=None):
    root = Path(root).absolute()
    recovery_root = safe_path(recovery_root, root)
    base = {
        "writes": 0,
        "outcomes": {
            "documents": "not-run",
            "controls": "not-run",
            "cleanup": "incomplete",
        },
    }
    try:
        if journal is None:
            journal, _ = _load_journal_v3(root, recovery_root)
        _verify_recovery_ignore_v3(recovery_root)
        _revalidate_recorded_parent_facts_v3(
            root,
            journal["parent_facts"],
            recovery_root,
            control_directory_preexisted=journal[
                "control_directory_preexisted"
            ],
            created_directories=journal["created_parent_identities"],
        )
    except (OSError, ValueError) as error:
        return {
            **base,
            "complete": False,
            "classification": _classify_os_error(error),
            "boundary": "rollback-parent-preflight",
        }
    reconciliation = _reconciled_journal_v3(root, journal)
    if reconciliation["third_state"]:
        return {
            **base,
            "complete": False,
            "classification": "recovery-third-state",
            "boundary": "rollback-preflight",
        }
    created_directories = journal["created_parent_identities"]
    writes = 0
    touched = {"documents": False, "controls": False}
    try:
        for entry in reversed(journal["entries"]):
            _revalidate_recorded_parent_facts_v3(
                root,
                journal["parent_facts"],
                recovery_root,
                control_directory_preexisted=journal[
                    "control_directory_preexisted"
                ],
                created_directories=created_directories,
            )
            _, classification = _classify_live_entry_v3(root, entry)
            if classification == "start":
                continue
            if classification != "result":
                raise ValueError("rollback target entered a third state")
            target = safe_path(root / entry["path"], root)
            if entry["start"]["kind"] == "absent":
                _revalidate_recorded_parent_facts_v3(
                    root,
                    journal["parent_facts"],
                    recovery_root,
                    control_directory_preexisted=journal[
                        "control_directory_preexisted"
                    ],
                    created_directories=created_directories,
                )
                _, current = _classify_live_entry_v3(root, entry)
                if current != "result":
                    raise ValueError("rollback target changed at mutation boundary")
                _verify_recovery_ignore_v3(recovery_root)
                os.unlink(target)
            else:
                start = entry["start"]
                _verify_recovery_ignore_v3(recovery_root)
                data = _read_recovery_body_v3(
                    recovery_root,
                    start["backup"],
                    start["bytes"],
                    start["digest"],
                    _entry_capacity_v3(entry),
                )
                temporary = (
                    recovery_root / "backups" / f"{entry['index']:04d}.restore"
                )
                if os.path.lexists(temporary):
                    temporary.unlink()
                _write_flushed_file_v3(temporary, data, exclusive=True)
                _verify_exact_file_v3(temporary, data)
                _revalidate_recorded_parent_facts_v3(
                    root,
                    journal["parent_facts"],
                    recovery_root,
                    control_directory_preexisted=journal[
                        "control_directory_preexisted"
                    ],
                    created_directories=created_directories,
                )
                _, current = _classify_live_entry_v3(root, entry)
                if current != "result":
                    raise ValueError("rollback target changed at mutation boundary")
                _verify_recovery_ignore_v3(recovery_root)
                os.replace(temporary, target)
            writes += 1
            touched[
                "documents" if entry["plane"] == "document" else "controls"
            ] = True
            if entry["start"]["kind"] == "file":
                os.chmod(target, start["mode"])
                os.utime(target, ns=(start["mtime_ns"], start["mtime_ns"]))
            _directory_fsync(target.parent)
            _, restored = _classify_live_entry_v3(root, entry)
            if restored != "start":
                raise OSError("rollback did not restore exact start state")
        _revalidate_recorded_parent_facts_v3(
            root,
            journal["parent_facts"],
            recovery_root,
            control_directory_preexisted=journal[
                "control_directory_preexisted"
            ],
            created_directories=created_directories,
        )
        writes += _cleanup_recovery_area_v3(
            root,
            recovery_root,
            action="cleanup",
        )
        cleanup_complete = _remove_empty_created_directories_v3(
            root,
            created_directories,
        )
        return {
            "complete": cleanup_complete,
            "writes": writes,
            "outcomes": {
                "documents": "complete" if touched["documents"] else "not-required",
                "controls": "complete" if touched["controls"] else "not-required",
                "cleanup": "complete" if cleanup_complete else "incomplete",
            },
        }
    except (OSError, ValueError) as error:
        writes += getattr(error, "writes", 0)
        underlying = getattr(error, "error", error)
        return {
            "complete": False,
            "writes": writes,
            "classification": _classify_os_error(underlying),
            "boundary": "rollback",
            "outcomes": {
                "documents": "incomplete" if touched["documents"] else "not-run",
                "controls": "incomplete" if touched["controls"] else "not-run",
                "cleanup": "incomplete",
            },
        }


def _apply_verified_closeout_v3(
    root,
    plan,
    *,
    approved_transaction,
    verification,
    protected_preview=None,
    protected_verification=None,
    documentation_rollback=None,
):
    root = Path(root).absolute()
    if not isinstance(plan, Mapping) or plan.get("status") != "approval-required":
        raise ValueError("closeout plan is not applicable")
    txid = plan.get("transaction_id")
    if approved_transaction != txid:
        raise ValueError("approved transaction does not match closeout plan")
    try:
        _validate_plan_authorization(root, plan)
    except (AttributeError, KeyError, TypeError, ValueError, RecursionError, OverflowError):
        return {
            "status": "closeout-failed",
            "classification": "transaction-authorization-mismatch",
            "boundary": "transaction-authorization",
            "control_plane_rolled_back": True,
            "successful_event_recorded": False,
        }
    if not callable(verification):
        raise ValueError("verification callback is required")
    if verification() is not True:
        return {
            "status": "verification-failed",
            "successful_event_recorded": False,
        }
    effective_protected = (
        protected_preview
        if protected_preview is not None
        else plan.get("protected_preview")
    )
    expected_protected_digest = plan["event"].get("protected_preview_digest")
    actual_protected_digest = (
        _sha256(_canonical_bytes(effective_protected))
        if effective_protected is not None
        else None
    )
    if actual_protected_digest != expected_protected_digest:
        raise ValueError("protected surface preview does not match approved transaction")
    if effective_protected is not None and (
        not validate_protected_disposition_preview(effective_protected)
        or effective_protected["status"] != "allowed-preview"
        or not callable(protected_verification)
        or protected_verification() is not True
    ):
        if callable(documentation_rollback):
            documentation_rollback()
        return {
            "status": "protected-verification-failed",
            "successful_event_recorded": False,
        }

    recovery_root = _recovery_root_v3(root, txid)
    journal = None
    created_directories = {}
    try:
        prepared_recovery = _prepare_recovery_area_v3(root, plan)
        journal = prepared_recovery["journal"]
        created_directories = journal["created_parent_identities"]
        journal["phase"] = "installing"
        _write_active_journal_v3(recovery_root, journal)
        event_entry = next(
            entry for entry in journal["entries"] if entry["role"] == "event"
        )
        for entry in journal["entries"]:
            if entry is event_entry:
                break
            _install_entry_v3(
                root,
                plan,
                recovery_root,
                entry,
                created_directories,
                journal["parent_facts"],
                journal["control_directory_preexisted"],
                journal,
            )
            entry["status"] = "installed"
            _write_active_journal_v3(recovery_root, journal)
        _verify_pre_event_v3(root, plan, journal)
        journal["phase"] = "verified"
        final_journal_digest = _write_active_journal_v3(recovery_root, journal)
        _write_terminal_marker_v3(
            root,
            recovery_root,
            journal,
            final_journal_digest,
        )
        _load_terminal_marker_v3(root, recovery_root)
        _install_entry_v3(
            root,
            plan,
            recovery_root,
            event_entry,
            created_directories,
            journal["parent_facts"],
            journal["control_directory_preexisted"],
            journal,
        )
        reconciliation = _reconciled_journal_v3(root, journal)
        if not reconciliation["successful_event_recorded"]:
            raise _V3InstallFailure(
                OSError("successful event is not installed"),
                "event-commit",
                third_state=True,
            )
    except KeyboardInterrupt:
        raise
    except (_V3InstallFailure, OSError, ValueError) as failure:
        error = (
            failure.error
            if isinstance(failure, (_V3InstallFailure, _V3PreparationFailure))
            else failure
        )
        boundary = (
            failure.boundary
            if isinstance(failure, _V3InstallFailure)
            else "transaction-preparation"
        )
        if isinstance(failure, _V3PreparationFailure):
            return {
                "status": "closeout-failed",
                "classification": _classify_os_error(error),
                "boundary": boundary,
                "control_plane_rolled_back": False,
                "rollback": failure.rollback,
                "successful_event_recorded": False,
            }
        if journal is None:
            clean = not os.path.lexists(recovery_root)
            return {
                "status": "closeout-failed",
                "classification": _classify_os_error(error),
                "boundary": boundary,
                "control_plane_rolled_back": clean,
                **(
                    {}
                    if clean
                    else {
                        "rollback": {
                            "complete": False,
                            "writes": 0,
                            "classification": "state-conflict",
                            "boundary": "preparation-abort",
                            "outcomes": {
                                "documents": "not-required",
                                "controls": "not-required",
                                "cleanup": "incomplete",
                            },
                        }
                    }
                ),
                "successful_event_recorded": False,
            }
        reconciliation = _reconciled_journal_v3(root, journal)
        if reconciliation["successful_event_recorded"]:
            final_journal_digest = _sha256(
                _canonical_bytes(journal)
            )
            return {
                "status": "closeout-committed-cleanup-incomplete",
                "transaction_id": txid,
                "event_id": plan["event"]["event_id"],
                "journal_digest": final_journal_digest,
                "reconciled_state_digest": reconciliation["digest"],
                "successful_event_recorded": True,
            }
        rollback = _rollback_recovery_v3(root, recovery_root, journal)
        return {
            "status": "closeout-failed",
            "classification": _classify_os_error(error),
            "boundary": boundary,
            "control_plane_rolled_back": rollback["complete"],
            "rollback": rollback,
            "successful_event_recorded": False,
        }

    try:
        _cleanup_recovery_area_v3(root, recovery_root, action="finalize")
    except (OSError, ValueError):
        return {
            "status": "closeout-committed-cleanup-incomplete",
            "transaction_id": txid,
            "event_id": plan["event"]["event_id"],
            "journal_digest": final_journal_digest,
            "reconciled_state_digest": reconciliation["digest"],
            "successful_event_recorded": True,
        }
    return {
        "status": "applied",
        "transaction_id": txid,
        "event_id": plan["event"]["event_id"],
        "successful_event_recorded": True,
    }


def apply_verified_closeout(
    root,
    plan,
    *,
    approved_transaction,
    verification,
    protected_preview=None,
    protected_verification=None,
    documentation_rollback=None,
):
    if (
        isinstance(plan, Mapping)
        and plan.get("command") == "init"
        and plan.get("transaction_schema_version") == 3
        and plan.get("transaction_policy_version") == "init-closeout-v3"
    ):
        return _apply_verified_closeout_v3(
            root,
            plan,
            approved_transaction=approved_transaction,
            verification=verification,
            protected_preview=protected_preview,
            protected_verification=protected_verification,
            documentation_rollback=documentation_rollback,
        )
    return _apply_verified_closeout_legacy(
        root,
        plan,
        approved_transaction=approved_transaction,
        verification=verification,
        protected_preview=protected_preview,
        protected_verification=protected_verification,
        documentation_rollback=documentation_rollback,
    )


def _apply_verified_closeout_legacy(
    root,
    plan,
    *,
    approved_transaction,
    verification,
    protected_preview=None,
    protected_verification=None,
    documentation_rollback=None,
):
    """Verify, stage, replace deterministically, and install the event last."""
    root = Path(root).absolute()
    if not isinstance(plan, Mapping) or plan.get("status") != "approval-required":
        raise ValueError("closeout plan is not applicable")
    txid = plan.get("transaction_id")
    if approved_transaction != txid:
        raise ValueError("approved transaction does not match closeout plan")
    try:
        _validate_plan_authorization(root, plan)
    except (AttributeError, KeyError, TypeError, ValueError, RecursionError, OverflowError):
        return {
            "status": "closeout-failed",
            "classification": "transaction-authorization-mismatch",
            "control_plane_rolled_back": True,
            "successful_event_recorded": False,
            "documentation_changes_preserved": True,
        }
    if not callable(verification):
        raise ValueError("verification callback is required")
    if verification() is not True:
        return {
            "status": "verification-failed",
            "successful_event_recorded": False,
            "documentation_changes_preserved": True,
        }
    effective_protected = protected_preview if protected_preview is not None else plan.get("protected_preview")
    expected_protected_digest = plan["event"].get("protected_preview_digest")
    actual_protected_digest = (
        _sha256(_canonical_bytes(effective_protected))
        if effective_protected is not None
        else None
    )
    if actual_protected_digest != expected_protected_digest:
        raise ValueError("protected surface preview does not match approved transaction")
    if effective_protected is not None:
        if (
            not validate_protected_disposition_preview(effective_protected)
            or effective_protected["status"] != "allowed-preview"
        ):
            raise ValueError("protected surface authorization is missing")
        if not callable(protected_verification) or protected_verification() is not True:
            rolled_back = False
            if callable(documentation_rollback):
                documentation_rollback()
                rolled_back = True
            return {
                "status": "protected-verification-failed",
                "successful_event_recorded": False,
                "documentation_rolled_back": rolled_back,
            }

    starting = dict(plan["starting_digests"])
    for relative, expected in starting.items():
        actual = _path_digest(
            safe_path(root / relative, root),
            _target_capacity(relative),
        )
        if actual != expected:
            return {
                "status": "stale-target",
                "path": relative,
                "successful_event_recorded": False,
            }

    order = list(plan["replacement_order"])
    originals = {}
    staged = {}
    replaced = []
    created_directories = set()
    recovery_marker = None
    active_boundary = "transaction-start"
    try:
        for relative in order:
            target = safe_path(root / relative, root)
            original = _read_bounded_target(target, _target_capacity(relative))
            target_stat = target.stat() if original is not None else None
            mtime = target_stat.st_mtime_ns if target_stat is not None else None
            mode = stat.S_IMODE(target_stat.st_mode) if target_stat is not None else None
            originals[relative] = (original, mtime, mode)
            active_boundary = f"stage:{relative}"
            staged_path = _stage_bytes(
                target,
                plan["targets"][relative],
                txid,
                created_directories,
            )
            staged[relative] = staged_path
            active_boundary = f"verify:{relative}"
            _verify_staged(relative, plan["targets"][relative], staged_path, root)
        active_boundary = "verify:transaction-set"
        _verify_transaction_set(root, plan, staged)
        for relative, expected in starting.items():
            active_boundary = f"compare-before-replace:{relative}"
            if _path_digest(
                safe_path(root / relative, root),
                _target_capacity(relative),
            ) != expected:
                raise FileExistsError("target changed after staging")
        active_boundary = "stage:transaction-recovery-marker"
        recovery_marker = _create_recovery_marker(
            safe_path(root / STATE_DIRECTORY, root),
            txid,
            created_directories,
        )
        for relative in order:
            target = safe_path(root / relative, root)
            active_boundary = f"replace:{relative}"
            try:
                os.replace(staged[relative], target)
            except BaseException:
                try:
                    installed = _read_bounded_target(
                        target,
                        _target_capacity(relative),
                    )
                except OSError:
                    installed = None
                if installed == plan["targets"][relative] and relative not in replaced:
                    staged.pop(relative, None)
                    replaced.append(relative)
                raise
            staged.pop(relative)
            replaced.append(relative)
            active_boundary = f"fsync:{relative}"
            _directory_fsync(target.parent)
        active_boundary = "verify:installed-transaction"
        for relative in order:
            if _read_bounded_target(
                safe_path(root / relative, root), _target_capacity(relative)
            ) != plan["targets"][relative]:
                raise OSError("installed transaction bytes differ")
        if LOCAL_MAP_PATH in plan["targets"] and _git_ignore_status(root) != "ignored":
            raise ValueError("installed local map is not ignored")
        active_boundary = "clear:transaction-recovery-marker"
        _clear_recovery_marker(recovery_marker)
        recovery_marker = None
    except KeyboardInterrupt:
        for relative in reversed(replaced):
            original, mtime, mode = originals[relative]
            _restore_target(
                safe_path(root / relative, root), original, mtime, mode, txid
            )
        for path in staged.values():
            try:
                Path(path).unlink()
            except OSError:
                pass
        _clear_recovery_marker(recovery_marker)
        recovery_marker = None
        _remove_empty_created_directories(created_directories)
        raise
    except (OSError, ValueError) as error:
        rollback_ok = True
        for relative in reversed(replaced):
            original, mtime, mode = originals[relative]
            try:
                _restore_target(
                    safe_path(root / relative, root), original, mtime, mode, txid
                )
            except OSError:
                rollback_ok = False
        for path in staged.values():
            try:
                Path(path).unlink()
            except OSError:
                rollback_ok = False
        if rollback_ok:
            try:
                _clear_recovery_marker(recovery_marker)
                recovery_marker = None
            except OSError:
                rollback_ok = False
        _remove_empty_created_directories(created_directories)
        return {
            "status": "closeout-failed",
            "classification": _classify_os_error(error),
            "boundary": active_boundary,
            "control_plane_rolled_back": rollback_ok,
            "successful_event_recorded": False,
            "documentation_changes_preserved": True,
        }
    return {
        "status": "applied",
        "transaction_id": txid,
        "event_id": plan["event"]["event_id"],
        "successful_event_recorded": True,
    }


def validate_protected_intent_change(
    root,
    protected_intent,
    contradictions,
    *,
    exact_intent_change_authorizations=(),
):
    """Load maintained intent sources and block evidenced contradictions by default."""
    root = Path(root).absolute()
    if not isinstance(protected_intent, Sequence) or isinstance(
        protected_intent, (str, bytes, bytearray)
    ):
        raise ValueError("protected intent routes must be an array")
    intents = {record["id"]: record for record in protected_intent}
    if len(intents) != len(protected_intent) or len(intents) > MAX_PROTECTED_INTENTS:
        raise ValueError("protected intent routes are invalid")
    authorized = set(exact_intent_change_authorizations)
    findings = []
    verified = []
    remaining = MAX_PROTECTED_INTENT_TOTAL_BYTES
    for intent in intents.values():
        source, _, anchor = intent["source"].partition("#")
        relative = normalize_repo_relative(source, "protected intent source")
        path = safe_path(root / relative, root)
        capacity = min(INTENT_SOURCE_MAX_BYTES, MAX_PROTECTED_INTENT_BYTES, remaining)
        if capacity <= 0:
            raise ValueError("protected intent content budget exceeded")
        with path.open("rb") as handle:
            data = handle.read(capacity + 1)
        if len(data) > capacity:
            raise ValueError("protected intent source exceeds capacity")
        remaining -= len(data)
        text = data.decode("utf-8")
        if slug(anchor) not in _markdown_heading_anchors(text):
            evidence = [{"path": f"{relative}#{anchor}", "intent_key": intent["intent_key"]}]
            fingerprint = finding_fingerprint("protected-intent-missing", evidence)
            findings.append(
                {
                    "id": finding_id(fingerprint, {}),
                    "fingerprint": fingerprint,
                    "kind": "protected-intent-missing",
                    "priority": "P0",
                    "path": f"{relative}#{anchor}",
                    "detail": "maintained protected-intent anchor is unavailable",
                }
            )
        else:
            verified.append(intent["id"])
    conflicting = []
    for contradiction in contradictions:
        if not isinstance(contradiction, Mapping) or set(contradiction) != {"intent_id", "effect"}:
            raise ValueError("protected intent contradiction is invalid")
        identifier = contradiction["intent_id"]
        if identifier not in intents or contradiction["effect"] != "contradicts":
            raise ValueError("protected intent contradiction is invalid")
        if identifier not in authorized:
            conflicting.append(identifier)
            intent = intents[identifier]
            evidence = [{"path": intent["source"], "intent_key": intent["intent_key"]}]
            fingerprint = finding_fingerprint("protected-intent-conflict", evidence)
            findings.append(
                {
                    "id": finding_id(fingerprint, {}),
                    "fingerprint": fingerprint,
                    "kind": "protected-intent-conflict",
                    "priority": "P0",
                    "path": intent["source"],
                    "detail": "proposed change contradicts confirmed protected intent",
                }
            )
    return {
        "status": "blocked" if findings else "authorized-intent-change",
        "writes": 0,
        "verified_intents": sorted(verified),
        "conflicting_intents": sorted(conflicting),
        "findings": findings,
    }


def _load_journal_v3(root, recovery_root):
    root = Path(root).absolute()
    recovery_root = safe_path(recovery_root, root)
    _verify_recovery_ignore_v3(recovery_root)
    journal_path = safe_path(recovery_root / "journal.json", root)
    with journal_path.open("rb") as handle:
        data = handle.read(INIT_RECOVERY_JOURNAL_MAX_BYTES + 1)
    if len(data) > INIT_RECOVERY_JOURNAL_MAX_BYTES:
        raise ValueError("initialization recovery journal exceeds capacity")
    try:
        journal = json.loads(data)
    except (UnicodeError, ValueError, RecursionError) as exc:
        raise ValueError("initialization recovery journal is malformed") from exc
    if not isinstance(journal, Mapping) or _canonical_bytes(journal) != data:
        raise ValueError("initialization recovery journal is not canonical")
    if set(journal) != {
        "schema_version",
        "journal_version",
        "transaction_id",
        "transaction_digest",
        "authorization_projection",
        "phase",
        "control_directory_preexisted",
        "recovery_container_preexisted",
        "created_parent_identities",
        "parent_facts",
        "entries",
        "event_commit",
    }:
        raise ValueError("initialization recovery journal fields are invalid")
    transaction_match = _V3_TRANSACTION_ID.fullmatch(recovery_root.name)
    tombstone_match = _V3_CLEANUP_TOMBSTONE.fullmatch(recovery_root.name)
    recovery_transaction_id = (
        transaction_match.group(0)
        if transaction_match is not None
        else tombstone_match.group(1) if tombstone_match is not None else None
    )
    if (
        journal["schema_version"] != 3
        or journal["journal_version"] != "init-recovery-v1"
        or journal["transaction_id"] != recovery_transaction_id
        or _V3_TRANSACTION_ID.fullmatch(journal["transaction_id"]) is None
        or not isinstance(journal["transaction_digest"], str)
        or _SHA256.fullmatch(journal["transaction_digest"]) is None
        or journal["phase"] not in {"preparing", "prepared", "installing", "verified"}
        or type(journal["control_directory_preexisted"]) is not bool
        or type(journal["recovery_container_preexisted"]) is not bool
    ):
        raise ValueError("initialization recovery journal header is invalid")
    facts = journal["parent_facts"]
    if not isinstance(facts, list) or len(facts) > 512:
        raise ValueError("initialization recovery parent facts are invalid")
    fact_paths = set()
    for fact in facts:
        if not isinstance(fact, Mapping) or set(fact) != {
            "path",
            "starting_kind",
            "device",
            "inode",
        }:
            raise ValueError("initialization recovery parent fact is invalid")
        path = normalize_repo_relative(fact["path"], "recovery parent fact")
        if path != fact["path"] or path.casefold() in fact_paths:
            raise ValueError("initialization recovery parent facts are not canonical")
        fact_paths.add(path.casefold())
        if fact["starting_kind"] == "directory":
            if (
                type(fact["device"]) is not int
                or type(fact["inode"]) is not int
                or fact["device"] <= 0
                or fact["inode"] <= 0
            ):
                raise ValueError("initialization recovery parent identity is invalid")
        elif fact["starting_kind"] == "absent":
            if fact["device"] is not None or fact["inode"] is not None:
                raise ValueError("initialization absent parent identity is invalid")
        else:
            raise ValueError("initialization recovery parent kind is invalid")
    created_parent_identities = journal["created_parent_identities"]
    if (
        not isinstance(created_parent_identities, Mapping)
        or len(created_parent_identities) > len(facts)
    ):
        raise ValueError("initialization created parent identities are invalid")
    absent_fact_paths = {
        fact["path"] for fact in facts if fact["starting_kind"] == "absent"
    }
    for path, identity in created_parent_identities.items():
        normalized = normalize_repo_relative(path, "created recovery parent")
        if (
            path != normalized
            or path not in absent_fact_paths
            or not isinstance(identity, Mapping)
            or set(identity) != {"device", "inode"}
            or type(identity["device"]) is not int
            or type(identity["inode"]) is not int
            or identity["device"] <= 0
            or identity["inode"] <= 0
        ):
            raise ValueError("initialization created parent identity is invalid")
    if (
        journal["control_directory_preexisted"] is False
        and STATE_DIRECTORY not in created_parent_identities
    ):
        raise ValueError("initialization control parent identity is missing")
    entries = journal["entries"]
    if not isinstance(entries, list) or not entries or len(entries) > 80:
        raise ValueError("initialization recovery journal entries are invalid")
    identities = set()
    event_entries = []
    document_entries = []
    control_entries = []
    body_reads = []
    backup_files = 0
    backup_bytes = 0
    result_files = 0
    document_result_bytes = 0
    for index, entry in enumerate(entries):
        if not isinstance(entry, Mapping) or set(entry) != {
            "index",
            "plane",
            "operation",
            "path",
            "role",
            "start",
            "result",
            "status",
        }:
            raise ValueError("initialization recovery journal entry is invalid")
        path = normalize_repo_relative(entry["path"], "recovery journal path")
        if (
            entry["index"] != index
            or path != entry["path"]
            or path.casefold() in identities
            or entry["plane"] not in {"document", "control"}
            or entry["operation"]
            not in {"CREATE", "REPLACE", "DELETE", "CONTROL_REPLACE"}
            or entry["status"] not in {"pending", "installed"}
        ):
            raise ValueError("initialization recovery journal entry is not canonical")
        identities.add(path.casefold())
        safe_path(root / path, root)
        if entry["plane"] == "document":
            if (
                entry["operation"] == "CONTROL_REPLACE"
                or entry["role"]
                not in {"document-result", "recovery-archive", "document-source"}
            ):
                raise ValueError("initialization document journal entry is invalid")
            document_entries.append(entry)
        else:
            expected_control_role = {
                f"{STATE_DIRECTORY}/{STATE_FILE}": "state",
                f"{STATE_DIRECTORY}/{FINDINGS_FILE}": "findings",
                f"{STATE_DIRECTORY}/{EVENTS_FILE}": "event",
                LOCAL_MAP_PATH: "local-map",
                ".gitignore": "gitignore",
                "AGENTS.md": "agents",
            }.get(path)
            if path.startswith(f"{STATE_DIRECTORY}/manifests/") and path.endswith(
                ".json"
            ):
                expected_control_role = "manifest"
            if (
                entry["operation"] != "CONTROL_REPLACE"
                or entry["role"] != expected_control_role
            ):
                raise ValueError("initialization control journal entry is invalid")
            control_entries.append(entry)
        entry_capacity = _entry_capacity_v3(entry)
        for side in ("start", "result"):
            value = entry[side]
            expected_fields = (
                {"kind", "digest", "bytes", "mode", "mtime_ns", "backup"}
                if side == "start"
                else {"kind", "digest", "bytes", "staged"}
            )
            if not isinstance(value, Mapping) or set(value) != expected_fields:
                raise ValueError("initialization recovery state fields are invalid")
            if (
                type(value["bytes"]) is not int
                or value["bytes"] < 0
                or value["bytes"] > entry_capacity
            ):
                raise ValueError("initialization recovery state byte count is invalid")
            if value["kind"] == "absent":
                if (
                    value["digest"] != _ABSENT_DIGEST
                    or value["bytes"] != 0
                    or any(
                        value[field] is not None
                        for field in expected_fields - {"kind", "digest", "bytes"}
                    )
                ):
                    raise ValueError("initialization absent recovery state is invalid")
            elif value["kind"] == "file":
                pointer = value["backup"] if side == "start" else value["staged"]
                prefix = "backups" if side == "start" else "results"
                if (
                    not isinstance(value["digest"], str)
                    or _SHA256.fullmatch(value["digest"]) is None
                    or not isinstance(pointer, str)
                    or pointer != f"{prefix}/{index:04d}.bin"
                ):
                    raise ValueError("initialization file recovery state is invalid")
                if side == "start" and (
                    type(value["mode"]) is not int
                    or type(value["mtime_ns"]) is not int
                ):
                    raise ValueError("initialization start metadata is invalid")
                body_reads.append(
                    (pointer, value["bytes"], value["digest"], entry_capacity)
                )
                if side == "start":
                    backup_files += 1
                    backup_bytes += value["bytes"]
                else:
                    result_files += 1
                    if entry["plane"] == "document":
                        document_result_bytes += value["bytes"]
            else:
                raise ValueError("initialization recovery state kind is invalid")
        state_kinds = (entry["start"]["kind"], entry["result"]["kind"])
        if entry["plane"] == "document":
            expected = {
                "CREATE": (
                    {"document-result", "recovery-archive"},
                    ("absent", "file"),
                ),
                "REPLACE": (
                    {"document-result", "document-source"},
                    ("file", "file"),
                ),
                "DELETE": ({"document-source"}, ("file", "absent")),
            }.get(entry["operation"])
            if (
                expected is None
                or entry["role"] not in expected[0]
                or state_kinds != expected[1]
            ):
                raise ValueError("initialization document journal binding is invalid")
        elif state_kinds not in {("absent", "file"), ("file", "file")}:
            raise ValueError("initialization control journal binding is invalid")
        if entry["role"] == "event":
            event_entries.append(entry)
    if (
        backup_files > INIT_RECOVERY_BACKUP_MAX_FILES
        or backup_bytes > INIT_RECOVERY_BACKUP_MAX_BYTES
        or result_files > INIT_RECOVERY_RESULT_MAX_FILES
        or document_result_bytes > INIT_RECOVERY_DOCUMENT_RESULT_MAX_BYTES
    ):
        raise ValueError("initialization recovery files exceed capacity")
    if len(event_entries) != 1:
        raise ValueError("initialization recovery journal event is invalid")
    control_roles = [entry["role"] for entry in control_entries]
    if (
        any(control_roles.count(role) != 1 for role in {"manifest", "state", "findings", "event"})
        or any(
            control_roles.count(role) > 1
            for role in {"local-map", "gitignore", "agents"}
        )
    ):
        raise ValueError("initialization recovery control set is invalid")
    document_probes = []
    probe_result = b"journal-contract-probe"
    for entry in document_entries:
        destructive = entry["operation"] != "CREATE"
        probe = {
            "operation": entry["operation"],
            "path": entry["path"],
            "role": entry["role"],
            "starting_digest": (
                entry["start"]["digest"] if destructive else _ABSENT_DIGEST
            ),
            "result_digest": (
                _ABSENT_DIGEST
                if entry["operation"] == "DELETE"
                else _sha256(probe_result)
            ),
            "source_item_ids": [],
            "recovery_binding": "sha256:" + "0" * 64 if destructive else None,
        }
        if entry["operation"] in {"CREATE", "REPLACE"}:
            probe["result_bytes"] = probe_result
        document_probes.append(probe)
    _normalize_transaction_operations_v3(document_probes, (), ".")
    normalized_documents = sorted(
        document_entries,
        key=lambda entry: (entry["path"].casefold(), entry["path"]),
    )
    expected_order = _replacement_order_v3(
        normalized_documents,
        _replacement_order(entry["path"] for entry in control_entries),
    )
    if [entry["path"] for entry in entries] != expected_order:
        raise ValueError("initialization recovery journal order is invalid")
    event = event_entries[0]
    if journal["event_commit"] != {
        "path": event["path"],
        "starting_digest": event["start"]["digest"],
        "result_digest": event["result"]["digest"],
    }:
        raise ValueError("initialization recovery event commit is invalid")
    _validate_authorization_projection_binding_v3(
        journal["authorization_projection"],
        entries,
        journal["transaction_id"],
        journal["transaction_digest"],
    )
    recovered_bodies = {}
    for pointer, expected_bytes, expected_digest, maximum_bytes in body_reads:
        recovered_bodies[pointer] = _read_recovery_body_v3(
            recovery_root,
            pointer,
            expected_bytes,
            expected_digest,
            maximum_bytes,
        )
    event_bytes = recovered_bodies[event["result"]["staged"]]
    try:
        staged_events = [
            json.loads(line)
            for line in event_bytes.splitlines()
            if line.strip()
        ]
    except (UnicodeError, ValueError, RecursionError) as exc:
        raise ValueError("initialization recovery event body is malformed") from exc
    if (
        not staged_events
        or b"".join(_canonical_bytes(item) for item in staged_events) != event_bytes
        or validate_operational_events(staged_events)
    ):
        raise ValueError("initialization recovery event body is invalid")
    init_event = staged_events[-1]
    if (
        init_event.get("kind") != "init"
        or init_event.get("transaction_id") != journal["transaction_id"]
    ):
        raise ValueError("initialization recovery event binding is invalid")
    manifest = next(entry for entry in control_entries if entry["role"] == "manifest")
    expected_manifest_path = (
        f"{STATE_DIRECTORY}/manifests/{init_event['event_id']}.json"
    )
    if manifest["path"] != expected_manifest_path:
        raise ValueError("initialization recovery manifest path is invalid")
    event_order = [
        "manifest" if path == manifest["path"] else path
        for path in expected_order
    ]
    if init_event.get("replacement_order") != event_order:
        raise ValueError("initialization recovery event order is invalid")
    _validate_recovered_authorization_v3(
        root,
        journal,
        recovered_bodies,
        staged_events,
    )
    return copy.deepcopy(dict(journal)), data


def _doctor_outcomes_v3(value="not-run"):
    return {"documents": value, "controls": value, "cleanup": value}


def _doctor_conflict_v3(
    *,
    transaction_id,
    journal_digest,
    reconciliation,
    classification,
    boundary,
):
    return {
        "schema_version": 3,
        "mode": "state-conflict-recovery",
        "status": "state-conflict",
        "classification": classification,
        "boundary": boundary,
        "action": "none",
        "transaction_id": transaction_id,
        "journal_digest": journal_digest,
        "reconciled_state_digest": reconciliation["digest"],
        "counts": reconciliation["counts"],
        "outcomes": _doctor_outcomes_v3(),
        "writes": 0,
        "successful_event_recorded": reconciliation[
            "successful_event_recorded"
        ],
    }


def _find_recovery_root_v3(root):
    root = Path(root).absolute()
    container = safe_path(root / STATE_DIRECTORY / "recovery", root)
    if not os.path.lexists(container) or not container.is_dir() or container.is_symlink():
        raise ValueError("initialization recovery container is unavailable")
    children = list(islice(container.iterdir(), 3))
    if not children:
        raise ValueError("initialization recovery container is unavailable")
    if len(children) >= 3:
        raise ValueError("initialization recovery container is ambiguous")
    validated = []
    seen = set()
    for discovered in children:
        recovery = safe_path(discovered, root)
        identity = recovery.name.casefold()
        if identity in seen:
            raise ValueError("initialization recovery path identity is ambiguous")
        seen.add(identity)
        active = _V3_TRANSACTION_ID.fullmatch(recovery.name)
        terminal = _V3_CLEANUP_TOMBSTONE.fullmatch(recovery.name)
        if (
            not recovery.is_dir()
            or recovery.is_symlink()
            or _paths._is_reparse(recovery)
            or (active is None and terminal is None)
        ):
            raise ValueError("initialization recovery transaction path is unsafe")
        validated.append(recovery)
    if len(validated) != 1:
        raise ValueError("initialization recovery container is ambiguous")
    return validated[0]


def _markerless_tombstone_reconciliation_v3(root, recovery, transaction_id, action):
    files, directories = _cleanup_layout_v3(recovery)
    evidence_files = [
        path for path in files if path.name != INIT_RECOVERY_IGNORE_NAME
    ]
    journal_path = recovery / "journal.json"
    journal_digest = _ABSENT_DIGEST
    if os.path.lexists(journal_path):
        if action != "cleanup":
            raise ValueError("terminal marker is required for committed cleanup")
        journal, journal_bytes = _load_journal_v3(root, recovery)
        if journal["transaction_id"] != transaction_id:
            raise ValueError("recovery journal transaction does not match tombstone")
        _revalidate_recorded_parent_facts_v3(
            root,
            journal["parent_facts"],
            recovery,
            control_directory_preexisted=journal[
                "control_directory_preexisted"
            ],
            created_directories=journal["created_parent_identities"],
        )
        reconciliation = _reconciled_journal_v3(root, journal)
        if (
            reconciliation["third_state"]
            or reconciliation["successful_event_recorded"]
            or any(
                record["classification"] != "start"
                for record in reconciliation["records"]
            )
        ):
            raise ValueError("recovery journal live state does not permit cleanup")
        _validate_no_live_init_commit_v3(root, transaction_id)
        reconciliation["journal_digest"] = _sha256(journal_bytes)
        return reconciliation
    if action == "finalize":
        if evidence_files:
            raise ValueError("terminal marker is missing while recovery evidence remains")
        for directory in directories:
            if any(islice(directory.iterdir(), 1)):
                raise ValueError("terminal marker is missing while recovery bodies remain")
    if action == "finalize":
        event = _validate_live_init_commit_v3(root, transaction_id)
        record = {
            "transaction_id": transaction_id,
            "event_id": event["event_id"],
            "classification": "result",
        }
        committed = True
    else:
        _validate_no_live_init_commit_v3(root, transaction_id)
        record = {
            "transaction_id": transaction_id,
            "event_id": None,
            "classification": "start",
        }
        committed = False
    return {
        "records": [record],
        "digest": _sha256(_canonical_bytes([record])),
        "counts": {"documents": 0, "controls": 0, "cleanup": 1},
        "successful_event_recorded": committed,
        "third_state": False,
        "journal_digest": journal_digest,
    }


def _preview_journal_recovery_v3(root):
    root = Path(root).absolute()
    try:
        recovery = _find_recovery_root_v3(root)
    except (OSError, ValueError):
        empty = {
            "records": [],
            "digest": _sha256(_canonical_bytes([])),
            "counts": {"documents": 0, "controls": 0, "cleanup": 0},
            "successful_event_recorded": False,
        }
        return _doctor_conflict_v3(
            transaction_id=None,
            journal_digest=_ABSENT_DIGEST,
            reconciliation=empty,
            classification="orphan-recovery-container",
            boundary="recovery-discovery",
        )
    transaction_id = (
        recovery.name if _V3_TRANSACTION_ID.fullmatch(recovery.name) else None
    )
    tombstone = _V3_CLEANUP_TOMBSTONE.fullmatch(recovery.name)
    if tombstone is not None:
        transaction_id = tombstone.group(1)
    try:
        layout_files, _layout_directories = _cleanup_layout_v3(recovery)
    except (OSError, ValueError):
        reconciliation = {
            "records": [],
            "digest": _sha256(_canonical_bytes([])),
            "counts": {"documents": 0, "controls": 0, "cleanup": 1},
            "successful_event_recorded": False,
            "third_state": True,
        }
        return _doctor_conflict_v3(
            transaction_id=transaction_id,
            journal_digest=_ABSENT_DIGEST,
            reconciliation=reconciliation,
            classification="invalid-recovery-layout",
            boundary="recovery-layout",
        )
    if tombstone is not None:
        action = tombstone.group(2)
        try:
            terminal_path = recovery / "terminal.json"
            if os.path.lexists(terminal_path):
                marker, marker_bytes = _load_terminal_marker_v3(root, recovery)
                if marker["transaction_id"] != transaction_id:
                    raise ValueError("terminal transaction does not match tombstone")
                reconciliation = _validate_terminal_action_v3(
                    root,
                    recovery,
                    marker,
                    action,
                )
                terminal_digest = _sha256(marker_bytes)
                reconciliation["digest"] = _sha256(
                    _canonical_bytes(
                        {
                            "records": reconciliation["records"],
                            "terminal_digest": terminal_digest,
                        }
                    )
                )
                journal_digest = (
                    marker["journal_digest"]
                    if os.path.lexists(recovery / "journal.json")
                    else terminal_digest
                )
            else:
                reconciliation = _markerless_tombstone_reconciliation_v3(
                    root,
                    recovery,
                    transaction_id,
                    action,
                )
                journal_digest = reconciliation.get(
                    "journal_digest",
                    _ABSENT_DIGEST,
                )
        except (OSError, ValueError, KeyError, TypeError, RecursionError):
            reconciliation = {
                "records": [],
                "digest": _sha256(_canonical_bytes([])),
                "counts": {"documents": 0, "controls": 0, "cleanup": 1},
                "successful_event_recorded": False,
                "third_state": True,
            }
            return _doctor_conflict_v3(
                transaction_id=transaction_id,
                journal_digest=_ABSENT_DIGEST,
                reconciliation=reconciliation,
                classification="invalid-recovery-terminal",
                boundary="recovery-terminal",
            )
        journal_token = journal_digest.removeprefix("sha256:")
        state_token = reconciliation["digest"].removeprefix("sha256:")
        approval = (
            f"Approve $docs doctor recovery {transaction_id} with journal "
            f"{journal_token} state {state_token} action {action}"
        )
        return {
            "schema_version": 3,
            "mode": "state-conflict-recovery",
            "status": "approval-required",
            "action": action,
            "transaction_id": transaction_id,
            "journal_digest": journal_digest,
            "reconciled_state_digest": reconciliation["digest"],
            "counts": reconciliation["counts"],
            "outcomes": _doctor_outcomes_v3(),
            "writes": 0,
            "approval": approval,
            "successful_event_recorded": reconciliation[
                "successful_event_recorded"
            ],
        }
    journal_path = recovery / "journal.json"
    if not os.path.lexists(journal_path):
        reconciliation = {
            "records": [],
            "digest": _sha256(_canonical_bytes([])),
            "counts": {"documents": 0, "controls": 0, "cleanup": 1},
            "successful_event_recorded": False,
            "third_state": False,
        }
        if transaction_id is None:
            return _doctor_conflict_v3(
                transaction_id=None,
                journal_digest=_ABSENT_DIGEST,
                reconciliation=reconciliation,
                classification="malformed-recovery-transaction",
                boundary="recovery-bootstrap",
            )
        bootstrap_files = {
            path.relative_to(recovery).as_posix() for path in layout_files
        }
        if not bootstrap_files.issubset(
            {INIT_RECOVERY_IGNORE_NAME, "journal.next"}
        ) or any(any(islice(directory.iterdir(), 1)) for directory in _layout_directories):
            reconciliation["third_state"] = True
            return _doctor_conflict_v3(
                transaction_id=transaction_id,
                journal_digest=_ABSENT_DIGEST,
                reconciliation=reconciliation,
                classification="invalid-recovery-bootstrap",
                boundary="recovery-bootstrap",
            )
        action = "cleanup"
        journal_digest = _ABSENT_DIGEST
    else:
        try:
            journal, journal_bytes = _load_journal_v3(root, recovery)
            if os.path.lexists(recovery / "terminal.json"):
                _load_terminal_marker_v3(root, recovery)
            _revalidate_recorded_parent_facts_v3(
                root,
                journal["parent_facts"],
                recovery,
                control_directory_preexisted=journal[
                    "control_directory_preexisted"
                ],
                created_directories=journal["created_parent_identities"],
            )
            reconciliation = _reconciled_journal_v3(root, journal)
        except (OSError, ValueError, KeyError, TypeError):
            reconciliation = {
                "records": [],
                "digest": _sha256(_canonical_bytes([])),
                "counts": {"documents": 0, "controls": 0, "cleanup": 1},
                "successful_event_recorded": False,
                "third_state": True,
            }
            return _doctor_conflict_v3(
                transaction_id=transaction_id,
                journal_digest=_ABSENT_DIGEST,
                reconciliation=reconciliation,
                classification="invalid-recovery-journal",
                boundary="recovery-journal",
            )
        journal_digest = _sha256(journal_bytes)
        if reconciliation["third_state"]:
            return _doctor_conflict_v3(
                transaction_id=transaction_id,
                journal_digest=journal_digest,
                reconciliation=reconciliation,
                classification="recovery-third-state",
                boundary="recovery-reconciliation",
            )
        classifications = {
            record["path"]: record["classification"]
            for record in reconciliation["records"]
        }
        event_classification = classifications[journal["event_commit"]["path"]]
        if event_classification == "result":
            if any(value != "result" for value in classifications.values()):
                return _doctor_conflict_v3(
                    transaction_id=transaction_id,
                    journal_digest=journal_digest,
                    reconciliation=reconciliation,
                    classification="committed-recovery-mismatch",
                    boundary="recovery-reconciliation",
                )
            action = "finalize"
        elif event_classification != "start":
            return _doctor_conflict_v3(
                transaction_id=transaction_id,
                journal_digest=journal_digest,
                reconciliation=reconciliation,
                classification="event-commit-state-conflict",
                boundary="recovery-reconciliation",
            )
        elif journal["phase"] == "preparing":
            if any(value != "start" for value in classifications.values()):
                return _doctor_conflict_v3(
                    transaction_id=transaction_id,
                    journal_digest=journal_digest,
                    reconciliation=reconciliation,
                    classification="preparing-recovery-mismatch",
                    boundary="recovery-reconciliation",
                )
            action = "cleanup"
        else:
            action = "rollback"
    journal_token = journal_digest.removeprefix("sha256:")
    state_token = reconciliation["digest"].removeprefix("sha256:")
    approval = (
        f"Approve $docs doctor recovery {transaction_id} with journal "
        f"{journal_token} state {state_token} action {action}"
    )
    return {
        "schema_version": 3,
        "mode": "state-conflict-recovery",
        "status": "approval-required",
        "action": action,
        "transaction_id": transaction_id,
        "journal_digest": journal_digest,
        "reconciled_state_digest": reconciliation["digest"],
        "counts": reconciliation["counts"],
        "outcomes": _doctor_outcomes_v3(),
        "writes": 0,
        "approval": approval,
        "successful_event_recorded": reconciliation[
            "successful_event_recorded"
        ],
    }


def preview_state_conflict_recovery(root):
    return _preview_journal_recovery_v3(root)


def _doctor_failure_v3(preview, current, *, classification, boundary):
    return {
        "schema_version": 3,
        "mode": "state-conflict-recovery",
        "status": "recovery-failed",
        "classification": classification,
        "boundary": boundary,
        "action": current.get("action", "none"),
        "transaction_id": current.get("transaction_id"),
        "journal_digest": current["journal_digest"],
        "reconciled_state_digest": current["reconciled_state_digest"],
        "counts": current["counts"],
        "outcomes": _doctor_outcomes_v3(),
        "writes": 0,
        "partial_state": "none",
        "successful_event_recorded": current[
            "successful_event_recorded"
        ],
    }


def apply_state_conflict_recovery(
    root,
    preview,
    *,
    approved_preview,
    verification,
):
    if (
        not isinstance(preview, Mapping)
        or preview.get("status") != "approval-required"
        or approved_preview != preview.get("approval")
    ):
        raise ValueError("approved recovery preview does not match")
    current = _preview_journal_recovery_v3(root)
    if (
        current.get("status") != "approval-required"
        or current.get("approval") != approved_preview
        or dict(preview) != current
    ):
        return _doctor_failure_v3(
            preview,
            current,
            classification="recovery-approval-drift",
            boundary="recovery-revalidation",
        )
    recovery = _find_recovery_root_v3(root)
    action = current["action"]
    try:
        if action == "rollback":
            journal, _ = _load_journal_v3(root, recovery)
            result = _rollback_recovery_v3(root, recovery, journal)
            if not result["complete"]:
                return {
                    "schema_version": 3,
                    "mode": "state-conflict-recovery",
                    "status": "recovery-failed",
                    "classification": result.get(
                        "classification", "rollback-incomplete"
                    ),
                    "boundary": result.get("boundary", "rollback"),
                    "action": action,
                    "transaction_id": preview["transaction_id"],
                    "journal_digest": preview["journal_digest"],
                    "reconciled_state_digest": preview[
                        "reconciled_state_digest"
                    ],
                    "counts": preview["counts"],
                    "outcomes": result["outcomes"],
                    "writes": result["writes"],
                    "partial_state": "possible" if result["writes"] else "none",
                    "successful_event_recorded": False,
                }
            writes = result["writes"]
            outcomes = result["outcomes"]
        else:
            writes = _cleanup_recovery_area_v3(
                root,
                recovery,
                action=action,
            )
            outcomes = {
                "documents": "not-required",
                "controls": "not-required",
                "cleanup": "complete",
            }
    except _V3CleanupFailure as error:
        return {
            "schema_version": 3,
            "mode": "state-conflict-recovery",
            "status": "recovery-failed",
            "classification": _classify_os_error(error.error),
            "boundary": action,
            "action": action,
            "transaction_id": preview["transaction_id"],
            "journal_digest": preview["journal_digest"],
            "reconciled_state_digest": preview["reconciled_state_digest"],
            "counts": preview["counts"],
            "outcomes": {
                "documents": "not-required",
                "controls": "not-required",
                "cleanup": "incomplete",
            },
            "writes": error.writes,
            "partial_state": (
                "committed"
                if action == "finalize"
                else "possible" if error.writes else "none"
            ),
            "successful_event_recorded": preview[
                "successful_event_recorded"
            ],
        }
    except (OSError, ValueError):
        return {
            "schema_version": 3,
            "mode": "state-conflict-recovery",
            "status": "recovery-failed",
            "classification": "recovery-io-failure",
            "boundary": action,
            "action": action,
            "transaction_id": preview["transaction_id"],
            "journal_digest": preview["journal_digest"],
            "reconciled_state_digest": preview["reconciled_state_digest"],
            "counts": preview["counts"],
            "outcomes": {
                "documents": "not-run",
                "controls": "not-run",
                "cleanup": "incomplete",
            },
            "writes": 0,
            "partial_state": "none",
            "successful_event_recorded": preview[
                "successful_event_recorded"
            ],
        }
    return {
        "schema_version": 3,
        "mode": "state-conflict-recovery",
        "status": "recovered",
        "action": action,
        "transaction_id": preview["transaction_id"],
        "journal_digest": preview["journal_digest"],
        "reconciled_state_digest": preview["reconciled_state_digest"],
        "counts": preview["counts"],
        "outcomes": outcomes,
        "writes": writes,
        "successful_event_recorded": preview[
            "successful_event_recorded"
        ],
    }


__all__ = (
    "apply_state_conflict_recovery",
    "apply_verified_closeout",
    "prepare_verified_closeout",
    "preview_state_conflict_recovery",
    "validate_protected_intent_change",
    "verify_local_route_hashes",
)
