"""Independent synthetic repositories for the real Init CLI journey tests."""

from dataclasses import dataclass
import hashlib
import os
from pathlib import Path


@dataclass(frozen=True)
class InitJourneyFixture:
    root: Path
    shared_paths: tuple[str, ...]
    private_routes: tuple[str, ...]
    unique_facts: dict[str, str]
    protected_paths: tuple[str, ...]


def build_large_init_fixture(root: Path, *, shared_file_count: int = 103) -> InitJourneyFixture:
    """Build a deterministic corpus whose oracle does not use production discovery."""
    if shared_file_count < 24:
        raise ValueError("large Init fixture requires at least 24 shared files")

    def write(relative: str, text: str) -> None:
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8", newline="\n")

    root_files = {
        "README.md": "# Fixture repository\n",
        "CHANGELOG.md": "# Changelog\n",
        "CONTRIBUTING.md": "# Contributing\n",
        "SECURITY.md": "# Security policy\n",
        "AGENTS.md": "# Agent instructions\n",
        ".github/ISSUE_TEMPLATE/bug.md": "---\nname: Bug\n---\n",
    }
    for path, text in root_files.items():
        write(path, text)

    fixed_shared = {
        "docs/README.md": "# Documentation map\n",
        "docs/tutorials/first-run.md": "# First run tutorial\n",
        "docs/how-to/local-setup.md": (
            "# Local setup\n\nUse the fixture command.\n"
        ),
        "docs/how-to/release.md": "# Release\n\nUse the fixture command.\n",
        "docs/reference/configuration.md": "# Configuration reference\n",
        "docs/reference/commands.md": "# Command reference\n",
        "docs/explanation/architecture.md": "# Architecture\n",
        "docs/explanation/design.md": "# Design\n",
        "docs/reviews/review-001.md": "# Historical review\n",
        "docs/archive/launch-plan.md": "# Archived launch plan\n",
        "docs/imported-tool/README.md": "# Imported tool\n",
        "docs/superpowers/plans/plan-001.md": "# Working plan\n",
    }
    for path, text in fixed_shared.items():
        write(path, text)

    shared = list(fixed_shared)
    filler_count = shared_file_count - len(shared) - 1
    for index in range(filler_count):
        path = f"docs/superpowers/specs/spec-{index:03d}.md"
        write(
            path,
            f"# Historical specification {index:03d}\n" + ("context\n" * 96),
        )
        shared.append(path)

    late_path = "docs/zz-late/unique-design-decision.md"
    late_fact = (
        "Product buttons remain purple unless the repository owner explicitly overrides them."
    )
    write(late_path, f"# Durable product decision\n{late_fact}\n")
    shared.append(late_path)

    private_routes = (".local/0.2.9-campaign", ".local/0.3.0-campaign")
    write(".gitignore", ".local/\n")
    write(f"{private_routes[0]}/PLAN.md", "PRIVATE_SENTINEL_029\n")
    write(f"{private_routes[1]}/KICKOFF.md", "PRIVATE_SENTINEL_030\n")

    return InitJourneyFixture(
        root=root,
        shared_paths=tuple(sorted(shared, key=lambda value: (value.casefold(), value))),
        private_routes=private_routes,
        unique_facts={late_path: late_fact},
        protected_paths=tuple(
            sorted(root_files, key=lambda value: (value.casefold(), value))
        ),
    )


def build_small_init_fixture(
    root: Path,
    *,
    shared_roots: tuple[str, ...] = ("docs",),
    files_per_root: int = 1,
    private_routes: tuple[str, ...] = (),
    file_bytes: int = 32,
) -> InitJourneyFixture:
    """Build a compact adversarial corpus with an explicit independent oracle."""
    if files_per_root < 0 or file_bytes < 1:
        raise ValueError("small Init fixture dimensions must be positive")

    shared_paths = []
    unique_facts = {}
    for shared_root in shared_roots:
        for index in range(files_per_root):
            name = "guide.md" if files_per_root == 1 else f"guide-{index:02d}.md"
            relative = f"{shared_root}/{name}"
            text = (f"# Shared guide {index}\n" + "x" * max(file_bytes - 18, 0) + "\n")
            target = root / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(text, encoding="utf-8", newline="\n")
            shared_paths.append(relative)
            if index == files_per_root - 1:
                unique_facts[relative] = f"Unique fact for {relative}."

    for index, route in enumerate(private_routes):
        target = root / route
        target.mkdir(parents=True, exist_ok=True)
        (target / "PLAN.md").write_text(
            f"PRIVATE_SENTINEL_{index:03d}\n",
            encoding="utf-8",
            newline="\n",
        )

    return InitJourneyFixture(
        root=root,
        shared_paths=tuple(sorted(shared_paths, key=lambda value: (value.casefold(), value))),
        private_routes=private_routes,
        unique_facts=unique_facts,
        protected_paths=(),
    )


def snapshot_repository(root: Path) -> tuple[tuple[str, str, str], ...]:
    """Return path/type/digest evidence for the zero-write assertion."""
    entries = []
    for path in sorted(root.rglob("*"), key=lambda value: value.relative_to(root).as_posix()):
        relative = path.relative_to(root).as_posix()
        if path.is_symlink():
            entries.append((relative, "symlink", os.readlink(path)))
        elif path.is_dir():
            entries.append((relative, "directory", ""))
        elif path.is_file():
            entries.append(
                (
                    relative,
                    "file",
                    hashlib.sha256(path.read_bytes()).hexdigest(),
                )
            )
        else:
            entries.append((relative, "other", ""))
    return tuple(entries)
