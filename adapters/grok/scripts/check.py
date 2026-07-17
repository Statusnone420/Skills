#!/usr/bin/env python3
"""Read-only, standard-library documentation integrity checker."""

import argparse
import json
import os
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path


_SAFE_PUBLIC_CLI_ERRORS = frozenset(
    {
        "--agent requires --json",
        "--doctor-recovery-preview and --doctor-recovery-apply are mutually exclusive",
        "Doctor recovery cannot be combined with --init-discovery",
        "--init-discovery requires --json --agent",
        "--continuation requires --init-discovery --json --agent",
        "content continuation token is invalid",
        "path traversal is not allowed",
        "root must be a real directory",
        "unsupported documentation navigation manifest",
    }
)
_PUBLIC_CONFINEMENT_ERROR = "symlink or reparse path component"
_SAFE_PUBLIC_CONFINEMENT_ERRORS = frozenset(
    {
        _PUBLIC_CONFINEMENT_ERROR,
        "symlink root",
        "symlink path",
        "explicit scope must not contain a reparse component",
    }
)

# Importing the internal package must not mutate the checker or inspected tree.
_previous_dont_write_bytecode = sys.dont_write_bytecode
sys.dont_write_bytecode = True

from _docs_checker.health import (
    HEALTH_RUBRIC_VERSION,
    HEALTH_WEIGHTS,
    PROVISIONAL_TARGET_BYTES,
    _count,
    _fraction,
    evaluate_coverage,
    evaluate_freshness,
    health_meter,
    health_summary,
    normalized_content_digest,
)
from _docs_checker.discovery import (
    derive_result_corpus,
    discover_init_scope,
    prepare_init_discovery,
    scan_selected_document_corpus,
    validate_corpus_coverage,
)
from _docs_checker.init_closeout import inspect_initialization_preflight
from _docs_checker.continuation import decode_continuation_token
from _docs_checker.identity import (
    _EVENT_ID,
    _FINDING_ID,
    _FINGERPRINT,
    _IDENTITY_PATH_FIELDS,
    _IDENTITY_PATH_LIST_FIELDS,
    _IDENTITY_SCALAR_FIELDS,
    _canonical_finding_evidence,
    _canonical_path_identity,
    _canonical_scalar_identity,
    _normalize_event_id,
    _normalize_fingerprint,
    _require_mapping,
    _require_sequence,
    _require_string,
    event_fingerprint,
    event_id,
    finding_fingerprint,
    finding_id,
    slug,
)
from _docs_checker.knowledge import (
    LOCAL_MAP_MAX_BYTES,
    LOCAL_MAP_PATH,
    LOCAL_MAP_PREVIEW,
    LOCAL_MAP_SCHEMA_VERSION,
    inspect_local_map,
    route_local_knowledge,
)
from _docs_checker.memory import (
    EVENTS_FILE,
    FINDINGS_FILE,
    FINDING_STATUSES,
    MAX_EVENTS_BYTES,
    MAX_FINDINGS_BYTES,
    MAX_JSON_DEPTH,
    MAX_MANIFEST_BYTES,
    MAX_STATE_BYTES,
    PRIORITIES,
    STATE_DIRECTORY,
    STATE_FILE,
    STATE_SCHEMA_VERSION,
    _DIGEST,
    _MANIFEST_DIGEST,
    _MERGE_MARKER,
    _OperationalMemoryIssue,
    _SEMVER,
    _StrictJSONError,
    _decode_operational_bytes,
    _memory_finding,
    _normalize_checked_path,
    _normalize_checked_pattern,
    _normalize_checked_route,
    _normalize_digest,
    _normalize_manifest,
    _operational_control,
    _operational_file,
    _operational_memory_findings,
    _read_bounded_bytes,
    _read_operational_file,
    _reject_json_constant,
    _require_exact_keys,
    _require_int,
    _strict_json_loads,
    _strict_object,
    _validate_finding_evidence,
    _validate_json_nesting,
    build_initialization_state,
    inspect_operational_memory,
    load_operational_events,
    load_operational_findings,
    load_operational_state,
    validate_operational_events,
    validate_operational_findings,
    validate_operational_state,
)
from _docs_checker.lifecycle import (
    build_verified_event,
    prepare_dispositions,
    preview_memory_compaction,
    select_persisted_findings,
    transition_finding,
)
from _docs_checker.lifecycle_io import (
    apply_state_conflict_recovery,
    apply_verified_closeout,
    prepare_verified_closeout,
    preview_state_conflict_recovery,
    validate_protected_intent_change,
    verify_local_route_hashes,
)
from _docs_checker.metadata_io import is_expected_environmental_error
from _docs_checker.navigation import NavigationBoundary, select_navigation
from _docs_checker.paths import (
    ANYWHERE_PRUNE_DIRS,
    REPOSITORY_ROOT_ONLY_PRUNE_DIRS,
    STANDARD_PRUNE_DIRS,
    _assert_no_reparse_components,
    _first_reparse_component,
    _is_pruned_relative,
    _is_reparse,
    _path_identity,
    _raise_walk_error,
    _relative_posix,
    iter_markdown_scope,
    normalize_repo_relative,
    prune_summary,
    route_matches_patterns,
    safe_path,
    unique_relative_paths,
)
from _docs_checker.scan import (
    H1,
    H2,
    HEADING,
    LINK,
    CURRENT_ROUTE_LINK,
    discover_markdown,
    hot_path_summary,
    scan_documents,
    strip_fences,
)
from _docs_checker.surfaces import (
    SURFACE_SCAN_LIMITS,
    SURFACE_SCHEMA_VERSION,
    classify_protected_surfaces,
    inspect_protected_surfaces,
    preview_protected_dispositions,
    validate_protected_disposition_preview,
)


# Build the CLI parser at import time.  On POSIX, argparse's locale discovery
# may probe the filesystem while constructing help text; that work is outside
# Init discovery so the bounded metadata budget measures repository evidence
# only.
_PARSER = argparse.ArgumentParser()
_PARSER.add_argument("root")
_PARSER.add_argument("--json", action="store_true")
_PARSER.add_argument("--agent", action="store_true")
_PARSER.add_argument("--init-discovery", action="store_true")
_PARSER.add_argument("--doctor-recovery-preview", action="store_true")
_PARSER.add_argument("--doctor-recovery-apply", default=None, metavar="APPROVAL")
_PARSER.add_argument("--continuation", default=None)
_PARSER.add_argument("--map", default="docs/README.md")
_PARSER.add_argument("--hot", default=None)
_PARSER.add_argument("--scope", default=None)

sys.dont_write_bytecode = _previous_dont_write_bytecode
del _previous_dont_write_bytecode


def check(
    root,
    map_path="docs/README.md",
    hot_paths=None,
    scope="docs",
    *,
    _measurements=False,
):
    root = Path(root).absolute()
    _assert_no_reparse_components(root)
    map_norm = normalize_repo_relative(map_path, "map")
    scope_norm = normalize_repo_relative(scope, "scope")
    navigation = select_navigation(root, scope_norm, map_norm)
    scan_scope = (
        navigation["scope"] if navigation["provider"] == "mintlify" else scope_norm
    )
    if navigation["provider"] == "mintlify":
        scan_map = navigation["entry"] or (
            navigation["navigated_pages"] + navigation["hidden_pages"]
        )[0]
    else:
        scan_map = map_norm
    configured_hot_paths = unique_relative_paths(
        [
            normalize_repo_relative(path, "hot paths")
            for path in (hot_paths or [])
        ]
    )
    normalized_hot_paths = unique_relative_paths([scan_map] + configured_hot_paths)
    scoped, findings, applied_prunes = discover_markdown(root, scan_scope)
    findings.extend(navigation.get("findings", []))
    findings.extend(inspect_operational_memory(root))
    state = None
    active_findings = []
    try:
        state = load_operational_state(root)
    except (OSError, UnicodeError, ValueError):
        pass
    try:
        active_findings = load_operational_findings(root)["findings"]
    except (OSError, UnicodeError, ValueError):
        pass
    result = scan_documents(
        root,
        scan_map,
        normalized_hot_paths,
        scoped,
        findings,
        applied_prunes,
        () if state is None else state["cold_paths"],
        navigation=navigation,
    )
    findings, hot_path, measurements = result
    freshness = (
        {"status": "unverified", "routes": [], "findings": []}
        if state is None
        else evaluate_freshness(root, state["verified_documents"])
    )
    findings.extend(freshness["findings"])
    coverage = evaluate_coverage(
        configured_routes=configured_hot_paths,
        state=state,
        map_routes=measurements["map_current_routes"],
        freshness=freshness,
    )
    measurements.update(
        {
            "active_findings": [*findings, *active_findings],
            "baseline": None if state is None else state["rubric"],
            "freshness": freshness,
            "coverage": coverage,
            "navigation": navigation,
        }
    )
    if _measurements:
        return findings, hot_path, measurements
    return findings, hot_path


def main(argv=None):
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    argv = list(sys.argv[1:] if argv is None else argv)
    value_options = {
        "--map",
        "--hot",
        "--scope",
        "--continuation",
        "--doctor-recovery-apply",
    }
    positional = []
    skip = False
    for arg in argv:
        if skip:
            skip = False
            continue
        if arg in value_options:
            skip = True
            continue
        if arg.startswith("--"):
            continue
        positional.append(arg)
    machine_output = any(
        option in argv
        for option in (
            "--json",
            "--doctor-recovery-preview",
            "--doctor-recovery-apply",
        )
    )
    if machine_output and not positional:
        print(
            json.dumps(
                {
                    "status": "error",
                    "has_findings": False,
                    "error": "the following arguments are required: root",
                    "findings": [],
                }
            )
        )
        return 2
    namespace = _PARSER.parse_args(argv)
    recovery_mode = (
        namespace.doctor_recovery_preview
        or namespace.doctor_recovery_apply is not None
    )
    try:
        if namespace.agent and not namespace.json:
            raise ValueError("--agent requires --json")
        if (
            namespace.doctor_recovery_preview
            and namespace.doctor_recovery_apply is not None
        ):
            raise ValueError(
                "--doctor-recovery-preview and --doctor-recovery-apply are mutually exclusive"
            )
        if recovery_mode and namespace.init_discovery:
            raise ValueError("Doctor recovery cannot be combined with --init-discovery")
        if namespace.init_discovery and not (
            namespace.json and namespace.agent
        ):
            raise ValueError("--init-discovery requires --json --agent")
        if namespace.continuation is not None and not (
            namespace.init_discovery and namespace.json and namespace.agent
        ):
            raise ValueError("--continuation requires --init-discovery --json --agent")
        continuation = (
            None
            if namespace.continuation is None
            else decode_continuation_token(namespace.continuation)
        )
        if any(part == ".." for part in Path(namespace.root).parts):
            raise ValueError("path traversal is not allowed")
        # Normalize the CLI root lexically.  Init discovery owns every
        # filesystem metadata operation and applies its physical budget at
        # validate_root; the façade must not preflight ancestors through a
        # platform-specific Path operation before that boundary.
        raw = Path(os.path.abspath(os.path.expanduser(os.fspath(namespace.root))))
        if recovery_mode:
            recovery_preview = preview_state_conflict_recovery(raw)
            recovery_response = (
                recovery_preview
                if namespace.doctor_recovery_preview
                else apply_state_conflict_recovery(
                    raw,
                    recovery_preview,
                    approved_preview=namespace.doctor_recovery_apply,
                    verification=None,
                )
            )
        elif namespace.init_discovery:
            discovery_state, discovery = prepare_init_discovery(
                raw,
                lambda candidate: inspect_initialization_preflight(
                    candidate,
                    control_present=True,
                ),
            )
            if discovery is None:
                discovery = discover_init_scope(
                    raw,
                    explicit_scope=namespace.scope,
                    continuation=continuation,
                    _prepared_state=discovery_state,
                )
        else:
            _assert_no_reparse_components(raw)
            if _is_reparse(raw) or not raw.is_dir():
                raise ValueError("root must be a real directory")
            root = safe_path(raw, raw)
            scope_value = "docs" if namespace.scope is None else namespace.scope
            map_norm = normalize_repo_relative(namespace.map, "map")
            hot = (
                [
                    normalize_repo_relative(path, "hot paths")
                    for path in namespace.hot.split(",")
                ]
                if namespace.hot
                else None
            )
            scope_norm = normalize_repo_relative(scope_value, "scope")
            findings, hot_path, measurements = check(
                root, map_norm, hot, scope_norm, _measurements=True
            )
    except NavigationBoundary as exc:
        if namespace.json or recovery_mode:
            print(
                json.dumps(
                    {
                        "status": exc.result.get("status", "unmeasured"),
                        "has_findings": False,
                        "root": ".",
                        "scope": exc.result.get("scope"),
                        "navigation": exc.result,
                        "error": exc.result.get(
                            "classification",
                            "unsupported documentation navigation manifest",
                        ),
                        "findings": [],
                    },
                    ensure_ascii=True,
                )
            )
        else:
            print(
                "error: "
                + exc.result.get(
                    "classification", "unsupported documentation navigation manifest"
                )
            )
        return 2
    except OSError as exc:
        if not is_expected_environmental_error(exc):
            raise
        if namespace.json or recovery_mode:
            print(
                json.dumps(
                    {
                        "status": "error",
                        "has_findings": False,
                        "error": "filesystem metadata unavailable",
                        "findings": [],
                    }
                )
            )
        else:
            print("error: filesystem metadata unavailable")
        return 2
    except (ValueError, UnicodeError) as exc:
        raw_detail = (
            exc.args[0]
            if type(exc) is ValueError
            and len(exc.args) == 1
            and type(exc.args[0]) is str
            else None
        )
        detail = (
            _PUBLIC_CONFINEMENT_ERROR
            if raw_detail in _SAFE_PUBLIC_CONFINEMENT_ERRORS
            else raw_detail
            if raw_detail in _SAFE_PUBLIC_CLI_ERRORS
            else "invalid command input"
        )
        if namespace.json or recovery_mode:
            print(
                json.dumps(
                    {
                        "status": "error",
                        "has_findings": False,
                        "error": detail,
                        "findings": [],
                    }
                )
            )
        else:
            print(f"error: {detail}")
        return 2
    if recovery_mode:
        print(
            json.dumps(
                recovery_response,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=True,
                allow_nan=False,
            )
        )
        return 0 if recovery_response.get("status") in {
            "approval-required",
            "recovered",
        } else 2
    if namespace.init_discovery:
        print(json.dumps(discovery, ensure_ascii=True))
        return 2 if discovery.get("status") == "state-conflict" else 0
    if namespace.json:
        health = health_summary(
            measurements,
            findings=measurements["active_findings"],
            baseline=measurements["baseline"],
            freshness=measurements["freshness"],
            coverage=measurements["coverage"],
        )
        health["surface"] = measurements["navigation"]["scope"]
        health["provider"] = measurements["navigation"]["provider"]
        print(
            json.dumps(
                {
                    "status": "findings" if findings else "clean",
                    "has_findings": bool(findings),
                    "root": ".",
                    "scope": scope_norm,
                    "map": map_norm,
                    "prunes": measurements["prunes"],
                    "hot_path": hot_path,
                    "navigation": measurements["navigation"],
                    "health": health,
                    "findings": findings,
                },
                ensure_ascii=True,
            )
        )
    elif findings:
        for finding in findings:
            print(f"{finding['kind']}: {finding}")
    else:
        print("clean")
    return 0 if namespace.agent else (1 if findings else 0)


if __name__ == "__main__":
    sys.exit(main())
