"""Canonical policy for documentation text formats and navigation manifests."""

from pathlib import Path


DOCUMENT_SUFFIXES = frozenset({".md", ".markdown", ".mdx"})
NAVIGATION_MANIFEST_NAMES = frozenset({"docs.json"})


def is_document_path(value):
    """Return whether a path is supported as inert documentation text."""
    return Path(value).suffix.casefold() in DOCUMENT_SUFFIXES


def is_component_document_path(value):
    """Return whether a document uses the inert component-capable format."""
    return Path(value).suffix.casefold() == ".mdx"


def is_readme_document(value):
    path = Path(value)
    return path.stem.casefold() == "readme" and is_document_path(path)


def is_navigation_manifest_path(value):
    return Path(value).name.casefold() in NAVIGATION_MANIFEST_NAMES


__all__ = (
    "DOCUMENT_SUFFIXES",
    "NAVIGATION_MANIFEST_NAMES",
    "is_component_document_path",
    "is_document_path",
    "is_navigation_manifest_path",
    "is_readme_document",
)
