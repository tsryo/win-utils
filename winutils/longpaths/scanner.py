r"""Find files and folders whose paths are too long to copy between Windows disks.

Explorer and many older programs still refuse paths of MAX_PATH (260) characters
or more. A tree that is fine where it is can fail once it is copied under a deeper
destination folder, so paths can be checked both where they are now and where
they would end up.

Folders are read through the \\?\ extended-length prefix, so trees that are
already too long can still be scanned.
"""

from __future__ import annotations

import ntpath
import os
from dataclasses import dataclass, field

from ..common import is_link, normalize, strip_extended, to_extended  # noqa: F401 (re-exported)

MAX_PATH = 260  # includes the terminating null, so 259 usable characters
DIR_RESERVE = 12  # CreateDirectory keeps room for an 8.3 file name: MAX_PATH - 12
MAX_NAME = 255  # longest single file or folder name on NTFS


def win_len(text: str) -> int:
    """Return the length of text the way Windows counts it, in UTF-16 code units."""
    return len(text.encode("utf-16-le", "surrogatepass")) // 2


def path_limit(kind: str, max_path: int = MAX_PATH) -> int:
    """Return the path length at which an item of this kind fails."""
    return max_path if kind == "file" else max_path - DIR_RESERVE


@dataclass
class Finding:
    """A file or folder that breaks at least one limit."""

    kind: str  # "file" or "dir"
    relative_path: str
    source_path: str
    source_length: int
    target_path: str | None
    target_length: int | None
    limit: int  # path length that fails for this kind of item
    problems: list[str]  # any of "source", "target", "name"

    @property
    def length(self) -> int:
        """Longest of the checked paths."""
        return max(self.source_length, self.target_length or 0)

    @property
    def over_by(self) -> int:
        """Number of characters to remove to get under the limit."""
        return max(0, self.length - self.limit + 1)


@dataclass
class ScanResult:
    root: str
    target: str | None
    max_path: int
    findings: list[Finding] = field(default_factory=list)
    files_scanned: int = 0
    dirs_scanned: int = 0
    skipped_links: int = 0
    errors: list[tuple[str, str]] = field(default_factory=list)  # (path, message)


def check(
    kind: str,
    relative_path: str,
    source_path: str,
    target_path: str | None = None,
    max_path: int = MAX_PATH,
) -> Finding | None:
    """Return a Finding if the item breaks a limit, otherwise None."""
    limit = path_limit(kind, max_path)
    source_length = win_len(source_path)
    target_length = None if target_path is None else win_len(target_path)

    problems = []
    if source_length >= limit:
        problems.append("source")
    if target_length is not None and target_length >= limit:
        problems.append("target")
    if win_len(ntpath.basename(relative_path)) > MAX_NAME:
        problems.append("name")
    if not problems:
        return None
    return Finding(
        kind, relative_path, source_path, source_length, target_path, target_length, limit, problems
    )


def scan(root: str, target: str | None = None, max_path: int = MAX_PATH) -> ScanResult:
    """Walk root and check every file and folder below it.

    With target, each item is also checked at the path it would get if the
    contents of root were copied into target. Target does not need to exist.
    Symbolic links and junctions are counted but not followed.
    """
    root = normalize(root)
    target = None if target is None else normalize(target)
    result = ScanResult(root, target, max_path)

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
            source = ntpath.join(root, rel)
            try:
                if is_link(entry):
                    result.skipped_links += 1
                    continue
                is_dir = entry.is_dir(follow_symlinks=False)
            except OSError as exc:
                result.errors.append((source, exc.strerror or str(exc)))
                continue

            if is_dir:
                result.dirs_scanned += 1
                pending.append(rel)
            else:
                result.files_scanned += 1

            dest = None if target is None else ntpath.join(target, rel)
            finding = check("dir" if is_dir else "file", rel, source, dest, max_path)
            if finding:
                result.findings.append(finding)

    return result
