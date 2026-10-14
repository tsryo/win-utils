"""Find the most recently modified files below a folder.

Files can be filtered by name pattern and by modification time, listed newest
first or grouped by file type or by folder, and counted per owner.
"""

from __future__ import annotations

import fnmatch
import ntpath
import os
from collections import Counter
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field

from ..common import is_link, normalize, to_extended
from .owners import file_owner

GROUPINGS = ("file", "type", "dir")
NO_EXTENSION = "(no extension)"  # type group for files such as README
TOP_FOLDER = "."  # folder group for files directly in the scanned folder


@dataclass
class FileInfo:
    """A file that passed every filter."""

    relative_path: str
    path: str
    modified: float  # POSIX timestamp
    size: int
    owner: str | None  # None when owners are not looked up

    @property
    def extension(self) -> str:
        """Lowercased extension including the dot, or "" if there is none."""
        return ntpath.splitext(self.relative_path)[1].lower()

    @property
    def folder(self) -> str:
        """Containing folder relative to the scanned folder, "." for the folder itself."""
        return ntpath.dirname(self.relative_path) or TOP_FOLDER


@dataclass
class ScanResult:
    root: str
    include: list[str]
    exclude: list[str]
    since: float | None  # POSIX timestamp; files modified earlier are left out
    files: list[FileInfo] = field(default_factory=list)  # newest first
    files_scanned: int = 0
    dirs_scanned: int = 0
    skipped_links: int = 0
    errors: list[tuple[str, str]] = field(default_factory=list)  # (path, message)


def name_matches(name: str, patterns: Iterable[str]) -> bool:
    """Return True if name matches any of the wildcard patterns, ignoring case."""
    name = name.lower()
    return any(fnmatch.fnmatchcase(name, pattern.lower()) for pattern in patterns)


def scan(
    root: str,
    include: Iterable[str] = (),
    exclude: Iterable[str] = (),
    since: float | None = None,
    owner_of: Callable[[str], str] | None = file_owner,
) -> ScanResult:
    """Walk root and collect the files that pass every filter, newest first.

    A file passes if its name matches an include pattern (or none are given),
    matches no exclude pattern, and was modified at or after since. Folders whose
    name matches an exclude pattern are not entered. Symbolic links and junctions
    are counted but not followed. Owners are looked up with owner_of, for
    matching files only; pass None to skip the lookup.
    """
    root = normalize(root)
    result = ScanResult(root, list(include), list(exclude), since)

    pending = [""]  # folders still to read, relative to root
    while pending:
        rel_dir = pending.pop()
        dir_path = ntpath.join(root, rel_dir) if rel_dir else root
        try:
            with os.scandir(to_extended(dir_path)) as it:
                entries = list(it)
        except OSError as exc:
            result.errors.append((dir_path, exc.strerror or str(exc)))
            continue

        for entry in entries:
            rel = ntpath.join(rel_dir, entry.name) if rel_dir else entry.name
            path = ntpath.join(root, rel)
            try:
                if is_link(entry):
                    result.skipped_links += 1
                    continue
                if entry.is_dir(follow_symlinks=False):
                    if not name_matches(entry.name, result.exclude):
                        result.dirs_scanned += 1
                        pending.append(rel)
                    continue
                stat = entry.stat(follow_symlinks=False)
            except OSError as exc:
                result.errors.append((path, exc.strerror or str(exc)))
                continue

            result.files_scanned += 1
            if result.include and not name_matches(entry.name, result.include):
                continue
            if name_matches(entry.name, result.exclude):
                continue
            if since is not None and stat.st_mtime < since:
                continue
            owner = owner_of(path) if owner_of else None
            result.files.append(FileInfo(rel, path, stat.st_mtime, stat.st_size, owner))

    result.files.sort(key=lambda f: (-f.modified, f.relative_path.lower()))
    return result


def group_files(files: list[FileInfo], by: str) -> list[tuple[str | None, list[FileInfo]]]:
    """Split files, which must be newest first, into (key, files) groups, newest group first.

    by is "type" (key is the extension), "dir" (key is the containing folder) or
    "file" (a single group with key None). No files gives no groups.
    """
    if by == "file":
        return [(None, list(files))] if files else []
    groups: dict[str, list[FileInfo]] = {}
    for info in files:
        key = (info.extension or NO_EXTENSION) if by == "type" else info.folder
        groups.setdefault(key, []).append(info)
    return list(groups.items())  # each group was created by its newest file, in order


def owner_counts(files: Iterable[FileInfo]) -> list[tuple[str, int]]:
    """Return (owner, number of files) pairs, most files first, then by name."""
    counts = Counter(f.owner for f in files if f.owner is not None)
    return sorted(counts.items(), key=lambda item: (-item[1], item[0].lower()))
