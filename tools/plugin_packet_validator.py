"""Structural and recursive safety validation for the submission-readiness packet."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence

_TYPES = {"functional", "evidence", "safety"}
_RESULT_SHAPES = {"standard"}
_RESULT_ENUM = "PASS|FAIL|INCONCLUSIVE"
_DISPOSITIONS = "accepted|rejected|clarify|not-run"
_HIDDEN_KEYS = re.compile(r"(?:chain[_-]?of[_-]?thought|reasoning[_-]?content|hidden[_-]?reasoning)", re.I)
_ABSOLUTE_PATH = re.compile(r"(?i)(?:\b[A-Z]:[\\/]|/(?:users|home)(?:/|$))")
_SECRET_KEY = re.compile(
    r"(?i)(?:api[_-]?key|(?:access|auth|session)?[_-]?token|secret|password|credential|private[_-]?key)"
)
_SECRET_VALUE = re.compile(
    r"(?i)(?:\b(?:sk|rk|ghp|github_pat|xox[baprs]-)[a-z0-9_-]{8,}\b|bearer\s+[a-z0-9._-]{12,}|-----begin\s+.+?private\s+key-----)"
)


def _walk(value, path=()):
    """Yield every mapping key and string value, including nested containers."""
    if isinstance(value, Mapping):
        for key, child in value.items():
            yield key, path + (str(key),)
            yield from _walk(child, path + (str(key),))
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        for index, child in enumerate(value):
            yield from _walk(child, path + (str(index),))
    elif isinstance(value, str):
        yield value, path


def validate_packet(packet: Mapping) -> None:
    """Raise ``ValueError`` when packet structure or recursive safety is invalid."""
    if not isinstance(packet, Mapping):
        raise ValueError("packet must be an object")
    for group in ("positive", "negative"):
        cases = packet.get(group)
        if not isinstance(cases, list):
            raise ValueError(f"{group} must be an array")
        for case in cases:
            if not isinstance(case, Mapping):
                raise ValueError("case must be an object")
            if case.get("kind") != group:
                raise ValueError("case kind mismatch")
            if case.get("type") not in _TYPES:
                raise ValueError("unsupported case type")
            if case.get("result_shape") not in _RESULT_SHAPES:
                raise ValueError("unsupported result shape")
            for field in ("id", "starter_prompt", "expected_behavior"):
                if not isinstance(case.get(field), str) or not case[field]:
                    raise ValueError(f"invalid case field: {field}")
    schema = packet.get("result_schema")
    expected = {
        "case_id": "string",
        "result": _RESULT_ENUM,
        "visible_output": "string",
        "evidence": "array of repository-relative evidence",
        "file_line": "repository-relative file:line",
        "diff": "visible diff or none",
        "tool_events": "visible command/tool events or none",
        "disposition": _DISPOSITIONS,
        "status": _RESULT_ENUM,
        "limitations": "string",
    }
    if not isinstance(schema, Mapping) or dict(schema) != expected:
        raise ValueError("result_schema does not match the exact documented schema")
    for value, path in _walk(packet):
        text = str(value)
        if _ABSOLUTE_PATH.search(text):
            raise ValueError(f"absolute path at {'/'.join(path)}")
        if _SECRET_VALUE.search(text):
            raise ValueError(f"credential-like value at {'/'.join(path)}")
        if _HIDDEN_KEYS.search(text) and path and path[-1] != "starter_prompt":
            # Safety wording in prompts/Markdown may mention hidden reasoning; schema keys may not.
            if isinstance(packet, Mapping) and path[-1] not in {"expected_behavior", "starter_prompt"}:
                raise ValueError(f"hidden-reasoning key at {'/'.join(path)}")
        if path and _SECRET_KEY.fullmatch(path[-1]):
            raise ValueError(f"credential-like key at {'/'.join(path)}")
