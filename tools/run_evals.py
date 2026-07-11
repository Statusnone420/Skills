"""Safe standard-library evaluation harness."""
from __future__ import annotations
import argparse, json, os, re, subprocess, sys, time, uuid
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCHEMA = ROOT / "evals" / "evals.json"
WORKSPACE = ROOT / "evals" / "workspace"
TARGET_BYTES, TARGET_LINES = 290542, 2041
SECRET = re.compile(r"(?:sk-[A-Za-z0-9_-]{16,}|(?:token|password|secret|api[_-]?key)\s*[=:]\s*[^\s]+)", re.I)

def load_scenarios(): return json.loads(SCHEMA.read_text(encoding="utf-8"))

def build_fixture(destination: Path) -> Path:
    destination.mkdir(parents=True, exist_ok=True)
    path = destination / "legacy-state.md"
    lines = [f"synthetic legacy state entry {i:04d}: verified fixture content" for i in range(TARGET_LINES)]
    lines[-1] += " " * (TARGET_BYTES - sum(len(x.encode()) for x in lines) - TARGET_LINES)
    path.write_bytes(("\n".join(lines) + "\n").encode())
    return path

def is_confined(path: Path, root: Path) -> bool:
    try: path.resolve().relative_to(root.resolve()); return True
    except ValueError: return False

def _workspace(root: Path) -> Path:
    if root.exists() and root.is_symlink(): raise ValueError("workspace symlink is not allowed")
    resolved = root.resolve()
    if resolved != root.absolute() and not root.exists(): raise ValueError("workspace must not escape configured root")
    return resolved

def prepare_attempt(scenario_id: str) -> Path:
    root = _workspace(WORKSPACE); root.mkdir(parents=True, exist_ok=True)
    attempt = root / f"attempt-{uuid.uuid4().hex}"
    attempt.mkdir()
    if attempt.is_symlink() or not is_confined(attempt, root): raise ValueError("attempt escaped workspace")
    subprocess.run(["git", "init", "--quiet"], cwd=attempt, shell=False, check=True, timeout=15)
    (attempt / "scenario.txt").write_text(scenario_id + "\n", encoding="utf-8"); build_fixture(attempt / "fixtures")
    subprocess.run(["git", "add", "-A"], cwd=attempt, shell=False, check=True, timeout=15)
    subprocess.run(["git", "-c", "user.email=evals@example.invalid", "-c", "user.name=evals", "commit", "--quiet", "-m", "initial fixture"], cwd=attempt, shell=False, check=True, timeout=15)
    return attempt

def redact(value, parent=None):
    text = str(value)
    if parent: text = text.replace(str(Path(parent).resolve()), "<REPO_PARENT>")
    text = re.sub(r"(?<!\w)(?:[A-Za-z]:[\\/]|/)[^\s]+", "<PATH>", text)
    return SECRET.sub("<REDACTED>", text)

def _safe_command(command):
    if not isinstance(command, (list, tuple)) or not command or not all(isinstance(x, str) for x in command): raise ValueError("command must be argv array")
    result = list(command); result[0] = "<PYTHON>" if Path(result[0]).name.lower().startswith("python") else Path(result[0]).name
    return [redact(x) for x in result]

def run_command(command, cwd: Path, timeout: float):
    started = datetime.now(timezone.utc); timer = time.monotonic(); timed_out = False; error = ""
    env = {k:v for k,v in os.environ.items() if not re.search(r"(TOKEN|PASSWORD|SECRET|API[_-]?KEY)", k, re.I)}
    try:
        proc = subprocess.run(list(command), cwd=cwd, env=env, shell=False, capture_output=True, text=True, timeout=timeout)
        status, output = proc.returncode, proc.stdout + proc.stderr
    except subprocess.TimeoutExpired as exc:
        timed_out, status, output = True, None, (exc.stdout or "") + (exc.stderr or "")
    except OSError as exc:
        status, output, error = None, "", str(exc)
    return {"started_at": started.isoformat(), "finished_at": datetime.now(timezone.utc).isoformat(), "exit_status": status, "final_output": redact(output), "duration_seconds": time.monotonic()-timer, "timed_out": timed_out, "error": redact(error)}

def record_attempt(root, attempt_id, prompt, result):
    path = Path(root) / f"{attempt_id}.json"
    if path.exists(): raise FileExistsError(path)
    data = {"attempt_id": attempt_id, "prompt": redact(prompt), "recorded_at": datetime.now(timezone.utc).isoformat(), "harness": "statusnone-evals/0.1", "model": None, "usage": None, **result}
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8"); return path

def relative_attempt(path): return f"evals/workspace/{Path(path).name}"

def execute(scenario_id, *, dry_run=False, command=None, timeout=30):
    scenario = next((x for x in load_scenarios()["evals"] if x["id"] == scenario_id), None)
    if scenario is None: raise ValueError(f"unknown scenario: {scenario_id}")
    argv = list(command or [sys.executable, "-c", "print('model command not configured')"]); safe = _safe_command(argv)
    workspace = _workspace(WORKSPACE)
    if dry_run: return {"dry_run": True, "scenario_id": scenario_id, "command": safe, "workspace": "evals/workspace"}
    attempt = prepare_attempt(scenario_id); result = run_command(argv, attempt, timeout)
    subprocess.run(["git", "add", "-A"], cwd=attempt, shell=False, check=True, timeout=15)
    diff = subprocess.run(["git", "diff", "HEAD", "--no-ext-diff"], cwd=attempt, shell=False, capture_output=True, text=True, timeout=15)
    result.update({"scenario_id": scenario_id, "attempt_workspace": relative_attempt(attempt), "command": safe, "git_diff": redact(diff.stdout, workspace.parent), "git_status": redact(subprocess.run(["git", "status", "--short"], cwd=attempt, shell=False, capture_output=True, text=True, timeout=15).stdout, workspace.parent)})
    record_attempt(workspace, attempt.name, scenario["prompt"], result); return result

def main(argv=None):
    p=argparse.ArgumentParser(); p.add_argument("action", choices=["list","prepare","run","summarize"]); p.add_argument("scenario", nargs="?"); p.add_argument("--dry-run", action="store_true"); args=p.parse_args(argv)
    if args.action == "list": print(json.dumps(load_scenarios(), indent=2))
    elif args.action == "prepare":
        if not args.scenario: p.error("prepare requires scenario")
        print(relative_attempt(prepare_attempt(args.scenario)))
    elif args.action == "run":
        if not args.scenario: p.error("run requires scenario")
        print(json.dumps(execute(args.scenario, dry_run=args.dry_run), indent=2))
    else: print(json.dumps({"attempts": len(list(WORKSPACE.glob("*.json")))}, indent=2))
if __name__ == "__main__": main()
