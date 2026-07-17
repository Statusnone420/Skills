"""Bounded, metadata-first first-contact documentation discovery."""

import hashlib
import json
import os
import re
import stat
from collections.abc import Mapping, Sequence
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
    _take_metadata_operation,
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
from .formats import is_document_path
from .paths import (
    _path_identity,
    normalize_repo_relative,
    prune_summary,
    tracked_markdown_scope,
)
from .receipt import (
    DISCOVERY_CONTRACT_VERSION,
    DISCOVERY_FIELDS,
    discovery_fields,
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
_CORPUS_COVERAGE_VERSION = "init-corpus-v1"
_CORPUS_ORDERING_VERSION = "repo-relative-casefold-v1"
_CORPUS_COVERAGE_MODES = frozenset({"selected-scope-exact", "empty-adoption"})


class CorpusValidationError(ValueError):
    """One stable corpus-completeness failure."""

    def __init__(self, classification):
        super().__init__(classification)
        self.classification = classification


def _canonical_corpus_bytes(value):
    try:
        return (
            json.dumps(
                value,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=True,
                allow_nan=False,
            )
            + "\n"
        ).encode("utf-8")
    except (TypeError, ValueError, RecursionError, OverflowError) as exc:
        raise ValueError("corpus payload is not canonical JSON") from exc


def _corpus_path_identity(path):
    return normalize_repo_relative(path, "corpus path").casefold()


def _corpus_object(paths, selected_scope, coverage_mode):
    paths = sorted(paths, key=_sort_key)
    digest_input = {
        "ordering_version": _CORPUS_ORDERING_VERSION,
        "paths": paths,
    }
    return {
        "coverage_version": _CORPUS_COVERAGE_VERSION,
        "coverage_mode": coverage_mode,
        "ordering_version": _CORPUS_ORDERING_VERSION,
        "selected_scope": selected_scope,
        "write_boundary": "." if coverage_mode == "empty-adoption" else selected_scope,
        "path_count": len(paths),
        "paths_digest": "sha256:"
        + hashlib.sha256(_canonical_corpus_bytes(digest_input)).hexdigest(),
    }


def _corpus_scan_failure(classification):
    return {
        "complete": False,
        "paths": [],
        "content_reads": 0,
        "corpus": None,
        "boundary": {
            "classification": classification,
            "phase": "corpus-scan",
        },
    }


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


def _windows_change_time(path):
    """Return NTFS change time without opening a document body."""
    import ctypes
    from ctypes import wintypes

    class FileBasicInfo(ctypes.Structure):
        _fields_ = (
            ("creation_time", ctypes.c_longlong),
            ("last_access_time", ctypes.c_longlong),
            ("last_write_time", ctypes.c_longlong),
            ("change_time", ctypes.c_longlong),
            ("file_attributes", wintypes.DWORD),
        )

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateFileW.argtypes = (
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.LPVOID,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.HANDLE,
    )
    kernel32.CreateFileW.restype = wintypes.HANDLE
    kernel32.GetFileInformationByHandleEx.argtypes = (
        wintypes.HANDLE,
        ctypes.c_int,
        wintypes.LPVOID,
        wintypes.DWORD,
    )
    kernel32.GetFileInformationByHandleEx.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)

    handle = kernel32.CreateFileW(
        os.fspath(path),
        0,
        0x00000001 | 0x00000002 | 0x00000004,
        None,
        3,
        0x00200000 | 0x02000000,
        None,
    )
    invalid_handle = wintypes.HANDLE(-1).value
    if handle in (None, invalid_handle):
        raise OSError(ctypes.get_last_error(), "metadata identity unavailable")
    try:
        info = FileBasicInfo()
        if not kernel32.GetFileInformationByHandleEx(
            handle,
            0,
            ctypes.byref(info),
            ctypes.sizeof(info),
        ):
            raise OSError(ctypes.get_last_error(), "metadata identity unavailable")
        return info.change_time
    finally:
        kernel32.CloseHandle(handle)


def _change_identity_factory(state):
    cache = {}

    def change_identity(evidence):
        path = evidence["path"]
        if path not in cache:
            if not _take_metadata_operation(state, "content", path):
                raise OSError("metadata identity limit reached")
            current = os.lstat(state["root"] / Path(path))
            expected = tuple(
                evidence[field]
                for field in ("bytes", "modified_ns", "mode")
            )
            observed = (
                current.st_size,
                current.st_mtime_ns,
                current.st_mode,
            )
            identity_changed = bool(
                (evidence["device"] and evidence["device"] != current.st_dev)
                or (evidence["inode"] and evidence["inode"] != current.st_ino)
            )
            if expected != observed or identity_changed or _info_is_reparse(current):
                raise OSError("metadata identity changed during discovery")
            cache[path] = (
                _windows_change_time(state["root"] / Path(path))
                if os.name == "nt"
                else current.st_ctime_ns
            )
        return cache[path]

    return change_identity


def _plan_content_batch(
    state,
    scope_metadata,
    selected_scope,
    continuation,
):
    if scope_metadata["truncated"]:
        return _empty_content_batch(blocked=True), _empty_continuation(blocked=True)
    try:
        batch, continuation_result = plan_content_batch(
            scope_metadata["paths"],
            state["selected_evidence"],
            selected_scope,
            continuation=continuation,
            repository_identity=state["repository_identity"],
            file_limit=INIT_DISCOVERY_LIMITS["content_files"],
            byte_limit=INIT_DISCOVERY_LIMITS["content_bytes"],
            change_identity=_change_identity_factory(state),
        )
    except OSError:
        state["content_blocked"] = True
        return _empty_content_batch(blocked=True), _empty_continuation(blocked=True)
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


def _initial_state(root):
    return {
        "root": root,
        "legacy_missing_ok": False,
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
        "content_reads": 0,
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
        "tracked_metadata": {},
        "has_root_instructions": False,
        "local_candidates": [],
        "surface_paths": set(),
        "repository_identity": None,
    }


def _tracked_scope_paths(tracked_paths, selected_scope):
    """Return tracked routes governed by one selected discovery scope."""
    if tracked_paths is None:
        return None
    if selected_scope == ".":
        return [
            path
            for path in tracked_paths
            if "/" not in path and is_maintained_root_document(path)
        ]
    scope_parts = Path(_path_identity(selected_scope)).parts
    matches = []
    for path in tracked_paths:
        path_parts = Path(_path_identity(path)).parts
        if (
            len(path_parts) > len(scope_parts)
            and path_parts[: len(scope_parts)] == scope_parts
        ):
            matches.append(path)
    return matches


def _budgeted_tracked_route_info(state, relative, *, phase):
    """Validate one Git route component-by-component through the Init budget."""
    relative = normalize_repo_relative(relative, "tracked path")
    current = state["root"]
    current_relative = "."
    parts = tuple(Path(relative).parts)
    for index, part in enumerate(parts):
        current = current / part
        current_relative = _join_relative(current_relative, part)
        identity = _path_identity(current_relative)
        if identity in state["tracked_metadata"]:
            info = state["tracked_metadata"][identity]
        else:
            info = _lstat_path(
                state,
                current,
                current_relative,
                phase=phase,
                depth=index,
                missing_ok=True,
            )
            state["tracked_metadata"][identity] = info
        if state["halted"] or info is None:
            return None
        if _info_is_reparse(info):
            raise ValueError("tracked path crosses a symlink or reparse component")
        if index < len(parts) - 1:
            if not stat.S_ISDIR(info.st_mode):
                return None
        elif not stat.S_ISREG(info.st_mode):
            return None
    return info


def _tracked_repository_markdown(state):
    """Resolve Git visibility and budget every tracked-route validation."""
    if state["halted"]:
        return None
    marker = _lstat_path(
        state,
        state["root"] / ".git",
        ".git",
        phase="candidate",
        missing_ok=True,
    )
    if state["halted"]:
        return None
    inventory = tracked_markdown_scope(
        state["root"],
        ".",
        git_marker_present=marker is not None,
        inventory_only=True,
        include_navigation=True,
    )
    if inventory is None:
        return None
    tracked = []
    for relative in inventory:
        info = _budgeted_tracked_route_info(
            state,
            relative,
            phase="candidate",
        )
        if state["halted"]:
            break
        if info is not None:
            if surface_observation_allowed(relative, is_directory=False):
                state["surface_paths"].add(relative)
            if is_document_path(relative):
                tracked.append(relative)
    return tracked


def _tracked_scope_metadata(state, selected_scope, tracked_paths):
    """Build bounded metadata without traversing local-only filesystem trees."""
    paths = _tracked_scope_paths(tracked_paths, selected_scope)
    if paths is None:
        return None
    metadata = _empty_scope_metadata()
    metadata["complete"] = True
    for relative in paths:
        if metadata["path_count"] >= INIT_DISCOVERY_LIMITS["selected_markdown_paths"]:
            state["scope_truncated"] = True
            metadata.update(
                complete=False,
                truncated=True,
                next_boundary=relative,
            )
            _record_boundary(state, "selected-markdown-paths", relative)
            break
        info = _budgeted_tracked_route_info(
            state,
            relative,
            phase="scope",
        )
        if state["halted"] or info is None:
            metadata["complete"] = False
            break
        if _info_is_reparse(info) or not stat.S_ISREG(info.st_mode):
            state["halted"] = True
            metadata["complete"] = False
            _record_boundary(state, "unsafe-container", relative)
            break
        metadata["observed_path_count"] += 1
        metadata["observed_bytes"] += info.st_size
        if metadata["bytes"] + info.st_size > INIT_DISCOVERY_LIMITS["selected_markdown_bytes"]:
            state["scope_truncated"] = True
            metadata.update(
                complete=False,
                truncated=True,
                next_boundary=relative,
            )
            _record_boundary(state, "selected-markdown-bytes", relative)
            break
        metadata["paths"].append({"path": relative, "bytes": info.st_size})
        metadata["path_count"] += 1
        metadata["bytes"] += info.st_size
        state["selected_evidence"].append(root_document_evidence(relative, info))
    return metadata


def _filter_discovery_to_tracked(state, tracked_paths):
    """Remove local-only routes from shared discovery candidates and evidence."""
    if tracked_paths is None:
        return
    identities = {_path_identity(path) for path in tracked_paths}
    state["root_documents"] = [
        evidence
        for evidence in state["root_documents"]
        if _path_identity(evidence["path"]) in identities
    ]

    def has_tracked_descendant(candidate):
        prefix = candidate["path"] + "/"
        return any(path.startswith(prefix) for path in tracked_paths)

    state["candidates"] = [
        candidate
        for candidate in state["candidates"]
        if has_tracked_descendant(candidate)
    ]
    state["candidate_keys"] = {
        _path_identity(candidate["path"]) for candidate in state["candidates"]
    }
    state["observed_candidate_roots"] = len(state["candidates"])


def _discover_tracked_candidates(state, tracked_paths):
    """Derive Git-backed candidate roots without walking local-only trees."""
    candidate_sources = {}
    for relative in tracked_paths:
        parts = relative.split("/")
        if len(parts) == 1:
            if is_maintained_root_document(relative):
                info = _budgeted_tracked_route_info(
                    state,
                    relative,
                    phase="candidate",
                )
                if state["halted"] or info is None:
                    return
                state["root_documents"].append(
                    root_document_evidence(relative, info)
                )
            if relative.casefold() == "agents.md":
                state["has_root_instructions"] = True
            continue
        if parts[0].casefold() in _DOC_ROOT_KEYS:
            candidate_sources.setdefault(parts[0], "root")
        if len(parts) >= 3 and parts[1].casefold() in _DOC_ROOT_KEYS:
            candidate_sources.setdefault(
                "/".join(parts[:2]),
                "direct-child",
            )
        if (
            len(parts) >= 4
            and parts[0].casefold() in _PACKAGE_CONTAINER_KEYS
            and parts[2].casefold() in _DOC_ROOT_KEYS
        ):
            candidate_sources.setdefault(
                "/".join(parts[:3]),
                f"container:{parts[0]}",
            )
    for relative in sorted(candidate_sources, key=_sort_key):
        _add_candidate(state, relative, candidate_sources[relative])


def scan_selected_document_corpus(
    root,
    selected_scope,
    coverage_mode,
    *,
    additional_shared_paths=(),
):
    """Rederive one bounded metadata-only Markdown corpus for Init closeout."""
    if coverage_mode not in _CORPUS_COVERAGE_MODES:
        raise ValueError("corpus coverage mode is invalid")
    raw_selected_scope = selected_scope
    try:
        selected_scope = normalize_repo_relative(selected_scope, "selected scope")
    except (TypeError, ValueError):
        return _corpus_scan_failure("incomplete-corpus")
    if coverage_mode == "empty-adoption" and selected_scope != ".":
        raise ValueError("empty-adoption requires root write jurisdiction")

    root = Path(root).absolute()
    state = _initial_state(root)
    try:
        validate_root(state)
        tracked_paths = _tracked_repository_markdown(state)
        if tracked_paths is not None:
            additions = [
                normalize_repo_relative(path, "additional shared path")
                for path in additional_shared_paths
            ]
            tracked_paths = sorted(
                set(tracked_paths).union(additions),
                key=_sort_key,
            )
        _, normalized_scope, root_only_overrides = _validated_explicit_scope(
            state,
            raw_selected_scope,
        )
        if state["halted"]:
            return _corpus_scan_failure(
                "incomplete-corpus" if state["io_errors"] else "corpus-scope-limited"
            )
        metadata = _tracked_scope_metadata(
            state,
            normalized_scope,
            tracked_paths,
        )
        if metadata is not None:
            pass
        elif normalized_scope == ".":
            inspect_root_entries(
                state,
                is_root_document=is_maintained_root_document,
                evidence_factory=root_document_evidence,
                surface_observation=surface_observation_allowed,
            )
            metadata = scan_root_document_scope(state)
        else:
            metadata = _scan_selected_scope(
                state,
                normalized_scope,
                root_only_overrides,
                local_prune=local_prune_reason,
                surface_observation=surface_observation_allowed,
                evidence_factory=root_document_evidence,
            )
    except (OSError, TypeError, ValueError):
        return _corpus_scan_failure("incomplete-corpus")

    if (
        state["physical_limit"] is not None
        or state["scope_truncated"]
        or metadata.get("truncated")
    ):
        return _corpus_scan_failure("corpus-scope-limited")
    if state["halted"] or state["io_errors"] or metadata.get("complete") is not True:
        return _corpus_scan_failure("incomplete-corpus")

    paths = [item["path"] for item in metadata["paths"]]
    paths.sort(key=_sort_key)
    return {
        "complete": True,
        "paths": paths,
        "content_reads": state["content_reads"],
        "corpus": _corpus_object(paths, normalized_scope, coverage_mode),
        "boundary": None,
    }


def validate_corpus_coverage(starting_scan, dispositions):
    """Require one exact whole-file base disposition per scanned path."""
    if not isinstance(starting_scan, Mapping) or starting_scan.get("complete") is not True:
        raise CorpusValidationError("corpus-scope-limited")
    paths = starting_scan.get("paths")
    if not isinstance(paths, Sequence) or isinstance(paths, (str, bytes, bytearray)):
        raise CorpusValidationError("incomplete-corpus")

    scanned = {}
    for path in paths:
        try:
            normalized = normalize_repo_relative(path, "scanned corpus path")
            identity = _corpus_path_identity(normalized)
        except (TypeError, ValueError) as exc:
            raise CorpusValidationError("incomplete-corpus") from exc
        if identity in scanned:
            raise CorpusValidationError("duplicate-document-disposition")
        scanned[identity] = normalized

    if not isinstance(dispositions, Sequence) or isinstance(
        dispositions,
        (str, bytes, bytearray),
    ):
        raise CorpusValidationError("incomplete-corpus")
    normalized_items = []
    covered = set()
    subordinate_paths = set()
    for item in dispositions:
        if not isinstance(item, Mapping):
            raise CorpusValidationError("unsupported-item-granularity")
        section = item.get("section")
        if not isinstance(section, Mapping):
            raise CorpusValidationError("unsupported-item-granularity")
        section = dict(section)
        whole_file = section == {"kind": "whole-file"}
        subordinate = section.get("kind") == "atx-section-v1"
        if not whole_file and not subordinate:
            raise CorpusValidationError("unsupported-item-granularity")
        try:
            path = normalize_repo_relative(item.get("path"), "disposition path")
            identity = _corpus_path_identity(path)
        except (TypeError, ValueError) as exc:
            raise CorpusValidationError("foreign-disposition") from exc
        expected_spelling = scanned.get(identity)
        if expected_spelling is None:
            raise CorpusValidationError("foreign-disposition")
        if path != expected_spelling:
            raise CorpusValidationError("duplicate-document-disposition")
        if whole_file:
            if identity in covered:
                raise CorpusValidationError("duplicate-document-disposition")
            covered.add(identity)
        else:
            subordinate_paths.add(identity)
        normalized_items.append(dict(item))

    if not subordinate_paths.issubset(covered):
        raise CorpusValidationError("unsupported-item-granularity")
    if covered != set(scanned):
        raise CorpusValidationError("incomplete-corpus")
    normalized_items.sort(
        key=lambda item: (
            _sort_key(item["path"]),
            0 if dict(item["section"]) == {"kind": "whole-file"} else 1,
            str(item.get("item_id", "")).casefold(),
            str(item.get("item_id", "")),
        )
    )
    return tuple(normalized_items)


def derive_result_corpus(starting_scan, document_operations):
    """Derive the approval-bound result path set without reading document bodies."""
    if not isinstance(starting_scan, Mapping) or starting_scan.get("complete") is not True:
        raise CorpusValidationError("corpus-scope-limited")
    corpus = starting_scan.get("corpus")
    paths = starting_scan.get("paths")
    if not isinstance(corpus, Mapping) or not isinstance(paths, Sequence):
        raise CorpusValidationError("incomplete-corpus")
    if not isinstance(document_operations, Sequence) or isinstance(
        document_operations,
        (str, bytes, bytearray),
    ):
        raise ValueError("document operations must be an array")

    by_identity = {}
    for path in paths:
        normalized = normalize_repo_relative(path, "starting corpus path")
        identity = _corpus_path_identity(normalized)
        if identity in by_identity:
            raise CorpusValidationError("duplicate-document-disposition")
        by_identity[identity] = normalized

    seen_operations = set()
    creates = {}
    deletes = set()
    for raw in document_operations:
        if not isinstance(raw, Mapping):
            raise ValueError("document operation must be an object")
        operation = raw.get("operation")
        if operation not in {"CREATE", "REPLACE", "DELETE"}:
            raise ValueError("document operation is invalid")
        path = normalize_repo_relative(raw.get("path"), "document operation path")
        identity = _corpus_path_identity(path)
        if identity in seen_operations:
            raise ValueError("document operation paths must be unique")
        seen_operations.add(identity)
        if operation == "CREATE":
            if identity in by_identity:
                raise ValueError("CREATE requires an absent path")
            creates[identity] = path
        elif operation == "REPLACE":
            if identity not in by_identity:
                raise ValueError("REPLACE requires an existing corpus path")
        else:
            if identity not in by_identity:
                raise ValueError("DELETE requires an existing corpus path")
            deletes.add(identity)

    result_paths = [
        path for identity, path in by_identity.items() if identity not in deletes
    ]
    result_paths.extend(creates.values())
    result_paths.sort(key=_sort_key)
    return _corpus_object(
        result_paths,
        corpus["selected_scope"],
        corpus["coverage_mode"],
    )


def discover_init_scope(
    root,
    explicit_scope=None,
    continuation=None,
    *,
    contract_version=DISCOVERY_CONTRACT_VERSION,
    _prepared_state=None,
):
    """Return deterministic first-contact metadata with bounded content identity."""
    discovery_fields(contract_version)
    root = Path(root).absolute()
    if _prepared_state is None:
        state = _initial_state(root)
        validate_root(state)
    else:
        state = _prepared_state
        if state.get("root") != root:
            raise ValueError("prepared discovery root does not match")

    requested_scope = (
        None if explicit_scope is None else os.fspath(explicit_scope)
    )
    tracked_paths = _tracked_repository_markdown(state)
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
    elif explicit_narrow:
        _add_candidate(state, normalized_scope, "explicit")
        selected_scope = normalized_scope
        selection_reason = "explicit-scope"
        metadata_phases = 1
    else:
        if tracked_paths is None:
            _discover_automatic_candidates(
                state,
                include_local=True,
            )
        else:
            _discover_tracked_candidates(state, tracked_paths)
        _filter_discovery_to_tracked(state, tracked_paths)
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
        tracked_metadata = _tracked_scope_metadata(
            state,
            selected_scope,
            tracked_paths,
        )
        scope_metadata = (
            tracked_metadata
            if tracked_metadata is not None
            else scan_root_document_scope(state)
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
    if explicit_narrow and not state["halted"]:
        inspect_root_entries(
            state,
            is_root_document=is_maintained_root_document,
            evidence_factory=root_document_evidence,
            surface_observation=surface_observation_allowed,
        )
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
        not explicit_narrow
        and not state["candidate_truncated"]
        and not state["io_errors"]
    )
    internal_result = {
        "schema_version": DISCOVERY_CONTRACT_VERSION,
        "mode": "init-discovery",
        "status": status,
        "root": ".",
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
                or state["content_truncated"]
                or state["content_blocked"]
                or state["continuation_rejected"]
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
        "content_reads": state["content_reads"],
    }
    if set(internal_result) != DISCOVERY_FIELDS:
        raise AssertionError("discovery result fields drifted from schema 3")
    return internal_result


def prepare_init_discovery(root, initialization_preflight):
    """Budget root/control probes before optional operational preflight."""
    root = Path(root).absolute()
    state = _initial_state(root)
    validate_root(state)
    result = None
    if not state["halted"]:
        control = _lstat_path(
            state,
            root / ".diataxis",
            ".diataxis",
            phase="candidate",
            missing_ok=True,
        )
        if control is not None and not state["halted"]:
            result = initialization_preflight(root)
    return state, result


__all__ = (
    "CorpusValidationError",
    "DOCUMENTATION_ROOT_NAMES",
    "INIT_DISCOVERY_LIMITS",
    "MAINTAINED_ROOT_DOCUMENT_NAMES",
    "PACKAGE_CONTAINER_NAMES",
    "derive_result_corpus",
    "discover_init_scope",
    "prepare_init_discovery",
    "scan_selected_document_corpus",
    "validate_corpus_coverage",
)
