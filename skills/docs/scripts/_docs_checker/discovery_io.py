"""Physically bounded metadata enumeration for Init discovery."""

import stat
from pathlib import Path

from .formats import is_document_path

from .discovery_policy import (
    INIT_DISCOVERY_LIMITS,
    join_relative,
    prune_reason,
    sort_key,
)
from .metadata_io import (
    close_scandir,
    entry_stat as physical_entry_stat,
    lstat as physical_lstat,
    next_scandir,
    open_scandir,
)


def _record_exclusion(state, relative, reason, *, pruned=False):
    key = (relative, reason)
    if key not in state["exclusion_keys"]:
        state["exclusion_keys"].add(key)
        state["applied_exclusions"].append({"path": relative, "reason": reason})
    if pruned:
        state["applied_prunes"].append(relative)


def _record_boundary(state, kind, relative):
    key = (kind, relative)
    if key not in state["boundary_keys"]:
        state["boundary_keys"].add(key)
        state["next_boundary"].append({"kind": kind, "path": relative})


def _mark_truncated(state, phase):
    state[f"{phase}_truncated"] = True


def _stop_incomplete(state, phase, kind, relative):
    if state["halted"]:
        return
    state["halted"] = True
    _mark_truncated(state, phase)
    _record_boundary(state, kind, relative)


def _record_io_error(state, error):
    if state["halted"]:
        return
    state["io_errors"].append(
        {key: value for key, value in error.items() if not key.startswith("_")}
    )
    _stop_incomplete(state, error["phase"], "metadata-io", error["path"])


def _stop_physical_limit(
    state,
    phase,
    kind,
    relative,
    *,
    observed,
    observed_is_lower_bound,
    depth=None,
):
    if state["halted"]:
        return
    state["physical_limit"] = {
        "kind": kind,
        "limit": INIT_DISCOVERY_LIMITS[kind],
        "observed": observed,
        "observed_is_lower_bound": observed_is_lower_bound,
        "container": relative,
        "depth": depth,
    }
    state["halted"] = True
    _mark_truncated(state, phase)
    _record_boundary(state, "physical-limit", relative)


def _take_metadata_operation(state, phase, relative, *, depth=None):
    if state["halted"]:
        return False
    limit = INIT_DISCOVERY_LIMITS["metadata_operations"]
    if state["metadata_operations"] >= limit:
        _stop_physical_limit(
            state,
            phase,
            "metadata_operations",
            relative,
            observed=state["metadata_operations"],
            observed_is_lower_bound=False,
            depth=depth,
        )
        return False
    state["metadata_operations"] += 1
    return True


def _lstat_path(
    state,
    path,
    relative,
    *,
    phase,
    depth=None,
    missing_ok=False,
):
    if not _take_metadata_operation(state, phase, relative, depth=depth):
        return None
    info, error = physical_lstat(path, relative, phase, depth)
    if error is not None:
        if (
            error.get("_environmental_kind") == "not-found"
            and (state["legacy_missing_ok"] or missing_ok)
        ):
            return None
        _record_io_error(state, error)
        return None
    return info


def _entry_stat(state, entry, relative, *, phase, depth=None):
    if not _take_metadata_operation(state, phase, relative, depth=depth):
        return None
    info, error = physical_entry_stat(entry, relative, phase, depth)
    if error is not None:
        _record_io_error(state, error)
        return None
    return info


def _info_is_reparse(info):
    return bool(
        stat.S_ISLNK(info.st_mode)
        or getattr(info, "st_file_attributes", 0) & 0x400
    )


def _container_observation(
    relative,
    observed,
    *,
    complete,
    opened,
    limit_kind=None,
):
    return {
        "path": relative,
        "observed_child_entries": observed,
        "observed_child_entries_is_lower_bound": not complete,
        "considered_child_entries": observed if complete else 0,
        "complete": complete,
        "opened": opened,
        "truncated": not complete,
        "limit_kind": limit_kind,
        "next_boundary": None,
    }


def _list_entries(directory, relative, state, *, phase, depth=None):
    if state["halted"]:
        return []
    if state["scandir_calls"] >= INIT_DISCOVERY_LIMITS["scandir_calls"]:
        _stop_physical_limit(
            state,
            phase,
            "scandir_calls",
            relative,
            observed=state["scandir_calls"],
            observed_is_lower_bound=False,
            depth=depth,
        )
        state["containers"].append(
            _container_observation(
                relative,
                0,
                complete=False,
                opened=False,
                limit_kind="scandir_calls",
            )
        )
        return []

    info = _lstat_path(state, directory, relative, phase=phase, depth=depth)
    if state["halted"]:
        state["containers"].append(
            _container_observation(
                relative,
                0,
                complete=False,
                opened=False,
                limit_kind="metadata_operations",
            )
        )
        return []
    if info is None:
        _stop_incomplete(state, phase, "missing-container", relative)
        state["containers"].append(
            _container_observation(relative, 0, complete=False, opened=False)
        )
        return []
    if _info_is_reparse(info):
        _record_exclusion(state, relative, "unsafe-reparse")
        _stop_incomplete(state, phase, "unsafe-container", relative)
        state["containers"].append(
            _container_observation(relative, 0, complete=False, opened=False)
        )
        return []
    if not stat.S_ISDIR(info.st_mode):
        _record_exclusion(state, relative, "not-directory")
        _stop_incomplete(state, phase, "invalid-container", relative)
        state["containers"].append(
            _container_observation(relative, 0, complete=False, opened=False)
        )
        return []

    if not _take_metadata_operation(state, phase, relative, depth=depth):
        state["containers"].append(
            _container_observation(
                relative,
                0,
                complete=False,
                opened=False,
                limit_kind="metadata_operations",
            )
        )
        return []
    state["scandir_calls"] += 1
    entries = []
    complete = False
    limit_kind = None
    handle, iterator, error = open_scandir(
        directory,
        relative,
        phase,
        depth,
    )
    if error is not None:
        _record_io_error(state, error)
        state["containers"].append(
            _container_observation(relative, 0, complete=False, opened=False)
        )
        return []
    try:
        while not state["halted"]:
            raw_limit = INIT_DISCOVERY_LIMITS["raw_directory_entries"]
            if state["raw_directory_entries"] >= raw_limit:
                limit_kind = "raw_directory_entries"
                _stop_physical_limit(
                    state,
                    phase,
                    limit_kind,
                    relative,
                    observed=state["raw_directory_entries"],
                    observed_is_lower_bound=True,
                    depth=depth,
                )
                break
            entry, exhausted, error = next_scandir(
                iterator,
                relative,
                phase,
                depth,
            )
            if error is not None:
                _record_io_error(state, error)
                break
            if exhausted:
                complete = True
                break
            state["raw_directory_entries"] += 1
            entries.append(entry)
            if len(entries) > INIT_DISCOVERY_LIMITS["child_entries_per_container"]:
                limit_kind = "child_entries_per_container"
                _stop_physical_limit(
                    state,
                    phase,
                    limit_kind,
                    relative,
                    observed=len(entries),
                    observed_is_lower_bound=True,
                    depth=depth,
                )
                break
    finally:
        close_scandir(handle)

    state["containers"].append(
        _container_observation(
            relative,
            len(entries),
            complete=complete,
            opened=True,
            limit_kind=limit_kind,
        )
    )
    if not complete:
        return []
    return sorted(entries, key=lambda entry: sort_key(entry.name))


def _safe_directory_entry(
    entry,
    relative,
    state,
    *,
    phase,
    root_only_overrides=(),
):
    reason = prune_reason(relative, root_only_overrides)
    if reason:
        _record_exclusion(state, relative, reason, pruned=True)
        return False
    info = _entry_stat(state, entry, relative, phase=phase)
    if state["halted"] or info is None:
        return False
    if _info_is_reparse(info):
        _record_exclusion(state, relative, "unsafe-reparse")
        return False
    return stat.S_ISDIR(info.st_mode)


def inspect_root_entries(
    state,
    *,
    is_root_document,
    evidence_factory,
    surface_observation,
):
    root_entries = _list_entries(state["root"], ".", state, phase="candidate")
    if state["halted"]:
        return [], {}
    directories = []
    directories_by_key = {}
    for entry in root_entries:
        relative = entry.name
        reason = prune_reason(relative)
        if reason:
            _record_exclusion(state, relative, reason, pruned=True)
            continue
        info = _entry_stat(state, entry, relative, phase="candidate")
        if state["halted"] or info is None:
            return [], {}
        if _info_is_reparse(info):
            _record_exclusion(state, relative, "unsafe-reparse")
        elif stat.S_ISDIR(info.st_mode):
            if surface_observation(relative, is_directory=True):
                state["surface_paths"].add(relative)
            directories.append(entry)
            directories_by_key.setdefault(entry.name.casefold(), entry)
        elif stat.S_ISREG(info.st_mode):
            if surface_observation(relative, is_directory=False):
                state["surface_paths"].add(relative)
            if is_root_document(entry.name):
                state["root_documents"].append(evidence_factory(relative, info))
        if entry.name.casefold() == "agents.md":
            state["has_root_instructions"] = True
    return directories, directories_by_key


def _empty_scope_metadata():
    return {
        "paths": [],
        "path_count": 0,
        "bytes": 0,
        "observed_path_count": 0,
        "observed_bytes": 0,
        "complete": False,
        "truncated": False,
        "next_boundary": None,
    }


def scan_root_document_scope(state):
    metadata = _empty_scope_metadata()
    metadata["complete"] = True
    for evidence in sorted(
        state["root_documents"],
        key=lambda item: sort_key(item["path"]),
    ):
        item = {"path": evidence["path"], "bytes": evidence["bytes"]}
        metadata["paths"].append(item)
        metadata["path_count"] += 1
        metadata["bytes"] += item["bytes"]
        metadata["observed_path_count"] += 1
        metadata["observed_bytes"] += item["bytes"]
        state["selected_evidence"].append(dict(evidence))
    return metadata


def _scan_selected_scope(
    state,
    selected_scope,
    root_only_overrides,
    *,
    local_prune,
    surface_observation,
    evidence_factory,
):
    scope_path = state["root"] / selected_scope
    metadata = _empty_scope_metadata()
    metadata["complete"] = True
    pending = [(scope_path, selected_scope, 0)]

    while pending and not state["halted"]:
        directory, relative, depth = pending.pop()
        if depth > INIT_DISCOVERY_LIMITS["selected_scope_depth"]:
            _stop_physical_limit(
                state,
                "scope",
                "selected_scope_depth",
                relative,
                observed=state["selected_scope_max_depth"],
                observed_is_lower_bound=False,
                depth=depth,
            )
            break
        state["selected_scope_max_depth"] = max(
            state["selected_scope_max_depth"],
            depth,
        )
        entries = _list_entries(
            directory,
            relative,
            state,
            phase="scope",
            depth=depth,
        )
        if state["halted"]:
            break

        files = []
        directories = []
        for entry in entries:
            child_relative = join_relative(relative, entry.name)
            reason = prune_reason(child_relative, root_only_overrides)
            if selected_scope == ".local" or selected_scope.startswith(".local/"):
                reason = reason or local_prune(child_relative)
            if reason:
                _record_exclusion(state, child_relative, reason, pruned=True)
                continue
            info = _entry_stat(
                state,
                entry,
                child_relative,
                phase="scope",
                depth=depth,
            )
            if state["halted"]:
                break
            if _info_is_reparse(info):
                _record_exclusion(state, child_relative, "unsafe-reparse")
                continue
            if stat.S_ISDIR(info.st_mode):
                if surface_observation(child_relative, is_directory=True):
                    state["surface_paths"].add(child_relative)
                directories.append((Path(entry.path), child_relative))
            elif stat.S_ISREG(info.st_mode):
                if surface_observation(child_relative, is_directory=False):
                    state["surface_paths"].add(child_relative)
                if is_document_path(entry.name):
                    files.append(
                        (
                            child_relative,
                            info.st_size,
                            evidence_factory(child_relative, info),
                        )
                    )
        if state["halted"]:
            break

        for child_relative, size, evidence in files:
            metadata["observed_path_count"] += 1
            if metadata["path_count"] >= INIT_DISCOVERY_LIMITS["selected_markdown_paths"]:
                state["scope_truncated"] = True
                state["halted"] = True
                metadata["truncated"] = True
                metadata["complete"] = False
                metadata["next_boundary"] = child_relative
                _record_boundary(state, "selected-markdown-paths", child_relative)
                break
            metadata["observed_bytes"] += size
            if metadata["bytes"] + size > INIT_DISCOVERY_LIMITS["selected_markdown_bytes"]:
                state["scope_truncated"] = True
                state["halted"] = True
                metadata["truncated"] = True
                metadata["complete"] = False
                metadata["next_boundary"] = child_relative
                _record_boundary(state, "selected-markdown-bytes", child_relative)
                break
            metadata["paths"].append({"path": child_relative, "bytes": size})
            state["selected_evidence"].append(evidence)
            metadata["path_count"] += 1
            metadata["bytes"] += size
        if state["halted"]:
            break

        for child_path, child_relative in reversed(directories):
            pending.append((child_path, child_relative, depth + 1))

    if metadata["complete"] and not metadata["truncated"] and not state["halted"]:
        metadata["paths"].sort(key=lambda item: sort_key(item["path"]))
        state["selected_evidence"].sort(key=lambda item: sort_key(item["path"]))

    if state["scope_truncated"]:
        metadata["truncated"] = True
        metadata["complete"] = False
    return metadata


def validate_root(state):
    root = state["root"]
    parts = root.parts
    current = Path(parts[0])
    components = [current]
    for part in parts[1:]:
        current = current / part
        components.append(current)

    final_info = None
    for component in components:
        final_info = _lstat_path(state, component, ".", phase="candidate")
        if state["halted"]:
            return False
        if final_info is None or not stat.S_ISDIR(final_info.st_mode):
            raise ValueError("root must be a real directory")
        if _info_is_reparse(final_info):
            raise ValueError("symlink or reparse path component")
    state["repository_identity"] = {
        "device": final_info.st_dev,
        "inode": final_info.st_ino,
        "mode": final_info.st_mode,
    }
    return True


__all__ = (
    "INIT_DISCOVERY_LIMITS",
    "_entry_stat",
    "_info_is_reparse",
    "_list_entries",
    "_lstat_path",
    "_record_boundary",
    "_record_exclusion",
    "_safe_directory_entry",
    "_scan_selected_scope",
    "_stop_physical_limit",
    "_take_metadata_operation",
    "inspect_root_entries",
    "scan_root_document_scope",
    "validate_root",
)
