#!/usr/bin/env python3
"""Emit one sanitized Diataxis Docs evidence receipt to stdout."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

from _docs_checker.evidence import (
    build_evidence_receipt,
    canonical_receipt_bytes,
    observe_entry_orientation,
)
from _docs_checker.health import health_summary
from _docs_checker.init_adoption import SKILL_VERSION
from check import check


_PARSER = argparse.ArgumentParser()
_PARSER.add_argument("root")
_PARSER.add_argument("--metadata-file", required=True)
_PARSER.add_argument("--map", default="docs/README.md")
_PARSER.add_argument("--scope", default="docs")


def _git(root, *args):
    completed = subprocess.run(
        ["git", "-C", str(root), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="strict",
        check=False,
    )
    if completed.returncode:
        raise ValueError("repository Git evidence is unavailable")
    return completed.stdout.strip()


def _status(root):
    return "dirty" if _git(root, "status", "--porcelain=v1", "--untracked-files=all") else "clean"


def _metadata(path):
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError) as exc:
        raise ValueError("metadata file is unavailable or malformed") from exc
    expected = {"receipt_id", "repository_identifier", "run", "semantic", "unresolved", "doctor"}
    if not isinstance(value, dict) or set(value) != expected:
        raise ValueError("metadata fields are invalid")
    return value


def main(argv=None):
    try:
        namespace = _PARSER.parse_args(argv)
        root = Path(namespace.root).absolute()
        metadata = _metadata(namespace.metadata_file)
        before_raw = _git(root, "status", "--porcelain=v1", "--untracked-files=all")
        before = "dirty" if before_raw else "clean"
        commit = _git(root, "rev-parse", "HEAD")
        findings, _, measurements = check(
            root,
            map_path=namespace.map,
            scope=namespace.scope,
            _measurements=True,
        )
        health = health_summary(
            measurements,
            findings=measurements["active_findings"],
            baseline=measurements["baseline"],
            freshness=measurements["freshness"],
            coverage=measurements["coverage"],
        )
        health["surface"] = measurements["navigation"]["scope"]
        health["provider"] = measurements["navigation"]["provider"]
        after_raw = _git(root, "status", "--porcelain=v1", "--untracked-files=all")
        after = "dirty" if after_raw else "clean"
        payload = {
            "navigation": measurements["navigation"],
            "health": health,
            "findings": findings,
        }
        receipt = build_evidence_receipt(
            receipt_id=metadata["receipt_id"],
            repository_identifier=metadata["repository_identifier"],
            commit=commit,
            checker_version=SKILL_VERSION,
            run=metadata["run"],
            checker_payload=payload,
            orientation=observe_entry_orientation(root, measurements["navigation"].get("entry")),
            semantic=metadata["semantic"],
            unresolved=metadata["unresolved"],
            doctor=metadata["doctor"],
            writes_attempted=0,
            writes_observed=0 if before_raw == after_raw else len(after_raw.splitlines()),
            git_before=before,
            git_after=after,
        )
        sys.stdout.buffer.write(canonical_receipt_bytes(receipt))
        return 0
    except (OSError, UnicodeError, ValueError) as exc:
        print(
            json.dumps(
                {"status": "failed", "error": str(exc), "receipt": None},
                ensure_ascii=True,
            )
        )
        return 2


if __name__ == "__main__":
    sys.exit(main())
