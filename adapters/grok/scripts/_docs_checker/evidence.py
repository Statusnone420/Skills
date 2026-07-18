"""Versioned, sanitized product-evidence receipts.

This module stores facts, explicit absence, and lane provenance.  It never
accepts transcript-shaped data or calculates the deterministic health score.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import string
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
_OMITTED = object()

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
_URI_SCHEME = re.compile(r"[A-Za-z][A-Za-z0-9+.-]*:")
_ABSOLUTE_IDENTIFIER = re.compile(r"(?:^|[^A-Za-z0-9])/")
_PRIVATE_LOCAL = re.compile(r"(?i)(?<![A-Za-z0-9_.-])\.local(?:[\\/]|$)")
_WINDOWS_ROOTED = re.compile(r"(?<![A-Za-z0-9_.\\/-])\\(?!\\)[^\s]+")
_CREDENTIAL_PARAMETER = re.compile(
    r"(?:^|[\\/?&#;])[^=\\/&#;]*(?:api[_-]?key|authorization|credential|password|secret|token)[^=\\/&#;]*=",
    re.IGNORECASE,
)
_CREDENTIAL_VALUE = re.compile(
    r"(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}|gsk_[A-Za-z0-9_-]{20,}|r8_[A-Za-z0-9_-]{37,}|(?:sk|rk)-[A-Za-z0-9_-]{20,}|(?:sk|rk)_(?:live|test)_[A-Za-z0-9_-]{12,}|sk-ant-[A-Za-z0-9_-]{20,}|glpat-[A-Za-z0-9_-]{20,}|(?:glsa|gldt)_[A-Za-z0-9_-]{20,}|npm_[A-Za-z0-9]{20,}|pypi-[A-Za-z0-9_-]{20,}|hf_[A-Za-z0-9]{20,}|lin_api_[A-Za-z0-9_-]{12,}|ya29\.[A-Za-z0-9_-]{12,}|SG\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}|AIza[0-9A-Za-z_-]{20,}|xox[baprs]-[A-Za-z0-9-]{10,}|(?:AKIA|ASIA)[0-9A-Z]{16}|(?:shpat|shpca|shppa|shpss)_[A-Za-z0-9_-]{20,}|(?:sq0atp|sq0csp)-[A-Za-z0-9_-]{20,}|dop_v1_[A-Za-z0-9_-]{20,}|(?:vercel|sbp)_[A-Za-z0-9_-]{20,}|bearer\s+[A-Za-z0-9._-]{12,}|eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}|-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----)",
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


def _enum_text(value, allowed, name):
    if not isinstance(value, str) or value not in allowed:
        raise ValueError(f"{name} is invalid")
    return value


def evidence_value(status, value=None):
    """Create one explicit evidence value without treating absence as zero."""
    _enum_text(status, EVIDENCE_STATES, "evidence status")
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
    value = _bounded_text(value, name, pattern=_SAFE_ID)
    for current in _decoded_forms(value, name):
        if _ABSOLUTE_IDENTIFIER.search(current):
            raise ValueError(f"{name} exposes an absolute or private path")
    return value


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
    _enum_text(status, EVIDENCE_STATES, f"{name}.status")
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


def validate_relative_evidence_path(value, name="path"):
    """Validate a repository-relative path before including it in public evidence."""
    return _relative(value, name)


def _route(value, name):
    value = _bounded_text(value, name)
    for current in _decoded_forms(value, name):
        candidate = current.strip()
        before_fragment = candidate.split("#", 1)[0]
        if _URI_SCHEME.search(before_fragment):
            raise ValueError(f"{name} must be a local route")
        if _CREDENTIAL_PARAMETER.search(current):
            raise ValueError(f"{name} exposes credential-shaped data")
        route_text = (
            candidate[1:]
            if candidate.startswith("/") and not candidate.startswith("//")
            else candidate
        )
        if shared_text_exposes_route(route_text):
            raise ValueError(f"{name} exposes a private or unsafe route")
    return value


def _reject_forbidden_keys(value, name="receipt"):
    pending = [(value, name)]
    while pending:
        current, current_name = pending.pop()
        if isinstance(current, Mapping):
            for key, child in current.items():
                if not isinstance(key, str) or _FORBIDDEN_KEY.search(key):
                    raise ValueError(f"{current_name} contains a forbidden field")
                pending.append((child, f"{current_name}.{key}"))
        elif isinstance(current, Sequence) and not isinstance(current, (str, bytes, bytearray)):
            pending.extend(
                (child, f"{current_name}[{index}]")
                for index, child in enumerate(current)
            )


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
    _enum_text(value["status"], EVIDENCE_STATES, f"{name}.status")
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
    _enum_text(value["status"], EVIDENCE_STATES, "evidence.unresolved.status")
    candidates = _sequence(value["candidates"], "evidence.unresolved.candidates")
    if len(candidates) > 1_000:
        raise ValueError("evidence.unresolved.candidates exceeds capacity")
    for index, candidate in enumerate(candidates):
        candidate = _exact_keys(
            candidate, {"kind", "status"}, f"evidence.unresolved.candidates[{index}]"
        )
        _safe_identifier(candidate["kind"], f"evidence.unresolved.candidates[{index}].kind")
        _enum_text(
            candidate["status"],
            {"not_assessed", "unavailable", "failed"},
            "unresolved candidate status",
        )


def _validate_doctor(value):
    value = _exact_keys(
        value, {"status", "treatment_fingerprint", "approval_line_present"}, "doctor"
    )
    _enum_text(value["status"], EVIDENCE_STATES, "doctor.status")
    _evidence(value["treatment_fingerprint"], "doctor.treatment_fingerprint", validator=_digest)
    _evidence(value["approval_line_present"], "doctor.approval_line_present", validator=_boolean)
    return value


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
    _enum_text(health["status"], EVIDENCE_STATES, "health.status")
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

    _validate_doctor(value["doctor"])

    write_audit = _exact_keys(
        value["write_audit"], {"status", "writes_attempted", "writes_observed"}, "write_audit"
    )
    _enum_text(write_audit["status"], EVIDENCE_STATES, "write_audit.status")
    _evidence(write_audit["writes_attempted"], "write_audit.writes_attempted", validator=_integer)
    _evidence(write_audit["writes_observed"], "write_audit.writes_observed", validator=_integer)
    audit_completed = all(
        write_audit[field]["status"] == "completed"
        for field in ("writes_attempted", "writes_observed")
    )
    if (write_audit["status"] == "completed") != audit_completed:
        raise ValueError("write_audit.status does not match its evidence")

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


def _has_column_zero_frontmatter_opener(text):
    source = text.removeprefix("\ufeff")
    first_line = re.split(r"\r\n|\r|\n", source, maxsplit=1)[0]
    return (
        not first_line.startswith((" ", "\t"))
        and first_line.rstrip(" \t") == "---"
    )


def _markdown_body_lines(text):
    """Return body lines after bounded frontmatter, or None when its boundary is unresolved."""
    source = text.removeprefix("\ufeff")
    lines = []
    start = 0
    for match in re.finditer(r"\r\n|\r|\n", source):
        lines.append(source[start : match.end()])
        start = match.end()
    if start < len(source):
        lines.append(source[start:])
    if not lines or not _has_column_zero_frontmatter_opener(text):
        return lines
    region_bytes = 0
    for index, line in enumerate(lines):
        region_bytes += len(line.encode("utf-8", "strict"))
        if region_bytes > MAX_FRONTMATTER_BYTES:
            return None
        delimiter = line.rstrip("\r\n")
        if (
            index
            and not delimiter.startswith((" ", "\t"))
            and delimiter.rstrip(" \t") in {"---", "..."}
        ):
            return lines[index + 1 :]
    return None


def _comment_remains_open(line, opening, closing, already_open=False):
    position = 0
    if already_open:
        end = line.find(closing)
        if end < 0:
            return True
        position = end + len(closing)
    while True:
        start = _find_unescaped(line, opening, position)
        if start < 0:
            return False
        if opening == "<!--":
            if line.startswith("<!--->", start):
                position = start + len("<!--->")
                continue
            if (
                start > len(line) - len(line.lstrip(" \t"))
                and line.startswith("<!-->", start)
            ):
                position = start + len("<!-->")
                continue
        end = line.find(closing, start + len(opening))
        if end < 0:
            return True
        position = end + len(closing)


def _comment_close_end(line, start, opening, closing):
    if opening == "<!--":
        if line.startswith("<!--->", start):
            return start + len("<!--->")
        if start > len(line) - len(line.lstrip(" \t")) and line.startswith("<!-->", start):
            return start + len("<!-->")
    end = line.find(closing, start + len(opening))
    return -1 if end < 0 else end + len(closing)


def _tag_close_end(line, start):
    quote = None
    escaped = False
    for index in range(start, len(line)):
        char = line[index]
        if quote is not None:
            if char == "\\" and not escaped:
                escaped = True
                continue
            if char == quote and not escaped:
                quote = None
            escaped = False
            continue
        if char in {'"', "'"}:
            quote = char
        elif char == "{":
            return -1
        elif char == ">":
            return index + 1
    return -1


def _find_unescaped(value, token, start=0):
    position = start
    while True:
        position = value.find(token, position)
        if position < 0:
            return -1
        slashes = 0
        cursor = position - 1
        while cursor >= 0 and value[cursor] == "\\":
            slashes += 1
            cursor -= 1
        if slashes % 2 == 0:
            return position
        position += len(token)


def _find_tick_run(value, length, start=0):
    marker = "`" * length
    position = start
    while True:
        position = value.find(marker, position)
        if position < 0:
            return -1
        before = position > 0 and value[position - 1] == "`"
        after_position = position + length
        after = after_position < len(value) and value[after_position] == "`"
        if not before and not after:
            return position
        position = after_position


def _without_inline_code_spans(line, open_length=0):
    """Mask CommonMark backtick spans on one line and return multiline state."""
    masked = list(line)
    index = 0
    if open_length:
        close = _find_tick_run(line, open_length)
        if close < 0:
            for position, char in enumerate(masked):
                if char not in "\r\n":
                    masked[position] = "x"
            return "".join(masked), open_length
        for position in range(close + open_length):
            if masked[position] not in "\r\n":
                masked[position] = "x"
        index = close + open_length
    while index < len(line):
        start = _find_unescaped(line, "`", index)
        if start < 0:
            break
        index = start
        if line[index] != "`":
            index += 1
            continue
        while index < len(line) and line[index] == "`":
            index += 1
        length = index - start
        close = _find_tick_run(line, length, index)
        if close < 0:
            for position in range(start, len(masked)):
                if masked[position] not in "\r\n":
                    masked[position] = "x"
            return "".join(masked), length
        for position in range(start, close + length):
            if masked[position] not in "\r\n":
                masked[position] = "x"
        index = close + length
    return "".join(masked), 0


def _is_safe_single_line_js_string(value):
    """Validate the bounded JavaScript string forms accepted as inert ESM."""
    if len(value) < 2 or value[0] not in {'"', "'"} or value[-1] != value[0]:
        return False
    content = value[1:-1]
    index = 0
    while index < len(content):
        char = content[index]
        if char in "\r\n\u2028\u2029":
            return False
        if char != "\\":
            index += 1
            continue
        if index + 1 >= len(content):
            return False
        escaped = content[index + 1]
        if escaped in "123456789":
            return False
        if escaped == "0":
            if index + 2 < len(content) and content[index + 2].isdigit():
                return False
            index += 2
            continue
        if escaped == "x":
            digits = content[index + 2 : index + 4]
            if len(digits) != 2 or any(char not in string.hexdigits for char in digits):
                return False
            index += 4
            continue
        if escaped == "u":
            if index + 2 < len(content) and content[index + 2] == "{":
                end = content.find("}", index + 3)
                digits = content[index + 3 : end] if end >= 0 else ""
                if (
                    not 1 <= len(digits) <= 6
                    or any(char not in string.hexdigits for char in digits)
                    or int(digits, 16) > 0x10FFFF
                ):
                    return False
                index = end + 1
                continue
            digits = content[index + 2 : index + 6]
            if len(digits) != 4 or any(char not in string.hexdigits for char in digits):
                return False
            index += 6
            continue
        index += 2
    return True


def _is_single_line_mdx_esm(line):
    stripped = line.strip()
    simple_import = re.fullmatch(
        r"import(?:\s+(?P<import_binding>[A-Za-z_$][A-Za-z0-9_$]*)\s+from)?\s+"
        r"(?P<import_value>\"(?:\\[^\r\n]|[^\"\\\r\n])+\"|"
        r"'(?:\\[^\r\n]|[^'\\\r\n])+')\s*;?",
        stripped,
    )
    simple_export = re.fullmatch(
        r"export\s+const\s+(?P<export_binding>[A-Za-z_$][A-Za-z0-9_$]*)\s*=\s*"
        r"(?P<export_value>\"(?:\\[^\r\n]|[^\"\\\r\n])*\"|"
        r"'(?:\\[^\r\n]|[^'\\\r\n])*')\s*;?",
        stripped,
    )
    binding = None
    value = None
    if simple_import is not None:
        binding = simple_import.group("import_binding")
        value = simple_import.group("import_value")
    elif simple_export is not None:
        binding = simple_export.group("export_binding")
        value = simple_export.group("export_value")
    else:
        return False
    return (
        (binding is None or binding not in _JS_RESERVED_BINDINGS)
        and _is_safe_single_line_js_string(value)
    )


_JS_RESERVED_BINDINGS = frozenset(
    {
        "arguments", "await", "break", "case", "catch", "class", "const", "continue",
        "debugger", "default", "delete", "do", "else", "enum", "eval", "export", "extends",
        "false", "finally", "for", "function", "if", "implements", "import", "in",
        "instanceof", "interface", "let", "new", "null", "package", "private", "protected",
        "public", "return", "static", "super", "switch", "this", "throw", "true", "try",
        "typeof", "var", "void", "while", "with", "yield",
    }
)


_RAW_HTML_BLANK_TAGS = frozenset(
    {
        "address", "article", "aside", "base", "basefont", "blockquote", "body", "caption", "center",
        "col", "colgroup", "dd", "details", "dialog", "dir", "div", "dl", "dt",
        "fieldset", "figcaption", "figure", "footer", "form", "frame", "frameset",
        "h1", "h2", "h3", "h4", "h5", "h6", "head", "header", "hr", "html",
        "iframe", "legend", "li", "link", "main", "menu", "menuitem", "nav",
        "noframes", "ol", "optgroup", "option", "p", "param", "search", "section",
        "summary", "table", "tbody", "td", "tfoot", "th", "thead", "title", "tr",
        "track", "ul",
    }
)


def _fence_marker(line):
    """Return a CommonMark-style fence marker and suffix for up to three spaces."""
    match = re.match(r"^( {0,3})(`{3,}|~{3,})(.*)$", line.rstrip("\r\n"))
    if match is None:
        return None
    marker = match.group(2)
    return marker[0], len(marker), match.group(3)


def _leading_indent_columns(line):
    """Count leading Markdown indentation columns with four-column tab stops."""
    columns = 0
    for char in line:
        if char == " ":
            columns += 1
        elif char == "\t":
            columns += 4 - (columns % 4)
        else:
            break
    return columns


def _is_ascii_blank_line(line):
    return not line.rstrip("\r\n").strip(" \t")


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
        with path.open("r", encoding="utf-8", newline="") as stream:
            text = stream.read()
    except (OSError, UnicodeError, ValueError):
        return {
            "literal_h1": evidence_value("unavailable"),
            "frontmatter_title": evidence_value("unavailable"),
            "provider_rendered_title": evidence_value("unavailable"),
        }
    body_lines = _markdown_body_lines(text)
    frontmatter_opened = _has_column_zero_frontmatter_opener(text)
    literal_h1 = None
    if body_lines is not None:
        component_document = Path(relative).suffix.casefold() == ".mdx"
        fence = None
        inline_code_length = 0
        in_html_comment = False
        html_comment_block = False
        in_mdx_comment = False
        raw_html_tag = None
        raw_html_terminator = None
        raw_html_until_blank = False
        in_mdx_esm = False
        simple_mdx_esm_pending = False
        in_mdx_expression = False
        uncertain = False
        literal_h1 = False
        for raw_line in body_lines:
            line = raw_line.rstrip("\r\n")
            if simple_mdx_esm_pending:
                if _is_ascii_blank_line(line):
                    simple_mdx_esm_pending = False
                    continue
                if re.match(
                    r"^(?:import|export)(?:\s|\{|$)", line
                ) and _is_single_line_mdx_esm(line):
                    continue
                uncertain = True
                in_mdx_esm = True
                continue
            if in_html_comment:
                if not html_comment_block and (
                    _is_ascii_blank_line(line)
                    or (
                        _leading_indent_columns(line) < 4
                        and re.match(
                            r"^#(?:[ \t]+|$)", line.lstrip(" \t")
                        )
                    )
                ):
                    in_html_comment = False
                    html_comment_block = False
                    if _is_ascii_blank_line(line):
                        continue
                else:
                    in_html_comment = _comment_remains_open(
                        line, "<!--", "-->", True
                    )
                    if not in_html_comment:
                        html_comment_block = False
                    continue
            if in_mdx_comment:
                in_mdx_comment = _comment_remains_open(line, "{/*", "*/}", True)
                continue
            if raw_html_tag is not None:
                if re.search(
                    r"</(?:pre|script|style|textarea)[ \t]*>", line, re.IGNORECASE
                ):
                    raw_html_tag = None
                continue
            if raw_html_terminator is not None:
                if raw_html_terminator in line:
                    raw_html_terminator = None
                continue
            if raw_html_until_blank:
                if _is_ascii_blank_line(line):
                    raw_html_until_blank = False
                continue
            if in_mdx_esm or in_mdx_expression:
                continue
            list_item = re.match(
                r"^ {0,3}(?:[-+*]|[0-9]{1,9}[.)])[ \t]+(.*)$",
                line.rstrip("\r\n"),
            )
            if list_item is not None:
                nested = list_item.group(1)
                nested_fence = _fence_marker(nested)
                nested_raw = re.match(
                    r"^<(?:pre|script|style|textarea)(?:[ \t]|>|$)",
                    nested,
                    re.IGNORECASE,
                ) or re.match(
                    r"^</?([A-Za-z][A-Za-z0-9-]*)(?:[ \t]|/?>|$)", nested
                )
                if (
                    nested_fence is not None
                    and (
                        nested_fence[0] != "`" or "`" not in nested_fence[2]
                    )
                ) or nested_raw is not None:
                    uncertain = True
                    break
            marker = _fence_marker(line)
            if fence is not None:
                if (
                    marker is not None
                    and marker[0] == fence[0]
                    and marker[1] >= fence[1]
                    and not marker[2].strip(" \t")
                ):
                    fence = None
                continue
            if inline_code_length:
                interrupt = line.lstrip(" \t")
                interrupt_marker = _fence_marker(line)
                if _is_ascii_blank_line(line):
                    inline_code_length = 0
                    continue
                if (
                    re.match(r"^#(?:[ \t]+|$)", interrupt)
                    or (
                        interrupt_marker is not None
                        and (
                            interrupt_marker[0] != "`"
                            or "`" not in interrupt_marker[2]
                        )
                    )
                    or re.match(r"^</?[A-Za-z]", interrupt)
                    or interrupt.startswith(("<!--", "<?", "<![CDATA["))
                    or (
                        component_document
                        and (
                            interrupt.startswith("{")
                            or re.match(r"^(?:import|export)(?:\s|\{|$)", interrupt)
                        )
                    )
                ):
                    inline_code_length = 0
            if inline_code_length == 0 and marker is not None:
                if marker[0] != "`" or "`" not in marker[2]:
                    fence = (marker[0], marker[1])
                continue
            if inline_code_length == 0 and _leading_indent_columns(line) >= 4:
                continue
            pre_stripped = line.lstrip(" \t")
            if component_document and re.match(
                r"^(?:import|export)(?:\s|\{|$)", pre_stripped
            ):
                if _is_single_line_mdx_esm(pre_stripped):
                    simple_mdx_esm_pending = True
                else:
                    uncertain = True
                    in_mdx_esm = True
                continue
            jsx_match = re.match(
                r"^</?([A-Za-z][A-Za-z0-9._-]*)(?:[ \t]|/?>|$)", pre_stripped
            )
            if component_document and jsx_match is not None:
                jsx_end = _tag_close_end(pre_stripped, 0)
                if jsx_end < 0 or pre_stripped[jsx_end:].strip(" \t\r\n"):
                    uncertain = True
                    in_mdx_expression = True
                continue
            raw_match = re.match(
                r"^<(pre|script|style|textarea)(?:[ \t]|>|$)",
                pre_stripped,
                re.IGNORECASE,
            )
            if raw_match is not None:
                if re.search(
                    r"</(?:pre|script|style|textarea)[ \t]*>",
                    pre_stripped,
                    re.IGNORECASE,
                ) is None:
                    raw_html_tag = raw_match.group(1)
                continue
            if pre_stripped.startswith("<?"):
                if "?>" not in pre_stripped[2:]:
                    raw_html_terminator = "?>"
                continue
            if pre_stripped.startswith("<![CDATA["):
                if "]]>" not in pre_stripped[9:]:
                    raw_html_terminator = "]]>"
                continue
            if re.match(r"^<![A-Za-z]", pre_stripped):
                if ">" not in pre_stripped[2:]:
                    raw_html_terminator = ">"
                continue
            block_match = re.match(
                r"^</?([A-Za-z][A-Za-z0-9-]*)(?:[ \t]|/?>|$)", pre_stripped
            )
            if (
                block_match is not None
                and block_match.group(1).casefold() in _RAW_HTML_BLANK_TAGS
                and not (
                    component_document and block_match.group(1)[0].isupper()
                )
            ):
                raw_html_until_blank = True
                continue
            if component_document and block_match is not None:
                jsx_end = _tag_close_end(pre_stripped, 0)
                if jsx_end < 0 or pre_stripped[jsx_end:].strip(" \t\r\n"):
                    uncertain = True
                    in_mdx_expression = True
                continue
            if not component_document and block_match is not None:
                uncertain = True
                raw_html_until_blank = True
                continue
            leading = len(line) - len(line.lstrip(" \t"))
            html_comment = _find_unescaped(line, "<!--")
            if html_comment == leading:
                in_html_comment = _comment_remains_open(line, "<!--", "-->")
                html_comment_block = in_html_comment
                comment_end = _comment_close_end(line, html_comment, "<!--", "-->")
                remainder = line[comment_end:] if comment_end >= 0 else ""
                if (
                    not in_html_comment
                    and component_document
                    and (
                        _find_unescaped(remainder, "{") >= 0
                        or re.search(r"</?[A-Za-z]", remainder) is not None
                    )
                ):
                    uncertain = True
                    in_mdx_expression = component_document
                continue
            mdx_comment = _find_unescaped(line, "{/*") if component_document else -1
            if mdx_comment == leading:
                in_mdx_comment = _comment_remains_open(line, "{/*", "*/}")
                comment_end = _comment_close_end(line, mdx_comment, "{/*", "*/}")
                remainder = line[comment_end:] if comment_end >= 0 else ""
                if (
                    not in_mdx_comment
                    and (
                        _find_unescaped(remainder, "{") >= 0
                        or _find_unescaped(remainder, "<!--") >= 0
                        or re.search(r"</?[A-Za-z]", remainder) is not None
                    )
                ):
                    uncertain = True
                    in_mdx_expression = True
                continue
            if component_document and _find_unescaped(pre_stripped, "{") == 0:
                uncertain = True
                in_mdx_expression = True
                continue
            visible_line, inline_code_length = _without_inline_code_spans(
                line, inline_code_length
            )
            stripped = visible_line.lstrip(" \t")
            if not stripped:
                continue
            if re.match(r"^#(?:[ \t]+|$)", stripped):
                literal_h1 = True
                break
            scan_line = visible_line
            while True:
                inline_tag = re.search(
                    r"</?[A-Za-z][A-Za-z0-9._-]*(?:[ \t]|/?>|$)", scan_line
                )
                html_comment = _find_unescaped(scan_line, "<!--")
                mdx_comment = (
                    _find_unescaped(scan_line, "{/*") if component_document else -1
                )
                brace = _find_unescaped(scan_line, "{") if component_document else -1
                candidates = [
                    (position, priority, kind)
                    for position, priority, kind in (
                        (html_comment, 0, "html-comment"),
                        (mdx_comment, 0, "mdx-comment"),
                        (inline_tag.start() if inline_tag is not None else -1, 1, "tag"),
                        (brace, 2, "expression"),
                    )
                    if position >= 0
                ]
                if not candidates:
                    break
                position, _, kind = min(candidates)
                if kind == "tag":
                    end = _tag_close_end(scan_line, position)
                    if end < 0:
                        uncertain = True
                        in_mdx_expression = component_document
                        break
                    scan_line = (
                        scan_line[:position]
                        + "x" * (end - position)
                        + scan_line[end:]
                    )
                    continue
                if kind == "html-comment":
                    end = _comment_close_end(scan_line, position, "<!--", "-->")
                    if end < 0:
                        in_html_comment = True
                        html_comment_block = False
                        break
                    scan_line = (
                        scan_line[:position] + "x" * (end - position) + scan_line[end:]
                    )
                    continue
                if kind == "mdx-comment":
                    end = _comment_close_end(scan_line, position, "{/*", "*/}")
                    if end < 0:
                        in_mdx_comment = True
                        break
                    scan_line = (
                        scan_line[:position] + "x" * (end - position) + scan_line[end:]
                    )
                    continue
                uncertain = True
                in_mdx_expression = True
                break
        if literal_h1 is False and (
            uncertain or in_mdx_esm or in_mdx_expression or inline_code_length
        ):
            literal_h1 = None
    if body_lines is None:
        frontmatter_title = evidence_value("unavailable")
    else:
        metadata = (
            parse_frontmatter_scalars(text[: MAX_FRONTMATTER_BYTES + 1])
            if frontmatter_opened
            else {"status": "absent", "values": {}, "unresolved": []}
        )
        title = metadata.get("values", {}).get("title")
        unresolved_metadata = set(metadata.get("unresolved", ()))
        if isinstance(title, str) and "title" not in unresolved_metadata:
            frontmatter_title = evidence_value("completed", bool(title.strip()))
        elif metadata.get("status") in {"absent", "measured"}:
            frontmatter_title = evidence_value("completed", False)
        else:
            frontmatter_title = evidence_value("unavailable")
    return {
        "literal_h1": evidence_value("completed", literal_h1)
        if literal_h1 is not None
        else evidence_value("unavailable"),
        "frontmatter_title": frontmatter_title,
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
    doctor=_OMITTED,
    writes_attempted=0,
    writes_observed=0,
    git_before="clean",
    git_after="clean",
):
    """Build one receipt from existing deterministic checker evidence."""
    checker_payload = _mapping(checker_payload, "checker payload")
    navigation = _mapping(checker_payload.get("navigation", {}), "checker payload.navigation")
    _validate_run(run)
    run = dict(run)
    orientation = _exact_keys(
        orientation,
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
    semantic = _exact_keys(semantic, {"status", "evaluator", "findings"}, "semantic")
    _validate_lane(semantic, "semantic", semantic=True)
    unresolved = list(_sequence(unresolved, "unresolved"))
    _validate_unresolved({"status": "completed", "candidates": unresolved})
    if doctor is _OMITTED:
        doctor = {
            "status": "not_assessed",
            "treatment_fingerprint": evidence_value("not_assessed"),
            "approval_line_present": evidence_value("not_assessed"),
        }
    else:
        doctor = dict(_validate_doctor(doctor))
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

    semantic_lane = {
        "status": semantic["status"],
        "evaluator": dict(semantic["evaluator"]),
        "findings": list(semantic["findings"]),
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
        "run": run,
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
            "status": "completed" if writes_observed is not None else "unavailable",
            "writes_attempted": evidence_value("completed", writes_attempted),
            "writes_observed": evidence_value("completed", writes_observed)
            if writes_observed is not None
            else evidence_value("unavailable"),
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
