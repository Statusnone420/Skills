"""Bounded, vendor-neutral navigation evidence providers."""

import json
import os
import posixpath
from collections.abc import Mapping
from pathlib import Path, PurePosixPath
from urllib.parse import unquote, urlsplit

from .formats import (
    DOCUMENT_SUFFIXES,
    MAX_FRONTMATTER_BYTES,
    is_document_path,
    is_navigation_manifest_path,
    parse_frontmatter_scalars,
)
from .paths import (
    _is_reparse,
    _relative_posix,
    iter_markdown_scope,
    normalize_repo_relative,
    safe_path,
    tracked_markdown_scope,
)


MAX_NAVIGATION_MANIFEST_BYTES = 256 * 1024
MAX_NAVIGATION_DEPTH = 32
MAX_NAVIGATION_PAGES = 10_000
MAX_NAVIGATION_REDIRECTS = 2_048
MAX_NAVIGATION_STRING_BYTES = 16 * 1024
MAX_REDIRECT_HOPS = 8
MAX_PAGE_BYTES = 2 * 1024 * 1024
MINTLIFY_SCHEMA_URLS = frozenset(
    {
        "https://mintlify.com/docs.json",
        "https://mintlify.com/schema.json",
    }
)
_UNSUPPORTED_MANIFEST_KEYS = frozenset({"$ref", "openapi", "personalization"})
_CONTEXT_KEYS = ("tab", "group", "dropdown", "anchor", "product", "version", "language", "item")
_CONTAINER_KEYS = (
    "global",
    "languages",
    "versions",
    "tabs",
    "anchors",
    "dropdowns",
    "products",
    "groups",
    "pages",
    "menu",
)
_ALLOWED_NAVIGATION_FIELDS = frozenset(
    {*_CONTAINER_KEYS, *_CONTEXT_KEYS, "root", "page", "hidden", "icon"}
)
_UNSET = object()


class NavigationBoundary(ValueError):
    """A recognized navigation surface that cannot be measured safely."""

    def __init__(self, result):
        self.result = result
        super().__init__(result.get("classification", "unsupported-documentation-navigation-manifest"))


def _sort_key(value):
    return value.casefold(), value


def _base_result(
    *, status, provider, scope, authority, manifest_bytes=0, provider_root=None
):
    return {
        "status": status,
        "provider": provider,
        "scope": scope,
        "provider_root": provider_root,
        "authority": authority,
        "entry": None,
        "navigated_pages": [],
        "hidden_pages": [],
        "redirects": [],
        "unsupported_features": [],
        "contexts": {},
        "findings": [],
        "limits": {
            "manifest_bytes": manifest_bytes,
            "max_manifest_bytes": MAX_NAVIGATION_MANIFEST_BYTES,
            "max_depth": MAX_NAVIGATION_DEPTH,
            "max_pages": MAX_NAVIGATION_PAGES,
            "max_redirects": MAX_NAVIGATION_REDIRECTS,
            "max_page_bytes": MAX_PAGE_BYTES,
            "max_frontmatter_bytes": MAX_FRONTMATTER_BYTES,
            "max_redirect_hops": MAX_REDIRECT_HOPS,
        },
    }


def _unmeasured(
    *,
    provider,
    scope,
    authority,
    manifest_bytes=0,
    features=(),
    classification="unsupported-documentation-navigation-manifest",
    provider_root=None,
):
    result = _base_result(
        status="unmeasured",
        provider=provider,
        scope=scope,
        authority=authority,
        manifest_bytes=manifest_bytes,
        provider_root=provider_root,
    )
    result["classification"] = classification
    result["unsupported_features"] = sorted(set(features), key=_sort_key)
    return result


def _manifest_candidates(scope):
    if scope == ".":
        return ("docs.json", "docs/docs.json")
    parts = scope.split("/")
    return tuple(
        [
            f"{'/'.join(parts[:depth])}/docs.json"
            for depth in range(len(parts), 0, -1)
        ]
        + ["docs.json"]
    )


def _candidate_manifest_paths(root, scope):
    candidates = []
    for relative in _manifest_candidates(scope):
        try:
            candidate = safe_path(root / relative, root)
        except ValueError:
            continue
        if not os.path.lexists(candidate):
            continue
        if _is_reparse(candidate) or not candidate.is_file():
            return None, relative, "unsafe-manifest"
        candidates.append((relative, candidate))
    if len(candidates) > 1:
        return None, None, "ambiguous-manifest"
    return (candidates[0] if candidates else None), None, None


def _strict_json_loads(data):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError("duplicate JSON key")
            result[key] = value
        return result

    def reject_constant(value):
        raise ValueError(f"invalid JSON constant: {value}")

    return json.loads(
        data,
        object_pairs_hook=pairs,
        parse_constant=reject_constant,
    )


def _validate_json_limits(value, *, depth=0, counts=None):
    if counts is None:
        counts = {"nodes": 0, "strings": 0}
    if depth > MAX_NAVIGATION_DEPTH:
        raise ValueError("navigation manifest exceeds maximum depth")
    counts["nodes"] += 1
    if counts["nodes"] > MAX_NAVIGATION_PAGES * 4:
        raise ValueError("navigation manifest exceeds maximum nodes")
    if isinstance(value, str):
        counts["strings"] += 1
        if len(value.encode("utf-8", "strict")) > MAX_NAVIGATION_STRING_BYTES:
            raise ValueError("navigation manifest string exceeds capacity")
    elif isinstance(value, Mapping):
        for key, child in value.items():
            if not isinstance(key, str):
                raise ValueError("navigation manifest key is invalid")
            _validate_json_limits(child, depth=depth + 1, counts=counts)
    elif isinstance(value, list):
        for child in value:
            _validate_json_limits(child, depth=depth + 1, counts=counts)
    elif value is not None and type(value) not in {bool, int, float}:
        raise ValueError("navigation manifest value is invalid")
    return counts


def _unsupported_keys(value, *, path=()):
    found = []
    if isinstance(value, Mapping):
        for key, child in value.items():
            if key in _UNSUPPORTED_MANIFEST_KEYS:
                found.append(key)
            found.extend(_unsupported_keys(child, path=(*path, key)))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            found.extend(_unsupported_keys(child, path=(*path, str(index))))
    return found


def _route_parts(raw, *, label, allow_fragment=False):
    if not isinstance(raw, str) or not raw:
        raise ValueError(f"{label} must be a non-empty string")
    if len(raw.encode("utf-8", "strict")) > MAX_NAVIGATION_STRING_BYTES:
        raise ValueError(f"{label} exceeds capacity")
    try:
        parsed = urlsplit(raw)
    except ValueError as exc:
        raise ValueError(f"{label} is malformed") from exc
    if parsed.scheme or parsed.netloc or raw.startswith("\\") or raw.startswith("//"):
        raise ValueError(f"{label} is outside the provider root")
    if parsed.query or (parsed.fragment and not allow_fragment):
        raise ValueError(f"{label} contains unsupported query or fragment")
    path = unquote(parsed.path.replace("\\", "/"))
    if not path:
        path = "."
    if any(part == ".." for part in PurePosixPath(path).parts):
        raise ValueError(f"{label} contains traversal")
    if path.startswith("/"):
        path = path[1:]
    normalized = posixpath.normpath(path)
    if normalized in {"", "."}:
        normalized = "."
    if normalized == ".." or normalized.startswith("../"):
        raise ValueError(f"{label} contains traversal")
    return normalized, parsed.fragment if allow_fragment else ""


def _provider_relative(scope, path):
    scope = "." if scope == "." else scope.rstrip("/")
    if scope == ".":
        return path
    if path == scope:
        return "."
    prefix = scope + "/"
    if not path.startswith(prefix):
        raise ValueError("route escapes provider root")
    return path[len(prefix) :]


def _provider_absolute(scope, relative):
    if scope == ".":
        return relative
    return f"{scope}/{relative}" if relative != "." else scope


def _route_candidates(root, scope, relative):
    provider_root = root if scope == "." else safe_path(root / scope, root)
    raw_path = provider_root if relative == "." else provider_root / relative
    explicit_suffix = Path(relative).suffix.casefold()
    if explicit_suffix in DOCUMENT_SUFFIXES:
        candidates = [raw_path]
    elif explicit_suffix:
        candidates = []
    else:
        candidates = [raw_path.with_suffix(suffix) for suffix in sorted(DOCUMENT_SUFFIXES)]
        if raw_path.is_dir():
            candidates.extend(raw_path / ("index" + suffix) for suffix in sorted(DOCUMENT_SUFFIXES))
    existing = []
    for candidate in candidates:
        safe_candidate = safe_path(candidate, root)
        if _is_reparse(safe_candidate):
            raise ValueError("route crosses a symlink or reparse component")
        if safe_candidate.is_file() and is_document_path(safe_candidate):
            existing.append(safe_candidate)
    if len(existing) > 1:
        raise ValueError("ambiguous extension match")
    return existing[0] if existing else None


def _route_key(relative):
    return "/" if relative == "." else "/" + relative.strip("/")


def _normalize_redirect(raw, *, label, allow_fragment):
    relative, fragment = _route_parts(raw, label=label, allow_fragment=allow_fragment)
    value = _route_key(relative)
    if fragment:
        value += "#" + fragment
    return value


def _read_manifest(root, relative, candidate):
    size = candidate.stat().st_size
    if size > MAX_NAVIGATION_MANIFEST_BYTES:
        return None, _unmeasured(
            provider="unknown",
            scope=relative.rsplit("/", 1)[0] or ".",
            authority=relative,
            manifest_bytes=size,
            features=("manifest-size",),
            classification="oversized-navigation-manifest",
        )
    try:
        raw = candidate.read_text(encoding="utf-8", errors="strict")
        payload = _strict_json_loads(raw)
        _validate_json_limits(payload)
    except (OSError, UnicodeError, TypeError, ValueError, json.JSONDecodeError) as exc:
        result = _unmeasured(
            provider="unknown",
            scope=relative.rsplit("/", 1)[0] or ".",
            authority=relative,
            manifest_bytes=size,
            features=("malformed-manifest",),
            classification="malformed-navigation-manifest",
        )
        if isinstance(exc, ValueError) and str(exc) == "duplicate JSON key":
            result["unsupported_features"] = ["duplicate-json-key"]
            result["classification"] = "duplicate-navigation-manifest-key"
        elif isinstance(exc, ValueError) and any(
            marker in str(exc) for marker in ("capacity", "exceeds maximum")
        ):
            result["unsupported_features"] = ["manifest-capacity"]
            result["classification"] = "oversized-navigation-manifest"
        return None, result
    if not isinstance(payload, dict):
        return None, _unmeasured(
            provider="unknown",
            scope=relative.rsplit("/", 1)[0] or ".",
            authority=relative,
            manifest_bytes=size,
            features=("manifest-root",),
            classification="malformed-navigation-manifest",
        )
    return payload, None


def _collect_navigation_pages(navigation):
    entries = []
    page_count = 0

    def context_for(value, context):
        result = list(context)
        for key in _CONTEXT_KEYS:
            label = value.get(key) if isinstance(value, Mapping) else None
            if isinstance(label, str) and label and label not in result:
                result.append(label)
        return result

    def collect(raw, context, hidden, *, label):
        nonlocal page_count
        page_count += 1
        if page_count > MAX_NAVIGATION_PAGES:
            raise ValueError("navigation page count exceeds capacity")
        if not isinstance(raw, str):
            raise ValueError(f"{label} page must be a string")
        entries.append({"raw": raw, "context": list(context), "hidden": bool(hidden)})

    def walk(value, context=(), hidden=False, *, depth=0, label="navigation"):
        if depth > MAX_NAVIGATION_DEPTH:
            raise ValueError("navigation depth exceeds capacity")
        if isinstance(value, list):
            for index, child in enumerate(value):
                walk(child, context, hidden, depth=depth + 1, label=f"{label}[{index}]")
            return
        if not isinstance(value, Mapping):
            raise ValueError(f"{label} must be an object")
        unsupported_fields = set(value) - _ALLOWED_NAVIGATION_FIELDS
        if unsupported_fields:
            raise ValueError(f"{label} has unsupported fields")
        local_context = context_for(value, context)
        local_hidden = hidden or value.get("hidden") is True
        if "hidden" in value and type(value["hidden"]) is not bool:
            raise ValueError(f"{label}.hidden must be boolean")
        if "icon" in value and (
            not isinstance(value["icon"], str) or not value["icon"]
        ):
            raise ValueError(f"{label}.icon must be a non-empty string")
        if "root" in value:
            collect(value["root"], local_context, local_hidden, label=f"{label}.root")
        for key in _CONTAINER_KEYS:
            if key not in value:
                continue
            child = value[key]
            if key in {"pages", "menu"}:
                if not isinstance(child, list):
                    raise ValueError(f"{label}.{key} must be an array")
                for index, item in enumerate(child):
                    item_label = f"{label}.{key}[{index}]"
                    if isinstance(item, str):
                        collect(item, local_context, local_hidden, label=item_label)
                    elif isinstance(item, Mapping) and isinstance(item.get("page"), str):
                        allowed = {"page", "hidden"}
                        if set(item) - allowed:
                            raise ValueError(f"{item_label} has unsupported fields")
                        item_hidden = local_hidden or item.get("hidden") is True
                        if "hidden" in item and type(item["hidden"]) is not bool:
                            raise ValueError(f"{item_label}.hidden must be boolean")
                        collect(item["page"], local_context, item_hidden, label=item_label)
                    elif isinstance(item, Mapping):
                        walk(item, local_context, local_hidden, depth=depth + 1, label=item_label)
                    else:
                        raise ValueError(f"{item_label} is not a page or navigation object")
            elif isinstance(child, (list, Mapping)):
                walk(child, local_context, local_hidden, depth=depth + 1, label=f"{label}.{key}")
            else:
                raise ValueError(f"{label}.{key} has unsupported shape")

    walk(navigation)
    return entries


def _load_shared_pages(root, scope):
    tracked = tracked_markdown_scope(root, scope)
    if tracked is not None:
        return tracked
    paths, _ = iter_markdown_scope(root, scope, [])
    return sorted(
        {_relative_posix(path, root) for path in paths if is_document_path(path)},
        key=_sort_key,
    )


def _page_metadata(root, relative):
    path = safe_path(root / relative, root)
    if path.stat().st_size > MAX_PAGE_BYTES:
        raise ValueError(f"page exceeds capacity: {relative}")
    text = path.read_text(encoding="utf-8", errors="strict")
    return parse_frontmatter_scalars(text)


def _parse_redirects(payload):
    raw_redirects = payload.get("redirects", [])
    if raw_redirects is None:
        raw_redirects = []
    if not isinstance(raw_redirects, list):
        raise ValueError("redirects must be an array")
    if len(raw_redirects) > MAX_NAVIGATION_REDIRECTS:
        raise ValueError("redirect count exceeds capacity")
    result = []
    sources = set()
    for index, item in enumerate(raw_redirects):
        if not isinstance(item, Mapping):
            raise ValueError(f"redirect {index} must be an object")
        if set(item) - {"source", "destination", "permanent"}:
            raise ValueError(f"redirect {index} has unsupported fields")
        if "source" not in item or "destination" not in item:
            raise ValueError(f"redirect {index} is incomplete")
        if "permanent" in item and type(item["permanent"]) is not bool:
            raise ValueError(f"redirect {index}.permanent must be boolean")
        source = _normalize_redirect(item["source"], label=f"redirect {index}.source", allow_fragment=False)
        destination = _normalize_redirect(
            item["destination"],
            label=f"redirect {index}.destination",
            allow_fragment=True,
        )
        if source in sources:
            raise ValueError("duplicate redirect source")
        sources.add(source)
        result.append({"source": source, "destination": destination})
    return sorted(result, key=lambda item: _sort_key(item["source"]))


def _validate_redirect_cycles(redirects):
    mapping = {item["source"]: item["destination"] for item in redirects}
    for source in mapping:
        current = source
        seen = set()
        for _ in range(MAX_REDIRECT_HOPS + 1):
            if current in seen:
                raise ValueError("redirect cycle")
            seen.add(current)
            destination = mapping.get(current)
            if destination is None:
                break
            current = destination.split("#", 1)[0]
        else:
            raise ValueError("redirect hop limit exceeded")


def _resolve_manifest_page(root, provider_root, raw):
    relative, fragment = _route_parts(raw, label="navigation page", allow_fragment=False)
    path = _route_candidates(root, provider_root, relative)
    if path is None:
        return None, _provider_absolute(provider_root, relative)
    return _relative_posix(path, root), fragment


def _is_within_scope(relative, scope):
    if scope == ".":
        return True
    scope_key = scope.casefold().rstrip("/")
    relative_key = relative.casefold()
    return relative_key == scope_key or relative_key.startswith(scope_key + "/")


def _measure_mintlify(root, authority, candidate, provider_root, selected_scope):
    manifest_bytes = candidate.stat().st_size
    result = _base_result(
        status="measured",
        provider="mintlify",
        scope=selected_scope,
        authority=authority,
        manifest_bytes=manifest_bytes,
        provider_root=provider_root,
    )
    payload, boundary = _read_manifest(root, authority, candidate)
    if boundary is not None:
        boundary["scope"] = selected_scope
        boundary["provider_root"] = provider_root
        return boundary
    schema = payload.get("$schema")
    if not isinstance(schema, str) or schema.casefold().rstrip("/") not in MINTLIFY_SCHEMA_URLS:
        return _unmeasured(
            provider="unknown",
            scope=selected_scope,
            authority=authority,
            manifest_bytes=manifest_bytes,
            provider_root=provider_root,
            features=("unknown-schema",),
            classification="unsupported-documentation-navigation-manifest",
        )
    unsupported = _unsupported_keys(payload)
    if unsupported:
        return _unmeasured(
            provider="mintlify",
            scope=selected_scope,
            authority=authority,
            manifest_bytes=manifest_bytes,
            provider_root=provider_root,
            features=unsupported,
            classification="unsupported-mintlify-feature",
        )
    navigation = payload.get("navigation")
    if not isinstance(navigation, Mapping):
        return _unmeasured(
            provider="mintlify",
            scope=selected_scope,
            authority=authority,
            manifest_bytes=manifest_bytes,
            provider_root=provider_root,
            features=("navigation-shape",),
            classification="malformed-mintlify-navigation",
        )
    try:
        entries = _collect_navigation_pages(navigation)
        if not entries:
            return _unmeasured(
                provider="mintlify",
                scope=selected_scope,
                authority=authority,
                manifest_bytes=manifest_bytes,
                provider_root=provider_root,
                features=("empty-navigation",),
                classification="empty-mintlify-navigation",
            )
        redirects = _parse_redirects(payload)
        _validate_redirect_cycles(redirects)
        shared_pages = _load_shared_pages(root, selected_scope)
        shared_set = set(shared_pages)
        visible = []
        hidden = set()
        contexts = {}
        missing = []
        for entry in entries:
            resolved, route_or_fragment = _resolve_manifest_page(
                root, provider_root, entry["raw"]
            )
            if resolved is None:
                missing.append(
                    {
                        "kind": "missing-navigation-page",
                        "path": route_or_fragment,
                        "route": entry["raw"],
                        "context": list(entry["context"]),
                    }
                )
                continue
            if not _is_within_scope(resolved, selected_scope):
                continue
            if resolved not in shared_set:
                missing.append(
                    {
                        "kind": "missing-navigation-page",
                        "path": resolved,
                        "route": entry["raw"],
                        "context": list(entry["context"]),
                    }
                )
                continue
            metadata = _page_metadata(root, resolved)
            page_hidden = entry["hidden"] or metadata["values"].get("hidden") is True
            contexts.setdefault(resolved, []).append(
                {"breadcrumb": list(entry["context"]), "hidden": page_hidden}
            )
            if page_hidden:
                hidden.add(resolved)
            elif resolved not in visible:
                visible.append(resolved)

        for relative in shared_pages:
            if relative not in contexts:
                hidden.add(relative)
        visible = [relative for relative in visible if relative not in hidden]
        result["entry"] = visible[0] if visible else next(iter(sorted(hidden, key=_sort_key)), None)
        result["navigated_pages"] = visible
        result["hidden_pages"] = sorted(hidden, key=_sort_key)
        result["redirects"] = redirects
        result["contexts"] = contexts
        result["findings"] = missing
        result["orientation"] = (
            {"path": "README.md", "separate": True}
            if provider_root != "." and (root / "README.md").is_file()
            else None
        )
        result["limits"].update(
            {
                "observed_pages": len(entries),
                "observed_shared_pages": len(shared_pages),
                "observed_redirects": len(redirects),
            }
        )
    except (OSError, UnicodeError, TypeError, ValueError) as exc:
        message = str(exc)
        feature = (
            "ambiguous-extension-match"
            if "ambiguous extension" in message
            else "redirect-cycle"
            if "redirect cycle" in message
            else "redirect-hop-limit"
            if "redirect hop" in message
            else "unsafe-route"
            if any(token in message for token in ("outside", "traversal", "reparse", "escapes"))
            else "navigation-capacity"
            if any(token in message for token in ("capacity", "exceeds maximum"))
            else "navigation-shape"
        )
        classification = (
            "oversized-mintlify-navigation"
            if feature == "navigation-capacity"
            else "unsupported-mintlify-navigation"
        )
        return _unmeasured(
            provider="mintlify",
            scope=selected_scope,
            authority=authority,
            manifest_bytes=manifest_bytes,
            provider_root=provider_root,
            features=(feature,),
            classification=classification,
        )
    return result


def _find_navigation_manifest(root, scope):
    candidate_data, bad_relative, bad_kind = _candidate_manifest_paths(root, scope)
    if bad_kind:
        relative = bad_relative or "docs.json"
        return None, relative, _unmeasured(
            provider="unknown",
            scope=scope,
            authority=relative,
            features=(bad_kind,),
            classification="unsafe-navigation-manifest",
        )
    if candidate_data is None:
        return None, None, None
    return candidate_data[1], candidate_data[0], None


def select_navigation(root, scope="docs", map_path="docs/README.md"):
    """Select exactly one bounded documentation surface and measure its facts."""
    root = Path(root).absolute()
    scope = normalize_repo_relative(scope, "scope")
    map_path = normalize_repo_relative(map_path, "map")
    candidate, authority, boundary = _find_navigation_manifest(root, scope)
    if boundary is not None:
        raise NavigationBoundary(boundary)
    if candidate is None:
        result = _base_result(
            status="measured",
            provider="markdown-map",
            scope=scope,
            authority=map_path,
        )
        result["entry"] = map_path
        result["navigated_pages"] = [map_path]
        result["limits"].update({"observed_pages": 1, "observed_shared_pages": 0, "observed_redirects": 0})
        return result
    provider_scope = authority.rsplit("/", 1)[0] or "."
    selected_scope = provider_scope if scope == "." else scope
    if not _is_within_scope(provider_scope, selected_scope) and not _is_within_scope(
        selected_scope, provider_scope
    ):
        raise NavigationBoundary(
            _unmeasured(
                provider="mintlify",
                scope=scope,
                authority=authority,
                features=("scope-outside-provider-root",),
                classification="unsafe-navigation-scope",
                provider_root=provider_scope,
            )
        )
    result = _measure_mintlify(
        root, authority, candidate, provider_scope, selected_scope
    )
    if result.get("status") != "measured":
        raise NavigationBoundary(result)
    return result


def _redirect_destination(redirects, current):
    mapping = {item["source"]: item["destination"] for item in redirects}
    return mapping.get(current)


def resolve_navigation_link(root, navigation, source_relative, raw_target):
    """Resolve one provider link without executing document or component code."""
    if navigation.get("provider") != "mintlify":
        return {"status": "not-provider"}
    if not isinstance(raw_target, str):
        return {"status": "invalid"}
    try:
        parsed = urlsplit(raw_target)
    except ValueError:
        return {"status": "invalid"}
    if parsed.scheme or parsed.netloc or raw_target.startswith("//"):
        return {"status": "external"}
    try:
        source_relative = normalize_repo_relative(source_relative, "source path")
        provider_root = navigation.get("provider_root") or navigation["scope"]
        selected_scope = navigation["scope"]
        source_provider = _provider_relative(provider_root, source_relative)
        if parsed.path:
            raw_path = unquote(parsed.path.replace("\\", "/"))
            if raw_path.startswith("/"):
                target_provider = raw_path[1:] or "."
            else:
                target_provider = posixpath.join(
                    posixpath.dirname(source_provider), raw_path
                )
        else:
            target_provider = source_provider
        if any(part == ".." for part in PurePosixPath(target_provider).parts):
            return {"status": "outside"}
        target_provider = posixpath.normpath(target_provider)
        if target_provider in {"", "."}:
            target_provider = "."
        target_key = _route_key(target_provider)
        fragment = parsed.fragment
        seen = set()
        for _ in range(MAX_REDIRECT_HOPS + 1):
            if target_key in seen:
                return {"status": "unsupported", "reason": "redirect-cycle"}
            seen.add(target_key)
            destination = _redirect_destination(navigation.get("redirects", []), target_key)
            if destination is None:
                break
            destination_path, _, destination_fragment = destination.partition("#")
            target_key = destination_path
            fragment = destination_fragment or fragment
        else:
            return {"status": "unsupported", "reason": "redirect-hop-limit"}
        target_provider = target_key.lstrip("/") or "."
        resolved = _route_candidates(root, provider_root, target_provider)
        if resolved is None:
            return {
                "status": "missing",
                "path": _provider_absolute(provider_root, target_provider),
            }
        resolved_relative = _relative_posix(resolved, Path(root).absolute())
        if not _is_within_scope(resolved_relative, selected_scope):
            return {"status": "outside"}
        return {
            "status": "resolved",
            "path": resolved_relative,
            "fragment": fragment,
            "query": parsed.query,
        }
    except (OSError, TypeError, UnicodeError, ValueError):
        return {"status": "outside"}


def unsupported_navigation_manifest(root, scope, map_path):
    """Backward-compatible probe for callers that only need the old boundary."""
    root = Path(root).absolute()
    scope_norm = normalize_repo_relative(scope, "scope")
    map_norm = normalize_repo_relative(map_path, "map")
    candidate, relative, boundary = _find_navigation_manifest(root, scope_norm)
    if boundary is not None:
        return relative
    if candidate is None:
        return None
    try:
        provider_scope = relative.rsplit("/", 1)[0] or "."
        result = _measure_mintlify(
            root, relative, candidate, provider_scope, provider_scope
        )
    except (OSError, TypeError, UnicodeError, ValueError):
        return relative
    if result.get("status") != "measured":
        return relative
    try:
        map_candidate = safe_path(root / map_norm, root)
    except ValueError:
        return None
    if is_document_path(map_norm) and map_candidate.is_file() and not _is_reparse(map_candidate):
        return None
    return relative


__all__ = (
    "MAX_NAVIGATION_DEPTH",
    "MAX_NAVIGATION_MANIFEST_BYTES",
    "MAX_NAVIGATION_PAGES",
    "MAX_NAVIGATION_REDIRECTS",
    "MAX_REDIRECT_HOPS",
    "MINTLIFY_SCHEMA_URLS",
    "NavigationBoundary",
    "resolve_navigation_link",
    "select_navigation",
    "unsupported_navigation_manifest",
)
