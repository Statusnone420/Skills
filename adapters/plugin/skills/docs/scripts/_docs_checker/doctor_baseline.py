"""Engine-owned first-contact Doctor measurement policy."""

from .discovery import discover_init_scope, prepare_init_discovery
from .health import health_summary
from .init_closeout import inspect_initialization_preflight
from .navigation import NavigationBoundary, select_navigation
from .paths import tracked_markdown_scope


DOCTOR_BASELINE_LABEL = "Provisional structural baseline (root README orientation fallback)"
MAINTAINED_ENTRY_STEMS = ("readme", "index", "overview", "docs", "documentation", "home")


def _failed_discovery():
    return {
        "status": "unavailable",
        "selected_scope": None,
        "inspected_scope": None,
        "completeness": {"status": "incomplete", "errors": []},
        "requires_user_action": False,
    }


def _unavailable(discovery, reason, *, navigation=None):
    result = {
        "status": "unavailable",
        "has_findings": False,
        "root": ".",
        "scope": discovery.get("selected_scope"),
        "map": None,
        "doctor_baseline": {
            "status": "unavailable",
            "reason": reason,
            "label": "Doctor baseline unavailable",
            "authority_kind": None,
            "maintained_map": None,
            "treatment_authority": False,
            "writes": 0,
            "recommendation": None,
        },
        "discovery": discovery,
        "findings": [],
    }
    if navigation is not None:
        result["navigation"] = navigation
    return result


def _entry_candidate(tracked, selected_scope):
    if selected_scope == ".":
        return None
    prefix = selected_scope.rstrip("/") + "/"
    candidates = {}
    for path in tracked:
        if not path.startswith(prefix):
            continue
        relative = path[len(prefix):]
        if "/" in relative or "." not in relative:
            continue
        stem, extension = relative.rsplit(".", 1)
        if extension.casefold() not in {"md", "markdown", "mdx"}:
            continue
        key = stem.casefold()
        if key in MAINTAINED_ENTRY_STEMS:
            candidates.setdefault(key, []).append(path)
    for stem in MAINTAINED_ENTRY_STEMS:
        if stem in candidates:
            return sorted(candidates[stem], key=lambda item: (item.casefold(), item))[0]
    return None


def _content_batch_only(discovery):
    boundaries = discovery.get("next_boundary")
    scope_metadata = discovery.get("scope_metadata", {})
    content_batch = discovery.get("content_batch", {})
    return (
        discovery.get("status") == "batch-limited"
        and discovery.get("requires_user_action") is False
        and discovery.get("physical_limit") is None
        and discovery.get("completeness", {}).get("errors") == []
        and scope_metadata.get("complete") is True
        and scope_metadata.get("truncated") is False
        and scope_metadata.get("next_boundary") is None
        and content_batch.get("blocked_by_metadata") is False
        and content_batch.get("truncated") is True
        and isinstance(boundaries, list)
        and bool(boundaries)
        and all(item.get("kind") == "content-files" for item in boundaries)
    )


def doctor_orientation_baseline(root, check_measurements):
    """Return provider, candidate, or orientation evidence without write authority."""
    try:
        discovery_state, discovery = prepare_init_discovery(
            root,
            lambda candidate: inspect_initialization_preflight(
                candidate,
                control_present=True,
            ),
        )
        if discovery is None:
            discovery = discover_init_scope(
                root,
                explicit_scope=None,
                continuation=None,
                _prepared_state=discovery_state,
            )
    except (OSError, UnicodeError, ValueError):
        return _unavailable(_failed_discovery(), "discovery-unavailable")
    selected_scope = discovery.get("selected_scope")
    content_batch_only = _content_batch_only(discovery)
    if (
        discovery.get("status") != "ready"
        and not content_batch_only
    ) or discovery.get("requires_user_action"):
        return _unavailable(discovery, "discovery-not-ready")
    if (
        (
            discovery.get("completeness", {}).get("status") != "complete"
            and not content_batch_only
        )
        or selected_scope is None
        or discovery.get("inspected_scope") != selected_scope
    ):
        return _unavailable(discovery, "discovery-incomplete")
    try:
        tracked = tracked_markdown_scope(root, ".", include_navigation=True)
    except (OSError, UnicodeError, ValueError):
        return _unavailable(discovery, "git-tracking-unavailable")
    if tracked is None:
        return _unavailable(discovery, "git-tracking-unavailable")
    try:
        navigation = select_navigation(root, selected_scope, "README.md")
    except NavigationBoundary as exc:
        return _unavailable(
            discovery,
            "navigation-unavailable",
            navigation=exc.result,
        )
    except (OSError, UnicodeError, ValueError):
        return _unavailable(discovery, "navigation-unavailable")
    provider_measurement = navigation.get("provider") != "markdown-map"
    entry_candidate = None if provider_measurement else _entry_candidate(tracked, selected_scope)
    orientation_fallback = not provider_measurement and entry_candidate is None
    root_readme = None
    if orientation_fallback:
        root_documents = discovery.get("root_documents", {})
        if root_documents.get("complete") is not True:
            return _unavailable(discovery, "root-readme-unavailable")
        root_readme = next(
            (
                item.get("path")
                for item in root_documents.get("paths", [])
                if item.get("path", "").casefold() == "readme.md"
            ),
            None,
        )
        if root_readme is None:
            return _unavailable(discovery, "root-readme-unavailable")
        tracked_readme = next(
            (path for path in tracked if path.casefold() == root_readme.casefold()),
            None,
        )
        if tracked_readme is None:
            return _unavailable(discovery, "root-readme-not-tracked")
        root_readme = tracked_readme
    map_path = entry_candidate or root_readme or "README.md"
    if not provider_measurement:
        navigation = {
            **navigation,
            "authority": map_path,
            "entry": map_path,
            "navigated_pages": [map_path],
        }
    try:
        findings, hot_path, measurements = check_measurements(
            root,
            map_path,
            None,
            selected_scope,
            _measurements=True,
            _navigation=navigation,
        )
    except (OSError, UnicodeError, ValueError):
        return _unavailable(
            discovery,
            "measurement-unavailable",
            navigation=navigation,
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
    authority_kind = (
        "provider"
        if provider_measurement
        else "existing-entry-candidate"
        if entry_candidate is not None
        else "orientation-fallback"
    )
    return {
        "status": "findings" if findings else "clean",
        "has_findings": bool(findings),
        "root": ".",
        "scope": selected_scope,
        "map": measurements["navigation"].get("entry") or map_path,
        "prunes": measurements["prunes"],
        "hot_path": hot_path,
        "navigation": measurements["navigation"],
        "health": health,
        "doctor_baseline": {
            "status": "measured",
            "reason": (
                "supported-provider"
                if provider_measurement
                else "existing-entry-candidate"
                if entry_candidate is not None
                else "safe-root-readme-orientation"
            ),
            "label": (
                "Authoritative provider measurement"
                if provider_measurement
                else "Provisional existing-entry candidate measurement"
                if entry_candidate is not None
                else DOCTOR_BASELINE_LABEL
            ),
            "authority_kind": authority_kind,
            "maintained_map": None if entry_candidate is not None or provider_measurement else False,
            "treatment_authority": provider_measurement,
            "writes": 0,
            "recommendation": (
                None
                if provider_measurement
                else "$docs map"
                if entry_candidate is not None
                else "$docs init"
            ),
        },
        "discovery": discovery,
        "findings": findings,
    }


__all__ = ("DOCTOR_BASELINE_LABEL", "doctor_orientation_baseline")
