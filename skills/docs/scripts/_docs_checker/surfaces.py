"""Data-driven, read-only policy for protected public documentation surfaces."""

import copy
import os
import stat
from pathlib import Path

from .knowledge import local_prune_reason
from .paths import _is_pruned_relative, normalize_repo_relative, safe_path


SURFACE_SCHEMA_VERSION = 1
SURFACE_SCAN_LIMITS = {
    "scandir_calls": 32,
    "raw_entries": 512,
    "depth": 2,
}
_PROBE_ROOTS = frozenset({".github", "docs"})
_LEGAL_NAMES = frozenset({"license", "notice", "citation.cff"})
_COMMUNITY_STEMS = frozenset(
    {
        "security",
        "contributing",
        "code_of_conduct",
        "support",
        "governance",
    }
)
_DESTRUCTIVE_ACTIONS = frozenset(
    {"move", "replace", "merge", "archive", "delete", "remove", "front-door-conversion"}
)
_DISPOSITION_ACTIONS = _DESTRUCTIVE_ACTIONS | {"retain"}


def _sort_key(value):
    return value.casefold(), value


def _normalized_paths(paths):
    if type(paths) not in {list, tuple}:
        raise ValueError("protected surface paths must be a sequence")
    normalized = []
    seen = set()
    for value in paths:
        if type(value) is not str:
            raise ValueError("protected surface path must be a string")
        path = normalize_repo_relative(value, "protected surface path")
        if path == "." or path != value:
            raise ValueError("protected surface path must be normalized")
        identity = path.casefold()
        if identity in seen:
            raise ValueError("protected surface paths must be unique")
        seen.add(identity)
        normalized.append(path)
    return sorted(normalized, key=_sort_key)


def _placement(path):
    if path.startswith(".github/") or path == ".github":
        return "github-community"
    if path.startswith("docs/") or path == "docs":
        return "docs"
    return "repository-root" if "/" not in path else "established-path"


def _stem(path):
    return Path(path).stem.casefold()


def _is_readme(path):
    candidate = Path(path)
    return candidate.stem.casefold() == "readme" and candidate.suffix.casefold() in {".md", ".markdown"}


def _base_policy(path, host):
    lowered = path.casefold()
    name = Path(path).name.casefold()
    stem = _stem(path)
    if _is_readme(path):
        return "repository-entry", "platform-recognized", True
    if name in _LEGAL_NAMES or stem in _COMMUNITY_STEMS:
        return "community-contract", "legal/community-governance", True
    if lowered == "agents.md":
        return "repository-instructions", "repository-convention", True
    if lowered == "evaluation.md":
        return "repository-evaluation", "repository-convention", True
    if lowered == ".github" or lowered.startswith(".github/"):
        return "host-community-surface", "platform-recognized", True
    if any(token in stem for token in ("changelog", "migration", "migrating", "release")):
        return "version-history", "externally-linked/stable-public-path", True
    if name in {"mkdocs.yml", "mkdocs.yaml", "docusaurus.config.js", "docusaurus.config.ts"}:
        return "documentation-site-config", "automation/tooling-consumed", True
    if lowered.startswith(("docs/site/", "website/docs/", "site/docs/", "public/docs/")):
        return "documentation-site-route", "externally-linked/stable-public-path", True
    if host == "unknown" and (lowered.startswith("docs/") or "/" not in path):
        return "established-public-path", "externally-linked/stable-public-path", True
    return "internal-documentation", "ordinary-internal-documentation", False


def surface_observation_allowed(path, *, is_directory):
    """Return whether generic observed metadata may enter the shared surface lane."""
    if _is_pruned_relative(path) or local_prune_reason(path) or path.startswith(".local/") or path == ".local":
        return False
    if is_directory:
        return path in {".github", "docs"} or path.startswith((".github/", "docs/"))
    _, _, protected = _base_policy(path, "github")
    return bool(protected or (path.startswith("docs/") and Path(path).suffix.casefold() in {".md", ".markdown"}))


def _references(value):
    if type(value) not in {list, tuple}:
        raise ValueError("protected surface references must be a sequence")
    references = {}
    for item in value:
        if type(item) is not dict or set(item) != {"source", "target", "kind"}:
            raise ValueError("protected surface reference is invalid")
        source = normalize_repo_relative(item["source"], "reference source")
        target = normalize_repo_relative(item["target"], "reference target")
        if source != item["source"] or target != item["target"] or item["kind"] not in {"automation", "tooling", "external-link"}:
            raise ValueError("protected surface reference is invalid")
        references.setdefault(target.casefold(), []).append(source)
    return references


def _external_routes(value):
    if type(value) not in {list, tuple}:
        raise ValueError("external routes must be a sequence")
    routes = []
    for item in value:
        if type(item) is not dict or set(item) != {"route", "provider", "availability"}:
            raise ValueError("external route is invalid")
        if (
            type(item["route"]) is not str
            or not item["route"]
            or item["provider"] not in {"github", "unknown"}
            or item["availability"] not in {"external-unavailable", "external-declared"}
        ):
            raise ValueError("external route is invalid")
        routes.append(
            {
                **item,
                "protected": True,
                "default_disposition": "retain",
            }
        )
    return sorted(routes, key=lambda item: _sort_key(item["route"]))


def classify_protected_surfaces(
    paths,
    *,
    host=None,
    references=(),
    external_routes=(),
    complete=True,
):
    """Classify observed paths without reading their contents."""
    paths = _normalized_paths(paths)
    if host is None:
        host = "github" if any(path == ".github" or path.startswith(".github/") for path in paths) else "unknown"
    if host not in {"github", "unknown"}:
        raise ValueError("unsupported protected surface host")
    reference_sources = _references(references)
    readmes = [path for path in paths if _is_readme(path)]
    readme_order = {"github-community": 0, "repository-root": 1, "docs": 2}
    surfaced_readme = min(
        readmes,
        key=lambda path: (readme_order.get(_placement(path), 3), _sort_key(path)),
        default=None,
    )
    items = []
    for path in paths:
        role, reason, protected = _base_policy(path, host)
        sources = sorted(reference_sources.get(path.casefold(), ()), key=_sort_key)
        if sources:
            reason = "automation/tooling-consumed"
            protected = True
        items.append(
            {
                "path": path,
                "role": role,
                "protection_reason": reason,
                "protected": protected,
                "placement": _placement(path),
                "surfaced": path == surfaced_readme,
                "default_disposition": "retain" if protected else "eligible-with-disposition",
                "compatibility_evidence": sources,
            }
        )
    return {
        "schema_version": SURFACE_SCHEMA_VERSION,
        "host": host,
        "items": items,
        "external_routes": _external_routes(external_routes),
        "complete": bool(complete),
        "healthy_placement_affects_score": False,
        "mutation_default": "retain",
        "preserve_by_default": [
            "path",
            "semantics",
            "rendering",
            "relative-links",
            "anchors",
            "tool-references",
        ],
    }


def validate_protected_surfaces(value):
    """Validate the exact protected-surface receipt without filesystem access."""
    if type(value) is not dict or set(value) != {
        "schema_version",
        "host",
        "items",
        "external_routes",
        "complete",
        "healthy_placement_affects_score",
        "mutation_default",
        "preserve_by_default",
    }:
        return False
    if (
        value["schema_version"] != SURFACE_SCHEMA_VERSION
        or value["host"] not in {"github", "unknown"}
        or type(value["items"]) is not list
        or type(value["external_routes"]) is not list
        or type(value["complete"]) is not bool
        or value["healthy_placement_affects_score"] is not False
        or value["mutation_default"] != "retain"
        or value["preserve_by_default"]
        != [
            "path",
            "semantics",
            "rendering",
            "relative-links",
            "anchors",
            "tool-references",
        ]
    ):
        return False
    previous = None
    surfaced = 0
    for item in value["items"]:
        if type(item) is not dict or set(item) != {
            "path",
            "role",
            "protection_reason",
            "protected",
            "placement",
            "surfaced",
            "default_disposition",
            "compatibility_evidence",
        }:
            return False
        path = item["path"]
        try:
            normalized = normalize_repo_relative(path, "protected surface path")
        except (TypeError, ValueError):
            return False
        order = (path.casefold(), path) if type(path) is str else None
        evidence = item["compatibility_evidence"]
        if (
            normalized != path
            or path == "."
            or (previous is not None and order <= previous)
            or type(item["role"]) is not str
            or not item["role"]
            or type(item["protection_reason"]) is not str
            or not item["protection_reason"]
            or type(item["protected"]) is not bool
            or item["placement"]
            not in {"github-community", "docs", "repository-root", "established-path"}
            or type(item["surfaced"]) is not bool
            or item["default_disposition"]
            not in {"retain", "eligible-with-disposition"}
            or type(evidence) is not list
            or any(
                type(source) is not str
                or normalize_repo_relative(source, "compatibility evidence") != source
                for source in evidence
            )
            or evidence != sorted(evidence, key=_sort_key)
            or (item["protected"] and item["default_disposition"] != "retain")
        ):
            return False
        previous = order
        surfaced += int(item["surfaced"])
    if surfaced > 1:
        return False
    previous_route = None
    for route in value["external_routes"]:
        if type(route) is not dict or set(route) != {
            "route",
            "provider",
            "availability",
            "protected",
            "default_disposition",
        }:
            return False
        order = (
            (route["route"].casefold(), route["route"])
            if type(route["route"]) is str
            else None
        )
        if (
            order is None
            or not route["route"]
            or (previous_route is not None and order <= previous_route)
            or route["provider"] not in {"github", "unknown"}
            or route["availability"]
            not in {"external-unavailable", "external-declared"}
            or route["protected"] is not True
            or route["default_disposition"] != "retain"
        ):
            return False
        previous_route = order
    references = [
        {"source": source, "target": item["path"], "kind": "tooling"}
        for item in value["items"]
        for source in item["compatibility_evidence"]
    ]
    external_routes = [
        {
            "route": route["route"],
            "provider": route["provider"],
            "availability": route["availability"],
        }
        for route in value["external_routes"]
    ]
    try:
        canonical = classify_protected_surfaces(
            [item["path"] for item in value["items"]],
            host=value["host"],
            references=references,
            external_routes=external_routes,
            complete=value["complete"],
        )
    except (TypeError, ValueError):
        return False
    return value == canonical


def inspect_protected_surfaces(root, *, host=None, references=(), external_routes=()):
    """Inspect only root, .github, and docs metadata under fixed physical caps."""
    root = Path(root).absolute()
    safe_path(root, root)
    paths = []
    scandir_calls = 0
    raw_entries = 0
    pending = [(root, ".", 0)]
    complete = True
    while pending:
        directory, relative, depth = pending.pop()
        if scandir_calls >= SURFACE_SCAN_LIMITS["scandir_calls"]:
            complete = False
            break
        scandir_calls += 1
        try:
            with os.scandir(directory) as iterator:
                entries = []
                for entry in iterator:
                    if raw_entries >= SURFACE_SCAN_LIMITS["raw_entries"]:
                        complete = False
                        break
                    raw_entries += 1
                    entries.append(entry)
        except OSError:
            complete = False
            break
        if not complete:
            break
        for entry in sorted(entries, key=lambda item: _sort_key(item.name)):
            child = entry.name if relative == "." else f"{relative}/{entry.name}"
            if _is_pruned_relative(child) or local_prune_reason(child):
                continue
            try:
                info = entry.stat(follow_symlinks=False)
            except OSError:
                complete = False
                break
            if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
                continue
            if stat.S_ISREG(info.st_mode):
                paths.append(child)
            elif stat.S_ISDIR(info.st_mode):
                if relative == "." and entry.name.casefold() in _PROBE_ROOTS:
                    paths.append(child)
                    pending.append((Path(entry.path), child, depth + 1))
                elif relative != "." and depth < SURFACE_SCAN_LIMITS["depth"]:
                    pending.append((Path(entry.path), child, depth + 1))
        if not complete:
            break
    result = classify_protected_surfaces(
        paths,
        host=host,
        references=references,
        external_routes=external_routes,
        complete=complete,
    )
    return {
        **result,
        "observed": {
            "scandir_calls": scandir_calls,
            "raw_entries": raw_entries,
            "limits": dict(SURFACE_SCAN_LIMITS),
        },
        "content_reads": 0,
        "evidence_reads": {"count": 0, "bytes": 0, "sources": []},
    }


def preview_protected_dispositions(classification, effects, *, exact_authorizations=()):
    """Preview compatibility guards without applying any effect."""
    if not validate_protected_surfaces(classification):
        raise ValueError("protected surface classification is invalid")
    protected_items = {
        item["path"]: item
        for item in classification["items"]
        if item["protected"] is True
    }
    classified_items = {item["path"]: item for item in classification["items"]}
    protected = set(protected_items)
    if type(effects) not in {list, tuple}:
        raise ValueError("protected disposition effects must be a sequence")
    if type(exact_authorizations) not in {list, tuple}:
        raise ValueError("protected authorizations must be a sequence")
    authorizations = []
    identities = set()
    for value in exact_authorizations:
        if type(value) is not str:
            raise ValueError("protected authorization is invalid")
        path = normalize_repo_relative(value, "protected authorization")
        identity = path.casefold()
        if path == "." or path != value or identity in identities:
            raise ValueError("protected authorization is invalid")
        identities.add(identity)
        authorizations.append(path)
    authorizations = set(authorizations)
    blocked = []
    retained = []
    normalized_effects = []
    for effect in effects:
        if type(effect) is not dict or set(effect) != {"path", "action", "disposition"}:
            raise ValueError("protected disposition effect is invalid")
        path = normalize_repo_relative(effect["path"], "protected effect path")
        action = effect["action"]
        disposition = effect["disposition"]
        if (
            path != effect["path"]
            or path not in classified_items
            or type(action) is not str
            or action not in _DISPOSITION_ACTIONS
            or type(disposition) is not str
            or not disposition
        ):
            raise ValueError("protected disposition effect is invalid")
        if path in protected and action == "retain":
            retained.append(path)
        elif path in protected and path not in authorizations:
            blocked.append(path)
        normalized_effects.append(
            {"path": path, "action": action, "disposition": disposition}
        )
    normalized_effects.sort(
        key=lambda item: (_sort_key(item["path"]), item["action"], item["disposition"])
    )
    effect_paths = [item["path"] for item in normalized_effects]
    if len(effect_paths) != len(set(path.casefold() for path in effect_paths)):
        raise ValueError("protected disposition paths must be unique")
    if not authorizations.issubset(set(effect_paths)):
        raise ValueError("protected authorization has no exact effect")
    if any(
        path not in protected
        or next(item for item in normalized_effects if item["path"] == path)["action"]
        == "retain"
        for path in authorizations
    ):
        raise ValueError("protected authorization is not required by an exact effect")
    affected_protected = sorted(
        protected.intersection(item["path"] for item in normalized_effects),
        key=_sort_key,
    )
    return {
        "status": "blocked" if blocked else "allowed-preview",
        "surface_classification": copy.deepcopy(classification),
        "blocked_paths": sorted(set(blocked), key=_sort_key),
        "protected_paths_retained": sorted(set(retained), key=_sort_key),
        "effects": normalized_effects,
        "exact_authorizations": sorted(authorizations, key=_sort_key),
        "protected_evidence": [
            {
                "path": path,
                "role": protected_items[path]["role"],
                "protection_reason": protected_items[path]["protection_reason"],
                "compatibility_evidence": list(
                    protected_items[path]["compatibility_evidence"]
                ) or [path],
                "preserve": list(classification["preserve_by_default"]),
            }
            for path in affected_protected
        ],
        "writes": 0,
    }


def validate_protected_disposition_preview(value):
    """Validate the closed, data-only compatibility preview consumed by lifecycle."""
    if type(value) is not dict or set(value) != {
        "status",
        "surface_classification",
        "blocked_paths",
        "protected_paths_retained",
        "effects",
        "exact_authorizations",
        "protected_evidence",
        "writes",
    }:
        return False
    if (
        value["status"] not in {"blocked", "allowed-preview"}
        or value["writes"] != 0
        or not validate_protected_surfaces(value["surface_classification"])
    ):
        return False
    classified_items = {
        item["path"].casefold(): item
        for item in value["surface_classification"]["items"]
    }
    list_fields = (
        "blocked_paths",
        "protected_paths_retained",
        "effects",
        "exact_authorizations",
        "protected_evidence",
    )
    if any(type(value[field]) is not list for field in list_fields):
        return False
    try:
        paths = {}
        for effect in value["effects"]:
            if type(effect) is not dict or set(effect) != {"path", "action", "disposition"}:
                return False
            path = normalize_repo_relative(effect["path"], "protected effect path")
            if (
                path != effect["path"]
                or path.casefold() in paths
                or path.casefold() not in classified_items
                or effect["action"] not in _DISPOSITION_ACTIONS
                or type(effect["disposition"]) is not str
                or not effect["disposition"]
            ):
                return False
            paths[path.casefold()] = effect
        authorizations = _normalized_paths(value["exact_authorizations"])
        blocked = _normalized_paths(value["blocked_paths"])
        retained = _normalized_paths(value["protected_paths_retained"])
        if any(path.casefold() not in paths for path in (*authorizations, *blocked, *retained)):
            return False
        evidence_paths = set()
        preserve_contract = [
            "path",
            "semantics",
            "rendering",
            "relative-links",
            "anchors",
            "tool-references",
        ]
        for item in value["protected_evidence"]:
            if type(item) is not dict or set(item) != {
                "path",
                "role",
                "protection_reason",
                "compatibility_evidence",
                "preserve",
            }:
                return False
            path = normalize_repo_relative(item["path"], "protected evidence path")
            if (
                path != item["path"]
                or path.casefold() not in paths
                or path.casefold() in evidence_paths
                or type(item["role"]) is not str
                or not item["role"]
                or type(item["protection_reason"]) is not str
                or not item["protection_reason"]
                or type(item["compatibility_evidence"]) is not list
                or not item["compatibility_evidence"]
                or any(
                    type(source) is not str
                    or normalize_repo_relative(source, "compatibility evidence")
                    != source
                    for source in item["compatibility_evidence"]
                )
                or item["preserve"] != preserve_contract
            ):
                return False
            classified = classified_items[path.casefold()]
            if (
                classified["protected"] is not True
                or item["role"] != classified["role"]
                or item["protection_reason"] != classified["protection_reason"]
                or item["compatibility_evidence"]
                != (classified["compatibility_evidence"] or [classified["path"]])
                or item["preserve"]
                != value["surface_classification"]["preserve_by_default"]
            ):
                return False
            evidence_paths.add(path.casefold())
    except (TypeError, ValueError):
        return False
    if value["status"] == "allowed-preview" and blocked:
        return False
    if value["status"] == "blocked" and not blocked:
        return False
    authorization_ids = {path.casefold() for path in authorizations}
    blocked_ids = {path.casefold() for path in blocked}
    retained_ids = {path.casefold() for path in retained}
    protected_effects = {
        identity: effect
        for identity, effect in paths.items()
        if classified_items[identity]["protected"] is True
    }
    destructive_ids = {
        identity
        for identity, effect in protected_effects.items()
        if effect["action"] != "retain"
    }
    expected_retained = set(protected_effects) - destructive_ids
    if (
        not authorization_ids.issubset(destructive_ids)
        or blocked_ids != destructive_ids - authorization_ids
        or retained_ids != expected_retained
        or evidence_paths != set(protected_effects)
    ):
        return False
    return True


__all__ = (
    "SURFACE_SCAN_LIMITS",
    "SURFACE_SCHEMA_VERSION",
    "classify_protected_surfaces",
    "inspect_protected_surfaces",
    "preview_protected_dispositions",
    "surface_observation_allowed",
    "validate_protected_disposition_preview",
    "validate_protected_surfaces",
)
