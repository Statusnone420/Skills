"""Collect and summarize reproducible Codex retrieval campaigns."""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import statistics
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


DEFAULT_SESSION_ROOT = Path.home() / ".codex" / "sessions"
DEFAULT_CACHE_ROOT = (
    Path.home() / ".codex" / "plugins" / "cache" / "statusnone-skills" / "diataxis-docs"
)
DEFAULT_REPO_ROOT = Path(__file__).resolve().parents[1]
PLUGIN_RELATIVE = "plugins/diataxis-docs"
CACHE_RELATIVE_ROOT = "statusnone-skills/diataxis-docs"
DEFAULT_BOUND_SKILL = "diataxis-docs:docs-map"
DEFAULT_BOUND_SKILL_FILE = "skills/docs-map/SKILL.md"
PROVENANCE_KEY_FILES = (
    "skills/docs-map/SKILL.md",
    "skills/docs/SKILL.md",
    "skills/docs/scripts/check.py",
)
# Codex Desktop injects the invoked skill verbatim as one user message:
# <skill>\n<name>…</name>\n<path>…</path>\n{exact file text}\n</skill>
_SKILL_INJECTION = re.compile(
    r"<skill>\n<name>(?P<name>[^<\n]+)</name>\n<path>(?P<path>[^<\n]+)</path>\n"
    r"(?P<body>.*?)\n</skill>",
    re.DOTALL,
)
_ABSOLUTE_PATH_MARKER = re.compile(r"[A-Za-z]:[\\/]|(?<![\w.])/(?:home|Users)/")
_SHELL_COMMAND = re.compile(r"tools\.shell_command\s*\(")
_MEMORY_PATH = re.compile(r"(?:\.codex[\\/]+memories|[\\/]+memories[\\/])", re.IGNORECASE)


def _load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return value


def _condition_ids(campaign: dict[str, Any]) -> set[str]:
    conditions = campaign.get("conditions")
    if not isinstance(conditions, list) or not conditions:
        raise ValueError("campaign.conditions must be a non-empty array")
    ids = {item.get("id") for item in conditions if isinstance(item, dict)}
    if len(ids) != len(conditions) or not all(isinstance(value, str) and value for value in ids):
        raise ValueError("campaign condition IDs must be unique non-empty strings")
    return ids


def _find_session(root: Path, thread_id: str) -> Path:
    if not isinstance(thread_id, str) or not thread_id:
        raise ValueError("run.thread_id must be a non-empty string")
    roots = [root]
    archived = root.parent / "archived_sessions"
    if archived != root and archived.exists():
        roots.append(archived)
    matches = tuple(path for candidate in roots for path in candidate.rglob(f"*{thread_id}*.jsonl"))
    if len(matches) != 1:
        raise ValueError(f"expected one raw session for {thread_id}, found {len(matches)}")
    return matches[0]


def _events(path: Path) -> list[dict[str, Any]]:
    events = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        try:
            value = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"malformed JSONL at line {line_number}") from exc
        if not isinstance(value, dict):
            raise ValueError(f"session line {line_number} must be an object")
        events.append(value)
    return events


def _messages(events: Iterable[dict[str, Any]], role: str) -> list[dict[str, Any]]:
    return [
        event
        for event in events
        if event.get("type") == "response_item"
        and event.get("payload", {}).get("type") == "message"
        and event.get("payload", {}).get("role") == role
    ]


def _text(message: dict[str, Any]) -> str:
    return "\n".join(
        item.get("text", "")
        for item in message.get("payload", {}).get("content", [])
        if isinstance(item, dict) and isinstance(item.get("text"), str)
    )


def _last_usage(events: Iterable[dict[str, Any]]) -> tuple[dict[str, Any], str]:
    matches = [
        event
        for event in events
        if event.get("type") == "event_msg"
        and event.get("payload", {}).get("type") == "token_count"
        and isinstance(event.get("payload", {}).get("info", {}).get("total_token_usage"), dict)
    ]
    if not matches:
        raise ValueError("session has no host-reported cumulative usage")
    event = matches[-1]
    return event["payload"]["info"]["total_token_usage"], event["timestamp"]


def _first_cached_input(events: Iterable[dict[str, Any]]) -> int:
    for event in events:
        usage = event.get("payload", {}).get("info", {}).get("last_token_usage")
        if event.get("type") == "event_msg" and isinstance(usage, dict):
            return int(usage.get("cached_input_tokens", 0))
    raise ValueError("session has no host-reported per-turn usage")


def _tool_metrics(events: Iterable[dict[str, Any]]) -> tuple[int, int, int]:
    events = list(events)
    outputs = {
        event.get("payload", {}).get("call_id"): event.get("payload", {}).get("output")
        for event in events
        if event.get("type") == "response_item"
        and event.get("payload", {}).get("type") in {"function_call_output", "custom_tool_call_output"}
    }
    commands = memory_ops = memory_chars = 0
    for event in events:
        payload = event.get("payload", {})
        if event.get("type") != "response_item" or payload.get("type") not in {
            "function_call", "custom_tool_call",
        }:
            continue
        source = payload.get("input", payload.get("arguments", ""))
        source = source if isinstance(source, str) else json.dumps(source)
        starts = [match.start() for match in _SHELL_COMMAND.finditer(source)]
        if payload.get("name") == "shell_command" and not starts:
            starts = [0]
        chunks = [source[start:starts[index + 1] if index + 1 < len(starts) else None]
                  for index, start in enumerate(starts)]
        memory_indexes = [index for index, chunk in enumerate(chunks) if _MEMORY_PATH.search(chunk)]
        commands += len(chunks)
        memory_ops += len(memory_indexes)
        output = outputs.get(payload.get("call_id"))
        texts = [item.get("text", "") for item in output or [] if isinstance(item, dict)]
        returned = texts[-1] if texts else output if isinstance(output, str) else ""
        if chunks and len(texts) >= len(chunks) + 1:
            segments = texts[-len(chunks):]
        else:
            try:
                decoded = json.loads(returned)
                segments = list(decoded.values()) if isinstance(decoded, dict) else decoded
                segments = segments if isinstance(segments, list) else [segments]
            except (json.JSONDecodeError, TypeError):
                segments = [returned] if len(chunks) == 1 else []
        memory_chars += sum(len(str(segments[index])) for index in memory_indexes if index < len(segments))
    return commands, memory_ops, memory_chars


def _git(repo_root: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo_root), *args], shell=False, check=True,
        capture_output=True, text=True, timeout=30,
    ).stdout.strip()


def _tree_digest(root: Path) -> dict[str, Any]:
    if not root.is_dir():
        raise ValueError(f"tree digest root is not a directory: {root.name}")
    entries = []
    files = [path for path in root.rglob("*") if path.is_file()]
    for path in sorted(files, key=lambda item: item.relative_to(root).as_posix()):
        relative = path.relative_to(root).as_posix()
        entries.append(f"{relative}\x00{hashlib.sha256(path.read_bytes()).hexdigest()}")
    if not entries:
        raise ValueError(f"tree digest root contains no files: {root.name}")
    return {
        "files": len(entries),
        "sha256": hashlib.sha256("\n".join(entries).encode("utf-8")).hexdigest(),
    }


def _snapshot_root(cache_root: Path) -> Path:
    if not cache_root.is_dir():
        raise ValueError(
            "plugin cache snapshot is unavailable; install or refresh the plugin "
            "from the local marketplace before the preflight"
        )
    versions = sorted(path for path in cache_root.iterdir() if path.is_dir())
    if len(versions) != 1:
        raise ValueError(
            f"expected exactly one cached plugin snapshot version, found {len(versions)}"
        )
    return versions[0]


def _assert_sanitized(payload: dict[str, Any], label: str) -> None:
    text = json.dumps(payload)
    if _ABSOLUTE_PATH_MARKER.search(text):
        raise ValueError(f"{label} must not contain absolute or user-profile paths")


def _verify_candidate_environment(
    receipt: dict[str, Any], repo_root: Path, cache_root: Path
) -> None:
    head = _git(repo_root, "rev-parse", "HEAD")
    if head != receipt["candidate_commit"]:
        raise ValueError(
            f"repository HEAD {head} does not match receipt candidate commit"
        )
    dirty = _git(repo_root, "status", "--porcelain=v1", "--", "plugins", "skills")
    if dirty:
        raise ValueError("plugins/skills working tree is dirty; candidate bytes are ambiguous")
    candidate = _tree_digest(repo_root / PLUGIN_RELATIVE)
    if candidate != receipt["package_tree"]:
        raise ValueError("candidate plugin bytes drifted from the provenance receipt")
    snapshot = _snapshot_root(cache_root)
    if snapshot.name != receipt["snapshot_version"]:
        raise ValueError(
            f"cached snapshot version {snapshot.name} does not match the receipt"
        )
    if _tree_digest(snapshot) != receipt["package_tree"]:
        raise ValueError("cached plugin snapshot drifted from the provenance receipt")


def build_provenance(
    campaign_path: Path,
    output_path: Path,
    *,
    expected_commit: str,
    conditions: list[str],
    repo_root: Path = DEFAULT_REPO_ROOT,
    cache_root: Path = DEFAULT_CACHE_ROOT,
    skill_name: str = DEFAULT_BOUND_SKILL,
    skill_file: str = DEFAULT_BOUND_SKILL_FILE,
) -> dict[str, Any]:
    campaign = _load(campaign_path)
    condition_ids = _condition_ids(campaign)
    bound = sorted(set(conditions))
    if not bound or not set(bound) <= condition_ids:
        raise ValueError("bound conditions must be a non-empty subset of campaign conditions")
    if skill_file not in PROVENANCE_KEY_FILES:
        raise ValueError("bound skill file must be one of the provenance key files")
    head = _git(repo_root, "rev-parse", "HEAD")
    if head != expected_commit:
        raise ValueError(f"repository HEAD {head} does not match expected candidate commit")
    dirty = _git(repo_root, "status", "--porcelain=v1", "--", "plugins", "skills")
    if dirty:
        raise ValueError("plugins/skills working tree is dirty; commit or restore before preflight")
    plugin_root = repo_root / PLUGIN_RELATIVE
    package_tree = _tree_digest(plugin_root)
    snapshot = _snapshot_root(cache_root)
    cached_tree = _tree_digest(snapshot)
    if cached_tree != package_tree:
        raise ValueError(
            "cached plugin snapshot does not match candidate plugin bytes; refresh the "
            "installed plugin from the local marketplace, then rerun this preflight"
        )
    marketplace = json.loads(
        (repo_root / ".agents" / "plugins" / "marketplace.json").read_text(encoding="utf-8")
    )
    entries = [
        entry for entry in marketplace.get("plugins", [])
        if isinstance(entry, dict) and entry.get("name") == "diataxis-docs"
    ]
    if len(entries) != 1 or entries[0].get("source") != {
        "source": "local", "path": f"./{PLUGIN_RELATIVE}",
    }:
        raise ValueError("marketplace must route diataxis-docs to the local candidate package")
    key_files = {
        relative: hashlib.sha256((plugin_root / relative).read_bytes()).hexdigest()
        for relative in PROVENANCE_KEY_FILES
    }
    receipt = {
        "schema_version": 1,
        "campaign_id": campaign["campaign_id"],
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "candidate_commit": head,
        "bound_conditions": bound,
        "bound_skill": skill_name,
        "bound_skill_file": skill_file,
        "marketplace_source": f"./{PLUGIN_RELATIVE}",
        "snapshot_version": snapshot.name,
        "cache_relative_prefix": f"{CACHE_RELATIVE_ROOT}/{snapshot.name}/",
        "package_tree": package_tree,
        "key_files": key_files,
    }
    _assert_sanitized(receipt, "provenance receipt")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return receipt


def _bind_injected_skill(
    events: Iterable[dict[str, Any]], receipt: dict[str, Any], started: datetime
) -> dict[str, str]:
    generated = datetime.fromisoformat(receipt["generated_at"])
    if started <= generated:
        raise ValueError("session predates the provenance receipt; not a candidate run")
    bodies = []
    paths = []
    for message in _messages(events, "user"):
        for match in _SKILL_INJECTION.finditer(_text(message)):
            if match.group("name") == receipt["bound_skill"]:
                bodies.append(match.group("body"))
                paths.append(match.group("path"))
    if not bodies:
        raise ValueError("session has no injected skill message for the bound skill")
    if len(set(bodies)) != 1:
        raise ValueError("session injected conflicting bytes for the bound skill")
    body_sha256 = hashlib.sha256(bodies[0].encode("utf-8")).hexdigest()
    expected = receipt["key_files"][receipt["bound_skill_file"]]
    if body_sha256 != expected:
        raise ValueError(
            "injected skill bytes do not match the candidate; the task loaded a "
            "different plugin snapshot"
        )
    expected_tail = receipt["cache_relative_prefix"] + receipt["bound_skill_file"]
    for path in paths:
        if not path.replace("\\", "/").endswith(expected_tail):
            raise ValueError("injected skill was not served from the pinned cache snapshot")
    return {
        "injected_skill_sha256": body_sha256,
        "injected_skill_source": expected_tail,
    }


def _bind_cli_skill(
    events: Iterable[dict[str, Any]], campaign: dict[str, Any], receipt: dict[str, Any],
    started: datetime,
) -> dict[str, str]:
    generated = datetime.fromisoformat(receipt["generated_at"])
    if started <= generated:
        raise ValueError("session predates the provenance receipt; not a candidate run")
    conditions = [item for item in campaign["conditions"] if item["id"] in receipt["bound_conditions"]]
    if len(conditions) != 1 or conditions[0].get("skill") != receipt["bound_skill"]:
        raise ValueError("CLI candidate condition must name the provenance-bound skill")
    expected_request = f"${receipt['bound_skill']}\n{conditions[0]['prompt']}"
    requests = [_text(message).replace("\r\n", "\n") for message in _messages(events, "user")]
    if expected_request not in requests:
        raise ValueError("CLI session does not contain the exact qualified skill request")
    expected_tail = receipt["cache_relative_prefix"] + receipt["bound_skill_file"]
    return {
        "requested_skill_sha256": receipt["key_files"][receipt["bound_skill_file"]],
        "requested_skill_source": expected_tail,
        "skill_binding_evidence": "qualified-cli-request-plus-verified-cache",
    }


def collect_session(
    path: Path,
    campaign: dict[str, Any],
    receipt: dict[str, Any] | None = None,
) -> dict[str, Any]:
    events = _events(path)
    contexts = [event for event in events if event.get("type") == "turn_context"]
    if len(contexts) != 1:
        raise ValueError(f"expected one fresh turn, found {len(contexts)}")
    context = contexts[0]
    execution = campaign["execution"]
    payload = context.get("payload", {})
    if payload.get("model") != execution["model"] or payload.get("effort") != execution["reasoning_effort"]:
        raise ValueError("session model or reasoning effort does not match campaign")

    metas = [event for event in events if event.get("type") == "session_meta"]
    if not metas:
        raise ValueError("session metadata is missing")
    meta = metas[0].get("payload", {})
    commit = meta.get("git", {}).get("commit_hash")
    if not commit:
        cwd = Path(meta.get("cwd", ""))
        if not cwd.exists():
            raise ValueError("session repository is unavailable for commit verification")

        import subprocess

        commit = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=cwd, shell=False, check=True,
            capture_output=True, text=True, timeout=15,
        ).stdout.strip()
    if commit != campaign["target"]["commit"]:
        raise ValueError(f"session target commit {commit} does not match campaign")

    usage, finished_at = _last_usage(events)
    started_at = context["timestamp"]
    started = datetime.fromisoformat(started_at.replace("Z", "+00:00"))
    finished = datetime.fromisoformat(finished_at.replace("Z", "+00:00"))
    assistant = _messages(events, "assistant")
    final_output = _text(assistant[-1]) if assistant else ""
    if not final_output:
        raise ValueError("session has no visible assistant output")
    tool_calls = sum(
        event.get("type") == "response_item"
        and event.get("payload", {}).get("type") in {"function_call", "custom_tool_call"}
        for event in events
    )
    shell_commands, memory_read_ops, memory_read_output_chars = _tool_metrics(events)
    raw = path.read_bytes()
    output = final_output.encode("utf-8")
    input_tokens = int(usage["input_tokens"])
    cached_tokens = int(usage.get("cached_input_tokens", 0))
    output_tokens = int(usage["output_tokens"])
    reasoning_tokens = int(usage.get("reasoning_output_tokens", 0))
    provenance_fields = {}
    if receipt is not None:
        provenance_fields = (
            _bind_cli_skill(events, campaign, receipt, started)
            if campaign.get("host_context", {}).get("host") == "Codex CLI"
            else _bind_injected_skill(events, receipt, started)
        )
    return {
        **provenance_fields,
        "raw_session_sha256": hashlib.sha256(raw).hexdigest(),
        "final_output_sha256": hashlib.sha256(output).hexdigest(),
        "duration_seconds": round((finished - started).total_seconds(), 3),
        "tool_call_wrappers": tool_calls,
        "shell_commands": shell_commands,
        "memory_read_ops": memory_read_ops,
        "memory_read_output_chars": memory_read_output_chars,
        "first_turn_cached_input_tokens": _first_cached_input(events),
        "input_tokens": input_tokens,
        "cached_input_tokens": cached_tokens,
        "uncached_input_tokens": input_tokens - cached_tokens,
        "reasoning_tokens": reasoning_tokens,
        "nonreasoning_output_tokens": output_tokens - reasoning_tokens,
        "total_tokens": int(usage["total_tokens"]),
    }


def collect(
    campaign_path: Path,
    manifest_path: Path,
    output_path: Path,
    session_root: Path,
    provenance_path: Path | None = None,
    repo_root: Path = DEFAULT_REPO_ROOT,
    cache_root: Path = DEFAULT_CACHE_ROOT,
) -> dict[str, Any]:
    campaign = _load(campaign_path)
    manifest = _load(manifest_path)
    condition_ids = _condition_ids(campaign)
    receipt = None
    if provenance_path is not None:
        receipt = _load(provenance_path)
        if receipt.get("campaign_id") != campaign["campaign_id"]:
            raise ValueError("provenance receipt belongs to a different campaign")
        if not set(receipt.get("bound_conditions", [])) <= condition_ids:
            raise ValueError("provenance receipt binds unknown conditions")
        _verify_candidate_environment(receipt, repo_root, cache_root)
    runs = manifest.get("runs")
    expected = len(condition_ids) * int(campaign["execution"]["repetitions_per_condition"])
    if not isinstance(runs, list) or len(runs) != expected:
        raise ValueError(f"private manifest must contain exactly {expected} runs")
    paired = campaign.get("paired_execution")
    if paired:
        if condition_ids != {"july11-bounded-recipe", "docs-map-candidate"}:
            raise ValueError("paired campaign must contain the frozen reference and candidate conditions")
        candidate = next(item for item in campaign["conditions"] if item["id"] == "docs-map-candidate")
        if candidate.get("skill") != DEFAULT_BOUND_SKILL:
            raise ValueError("paired candidate must use the fully qualified focused skill")
        if campaign.get("host_context", {}).get("memory") != "unavailable":
            raise ValueError("paired campaign must declare memory unavailable")
        if manifest.get("host_context") != campaign["host_context"]:
            raise ValueError("paired manifest host context must match the campaign")
        pairs = int(paired.get("pairs", 0))
        if pairs != int(campaign["execution"]["repetitions_per_condition"]):
            raise ValueError("paired campaign pairs must equal repetitions per condition")
        for pair in range(1, pairs + 1):
            members = [run for run in runs if isinstance(run, dict) and run.get("pair") == pair]
            if ({run.get("condition") for run in members} != condition_ids
                    or {run.get("pair_order") for run in members} != {1, 2}
                    or {run.get("repetition") for run in members} != {pair}):
                raise ValueError("paired runs require both conditions and recorded pair order")

    seen: set[tuple[str, int]] = set()
    public_runs = []
    for run in runs:
        if not isinstance(run, dict) or run.get("condition") not in condition_ids:
            raise ValueError("run condition is invalid")
        repetition = run.get("repetition")
        key = (run["condition"], repetition)
        if not isinstance(repetition, int) or repetition < 1 or key in seen:
            raise ValueError("run repetitions must be unique positive integers per condition")
        seen.add(key)
        bound = receipt is not None and run["condition"] in receipt["bound_conditions"]
        metrics = collect_session(
            _find_session(session_root, run.get("thread_id")),
            campaign,
            receipt if bound else None,
        )
        if paired and metrics["memory_read_ops"] > 0:
            raise ValueError(
                f"memory isolation failed for pair {run.get('pair')}; rerun the entire pair"
            )
        public_runs.append({
            "run_id": run.get("run_id"),
            "condition": run["condition"],
            "repetition": repetition,
            **({"pair": run.get("pair"), "pair_order": run.get("pair_order")} if paired else {}),
            "assertions": run.get("assertions", {}),
            **metrics,
        })

    result = {
        "schema_version": 1,
        "campaign_id": campaign["campaign_id"],
        "collected_at": datetime.now(timezone.utc).isoformat(),
        "target": campaign["target"],
        "execution": campaign["execution"],
        "validity": manifest.get("validity", "unspecified"),
        "host_context": manifest.get("host_context", {}),
        "limitations": manifest.get("limitations", []),
        "decision_rule": campaign["decision_rule"],
        "runs": sorted(public_runs, key=lambda item: (item["condition"], item["repetition"])),
    }
    if receipt is not None:
        candidate_provenance = {
            **{key: value for key, value in receipt.items() if key != "schema_version"},
            "verified_at": datetime.now(timezone.utc).isoformat(),
            "drift": "none",
        }
        _assert_sanitized(candidate_provenance, "candidate provenance")
        result["candidate_provenance"] = candidate_provenance
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return result


def summarize(result: dict[str, Any]) -> dict[str, Any]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for run in result.get("runs", []):
        grouped.setdefault(run["condition"], []).append(run)
    metric_names = (
        "duration_seconds", "tool_call_wrappers", "uncached_input_tokens",
        "reasoning_tokens", "nonreasoning_output_tokens", "total_tokens",
    )
    medians = {
        condition: {
            metric: round(statistics.median(run[metric] for run in runs), 3)
            for metric in metric_names
        }
        for condition, runs in sorted(grouped.items())
    }
    reference = medians.get("july11-bounded-recipe")
    candidate = medians.get("docs-map-candidate") or medians.get("docs-map-0.1.6")
    comparison = None
    if reference and candidate:
        comparison = {
            metric: round((candidate[metric] / reference[metric] - 1) * 100, 1)
            for metric in ("duration_seconds", "tool_call_wrappers", "uncached_input_tokens", "total_tokens")
        }
    comparability = {}
    for condition, runs in sorted(grouped.items()):
        prefixes: dict[str, int] = {}
        for run in runs:
            if "first_turn_cached_input_tokens" in run:
                key = str(run["first_turn_cached_input_tokens"])
                prefixes[key] = prefixes.get(key, 0) + 1
        comparability[condition] = {
            "runs_with_memory_reads": sum(run.get("memory_read_ops", 0) > 0 for run in runs),
            "first_turn_cached_input_tokens": dict(sorted(prefixes.items(), key=lambda item: int(item[0]))),
        }
    paired_differences = []
    for pair in sorted({run.get("pair") for run in result.get("runs", []) if run.get("pair")}):
        members = {run["condition"]: run for run in result["runs"] if run.get("pair") == pair}
        if reference_run := members.get("july11-bounded-recipe"):
            if candidate_run := members.get("docs-map-candidate"):
                delta = candidate_run["uncached_input_tokens"] - reference_run["uncached_input_tokens"]
                paired_differences.append({
                    "pair": pair,
                    "uncached_input_tokens_delta": delta,
                    "uncached_input_tokens_percent": round(
                        delta / reference_run["uncached_input_tokens"] * 100, 1
                    ),
                })
    return {
        "medians": medians,
        "docs_map_0_1_6_vs_july11_percent": comparison,
        "comparability": comparability,
        "paired_uncached_input_differences": paired_differences,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="action", required=True)
    collect_parser = subparsers.add_parser("collect")
    collect_parser.add_argument("campaign", type=Path)
    collect_parser.add_argument("manifest", type=Path)
    collect_parser.add_argument("output", type=Path)
    collect_parser.add_argument("--session-root", type=Path, default=DEFAULT_SESSION_ROOT)
    collect_parser.add_argument("--provenance", type=Path, default=None)
    collect_parser.add_argument("--repo-root", type=Path, default=DEFAULT_REPO_ROOT)
    collect_parser.add_argument("--cache-root", type=Path, default=DEFAULT_CACHE_ROOT)
    provenance_parser = subparsers.add_parser("provenance")
    provenance_parser.add_argument("campaign", type=Path)
    provenance_parser.add_argument("output", type=Path)
    provenance_parser.add_argument("--expected-commit", required=True)
    provenance_parser.add_argument("--conditions", nargs="+", required=True)
    provenance_parser.add_argument("--repo-root", type=Path, default=DEFAULT_REPO_ROOT)
    provenance_parser.add_argument("--cache-root", type=Path, default=DEFAULT_CACHE_ROOT)
    provenance_parser.add_argument("--skill", default=DEFAULT_BOUND_SKILL)
    provenance_parser.add_argument("--skill-file", default=DEFAULT_BOUND_SKILL_FILE)
    summarize_parser = subparsers.add_parser("summarize")
    summarize_parser.add_argument("result", type=Path)
    args = parser.parse_args(argv)
    if args.action == "collect":
        value = collect(
            args.campaign, args.manifest, args.output, args.session_root,
            provenance_path=args.provenance,
            repo_root=args.repo_root, cache_root=args.cache_root,
        )
        print(json.dumps({
            "runs": len(value["runs"]),
            "candidate_bytes": "VERIFIED" if args.provenance else "UNVERIFIED",
            "output": str(args.output),
        }, indent=2))
    elif args.action == "provenance":
        receipt = build_provenance(
            args.campaign, args.output,
            expected_commit=args.expected_commit, conditions=args.conditions,
            repo_root=args.repo_root, cache_root=args.cache_root,
            skill_name=args.skill, skill_file=args.skill_file,
        )
        print(json.dumps({
            "candidate_commit": receipt["candidate_commit"],
            "snapshot_version": receipt["snapshot_version"],
            "package_tree_sha256": receipt["package_tree"]["sha256"],
            "bound_skill_sha256": receipt["key_files"][receipt["bound_skill_file"]],
            "output": str(args.output),
        }, indent=2))
    else:
        print(json.dumps(summarize(_load(args.result)), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
