"""Markdown discovery, link analysis, reachability, titles, and hot-path telemetry."""

import re
from pathlib import Path
from urllib.parse import unquote

from .health import PROVISIONAL_TARGET_BYTES
from .identity import finding_fingerprint, finding_id, slug
from .paths import (
    _assert_no_reparse_components,
    _first_reparse_component,
    _is_reparse,
    _path_identity,
    _relative_posix,
    iter_markdown_scope,
    normalize_repo_relative,
    prune_summary,
    route_matches_patterns,
    safe_path,
)


LINK = re.compile(r"\[[^\]]*\]\(([^)]*)\)")
HEADING = re.compile(r"^#{1,6}\s+(.+?)\s*#*\s*$", re.M)
H1 = re.compile(r"^#\s+(.+?)\s*#*\s*$", re.M)
H2 = re.compile(r"^##\s+(.+?)\s*#*\s*$", re.M)
CURRENT_ROUTE_LINK = re.compile(
    r"\[[^\]]*\]\(([^)]*)\)[ \t]*<!-- docs:(current|authoritative) -->[ \t]*$",
    re.M,
)
SOURCES_LINE = re.compile(r"^Sources:[ \t]*(.+)$", re.M)
BACKTICK_ROUTE = re.compile(r"`([^`]+)`")


def strip_fences(text):
    out = []
    fenced = False
    marker = None
    for line in text.splitlines(True):
        match = re.match(r"^\s*(```+|~~~+)", line)
        if match:
            if not fenced:
                fenced = True
                marker = match.group(1)[0]
            elif match.group(1)[0] == marker:
                fenced = False
            out.append("\n")
            continue
        out.append("\n" if fenced else line)
    return "".join(out)


def discover_markdown(root, scope):
    """Collect deterministic scoped Markdown paths and confinement findings."""
    applied_prunes = []
    scoped, findings = iter_markdown_scope(root, scope, applied_prunes)
    return scoped, findings, applied_prunes


def hot_path_summary(root, hot_paths, reparse_paths=()):
    files = []
    total = 0
    for relative in hot_paths:
        if relative in reparse_paths:
            continue
        path = safe_path(root / relative, root)
        if path.is_file() and not _is_reparse(path):
            size = path.stat().st_size
            total += size
            files.append({"path": Path(relative).as_posix(), "bytes": size})
    return {
        "files": files,
        "bytes": total,
        "provisional_target_bytes": PROVISIONAL_TARGET_BYTES,
        "provenance": "filesystem-stat",
    }


def _has_body_paragraph(text):
    for block in re.split(r"\n\s*\n", strip_fences(text)):
        lines = [line.strip() for line in block.splitlines() if line.strip()]
        if not lines:
            continue
        if all(
            line.startswith(("#", "- ", "* ", "+ ", ">", "<!--"))
            or re.fullmatch(r"\[[^\]]*\]\([^)]*\)", line)
            for line in lines
        ):
            continue
        if re.search(r"\w", " ".join(lines), re.UNICODE):
            return True
    return False


def _conflict_finding(path, *, map_path=None, source_path=None):
    evidence = {"path": path}
    if map_path is not None:
        evidence["map"] = map_path
    if source_path is not None:
        evidence["source"] = source_path
    fingerprint = finding_fingerprint("cold-current-conflict", [evidence])
    finding = {
        "id": finding_id(fingerprint, {}),
        "fingerprint": fingerprint,
        "kind": "cold-current-conflict",
        "priority": "P1",
        "status": "Proposed",
        "path": path,
        "detail": "a state-declared cold route is referenced as current evidence",
    }
    if map_path is not None:
        finding["map"] = map_path
    if source_path is not None:
        finding["source"] = source_path
    return finding


def scan_documents(
    root,
    map_norm,
    normalized_hot_paths,
    scoped,
    findings,
    applied_prunes,
    cold_patterns=(),
):
    """Inspect selected Markdown content and return findings and measurements."""
    selected_files = []
    selected_paths = {}
    selected_reparse_paths = set()
    known_reparse_keys = {
        _path_identity(finding["path"])
        for finding in findings
        if finding.get("kind") == "symlink"
    }
    for relative in normalized_hot_paths:
        candidate = root / relative
        reparse_path = _first_reparse_component(candidate, root)
        if reparse_path is not None:
            selected_reparse_paths.add(relative)
            selected_paths[relative] = candidate
            reparse_relative = _relative_posix(reparse_path, root)
            reparse_key = _path_identity(reparse_relative)
            if reparse_key not in known_reparse_keys:
                findings.append({"kind": "symlink", "path": reparse_relative})
                known_reparse_keys.add(reparse_key)
            continue
        path = safe_path(candidate, root)
        selected_paths[relative] = path
        if path.is_file() and path.suffix.lower() == ".md":
            selected_files.append(path)
    mapfile = selected_paths[map_norm]
    cold_paths = {
        path
        for path in scoped
        if route_matches_patterns(_relative_posix(path, root), cold_patterns)
    }
    files = [path for path in scoped if path not in cold_paths]
    for path in selected_files:
        relative = _relative_posix(path, root)
        if path not in files and (
            path == mapfile or not route_matches_patterns(relative, cold_patterns)
        ):
            files.append(path)

    anchors = {}
    titles = {}
    first_h1 = {}
    texts = {}
    for path in files:
        text = strip_fences(path.read_text(encoding="utf-8", errors="replace"))
        texts[path] = text
        headings = HEADING.findall(text)
        anchors[path] = {slug(heading) for heading in headings}
        first_h1[path] = next((heading.strip() for heading in H1.findall(text)), None)
        if first_h1[path]:
            titles.setdefault(first_h1[path].lower(), []).append(
                _relative_posix(path, root)
            )

    links = {}
    checked_links = 0
    valid_links = 0
    checked_anchors = 0
    valid_anchors = 0
    valid_navigation_destinations = set()
    for path in files:
        links[path] = []
        text = texts[path]
        for raw_target in LINK.findall(text):
            raw_target = unquote(raw_target)
            target, _, anchor = raw_target.partition("#")
            if not target and anchor:
                target = "#" + anchor
            if target.startswith("#"):
                destination = path
            elif re.match(r"^[A-Za-z][A-Za-z0-9+.-]*:", target, re.I):
                continue
            else:
                destination = None
            checked_links += 1
            if anchor:
                checked_anchors += 1
            if destination is None:
                try:
                    destination = safe_path(path.parent / target, root)
                except ValueError:
                    findings.append(
                        {
                            "kind": "outside-link",
                            "path": _relative_posix(path, root),
                            "target": target,
                        }
                    )
                    continue
            if not destination.exists():
                findings.append(
                    {
                        "kind": "missing-link",
                        "path": _relative_posix(path, root),
                        "target": target,
                    }
                )
                continue
            valid_links += 1
            links[path].append(destination)
            if (
                path == mapfile
                and destination != mapfile
                and destination in files
                and destination.is_file()
                and destination.suffix.lower() == ".md"
            ):
                valid_navigation_destinations.add(destination)
            if anchor and destination not in anchors:
                destination_relative = _relative_posix(destination, root)
                if route_matches_patterns(destination_relative, cold_patterns):
                    anchors[destination] = set()
                else:
                    try:
                        _assert_no_reparse_components(destination)
                        anchors[destination] = {
                            slug(heading)
                            for heading in HEADING.findall(
                                strip_fences(
                                    destination.read_text(
                                        encoding="utf-8", errors="replace"
                                    )
                                )
                            )
                        }
                    except (OSError, UnicodeError, ValueError):
                        anchors[destination] = set()
            if anchor:
                if slug(anchor) in anchors.get(destination, set()):
                    valid_anchors += 1
                else:
                    findings.append(
                        {
                            "kind": "missing-anchor",
                            "path": _relative_posix(path, root),
                            "target": target + "#" + anchor,
                        }
                    )

    map_exists = map_norm not in selected_reparse_paths and mapfile in files
    if not map_exists and scoped:
        findings.append({"kind": "missing-map", "map": map_norm})

    map_current_routes = []
    if map_exists:
        for raw_target, marker in CURRENT_ROUTE_LINK.findall(texts[mapfile]):
            target = unquote(raw_target).partition("#")[0]
            if re.match(r"^[A-Za-z][A-Za-z0-9+.-]*:", target, re.I):
                continue
            try:
                destination = mapfile if not target else safe_path(mapfile.parent / target, root)
            except ValueError:
                continue
            if destination.exists() and destination.is_file() and not _is_reparse(destination):
                map_current_routes.append(
                    {
                        "route": _relative_posix(destination, root),
                        "marker": marker,
                    }
                )
    map_current_routes = sorted(
        {
            (row["route"], row["marker"]): row
            for row in map_current_routes
        }.values(),
        key=lambda row: (row["route"], row["marker"]),
    )

    reachable = set()
    conflict_fingerprints = {
        finding.get("fingerprint")
        for finding in findings
        if finding.get("kind") == "cold-current-conflict"
    }
    if mapfile in files:
        reachable = {mapfile}
        todo = [mapfile]
        while todo:
            for destination in links.get(todo.pop(), []):
                destination_relative = _relative_posix(destination, root)
                if (
                    destination.is_file()
                    and route_matches_patterns(destination_relative, cold_patterns)
                ):
                    conflict = _conflict_finding(
                        destination_relative, map_path=map_norm
                    )
                    if conflict["fingerprint"] not in conflict_fingerprints:
                        findings.append(conflict)
                        conflict_fingerprints.add(conflict["fingerprint"])
                    continue
                if destination in files and destination not in reachable:
                    reachable.add(destination)
                    todo.append(destination)
        for path in files:
            if path not in reachable:
                findings.append(
                    {
                        "kind": "unreachable",
                        "path": _relative_posix(path, root),
                        "map": map_norm,
                    }
                )

    for relative in normalized_hot_paths:
        if relative in selected_reparse_paths:
            continue
        path = selected_paths[relative]
        if not path.is_file():
            continue
        text = texts.get(path)
        if text is None:
            text = strip_fences(path.read_text(encoding="utf-8", errors="replace"))
        for source_group in SOURCES_LINE.findall(text):
            for raw_route in BACKTICK_ROUTE.findall(source_group):
                source_route = normalize_repo_relative(
                    raw_route.partition("#")[0], "Sources route"
                )
                source = safe_path(root / source_route, root)
                if (
                    source.is_file()
                    and route_matches_patterns(source_route, cold_patterns)
                ):
                    conflict = _conflict_finding(
                        source_route, source_path=relative
                    )
                    if conflict["fingerprint"] not in conflict_fingerprints:
                        findings.append(conflict)
                        conflict_fingerprints.add(conflict["fingerprint"])

    for title, paths in titles.items():
        if len(paths) > 1:
            findings.append({"kind": "duplicate-title", "title": title, "paths": paths})

    maintained_path_names = {_relative_posix(path, root) for path in files}
    maintained_path_names.update(
        finding["path"]
        for finding in findings
        if finding.get("kind") == "symlink"
        and Path(finding.get("path", "")).suffix.lower() == ".md"
    )
    implicated_paths = {
        finding.get("path")
        for finding in findings
        if finding.get("kind") in {"symlink", "outside-link"}
        and finding.get("path") in maintained_path_names
    }
    hot_path = hot_path_summary(root, normalized_hot_paths, selected_reparse_paths)
    map_text = texts.get(mapfile, "")
    measurements = {
        "map_exists": map_exists,
        "map_has_h1": bool(first_h1.get(mapfile)),
        "map_has_body": _has_body_paragraph(map_text),
        "map_has_h2": bool(H2.search(map_text)),
        "maintained_files": len(files),
        "maintained_paths": len(maintained_path_names),
        "safe_maintained_paths": max(
            0, len(maintained_path_names) - len(implicated_paths)
        ),
        "checked_links": checked_links,
        "valid_links": valid_links,
        "checked_anchors": checked_anchors,
        "valid_anchors": valid_anchors,
        "valid_navigation_routes": len(valid_navigation_destinations),
        "reachable_files": len(reachable),
        "usable_unique_titles": sum(
            len(paths) for paths in titles.values() if len(paths) == 1
        ),
        "hot_bytes": hot_path["bytes"],
        "hot_path_files": list(hot_path["files"]),
        "map_current_routes": map_current_routes,
        "cold_paths": sorted(_relative_posix(path, root) for path in cold_paths),
        "prunes": prune_summary(applied_prunes),
    }
    return findings, hot_path, measurements


__all__ = (
    "H1",
    "H2",
    "HEADING",
    "LINK",
    "CURRENT_ROUTE_LINK",
    "discover_markdown",
    "hot_path_summary",
    "scan_documents",
    "strip_fences",
)
