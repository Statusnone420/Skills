#!/usr/bin/env python3
"""Apply the deterministic docs initialization closeout.

Usage:
  python init_closeout.py <repository-root> preview < request.json
  python init_closeout.py <repository-root> apply < request.json
  python init_closeout.py <repository-root> preview --request-file request.json
  python init_closeout.py <repository-root> apply --request-file request.json
  python init_closeout.py <repository-root> adopt-preview --receipt-file receipt.json
  python init_closeout.py <repository-root> adopt-apply --receipt-file receipt.json --approval '<exact>'

Requests are bounded UTF-8 JSON on stdin or the explicit request file. Preview
is zero-write and emits the exact approval string. Apply requires the same
evidence plus that exact string; it reconstructs the plan from current files
and never accepts target bytes. Responses are bounded machine-readable JSON on
stdout.
"""

import argparse
import json
import os
from pathlib import Path
import re
import stat
import sys

from _docs_checker.init_closeout import (
    InitCloseoutError,
    MAX_REQUEST_BYTES,
    apply_response,
    inspect_initialization_preflight,
    prepare_initialization_closeout,
    preview_response,
    validate_public_request,
)
from _docs_checker.memory import _strict_json_loads
from _docs_checker.init_adoption import (
    adoption_apply,
    adoption_preview,
    canonical_request_bytes,
)


def _write_response(value):
    data = (
        json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=False,
        )
        + "\n"
    ).encode("utf-8")
    sys.stdout.buffer.write(data)


_APPROVAL_CONTEXT = re.compile(
    r"^Approve \$docs init preview (INIT-[0-9A-F]{12}) with manifest ([0-9a-f]{64})$"
)


def _failure(error, request=None):
    response = {
        "schema_version": 3,
        "status": error.status,
        "classification": error.classification,
        "boundary": error.boundary,
        "writes": 0,
        "partial_state": "none",
        "rollback": {
            "required": False,
            "complete": True,
            "documents": "not-required",
            "controls": "not-required",
            "cleanup": "not-required",
        },
        "successful_event_recorded": False,
    }
    if type(request) is dict and request.get("operation") == "apply":
        approval = request.get("approval")
        match = _APPROVAL_CONTEXT.fullmatch(approval) if type(approval) is str else None
        if match is not None:
            response.update(
                {
                    "preview_id": match.group(1),
                    "manifest_sha256": match.group(2),
                }
            )
    return response


def _read_request(request_file):
    if request_file is None:
        try:
            return sys.stdin.buffer.read(MAX_REQUEST_BYTES + 1)
        except OSError as exc:
            raise InitCloseoutError(
                "invalid-request", "request-unavailable", "request-read"
            ) from exc

    try:
        with request_file.open("rb") as handle:
            if not stat.S_ISREG(os.fstat(handle.fileno()).st_mode):
                raise InitCloseoutError(
                    "invalid-request", "request-unavailable", "request-read"
                )
            return handle.read(MAX_REQUEST_BYTES + 1)
    except InitCloseoutError:
        raise
    except OSError as exc:
        raise InitCloseoutError(
            "invalid-request", "request-unavailable", "request-read"
        ) from exc


def _decode_request(raw, operation):
    if len(raw) > MAX_REQUEST_BYTES:
        raise InitCloseoutError(
            "invalid-request", "request-capacity", "request-read"
        )
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise InitCloseoutError(
            "invalid-request", "request-not-utf8", "request-read"
        ) from exc
    try:
        request = _strict_json_loads(text, "initialization closeout request")
    except ValueError as exc:
        raise InitCloseoutError(
            "invalid-request", "malformed-request-json", "request-validation"
        ) from exc
    return validate_public_request(request, operation)


def _write_adoption_receipt(root, receipt_file, request):
    if receipt_file is None:
        raise InitCloseoutError(
            "invalid-request", "receipt-file-required", "receipt-write"
        )
    root = Path(root).absolute()
    receipt_file = Path(receipt_file).absolute()
    try:
        resolved_root = os.path.normcase(os.path.realpath(root))
        resolved_receipt = os.path.normcase(os.path.realpath(receipt_file))
        try:
            receipt_is_in_repository = (
                os.path.commonpath((resolved_root, resolved_receipt))
                == resolved_root
            )
        except ValueError:
            receipt_is_in_repository = False
        if receipt_is_in_repository:
            raise InitCloseoutError(
                "invalid-request", "receipt-must-be-outside-repository", "receipt-write"
            )
        parent_info = os.lstat(receipt_file.parent)
        if not stat.S_ISDIR(parent_info.st_mode) or stat.S_ISLNK(parent_info.st_mode):
            raise OSError("receipt parent is not a real directory")
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        if hasattr(os, "O_BINARY"):
            flags |= os.O_BINARY
        descriptor = os.open(receipt_file, flags, 0o600)
        try:
            data = canonical_request_bytes(request)
            view = memoryview(data)
            while view:
                written = os.write(descriptor, view)
                if written <= 0:
                    raise OSError("receipt write did not make progress")
                view = view[written:]
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    except InitCloseoutError:
        raise
    except OSError as exc:
        raise InitCloseoutError(
            "invalid-request", "receipt-unavailable", "receipt-write"
        ) from exc


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Preview or apply one verified docs initialization closeout."
    )
    parser.add_argument("root", type=Path, help="explicit repository root")
    parser.add_argument(
        "operation",
        choices=("preview", "apply", "adopt-preview", "adopt-apply"),
    )
    parser.add_argument(
        "--request-file",
        type=Path,
        help="bounded UTF-8 JSON request file; defaults to stdin",
    )
    parser.add_argument(
        "--receipt-file",
        type=Path,
        help="engine-owned adoption receipt outside the repository",
    )
    parser.add_argument(
        "--approval",
        help="exact engine-emitted adoption approval",
    )
    parser.add_argument(
        "--scope",
        help="explicit shared documentation scope when discovery is ambiguous",
    )
    arguments = parser.parse_args(argv)

    validated_request = None
    if arguments.operation == "adopt-preview":
        try:
            response = inspect_initialization_preflight(arguments.root)
            if response is None:
                request, response = adoption_preview(
                    arguments.root,
                    explicit_scope=arguments.scope,
                )
                _write_adoption_receipt(
                    arguments.root,
                    arguments.receipt_file,
                    request,
                )
        except InitCloseoutError as error:
            _write_response(_failure(error))
            return 2
        except (KeyError, TypeError, ValueError, OSError, RecursionError, OverflowError):
            error = InitCloseoutError(
                "invalid-request", "request-semantics", "request-validation"
            )
            _write_response(_failure(error))
            return 2
        _write_response(response)
        return 0 if response.get("status") in {
            "already-initialized",
            "approval-required",
        } else 2

    if arguments.operation == "adopt-apply":
        try:
            raw = _read_request(arguments.receipt_file)
            validated_request = _decode_request(raw, "preview")
            response = adoption_apply(
                arguments.root,
                validated_request,
                arguments.approval,
            )
        except InitCloseoutError as error:
            _write_response(_failure(error, validated_request))
            return 2
        except (KeyError, TypeError, ValueError, OSError, RecursionError, OverflowError):
            error = InitCloseoutError(
                "invalid-request", "request-semantics", "request-validation"
            )
            _write_response(_failure(error, validated_request))
            return 2
        _write_response(response)
        return 0 if response.get("status") in {
            "applied",
            "closeout-committed-cleanup-incomplete",
        } else 2

    try:
        raw = _read_request(arguments.request_file)
    except InitCloseoutError as error:
        _write_response(_failure(error))
        return 2
    try:
        request = _decode_request(raw, arguments.operation)
        validated_request = request
        response = (
            inspect_initialization_preflight(arguments.root)
            if arguments.operation == "preview"
            else None
        )
        if response is None:
            prepared = prepare_initialization_closeout(arguments.root, request)
            response = (
                preview_response(prepared)
                if arguments.operation == "preview"
                else apply_response(arguments.root, prepared, request["approval"])
            )
    except InitCloseoutError as error:
        _write_response(_failure(error, validated_request))
        return 2
    except (KeyError, TypeError, ValueError, OSError, RecursionError, OverflowError):
        error = InitCloseoutError(
            "invalid-request", "request-semantics", "request-validation"
        )
        _write_response(_failure(error, validated_request))
        return 2

    _write_response(response)
    return 0 if response.get("status") in {
        "already-initialized",
        "approval-required",
        "applied",
    } else 2


if __name__ == "__main__":
    raise SystemExit(main())
