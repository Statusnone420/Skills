"""Transactional filesystem I/O for approved, verified lifecycle closeout."""

import copy
import errno
import hashlib
import json
import os
import re
import stat
import subprocess
import unicodedata
from collections.abc import Mapping, Sequence
from pathlib import Path

from .identity import event_fingerprint, event_id, finding_fingerprint, finding_id, slug
from .knowledge import (
    LOCAL_MAP_MAX_BYTES,
    LOCAL_MAP_PATH,
    local_prune_reason,
    validate_local_map,
)
from .lifecycle import (
    LOCAL_MAP_SCHEMA_VERSION,
    MUTATING_COMMANDS,
    READ_ONLY_COMMANDS,
    TRANSACTION_PREFIX,
    TRANSACTION_POLICY_VERSION,
    TRANSACTION_SCHEMA_VERSION,
    build_verified_event,
    findings_digest,
    prepare_dispositions,
    state_semantic_digest,
    transaction_identity,
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
    _markdown_heading_anchors,
    validate_operational_events,
    validate_operational_findings,
    validate_operational_state,
)
from .paths import _is_pruned_relative, normalize_repo_relative, safe_path
from .surfaces import validate_protected_disposition_preview


INTENT_SOURCE_MAX_BYTES = 256 * 1024
_ABSENT_DIGEST = "sha256:ABSENT"
_SHA256 = re.compile(r"^sha256:[0-9a-f]{64}$")
_FINDING_ID = re.compile(r"^DOC-([0-9A-F]{8}(?:[0-9A-F]{4})*)$")
_FINGERPRINT = re.compile(r"^[0-9a-f]{64}$")
_TEMP_NAME = re.compile(r"^\.docs-txn-[0-9A-F]{16}-.+\.tmp$")


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
    raise ValueError("transaction target is not recognized")


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


def _safe_directory_rejection(result):
    stderr = result.stderr if isinstance(result.stderr, str) else ""
    detail = stderr.casefold()
    return result.returncode == 128 and (
        "dubious ownership" in detail or "safe.directory" in detail
    )


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
    return os.path.normcase(os.path.abspath(text))


def _run_git_probe(root, *arguments):
    root_text = os.fspath(root)
    result = subprocess.run(
        ["git", "-C", root_text, *arguments],
        capture_output=True,
        text=True,
        timeout=5,
    )
    if not _safe_directory_rejection(result):
        return result
    safe_root = os.path.abspath(root_text).replace("\\", "/")
    return subprocess.run(
        [
            "git",
            "-c",
            f"safe.directory={safe_root}",
            "-C",
            root_text,
            *arguments,
        ],
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


def _target_role(relative, manifest_path=None):
    roles = {
        f"{STATE_DIRECTORY}/{STATE_FILE}": "state",
        f"{STATE_DIRECTORY}/{FINDINGS_FILE}": "findings",
        f"{STATE_DIRECTORY}/{EVENTS_FILE}": "events",
        LOCAL_MAP_PATH: "local-map",
    }
    if relative in roles:
        return roles[relative]
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
    middle = sorted(target_set - {state_relative, findings_relative, event_relative})
    return [state_relative, findings_relative, *middle, event_relative]


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
            or payload.get("transaction_id") != event.get("transaction_id")
            or _canonical_bytes(payload) != manifest_bytes
            or _sha256(manifest_bytes) != event.get("manifest_digest")
            or event.get("manifest_digest") != manifest.get("digest")
        ):
            raise ValueError("transaction manifest semantics do not match")
        normalized = copy.deepcopy(dict(payload))
        normalized["transaction_id"] = "$TRANSACTION_ID"
        return {
            "storage": "external",
            "semantic_digest": _sha256(_canonical_bytes(normalized)),
        }, manifest_path
    if "dispositions" in event:
        payload = {
            "schema_version": 1,
            "transaction_id": event.get("transaction_id"),
            "dispositions": copy.deepcopy(event["dispositions"]),
        }
        encoded = _canonical_bytes(payload)
        if _sha256(encoded) != event.get("disposition_digest"):
            raise ValueError("inline disposition semantics do not match")
        payload["transaction_id"] = "$TRANSACTION_ID"
        return {
            "storage": "inline",
            "semantic_digest": _sha256(_canonical_bytes(payload)),
        }, None
    if any(field in event for field in ("disposition_digest", "manifest_digest")):
        raise ValueError("transaction disposition fields are incomplete")
    return None, None


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
    if event.get("event_id") != event_id(event_fingerprint(event)):
        raise ValueError("transaction event ID does not match semantic content")

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
    expected_roles = {
        relative: _target_role(relative, manifest_path)
        for relative in targets
    }
    expected_order = _replacement_order(targets)
    if plan.get("target_roles") != expected_roles:
        raise ValueError("transaction target roles do not match")
    if plan.get("replacement_order") != expected_order:
        raise ValueError("transaction replacement order does not match")

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

    return {
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
    }


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
    base_events_bytes=None,
    selected_boundary=".",
    _transaction_id=None,
):
    root = Path(root).absolute()
    safe_path(root, root)
    selected_boundary = normalize_repo_relative(
        selected_boundary,
        "transaction selected boundary",
    )
    approvals = list(approvals)
    dispositions = list(dispositions)
    removed_items = list(removed_items)
    recurring_findings = list(recurring_findings)
    if command in READ_ONLY_COMMANDS:
        raise ValueError(f"read-only command cannot close operational memory: {command}")
    if command not in MUTATING_COMMANDS and command != "state-conflict-recovery":
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
    if event_input.get("score_after") is not None:
        proposed_state["rubric"]["last_verified_score"] = event_input["score_after"]

    control = safe_path(root / STATE_DIRECTORY, root)
    fixed_targets = [
        f"{STATE_DIRECTORY}/{STATE_FILE}",
        f"{STATE_DIRECTORY}/{FINDINGS_FILE}",
        f"{STATE_DIRECTORY}/{EVENTS_FILE}",
    ]
    if local_map is not None:
        local_bytes = _validate_local_map(local_map)
        git_status = _git_ignore_status(root)
        if git_status != "ignored":
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

    starting = _capture_start(root, fixed_targets)
    local_map_digest = _sha256(local_bytes) if local_bytes is not None else None
    protected_preview_digest = (
        _sha256(_canonical_bytes(protected_preview))
        if protected_preview is not None
        else None
    )
    txid = _transaction_id or ("TXN-" + "0" * 16)
    prepared_dispositions = None
    if dispositions or removed_items:
        git_available = _git_ignore_status(root) != "no-git"
        prepared_dispositions = prepare_dispositions(
            None,
            dispositions,
            removed_items=removed_items,
            git_available=git_available,
            hard_delete_approval=hard_delete_approval,
            transaction_id=txid,
        )

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
    transaction_targets = list(fixed_targets)
    if prepared_dispositions is not None and prepared_dispositions["storage"] == "external":
        transaction_targets.insert(-1, "manifest")
        starting["manifest"] = _ABSENT_DIGEST
    built_event = build_verified_event(
        event_input,
        transaction_id=txid,
        dispositions=prepared_dispositions,
        recurring_findings=recurring_findings,
        starting_digests=starting,
        state_semantic_digest=state_digest,
        findings_digest=stored_findings_digest,
        transaction_targets=transaction_targets,
        approval_bindings=normalized_approvals,
        local_map_digest=local_map_digest,
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

    replacement_order = _replacement_order(targets)
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
    expected_transaction = transaction_identity(
        _plan_authorization_semantics(plan, root)
    )
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
            base_events_bytes=base_events_bytes,
            selected_boundary=selected_boundary,
            _transaction_id=expected_transaction,
        )
    if expected_transaction != _transaction_id:
        raise ValueError("transaction identity did not converge")
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
    selected_boundary=".",
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
        selected_boundary=selected_boundary,
    )


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
    expected = transaction_identity(_plan_authorization_semantics(plan, root))
    if expected != plan.get("transaction_id"):
        raise ValueError("transaction authorization identity does not match")


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


def _restore_target(target, original, mtime, transaction_id):
    target = Path(target)
    if original is None:
        try:
            target.unlink()
        except FileNotFoundError:
            pass
        return
    staged = _stage_bytes(target, original, transaction_id)
    os.replace(staged, target)
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
            mtime = target.stat().st_mtime_ns if original is not None else None
            originals[relative] = (original, mtime)
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
        active_boundary = "clear:transaction-recovery-marker"
        _clear_recovery_marker(recovery_marker)
        recovery_marker = None
    except KeyboardInterrupt:
        for relative in reversed(replaced):
            original, mtime = originals[relative]
            _restore_target(safe_path(root / relative, root), original, mtime, txid)
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
            original, mtime = originals[relative]
            try:
                _restore_target(safe_path(root / relative, root), original, mtime, txid)
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


def preview_state_conflict_recovery(root, *, canonical_state, recomputed_findings, surviving_events):
    """Build a deterministic, zero-write reconstruction preview."""
    root = Path(root).absolute()
    starting = _capture_start(
        root,
        (
            f"{STATE_DIRECTORY}/{STATE_FILE}",
            f"{STATE_DIRECTORY}/{FINDINGS_FILE}",
            f"{STATE_DIRECTORY}/{EVENTS_FILE}",
        ),
    )
    event = semantic_event = {
        "kind": "state-conflict-recovery",
        "completed_at": "informational",
        "skill_version": canonical_state["initialized"]["skill_version"],
        "approved_ids": [],
        "score_before": canonical_state["rubric"]["last_verified_score"],
        "score_after": canonical_state["rubric"]["last_verified_score"],
        "changed_paths": [
            f"{STATE_DIRECTORY}/{STATE_FILE}",
            f"{STATE_DIRECTORY}/{FINDINGS_FILE}",
        ],
        "reason": "Reconstructed conflicted operational continuity from canonical evidence.",
        "summary": "Preserved non-conflicting protected and verified-source records.",
    }
    base_events = b"".join(_canonical_bytes(event) for event in surviving_events)
    plan = _prepare_plan(
        root,
        command="state-conflict-recovery",
        state=canonical_state,
        findings=recomputed_findings,
        event=semantic_event,
        approvals=[],
        base_events_bytes=base_events,
    )
    public = {
        "status": "approval-required",
        "writes": 0,
        "state": copy.deepcopy(canonical_state),
        "findings": copy.deepcopy(recomputed_findings),
        "surviving_events": copy.deepcopy(surviving_events),
        "discarded_conflict_evidence": [
            {"path": relative, "digest": digest}
            for relative, digest in sorted(starting.items())
        ],
        "transaction_id": plan["transaction_id"],
    }
    public["preview_digest"] = _sha256(_canonical_bytes(public))
    public["_plan"] = plan
    return public


def apply_state_conflict_recovery(root, preview, *, approved_preview, verification):
    if not isinstance(preview, Mapping) or approved_preview != preview.get("preview_digest"):
        raise ValueError("approved recovery preview does not match")
    return apply_verified_closeout(
        root,
        preview["_plan"],
        approved_transaction=preview["transaction_id"],
        verification=verification,
    )


__all__ = (
    "apply_state_conflict_recovery",
    "apply_verified_closeout",
    "prepare_verified_closeout",
    "preview_state_conflict_recovery",
    "validate_protected_intent_change",
    "verify_local_route_hashes",
)
