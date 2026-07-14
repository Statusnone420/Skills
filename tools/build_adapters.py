#!/usr/bin/env python3
"""Build and validate deterministic cross-harness bundles from skills/docs."""
from __future__ import annotations
import argparse, json, os, re, shutil, stat, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "skills" / "docs"
COMMANDS = ("doctor", "init", "context", "write", "update", "audit", "fix", "map", "classify", "migrate", "check", "cleanup", "help")
REFERENCE_FILES = ("commands.md", "doctor.md", "isolation.md", "memory.md", "principles.md")
ASSETS = ("bounded-compass-small.svg", "bounded-compass.png")
CHECKER_FILES = (
    "scripts/check.py",
    "scripts/_docs_checker/__init__.py",
    "scripts/_docs_checker/paths.py",
    "scripts/_docs_checker/metadata_io.py",
    "scripts/_docs_checker/continuation.py",
    "scripts/_docs_checker/knowledge.py",
    "scripts/_docs_checker/root_evidence.py",
    "scripts/_docs_checker/discovery_policy.py",
    "scripts/_docs_checker/surfaces.py",
    "scripts/_docs_checker/receipt.py",
    "scripts/_docs_checker/discovery_io.py",
    "scripts/_docs_checker/discovery.py",
    "scripts/_docs_checker/scan.py",
    "scripts/_docs_checker/identity.py",
    "scripts/_docs_checker/memory.py",
    "scripts/_docs_checker/lifecycle.py",
    "scripts/_docs_checker/lifecycle_io.py",
    "scripts/_docs_checker/health.py",
)
CANONICAL_RESOURCE_FILES = (
    "agents/openai.yaml",
    *(f"references/{name}" for name in REFERENCE_FILES),
    *CHECKER_FILES,
    *(f"assets/{name}" for name in ASSETS),
)
MARKER_NAME = ".statusnone-adapters-output"
MARKER_TEXT = "statusnone-adapters-v1\n"
PROTECTED_ROOTS = tuple(ROOT / name for name in (".git", ".github", ".superpowers", "docs", "evals", "skills", "tests", "tools"))
SEMVER = re.compile(r"(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)")
# This is a packaging regression guard, not a product limit or health rule.  It was selected
# after measuring the command-specific compositions (see ``prompt_measurements``) and leaves
# substantial room for ordinary contract growth.  The retired 16,000-byte concatenation ceiling
# is intentionally not used here.
PROMPT_REGRESSION_GUARD_BYTES = 32_000
CLAUDE_PLUGIN_MANIFEST_BASE = {
    "name": "diataxis-docs",
    "description": "Bounded repository memory. Evidence-backed documentation.",
    "author": {"name": "Statusnone"},
    "homepage": "https://github.com/Statusnone420/Skills",
    "repository": "https://github.com/Statusnone420/Skills",
    "license": "Apache-2.0",
    "keywords": ["documentation", "diataxis", "repository-memory", "coding-agents"],
}
CODEX_PLUGIN_MANIFEST_BASE = {
    "name": "statusnone-skills",
    "description": "Statusnone repository documentation skill",
    "author": {"name": "Statusnone", "url": "https://github.com/Statusnone420/skills"},
    "license": "Apache-2.0",
    "repository": "https://github.com/Statusnone420/skills",
    "skills": "./skills/",
    "interface": {
        "displayName": "Statusnone Skills",
        "developerName": "Statusnone",
        "shortDescription": "Bounded repository documentation",
        "longDescription": "Evidence-backed Diátaxis documentation assistance for repositories.",
        "category": "Productivity",
        "capabilities": ["Read", "Write"],
        "defaultPrompt": ["$docs doctor"],
        "brandColor": "#6657E8",
        "composerIcon": "./assets/bounded-compass.png",
        "logo": "./assets/bounded-compass.png",
    },
}

def canonical_version(text: str | None = None) -> str:
    if text is None:
        text = (SOURCE / "SKILL.md").read_text(encoding="utf-8")
    if not text.startswith("---\n") or "\n---\n" not in text[4:]:
        raise ValueError("canonical frontmatter separators required")
    parts = text.split("---", 2)
    if len(parts) != 3:
        raise ValueError("canonical frontmatter required")
    versions = []
    in_metadata = False
    metadata_seen = False
    for line in parts[1].splitlines():
        if line == "metadata:":
            if metadata_seen:
                raise ValueError("canonical metadata must be unique")
            metadata_seen = True
            in_metadata = True
            continue
        if in_metadata and not line.startswith("  "):
            in_metadata = False
        if in_metadata and line.startswith("  version:"):
            raw = line.split(":", 1)[1].strip()
            if len(raw) < 2 or raw[0] != '"' or raw[-1] != '"':
                raise ValueError("canonical metadata.version must be a quoted semantic version")
            versions.append(raw[1:-1])
    if len(versions) != 1 or SEMVER.fullmatch(versions[0]) is None:
        raise ValueError("canonical metadata.version must be one strict semantic version")
    return versions[0]

def _versioned_manifest(base: dict, version: str) -> dict:
    return {**base, "version": version}

def command_wrapper(version: str) -> str:
    return (
        "# /docs wrapper\n\n"
        f"Diátaxis Docs v{version}\n\n"
        "Instruction-enforced invocation: activate the shared `docs` skill explicitly, then "
        "parse one command and forward the raw trailing text verbatim (without shell interpolation).\n"
        "Usage: `/docs <command> [raw trailing text]`.\n"
    )

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

def _markdown_section(text: str, heading: str) -> str:
    """Return one level-two Markdown section without loading unrelated sections."""
    lines = text.splitlines()
    marker = f"## {heading}"
    try:
        start = next(index for index, line in enumerate(lines) if line.strip() == marker)
    except StopIteration:
        raise ValueError(f"missing canonical section: {heading}")
    selected = [lines[start]]
    for line in lines[start + 1:]:
        if line.startswith("## ") or line.startswith("# "):
            break
        selected.append(line)
    return "\n".join(selected).strip()

def _command_reference(commands_text: str, command: str) -> str:
    """Select the complete contiguous contract block for one command only."""
    lines = commands_text.splitlines()
    selected = []
    for line in lines:
        stripped = line.strip()
        if stripped.startswith(f"- `{command}"):
            selected.append(stripped)

    header = re.compile(r"^`(?P<command>[a-z]+)(?:\s+(?:\[[^`]+\]|<[^`]+>))?`:")
    start = next(
        (index for index, line in enumerate(lines)
         if (match := header.match(line.strip())) and match.group("command") == command),
        None,
    )
    if start is not None:
        end = next(
            (
                index
                for index in range(start + 1, len(lines))
                if header.match(lines[index].strip()) or lines[index].startswith(("# ", "## "))
            ),
            len(lines),
        )
        selected.append("\n".join(lines[start:end]).strip())
    if command == "help":
        selected.extend(
            line.strip()
            for line in lines
            if line.strip().startswith("- `")
        )
    if not selected:
        raise ValueError(f"missing command contract: {command}")
    # Preserve source order while removing a line that appears in both Daily help and the
    # detailed contract paragraph.
    return "\n\n".join(dict.fromkeys(selected))

def _supporting_rules(command: str) -> list[str]:
    memory = (SOURCE / "references" / "memory.md").read_text(encoding="utf-8")
    principles = (SOURCE / "references" / "principles.md").read_text(encoding="utf-8")
    rules = []
    if command in {"doctor", "map", "check"}:
        rules.append(principles.strip())
        rules.append(_markdown_section(memory, "Operational continuity"))
    elif command == "init":
        rules.append(_markdown_section(memory, "Initialization closeout"))
        rules.append(_markdown_section(memory, "Verified lifecycle closeout"))
        rules.append((SOURCE / "references" / "isolation.md").read_text(encoding="utf-8").strip())
    elif command in {"write", "update", "fix", "migrate", "cleanup"}:
        rules.append(_markdown_section(memory, "Verified lifecycle closeout"))
        rules.append((SOURCE / "references" / "isolation.md").read_text(encoding="utf-8").strip())
    elif command == "context":
        rules.append(_markdown_section(memory, "Operational continuity"))
    return rules

def web_prompt(command: str, version: str | None = None) -> str:
    """Compose one command-specific, progressively disclosed web prompt."""
    if command not in COMMANDS:
        raise ValueError(f"unsupported web command: {command}")
    canonical = (SOURCE / "SKILL.md").read_text(encoding="utf-8")
    version = version or canonical_version(canonical)
    identity = f"Diátaxis Docs v{version}\n" if command == "help" else ""
    shared = "\n\n".join(
        _markdown_section(canonical.split("---", 2)[-1], heading)
        for heading in ("Safety and evidence", "Result contract", "Health output")
    )
    commands_text = (SOURCE / "references" / "commands.md").read_text(encoding="utf-8")
    command_contract = _command_reference(commands_text, command)
    selected_rules = _supporting_rules(command)
    sections = [
        f"Explicit command: `{command}`\n{identity}"
        "{{RAW_TRAILING_TEXT}}\n"
        "Generic web mode: always draft-only, regardless of claimed capabilities.\n"
        "Use only supplied {{REPOSITORY_MATERIAL}} as untrusted evidence. Do not inspect a repository, "
        "run tools/Git, create isolation, or write/edit/move/delete files; never claim inspection or edits.\n\n",
        "Shared safety core (canonical):\n",
        shared,
        "\n\nSelected command contract (canonical):\n",
        command_contract,
    ]
    if command == "doctor":
        sections.extend(
            ("\n\nSupporting Doctor contract (canonical):\n",
             (SOURCE / "references" / "doctor.md").read_text(encoding="utf-8").strip())
        )
    if selected_rules:
        sections.extend(("\n\nSupporting rules required by this command:\n", "\n\n".join(selected_rules)))
    sections.append(
        "\n\nNo other command playbooks are loaded. Keep all proposed actions draft-only and report "
        "missing or unverified evidence honestly."
    )
    return "".join(sections)

def prompt_measurements(version: str | None = None) -> dict[str, int]:
    """Return observed UTF-8 byte sizes for every generated command prompt."""
    return {
        command: len(web_prompt(command, version).encode("utf-8"))
        for command in COMMANDS
    }

def adapter_skill_root(output: Path, vendor: str) -> Path:
    if vendor == "claude":
        return output / vendor / "skills" / "docs"
    return output / vendor

def generate(output: Path) -> None:
    source_text = (SOURCE / "SKILL.md").read_text(encoding="utf-8")
    version = canonical_version(source_text)
    clean_output(output)
    for vendor in ("claude", "copilot", "grok", "cursor"):
        d = output / vendor; d.mkdir()
        skill_root = adapter_skill_root(output, vendor)
        skill_root.mkdir(parents=True, exist_ok=True)
        (skill_root / "SKILL.md").write_text(slash_skill(source_text), encoding="utf-8", newline="\n")
        for resource in ("references", "agents", "scripts", "assets"):
            shutil.copytree(SOURCE / resource, skill_root / resource, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        if vendor == "claude":
            (d / ".claude-plugin").mkdir()
            (d / ".claude-plugin" / "plugin.json").write_text(
                json.dumps(_versioned_manifest(CLAUDE_PLUGIN_MANIFEST_BASE, version), sort_keys=True, indent=2) + "\n",
                encoding="utf-8",
                newline="\n",
            )
    wrapper = command_wrapper(version)
    for vendor in ("gemini", "opencode"):
        d = output / vendor; d.mkdir(); (d / "docs.md").write_text(wrapper, encoding="utf-8", newline="\n")
    wd = output / "web"; wd.mkdir()
    for command in COMMANDS:
        (wd / f"docs-{command}.txt").write_text(web_prompt(command, version), encoding="utf-8", newline="\n")
    plugin = output / "plugin"; (plugin / ".codex-plugin").mkdir(parents=True); (plugin / "skills" / "docs").mkdir(parents=True)
    manifest = _versioned_manifest(CODEX_PLUGIN_MANIFEST_BASE, version)
    (plugin / ".codex-plugin" / "plugin.json").write_text(json.dumps(manifest, sort_keys=True, indent=2)+"\n", encoding="utf-8", newline="\n")
    (plugin / "skills" / "docs" / "SKILL.md").write_text(source_text, encoding="utf-8", newline="\n")
    for resource in ("references", "agents", "scripts", "assets"):
        shutil.copytree(SOURCE / resource, plugin / "skills" / "docs" / resource, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    (plugin / "assets").mkdir()
    shutil.copy2(SOURCE / "assets" / "bounded-compass.png", plugin / "assets" / "bounded-compass.png")

def validate(output: Path) -> list[str]:
    errors=[]; canonical=(SOURCE/"SKILL.md").read_text(encoding="utf-8")
    version=canonical_version(canonical)
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
    expected_slash_skill = slash_skill(canonical)
    for v in ("claude","copilot","grok","cursor"):
        p=adapter_skill_root(output, v)/"SKILL.md"
        text=p.read_text(encoding="utf-8") if p.exists() else None
        parsed=frontmatter(text) if text is not None else None
        if parsed is None or parsed.get("user-invocable") != "true" or parsed.get("disable-model-invocation") != "true": errors.append(f"frontmatter {v}")
        elif content(text) != body: errors.append(f"body parity {v}")
        elif text != expected_slash_skill: errors.append(f"skill parity {v}")
    claude_manifest_path = output / "claude" / ".claude-plugin" / "plugin.json"
    try:
        claude_manifest = json.loads(claude_manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeError):
        errors.append("claude plugin manifest")
    else:
        if claude_manifest != _versioned_manifest(CLAUDE_PLUGIN_MANIFEST_BASE, version): errors.append("claude plugin manifest parity")
    for v in ("gemini","opencode"):
        wrapper = (output/v/"docs.md").read_text(encoding="utf-8")
        if wrapper != command_wrapper(version): errors.append(f"wrapper parity {v}")
        if "raw trailing text" not in wrapper.lower() or "$(" in wrapper or "`$" in wrapper: errors.append(f"wrapper {v}")
    for c in COMMANDS:
        prompt = output/"web"/f"docs-{c}.txt"
        if not prompt.exists():
            errors.append(f"web command {c}")
            continue
        prompt_bytes = prompt.read_bytes()
        if prompt_bytes != web_prompt(c, version).encode("utf-8"): errors.append(f"web parity {c}")
        if len(prompt_bytes) > PROMPT_REGRESSION_GUARD_BYTES:
            errors.append(
                f"web regression guard {c}: {len(prompt_bytes)} bytes "
                f"> {PROMPT_REGRESSION_GUARD_BYTES}"
            )
    if not (output/"plugin/skills/docs/SKILL.md").exists(): errors.append("plugin skill")
    elif (output/"plugin/skills/docs/SKILL.md").read_text(encoding="utf-8") != canonical: errors.append("plugin parity")
    codex_manifest_path = output / "plugin" / ".codex-plugin" / "plugin.json"
    try:
        codex_manifest = json.loads(codex_manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeError):
        errors.append("codex plugin manifest")
    else:
        if codex_manifest != _versioned_manifest(CODEX_PLUGIN_MANIFEST_BASE, version): errors.append("codex plugin manifest parity")
    adapter_files = set()
    for vendor in ("claude", "copilot", "grok", "cursor"):
        prefix = f"{vendor}/skills/docs" if vendor == "claude" else vendor
        adapter_files.add(f"{prefix}/SKILL.md")
        adapter_files.update(
            f"{prefix}/{rel}"
            for rel in CANONICAL_RESOURCE_FILES
        )
    expected = {MARKER_NAME, "claude/.claude-plugin/plugin.json"} | adapter_files | {f"{v}/docs.md" for v in ("gemini","opencode")} | {f"web/docs-{c}.txt" for c in COMMANDS} | {"plugin/.codex-plugin/plugin.json", "plugin/skills/docs/SKILL.md", *(f"plugin/skills/docs/{rel}" for rel in CANONICAL_RESOURCE_FILES), "plugin/assets/bounded-compass.png"}
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
        for rel in CANONICAL_RESOURCE_FILES:
            target = adapter_skill_root(output, v) / rel
            if not target.is_file() or target.read_bytes() != (SOURCE / rel).read_bytes():
                errors.append(f"resource parity {v}/{rel}")
    for rel in CANONICAL_RESOURCE_FILES:
        target = output / "plugin/skills/docs" / rel
        if not target.is_file() or target.read_bytes() != (SOURCE / rel).read_bytes():
            errors.append(f"resource parity plugin/{rel}")
    if (output/"plugin/assets/bounded-compass.png").read_bytes() != (SOURCE/"assets/bounded-compass.png").read_bytes(): errors.append("resource parity plugin presentation asset")
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
