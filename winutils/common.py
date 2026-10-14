r"""Helpers shared by the utilities: Windows path handling and report wording.

Folders are read through the \\?\ extended-length prefix, so trees that are
already longer than MAX_PATH can still be scanned.
"""

from __future__ import annotations

import ntpath

_EXTENDED = "\\\\?\\"
_EXTENDED_UNC = "\\\\?\\UNC\\"


def strip_extended(path: str) -> str:
    r"""Remove a \\?\ or \\?\UNC\ prefix, if present."""
    if path.startswith(_EXTENDED_UNC):
        return "\\\\" + path[len(_EXTENDED_UNC):]
    if path.startswith(_EXTENDED):
        return path[len(_EXTENDED):]
    return path


def to_extended(path: str) -> str:
    """Add the extended-length prefix so Windows accepts paths over MAX_PATH."""
    if path.startswith(_EXTENDED):
        return path
    if path.startswith("\\\\"):
        return _EXTENDED_UNC + path[2:]
    return _EXTENDED + path


def normalize(path: str) -> str:
    """Return path as an absolute path without an extended-length prefix."""
    return ntpath.abspath(strip_extended(path))


def plural(count: int, singular: str, plural_form: str | None = None) -> str:
    word = singular if count == 1 else (plural_form or singular + "s")
    return f"{count:,} {word}"
