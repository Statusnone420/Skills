#!/usr/bin/env python3
"""Prepare or apply one exact, engine-owned Doctor treatment closeout."""

import argparse
import json
import os
from pathlib import Path
import stat
import sys

from _docs_checker.doctor_closeout import (
    DoctorCloseoutError,
    MAX_REQUEST_BYTES,
    SCHEMA_VERSION,
    apply_treatment_receipt,
    canonical_bytes,
    prepare_treatment_receipt,
)


def _write(value):
    sys.stdout.buffer.write(canonical_bytes(value))


def _failure(error):
    return {
        "schema_version": SCHEMA_VERSION,
        "status": error.status,
        "classification": error.classification,
        "boundary": error.boundary,
        "writes": 0,
        "successful_event_recorded": False,
    }


def _outside_repository(root, receipt):
    root = os.path.normcase(os.path.realpath(root))
    receipt = os.path.normcase(os.path.realpath(receipt))
    try:
        return os.path.commonpath((root, receipt)) != root
    except ValueError:
        return True


def _read_json(path):
    try:
        with Path(path).open("rb") as handle:
            if not stat.S_ISREG(os.fstat(handle.fileno()).st_mode):
                raise DoctorCloseoutError("invalid-request", "request-unavailable", "request-read")
            data = handle.read(MAX_REQUEST_BYTES + 1)
    except DoctorCloseoutError:
        raise
    except OSError as exc:
        raise DoctorCloseoutError("invalid-request", "request-unavailable", "request-read") from exc
    if len(data) > MAX_REQUEST_BYTES:
        raise DoctorCloseoutError("invalid-request", "request-capacity", "request-read")
    try:
        return json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise DoctorCloseoutError("invalid-request", "request-malformed", "request-read") from exc


def _write_receipt(root, path, receipt):
    path = Path(path).absolute()
    if not _outside_repository(root, path):
        raise DoctorCloseoutError("invalid-request", "receipt-must-be-outside-repository", "receipt-write")
    try:
        parent = os.lstat(path.parent)
        if not stat.S_ISDIR(parent.st_mode) or stat.S_ISLNK(parent.st_mode):
            raise OSError("receipt parent is not a real directory")
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        if hasattr(os, "O_BINARY"):
            flags |= os.O_BINARY
        descriptor = os.open(path, flags, 0o600)
        try:
            data = canonical_bytes(receipt)
            view = memoryview(data)
            while view:
                written = os.write(descriptor, view)
                if written <= 0:
                    raise OSError("receipt write did not make progress")
                view = view[written:]
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    except OSError as exc:
        raise DoctorCloseoutError("invalid-request", "receipt-unavailable", "receipt-write") from exc


def main(argv=None):
    parser = argparse.ArgumentParser(description="Prepare or apply an exact Doctor treatment closeout.")
    parser.add_argument("root", type=Path)
    parser.add_argument("operation", choices=("prepare", "apply"))
    parser.add_argument("--receipt-file", type=Path, required=True)
    parser.add_argument("--request-file", type=Path)
    parser.add_argument("--approval")
    arguments = parser.parse_args(argv)
    try:
        if arguments.operation == "prepare":
            if arguments.request_file is None:
                raise DoctorCloseoutError("invalid-request", "request-file-required", "request-read")
            receipt = prepare_treatment_receipt(arguments.root, _read_json(arguments.request_file))
            _write_receipt(arguments.root, arguments.receipt_file, receipt)
            response = {
                "schema_version": SCHEMA_VERSION,
                "status": "approval-required",
                "approval": receipt["approval"],
                "treatments": [
                    {
                        "id": item["id"],
                        "fingerprint": item["fingerprint"],
                        "affected_count": item["affected_count"],
                        "files": item["files"],
                    }
                    for item in receipt["treatments"]
                ],
                "writes": 0,
                "successful_event_recorded": False,
            }
        else:
            if arguments.approval is None:
                raise DoctorCloseoutError("invalid-request", "approval-required", "approval-revalidation")
            if not _outside_repository(arguments.root, arguments.receipt_file):
                raise DoctorCloseoutError(
                    "invalid-request", "receipt-must-be-outside-repository", "receipt-read"
                )
            receipt = _read_json(arguments.receipt_file)
            response = apply_treatment_receipt(arguments.root, receipt, arguments.approval)
    except DoctorCloseoutError as error:
        _write(_failure(error))
        return 2
    except (KeyError, TypeError, ValueError, OSError, OverflowError, RecursionError):
        _write(_failure(DoctorCloseoutError("invalid-request", "request-semantics", "request-validation")))
        return 2
    _write(response)
    return (
        0
        if response.get("status")
        in {"approval-required", "applied", "closeout-committed-cleanup-incomplete"}
        else 2
    )


if __name__ == "__main__":
    raise SystemExit(main())
