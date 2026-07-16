#!/usr/bin/env python3
"""Apply the deterministic docs initialization closeout.

Usage:
  python init_closeout.py <repository-root> preview < request.json
  python init_closeout.py <repository-root> apply < request.json
  python init_closeout.py <repository-root> preview --request-file request.json
  python init_closeout.py <repository-root> apply --request-file request.json

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


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Preview or apply one verified docs initialization closeout."
    )
    parser.add_argument("root", type=Path, help="explicit repository root")
    parser.add_argument("operation", choices=("preview", "apply"))
    parser.add_argument(
        "--request-file",
        type=Path,
        help="bounded UTF-8 JSON request file; defaults to stdin",
    )
    arguments = parser.parse_args(argv)

    validated_request = None
    try:
        raw = _read_request(arguments.request_file)
    except InitCloseoutError as error:
        _write_response(_failure(error))
        return 2
    if len(raw) > MAX_REQUEST_BYTES:
        error = InitCloseoutError(
            "invalid-request", "request-capacity", "request-read"
        )
        _write_response(_failure(error))
        return 2
    try:
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
        request = validate_public_request(request, arguments.operation)
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
