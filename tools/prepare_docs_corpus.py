#!/usr/bin/env python3
"""Opt-in, non-destructive acquisition for the pinned documentation corpus."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path

from run_docs_corpus import (
    DEFAULT_MANIFEST,
    DEFAULT_WORKSPACE,
    MAX_MANIFEST_BYTES,
    ROOT,
    _assert_no_reparse_components,
    load_manifest,
    safe_path,
    verify_checkout,
)


WORKSPACE_ROOT = ROOT / "evals" / "workspace"
MARKER = ".docs-corpus-owner.json"
MAX_MARKER_BYTES = 4 * 1024


def _bounded_bytes(path, maximum, name):
    try:
        with Path(path).open("rb") as stream:
            raw = stream.read(maximum + 1)
    except OSError as exc:
        raise ValueError(f"{name} is unavailable") from exc
    if len(raw) > maximum:
        raise ValueError(f"{name} exceeds capacity")
    return raw


def _run(command, *, operation, input_text=None):
    completed = subprocess.run(
        command,
        input=input_text,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="strict",
        check=False,
    )
    if completed.returncode:
        raise ValueError(f"corpus acquisition failed: {operation}")
    return completed


def _rooted_sparse_patterns(paths):
    """Encode validated literal paths as repository-rooted non-cone patterns."""
    if any(path != path.rstrip() for path in paths):
        raise ValueError("sparse checkout paths must not contain trailing whitespace")
    if any(path == "." for path in paths):
        raise ValueError("sparse checkout paths must be below the repository root")
    return "".join(f"/{path}\n" for path in paths)


def _workspace(path, manifest_path, corpus_id):
    root = WORKSPACE_ROOT.absolute()
    workspace_candidate = Path(path).absolute()
    try:
        workspace_candidate.relative_to(root)
    except ValueError as exc:
        raise ValueError("corpus workspace must remain under evals/workspace") from exc
    if workspace_candidate == root:
        raise ValueError("corpus workspace must be a named child")
    _assert_no_reparse_components(root)
    root.mkdir(parents=True, exist_ok=True)
    _assert_no_reparse_components(root)
    workspace = safe_path(workspace_candidate, root)
    if workspace.exists() and not workspace.is_dir():
        raise ValueError("corpus workspace is not a directory")
    workspace.mkdir(parents=True, exist_ok=True)
    workspace = safe_path(workspace, root)
    marker = safe_path(workspace / MARKER, workspace)
    digest = hashlib.sha256(
        _bounded_bytes(manifest_path, MAX_MANIFEST_BYTES, "corpus manifest")
    ).hexdigest()
    expected = {"corpus_id": corpus_id, "manifest_sha256": f"sha256:{digest}"}
    if marker.exists():
        try:
            marker_raw = _bounded_bytes(marker, MAX_MARKER_BYTES, "corpus workspace marker")
            current = json.loads(marker_raw.decode("utf-8", "strict"))
        except (UnicodeError, ValueError, RecursionError) as exc:
            raise ValueError("corpus workspace marker is malformed") from exc
        if current != expected:
            raise ValueError("corpus workspace marker does not match")
    else:
        if any(workspace.iterdir()):
            raise ValueError("refusing to reuse an unowned corpus workspace")
        marker.write_text(
            json.dumps(expected, sort_keys=True, separators=(",", ":")) + "\n",
            encoding="utf-8",
            newline="\n",
        )
    return workspace


def prepare(manifest=DEFAULT_MANIFEST, workspace=DEFAULT_WORKSPACE, repository_ids=()):
    manifest_value = load_manifest(manifest)
    workspace = _workspace(workspace, manifest, manifest_value["corpus_id"])
    selected = set(repository_ids)
    known = {spec["id"] for spec in manifest_value["repositories"]}
    if selected - known:
        raise ValueError("unknown corpus repository selection")
    rows = []
    for spec in manifest_value["repositories"]:
        if selected and spec["id"] not in selected:
            continue
        target = safe_path(workspace / spec["id"], workspace)
        if target.exists():
            raise ValueError(f"refusing to update or reuse corpus repository: {spec['id']}")
        _run(["git", "init", str(target)], operation="initialize repository")
        _run(
            ["git", "-C", str(target), "remote", "add", "origin", spec["repository_url"]],
            operation="configure repository remote",
        )
        _run(
            ["git", "-C", str(target), "sparse-checkout", "init", "--no-cone"],
            operation="initialize sparse checkout",
        )
        _run(
            ["git", "-C", str(target), "sparse-checkout", "set", "--no-cone", "--stdin"],
            operation="configure sparse checkout",
            input_text=_rooted_sparse_patterns(spec["sparse_paths"]),
        )
        _run(
            [
                "git",
                "-C",
                str(target),
                "fetch",
                "--depth=1",
                "--filter=blob:none",
                "origin",
                spec["commit"],
            ],
            operation="fetch pinned commit",
        )
        _run(
            ["git", "-C", str(target), "checkout", "--detach", "FETCH_HEAD"],
            operation="checkout pinned commit",
        )
        verify_checkout(workspace, spec)
        rows.append({"id": spec["id"], "commit": spec["commit"], "status": "prepared"})
    return {"status": "completed", "corpus_id": manifest_value["corpus_id"], "repositories": rows}


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", default=str(DEFAULT_MANIFEST))
    parser.add_argument("--workspace", default=str(DEFAULT_WORKSPACE))
    parser.add_argument("--repository", action="append", default=[])
    namespace = parser.parse_args(argv)
    try:
        print(
            json.dumps(
                prepare(namespace.manifest, namespace.workspace, namespace.repository),
                indent=2,
                sort_keys=True,
                ensure_ascii=True,
            )
        )
        return 0
    except (OSError, UnicodeError):
        print(json.dumps({"status": "failed", "error": "corpus preparation I/O failed"}))
        return 2
    except ValueError as exc:
        print(json.dumps({"status": "failed", "error": str(exc)}, ensure_ascii=True))
        return 2


if __name__ == "__main__":
    sys.exit(main())
