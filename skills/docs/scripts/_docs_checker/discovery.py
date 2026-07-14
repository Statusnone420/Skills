"""Bounded, metadata-only first-contact documentation discovery."""

import os
import re
import stat
from pathlib import Path

from .continuation import (
    CONTINUATION_SCHEMA_VERSION,
    plan_content_batch,
)
from .discovery_io import (
    _empty_scope_metadata,
    _entry_stat,
    _info_is_reparse,
    _list_entries,
    _lstat_path,
    _record_boundary,
    _record_exclusion,
    _safe_directory_entry,
    _scan_selected_scope,
    inspect_root_entries,
    scan_root_document_scope,
    validate_root,
)
from .discovery_policy import (
    DOCUMENTATION_ROOT_NAMES,
    INIT_DISCOVERY_LIMITS,
    PACKAGE_CONTAINER_NAMES,
    _ANYWHERE_PRUNE_KEYS,
    _ROOT_ONLY_PRUNE_KEYS,
    join_relative as _join_relative,
    prune_reason as _prune_reason,
    sort_key as _sort_key,
)
from .knowledge import (
    local_directory_evidence,
    local_document_file_evidence,
    local_knowledge_preview,
    local_prune_reason,
)
from .paths import (
    _path_identity,
    normalize_repo_relative,
    prune_summary,
)
from .receipt import (
    DISCOVERY_CONTRACT_V1,
    DISCOVERY_CONTRACT_V2,
    discovery_fields,
    project_discovery_result,
)
from .root_evidence import (
    MAINTAINED_ROOT_DOCUMENT_NAMES,
    is_maintained_root_document,
    public_root_document_evidence,
    repository_host,
    root_document_evidence,
)
from .surfaces import classify_protected_surfaces, surface_observation_allowed


_DOC_ROOT_KEYS = frozenset(name.casefold() for name in DOCUMENTATION_ROOT_NAMES)
_PACKAGE_CONTAINER_KEYS = frozenset(
    name.casefold() for name in PACKAGE_CONTAINER_NAMES
)
_WINDOWS_SHORT_COMPONENT = re.compile(r"^.+~[1-9][0-9]*(?:\..*)?$", re.IGNORECASE)


def _add_candidate(state, relative, source):
    relative = normalize_repo_relative(relative, "candidate root")
    identity = _path_identity(relative)
    if identity in state["candidate_keys"]:
        return
    if len(state["candidates"]) >= INIT_DISCOVERY_LIMITS["candidate_roots"]:
        state["candidate_limit_hit"] = True
        state["candidate_truncated"] = True
        state["halted"] = True
        state["observed_candidate_roots"] = len(state["candidates"]) + 1
        _record_boundary(state, "candidate-roots", relative)
        return
    state["candidate_keys"].add(identity)
    state["candidates"].append({"path": relative, "source": source})
    state["observed_candidate_roots"] = len(state["candidates"])


def _add_local_candidate(state, relative, evidence):
    relative = normalize_repo_relative(relative, "local candidate")
    identity = _path_identity(relative)
    if identity in state["local_candidate_keys"]:
        return
    if len(state["local_candidates"]) >= INIT_DISCOVERY_LIMITS["candidate_roots"]:
        state["candidate_truncated"] = True
        state["halted"] = True
        state["observed_local_candidates"] = len(state["local_candidates"]) + 1
        _record_boundary(state, "local-candidate-roots", relative)
        return
    state["local_candidate_keys"].add(identity)
    state["local_candidates"].append(
        {
            "path": relative,
            "visibility": "local-only",
            "source": "conventional-local-root",
            "evidence": evidence,
        }
    )
    state["observed_local_candidates"] = len(state["local_candidates"])


def _probe_candidate(state, relative, source):
    if state["halted"] or state["candidate_limit_hit"]:
        return
    relative = normalize_repo_relative(relative, "candidate root")
    reason = _prune_reason(relative)
    if reason:
        info = _lstat_path(
            state,
            state["root"] / relative,
            relative,
            phase="candidate",
            missing_ok=True,
        )
        if info is not None:
            _record_exclusion(state, relative, reason, pruned=True)
        return

    info = _lstat_path(
        state,
        state["root"] / relative,
        relative,
        phase="candidate",
        missing_ok=True,
    )
    if state["halted"] or info is None:
        return
    if _info_is_reparse(info):
        _record_exclusion(state, relative, "unsafe-reparse")
        return
    if not stat.S_ISDIR(info.st_mode):
        _record_exclusion(state, relative, "not-directory")
        return
    _add_candidate(state, relative, source)


def _discover_local_candidates(state, local_entry):
    local_relative = local_entry.name
    children = _list_entries(
        Path(local_entry.path),
        local_relative,
        state,
        phase="candidate",
    )
    if state["halted"]:
        return
    local_root_has_document = False
    pending = []
    for child in children:
        child_relative = _join_relative(local_relative, child.name)
        reason = _prune_reason(child_relative) or local_prune_reason(child_relative)
        if reason:
            _record_exclusion(state, child_relative, reason, pruned=True)
            continue
        info = _entry_stat(state, child, child_relative, phase="candidate", depth=1)
        if state["halted"]:
            return
        if info is None:
            return
        if _info_is_reparse(info):
            _record_exclusion(state, child_relative, "unsafe-reparse")
            continue
        if stat.S_ISREG(info.st_mode):
            local_root_has_document = bool(
                local_root_has_document or local_document_file_evidence(child.name)
            )
            continue
        if not stat.S_ISDIR(info.st_mode):
            continue
        evidence = local_directory_evidence(child.name)
        if evidence is None:
            grandchildren = _list_entries(
                Path(child.path),
                child_relative,
                state,
                phase="candidate",
                depth=2,
            )
            if state["halted"]:
                return
            for grandchild in grandchildren:
                grandchild_relative = _join_relative(child_relative, grandchild.name)
                reason = _prune_reason(grandchild_relative) or local_prune_reason(
                    grandchild_relative
                )
                if reason:
                    _record_exclusion(
                        state,
                        grandchild_relative,
                        reason,
                        pruned=True,
                    )
                    continue
                if not local_document_file_evidence(grandchild.name):
                    continue
                grandchild_info = _entry_stat(
                    state,
                    grandchild,
                    grandchild_relative,
                    phase="candidate",
                    depth=2,
                )
                if state["halted"]:
                    return
                if (
                    grandchild_info is not None
                    and stat.S_ISREG(grandchild_info.st_mode)
                    and not _info_is_reparse(grandchild_info)
                ):
                    evidence = "documentation-shaped-file-metadata"
                    break
        if evidence is not None:
            pending.append((child_relative, evidence))
    if local_root_has_document:
        pending.append((local_relative, "documentation-shaped-file-metadata"))
    for relative, evidence in sorted(pending, key=lambda item: _sort_key(item[0])):
        _add_local_candidate(state, relative, evidence)
        if state["halted"]:
            return


def _discover_automatic_candidates(state, *, include_local):
    for name in DOCUMENTATION_ROOT_NAMES:
        _probe_candidate(state, name, "root")
        if state["halted"]:
            return

    usable_root_entries, usable_by_key = inspect_root_entries(
        state,
        is_root_document=is_maintained_root_document,
        evidence_factory=root_document_evidence,
        surface_observation=surface_observation_allowed,
    )
    if state["halted"]:
        return

    local_entry = usable_by_key.get(".local")
    if include_local and local_entry is not None:
        _discover_local_candidates(state, local_entry)
        if state["halted"]:
            return

    for entry in usable_root_entries:
        key = entry.name.casefold()
        if key in _PACKAGE_CONTAINER_KEYS or key in _DOC_ROOT_KEYS or key == ".local":
            continue
        for doc_name in DOCUMENTATION_ROOT_NAMES:
            _probe_candidate(
                state,
                f"{entry.name}/{doc_name}",
                "direct-child",
            )
            if state["halted"]:
                return

    for container_name in PACKAGE_CONTAINER_NAMES:
        entry = usable_by_key.get(container_name.casefold())
        if entry is None:
            continue
        children = _list_entries(
            Path(entry.path),
            entry.name,
            state,
            phase="candidate",
        )
        if state["halted"]:
            return
        for child in children:
            child_relative = f"{entry.name}/{child.name}"
            if not _safe_directory_entry(
                child,
                child_relative,
                state,
                phase="candidate",
            ):
                if state["halted"]:
                    return
                continue
            for doc_name in DOCUMENTATION_ROOT_NAMES:
                _probe_candidate(
                    state,
                    f"{child_relative}/{doc_name}",
                    f"container:{container_name}",
                )
                if state["halted"]:
                    return


def _empty_content_batch(*, blocked=False):
    return {
        "paths": [],
        "path_count": 0,
        "bytes": 0,
        "complete": False,
        "truncated": False,
        "next_boundary": None,
        "blocked_by_metadata": blocked,
    }


def _empty_continuation(*, blocked=False):
    return {
        "schema_version": CONTINUATION_SCHEMA_VERSION,
        "status": "blocked" if blocked else "complete",
        "batch": None,
        "cursor": None,
        "token": None,
        "total_batches": None,
        "rejection": None,
        "fresh_preview_required": False,
    }


def _plan_content_batch(
    state,
    scope_metadata,
    selected_scope,
    continuation,
):
    if scope_metadata["truncated"]:
        return _empty_content_batch(blocked=True), _empty_continuation(blocked=True)
    batch, continuation_result = plan_content_batch(
        scope_metadata["paths"],
        state["selected_evidence"],
        selected_scope,
        continuation=continuation,
        discovery_contract_version=state["contract_version"],
        repository_identity=state["repository_identity"],
        file_limit=INIT_DISCOVERY_LIMITS["content_files"],
        byte_limit=INIT_DISCOVERY_LIMITS["content_bytes"],
    )
    if continuation_result["status"] == "rejected":
        state["continuation_rejected"] = True
    elif continuation_result["status"] == "blocked" and scope_metadata["paths"]:
        state["content_blocked"] = True
    elif batch["truncated"]:
        state["content_truncated"] = True
        boundary_kind = (
            "content-files"
            if batch["path_count"] >= INIT_DISCOVERY_LIMITS["content_files"]
            else "content-bytes"
        )
        _record_boundary(state, boundary_kind, batch["next_boundary"])
    return batch, continuation_result


def _validated_explicit_scope(state, explicit_scope):
    raw = os.fspath(explicit_scope)
    if raw == "":
        raise ValueError("explicit scope must not be empty")
    candidate = Path(raw.replace("\\", os.sep).replace("/", os.sep))
    if any(part == ".." for part in candidate.parts):
        raise ValueError("explicit scope must not contain '..' segments")
    if any(
        part.rstrip(" .") != part or _WINDOWS_SHORT_COMPONENT.fullmatch(part)
        for part in candidate.parts
    ):
        raise ValueError("explicit scope contains a Windows-ambiguous path segment")
    normalized = normalize_repo_relative(raw, "explicit scope")
    parts = () if normalized == "." else tuple(Path(normalized).parts)
    if any(part.casefold() in _ANYWHERE_PRUNE_KEYS for part in parts):
        raise ValueError("explicit scope is inside an anywhere-pruned tree")
    overrides = (
        [parts[0]]
        if parts and parts[0].casefold() in _ROOT_ONLY_PRUNE_KEYS
        else []
    )
    current = state["root"]
    current_relative = "."
    for part in parts:
        current = current / part
        current_relative = _join_relative(current_relative, part)
        info = _lstat_path(
            state,
            current,
            current_relative,
            phase="candidate",
        )
        if state["halted"]:
            return raw, normalized, overrides
        if info is None or not stat.S_ISDIR(info.st_mode):
            raise ValueError("explicit scope must be an existing directory")
        if _info_is_reparse(info):
            raise ValueError("explicit scope must not contain a reparse component")
    return raw, normalized, overrides


def _initial_state(root, contract_version):
    return {
        "root": root,
        "contract_version": contract_version,
        "legacy_missing_ok": contract_version == DISCOVERY_CONTRACT_V1,
        "candidates": [],
        "candidate_keys": set(),
        "local_candidate_keys": set(),
        "candidate_limit_hit": False,
        "observed_candidate_roots": 0,
        "observed_local_candidates": 0,
        "candidate_truncated": False,
        "scope_truncated": False,
        "content_truncated": False,
        "content_blocked": False,
        "continuation_rejected": False,
        "halted": False,
        "physical_limit": None,
        "scandir_calls": 0,
        "raw_directory_entries": 0,
        "metadata_operations": 0,
        "selected_scope_max_depth": 0,
        "containers": [],
        "applied_exclusions": [],
        "exclusion_keys": set(),
        "applied_prunes": [],
        "next_boundary": [],
        "boundary_keys": set(),
        "io_errors": [],
        "root_documents": [],
        "selected_evidence": [],
        "has_root_instructions": False,
        "local_candidates": [],
        "surface_paths": set(),
        "repository_identity": None,
    }


def discover_init_scope(
    root,
    explicit_scope=None,
    continuation=None,
    *,
    contract_version=DISCOVERY_CONTRACT_V1,
):
    """Return deterministic first-contact metadata without opening file content."""
    discovery_fields(contract_version)
    if contract_version == DISCOVERY_CONTRACT_V1 and continuation is not None:
        raise ValueError("discovery contract version 1 does not support continuation")
    root = Path(root).absolute()
    state = _initial_state(root, contract_version)
    validate_root(state)

    requested_scope = (
        None if explicit_scope is None else os.fspath(explicit_scope)
    )
    normalized_scope = None
    jurisdiction_scope = "."
    root_only_overrides = []
    explicit_narrow = False
    if explicit_scope is not None and not state["halted"]:
        requested_scope, normalized_scope, root_only_overrides = _validated_explicit_scope(
            state,
            explicit_scope,
        )
        jurisdiction_scope = normalized_scope
        explicit_narrow = normalized_scope != "."

    if state["halted"]:
        selected_scope = None
        selection_reason = "discovery-truncated"
        metadata_phases = 1
    elif explicit_narrow or (
        contract_version == DISCOVERY_CONTRACT_V2 and explicit_scope is not None
    ):
        if normalized_scope == "." or contract_version == DISCOVERY_CONTRACT_V2:
            inspect_root_entries(
                state,
                is_root_document=is_maintained_root_document,
                evidence_factory=root_document_evidence,
                surface_observation=surface_observation_allowed,
            )
        if state["halted"]:
            selected_scope = None
            selection_reason = "discovery-truncated"
        else:
            _add_candidate(state, normalized_scope, "explicit")
            selected_scope = normalized_scope
            selection_reason = "explicit-scope"
        metadata_phases = 1
    else:
        _discover_automatic_candidates(
            state,
            include_local=contract_version == DISCOVERY_CONTRACT_V2,
        )
        candidates = state["candidates"]
        metadata_phases = 1
        if state["candidate_truncated"]:
            selected_scope = None
            selection_reason = "discovery-truncated"
        elif len(candidates) == 1:
            selected_scope = candidates[0]["path"]
            selection_reason = "sole-candidate"
        elif candidates:
            selected_scope = None
            selection_reason = "choice-required"
        elif contract_version == DISCOVERY_CONTRACT_V1:
            selected_scope = None
            selection_reason = "no-candidates"
        elif state["root_documents"]:
            selected_scope = "."
            selection_reason = "sole-root-document-scope"
        else:
            selected_scope = "."
            selection_reason = "no-maintained-documentation"

    candidates = [
        {**candidate, "rank": index}
        for index, candidate in enumerate(state["candidates"], 1)
    ]
    recommended_scope = candidates[0]["path"] if candidates else None
    scope_metadata = _empty_scope_metadata()
    inspected_scope = None
    if selected_scope is not None and not state["halted"]:
        scope_metadata = (
            scan_root_document_scope(state)
            if selected_scope == "."
            else _scan_selected_scope(
                state,
                selected_scope,
                root_only_overrides,
                local_prune=local_prune_reason,
                surface_observation=surface_observation_allowed,
                evidence_factory=root_document_evidence,
            )
        )
        inspected_scope = selected_scope
        if not explicit_narrow:
            metadata_phases += 1
    if (
        selected_scope is not None
        and not state["physical_limit"]
        and not state["io_errors"]
    ):
        content_batch, continuation_result = _plan_content_batch(
            state,
            scope_metadata,
            selected_scope,
            continuation,
        )
    else:
        content_batch = _empty_content_batch(blocked=True)
        continuation_result = _empty_continuation(blocked=True)

    no_doc_preview = bool(
        contract_version == DISCOVERY_CONTRACT_V2
        and
        selected_scope == "." and not scope_metadata["paths"] and not state["halted"]
    )
    if state["continuation_rejected"]:
        status = "stopped"
        requires_user_action = True
        user_action = "restart-fresh-discovery"
    elif state["candidate_truncated"] or state["scope_truncated"] or state["content_blocked"]:
        status = "stopped"
        requires_user_action = True
        user_action = "narrow-scope-or-continuation"
    elif selected_scope is None and candidates:
        status = "choice-required"
        requires_user_action = True
        user_action = "choose-explicit-scope"
    elif selected_scope is None:
        status = "no-candidates"
        requires_user_action = True
        user_action = "provide-explicit-scope"
    elif no_doc_preview:
        status = "adoption-preview"
        requires_user_action = True
        user_action = "review-no-doc-adoption-preview"
    elif state["content_truncated"]:
        status = "batch-limited"
        if contract_version == DISCOVERY_CONTRACT_V1:
            requires_user_action = True
            user_action = "after-content-batch-choose-continuation-or-narrow-scope"
        else:
            requires_user_action = False
            user_action = "continue-init-inspection"
    else:
        status = "ready"
        requires_user_action = False
        user_action = None

    state["applied_exclusions"].sort(
        key=lambda item: (_sort_key(item["path"]), item["reason"])
    )
    state["next_boundary"].sort(
        key=lambda item: (item["kind"], _sort_key(item["path"]))
    )
    orientation_text = (
        "Repository knowledge starts at the shared documentation map. Before "
        "declaring a plan, decision, preference, or repository context absent, "
        "inspect the declared local knowledge map when present."
    )
    root_evidence_complete = bool(
        not state["candidate_truncated"] and not state["io_errors"]
    )
    internal_result = {
        "schema_version": contract_version,
        "mode": "init-discovery",
        "status": status,
        "root": str(root),
        "requested_scope": requested_scope,
        "normalized_scope": normalized_scope,
        "jurisdiction_scope": jurisdiction_scope,
        "candidates": candidates,
        "recommended_scope": recommended_scope,
        "selected_scope": selected_scope,
        "inspected_scope": inspected_scope,
        "selection_reason": selection_reason,
        "limits": dict(INIT_DISCOVERY_LIMITS),
        "observed": {
            "metadata_phases": metadata_phases,
            "scandir_calls": state["scandir_calls"],
            "raw_directory_entries": state["raw_directory_entries"],
            "metadata_operations": state["metadata_operations"],
            "selected_scope_max_depth": state["selected_scope_max_depth"],
            "containers": state["containers"],
            "candidate_roots": state["observed_candidate_roots"],
            "reported_candidate_roots": len(candidates),
            "selected_markdown_paths": scope_metadata["observed_path_count"],
            "selected_markdown_bytes": scope_metadata["observed_bytes"],
        },
        "scope_metadata": scope_metadata,
        "content_batch": content_batch,
        "continuation": continuation_result,
        "completeness": {
            "status": (
                "incomplete"
                if state["io_errors"]
                or state["candidate_truncated"]
                or state["scope_truncated"]
                else "complete"
            ),
            "errors": list(state["io_errors"]),
        },
        "adoption_preview": {
            "operation": "propose-generic-orientation-hook",
            "target": "AGENTS.md" if state["has_root_instructions"] else "shared-map",
            "text": orientation_text,
            "includes_local_details": False,
            "writes": 0,
        },
        "local_knowledge": local_knowledge_preview(
            state["local_candidates"],
            selected_scope,
        ),
        "root_documents": public_root_document_evidence(
            state["root_documents"] if root_evidence_complete else (),
            complete=root_evidence_complete,
        ),
        "evidence_reads": {
            "count": 0,
            "bytes": 0,
            "byte_limit": 64 * 1024,
            "sources": [],
        },
        "protected_surfaces": classify_protected_surfaces(
            sorted(state["surface_paths"], key=_sort_key),
            host=repository_host(
                sorted(state["surface_paths"], key=_sort_key)
            ),
            complete=False,
        ),
        "physical_limit": state["physical_limit"],
        "prunes": prune_summary(state["applied_prunes"]),
        "applied_exclusions": state["applied_exclusions"],
        "explicit_root_only_overrides": root_only_overrides,
        "truncated": bool(state["next_boundary"]),
        "next_boundary": state["next_boundary"],
        "requires_user_action": requires_user_action,
        "user_action": user_action,
        "scope_limited": True,
        "repository_exhaustive": False,
        "content_reads": 0,
    }
    return project_discovery_result(
        internal_result,
        contract_version,
        absolute_root=root,
    )


__all__ = (
    "DOCUMENTATION_ROOT_NAMES",
    "INIT_DISCOVERY_LIMITS",
    "MAINTAINED_ROOT_DOCUMENT_NAMES",
    "PACKAGE_CONTAINER_NAMES",
    "discover_init_scope",
)
