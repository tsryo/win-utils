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
_JUNCTION_TAG = 0xA0000003  # IO_REPARSE_TAG_MOUNT_POINT; stat only names it from Python 3.8


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
    which needs Python 3.12. On Windows the attributes and tag come with the folder
    listing, so this only touches the disk on Python 3.7, and only for reparse points.
    """
    if entry.is_symlink():
        return True
    info = entry.stat(follow_symlinks=False)
    if not getattr(info, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT:
        return False
    tag = getattr(info, "st_reparse_tag", None)  # Python 3.8+
    if tag is None:
        tag = find_reparse_tag(entry.path)
    return tag == _JUNCTION_TAG


def find_reparse_tag(path: str) -> int:
    """Read the reparse tag of path with FindFirstFileW, for Python 3.7, whose stat has none."""
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    find_first = kernel32.FindFirstFileW
    find_first.argtypes = (wintypes.LPCWSTR, ctypes.POINTER(wintypes.WIN32_FIND_DATAW))
    find_first.restype = wintypes.HANDLE
    kernel32.FindClose.argtypes = (wintypes.HANDLE,)

    data = wintypes.WIN32_FIND_DATAW()
    handle = find_first(to_extended(path), ctypes.byref(data))
    if handle is None or handle == wintypes.HANDLE(-1).value:  # INVALID_HANDLE_VALUE
        raise ctypes.WinError(ctypes.get_last_error())
    kernel32.FindClose(handle)
    return data.dwReserved0  # holds the reparse tag when the item is a reparse point


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
