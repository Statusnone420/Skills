#!/usr/bin/env python3
"""Build and validate deterministic cross-harness bundles from skills/docs."""
from __future__ import annotations
import argparse, json, re, shutil, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "skills" / "docs"
COMMANDS = ("init", "context", "write", "update", "audit", "fix", "map", "classify", "migrate", "check", "cleanup", "help")

def clean_output(path: Path) -> None:
    if path.exists():
        if path.is_symlink(): raise ValueError("output must not be a symlink")
        shutil.rmtree(path)
    path.mkdir(parents=True)

def slash_skill(text: str) -> str:
    return text.replace("---\n\n# Diátaxis Docs", "---\nuser-invocable: true\ndisable-model-invocation: true\n\n# Diátaxis Docs", 1)

def generate(output: Path) -> None:
    source_text = (SOURCE / "SKILL.md").read_text(encoding="utf-8")
    clean_output(output)
    for vendor in ("claude", "copilot", "grok", "cursor"):
        d = output / vendor; d.mkdir()
        (d / "SKILL.md").write_text(slash_skill(source_text), encoding="utf-8", newline="\n")
        for resource in ("references", "agents"):
            shutil.copytree(SOURCE / resource, d / resource)
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
    manifest = {"name":"statusnone-skills", "version":"0.1.0", "description":"Statusnone repository documentation skill", "author":{"name":"Statusnone", "url":"https://github.com/Statusnone420/skills"}, "license":"Apache-2.0", "repository":"https://github.com/Statusnone420/skills", "skills":"./skills/", "interface":{"displayName":"Statusnone Skills", "developerName":"Statusnone", "shortDescription":"Bounded repository documentation", "longDescription":"Evidence-backed Diátaxis documentation assistance for repositories.", "category":"Productivity", "capabilities":["Read"], "defaultPrompt":["$docs help"]}}
    (plugin / ".codex-plugin" / "plugin.json").write_text(json.dumps(manifest, sort_keys=True, indent=2)+"\n", encoding="utf-8", newline="\n")
    (plugin / "skills" / "docs" / "SKILL.md").write_text(source_text, encoding="utf-8", newline="\n")
    for resource in ("references", "agents", "scripts"):
        shutil.copytree(SOURCE / resource, plugin / "skills" / "docs" / resource)

def validate(output: Path) -> list[str]:
    errors=[]; canonical=(SOURCE/"SKILL.md").read_text(encoding="utf-8")
    body=canonical.split("---",2)[-1]
    def content(text):
        return re.sub(r"\A\nuser-invocable: true\ndisable-model-invocation: true", "", text.split("---",2)[-1])
    if not re.search(r"^name:\s*docs$", canonical, re.M): errors.append("canonical frontmatter name")
    if len(body.split()) > 500: errors.append("canonical word budget")
    for v in ("claude","copilot","grok","cursor"):
        p=output/v/"SKILL.md"
        if not p.exists() or "user-invocable: true" not in p.read_text() or "disable-model-invocation: true" not in p.read_text(): errors.append(f"slash parity {v}")
        elif content(p.read_text(encoding="utf-8")) != body: errors.append(f"body parity {v}")
    for v in ("gemini","opencode"):
        if "raw trailing text" not in (output/v/"docs.md").read_text(encoding="utf-8").lower(): errors.append(f"wrapper {v}")
    for c in COMMANDS:
        if not (output/"web"/f"docs-{c}.txt").exists(): errors.append(f"web command {c}")
    if not (output/"plugin/skills/docs/SKILL.md").exists(): errors.append("plugin skill")
    elif (output/"plugin/skills/docs/SKILL.md").read_text(encoding="utf-8") != canonical: errors.append("plugin parity")
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
