"""Capture exact Task 5 init-discovery JSON as a coherent public receipt."""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path, PurePosixPath, PureWindowsPath

_REPOSITORY_ROOT = str(Path(__file__).resolve().parents[1])
_ADDED_REPOSITORY_ROOT = _REPOSITORY_ROOT not in sys.path
if _ADDED_REPOSITORY_ROOT:
    sys.path.insert(0, _REPOSITORY_ROOT)
try:
    from skills.docs.scripts._docs_checker.discovery_policy import (
        DOCUMENTATION_ROOT_NAMES,
        INIT_DISCOVERY_LIMITS,
        PACKAGE_CONTAINER_NAMES,
        _prune_reason,
    )
    from skills.docs.scripts._docs_checker.receipt import (
        DISCOVERY_CONTRACT_V1,
        DISCOVERY_CONTRACT_V2,
        DISCOVERY_V1_FIELDS,
        DISCOVERY_V2_FIELDS,
        discovery_fields,
        validate_v2_extensions,
    )
    from skills.docs.scripts._docs_checker.paths import (
        ANYWHERE_PRUNE_DIRS,
        REPOSITORY_ROOT_ONLY_PRUNE_DIRS,
    )
finally:
    if _ADDED_REPOSITORY_ROOT:
        sys.path.remove(_REPOSITORY_ROOT)
    del _ADDED_REPOSITORY_ROOT

DOCTOR_DISCOVERY_KIND = "init-discovery"
DISCOVERY_RECEIPT_CHECKSUM_VERSION = 1
DOCTOR_DISCOVERY_RECEIPT_FIELDS = DISCOVERY_V1_FIELDS - {"root"}
DOCTOR_DISCOVERY_RECEIPT_FIELDS_V2 = DISCOVERY_V2_FIELDS - {"root"}


def doctor_discovery_receipt_fields(version):
    return discovery_fields(version) - {"root"}


def _is_exact_json(value):
    value_type = type(value)
    if value is None or value_type in {str, int, bool}:
        return True
    if value_type is list:
        return all(_is_exact_json(item) for item in value)
    return bool(
        value_type is dict
        and all(
            type(key) is str and _is_exact_json(item)
            for key, item in value.items()
        )
    )


def _copy_exact_json(value):
    value_type = type(value)
    if value is None or value_type in {str, int, bool}:
        return value
    if value_type is list:
        return [_copy_exact_json(item) for item in value]
    if value_type is dict and all(type(key) is str for key in value):
        return {key: _copy_exact_json(item) for key, item in value.items()}
    raise ValueError("discovery result must contain exact built-in JSON types")


def _canonical_receipt_checksum(payload):
    """Return the raw v1 checksum for an exact sanitized Task 5 receipt."""
    if type(payload) is not dict:
        return None
    version = payload.get("schema_version")
    try:
        fields = doctor_discovery_receipt_fields(version)
    except ValueError:
        return None
    if set(payload) != fields:
        return None
    if not _is_exact_json(payload):
        return None
    envelope = {
        "contract": (
            "task5-init-discovery-receipt-checksum"
            if version == DISCOVERY_CONTRACT_V1
            else "task5.1-init-discovery-receipt-checksum"
        ),
        "payload": payload,
        "version": version,
    }
    canonical = json.dumps(
        envelope,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def _is_absolute_root(value):
    return bool(
        type(value) is str
        and value
        and (
            PurePosixPath(value).is_absolute()
            or PureWindowsPath(value).is_absolute()
        )
    )


def build_doctor_discovery_action(discovery_result):
    """Copy Task 5 JSON and attach a deterministic receipt-coherence checksum.

    The checksum detects accidental drift within the serialized receipt. It is
    not provenance, not authentication, not proof of Task 5 execution, and not
    protection against coordinated payload-and-checksum fabrication.
    """
    if type(discovery_result) is not dict:
        raise ValueError("discovery result must be exact versioned JSON")
    version = discovery_result.get("schema_version")
    try:
        capture_fields = discovery_fields(version)
    except ValueError as error:
        raise ValueError("discovery result has an unsupported contract version") from error
    root = discovery_result.get("root")
    valid_root = (
        _is_absolute_root(root)
        if version == DISCOVERY_CONTRACT_V1
        else type(root) is str and root == "."
    )
    if (
        set(discovery_result) != capture_fields
        or not _is_exact_json(discovery_result)
        or not valid_root
    ):
        raise ValueError(
            "discovery result must be exact Task 5 JSON or exact Task 5.1 JSON "
            "with a sanitized root"
        )
    payload = {
        key: _copy_exact_json(value)
        for key, value in discovery_result.items()
        if key != "root"
    }
    checksum = _canonical_receipt_checksum(payload)
    if checksum is None:
        raise ValueError("discovery result does not match the v1 receipt contract")
    return dict(
        owner="docs",
        kind=DOCTOR_DISCOVERY_KIND,
        **payload,
        receipt_checksum=checksum,
    )


__all__ = (
    "ANYWHERE_PRUNE_DIRS",
    "DISCOVERY_RECEIPT_CHECKSUM_VERSION",
    "DOCUMENTATION_ROOT_NAMES",
    "DOCTOR_DISCOVERY_KIND",
    "DOCTOR_DISCOVERY_RECEIPT_FIELDS",
    "DOCTOR_DISCOVERY_RECEIPT_FIELDS_V2",
    "INIT_DISCOVERY_LIMITS",
    "PACKAGE_CONTAINER_NAMES",
    "REPOSITORY_ROOT_ONLY_PRUNE_DIRS",
    "_prune_reason",
    "build_doctor_discovery_action",
    "doctor_discovery_receipt_fields",
    "validate_v2_extensions",
)
