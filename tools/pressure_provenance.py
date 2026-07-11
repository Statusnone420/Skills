"""Offline, deterministic provenance checks for the Task 3 pressure ledger."""
from __future__ import annotations
import base64, hashlib, json, os, subprocess, tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FIXTURE_MANIFEST = ROOT / "evals" / "task3-fixtures.json"
SNAPSHOT_CATALOG = ROOT / "evals" / "task3-source-snapshots.json"

def _git(args, cwd):
    env = os.environ.copy()
    env.pop("GIT_DIR", None)
    env.pop("GIT_WORK_TREE", None)
    return subprocess.check_output(["git", *args], cwd=cwd, env=env, shell=False, text=True).strip()

def _materialize(spec, destination):
    for item in spec["files"]:
        data = base64.b64decode(item["content_b64"])
        # Deliberately split the synthetic value so scanners never see a key in source.
        data = data.replace(b"{SYNTHETIC_COMPONENTS}", b"sk-" + b"example-not-a-real-" + b"secret-1234567890")
        path = destination / item["path"]
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)

def verify_fixtures():
    manifest = json.loads(FIXTURE_MANIFEST.read_text(encoding="utf-8"))
    results = {}
    with tempfile.TemporaryDirectory(prefix="task3-fixtures-") as tmp:
        root = Path(tmp)
        for spec in manifest["fixtures"]:
            repo = root / spec["pair_id"]
            repo.mkdir()
            _materialize(spec, repo)
            legacy = repo / "fixtures" / "legacy-state.md"
            legacy.parent.mkdir(parents=True, exist_ok=True)
            lines = [f"synthetic legacy state entry {i:04d}: verified fixture content" for i in range(2041)]
            lines[-1] += " " * (290542 - sum(len(x.encode()) for x in lines) - 2041)
            legacy.write_bytes(("\n".join(lines) + "\n").encode())
            _git(["-c", "core.autocrlf=false", "init", "--quiet"], repo)
            _git(["-c", "core.autocrlf=false", "add", "-A"], repo)
            actual = _git(["-c", "core.autocrlf=false", "write-tree"], repo)
            if actual != spec["tree_oid"]:
                raise AssertionError(f"{spec['pair_id']}: expected {spec['tree_oid']}, got {actual}")
            results[spec["pair_id"]] = actual
    return results

def _snapshot_digest(files):
    h = hashlib.sha256()
    for item in sorted(files, key=lambda x: x["path"]):
        data = base64.b64decode(item["content_b64"])
        h.update(item["path"].encode() + b"\0" + data + b"\0")
    return h.hexdigest()

def verify_snapshots():
    catalog = json.loads(SNAPSHOT_CATALOG.read_text(encoding="utf-8"))
    for snap in catalog["snapshots"]:
        if _snapshot_digest(snap["files"]) != snap["catalog_digest"]:
            raise AssertionError(f"snapshot digest mismatch: {snap['id']}")
        for item in snap["files"]:
            data = base64.b64decode(item["content_b64"])
            if hashlib.sha256(data).hexdigest() != item["sha256"]:
                raise AssertionError(f"snapshot file digest mismatch: {snap['id']}:{item['path']}")
    return [s["id"] for s in catalog["snapshots"]]

def main():
    print(json.dumps({"fixtures": verify_fixtures(), "snapshots": verify_snapshots()}, sort_keys=True))

if __name__ == "__main__":
    main()
