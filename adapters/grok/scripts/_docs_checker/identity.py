"""Stable semantic identity normalization, fingerprints, and content-derived IDs."""

import hashlib
import json
import os
import posixpath
import re
import unicodedata
from collections.abc import Mapping, Sequence
from urllib.parse import unquote


_FINGERPRINT = re.compile(r"^[0-9a-f]{8,64}$")
_FINDING_ID = re.compile(r"^DOC-([0-9A-F]{8}(?:[0-9A-F]{4})*)$")
_EVENT_ID = re.compile(r"^EVT-([0-9A-F]{8}(?:[0-9A-F]{4})*)$")
_IDENTITY_PATH_FIELDS = {
    "destination",
    "document",
    "map",
    "path",
    "source",
    "target",
}
_IDENTITY_PATH_LIST_FIELDS = {"paths", "sources", "targets"}
_IDENTITY_SCALAR_FIELDS = {
    "anchor",
    "conflict_id",
    "event_id",
    "field",
    "finding_id",
    "intent_key",
    "key",
    "title",
}
_EVENT_VOLATILE_FIELDS = {
    "absolute_path",
    "audit",
    "audit_metadata",
    "byte_offset",
    "column",
    "completed_at",
    "created_at",
    "cwd",
    "display",
    "display_metadata",
    "event_id",
    "line",
    "locator",
    "manifest",
    "metadata",
    "recorded_at",
    "timestamp",
    "updated_at",
}
_EVENT_PATH_FIELDS = _IDENTITY_PATH_FIELDS | {"changed_path"}
_EVENT_PATH_LIST_FIELDS = _IDENTITY_PATH_LIST_FIELDS | {"changed_paths"}
_EVENT_SET_LIKE_FIELDS = _EVENT_PATH_LIST_FIELDS | {
    "approved_ids",
    "discarded_ids",
    "finding_ids",
    "treatment_ids",
}


def _require_mapping(value, name):
    if not isinstance(value, Mapping):
        raise ValueError(f"{name} must be an object")
    return value


def _require_sequence(value, name):
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        raise ValueError(f"{name} must be an array")
    return value


def _require_string(value, name):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")
    return value


def slug(value):
    value = unquote(value).strip().lower()
    value = unicodedata.normalize("NFKC", value)
    return re.sub(r"[^\w -]", "", value, flags=re.UNICODE).replace(" ", "-")


def _normalize_event_id(value, name):
    normalized = _require_string(value, name).upper()
    if not _EVENT_ID.fullmatch(normalized):
        raise ValueError(f"{name} is invalid")
    return normalized


def _normalize_fingerprint(value):
    normalized = _require_string(value, "fingerprint").lower()
    if normalized.startswith("sha256:"):
        normalized = normalized[7:]
    if not _FINGERPRINT.fullmatch(normalized) or len(normalized) % 4:
        raise ValueError("fingerprint must be hexadecimal in four-character groups")
    return normalized


def _canonical_path_identity(value):
    if not isinstance(value, (str, os.PathLike)):
        return None
    text = unicodedata.normalize("NFC", os.fspath(value).strip()).replace("\\", "/")
    if not text:
        return None
    path_text, separator, fragment = text.partition("#")
    if re.match(r"^[A-Za-z]:/", path_text) or path_text.startswith("/"):
        return None
    if re.match(r"^[A-Za-z][A-Za-z0-9+.-]*:", path_text):
        return text
    normalized = posixpath.normpath(path_text) if path_text else ""
    if normalized == ".." or normalized.startswith("../"):
        return None
    if normalized == "." and path_text not in ("", "."):
        normalized = ""
    if separator:
        normalized_fragment = slug(fragment)
        return f"{normalized}#{normalized_fragment}"
    return normalized or "."


def _canonical_scalar_identity(value):
    if isinstance(value, str):
        return " ".join(unicodedata.normalize("NFC", value).split())
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return None


def _canonical_finding_evidence(item):
    if not isinstance(item, Mapping):
        raise ValueError("finding evidence must contain objects")
    identity = {}
    for key in sorted(item):
        if key in _IDENTITY_PATH_FIELDS:
            value = _canonical_path_identity(item[key])
            if value is not None:
                identity[key] = value
        elif key in _IDENTITY_PATH_LIST_FIELDS:
            values = _require_sequence(item[key], f"finding evidence {key}")
            canonical = {
                value
                for candidate in values
                if (value := _canonical_path_identity(candidate)) is not None
            }
            if canonical:
                identity[key] = sorted(canonical)
        elif key in _IDENTITY_SCALAR_FIELDS:
            value = _canonical_scalar_identity(item[key])
            if value is not None:
                identity[key] = value
    return identity


def finding_fingerprint(kind: str, evidence: Sequence[Mapping]) -> str:
    """Hash only normalized, stable semantic identity for one finding."""
    normalized_kind = "-".join(_require_string(kind, "finding kind").casefold().split())
    try:
        canonical_evidence = {
            json.dumps(
                _canonical_finding_evidence(item),
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=True,
                allow_nan=False,
            )
            for item in _require_sequence(evidence, "finding evidence")
        }
    except ValueError as exc:
        raise ValueError("canonical finding evidence is malformed JSON") from exc
    payload = {
        "kind": normalized_kind,
        "evidence": [json.loads(item) for item in sorted(canonical_evidence)],
    }
    canonical = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def finding_id(fingerprint: str, existing: Mapping[str, str]) -> str:
    """Return the shortest collision-free content-derived finding ID."""
    normalized = _normalize_fingerprint(fingerprint)
    existing = _require_mapping(existing, "existing finding identities")
    normalized_existing = {}
    for identifier, stored_fingerprint in existing.items():
        if not isinstance(identifier, str) or not _FINDING_ID.fullmatch(identifier.upper()):
            raise ValueError("existing finding ID is invalid")
        normalized_existing[identifier.upper()] = _normalize_fingerprint(stored_fingerprint)

    matches = sorted(
        (
            identifier
            for identifier, stored_fingerprint in normalized_existing.items()
            if stored_fingerprint == normalized
            and normalized.startswith(identifier.removeprefix("DOC-").lower())
        ),
        key=lambda identifier: (len(identifier), identifier),
    )
    if matches:
        return matches[0]

    for length in range(8, len(normalized) + 1, 4):
        identifier = "DOC-" + normalized[:length].upper()
        stored = normalized_existing.get(identifier)
        if stored is None or stored == normalized:
            return identifier
    raise ValueError("finding fingerprint collides at full length")


def _canonical_event_value(value, key=None, *, depth=1, active=None):
    if depth > 128:
        raise ValueError("event exceeds maximum canonical nesting")
    if active is None:
        active = set()
    if isinstance(value, Mapping):
        identity = id(value)
        if identity in active:
            raise ValueError("event contains a cyclic object")
        active.add(identity)
        try:
            return {
                str(child_key): _canonical_event_value(
                    child,
                    str(child_key),
                    depth=depth + 1,
                    active=active,
                )
                for child_key, child in sorted(
                    value.items(), key=lambda item: str(item[0])
                )
                if str(child_key) not in _EVENT_VOLATILE_FIELDS
            }
        finally:
            active.remove(identity)
    if isinstance(value, Sequence) and not isinstance(
        value, (str, bytes, bytearray)
    ):
        identity = id(value)
        if identity in active:
            raise ValueError("event contains a cyclic array")
        active.add(identity)
        try:
            items = [
                _canonical_event_value(
                    item,
                    key,
                    depth=depth + 1,
                    active=active,
                )
                for item in value
            ]
        finally:
            active.remove(identity)
        if key in _EVENT_SET_LIKE_FIELDS:
            serialized = {
                json.dumps(
                    item,
                    sort_keys=True,
                    separators=(",", ":"),
                    ensure_ascii=True,
                    allow_nan=False,
                )
                for item in items
            }
            return [json.loads(item) for item in sorted(serialized)]
        return items
    if key in _EVENT_PATH_FIELDS or key in _EVENT_PATH_LIST_FIELDS:
        normalized_path = _canonical_path_identity(value)
        if normalized_path is None:
            raise ValueError(f"event {key} must be a repository-relative path")
        return normalized_path
    if isinstance(value, str):
        return unicodedata.normalize(
            "NFC", value.replace("\r\n", "\n").replace("\r", "\n")
        ).strip()
    if value is None or isinstance(value, (bool, int, float)):
        return value
    raise ValueError("event contains unsupported semantic content")


def event_fingerprint(event: Mapping) -> str:
    """Hash normalized semantic event content, excluding audit/display metadata."""
    event = _require_mapping(event, "event")
    try:
        canonical_event = _canonical_event_value(event)
        canonical = json.dumps(
            canonical_event,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=False,
        )
    except (TypeError, ValueError, RecursionError, OverflowError) as exc:
        raise ValueError("canonical event content is malformed JSON") from exc
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def event_id(fingerprint: str, existing: Mapping[str, str] | None = None) -> str:
    """Return the shortest collision-free content-derived event ID."""
    normalized = _normalize_fingerprint(fingerprint)
    existing = {} if existing is None else _require_mapping(existing, "existing event identities")
    normalized_existing = {}
    for identifier, stored_fingerprint in existing.items():
        if not isinstance(identifier, str) or not _EVENT_ID.fullmatch(identifier.upper()):
            raise ValueError("existing event ID is invalid")
        normalized_existing[identifier.upper()] = _normalize_fingerprint(stored_fingerprint)

    matches = sorted(
        (
            identifier
            for identifier, stored_fingerprint in normalized_existing.items()
            if stored_fingerprint == normalized
            and normalized.startswith(identifier.removeprefix("EVT-").lower())
        ),
        key=lambda identifier: (len(identifier), identifier),
    )
    if matches:
        return matches[0]

    for length in range(8, len(normalized) + 1, 4):
        identifier = "EVT-" + normalized[:length].upper()
        stored = normalized_existing.get(identifier)
        if stored is None or stored == normalized:
            return identifier
    raise ValueError("event fingerprint collides at full length")


__all__ = (
    "event_fingerprint",
    "event_id",
    "finding_fingerprint",
    "finding_id",
    "slug",
)
