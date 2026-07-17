"""Data-only policy for maintained repository-root documentation evidence."""

from collections.abc import Iterable
from pathlib import Path

from .formats import is_document_path
from .paths import normalize_repo_relative


MAINTAINED_ROOT_DOCUMENT_NAMES = (
    "README",
    "CONTRIBUTING",
    "CHANGELOG",
    "SECURITY",
    "SUPPORT",
    "ROADMAP",
    "PLAN",
    "PRODUCT",
    "DESIGN",
    "ARCHITECTURE",
    "EVALUATION",
    "STATE",
)
_MAINTAINED_KEYS = frozenset(name.casefold() for name in MAINTAINED_ROOT_DOCUMENT_NAMES)
def is_maintained_root_document(name):
    path = Path(name)
    return bool(
        is_document_path(path)
        and path.stem.casefold() in _MAINTAINED_KEYS
    )


def root_document_evidence(relative, info):
    """Return internal metadata evidence; only path/bytes are published."""
    return {
        "path": relative,
        "bytes": info.st_size,
        "modified_ns": info.st_mtime_ns,
        "mode": info.st_mode,
        "device": info.st_dev,
        "inode": info.st_ino,
    }


def public_root_document_evidence(evidence, *, complete):
    paths = [
        {"path": item["path"], "bytes": item["bytes"]}
        for item in sorted(evidence, key=lambda item: (item["path"].casefold(), item["path"]))
    ]
    return {
        "paths": paths,
        "path_count": len(paths),
        "bytes": sum(item["bytes"] for item in paths),
        "complete": bool(complete),
    }


def repository_host(surface_paths: Iterable[str]) -> str:
    """Infer the repository host from the bounded observed surface paths."""
    normalized = {
        normalize_repo_relative(path, "surface path")
        for path in surface_paths
    }
    return (
        "github"
        if any(path == ".github" or path.startswith(".github/") for path in normalized)
        else "unknown"
    )


__all__ = (
    "MAINTAINED_ROOT_DOCUMENT_NAMES",
    "is_maintained_root_document",
    "public_root_document_evidence",
    "repository_host",
    "root_document_evidence",
)
