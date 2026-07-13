#!/usr/bin/env python3
"""Read-only, standard-library documentation integrity checker."""
import argparse, json, os, re, sys, unicodedata, stat
from collections.abc import Mapping
from pathlib import Path
from urllib.parse import unquote

MAX_HOT = 16 * 1024
HEALTH_RUBRIC_VERSION = 1
HEALTH_WEIGHTS = {
    "entry": 20,
    "path_safety": 15,
    "links": 15,
    "anchors": 10,
    "reachability": 20,
    "titles": 10,
    "hot_path": 10,
}
LINK = re.compile(r"\[[^\]]*\]\(([^)]*)\)")
HEADING = re.compile(r"^#{1,6}\s+(.+?)\s*#*\s*$", re.M)
H1 = re.compile(r"^#\s+(.+?)\s*#*\s*$", re.M)


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


def health_summary(measurements: Mapping):
    map_exists = measurements.get("map_exists") is True
    maintained_files = _count(measurements, "maintained_files")
    maintained_paths = _count(measurements, "maintained_paths")
    safe_maintained_paths = min(maintained_paths, _count(measurements, "safe_maintained_paths"))
    checked_links = _count(measurements, "checked_links")
    valid_links = min(checked_links, _count(measurements, "valid_links"))
    checked_anchors = _count(measurements, "checked_anchors")
    valid_anchors = min(checked_anchors, _count(measurements, "valid_anchors"))
    reachable_files = min(maintained_files, _count(measurements, "reachable_files"))
    usable_unique_titles = min(maintained_files, _count(measurements, "usable_unique_titles"))
    hot_bytes = _count(measurements, "hot_bytes")

    earned = {
        "entry": HEALTH_WEIGHTS["entry"] if map_exists else 0,
        "path_safety": HEALTH_WEIGHTS["path_safety"] * _fraction(safe_maintained_paths, maintained_paths),
        "links": HEALTH_WEIGHTS["links"] * _fraction(valid_links, checked_links),
        "anchors": HEALTH_WEIGHTS["anchors"] if checked_anchors == 0 else HEALTH_WEIGHTS["anchors"] * _fraction(valid_anchors, checked_anchors),
        "reachability": HEALTH_WEIGHTS["reachability"] * _fraction(reachable_files, maintained_files),
        "titles": HEALTH_WEIGHTS["titles"] * _fraction(usable_unique_titles, maintained_files),
        "hot_path": 0 if not map_exists else HEALTH_WEIGHTS["hot_path"] * min(1, MAX_HOT / max(hot_bytes, 1)),
    }
    raw = {
        "entry": {"map_exists": map_exists},
        "path_safety": {"safe": safe_maintained_paths, "maintained": maintained_paths},
        "links": {"valid": valid_links, "checked": checked_links},
        "anchors": {"valid": valid_anchors, "checked": checked_anchors},
        "reachability": {"reachable": reachable_files, "maintained": maintained_files},
        "titles": {"usable_unique": usable_unique_titles, "maintained": maintained_files},
        "hot_path": {"bytes": hot_bytes, "limit": MAX_HOT},
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
    earned_weight = round(sum(category["earned"] for category in categories.values()), 2)
    percentage = max(0, min(100, int(earned_weight + 0.5)))
    return {
        "rubric_version": HEALTH_RUBRIC_VERSION,
        "percentage": percentage,
        "meter": health_meter(percentage),
        "earned_weight": earned_weight,
        "available_weight": sum(HEALTH_WEIGHTS.values()),
        "categories": categories,
    }

def strip_fences(text):
    out=[]; fenced=False; marker=None
    for line in text.splitlines(True):
        m=re.match(r"^\s*(```+|~~~+)", line)
        if m:
            if not fenced: fenced=True; marker=m.group(1)[0]
            elif m.group(1)[0]==marker: fenced=False
            out.append("\n"); continue
        out.append("\n" if fenced else line)
    return "".join(out)

def slug(value):
    value = unquote(value).strip().lower()
    value = unicodedata.normalize("NFKC", value)
    return re.sub(r"[^\w -]", "", value, flags=re.UNICODE).replace(" ", "-")

def _is_reparse(path):
    try:
        st = os.lstat(path)
    except OSError:
        return False
    if stat.S_ISLNK(st.st_mode):
        return True
    return bool(getattr(st, "st_file_attributes", 0) & 0x400)

def _assert_no_reparse_components(path):
    """Reject symlink/junction/reparse components before any filesystem use."""
    p = Path(path).absolute()
    parts = p.parts
    current = Path(parts[0])
    for part in parts[1:]:
        current = current / part
        if _is_reparse(current):
            raise ValueError("symlink or reparse path component")

def safe_path(path, root):
    """Resolve only paths whose existing components are non-symlink and root-confined."""
    raw = os.path.abspath(os.fspath(path)); base = os.path.abspath(os.fspath(root))
    if os.path.commonpath((raw, base)) != base: raise ValueError("path escapes root")
    rel = os.path.relpath(raw, base)
    current = base
    _assert_no_reparse_components(base)
    if current != raw and _is_reparse(current): raise ValueError("symlink root")
    for part in rel.split(os.sep):
        current = os.path.join(current, part)
        if os.path.lexists(current) and _is_reparse(current): raise ValueError("symlink path")
    return Path(raw)

def hot_path_summary(root, hot_paths):
    files=[]; total=0
    for relative in hot_paths:
        path = safe_path(root / relative, root)
        if path.is_file() and not _is_reparse(path):
            size = path.stat().st_size; total += size
            files.append({"path":Path(relative).as_posix(),"bytes":size})
    return {"files":files,"bytes":total,"limit":MAX_HOT,"percentage":round(total / MAX_HOT * 100, 2)}

def unique_relative_paths(paths):
    unique=[]; seen=set()
    for relative in paths:
        normalized = os.path.normpath(os.fspath(relative))
        key = os.path.normcase(normalized)
        if key not in seen:
            seen.add(key); unique.append(Path(normalized).as_posix())
    return unique

def _check_with_measurements(root, map_path="docs/README.md", hot_paths=None, scope="docs"):
    root = Path(root); findings=[]; files=[]; candidate_files=[]
    _assert_no_reparse_components(root)
    if Path(map_path).is_absolute() or any(x == '..' for x in Path(map_path).parts): raise ValueError("map must be repo-relative")
    if hot_paths and any(Path(x).is_absolute() or any(y == '..' for y in Path(x).parts) for x in hot_paths): raise ValueError("hot paths must be repo-relative")
    hot_paths = unique_relative_paths([map_path] + (hot_paths or []))
    if Path(scope).is_absolute() or any(x == '..' for x in Path(scope).parts): raise ValueError("scope must be repo-relative")
    scope_path = safe_path(root / scope, root)
    if scope_path.exists() and not scope_path.is_dir(): raise ValueError("scope must be a directory")
    mapfile = safe_path(root / map_path, root)
    for r in hot_paths: safe_path(root / r, root)
    for base, dirs, names in os.walk(root, followlinks=False):
        dirs[:] = [d for d in dirs if not _is_reparse(Path(base)/d)]
        for name in names:
            p=Path(base)/name
            if p.suffix.lower()==".md": candidate_files.append(p)
            if _is_reparse(p): findings.append({"kind":"symlink","path":str(p.relative_to(root))}); continue
            if p.suffix.lower()==".md": files.append(p)
    scope_norm = scope.strip('/').replace('\\','/')
    prefix = '' if scope_norm in ('', '.') else scope_norm.rstrip('/') + '/'
    scoped=[p for p in files if (not prefix) or str(p.relative_to(root)).replace('\\','/').startswith(prefix)]
    candidate_scoped=[p for p in candidate_files if (not prefix) or str(p.relative_to(root)).replace('\\','/').startswith(prefix)]
    files=scoped+([mapfile] if mapfile in files and mapfile not in scoped else [])
    candidate_files_for_health=candidate_scoped+([mapfile] if mapfile in candidate_files and mapfile not in candidate_scoped else [])
    anchors={}; titles={}; first_h1={}
    for p in files:
        text=strip_fences(p.read_text(encoding="utf-8", errors="replace"))
        hs=HEADING.findall(text); anchors[p]={slug(h) for h in hs}
        first_h1[p]=next((h.strip() for h in H1.findall(text)), None)
        if first_h1[p]: titles.setdefault(first_h1[p].lower(), []).append(str(p.relative_to(root)))
    links={}; checked_links=0; valid_links=0; checked_anchors=0; valid_anchors=0
    for p in files:
        links[p]=[]; text=strip_fences(p.read_text(encoding="utf-8", errors="replace"))
        for rawtarget in LINK.findall(text):
            rawtarget=unquote(rawtarget); target, sep, anchor = rawtarget.partition('#')
            if not target and anchor: target="#"+anchor
            if target.startswith("#"): dest=p
            elif re.match(r"^[A-Za-z][A-Za-z0-9+.-]*:", target, re.I): continue
            else: dest=None
            checked_links += 1
            if anchor: checked_anchors += 1
            if dest is None:
                try: dest=safe_path((p.parent/target), root)
                except ValueError: findings.append({"kind":"outside-link","path":str(p.relative_to(root)),"target":target}); continue
            if not dest.exists(): findings.append({"kind":"missing-link","path":str(p.relative_to(root)),"target":target}); continue
            valid_links += 1
            links[p].append(dest)
            if anchor and dest not in anchors:
                try:
                    _assert_no_reparse_components(dest)
                    anchors[dest] = {slug(h) for h in HEADING.findall(strip_fences(dest.read_text(encoding="utf-8", errors="replace")))}
                except (OSError, UnicodeError, ValueError):
                    anchors[dest] = set()
            if anchor:
                if slug(anchor) in anchors.get(dest,set()): valid_anchors += 1
                else: findings.append({"kind":"missing-anchor","path":str(p.relative_to(root)),"target":target+"#"+anchor})
    if not mapfile.exists() and scoped:
        findings.append({"kind":"missing-map","map":map_path})
    reachable=set()
    if mapfile in files:
        reachable={mapfile}; todo=[mapfile]
        while todo:
            for dest in links.get(todo.pop(),[]):
                if dest in files and dest not in reachable: reachable.add(dest); todo.append(dest)
        for p in scoped:
            if p not in reachable: findings.append({"kind":"unreachable","path":str(p.relative_to(root)),"map":map_path})
    for title, paths in titles.items():
        if len(paths)>1: findings.append({"kind":"duplicate-title","title":title,"paths":paths})
    maintained_path_names={str(p.relative_to(root)).replace('\\','/') for p in candidate_files_for_health}
    implicated_paths={
        finding.get("path")
        for finding in findings
        if finding.get("kind") in {"symlink", "outside-link"} and finding.get("path") in maintained_path_names
    }
    hot_path = hot_path_summary(root, hot_paths)
    if hot_path["bytes"]>MAX_HOT: findings.append({"kind":"hot-path-bytes","bytes":hot_path["bytes"],"limit":MAX_HOT})
    measurements = {
        "map_exists": mapfile in files,
        "maintained_files": len(files),
        "maintained_paths": len(maintained_path_names),
        "safe_maintained_paths": max(0, len(maintained_path_names) - len(implicated_paths)),
        "checked_links": checked_links,
        "valid_links": valid_links,
        "checked_anchors": checked_anchors,
        "valid_anchors": valid_anchors,
        "reachable_files": len(reachable),
        "usable_unique_titles": sum(len(paths) for paths in titles.values() if len(paths) == 1),
        "hot_bytes": hot_path["bytes"],
    }
    return findings, hot_path, measurements


def check(root, map_path="docs/README.md", hot_paths=None, scope="docs"):
    findings, hot_path, _ = _check_with_measurements(root, map_path, hot_paths, scope)
    return findings, hot_path

def main(argv=None):
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    argv = list(sys.argv[1:] if argv is None else argv)
    value_options = {"--map", "--hot", "--scope"}
    positional = []
    skip = False
    for arg in argv:
        if skip: skip = False; continue
        if arg in value_options: skip = True; continue
        if arg.startswith("--"): continue
        positional.append(arg)
    if "--json" in argv and not positional:
        print(json.dumps({"status": "error", "has_findings": False, "error": "the following arguments are required: root", "findings": []}))
        return 2
    ap=argparse.ArgumentParser(); ap.add_argument("root"); ap.add_argument("--json",action="store_true"); ap.add_argument("--agent",action="store_true"); ap.add_argument("--map",default="docs/README.md"); ap.add_argument("--hot",default=None); ap.add_argument("--scope",default="docs")
    ns=ap.parse_args(argv)
    try:
        if ns.agent and not ns.json: raise ValueError("--agent requires --json")
        if any(part == ".." for part in Path(ns.root).parts): raise ValueError("path traversal is not allowed")
        raw=Path(ns.root).expanduser().absolute()
        _assert_no_reparse_components(raw)
        if _is_reparse(raw) or not raw.is_dir(): raise ValueError("root must be a real directory")
        root=safe_path(raw, raw); hot=ns.hot.split(",") if ns.hot else None
        if Path(ns.map).is_absolute() or any(x=='..' for x in Path(ns.map).parts): raise ValueError("map must be repo-relative")
        if hot and any(Path(x).is_absolute() or any(y=='..' for y in Path(x).parts) for x in hot): raise ValueError("hot paths must be repo-relative")
        if Path(ns.scope).is_absolute() or any(x=='..' for x in Path(ns.scope).parts): raise ValueError("scope must be repo-relative")
        findings, hot_path, measurements=_check_with_measurements(root, ns.map, hot, ns.scope)
    except (OSError,ValueError,UnicodeError) as exc:
        if ns.json: print(json.dumps({"status":"error","has_findings":False,"error":str(exc),"findings":[]}))
        else: print(f"error: {exc}")
        return 2
    if ns.json: print(json.dumps({"status":"findings" if findings else "clean","has_findings":bool(findings),"root":str(root),"hot_path":hot_path,"health":health_summary(measurements),"findings":findings},ensure_ascii=True))
    elif findings:
        for f in findings: print(f"{f['kind']}: {f}")
    else: print("clean")
    return 0 if ns.agent else (1 if findings else 0)

if __name__ == "__main__": sys.exit(main())
