"""Observable, cross-platform orchestration for the stdlib unittest suite."""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TESTS = ROOT / "tests"
GROUP_ORDER = ("core", "lifecycle", "trajectory")
TRAJECTORY_MODULES = frozenset(
    {
        "test_pressure_provenance.py",
        "test_red_sanitization.py",
        "test_task6a_artifacts.py",
        "test_trajectory_gate.py",
    }
)
LIFECYCLE_MODULES = frozenset(
    {
        "test_repository_memory.py",
        "test_shared_corpus_visibility.py",
        "test_task_5_1.py",
        "test_task_7_lifecycle.py",
    }
)
LIFECYCLE_PREFIXES = ("test_doctor_", "test_init_")


def discover_test_files(tests: Path = TESTS) -> tuple[Path, ...]:
    return tuple(sorted(tests.glob("test_*.py"), key=lambda path: path.name.casefold()))


def classify_test_file(path: Path) -> str:
    name = path.name
    if name in TRAJECTORY_MODULES:
        return "trajectory"
    if name in LIFECYCLE_MODULES or name.startswith(LIFECYCLE_PREFIXES):
        return "lifecycle"
    return "core"


def grouped_test_files(tests: Path = TESTS) -> dict[str, tuple[Path, ...]]:
    groups: dict[str, list[Path]] = {name: [] for name in GROUP_ORDER}
    for path in discover_test_files(tests):
        groups[classify_test_file(path)].append(path)
    return {name: tuple(paths) for name, paths in groups.items()}


def verify_partition(groups: dict[str, tuple[Path, ...]], tests: Path = TESTS) -> None:
    discovered = discover_test_files(tests)
    assigned = [path for name in GROUP_ORDER for path in groups.get(name, ())]
    unknown_groups = sorted(set(groups) - set(GROUP_ORDER))
    if unknown_groups:
        raise ValueError(f"unknown test groups: {', '.join(unknown_groups)}")
    if len(assigned) != len(set(assigned)):
        raise ValueError("a test module belongs to more than one group")
    if set(assigned) != set(discovered):
        missing = sorted(path.name for path in set(discovered) - set(assigned))
        extra = sorted(path.name for path in set(assigned) - set(discovered))
        raise ValueError(f"test partition mismatch: missing={missing}, extra={extra}")


def module_names(paths: tuple[Path, ...]) -> tuple[str, ...]:
    return tuple(f"tests.{path.stem}" for path in paths)


def test_command(paths: tuple[Path, ...], *, failfast: bool = False) -> list[str]:
    command = [sys.executable, "-B", "-u", "-m", "unittest", "-v"]
    if failfast:
        command.append("--failfast")
    command.extend(module_names(paths))
    return command


def run_group(
    name: str,
    paths: tuple[Path, ...],
    *,
    heartbeat_seconds: float,
    failfast: bool,
) -> int:
    started = time.monotonic()
    print(f"[tests] START group={name} modules={len(paths)}", flush=True)
    env = dict(os.environ)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["PYTHONUNBUFFERED"] = "1"
    process = subprocess.Popen(test_command(paths, failfast=failfast), cwd=ROOT, env=env)
    next_heartbeat = started + heartbeat_seconds
    try:
        while process.poll() is None:
            now = time.monotonic()
            if now >= next_heartbeat:
                print(
                    f"[tests] RUNNING group={name} elapsed={now - started:.1f}s",
                    flush=True,
                )
                next_heartbeat = now + heartbeat_seconds
            time.sleep(min(1.0, heartbeat_seconds))
    except KeyboardInterrupt:
        print(f"[tests] INTERRUPT group={name}", flush=True)
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
        return 130
    duration = time.monotonic() - started
    status = "PASS" if process.returncode == 0 else "FAIL"
    print(
        f"[tests] {status} group={name} elapsed={duration:.1f}s exit={process.returncode}",
        flush=True,
    )
    return int(process.returncode or 0)


def list_groups(groups: dict[str, tuple[Path, ...]]) -> None:
    for name in GROUP_ORDER:
        print(f"{name} ({len(groups[name])} modules)")
        for path in groups[name]:
            print(f"  - {path.name}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("group", choices=(*GROUP_ORDER, "all", "list", "verify"))
    parser.add_argument(
        "--heartbeat-seconds",
        type=float,
        default=30.0,
        help="seconds between still-running messages (default: 30)",
    )
    parser.add_argument("--failfast", action="store_true")
    args = parser.parse_args(argv)
    if args.heartbeat_seconds <= 0:
        parser.error("--heartbeat-seconds must be greater than zero")

    groups = grouped_test_files()
    verify_partition(groups)
    if args.group == "list":
        list_groups(groups)
        return 0
    if args.group == "verify":
        print(
            f"[tests] partition clean: {sum(len(paths) for paths in groups.values())} modules "
            f"across {len(groups)} groups"
        )
        return 0

    selected = GROUP_ORDER if args.group == "all" else (args.group,)
    suite_started = time.monotonic()
    for name in selected:
        result = run_group(
            name,
            groups[name],
            heartbeat_seconds=args.heartbeat_seconds,
            failfast=args.failfast,
        )
        if result != 0:
            print(
                f"[tests] SUITE FAIL elapsed={time.monotonic() - suite_started:.1f}s",
                flush=True,
            )
            return result
    print(
        f"[tests] SUITE PASS groups={','.join(selected)} "
        f"elapsed={time.monotonic() - suite_started:.1f}s",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
