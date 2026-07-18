#!/usr/bin/env python3
"""Run the pinned documentation corpus without modifying target repositories."""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "skills" / "docs" / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from _docs_checker.evidence import (  # noqa: E402
    build_evidence_receipt,
    config_probe,
    evidence_value,
    observe_entry_orientation,
    validate_relative_evidence_path,
)
from _docs_checker.health import HEALTH_RUBRIC_VERSION, HEALTH_WEIGHTS, health_summary  # noqa: E402
from _docs_checker.init_adoption import SKILL_VERSION  # noqa: E402
from _docs_checker.paths import _assert_no_reparse_components, safe_path  # noqa: E402
from check import check  # noqa: E402


DEFAULT_MANIFEST = ROOT / "evals" / "docs-corpus-v1.json"
DEFAULT_WORKSPACE = ROOT / "evals" / "workspace" / "docs-corpus-v1"
MAX_MANIFEST_BYTES = 256 * 1024
MAX_MANIFEST_PATH_BYTES = 512
MAX_PROBES_PER_REPOSITORY = 64
MAX_SPARSE_PATHS_PER_REPOSITORY = 128
_SHA = re.compile(r"^[0-9a-f]{40}$")
_ID = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
_PROVIDERS = frozenset({"mintlify", "custom-mdx", "docusaurus", "vitepress", "mkdocs", "hugo"})
_SPARSE_PATTERN_SYNTAX = re.compile(r"[*?!\[\]]")


def _git(root, *args, allowed=(0,)):
    completed = subprocess.run(
        ["git", "-C", str(root), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="strict",
        check=False,
    )
    if completed.returncode not in allowed:
        raise ValueError(f"Git evidence failed: {' '.join(args)}")
    return completed


def _relative(value, name):
    normalized = validate_relative_evidence_path(value, name)
    if len(value.encode("utf-8", "strict")) > MAX_MANIFEST_PATH_BYTES:
        raise ValueError(f"{name} exceeds capacity")
    return normalized


def load_manifest(path=DEFAULT_MANIFEST):
    try:
        with Path(path).open("rb") as stream:
            raw = stream.read(MAX_MANIFEST_BYTES + 1)
    except OSError as exc:
        raise ValueError("corpus manifest is unavailable or malformed") from exc
    if len(raw) > MAX_MANIFEST_BYTES:
        raise ValueError("corpus manifest exceeds capacity")
    try:
        value = json.loads(raw.decode("utf-8", "strict"))
    except (UnicodeError, ValueError, RecursionError) as exc:
        raise ValueError("corpus manifest is unavailable or malformed") from exc
    if not isinstance(value, dict) or set(value) != {"schema_version", "corpus_id", "repositories"}:
        raise ValueError("corpus manifest fields are invalid")
    if value["schema_version"] != 1 or value["corpus_id"] != "docs-corpus-v1":
        raise ValueError("corpus manifest identity is invalid")
    repositories = value["repositories"]
    if not isinstance(repositories, list) or len(repositories) != 6:
        raise ValueError("corpus manifest must contain six repositories")
    expected_fields = {
        "id",
        "repository_url",
        "commit",
        "provider",
        "measurement",
        "scope",
        "entry",
        "authority_probes",
        "config_probes",
        "sparse_paths",
    }
    seen = set()
    for index, spec in enumerate(repositories):
        name = f"repositories[{index}]"
        if not isinstance(spec, dict) or set(spec) != expected_fields:
            raise ValueError(f"{name} fields are invalid")
        if not isinstance(spec["id"], str) or _ID.fullmatch(spec["id"]) is None:
            raise ValueError(f"{name}.id is invalid")
        if spec["id"] in seen:
            raise ValueError("corpus repository IDs must be unique")
        seen.add(spec["id"])
        if not isinstance(spec["repository_url"], str) or not re.fullmatch(
            r"https://github\.com/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+\.git",
            spec["repository_url"],
        ):
            raise ValueError(f"{name}.repository_url is invalid")
        if not isinstance(spec["commit"], str) or _SHA.fullmatch(spec["commit"]) is None:
            raise ValueError(f"{name}.commit is invalid")
        if not isinstance(spec["provider"], str) or spec["provider"] not in _PROVIDERS:
            raise ValueError(f"{name}.provider is invalid")
        expected_measurement = "supported" if spec["provider"] == "mintlify" else "unsupported"
        if spec["measurement"] != expected_measurement:
            raise ValueError(f"{name}.measurement is invalid")
        _relative(spec["scope"], f"{name}.scope")
        _relative(spec["entry"], f"{name}.entry")
        for field in ("authority_probes", "config_probes", "sparse_paths"):
            paths = spec[field]
            maximum = (
                MAX_SPARSE_PATHS_PER_REPOSITORY
                if field == "sparse_paths"
                else MAX_PROBES_PER_REPOSITORY
            )
            if (
                not isinstance(paths, list)
                or not paths
                or len(paths) > maximum
                or any(not isinstance(path, str) for path in paths)
                or len(paths) != len(set(paths))
            ):
                raise ValueError(f"{name}.{field} is invalid")
            for path_index, path in enumerate(paths):
                _relative(path, f"{name}.{field}[{path_index}]")
                if field == "sparse_paths" and _SPARSE_PATTERN_SYNTAX.search(path):
                    raise ValueError(f"{name}.{field}[{path_index}] contains pattern syntax")
                if field == "sparse_paths" and path != path.rstrip():
                    raise ValueError(f"{name}.{field}[{path_index}] contains trailing whitespace")
                if field == "sparse_paths" and path == ".":
                    raise ValueError(f"{name}.{field}[{path_index}] must be below the root")
    return value


def _repo_path(workspace, repository_id):
    workspace = Path(workspace).absolute()
    _assert_no_reparse_components(workspace)
    candidate = safe_path(workspace / repository_id, workspace)
    _assert_no_reparse_components(candidate)
    if candidate.exists() and (
        candidate.is_symlink()
        or (os.name == "nt" and getattr(candidate.lstat(), "st_file_attributes", 0) & 0x400)
    ):
        raise ValueError("corpus repository cannot be a reparse point")
    return candidate


def verify_checkout(workspace, spec):
    path = _repo_path(workspace, spec["id"])
    if not path.is_dir():
        raise ValueError(f"corpus repository is missing: {spec['id']}")
    head = _git(path, "rev-parse", "HEAD").stdout.strip()
    if head != spec["commit"]:
        raise ValueError(f"corpus commit mismatch: {spec['id']}")
    symbolic = _git(path, "symbolic-ref", "-q", "HEAD", allowed=(0, 1))
    if symbolic.returncode == 0:
        raise ValueError(f"corpus checkout is not detached: {spec['id']}")
    remote = _git(path, "remote", "get-url", "origin").stdout.strip()
    if remote != spec["repository_url"]:
        raise ValueError(f"corpus remote mismatch: {spec['id']}")
    status = _git(path, "status", "--porcelain=v1", "--untracked-files=all").stdout
    if status:
        raise ValueError(f"corpus checkout is dirty: {spec['id']}")
    entry = safe_path(path / spec["entry"], path)
    if not entry.is_file():
        raise ValueError(f"corpus entry is missing: {spec['id']}")
    for relative in {*spec["authority_probes"], *spec["config_probes"]}:
        if not safe_path(path / relative, path).is_file():
            raise ValueError(f"corpus probe is missing: {spec['id']}:{relative}")
    return path, status


def _semantic_not_assessed():
    return {
        "status": "not_assessed",
        "evaluator": {
            "provider": evidence_value("not_assessed"),
            "model": evidence_value("not_assessed"),
            "version": evidence_value("not_assessed"),
        },
        "findings": [],
    }


def _unsupported_payload(spec):
    return {
        "navigation": {
            "status": "unmeasured",
            "provider": spec["provider"],
            "authority": spec["authority_probes"][0],
            "provider_root": spec["scope"],
            "entry": spec["entry"],
            "navigated_pages": [],
            "hidden_pages": [],
            "redirects": [],
        },
        "findings": [],
    }


def run_repository(workspace, spec):
    for index, relative in enumerate(spec["config_probes"]):
        _relative(relative, f"{spec['id']}.config_probes[{index}]")
    path, before_raw = verify_checkout(workspace, spec)
    configurations = []
    for relative in spec["config_probes"]:
        probe = config_probe(safe_path(path / relative, path))
        if probe["status"] != "completed":
            raise ValueError(f"corpus configuration became unavailable: {spec['id']}:{relative}")
        configurations.append({"path": relative, **probe})

    if spec["measurement"] == "supported":
        findings, _, measurements = check(
            path,
            map_path=spec["entry"],
            scope=spec["scope"],
            _measurements=True,
        )
        health = health_summary(
            measurements,
            findings=measurements["active_findings"],
            baseline=measurements["baseline"],
            freshness=measurements["freshness"],
            coverage=measurements["coverage"],
        )
        payload = {
            "navigation": measurements["navigation"],
            "health": health,
            "findings": findings,
        }
        unresolved = []
    else:
        payload = _unsupported_payload(spec)
        unresolved = [
            {"kind": f"unsupported-{spec['provider']}-semantics", "status": "not_assessed"}
        ]

    after_raw = _git(path, "status", "--porcelain=v1", "--untracked-files=all").stdout
    if after_raw != before_raw:
        raise ValueError(f"corpus runner modified target repository: {spec['id']}")
    receipt = build_evidence_receipt(
        receipt_id=f"docs-corpus-v1-{spec['id']}",
        repository_identifier=spec["repository_url"].removeprefix("https://").removesuffix(".git"),
        commit=spec["commit"],
        checker_version=SKILL_VERSION,
        run={
            "id": f"docs-corpus-v1-{spec['id']}",
            "client": "local-corpus-harness",
            "model_provider": "local",
            "model": "deterministic-harness",
            "effort": "not-applicable",
            "turns": evidence_value("completed", 1),
            "duration_seconds": evidence_value("not_assessed"),
            "commands": ["corpus-check"],
        },
        checker_payload=payload,
        orientation=observe_entry_orientation(path, spec["entry"]),
        semantic=_semantic_not_assessed(),
        unresolved=unresolved,
        writes_attempted=0,
        writes_observed=0,
        git_before="clean",
        git_after="clean",
    )
    return {
        "id": spec["id"],
        "commit": spec["commit"],
        "provider": spec["provider"],
        "measurement": spec["measurement"],
        "configurations": configurations,
        "receipt": receipt,
    }


def run_corpus(manifest=DEFAULT_MANIFEST, workspace=DEFAULT_WORKSPACE):
    manifest_value = load_manifest(manifest)
    workspace = Path(workspace).absolute()
    if not workspace.is_dir():
        raise ValueError("corpus workspace is missing")
    rows = [run_repository(workspace, spec) for spec in manifest_value["repositories"]]
    return {
        "schema_version": 1,
        "corpus_id": manifest_value["corpus_id"],
        "checker_version": SKILL_VERSION,
        "rubric": {
            "version": HEALTH_RUBRIC_VERSION,
            "weights": dict(HEALTH_WEIGHTS),
            "changed": False,
        },
        "repositories": rows,
    }


def _output_path(value, workspace):
    """Reject output paths that could write into a corpus checkout or through a reparse point."""
    workspace = Path(workspace).absolute()
    output = Path(value).absolute()
    _assert_no_reparse_components(workspace)
    _assert_no_reparse_components(output)
    try:
        output.relative_to(workspace)
    except ValueError:
        return output
    raise ValueError("corpus output must remain outside the corpus workspace")


def _write_output(output, encoded):
    """Atomically replace output without following an existing hard link."""
    _assert_no_reparse_components(output)
    descriptor, temporary_name = tempfile.mkstemp(
        dir=output.parent,
        prefix=f".{output.name}.",
        suffix=".tmp",
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
            descriptor = None
            stream.write(encoded)
        _assert_no_reparse_components(output)
        os.replace(temporary, output)
        temporary = None
    finally:
        if descriptor is not None:
            os.close(descriptor)
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", default=str(DEFAULT_MANIFEST))
    parser.add_argument("--workspace", default=str(DEFAULT_WORKSPACE))
    parser.add_argument("--output")
    namespace = parser.parse_args(argv)
    try:
        output = _output_path(namespace.output, namespace.workspace) if namespace.output else None
        result = run_corpus(namespace.manifest, namespace.workspace)
        encoded = json.dumps(result, indent=2, sort_keys=True, ensure_ascii=True) + "\n"
        if output:
            _write_output(output, encoded)
        else:
            print(encoded, end="")
        return 0
    except (OSError, UnicodeError):
        print(json.dumps({"status": "failed", "error": "corpus runner I/O failed"}))
        return 2
    except ValueError as exc:
        print(json.dumps({"status": "failed", "error": str(exc)}, ensure_ascii=True))
        return 2


if __name__ == "__main__":
    sys.exit(main())
