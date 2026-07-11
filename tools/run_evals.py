"""Safe, deterministic local evaluation harness."""
from __future__ import annotations
import argparse, hashlib, json, os, re, shutil, subprocess, sys, time, uuid
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCHEMA = ROOT / "evals" / "evals.json"
WORKSPACE = ROOT / "evals" / "workspace"
TARGET_BYTES, TARGET_LINES = 290542, 2041

def load_scenarios():
    return json.loads(SCHEMA.read_text(encoding="utf-8"))

def build_fixture(destination: Path) -> Path:
    destination.mkdir(parents=True, exist_ok=True)
    path = destination / "legacy-state.md"
    lines = [f"synthetic legacy state entry {i:04d}: verified fixture content" for i in range(TARGET_LINES)]
    used = sum(len(line.encode("utf-8")) for line in lines) + TARGET_LINES
    lines[-1] += " " * (TARGET_BYTES - used)
    payload = ("\n".join(lines) + "\n").encode("utf-8")
    assert len(payload) == TARGET_BYTES and len(payload.splitlines()) == TARGET_LINES
    path.write_bytes(payload)
    return path

def is_confined(path: Path, root: Path) -> bool:
    try: path.resolve().relative_to(root.resolve()); return True
    except ValueError: return False

def prepare_attempt(root: Path, scenario_id: str) -> Path:
    root = root.resolve(); root.mkdir(parents=True, exist_ok=True)
    attempt = root / f"attempt-{uuid.uuid4().hex}"
    attempt.mkdir()
    subprocess.run(["git", "init", "--quiet"], cwd=attempt, shell=False, check=True, timeout=15)
    (attempt / "scenario.txt").write_text(scenario_id + "\n", encoding="utf-8")
    build_fixture(attempt / "fixtures")
    return attempt

SECRET = re.compile(r"(?:sk-[A-Za-z0-9_-]{16,}|(?:token|password|secret|api[_-]?key)\s*[=:]\s*[^\s]+)", re.I)
def redact(value: str, parent: str | Path | None = None) -> str:
    text = str(value)
    if parent: text = text.replace(str(Path(parent).resolve()), "<REPO_PARENT>")
    text = re.sub(r"(?<!\w)(?:[A-Za-z]:[\\/]|/)[^\s]+", "<PATH>", text)
    return SECRET.sub("<REDACTED>", text)

def run_command(command, cwd: Path, timeout: float):
    started = time.monotonic(); timed_out = False; error = None
    env = {k: v for k, v in os.environ.items() if not re.search(r"(TOKEN|PASSWORD|SECRET|API[_-]?KEY)", k, re.I)}
    try:
        proc = subprocess.run(command, cwd=cwd, env=env, shell=False, capture_output=True, text=True, timeout=timeout)
        status = proc.returncode; output = proc.stdout + proc.stderr
    except subprocess.TimeoutExpired as exc:
        timed_out, status, output = True, None, (exc.stdout or "") + (exc.stderr or "")
    except OSError as exc:
        status, output, error = None, "", str(exc)
    return {"exit_status": status, "final_output": redact(output), "duration_seconds": time.monotonic()-started,
            "timed_out": timed_out, "error": redact(error or "")}

def record_attempt(root: Path, attempt_id: str, prompt: str, result: dict) -> Path:
    path = root / f"{attempt_id}.json"
    if path.exists(): raise FileExistsError(path)
    data = {"attempt_id": attempt_id, "prompt": prompt, "recorded_at": datetime.now(timezone.utc).isoformat(),
            "harness": "statusnone-evals/0.1", "model": None, "usage": None, **result}
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path

def execute(scenario_id: str, *, dry_run=False, root=WORKSPACE):
    scenario = next(x for x in load_scenarios()["evals"] if x["id"] == scenario_id)
    command = [sys.executable, "-c", "print('dry-run model placeholder')"]
    if dry_run: return {"dry_run": True, "scenario_id": scenario_id, "command": command, "workspace": str(Path(root).resolve())}
    attempt = prepare_attempt(Path(root), scenario_id)
    result = run_command(command, attempt, 30)
    diff = subprocess.run(["git", "diff", "--no-ext-diff"], cwd=attempt, shell=False, capture_output=True, text=True, timeout=15)
    result["git_diff"] = redact(diff.stdout, Path(root).parent)
    result.update({"scenario_id": scenario_id, "attempt_workspace": str(attempt)})
    record_attempt(Path(root), attempt.name, scenario["prompt"], result)
    return result

def main(argv=None):
    p = argparse.ArgumentParser(); p.add_argument("action", choices=["list","prepare","run","summarize"]); p.add_argument("scenario", nargs="?"); p.add_argument("--dry-run", action="store_true")
    args = p.parse_args(argv)
    if args.action == "list": print(json.dumps(load_scenarios(), indent=2))
    elif args.action == "prepare": print(prepare_attempt(WORKSPACE, args.scenario))
    elif args.action == "run": print(json.dumps(execute(args.scenario, dry_run=args.dry_run), indent=2))
    else:
        records = list(WORKSPACE.glob("*.json")); print(json.dumps({"attempts": len(records)}, indent=2))
if __name__ == "__main__": main()
