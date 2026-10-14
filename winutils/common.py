r"""Helpers shared by the utilities: Windows path handling and report wording.

Folders are read through the \\?\ extended-length prefix, so trees that are
already longer than MAX_PATH can still be scanned.
"""

from __future__ import annotations

import argparse
import ntpath
import os
import stat

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


def is_link(entry: os.DirEntry) -> bool:
    """Return True for a symbolic link or junction, which the scans count but don't follow.

    Junctions are recognised by their reparse tag rather than DirEntry.is_junction(),
    which needs Python 3.12. On Windows the tag comes with the folder listing, so
    this does not touch the disk.
    """
    if entry.is_symlink():
        return True
    tag = getattr(entry.stat(follow_symlinks=False), "st_reparse_tag", 0)
    return tag == stat.IO_REPARSE_TAG_MOUNT_POINT


def path_arg(text: str) -> str:
    r"""Undo how Windows splits a quoted path that ends in a backslash; for argparse type=.

    In "D:\" the backslash escapes the closing quote, so the program receives D:"
    and, if more arguments follow, the rest of the line as part of the same one.
    A quote can't be part of a Windows path, so a trailing one is turned back into
    the backslash it was typed as (not dropped: D: alone is the current folder on D:).
    """
    path, quote, rest = text.partition('"')
    if not quote:
        return text
    if not rest.strip():  # cmd can leave the spaces before the next command inside the argument
        return path + "\\"
    raise argparse.ArgumentTypeError(
        f"path contains a quote: {text}\n"
        'A quoted path that ends in a backslash, such as "D:\\", escapes its closing quote '
        "and swallows the rest of the line. Leave out the quotes (D:\\) or double the "
        'last backslash ("D:\\\\").'
    )


def plural(count: int, singular: str, plural_form: str | None = None) -> str:
    word = singular if count == 1 else (plural_form or singular + "s")
    return f"{count:,} {word}"
