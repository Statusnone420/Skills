#!/usr/bin/env python3
"""Build and validate deterministic cross-harness bundles from skills/docs."""
from __future__ import annotations
import argparse, json, os, re, shutil, stat, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "skills" / "docs"
COMMANDS = ("init", "context", "write", "update", "audit", "fix", "map", "classify", "migrate", "check", "cleanup", "help")
MARKER_NAME = ".statusnone-adapters-output"
MARKER_TEXT = "statusnone-adapters-v1\n"
PROTECTED_ROOTS = tuple(ROOT / name for name in (".git", ".github", ".superpowers", "docs", "evals", "skills", "tests", "tools"))

def _lexical(path: Path) -> Path:
    return Path(os.path.abspath(os.fspath(path)))

def _is_reparse(path: Path) -> bool:
    info = os.lstat(path)
    return stat.S_ISLNK(info.st_mode) or bool(getattr(info, "st_file_attributes", 0) & 0x400)

def _safe_output(path: Path) -> Path:
    path = _lexical(path); root = _lexical(ROOT)
    try:
        relative = path.relative_to(root)
    except ValueError as exc:
        raise ValueError("output must be repository-confined") from exc
    if not relative.parts:
        raise ValueError("output must not be the repository root")
    for protected in PROTECTED_ROOTS:
        protected = _lexical(protected)
        if path == protected or protected in path.parents:
            raise ValueError(f"output overlaps protected repository subtree: {protected.name}")
    current = root
    for part in relative.parts:
        current /= part
        if os.path.lexists(current) and _is_reparse(current):
            raise ValueError("output path must not contain a symlink or reparse point")
    return path

def _has_valid_marker(path: Path) -> bool:
    marker = path / MARKER_NAME
    return marker.is_file() and not _is_reparse(marker) and marker.read_text(encoding="utf-8") == MARKER_TEXT

def clean_output(path: Path) -> None:
    path = _safe_output(path)
    if path.exists():
        if not path.is_dir(): raise ValueError("output must be a directory")
        marker = path / MARKER_NAME
        if os.path.lexists(marker) and not _has_valid_marker(path):
            raise ValueError("output ownership marker is invalid")
        if not os.path.lexists(marker) and path != _lexical(ROOT / "adapters"):
            raise ValueError("refusing to replace an unowned output directory")
        shutil.rmtree(path)
    path.mkdir(parents=True)
    (path / MARKER_NAME).write_text(MARKER_TEXT, encoding="utf-8", newline="\n")

def slash_skill(text: str) -> str:
    canonical = (SOURCE / "SKILL.md").read_text(encoding="utf-8")
    expected_header = canonical.split("---", 2)[1]
    if not text.startswith("---\n") or "\n---\n" not in text[4:]: raise ValueError("canonical frontmatter separators required")
    parts = text.split("---", 2)
    if len(parts) != 3: raise ValueError("canonical frontmatter required")
    if parts[1] != expected_header: raise ValueError("frontmatter must match canonical source exactly")
    return "---" + parts[1].rstrip() + "\nuser-invocable: true\ndisable-model-invocation: true\n---" + parts[2]

def generate(output: Path) -> None:
    source_text = (SOURCE / "SKILL.md").read_text(encoding="utf-8")
    clean_output(output)
    for vendor in ("claude", "copilot", "grok", "cursor"):
        d = output / vendor; d.mkdir()
        (d / "SKILL.md").write_text(slash_skill(source_text), encoding="utf-8", newline="\n")
        for resource in ("references", "agents"):
            shutil.copytree(SOURCE / resource, d / resource, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    wrapper = (
        "# /docs wrapper\n\n"
        "Instruction-enforced invocation: activate the shared `docs` skill explicitly, then "
        "parse one command and forward the raw trailing text verbatim (without shell interpolation).\n"
        "Usage: `/docs <command> [raw trailing text]`.\n"
    )
    for vendor in ("gemini", "opencode"):
        d = output / vendor; d.mkdir(); (d / "docs.md").write_text(wrapper, encoding="utf-8", newline="\n")
    wd = output / "web"; wd.mkdir()
    for command in COMMANDS:
        (wd / f"docs-{command}.txt").write_text(
            f"Activate the shared docs skill and run `{command}` with the user's raw trailing text. "
            "This generic web prompt has no guaranteed filesystem, shell, or repository-tool capabilities; "
            "report unavailable tools honestly.\n", encoding="utf-8", newline="\n")
    plugin = output / "plugin"; (plugin / ".codex-plugin").mkdir(parents=True); (plugin / "skills" / "docs").mkdir(parents=True)
    manifest = {"name":"statusnone-skills", "version":"0.1.0", "description":"Statusnone repository documentation skill", "author":{"name":"Statusnone", "url":"https://github.com/Statusnone420/skills"}, "license":"Apache-2.0", "repository":"https://github.com/Statusnone420/skills", "skills":"./skills/", "interface":{"displayName":"Statusnone Skills", "developerName":"Statusnone", "shortDescription":"Bounded repository documentation", "longDescription":"Evidence-backed Diátaxis documentation assistance for repositories.", "category":"Productivity", "capabilities":["Read", "Write"], "defaultPrompt":["$docs help"]}}
    (plugin / ".codex-plugin" / "plugin.json").write_text(json.dumps(manifest, sort_keys=True, indent=2)+"\n", encoding="utf-8", newline="\n")
    (plugin / "skills" / "docs" / "SKILL.md").write_text(source_text, encoding="utf-8", newline="\n")
    for resource in ("references", "agents", "scripts"):
        shutil.copytree(SOURCE / resource, plugin / "skills" / "docs" / resource, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))

def validate(output: Path) -> list[str]:
    errors=[]; canonical=(SOURCE/"SKILL.md").read_text(encoding="utf-8")
    body=canonical.split("---",2)[-1]
    def frontmatter(text):
        parts=text.split("---",2)
        if len(parts)!=3: return None
        vals={}
        for line in parts[1].strip().splitlines():
            if not line.strip() or ":" not in line: continue
            key,val=line.split(":",1); key=key.strip()
            if key in vals: return None
            vals[key]=val.strip()
        return vals
    def content(text):
        return re.sub(r"\A\nuser-invocable: true\ndisable-model-invocation: true", "", text.split("---",2)[-1])
    if not re.search(r"^name:\s*docs$", canonical, re.M): errors.append("canonical frontmatter name")
    if re.search(r"\b(?:Claude|Copilot|Grok|Cursor|Gemini|OpenCode|GPT|model)\b", body, re.I): errors.append("forbidden vendor/model term")
    if len(body.split()) > 500: errors.append("canonical word budget")
    links = re.findall(r"\[[^]]+\]\(([^)#]+)", canonical)
    for link in links:
        target = SOURCE / link
        if not target.is_file(): errors.append(f"missing reference {link}")
        elif re.search(r"\[[^]]+\]\(([^)#]+)", target.read_text(encoding="utf-8")):
            errors.append(f"reference exceeds one hop {link}")
    for v in ("claude","copilot","grok","cursor"):
        p=output/v/"SKILL.md"
        parsed=frontmatter(p.read_text(encoding="utf-8")) if p.exists() else None
        if parsed is None or parsed.get("user-invocable") != "true" or parsed.get("disable-model-invocation") != "true": errors.append(f"frontmatter {v}")
        elif content(p.read_text(encoding="utf-8")) != body: errors.append(f"body parity {v}")
    for v in ("gemini","opencode"):
        wrapper = (output/v/"docs.md").read_text(encoding="utf-8")
        if "raw trailing text" not in wrapper.lower() or "$(" in wrapper or "`$" in wrapper: errors.append(f"wrapper {v}")
    for c in COMMANDS:
        if not (output/"web"/f"docs-{c}.txt").exists(): errors.append(f"web command {c}")
    if not (output/"plugin/skills/docs/SKILL.md").exists(): errors.append("plugin skill")
    elif (output/"plugin/skills/docs/SKILL.md").read_text(encoding="utf-8") != canonical: errors.append("plugin parity")
    expected = {MARKER_NAME} | {f"{v}/SKILL.md" for v in ("claude","copilot","grok","cursor")} | {f"{v}/{r}" for v in ("claude","copilot","grok","cursor") for r in ("agents/openai.yaml","references/commands.md","references/memory.md")} | {f"{v}/docs.md" for v in ("gemini","opencode")} | {f"web/docs-{c}.txt" for c in COMMANDS} | {"plugin/.codex-plugin/plugin.json", "plugin/skills/docs/SKILL.md", "plugin/skills/docs/agents/openai.yaml", "plugin/skills/docs/references/commands.md", "plugin/skills/docs/references/memory.md", "plugin/skills/docs/scripts/check.py"}
    actual = {p.relative_to(output).as_posix() for p in output.rglob("*") if p.is_file()}
    marker = output / MARKER_NAME
    if not marker.is_file() or marker.read_text(encoding="utf-8") != MARKER_TEXT: errors.append("output ownership marker")
    for extra in sorted(actual - expected): errors.append(f"extra file {extra}")
    expected_dirs=set()
    for x in expected:
        parts=x.split('/')[:-1]
        for i in range(1,len(parts)+1): expected_dirs.add('/'.join(parts[:i]))
    for d in output.rglob('*'):
        if d.is_dir() and d.relative_to(output).as_posix() not in expected_dirs: errors.append(f"extra directory {d.relative_to(output).as_posix()}")
    for v in ("claude","copilot","grok","cursor"):
        for rel in ("agents/openai.yaml","references/commands.md","references/memory.md"):
            if (output/v/rel).read_bytes() != (SOURCE/rel).read_bytes(): errors.append(f"resource parity {v}/{rel}")
    for rel in ("agents/openai.yaml","references/commands.md","references/memory.md","scripts/check.py"):
        if (output/"plugin/skills/docs"/rel).read_bytes() != (SOURCE/rel).read_bytes(): errors.append(f"resource parity plugin/{rel}")
    return errors

def main(argv=None):
    ap=argparse.ArgumentParser(); ap.add_argument("mode", nargs="?", choices=("generate",), default="generate"); ap.add_argument("--check", action="store_true"); ap.add_argument("--output", type=Path, default=ROOT/"adapters")
    ns=ap.parse_args(argv)
    try:
        if ns.check: errors=validate(ns.output)
        else: generate(ns.output); errors=validate(ns.output)
    except (OSError, ValueError, UnicodeError) as exc: print(f"error: {exc}", file=sys.stderr); return 2
    if errors:
        print("\n".join(errors), file=sys.stderr); return 1
    print("clean"); return 0
if __name__ == "__main__": raise SystemExit(main())
