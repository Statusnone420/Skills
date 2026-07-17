"""Engine-owned closeout for exact, already-approved Doctor treatments."""

from __future__ import annotations

import copy
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile

from .identity import finding_fingerprint, finding_id
from .health import normalized_content_digest
from .lifecycle import select_persisted_findings, transition_finding
from .lifecycle_io import apply_verified_closeout, prepare_verified_closeout
from .memory import (
    EVENTS_FILE,
    FINDINGS_FILE,
    STATE_DIRECTORY,
    STATE_FILE,
    inspect_operational_memory,
    load_operational_events,
    load_operational_findings,
    load_operational_state,
    validate_operational_state,
)
from .paths import normalize_repo_relative, safe_path, tracked_markdown_scope


SCHEMA_VERSION = 1
MAX_REQUEST_BYTES = 1024 * 1024
MAX_TREATMENT_FILES = 64
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_ABSENT = "sha256:ABSENT"


class DoctorCloseoutError(ValueError):
    """A bounded public failure with an honest closeout classification."""

    def __init__(self, status, classification, boundary):
        super().__init__(classification)
        self.status = status
        self.classification = classification
        self.boundary = boundary


def canonical_bytes(value):
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


def _sha256(data):
    return hashlib.sha256(data).hexdigest()


def _raw_digest(path):
    path = Path(path)
    if not os.path.lexists(path):
        return _ABSENT
    if not path.is_file() or path.is_symlink():
        raise DoctorCloseoutError("stale-preview", "document-path-unavailable", "document-revalidation")
    return "sha256:" + _sha256(path.read_bytes())


def _within_scope(path, scope):
    return scope == "." or path == scope or path.startswith(scope + "/")


def _normalize_digest(value, name):
    if not isinstance(value, str):
        raise DoctorCloseoutError("invalid-request", f"{name}-invalid", "request-validation")
    prefix, separator, digest = value.partition(":")
    if prefix != "sha256" or not separator or _SHA256.fullmatch(digest.lower()) is None:
        raise DoctorCloseoutError("invalid-request", f"{name}-invalid", "request-validation")
    return digest.lower()


def _normalize_markdown_path(value, name, scope):
    try:
        path = normalize_repo_relative(value, name)
    except (TypeError, ValueError) as exc:
        raise DoctorCloseoutError("invalid-request", "path-invalid", "request-validation") from exc
    if (
        path == "."
        or Path(path).suffix.lower() != ".md"
        or not _within_scope(path, scope)
        or path.casefold() == ".local"
        or path.casefold().startswith(".local/")
    ):
        raise DoctorCloseoutError("invalid-request", "path-outside-shared-scope", "request-validation")
    return path


def _git_path_is_ignored(root, path):
    result = _run(
        root,
        ["check-ignore", "--quiet", "--no-index", "--", path],
        boundary="shared-corpus-validation",
    )
    if result.returncode == 0:
        return True
    if result.returncode == 1:
        return False
    raise DoctorCloseoutError(
        "invalid-request", "git-ignore-unavailable", "shared-corpus-validation"
    )


def _validate_treatment_paths(root, scope, paths, *, require_existing_shared):
    try:
        tracked = tracked_markdown_scope(root, scope)
    except (OSError, ValueError) as exc:
        raise DoctorCloseoutError(
            "invalid-request", "shared-corpus-unavailable", "shared-corpus-validation"
        ) from exc
    if tracked is None:
        raise DoctorCloseoutError(
            "requires-user-action", "git-required", "shared-corpus-validation"
        )
    shared = set(tracked)
    for path in paths:
        if _git_path_is_ignored(root, path):
            raise DoctorCloseoutError(
                "invalid-request", "ignored-treatment-path", "shared-corpus-validation"
            )
        try:
            target = safe_path(root / path, root)
        except (OSError, ValueError) as exc:
            raise DoctorCloseoutError(
                "invalid-request", "path-invalid", "shared-corpus-validation"
            ) from exc
        if require_existing_shared and os.path.lexists(target) and path not in shared:
            raise DoctorCloseoutError(
                "invalid-request", "unshared-treatment-path", "shared-corpus-validation"
            )


def _canonical_finding(raw):
    if not isinstance(raw, dict) or not isinstance(raw.get("kind"), str):
        raise DoctorCloseoutError("invalid-request", "finding-invalid", "request-validation")
    try:
        fingerprint = finding_fingerprint(raw["kind"], [raw])
    except (TypeError, ValueError) as exc:
        raise DoctorCloseoutError("invalid-request", "finding-invalid", "request-validation") from exc
    return fingerprint, copy.deepcopy(raw)


def _identified_findings(findings):
    rows = []
    for raw in findings:
        fingerprint, evidence = _canonical_finding(raw)
        rows.append((fingerprint, evidence))
    rows.sort(
        key=lambda row: (
            row[0],
            json.dumps(row[1], sort_keys=True, separators=(",", ":"), ensure_ascii=True),
        )
    )
    existing = {}
    identified = []
    seen = set()
    for fingerprint, evidence in rows:
        if fingerprint in seen:
            continue
        identifier = finding_id(fingerprint, existing)
        existing[identifier] = fingerprint
        identified.append(
            {"id": identifier, "fingerprint": fingerprint, "evidence": evidence}
        )
        seen.add(fingerprint)
    return identified


def _run(root, arguments, *, env=None, boundary="git-verification"):
    try:
        result = subprocess.run(
            ["git", "-C", os.fspath(root), *arguments],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
            env=env,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise DoctorCloseoutError("invalid-request", "git-unavailable", boundary) from exc
    return result


def _require_clean_start(root):
    result = _run(root, ["status", "--porcelain=v1", "--untracked-files=no"], boundary="isolation")
    if result.returncode != 0:
        raise DoctorCloseoutError("invalid-request", "git-unavailable", "isolation")
    if result.stdout:
        raise DoctorCloseoutError("requires-user-action", "current-branch-not-clean", "isolation")


def _checker_command(root, *, scope, map_path, hot_paths, env=None):
    checker = Path(__file__).parents[1] / "check.py"
    command = [
        sys.executable,
        os.fspath(checker),
        os.fspath(root),
        "--json",
        "--agent",
        "--map",
        map_path,
        "--scope",
        scope,
    ]
    if hot_paths:
        command.extend(["--hot", ",".join(hot_paths)])
    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
            env=env,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise DoctorCloseoutError("invalid-request", "checker-unavailable", "candidate-verification") from exc
    try:
        payload = json.loads(result.stdout)
    except (TypeError, json.JSONDecodeError) as exc:
        raise DoctorCloseoutError("invalid-request", "checker-malformed", "candidate-verification") from exc
    if result.returncode != 0 or not isinstance(payload, dict) or not isinstance(payload.get("findings"), list):
        raise DoctorCloseoutError("stale-preview", "checker-failed", "candidate-verification")
    return payload


def _temporary_index(root, allowed_paths):
    class Overlay:
        def __enter__(self):
            handle = tempfile.NamedTemporaryFile(prefix="docs-treatment-index-", delete=False)
            self.path = Path(handle.name)
            handle.close()
            self.path.unlink(missing_ok=True)
            self.env = dict(os.environ)
            self.env["GIT_INDEX_FILE"] = os.fspath(self.path)
            head = _run(root, ["rev-parse", "--verify", "HEAD"], env=self.env, boundary="candidate-index")
            tree = ["read-tree", "HEAD"] if head.returncode == 0 else ["read-tree", "--empty"]
            if _run(root, tree, env=self.env, boundary="candidate-index").returncode != 0:
                self.__exit__(None, None, None)
                raise DoctorCloseoutError("invalid-request", "candidate-index-unavailable", "candidate-index")
            present = [path for path in allowed_paths if (root / path).is_file()]
            absent = [path for path in allowed_paths if path not in present]
            if present and _run(root, ["add", "--", *present], env=self.env, boundary="candidate-index").returncode != 0:
                self.__exit__(None, None, None)
                raise DoctorCloseoutError("stale-preview", "candidate-index-stage-failed", "candidate-index")
            if absent and _run(root, ["add", "-u", "--", *absent], env=self.env, boundary="candidate-index").returncode != 0:
                self.__exit__(None, None, None)
                raise DoctorCloseoutError("stale-preview", "candidate-index-stage-failed", "candidate-index")
            return self.env

        def __exit__(self, _type, _value, _traceback):
            self.path.unlink(missing_ok=True)
            Path(str(self.path) + ".lock").unlink(missing_ok=True)
            return False

    return Overlay()


def _control_digests(root):
    return {
        f"{STATE_DIRECTORY}/{STATE_FILE}": _raw_digest(root / STATE_DIRECTORY / STATE_FILE),
        f"{STATE_DIRECTORY}/{FINDINGS_FILE}": _raw_digest(root / STATE_DIRECTORY / FINDINGS_FILE),
        f"{STATE_DIRECTORY}/{EVENTS_FILE}": _raw_digest(root / STATE_DIRECTORY / EVENTS_FILE),
    }


def _operational_inputs(root):
    try:
        issues = inspect_operational_memory(root)
        state = load_operational_state(root)
        findings = load_operational_findings(root)
        events = load_operational_events(root)
    except (OSError, UnicodeError, ValueError) as exc:
        raise DoctorCloseoutError("stale-preview", "operational-memory-invalid", "memory-revalidation") from exc
    if state is None or findings is None or any(item.get("kind") == "state-conflict" for item in issues):
        raise DoctorCloseoutError("stale-preview", "state-conflict", "memory-revalidation")
    return state, findings, events


def _scope_digests(root, scope):
    try:
        routes = tracked_markdown_scope(root, scope)
    except (OSError, ValueError) as exc:
        raise DoctorCloseoutError("invalid-request", "shared-corpus-unavailable", "scope-revalidation") from exc
    if routes is None:
        raise DoctorCloseoutError("requires-user-action", "git-required", "scope-revalidation")
    return {route: _raw_digest(safe_path(root / route, root)) for route in routes}


def _reject_unapproved_changed_markdown(root, scope, allowed_paths):
    try:
        result = subprocess.run(
            [
                "git",
                "-C",
                os.fspath(root),
                "status",
                "--porcelain=v1",
                "-z",
                "--untracked-files=all",
                "--",
                scope,
            ],
            capture_output=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise DoctorCloseoutError(
            "stale-preview", "git-unavailable", "document-revalidation"
        ) from exc
    if result.returncode != 0:
        raise DoctorCloseoutError(
            "stale-preview", "git-unavailable", "document-revalidation"
        )
    for record in result.stdout.split(b"\0"):
        if not record:
            continue
        if len(record) < 4 or record[2:3] != b" ":
            raise DoctorCloseoutError(
                "stale-preview", "git-status-malformed", "document-revalidation"
            )
        status, raw_path = record[:2], record[3:]
        if b"R" in status or b"C" in status:
            raise DoctorCloseoutError(
                "stale-preview", "unapproved-document-change", "document-revalidation"
            )
        try:
            path = normalize_repo_relative(
                raw_path.decode("utf-8", "surrogateescape"),
                "changed document path",
            )
        except (TypeError, ValueError, UnicodeError) as exc:
            raise DoctorCloseoutError(
                "stale-preview", "git-status-malformed", "document-revalidation"
            ) from exc
        if (
            path.casefold().endswith(".md")
            and _within_scope(path, scope)
            and path not in allowed_paths
        ):
            raise DoctorCloseoutError(
                "stale-preview", "unapproved-document-change", "document-revalidation"
            )


def _validate_request(root, request, state, initial_findings):
    if not isinstance(request, dict) or set(request) != {
        "schema_version", "scope", "map", "hot_paths", "treatments"
    }:
        raise DoctorCloseoutError("invalid-request", "request-contract", "request-validation")
    if request["schema_version"] != SCHEMA_VERSION:
        raise DoctorCloseoutError("invalid-request", "request-schema", "request-validation")
    try:
        scope = normalize_repo_relative(request["scope"], "scope")
        map_path = _normalize_markdown_path(request["map"], "map", scope)
    except (TypeError, ValueError) as exc:
        raise DoctorCloseoutError("invalid-request", "scope-invalid", "request-validation") from exc
    raw_hot = request["hot_paths"]
    if not isinstance(raw_hot, list):
        raise DoctorCloseoutError("invalid-request", "hot-paths-invalid", "request-validation")
    hot_paths = sorted({_normalize_markdown_path(value, "hot path", scope) for value in raw_hot})
    if (
        state["scope"]["selected"] != scope
        or state["initialized"]["map"] != map_path
        or sorted(state["initialized"]["hot_paths"]) != hot_paths
    ):
        raise DoctorCloseoutError("stale-preview", "state-routing-drift", "request-validation")
    actual = {row["fingerprint"]: row for row in _identified_findings(initial_findings)}
    raw_treatments = request["treatments"]
    if not isinstance(raw_treatments, list) or not raw_treatments:
        raise DoctorCloseoutError("invalid-request", "treatments-invalid", "request-validation")
    used_coverage = set()
    treatment_ids = {}
    treatments = []
    allowed_paths = set()
    for index, raw in enumerate(raw_treatments):
        if not isinstance(raw, dict) or set(raw) != {"findings", "files"}:
            raise DoctorCloseoutError("invalid-request", "treatment-invalid", "request-validation")
        if not isinstance(raw["findings"], list) or not raw["findings"] or not isinstance(raw["files"], list) or not raw["files"]:
            raise DoctorCloseoutError("invalid-request", "treatment-invalid", "request-validation")
        coverage = []
        for candidate in raw["findings"]:
            fingerprint, _evidence = _canonical_finding(candidate)
            if fingerprint in used_coverage or fingerprint not in actual:
                raise DoctorCloseoutError("stale-preview", "finding-fingerprint-mismatch", "approval-revalidation")
            row = actual[fingerprint]
            coverage.append({"id": row["id"], "fingerprint": row["fingerprint"]})
            used_coverage.add(fingerprint)
        coverage.sort(key=lambda row: row["id"])
        files = sorted({_normalize_markdown_path(value, "treatment file", scope) for value in raw["files"]})
        if len(files) != len(raw["files"]):
            raise DoctorCloseoutError("invalid-request", "duplicate-treatment-file", "request-validation")
        basis = {
            "scope": scope,
            "map": map_path,
            "hot_paths": hot_paths,
            "coverage": coverage,
            "files": files,
        }
        fingerprint = _sha256(canonical_bytes(basis))
        identifier = finding_id(fingerprint, treatment_ids)
        treatment_ids[identifier] = fingerprint
        treatments.append(
            {
                "id": identifier,
                "fingerprint": "sha256:" + fingerprint,
                "coverage": coverage,
                "files": files,
                "affected_count": len(files),
            }
        )
        allowed_paths.update(files)
    treatments.sort(key=lambda row: row["id"])
    allowed_paths = sorted(allowed_paths)
    if len(allowed_paths) > MAX_TREATMENT_FILES:
        raise DoctorCloseoutError(
            "invalid-request", "treatment-file-capacity", "request-validation"
        )
    _validate_treatment_paths(
        root, scope, allowed_paths, require_existing_shared=True
    )
    return scope, map_path, hot_paths, treatments, allowed_paths


def _validate_selected_finding_statuses(findings, treatments):
    selected = {
        row["fingerprint"]
        for treatment in treatments
        for row in treatment["coverage"]
    }
    for record in findings["findings"]:
        if (
            record.get("fingerprint") in selected
            and record.get("status") not in {"Proposed", "Approved"}
        ):
            raise DoctorCloseoutError(
                "stale-preview",
                "finding-status-not-actionable",
                "approval-revalidation",
            )


def _receipt_base(root, request):
    _require_clean_start(root)
    state, findings, _events = _operational_inputs(root)
    initial = _checker_command(
        root,
        scope=request["scope"],
        map_path=request["map"],
        hot_paths=request["hot_paths"],
    )
    scope, map_path, hot_paths, treatments, allowed_paths = _validate_request(
        root, request, state, initial["findings"]
    )
    _validate_selected_finding_statuses(findings, treatments)
    _reject_unapproved_changed_markdown(root, scope, ())
    starts = {path: _raw_digest(root / path) for path in allowed_paths}
    return {
        "schema_version": SCHEMA_VERSION,
        "kind": "doctor-treatment-closeout",
        "scope": scope,
        "map": map_path,
        "hot_paths": hot_paths,
        "treatments": treatments,
        "allowed_paths": allowed_paths,
        "allowed_starting_digests": starts,
        "scope_digests": _scope_digests(root, scope),
        "initial_finding_fingerprints": [
            row["fingerprint"] for row in _identified_findings(initial["findings"])
        ],
        "control_digests": _control_digests(root),
        "score_before": initial["health"]["percentage"],
    }


def prepare_treatment_receipt(root, request):
    """Build a zero-write receipt before the approved documents are changed."""
    root = Path(root).absolute()
    safe_path(root, root)
    base = _receipt_base(root, request)
    digest = "sha256:" + _sha256(canonical_bytes(base))
    approval = _approval_text(base["treatments"], digest)
    return {**base, "receipt_sha256": digest, "approval": approval}


def _approval_text(treatments, receipt_digest):
    fragments = []
    for treatment in treatments:
        identifier = treatment["id"]
        fingerprint = _normalize_digest(
            treatment["fingerprint"], "treatment-fingerprint"
        )
        fragments.append(f"{identifier} fingerprint sha256:{fingerprint}")
    noun = "treatment " if len(fragments) == 1 else "treatments "
    return "Approve $docs " + noun + "; ".join(fragments) + f"; receipt {receipt_digest}"


def _validated_receipt_treatments(
    receipt,
    *,
    scope,
    map_path,
    hot_paths,
    initial_fingerprints,
):
    initial_ids = {}
    for fingerprint in initial_fingerprints:
        initial_ids[finding_id(fingerprint, initial_ids)] = fingerprint
    raw_treatments = receipt["treatments"]
    if not isinstance(raw_treatments, list) or not raw_treatments:
        raise DoctorCloseoutError(
            "stale-preview", "receipt-treatment-mismatch", "approval-revalidation"
        )
    treatment_ids = {}
    covered = set()
    allowed_paths = set()
    normalized_treatments = []
    for raw in raw_treatments:
        if not isinstance(raw, dict) or set(raw) != {
            "id",
            "fingerprint",
            "coverage",
            "files",
            "affected_count",
        }:
            raise DoctorCloseoutError(
                "stale-preview", "receipt-treatment-mismatch", "approval-revalidation"
            )
        if not isinstance(raw["coverage"], list) or not raw["coverage"]:
            raise DoctorCloseoutError(
                "stale-preview", "receipt-treatment-mismatch", "approval-revalidation"
            )
        coverage = []
        for item in raw["coverage"]:
            if (
                not isinstance(item, dict)
                or set(item) != {"id", "fingerprint"}
                or not isinstance(item["id"], str)
                or not isinstance(item["fingerprint"], str)
                or _SHA256.fullmatch(item["fingerprint"]) is None
                or initial_ids.get(item["id"]) != item["fingerprint"]
                or item["fingerprint"] in covered
            ):
                raise DoctorCloseoutError(
                    "stale-preview", "receipt-treatment-mismatch", "approval-revalidation"
                )
            coverage.append(
                {"id": item["id"], "fingerprint": item["fingerprint"]}
            )
            covered.add(item["fingerprint"])
        if coverage != sorted(coverage, key=lambda item: item["id"]):
            raise DoctorCloseoutError(
                "stale-preview", "receipt-treatment-mismatch", "approval-revalidation"
            )
        if not isinstance(raw["files"], list) or not raw["files"]:
            raise DoctorCloseoutError(
                "stale-preview", "receipt-treatment-mismatch", "approval-revalidation"
            )
        try:
            files = sorted(
                {
                    _normalize_markdown_path(path, "receipt treatment path", scope)
                    for path in raw["files"]
                }
            )
        except (TypeError, ValueError) as exc:
            raise DoctorCloseoutError(
                "stale-preview", "receipt-treatment-mismatch", "approval-revalidation"
            ) from exc
        if files != raw["files"] or len(files) != len(raw["files"]):
            raise DoctorCloseoutError(
                "stale-preview", "receipt-treatment-mismatch", "approval-revalidation"
            )
        basis = {
            "scope": scope,
            "map": map_path,
            "hot_paths": hot_paths,
            "coverage": coverage,
            "files": files,
        }
        fingerprint = _sha256(canonical_bytes(basis))
        identifier = finding_id(fingerprint, treatment_ids)
        if (
            raw["id"] != identifier
            or raw["fingerprint"] != "sha256:" + fingerprint
            or type(raw["affected_count"]) is not int
            or raw["affected_count"] != len(files)
        ):
            raise DoctorCloseoutError(
                "stale-preview", "receipt-treatment-mismatch", "approval-revalidation"
            )
        treatment_ids[identifier] = fingerprint
        normalized_treatments.append(
            {
                "id": identifier,
                "fingerprint": "sha256:" + fingerprint,
                "coverage": coverage,
                "files": files,
                "affected_count": len(files),
            }
        )
        allowed_paths.update(files)
    if normalized_treatments != sorted(
        normalized_treatments, key=lambda item: item["id"]
    ):
        raise DoctorCloseoutError(
            "stale-preview", "receipt-treatment-mismatch", "approval-revalidation"
        )
    allowed_paths = sorted(allowed_paths)
    if (
        len(allowed_paths) > MAX_TREATMENT_FILES
        or receipt["allowed_paths"] != allowed_paths
    ):
        raise DoctorCloseoutError(
            "stale-preview", "receipt-treatment-mismatch", "approval-revalidation"
        )
    return allowed_paths


def _validate_receipt(root, receipt, approval):
    if not isinstance(receipt, dict):
        raise DoctorCloseoutError("stale-preview", "receipt-invalid", "approval-revalidation")
    expected_keys = {
        "schema_version", "kind", "scope", "map", "hot_paths", "treatments", "allowed_paths",
        "allowed_starting_digests", "scope_digests", "initial_finding_fingerprints", "control_digests", "score_before",
        "receipt_sha256", "approval",
    }
    if set(receipt) != expected_keys or receipt.get("schema_version") != SCHEMA_VERSION or receipt.get("kind") != "doctor-treatment-closeout":
        raise DoctorCloseoutError("stale-preview", "receipt-invalid", "approval-revalidation")
    base = {key: value for key, value in receipt.items() if key not in {"receipt_sha256", "approval"}}
    digest = "sha256:" + _sha256(canonical_bytes(base))
    if receipt.get("receipt_sha256") != digest:
        raise DoctorCloseoutError("stale-preview", "receipt-drift", "approval-revalidation")
    for treatment in receipt["treatments"]:
        if not isinstance(treatment, dict):
            raise DoctorCloseoutError("stale-preview", "receipt-invalid", "approval-revalidation")
        identifier = treatment.get("id")
        fingerprint = treatment.get("fingerprint")
        if not isinstance(identifier, str):
            raise DoctorCloseoutError("stale-preview", "receipt-invalid", "approval-revalidation")
        _normalize_digest(fingerprint, "receipt-fingerprint")
    expected_approval = _approval_text(receipt["treatments"], digest)
    if receipt.get("approval") != expected_approval:
        raise DoctorCloseoutError("stale-preview", "receipt-drift", "approval-revalidation")
    if approval != expected_approval:
        raise DoctorCloseoutError("stale-preview", "approval-mismatch", "approval-revalidation")
    try:
        scope = normalize_repo_relative(receipt["scope"], "receipt scope")
        map_path = _normalize_markdown_path(receipt["map"], "receipt map", scope)
        hot_paths = sorted({_normalize_markdown_path(path, "receipt hot path", scope) for path in receipt["hot_paths"]})
    except (TypeError, ValueError, KeyError) as exc:
        raise DoctorCloseoutError("stale-preview", "receipt-invalid", "approval-revalidation") from exc
    initial_fingerprints = receipt["initial_finding_fingerprints"]
    if (
        not isinstance(initial_fingerprints, list)
        or initial_fingerprints != sorted(initial_fingerprints)
        or len(initial_fingerprints) != len(set(initial_fingerprints))
        or any(not isinstance(value, str) or _SHA256.fullmatch(value) is None for value in initial_fingerprints)
    ):
        raise DoctorCloseoutError("stale-preview", "receipt-invalid", "approval-revalidation")
    allowed_paths = _validated_receipt_treatments(
        receipt,
        scope=scope,
        map_path=map_path,
        hot_paths=hot_paths,
        initial_fingerprints=initial_fingerprints,
    )
    _validate_treatment_paths(
        root, scope, allowed_paths, require_existing_shared=False
    )
    return scope, map_path, hot_paths, allowed_paths


def _verify_document_boundary(root, receipt, allowed_paths):
    if _control_digests(root) != receipt["control_digests"]:
        raise DoctorCloseoutError("stale-preview", "control-target-drift", "control-revalidation")
    _reject_unapproved_changed_markdown(root, receipt["scope"], allowed_paths)
    starts = receipt["allowed_starting_digests"]
    if set(starts) != set(allowed_paths):
        raise DoctorCloseoutError("stale-preview", "receipt-invalid", "document-revalidation")
    changed = []
    for path, expected in receipt["scope_digests"].items():
        actual = _raw_digest(root / path)
        if actual != expected and path not in allowed_paths:
            raise DoctorCloseoutError("stale-preview", "unapproved-document-change", "document-revalidation")
    for path in allowed_paths:
        actual = _raw_digest(root / path)
        if actual == _ABSENT and starts[path] != _ABSENT:
            raise DoctorCloseoutError(
                "stale-preview", "approved-document-deleted", "document-revalidation"
            )
        if actual != starts[path]:
            changed.append(path)
    if len(changed) != len(allowed_paths):
        raise DoctorCloseoutError(
            "stale-preview", "approved-document-unchanged", "document-revalidation"
        )
    return {path: _raw_digest(root / path) for path in allowed_paths}


def _candidate(root, receipt, *, result_digests):
    allowed_paths = receipt["allowed_paths"]
    if {path: _raw_digest(root / path) for path in allowed_paths} != result_digests:
        raise DoctorCloseoutError("stale-preview", "approved-document-drift", "candidate-verification")
    with _temporary_index(root, allowed_paths) as env:
        payload = _checker_command(
            root,
            scope=receipt["scope"],
            map_path=receipt["map"],
            hot_paths=receipt["hot_paths"],
            env=env,
        )
    identified = _identified_findings(payload["findings"])
    refreshable_stale = {
        row["fingerprint"]
        for row in identified
        if row["evidence"].get("kind") == "stale-evidence"
        and row["evidence"].get("path") in receipt["allowed_paths"]
    }
    observed = {row["fingerprint"] for row in identified}
    covered = {
        row["fingerprint"]
        for treatment in receipt["treatments"]
        for row in treatment["coverage"]
    }
    if observed & covered:
        raise DoctorCloseoutError("stale-preview", "approved-finding-remains", "candidate-verification")
    initial = set(receipt["initial_finding_fingerprints"])
    if observed - initial - refreshable_stale:
        raise DoctorCloseoutError(
            "stale-preview", "new-unapproved-finding", "candidate-verification"
        )
    health = payload.get("health")
    if not isinstance(health, dict) or not isinstance(health.get("percentage"), int):
        raise DoctorCloseoutError("stale-preview", "candidate-health-invalid", "candidate-verification")
    remaining = []
    for finding in payload["findings"]:
        fingerprint, _evidence = _canonical_finding(finding)
        if fingerprint not in refreshable_stale:
            remaining.append(finding)
    return {
        "status": "clean" if not remaining else "verified",
        "score": health["percentage"],
        "health": health,
        "findings": payload["findings"],
    }


def _refresh_verified_documents(root, state, allowed_paths):
    refreshed = set()
    documents = copy.deepcopy(state["verified_documents"])
    for record in documents:
        routes = [(record, "document")]
        routes.extend((source, "path") for source in record["sources"])
        for item, field in routes:
            route = item[field]
            if route not in allowed_paths:
                continue
            try:
                target = safe_path(root / route, root)
                if not target.is_file() or target.is_symlink():
                    raise OSError("verified route is unavailable")
                item["digest"] = normalized_content_digest(target)
            except (OSError, ValueError, UnicodeError) as exc:
                raise DoctorCloseoutError(
                    "stale-preview", "verified-evidence-unavailable", "candidate-verification"
                ) from exc
            refreshed.add(route)
    return documents, refreshed


def _refresh_coverage(health, refreshed_routes):
    coverage = copy.deepcopy(health.get("coverage"))
    if not isinstance(coverage, dict) or not isinstance(coverage.get("routes"), list):
        raise DoctorCloseoutError(
            "stale-preview", "candidate-coverage-invalid", "candidate-verification"
        )
    unresolved_stale = False
    for route in coverage["routes"]:
        if not isinstance(route, dict):
            raise DoctorCloseoutError(
                "stale-preview", "candidate-coverage-invalid", "candidate-verification"
            )
        if (
            route.get("freshness") == "stale"
            and route.get("route") in refreshed_routes
            and any(
                source in {"state:verified-document", "state:verified-source"}
                for source in route.get("sources", ())
            )
        ):
            route["freshness"] = "fresh"
            route["verified"] = True
        elif route.get("freshness") == "stale":
            unresolved_stale = True
    if unresolved_stale:
        raise DoctorCloseoutError(
            "stale-preview", "candidate-trust-stale", "candidate-verification"
        )
    coverage["routes"].sort(key=lambda route: (route["route"].casefold(), route["route"]))
    coverage["numerator"] = sum(route.get("verified") is True for route in coverage["routes"])
    coverage["denominator"] = len(coverage["routes"])
    coverage["status"] = (
        "unverified"
        if coverage["denominator"] == 0
        else "verified"
        if coverage["numerator"] == coverage["denominator"]
        else "partial"
    )
    return coverage


def _derive_state(root, state, candidate, receipt):
    health = candidate["health"]
    result = copy.deepcopy(state)
    result["verified_documents"], refreshed_routes = _refresh_verified_documents(
        root, state, receipt["allowed_paths"]
    )
    result["rubric"]["last_verified_score"] = candidate["score"]
    result["rubric"]["last_verified_status"] = health["structure_status"]
    result["structural_scores"] = {
        "before": receipt["score_before"],
        "after": candidate["score"],
    }
    result["hot_path_bytes"] = {
        "before": copy.deepcopy(state["hot_path_bytes"]["after"]),
        "after": {
            "value": health["hot_path_bytes"]["value"],
            "unit": "bytes",
            "provenance": copy.deepcopy(health["hot_path_bytes"]["provenance"]),
        },
    }
    result["trust_coverage"] = _refresh_coverage(health, refreshed_routes)
    try:
        return validate_operational_state(result, root)
    except (TypeError, ValueError) as exc:
        raise DoctorCloseoutError("stale-preview", "candidate-state-invalid", "candidate-verification") from exc


def _derive_findings(findings, receipt):
    _validate_selected_finding_statuses(findings, receipt["treatments"])
    selected = {
        row["fingerprint"]
        for treatment in receipt["treatments"]
        for row in treatment["coverage"]
    }
    retained = []
    for record in findings["findings"]:
        if record.get("fingerprint") in selected:
            status = record.get("status")
            if status == "Proposed":
                record = transition_finding(record, "Approved")
            if record.get("status") == "Approved":
                record = transition_finding(record, "Applied")
        retained.append(record)
    return {"schema_version": 1, "findings": select_persisted_findings(retained)}


def _completed_at():
    return datetime.now(timezone.utc).replace(microsecond=0).strftime("%Y-%m-%dT%H:%M:%SZ")


def apply_treatment_receipt(root, receipt, approval):
    """Revalidate one receipt and close the verified result through ``fix``."""
    root = Path(root).absolute()
    safe_path(root, root)
    scope, _map_path, _hot_paths, allowed_paths = _validate_receipt(root, receipt, approval)
    result_digests = _verify_document_boundary(root, receipt, allowed_paths)
    state, findings, _events = _operational_inputs(root)
    if state["scope"]["selected"] != scope:
        raise DoctorCloseoutError("stale-preview", "state-routing-drift", "approval-revalidation")
    _validate_selected_finding_statuses(findings, receipt["treatments"])
    candidate = _candidate(root, receipt, result_digests=result_digests)
    candidate_state = _derive_state(root, state, candidate, receipt)
    candidate_findings = _derive_findings(copy.deepcopy(findings), receipt)
    approvals = [
        {
            "id": treatment["id"],
            "fingerprint": _normalize_digest(treatment["fingerprint"], "treatment-fingerprint"),
        }
        for treatment in receipt["treatments"]
    ]
    approved_ids = [item["id"] for item in approvals]
    event = {
        "kind": "fix",
        "completed_at": _completed_at(),
        "skill_version": state["initialized"]["skill_version"],
        "approved_ids": approved_ids,
        "score_before": receipt["score_before"],
        "score_after": candidate["score"],
        "changed_paths": allowed_paths,
        "reason": "Applied exact approved Doctor treatments.",
        "summary": "Verified the approved documentation result and closed operational continuity.",
    }
    plan = prepare_verified_closeout(
        root,
        command="fix",
        state=candidate_state,
        findings=candidate_findings,
        event=event,
        approvals=approvals,
        selected_boundary=scope,
    )
    if plan.get("status") != "approval-required":
        raise DoctorCloseoutError("stale-preview", plan.get("reason", "closeout-preparation-failed"), "transaction-preparation")

    def verifier():
        try:
            _verify_document_boundary(root, receipt, allowed_paths)
            verified = _candidate(root, receipt, result_digests=result_digests)
            return verified["score"] == candidate["score"]
        except DoctorCloseoutError:
            return False

    closeout = apply_verified_closeout(
        root,
        plan,
        approved_transaction=plan["transaction_id"],
        verification=verifier,
        use_v3_recovery=True,
    )
    if closeout.get("status") not in {
        "applied",
        "closeout-committed-cleanup-incomplete",
    }:
        return {
            "schema_version": SCHEMA_VERSION,
            "status": closeout.get("status", "closeout-failed"),
            "classification": closeout.get("classification", "closeout-failed"),
            "affected_file_count": len(allowed_paths),
            "writes": closeout.get("writes", 0),
            "successful_event_recorded": bool(closeout.get("successful_event_recorded")),
        }
    try:
        installed = _candidate(root, receipt, result_digests=result_digests)
    except DoctorCloseoutError as error:
        return {
            "schema_version": SCHEMA_VERSION,
            "status": "post-closeout-conflict",
            "classification": error.classification,
            "boundary": error.boundary,
            "affected_file_count": len(allowed_paths),
            "writes": "committed",
            "successful_event_recorded": True,
            "transaction_id": closeout["transaction_id"],
            "event_id": closeout["event_id"],
            "next_action": (
                "run Doctor recovery cleanup, then rerun Doctor"
                if closeout["status"] == "closeout-committed-cleanup-incomplete"
                else "rerun Doctor to diagnose the committed post-closeout verification conflict"
            ),
        }
    if installed["score"] != candidate["score"]:
        return {
            "schema_version": SCHEMA_VERSION,
            "status": "post-closeout-conflict",
            "classification": "installed-result-drift",
            "affected_file_count": len(allowed_paths),
            "writes": "committed",
            "successful_event_recorded": True,
            "transaction_id": closeout["transaction_id"],
            "event_id": closeout["event_id"],
            "next_action": (
                "run Doctor recovery cleanup, then rerun Doctor"
                if closeout["status"] == "closeout-committed-cleanup-incomplete"
                else "rerun Doctor to diagnose the committed post-closeout verification conflict"
            ),
        }
    return {
        "schema_version": SCHEMA_VERSION,
        "status": closeout["status"],
        "approved_ids": approved_ids,
        "affected_file_count": len(allowed_paths),
        "transaction_id": closeout["transaction_id"],
        "event_id": closeout["event_id"],
        "verification": {
            "candidate": {"status": candidate["status"], "score": candidate["score"]},
            "installed": {"status": installed["status"], "score": installed["score"], "event_last": True},
        },
        "writes": "committed",
        "successful_event_recorded": True,
        "next_action": (
            "none"
            if closeout["status"] == "applied"
            else "run Doctor recovery cleanup"
        ),
    }


__all__ = (
    "DoctorCloseoutError",
    "MAX_REQUEST_BYTES",
    "SCHEMA_VERSION",
    "apply_treatment_receipt",
    "canonical_bytes",
    "prepare_treatment_receipt",
)
