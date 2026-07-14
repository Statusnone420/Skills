"""Structural health, Trust coverage, freshness, and byte telemetry."""

import hashlib
import re
import unicodedata
from collections.abc import Mapping, Sequence
from pathlib import Path

from .identity import finding_fingerprint, finding_id
from .paths import (
    _assert_no_reparse_components,
    _path_identity,
    normalize_repo_relative,
    safe_path,
)


PROVISIONAL_TARGET_BYTES = 16 * 1024
HEALTH_RUBRIC_VERSION = 2
HEALTH_WEIGHTS = {
    "entry": 20,
    "path_safety": 15,
    "links": 20,
    "anchors": 10,
    "reachability": 25,
    "titles": 10,
}
_DIGEST = re.compile(r"^sha256-(?:text|bytes):[0-9a-f]{64}$")
_FRESHNESS_RANK = {"unverified": 0, "fresh": 1, "missing": 2, "stale": 3}


def health_meter(percentage):
    percentage = int(percentage)
    filled = max(0, min(20, percentage // 5))
    cells = "█" * filled + "░" * (20 - filled)
    return f"Docs [{cells}] {percentage}%"


def _count(measurements, name):
    value = measurements.get(name, 0)
    if not isinstance(value, int) or isinstance(value, bool):
        return 0
    return max(0, value)


def _fraction(numerator, denominator):
    if denominator <= 0:
        return 0
    return min(1, max(0, numerator) / denominator)


def _sequence(value, name):
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        raise ValueError(f"{name} must be an array")
    return value


def _mapping(value, name):
    if not isinstance(value, Mapping):
        raise ValueError(f"{name} must be an object")
    return value


def _route(value, name="route"):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")
    path, _, _ = value.partition("#")
    return normalize_repo_relative(path, name)


def _deterministic_route_display(current, candidate):
    """Retain one normalized observed spelling for a filesystem identity."""
    return min((current, candidate), key=lambda route: (route.casefold(), route))


def normalized_content_digest(path: Path) -> str:
    """Return an NFC/newline-stable text digest, or a byte digest for non-UTF-8."""
    data = Path(path).read_bytes()
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        return "sha256-bytes:" + hashlib.sha256(data).hexdigest()
    normalized = unicodedata.normalize(
        "NFC", text.replace("\r\n", "\n").replace("\r", "\n")
    )
    return "sha256-text:" + hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def _stale_finding(route, expected_digests, actual_digest):
    fingerprint = finding_fingerprint("stale-evidence", [{"path": route}])
    return {
        "id": finding_id(fingerprint, {}),
        "fingerprint": fingerprint,
        "kind": "stale-evidence",
        "priority": "P1",
        "status": "Proposed",
        "path": route,
        "expected_digests": list(expected_digests),
        "actual_digest": actual_digest,
        "detail": "verified content is missing"
        if actual_digest is None
        else "verified content digest has changed",
    }


def evaluate_freshness(root: Path, verified_documents: Sequence[Mapping]) -> dict:
    """Evaluate only state-declared verified document and source routes."""
    root = Path(root).absolute()
    _assert_no_reparse_components(root)
    safe_path(root, root)
    declarations = {}

    def declare(value, digest, provenance):
        route = _route(value, provenance)
        if not isinstance(digest, str) or not _DIGEST.fullmatch(digest.lower()):
            raise ValueError(f"{provenance} digest must be normalized SHA-256")
        identity = _path_identity(route)
        row = declarations.setdefault(
            identity,
            {"route": route, "digests": set(), "provenance": set()},
        )
        row["route"] = _deterministic_route_display(row["route"], route)
        row["digests"].add(digest.lower())
        row["provenance"].add(provenance)

    for index, record in enumerate(_sequence(verified_documents, "verified_documents")):
        record = _mapping(record, f"verified_documents[{index}]")
        declare(
            record.get("document"),
            record.get("digest"),
            "state:verified-document",
        )
        for source_index, source in enumerate(
            _sequence(record.get("sources", ()), f"verified_documents[{index}].sources")
        ):
            source = _mapping(
                source, f"verified_documents[{index}].sources[{source_index}]"
            )
            declare(
                source.get("path"),
                source.get("digest"),
                "state:verified-source",
            )

    routes = []
    findings = []
    for identity in sorted(
        declarations,
        key=lambda key: (
            declarations[key]["route"].casefold(),
            declarations[key]["route"],
        ),
    ):
        declaration = declarations[identity]
        route = declaration["route"]
        expected_digests = sorted(declaration["digests"])
        path = safe_path(root / route, root)
        actual_digest = (
            normalized_content_digest(path) if path.exists() and path.is_file() else None
        )
        if actual_digest is None:
            status = "missing"
        elif len(expected_digests) == 1 and actual_digest == expected_digests[0]:
            status = "fresh"
        else:
            status = "stale"
        routes.append(
            {
                "route": route,
                "status": status,
                "expected_digest": expected_digests[0]
                if len(expected_digests) == 1
                else None,
                "expected_digests": expected_digests,
                "actual_digest": actual_digest,
                "provenance": sorted(declaration["provenance"]),
            }
        )
        if status != "fresh":
            findings.append(_stale_finding(route, expected_digests, actual_digest))

    if not routes:
        status = "unverified"
    elif findings:
        status = "stale"
    else:
        status = "fresh"
    return {"status": status, "routes": routes, "findings": findings}


def evaluate_coverage(
    *, configured_routes=(), state=None, map_routes=(), freshness=None
) -> dict:
    """Build the normalized declared-Trust union and its per-route provenance."""
    declarations = {}

    def declare(value, source):
        route = _route(value, source)
        identity = _path_identity(route)
        row = declarations.setdefault(identity, {"route": route, "sources": set()})
        row["route"] = _deterministic_route_display(row["route"], route)
        row["sources"].add(source)

    for value in _sequence(configured_routes, "configured_routes"):
        if isinstance(value, Mapping):
            value = value.get("route", value.get("path"))
        declare(value, "configured:hot-path")

    if state is not None:
        state = _mapping(state, "state")
        initialized = state.get("initialized", {})
        if initialized is not None:
            initialized = _mapping(initialized, "state.initialized")
            for value in _sequence(
                initialized.get("hot_paths", ()), "state.initialized.hot_paths"
            ):
                declare(value, "state:initialized-hot-path")
        for index, record in enumerate(
            _sequence(state.get("verified_documents", ()), "state.verified_documents")
        ):
            record = _mapping(record, f"state.verified_documents[{index}]")
            declare(record.get("document"), "state:verified-document")
            for source_index, source in enumerate(
                _sequence(
                    record.get("sources", ()),
                    f"state.verified_documents[{index}].sources",
                )
            ):
                source = _mapping(
                    source,
                    f"state.verified_documents[{index}].sources[{source_index}]",
                )
                declare(source.get("path"), "state:verified-source")

    for index, record in enumerate(_sequence(map_routes, "map_routes")):
        record = _mapping(record, f"map_routes[{index}]")
        marker = record.get("marker")
        if marker not in {"current", "authoritative"}:
            raise ValueError("map route marker must be current or authoritative")
        declare(record.get("route"), f"map:{marker}")

    freshness_by_route = {}
    if freshness is not None:
        freshness = _mapping(freshness, "freshness")
        for index, row in enumerate(_sequence(freshness.get("routes", ()), "freshness.routes")):
            row = _mapping(row, f"freshness.routes[{index}]")
            route = _route(row.get("route"), f"freshness.routes[{index}].route")
            identity = _path_identity(route)
            status = row.get("status", "unverified")
            if status not in _FRESHNESS_RANK:
                raise ValueError("freshness route status is invalid")
            previous = freshness_by_route.get(identity, "unverified")
            if _FRESHNESS_RANK[status] >= _FRESHNESS_RANK[previous]:
                freshness_by_route[identity] = status

    routes = []
    for identity in sorted(
        declarations,
        key=lambda key: (
            declarations[key]["route"].casefold(),
            declarations[key]["route"],
        ),
    ):
        declaration = declarations[identity]
        route = declaration["route"]
        freshness_status = freshness_by_route.get(identity, "unverified")
        routes.append(
            {
                "route": route,
                "verified": freshness_status == "fresh",
                "freshness": freshness_status,
                "sources": sorted(declaration["sources"]),
            }
        )
    numerator = sum(row["verified"] for row in routes)
    denominator = len(routes)
    status = (
        "unverified"
        if denominator == 0
        else "verified"
        if numerator == denominator
        else "partial"
    )
    return {
        "status": status,
        "numerator": numerator,
        "denominator": denominator,
        "routes": routes,
    }


def _normalize_coverage(coverage):
    if coverage is None:
        return {"status": "unverified", "numerator": 0, "denominator": 0, "routes": []}
    coverage = _mapping(coverage, "coverage")
    routes = []
    seen = set()
    for index, row in enumerate(_sequence(coverage.get("routes", ()), "coverage.routes")):
        row = _mapping(row, f"coverage.routes[{index}]")
        route = _route(row.get("route"), f"coverage.routes[{index}].route")
        if route in seen:
            raise ValueError("coverage routes must be normalized and deduplicated")
        seen.add(route)
        sources = sorted(
            {
                str(source)
                for source in _sequence(
                    row.get("sources", ()), f"coverage.routes[{index}].sources"
                )
            }
        )
        freshness_status = row.get("freshness", "unverified")
        verified = row.get("verified") is True or freshness_status == "fresh"
        routes.append(
            {
                "route": route,
                "verified": verified,
                "freshness": freshness_status,
                "sources": sources,
            }
        )
    routes.sort(key=lambda row: row["route"])
    numerator = sum(row["verified"] for row in routes)
    denominator = len(routes)
    status = (
        "unverified"
        if denominator == 0
        else "verified"
        if numerator == denominator
        else "partial"
    )
    return {
        "status": status,
        "numerator": numerator,
        "denominator": denominator,
        "routes": routes,
    }


def _open_priorities(findings):
    counts = {"P0": 0, "P1": 0, "P2": 0}
    for finding in findings:
        if not isinstance(finding, Mapping):
            continue
        status = str(finding.get("status", "")).casefold()
        if status in {"applied", "closed", "resolved"}:
            continue
        priority = finding.get("priority")
        if priority in counts:
            counts[priority] += 1
    return counts


def _baseline_score(baseline):
    if baseline is None:
        return None
    if not isinstance(baseline, Mapping):
        return None
    for name in ("last_verified_score", "percentage", "score"):
        value = baseline.get(name)
        if isinstance(value, int) and not isinstance(value, bool) and 0 <= value <= 100:
            return value
    return None


def health_summary(
    measurements: Mapping,
    *,
    findings: Sequence[Mapping] = (),
    baseline: Mapping | None = None,
    freshness: Mapping | None = None,
    coverage: Mapping | None = None,
) -> dict:
    """Return structural rubric v2 plus separate Trust and telemetry evidence."""
    measurements = _mapping(measurements, "measurements")
    map_exists = measurements.get("map_exists") is True
    map_has_h1 = measurements.get("map_has_h1") is True
    map_has_body = measurements.get("map_has_body") is True
    map_has_h2 = measurements.get("map_has_h2") is True
    maintained_files = _count(measurements, "maintained_files")
    maintained_paths = _count(measurements, "maintained_paths")
    safe_maintained_paths = min(
        maintained_paths, _count(measurements, "safe_maintained_paths")
    )
    checked_links = _count(measurements, "checked_links")
    valid_links = min(checked_links, _count(measurements, "valid_links"))
    checked_anchors = _count(measurements, "checked_anchors")
    valid_anchors = min(checked_anchors, _count(measurements, "valid_anchors"))
    valid_navigation_routes = _count(measurements, "valid_navigation_routes")
    reachable_files = min(maintained_files, _count(measurements, "reachable_files"))
    usable_unique_titles = min(
        maintained_files, _count(measurements, "usable_unique_titles")
    )
    hot_bytes = _count(measurements, "hot_bytes")

    complete_single_document = bool(
        maintained_files == 1
        and map_exists
        and map_has_h1
        and map_has_body
        and map_has_h2
    )
    useful_navigation = bool(
        map_exists and map_has_h1 and valid_navigation_routes > 0
    )
    useful_entry = complete_single_document or useful_navigation

    entry_earned = (
        (5 if map_exists else 0)
        + (5 if map_has_h1 else 0)
        + (10 if useful_entry else 0)
    )
    if not useful_entry:
        links_earned = 0
        anchors_earned = 0
        reachability_earned = 0
    else:
        links_earned = (
            HEALTH_WEIGHTS["links"]
            if complete_single_document and checked_links == 0
            else HEALTH_WEIGHTS["links"] * _fraction(valid_links, checked_links)
        )
        anchors_earned = (
            HEALTH_WEIGHTS["anchors"]
            if checked_anchors == 0
            else HEALTH_WEIGHTS["anchors"]
            * _fraction(valid_anchors, checked_anchors)
        )
        reachability_earned = HEALTH_WEIGHTS["reachability"] * _fraction(
            reachable_files, maintained_files
        )

    earned = {
        "entry": entry_earned,
        "path_safety": HEALTH_WEIGHTS["path_safety"]
        * _fraction(safe_maintained_paths, maintained_paths),
        "links": links_earned,
        "anchors": anchors_earned,
        "reachability": reachability_earned,
        "titles": HEALTH_WEIGHTS["titles"]
        * _fraction(usable_unique_titles, maintained_files),
    }
    raw = {
        "entry": {
            "map_exists": map_exists,
            "map_has_h1": map_has_h1,
            "map_has_body": map_has_body,
            "map_has_h2": map_has_h2,
            "valid_navigation_routes": valid_navigation_routes,
            "complete_single_document": complete_single_document,
            "useful_entry": useful_entry,
        },
        "path_safety": {"safe": safe_maintained_paths, "maintained": maintained_paths},
        "links": {"valid": valid_links, "checked": checked_links},
        "anchors": {"valid": valid_anchors, "checked": checked_anchors},
        "reachability": {"reachable": reachable_files, "maintained": maintained_files},
        "titles": {"usable_unique": usable_unique_titles, "maintained": maintained_files},
    }
    categories = {
        name: {
            "weight": HEALTH_WEIGHTS[name],
            "earned": round(earned[name], 2),
            "available": HEALTH_WEIGHTS[name],
            "raw": raw[name],
        }
        for name in HEALTH_WEIGHTS
    }
    earned_weight = round(
        sum(category["earned"] for category in categories.values()), 2
    )
    percentage = max(0, min(100, int(earned_weight + 0.5)))
    structure_status = "healthy" if percentage == 100 else "needs-attention"

    freshness = (
        measurements.get("freshness") if freshness is None else freshness
    )
    if freshness is None:
        freshness = {"status": "unverified", "routes": [], "findings": []}
    freshness = _mapping(freshness, "freshness")
    freshness_status = freshness.get("status", "unverified")
    if freshness_status not in {"fresh", "stale", "unverified"}:
        raise ValueError("freshness status is invalid")
    coverage = _normalize_coverage(
        measurements.get("coverage") if coverage is None else coverage
    )
    findings = tuple(_sequence(findings, "findings"))
    priorities = _open_priorities(findings)

    if priorities["P0"]:
        trust_status = "blocked"
    elif freshness_status == "stale":
        trust_status = "stale"
    elif coverage["denominator"] == 0:
        trust_status = "unverified"
    elif coverage["numerator"] < coverage["denominator"]:
        trust_status = "partial"
    else:
        trust_status = "verified"

    if trust_status == "blocked":
        verdict = "blocked"
    elif trust_status == "stale":
        verdict = "stale"
    elif structure_status != "healthy" or priorities["P1"]:
        verdict = "needs-attention"
    elif trust_status in {"partial", "unverified"}:
        verdict = trust_status
    else:
        verdict = "healthy"

    hot_path_provenance = []
    for row in measurements.get("hot_path_files", ()):
        if not isinstance(row, Mapping):
            continue
        value = row.get("bytes")
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            continue
        try:
            route = _route(row.get("path"), "hot path provenance")
        except ValueError:
            continue
        hot_path_provenance.append(
            {"route": route, "bytes": value, "source": "filesystem-stat"}
        )
    hot_path_provenance.sort(key=lambda row: row["route"])
    if not hot_path_provenance:
        hot_path_provenance.append({"source": "measurement:hot_bytes"})

    baseline_score = _baseline_score(baseline)
    delta = None if baseline_score is None else percentage - baseline_score
    provenance = {
        "structure": "checker:normalized-navigation-measurements",
        "baseline": "operational-state:last-verified-score"
        if baseline_score is not None
        else "none",
        "freshness": "operational-state:verified-document-digests"
        if freshness.get("routes")
        else "none",
        "trust_routes": [
            {"route": row["route"], "sources": list(row["sources"])}
            for row in coverage["routes"]
        ],
    }
    return {
        "rubric_version": HEALTH_RUBRIC_VERSION,
        "percentage": percentage,
        "meter": health_meter(percentage),
        "delta": delta,
        "earned_weight": earned_weight,
        "available_weight": sum(HEALTH_WEIGHTS.values()),
        "categories": categories,
        "structure_status": structure_status,
        "trust_status": trust_status,
        "coverage": coverage,
        "provenance": provenance,
        "hot_path_bytes": {
            "value": hot_bytes,
            "unit": "bytes",
            "provisional_target_bytes": PROVISIONAL_TARGET_BYTES,
            "provenance": hot_path_provenance,
        },
        "open_priorities": priorities,
        "verdict": verdict,
        "status": verdict,
    }


__all__ = (
    "HEALTH_RUBRIC_VERSION",
    "HEALTH_WEIGHTS",
    "PROVISIONAL_TARGET_BYTES",
    "evaluate_coverage",
    "evaluate_freshness",
    "health_meter",
    "health_summary",
    "normalized_content_digest",
)
