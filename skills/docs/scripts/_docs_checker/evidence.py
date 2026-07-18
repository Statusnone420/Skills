"""Versioned, sanitized product-evidence receipts.

This module stores facts, explicit absence, and lane provenance.  It never
accepts transcript-shaped data or calculates the deterministic health score.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections.abc import Mapping, Sequence
from pathlib import Path
from urllib.parse import unquote

from .formats import MAX_FRONTMATTER_BYTES, parse_frontmatter_scalars
from .paths import normalize_repo_relative, safe_path, shared_text_exposes_route


EVIDENCE_RECEIPT_VERSION = 1
EVIDENCE_STATES = frozenset({"completed", "not_assessed", "unavailable", "failed"})
MAX_RECEIPT_BYTES = 512 * 1024
MAX_TEXT_BYTES = 512
MAX_FINDINGS = 10_000

_SHA = re.compile(r"^[0-9a-f]{40}$")
_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")
_SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/@+-]{0,255}$")
_FORBIDDEN_KEY = re.compile(
    r"(?:api[_-]?key|authorization|credential|hidden[_-]?reasoning|password|private[_-]?path|raw[_-]?transcript|screenshot|secret|token)",
    re.IGNORECASE,
)
_WINDOWS_ABSOLUTE = re.compile(r"(?<![A-Za-z0-9_.\\/-])[A-Za-z]:[\\/]")
_PRIVATE_POSIX_ABSOLUTE = re.compile(
    r"(?<![A-Za-z0-9_./-])/(?:home|root|etc|var|tmp|private|mnt|media|opt|usr|bin|sbin|dev|proc|sys|run|srv|boot|Users|Volumes|Applications|System|Library)(?:/|$)",
    re.IGNORECASE,
)
_NETWORK_PATH = re.compile(r"(?<![A-Za-z0-9_.\\/-])(?://|\\\\)")
_URI_SCHEME = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*:")
_FILE_SCHEME = re.compile(r"(?<![A-Za-z0-9+.-])file:", re.IGNORECASE)
_PRIVATE_LOCAL = re.compile(r"(?i)(?<![A-Za-z0-9_.-])\.local(?:[\\/]|$)")
_WINDOWS_ROOTED = re.compile(r"(?<![A-Za-z0-9_.\\/-])\\(?!\\)[^\s]+")
_CREDENTIAL_PARAMETER = re.compile(
    r"(?:^|[\\/?&#;])[^=\\/&#;]*(?:api[_-]?key|authorization|credential|password|secret|token)[^=\\/&#;]*=",
    re.IGNORECASE,
)
_CREDENTIAL_VALUE = re.compile(
    r"(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}|(?:sk|rk)-[A-Za-z0-9_-]{20,}|glpat-[A-Za-z0-9_-]{20,}|npm_[A-Za-z0-9]{20,}|pypi-[A-Za-z0-9_-]{20,}|AIza[0-9A-Za-z_-]{20,}|xox[baprs]-[A-Za-z0-9-]{10,}|AKIA[0-9A-Z]{16}|bearer\s+[A-Za-z0-9._-]{12,}|eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}|-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----)",
    re.IGNORECASE,
)
_CATEGORY_RAW_FIELDS = {
    "entry": (
        "map_exists",
        "map_has_h1",
        "map_has_body",
        "map_has_h2",
        "valid_navigation_routes",
        "complete_single_document",
        "useful_entry",
    ),
    "path_safety": ("safe", "maintained"),
    "links": ("valid", "checked"),
    "anchors": ("valid", "checked"),
    "reachability": ("reachable", "maintained"),
    "titles": ("usable_unique", "maintained"),
}
_RECEIPT_FIELDS = frozenset(
    {
        "schema_version",
        "receipt_id",
        "repository",
        "checker",
        "run",
        "surface",
        "counts",
        "orientation",
        "health",
        "evidence",
        "doctor",
        "write_audit",
        "git",
        "unavailable_evidence",
    }
)


def evidence_value(status, value=None):
    """Create one explicit evidence value without treating absence as zero."""
    if status not in EVIDENCE_STATES:
        raise ValueError("evidence status is invalid")
    if status == "completed" and value is None:
        raise ValueError("completed evidence requires a value")
    if status != "completed" and value is not None:
        raise ValueError("incomplete evidence value must be null")
    return {"status": status, "value": value}


def _mapping(value, name):
    if not isinstance(value, Mapping):
        raise ValueError(f"{name} must be an object")
    return value


def _sequence(value, name):
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        raise ValueError(f"{name} must be an array")
    return value


def _exact_keys(value, expected, name):
    value = _mapping(value, name)
    if set(value) != set(expected):
        raise ValueError(f"{name} fields are invalid")
    return value


def _decoded_forms(value, name):
    current = value
    for _ in range(MAX_TEXT_BYTES + 1):
        yield current
        decoded = unquote(current)
        if decoded == current:
            return
        current = decoded
    raise ValueError(f"{name} has excessive encoding depth")


def _bounded_text(value, name, *, pattern=None, allow_empty=False):
    if not isinstance(value, str) or (not value and not allow_empty):
        raise ValueError(f"{name} must be text")
    if len(value.encode("utf-8", "strict")) > MAX_TEXT_BYTES:
        raise ValueError(f"{name} exceeds capacity")
    if any(ord(char) < 32 for char in value):
        raise ValueError(f"{name} contains control characters")
    for current in _decoded_forms(value, name):
        if (
            _WINDOWS_ABSOLUTE.search(current)
            or _PRIVATE_POSIX_ABSOLUTE.search(current)
            or _NETWORK_PATH.search(current)
            or _WINDOWS_ROOTED.search(current)
            or _PRIVATE_LOCAL.search(current)
        ):
            raise ValueError(f"{name} exposes an absolute or private path")
        if _CREDENTIAL_VALUE.search(current) or _CREDENTIAL_PARAMETER.search(current):
            raise ValueError(f"{name} exposes credential-shaped data")
    if pattern is not None and pattern.fullmatch(value) is None:
        raise ValueError(f"{name} is invalid")
    return value


def _safe_identifier(value, name):
    return _bounded_text(value, name, pattern=_SAFE_ID)


def _nonnegative_number(value, name):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be numeric")
    if not math.isfinite(value) or value < 0:
        raise ValueError(f"{name} must be finite and nonnegative")
    return value


def _percentage(value, name):
    value = _integer(value, name)
    if value > 100:
        raise ValueError(f"{name} must not exceed 100")
    return value


def _evidence(value, name, *, validator=None):
    value = _exact_keys(value, {"status", "value"}, name)
    status = value["status"]
    if status not in EVIDENCE_STATES:
        raise ValueError(f"{name}.status is invalid")
    current = value["value"]
    if status == "completed":
        if current is None:
            raise ValueError(f"{name}.value is required")
        if validator is not None:
            validator(current, f"{name}.value")
    elif current is not None:
        raise ValueError(f"{name}.value must be null")
    return {"status": status, "value": current}


def _integer(value, name):
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{name} must be a nonnegative integer")
    return value


def _boolean(value, name):
    if not isinstance(value, bool):
        raise ValueError(f"{name} must be boolean")
    return value


def _sha(value, name):
    if not isinstance(value, str) or _SHA.fullmatch(value) is None:
        raise ValueError(f"{name} must be a lowercase full commit SHA")
    return value


def _digest(value, name):
    if not isinstance(value, str) or _DIGEST.fullmatch(value) is None:
        raise ValueError(f"{name} must be a SHA-256 digest")
    return value


def _relative(value, name):
    _bounded_text(value, name)
    if any(shared_text_exposes_route(current) for current in _decoded_forms(value, name)):
        raise ValueError(f"{name} exposes a private or unsafe route")
    normalized = normalize_repo_relative(value, name)
    if normalized != value:
        raise ValueError(f"{name} must be normalized")
    return value


def _route(value, name):
    value = _bounded_text(value, name)
    for current in _decoded_forms(value, name):
        if _URI_SCHEME.match(current) or _FILE_SCHEME.search(current):
            raise ValueError(f"{name} must be a local route")
        if _CREDENTIAL_PARAMETER.search(current):
            raise ValueError(f"{name} exposes credential-shaped data")
    return value


def _reject_forbidden_keys(value, name="receipt"):
    if isinstance(value, Mapping):
        for key, child in value.items():
            if not isinstance(key, str) or _FORBIDDEN_KEY.search(key):
                raise ValueError(f"{name} contains a forbidden field")
            _reject_forbidden_keys(child, f"{name}.{key}")
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        for index, child in enumerate(value):
            _reject_forbidden_keys(child, f"{name}[{index}]")


def _validate_run(value):
    value = _exact_keys(
        value,
        {"id", "client", "model_provider", "model", "effort", "turns", "duration_seconds", "commands"},
        "run",
    )
    for field in ("id", "client", "model_provider", "model", "effort"):
        _safe_identifier(value[field], f"run.{field}")
    _evidence(value["turns"], "run.turns", validator=_integer)
    _evidence(value["duration_seconds"], "run.duration_seconds", validator=_nonnegative_number)
    commands = _sequence(value["commands"], "run.commands")
    if not commands or len(commands) > 32:
        raise ValueError("run.commands count is invalid")
    for index, command in enumerate(commands):
        _safe_identifier(command, f"run.commands[{index}]")


def _validate_finding(value, name):
    value = _exact_keys(value, {"kind", "path", "line", "target", "fingerprint"}, name)
    _safe_identifier(value["kind"], f"{name}.kind")
    _evidence(value["path"], f"{name}.path", validator=_relative)
    _evidence(value["line"], f"{name}.line", validator=_integer)
    _evidence(value["target"], f"{name}.target", validator=_route)
    _evidence(value["fingerprint"], f"{name}.fingerprint", validator=_digest)


def _validate_findings(value, name):
    findings = _sequence(value, name)
    if len(findings) > MAX_FINDINGS:
        raise ValueError(f"{name} exceeds capacity")
    for index, finding in enumerate(findings):
        _validate_finding(finding, f"{name}[{index}]")


def _validate_lane(value, name, *, semantic=False):
    expected = {"status", "findings"}
    if semantic:
        expected.add("evaluator")
    value = _exact_keys(value, expected, name)
    if value["status"] not in EVIDENCE_STATES:
        raise ValueError(f"{name}.status is invalid")
    _validate_findings(value["findings"], f"{name}.findings")
    if value["status"] != "completed" and value["findings"]:
        raise ValueError(f"{name} cannot contain findings when incomplete")
    if semantic:
        evaluator = _exact_keys(
            value["evaluator"], {"provider", "model", "version"}, f"{name}.evaluator"
        )
        for field in evaluator:
            _evidence(
                evaluator[field],
                f"{name}.evaluator.{field}",
                validator=_safe_identifier,
            )


def _validate_unresolved(value):
    value = _exact_keys(value, {"status", "candidates"}, "evidence.unresolved")
    if value["status"] not in EVIDENCE_STATES:
        raise ValueError("evidence.unresolved.status is invalid")
    candidates = _sequence(value["candidates"], "evidence.unresolved.candidates")
    if len(candidates) > 1_000:
        raise ValueError("evidence.unresolved.candidates exceeds capacity")
    for index, candidate in enumerate(candidates):
        candidate = _exact_keys(
            candidate, {"kind", "status"}, f"evidence.unresolved.candidates[{index}]"
        )
        _safe_identifier(candidate["kind"], f"evidence.unresolved.candidates[{index}].kind")
        if candidate["status"] not in {"not_assessed", "unavailable", "failed"}:
            raise ValueError("unresolved candidate status is invalid")


def _validate_categories(value, *, required=False):
    value = _mapping(value, "health.categories")
    if set(value) - set(_CATEGORY_RAW_FIELDS):
        raise ValueError("health.categories contains an unknown category")
    if required and set(value) != set(_CATEGORY_RAW_FIELDS):
        raise ValueError("completed health requires every category")
    for category, fields in _CATEGORY_RAW_FIELDS.items():
        if category not in value:
            continue
        row = _exact_keys(
            value[category], {"raw", "earned", "available"}, f"health.categories.{category}"
        )
        raw = _exact_keys(row["raw"], fields, f"health.categories.{category}.raw")
        for field, evidence in raw.items():
            validator = _boolean if field in {
                "map_exists",
                "map_has_h1",
                "map_has_body",
                "map_has_h2",
                "complete_single_document",
                "useful_entry",
            } else _integer
            _evidence(evidence, f"health.categories.{category}.raw.{field}", validator=validator)
        _evidence(row["earned"], f"health.categories.{category}.earned", validator=_nonnegative_number)
        _evidence(row["available"], f"health.categories.{category}.available", validator=_nonnegative_number)
        if (
            row["earned"]["status"] == "completed"
            and row["available"]["status"] == "completed"
            and row["earned"]["value"] > row["available"]["value"]
        ):
            raise ValueError(f"health.categories.{category}.earned exceeds available")


def _collect_unavailable(value, path=""):
    results = []
    if isinstance(value, Mapping):
        if set(value) == {"status", "value"} and value["status"] != "completed":
            results.append(path)
        else:
            for key, child in value.items():
                if key == "unavailable_evidence":
                    continue
                child_path = f"{path}.{key}" if path else key
                results.extend(_collect_unavailable(child, child_path))
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        for index, child in enumerate(value):
            results.extend(_collect_unavailable(child, f"{path}[{index}]"))
    return sorted(results)


def validate_evidence_receipt(value):
    """Validate and return one exact schema-v1 sanitized receipt."""
    value = _exact_keys(value, _RECEIPT_FIELDS, "receipt")
    _reject_forbidden_keys(value)
    if value["schema_version"] != EVIDENCE_RECEIPT_VERSION:
        raise ValueError("receipt schema_version is invalid")
    _safe_identifier(value["receipt_id"], "receipt_id")

    repository = _exact_keys(value["repository"], {"identifier", "commit"}, "repository")
    _safe_identifier(repository["identifier"], "repository.identifier")
    _evidence(repository["commit"], "repository.commit", validator=_sha)

    checker = _exact_keys(value["checker"], {"name", "version"}, "checker")
    _safe_identifier(checker["name"], "checker.name")
    _safe_identifier(checker["version"], "checker.version")
    _validate_run(value["run"])

    surface = _exact_keys(
        value["surface"], {"provider", "authority", "provider_root", "entry"}, "surface"
    )
    _safe_identifier(surface["provider"], "surface.provider")
    for field in ("authority", "provider_root", "entry"):
        _evidence(surface[field], f"surface.{field}", validator=_relative)

    counts = _exact_keys(
        value["counts"],
        {"pages", "hidden_pages", "redirects", "links_checked", "links_valid", "anchors_checked", "anchors_valid"},
        "counts",
    )
    for field, current in counts.items():
        _evidence(current, f"counts.{field}", validator=_integer)

    orientation = _exact_keys(
        value["orientation"],
        {"literal_h1", "frontmatter_title", "provider_rendered_title"},
        "orientation",
    )
    _evidence(orientation["literal_h1"], "orientation.literal_h1", validator=_boolean)
    _evidence(orientation["frontmatter_title"], "orientation.frontmatter_title", validator=_boolean)
    _evidence(
        orientation["provider_rendered_title"],
        "orientation.provider_rendered_title",
        validator=_boolean,
    )

    health = _exact_keys(
        value["health"],
        {"status", "rubric_version", "percentage", "earned_weight", "available_weight", "categories", "score_gates"},
        "health",
    )
    if health["status"] not in EVIDENCE_STATES:
        raise ValueError("health.status is invalid")
    _evidence(health["rubric_version"], "health.rubric_version", validator=_integer)
    _evidence(health["percentage"], "health.percentage", validator=_percentage)
    for field in ("earned_weight", "available_weight"):
        _evidence(health[field], f"health.{field}", validator=_nonnegative_number)
    if (
        health["earned_weight"]["status"] == "completed"
        and health["available_weight"]["status"] == "completed"
        and health["earned_weight"]["value"] > health["available_weight"]["value"]
    ):
        raise ValueError("health.earned_weight exceeds available_weight")
    _validate_categories(health["categories"], required=health["status"] == "completed")
    gates = _exact_keys(health["score_gates"], {"map_has_h1", "useful_entry"}, "health.score_gates")
    for field in gates:
        _evidence(gates[field], f"health.score_gates.{field}", validator=_boolean)

    evidence = _exact_keys(
        value["evidence"], {"deterministic", "semantic", "unresolved"}, "evidence"
    )
    _validate_lane(evidence["deterministic"], "evidence.deterministic")
    _validate_lane(evidence["semantic"], "evidence.semantic", semantic=True)
    _validate_unresolved(evidence["unresolved"])

    doctor = _exact_keys(
        value["doctor"], {"status", "treatment_fingerprint", "approval_line_present"}, "doctor"
    )
    if doctor["status"] not in EVIDENCE_STATES:
        raise ValueError("doctor.status is invalid")
    _evidence(doctor["treatment_fingerprint"], "doctor.treatment_fingerprint", validator=_digest)
    _evidence(doctor["approval_line_present"], "doctor.approval_line_present", validator=_boolean)

    write_audit = _exact_keys(
        value["write_audit"], {"status", "writes_attempted", "writes_observed"}, "write_audit"
    )
    if write_audit["status"] not in EVIDENCE_STATES:
        raise ValueError("write_audit.status is invalid")
    _evidence(write_audit["writes_attempted"], "write_audit.writes_attempted", validator=_integer)
    _evidence(write_audit["writes_observed"], "write_audit.writes_observed", validator=_integer)

    git = _exact_keys(value["git"], {"before", "after"}, "git")
    for field in git:
        _evidence(
            git[field],
            f"git.{field}",
            validator=lambda current, name: _bounded_text(
                current, name, pattern=re.compile(r"^(?:clean|dirty)$")
            ),
        )

    unavailable = _sequence(value["unavailable_evidence"], "unavailable_evidence")
    for index, field in enumerate(unavailable):
        _bounded_text(field, f"unavailable_evidence[{index}]")
    expected_unavailable = _collect_unavailable(value)
    if list(unavailable) != expected_unavailable:
        raise ValueError("unavailable_evidence does not match explicit evidence states")

    encoded = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    ).encode("utf-8")
    if len(encoded) > MAX_RECEIPT_BYTES:
        raise ValueError("receipt exceeds capacity")
    return value


def canonical_receipt_bytes(value):
    validated = validate_evidence_receipt(value)
    return (
        json.dumps(
            validated,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=False,
        )
        + "\n"
    ).encode("utf-8")


def finding_receipt(kind, *, path=None, line=None, target=None, fingerprint=None):
    return {
        "kind": kind,
        "path": evidence_value("completed", path) if path is not None else evidence_value("unavailable"),
        "line": evidence_value("completed", line) if line is not None else evidence_value("unavailable"),
        "target": evidence_value("completed", target) if target is not None else evidence_value("unavailable"),
        "fingerprint": evidence_value("completed", fingerprint)
        if fingerprint is not None
        else evidence_value("unavailable"),
    }


def observe_entry_orientation(root, entry):
    """Read bounded inert text evidence; never evaluate provider or MDX code."""
    if entry is None:
        return {
            "literal_h1": evidence_value("unavailable"),
            "frontmatter_title": evidence_value("unavailable"),
            "provider_rendered_title": evidence_value("unavailable"),
        }
    root = Path(root).absolute()
    relative = normalize_repo_relative(entry, "entry")
    path = safe_path(root / relative, root)
    try:
        if path.stat().st_size > 2 * 1024 * 1024:
            raise ValueError("entry exceeds capacity")
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError, ValueError):
        return {
            "literal_h1": evidence_value("unavailable"),
            "frontmatter_title": evidence_value("unavailable"),
            "provider_rendered_title": evidence_value("unavailable"),
        }
    lines = text.removeprefix("\ufeff").splitlines()
    in_fence = False
    literal_h1 = False
    for line in lines:
        stripped = line.lstrip()
        if stripped.startswith(("```", "~~~")):
            in_fence = not in_fence
            continue
        if not in_fence and re.match(r"^#(?:\s+|$)", stripped):
            literal_h1 = True
            break
    metadata = parse_frontmatter_scalars(text[: MAX_FRONTMATTER_BYTES + 1])
    title = metadata.get("values", {}).get("title")
    return {
        "literal_h1": evidence_value("completed", literal_h1),
        "frontmatter_title": evidence_value("completed", isinstance(title, str) and bool(title.strip())),
        "provider_rendered_title": evidence_value("unavailable"),
    }


def health_receipt(health):
    """Copy existing score evidence without recalculation or reinterpretation."""
    if not isinstance(health, Mapping):
        unavailable = evidence_value("not_assessed")
        return {
            "status": "not_assessed",
            "rubric_version": unavailable.copy(),
            "percentage": unavailable.copy(),
            "earned_weight": unavailable.copy(),
            "available_weight": unavailable.copy(),
            "categories": {},
            "score_gates": {
                "map_has_h1": unavailable.copy(),
                "useful_entry": unavailable.copy(),
            },
        }
    categories = {}
    for category, raw_fields in _CATEGORY_RAW_FIELDS.items():
        source = health.get("categories", {}).get(category)
        if not isinstance(source, Mapping):
            continue
        raw = source.get("raw", {})
        categories[category] = {
            "raw": {
                field: evidence_value("completed", raw[field])
                if field in raw
                else evidence_value("unavailable")
                for field in raw_fields
            },
            "earned": evidence_value("completed", source["earned"]),
            "available": evidence_value("completed", source["available"]),
        }
    entry = health.get("categories", {}).get("entry", {}).get("raw", {})
    return {
        "status": "completed",
        "rubric_version": evidence_value("completed", health["rubric_version"]),
        "percentage": evidence_value("completed", health["percentage"]),
        "earned_weight": evidence_value("completed", health["earned_weight"]),
        "available_weight": evidence_value("completed", health["available_weight"]),
        "categories": categories,
        "score_gates": {
            "map_has_h1": evidence_value("completed", entry["map_has_h1"])
            if "map_has_h1" in entry
            else evidence_value("unavailable"),
            "useful_entry": evidence_value("completed", entry["useful_entry"])
            if "useful_entry" in entry
            else evidence_value("unavailable"),
        },
    }


def build_evidence_receipt(
    *,
    receipt_id,
    repository_identifier,
    commit,
    checker_version,
    run,
    checker_payload,
    orientation,
    semantic,
    unresolved=(),
    doctor=None,
    writes_attempted=0,
    writes_observed=0,
    git_before="clean",
    git_after="clean",
):
    """Build one receipt from existing deterministic checker evidence."""
    checker_payload = _mapping(checker_payload, "checker payload")
    navigation = checker_payload.get("navigation", {})
    health = health_receipt(checker_payload.get("health"))
    measured = isinstance(checker_payload.get("health"), Mapping)
    counts = {
        "pages": len(navigation.get("navigated_pages", ())),
        "hidden_pages": len(navigation.get("hidden_pages", ())),
        "redirects": len(navigation.get("redirects", ())),
    }
    categories = health.get("categories", {})
    for receipt_name, category, raw_name in (
        ("links_checked", "links", "checked"),
        ("links_valid", "links", "valid"),
        ("anchors_checked", "anchors", "checked"),
        ("anchors_valid", "anchors", "valid"),
    ):
        source = categories.get(category, {}).get("raw", {}).get(raw_name)
        counts[receipt_name] = source if source is not None else evidence_value("not_assessed")
    for name in ("pages", "hidden_pages", "redirects"):
        counts[name] = (
            evidence_value("completed", counts[name])
            if measured
            else evidence_value("not_assessed")
        )

    findings = []
    for raw in checker_payload.get("findings", ()):
        if not isinstance(raw, Mapping) or not isinstance(raw.get("kind"), str):
            continue
        path = raw.get("path") if isinstance(raw.get("path"), str) else raw.get("source")
        line = raw.get("line") if isinstance(raw.get("line"), int) else None
        target = raw.get("target") if isinstance(raw.get("target"), str) else None
        findings.append(finding_receipt(raw["kind"], path=path, line=line, target=target))

    semantic = _mapping(semantic, "semantic")
    evaluator = semantic.get("evaluator", {})
    semantic_lane = {
        "status": semantic["status"],
        "evaluator": {
            field: evaluator.get(field, evidence_value("not_assessed"))
            for field in ("provider", "model", "version")
        },
        "findings": list(semantic.get("findings", ())),
    }
    doctor = doctor or {
        "status": "not_assessed",
        "treatment_fingerprint": evidence_value("not_assessed"),
        "approval_line_present": evidence_value("not_assessed"),
    }
    receipt = {
        "schema_version": EVIDENCE_RECEIPT_VERSION,
        "receipt_id": receipt_id,
        "repository": {
            "identifier": repository_identifier,
            "commit": evidence_value("completed", commit)
            if commit is not None
            else evidence_value("unavailable"),
        },
        "checker": {"name": "diataxis-docs", "version": checker_version},
        "run": dict(run),
        "surface": {
            "provider": navigation.get("provider", "unknown"),
            "authority": evidence_value("completed", navigation["authority"])
            if navigation.get("authority") is not None
            else evidence_value("unavailable"),
            "provider_root": evidence_value("completed", navigation["provider_root"])
            if navigation.get("provider_root") is not None
            else evidence_value("unavailable"),
            "entry": evidence_value("completed", navigation["entry"])
            if navigation.get("entry") is not None
            else evidence_value("unavailable"),
        },
        "counts": counts,
        "orientation": dict(orientation),
        "health": health,
        "evidence": {
            "deterministic": {"status": "completed", "findings": findings},
            "semantic": semantic_lane,
            "unresolved": {"status": "completed", "candidates": list(unresolved)},
        },
        "doctor": dict(doctor),
        "write_audit": {
            "status": "completed",
            "writes_attempted": evidence_value("completed", writes_attempted),
            "writes_observed": evidence_value("completed", writes_observed),
        },
        "git": {
            "before": evidence_value("completed", git_before),
            "after": evidence_value("completed", git_after),
        },
        "unavailable_evidence": [],
    }
    receipt["unavailable_evidence"] = _collect_unavailable(receipt)
    return validate_evidence_receipt(receipt)


def config_probe(path):
    """Return bounded presence/digest evidence without parsing configuration code."""
    path = Path(path)
    try:
        size = path.stat().st_size
        if size > 2 * 1024 * 1024:
            raise ValueError("configuration exceeds capacity")
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
    except (OSError, ValueError):
        return {"status": "unavailable", "sha256": None, "bytes": None}
    return {"status": "completed", "sha256": f"sha256:{digest}", "bytes": size}


__all__ = (
    "EVIDENCE_RECEIPT_VERSION",
    "EVIDENCE_STATES",
    "MAX_RECEIPT_BYTES",
    "build_evidence_receipt",
    "canonical_receipt_bytes",
    "config_probe",
    "evidence_value",
    "finding_receipt",
    "health_receipt",
    "observe_entry_orientation",
    "validate_evidence_receipt",
)
