"""Data-only policy for maintained repository-root documentation evidence."""

from pathlib import Path


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
_MARKDOWN_SUFFIXES = frozenset({".md", ".markdown"})


def is_maintained_root_document(name):
    path = Path(name)
    return bool(
        path.suffix.casefold() in _MARKDOWN_SUFFIXES
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


__all__ = (
    "MAINTAINED_ROOT_DOCUMENT_NAMES",
    "is_maintained_root_document",
    "public_root_document_evidence",
    "root_document_evidence",
)
