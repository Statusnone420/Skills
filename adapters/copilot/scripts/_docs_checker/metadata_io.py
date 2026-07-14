"""Sanitized physical metadata operations for bounded discovery."""

import errno
import os


_EXPECTED_OS_ERROR_NUMBERS = frozenset(
    value
    for value in (
        errno.EACCES,
        errno.EBUSY,
        errno.EIO,
        errno.ELOOP,
        errno.ENOENT,
        errno.ENOTDIR,
        errno.EPERM,
        getattr(errno, "ESTALE", None),
    )
    if value is not None
)


def is_expected_environmental_error(error):
    return bool(
        isinstance(error, (PermissionError, FileNotFoundError, NotADirectoryError))
        or error.errno in _EXPECTED_OS_ERROR_NUMBERS
    )


def metadata_error(operation, relative, phase, depth, error=None):
    """Return stable evidence without exposing an environmental exception."""
    evidence = {
        "operation": operation,
        "path": relative,
        "phase": phase,
        "depth": depth,
        "blocks_completeness": True,
        "blocks_selection": True,
        "blocks_content_planning": True,
    }
    if isinstance(error, FileNotFoundError):
        evidence["_environmental_kind"] = "not-found"
    return evidence


def lstat(path, relative, phase, depth=None):
    try:
        return os.lstat(path), None
    except OSError as error:
        if not is_expected_environmental_error(error):
            raise
        return None, metadata_error("lstat", relative, phase, depth, error)


def entry_stat(entry, relative, phase, depth=None):
    try:
        return entry.stat(follow_symlinks=False), None
    except OSError as error:
        if not is_expected_environmental_error(error):
            raise
        return None, metadata_error("direntry-stat", relative, phase, depth, error)


def open_scandir(directory, relative, phase, depth=None):
    handle = None
    try:
        handle = os.scandir(directory)
        return handle, handle.__enter__(), None
    except OSError as error:
        if not is_expected_environmental_error(error):
            raise
        if handle is not None:
            handle.__exit__(None, None, None)
        return None, None, metadata_error("scandir", relative, phase, depth, error)


def next_scandir(iterator, relative, phase, depth=None):
    try:
        return next(iterator), False, None
    except StopIteration:
        return None, True, None
    except OSError as error:
        if not is_expected_environmental_error(error):
            raise
        return None, False, metadata_error("scandir", relative, phase, depth, error)


def close_scandir(handle):
    if handle is not None:
        handle.__exit__(None, None, None)


__all__ = (
    "close_scandir",
    "entry_stat",
    "is_expected_environmental_error",
    "lstat",
    "metadata_error",
    "next_scandir",
    "open_scandir",
)
