"""Command line for recent: list the most recently modified files below a folder."""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
import sys
import time
from datetime import datetime, timedelta, timezone
from typing import TextIO

from ..common import normalize, plural, to_extended
from .owners import file_owner
from .scanner import GROUPINGS, FileInfo, ScanResult, group_files, owner_counts, scan

EXIT_OK = 0
EXIT_NONE = 1
EXIT_ERROR = 2

DEFAULT_TOP = 20
DEFAULT_TOP_PER_GROUP = 5
GROUP_OWNERS_SHOWN = 3  # owners named in each group header before "+N more"

FIELDS = ("group", "modified", "size", "owner", "relative_path", "path")
GROUP_NOUNS = {"type": ("extension", "extensions"), "dir": ("folder", "folders")}

Groups = list[tuple[str | None, list[FileInfo]]]

_DURATION = re.compile(r"(\d+)([smhdw])", re.IGNORECASE)
_UNIT_SECONDS = {"s": 1, "m": 60, "h": 3600, "d": 86400, "w": 7 * 86400}


def parse_since(text: str, now: float | None = None) -> float:
    """Turn an age such as 7d or 12h, or a local date such as 2026-10-01, into a POSIX timestamp."""
    text = text.strip()
    match = _DURATION.fullmatch(text)
    if match:
        now = time.time() if now is None else now
        return now - int(match[1]) * _UNIT_SECONDS[match[2].lower()]
    try:
        return datetime.fromisoformat(text).timestamp()
    except ValueError:
        raise argparse.ArgumentTypeError(
            f"expected an age such as 7d or 12h, or a date such as 2026-10-01: {text!r}"
        ) from None


def split_patterns(text: str) -> list[str]:
    return [pattern.strip() for pattern in text.split(",") if pattern.strip()]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="recent",
        description="List the most recently modified files below a folder, newest first "
        "or grouped by file type or folder, with a count of matching files per owner.",
        epilog="PATTERN is a wildcard such as *.csv, matched against file names "
        "regardless of case; give several by repeating the option or as a comma list. "
        "WHEN is an age (number plus s, m, h, d or w, such as 30m, 12h, 7d, 2w) or a "
        "local date or time (2026-10-01, 2026-10-01T08:00). Exit status: 0 files "
        "matched, 1 no file matched, 2 error.",
    )
    parser.add_argument("root", help="folder to scan")
    parser.add_argument(
        "-i",
        "--include",
        metavar="PATTERN",
        action="extend",
        type=split_patterns,
        default=[],
        help="only files whose name matches PATTERN",
    )
    parser.add_argument(
        "-x",
        "--exclude",
        metavar="PATTERN",
        action="extend",
        type=split_patterns,
        default=[],
        help="leave out files whose name matches PATTERN, and skip folders whose "
        "name matches it (for example .git)",
    )
    parser.add_argument(
        "-s", "--since", metavar="WHEN", type=parse_since, help="only files modified at or after WHEN"
    )
    parser.add_argument(
        "-b",
        "--by",
        choices=GROUPINGS,
        default="file",
        help="list files newest first (file), or group them by extension (type) or "
        "by containing folder (dir) (default: %(default)s)",
    )
    parser.add_argument(
        "-n",
        "--top",
        type=int,
        metavar="N",
        help=f"number of files to list, per group with --by type or dir; 0 lists all "
        f"(default: {DEFAULT_TOP}, or {DEFAULT_TOP_PER_GROUP} per group)",
    )
    parser.add_argument(
        "--no-owners",
        action="store_true",
        help="don't look up file owners (faster on large trees and network shares)",
    )
    parser.add_argument(
        "-f",
        "--format",
        choices=("table", "csv", "json"),
        default="table",
        help="report format (default: %(default)s)",
    )
    parser.add_argument(
        "-o", "--output", metavar="FILE", help="write the report to FILE (UTF-8) instead of the console"
    )
    parser.add_argument(
        "-q", "--quiet", action="store_true", help="count unreadable items instead of listing them"
    )
    return parser


def local_datetime(timestamp: float) -> datetime:
    """Return timestamp as an aware local time, or UTC if Windows can't convert it (before 1970)."""
    try:
        return datetime.fromtimestamp(timestamp).astimezone()
    except (OSError, OverflowError, ValueError):
        return datetime(1970, 1, 1, tzinfo=timezone.utc) + timedelta(seconds=timestamp)


def short_time(timestamp: float) -> str:
    return local_datetime(timestamp).strftime("%Y-%m-%d %H:%M")


def iso_time(timestamp: float) -> str:
    return local_datetime(timestamp).isoformat(timespec="seconds")


def human_size(size: int) -> str:
    """Return a byte count as a short string such as 512 B or 1.2 MB."""
    if size < 1024:
        return f"{size} B"
    value = size / 1024
    for unit in ("KB", "MB", "GB", "TB"):
        if value < 1024:
            break
        value /= 1024
    else:
        unit = "PB"
    return f"{value:.1f} {unit}"


def describe_filters(result: ScanResult) -> str:
    parts = []
    if result.include:
        parts.append("include " + ", ".join(result.include))
    if result.exclude:
        parts.append("exclude " + ", ".join(result.exclude))
    if result.since is not None:
        parts.append("modified since " + short_time(result.since))
    return "; ".join(parts)


def format_owners(counts: list[tuple[str, int]], most: int) -> str:
    text = ", ".join(f"{owner} {count:,}" for owner, count in counts[:most])
    if len(counts) > most:
        text += f", +{len(counts) - most} more"
    return text


def summary(result: ScanResult, groups: Groups, by: str, limit: int | None, quiet: bool) -> list[str]:
    matched = len(result.files)
    if not matched:
        lines = ["No files matched."]
    elif by == "file":
        shown = len(result.files[:limit])
        cut = f"; showing the newest {shown:,}" if shown < matched else ""
        lines = [f"Matched {plural(matched, 'file')}{cut}."]
    else:
        cut = any(len(files[:limit]) < len(files) for _, files in groups)
        lines = [
            f"Matched {plural(matched, 'file')} in {plural(len(groups), *GROUP_NOUNS[by])}"
            + (f"; showing the newest {limit:,} of each." if cut else ".")
        ]

    lines.append(
        f"Scanned {plural(result.files_scanned, 'file')} and {plural(result.dirs_scanned, 'folder')}."
    )
    if result.skipped_links:
        lines.append(
            "Skipped "
            + plural(result.skipped_links, "symbolic link or junction", "symbolic links or junctions")
            + " (not followed)."
        )
    if result.errors:
        lines.append(f"Could not read {plural(len(result.errors), 'item')}{'.' if quiet else ':'}")
        if not quiet:
            lines.extend(f"  {path}: {message}" for path, message in result.errors)

    counts = owner_counts(result.files)
    if counts:
        owner_width = max(len(owner) for owner, _ in counts)
        count_width = len(f"{counts[0][1]:,}")
        lines.append(f"Owners of the {plural(matched, 'matching file')}:")
        lines.extend(f"  {owner:<{owner_width}}  {count:>{count_width},}" for owner, count in counts)
    return lines


def file_table(files: list[FileInfo]) -> list[str]:
    with_owner = any(f.owner is not None for f in files)
    rows = [("MODIFIED", "SIZE", "OWNER", "PATH (relative to Folder)")]
    rows += [(short_time(f.modified), human_size(f.size), f.owner or "", f.relative_path) for f in files]
    size_width = max(len(row[1]) for row in rows)
    owner_width = max(len(row[2]) for row in rows)

    lines = []
    for modified, size, owner, path in rows:
        owner_cell = f"{owner:<{owner_width}}  " if with_owner else ""
        lines.append(f"{modified:<16}  {size:>{size_width}}  {owner_cell}{path}")
    return lines


def write_table(
    result: ScanResult, groups: Groups, by: str, limit: int | None, out: TextIO, quiet: bool
) -> None:
    lines = [f"Folder  {result.root}"]
    filters = describe_filters(result)
    if filters:
        lines.append(f"Filter  {filters}")
    lines.append("")

    for key, files in groups:
        if key is None:
            lines.extend(file_table(files[:limit]))
        else:
            header = f"{key}  {plural(len(files), 'file')}, newest {short_time(files[0].modified)}"
            counts = owner_counts(files)
            if counts:
                header += f"  ({format_owners(counts, GROUP_OWNERS_SHOWN)})"
            lines.append(header)
            lines.extend("  " + line for line in file_table(files[:limit]))
        lines.append("")

    lines.extend(summary(result, groups, by, limit, quiet))
    out.write("\n".join(lines) + "\n")


def write_csv(groups: Groups, limit: int | None, out: TextIO) -> None:
    writer = csv.writer(out, lineterminator="\n")
    writer.writerow(FIELDS)
    for key, files in groups:
        for info in files[:limit]:
            modified = local_datetime(info.modified).strftime("%Y-%m-%d %H:%M:%S")  # Excel reads this as a date
            writer.writerow((key or "", modified, info.size, info.owner or "", info.relative_path, info.path))


def file_dict(info: FileInfo) -> dict:
    return {
        "modified": iso_time(info.modified),
        "size": info.size,
        "owner": info.owner,
        "relative_path": info.relative_path,
        "path": info.path,
    }


def owner_list(files: list[FileInfo]) -> list[dict]:
    return [{"owner": owner, "files": count} for owner, count in owner_counts(files)]


def write_json(result: ScanResult, groups: Groups, by: str, limit: int | None, out: TextIO) -> None:
    report = {
        "root": result.root,
        "include": result.include,
        "exclude": result.exclude,
        "since": None if result.since is None else iso_time(result.since),
        "by": by,
        "files_scanned": result.files_scanned,
        "dirs_scanned": result.dirs_scanned,
        "skipped_links": result.skipped_links,
        "files_matched": len(result.files),
        "owners": owner_list(result.files),
        "groups": [
            {
                "key": key,
                "files_matched": len(files),
                "newest": iso_time(files[0].modified),
                "owners": owner_list(files),
                "files": [file_dict(f) for f in files[:limit]],
            }
            for key, files in groups
        ],
        "errors": [{"path": path, "message": message} for path, message in result.errors],
    }
    json.dump(report, out, indent=2)
    out.write("\n")


def write_report(
    fmt: str, result: ScanResult, groups: Groups, by: str, limit: int | None, out: TextIO, quiet: bool
) -> None:
    if fmt == "csv":
        write_csv(groups, limit, out)
    elif fmt == "json":
        write_json(result, groups, by, limit, out)
    else:
        write_table(result, groups, by, limit, out, quiet)


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.top is not None and args.top < 0:
        parser.error("--top must be 0 or more")
    top = args.top
    if top is None:
        top = DEFAULT_TOP if args.by == "file" else DEFAULT_TOP_PER_GROUP
    limit = top or None  # files[:None] keeps them all

    # Odd file names must not crash the report when the console encoding can't show them.
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace")

    root = normalize(args.root)
    if not os.path.isdir(to_extended(root)):
        print(f"recent: not a folder: {root}", file=sys.stderr)
        return EXIT_ERROR

    try:
        result = scan(root, args.include, args.exclude, args.since, None if args.no_owners else file_owner)
    except KeyboardInterrupt:
        print("recent: interrupted", file=sys.stderr)
        return 130
    groups = group_files(result.files, args.by)

    if args.output:
        encoding = "utf-8-sig" if args.format == "csv" else "utf-8"  # BOM lets Excel detect UTF-8
        try:
            with open(args.output, "w", encoding=encoding, errors="replace") as out:
                write_report(args.format, result, groups, args.by, limit, out, args.quiet)
        except OSError as exc:
            print(f"recent: cannot write {args.output}: {exc.strerror or exc}", file=sys.stderr)
            return EXIT_ERROR
        print(f"Report written to {normalize(args.output)}")
        print("\n".join(summary(result, groups, args.by, limit, args.quiet)))
    else:
        write_report(args.format, result, groups, args.by, limit, sys.stdout, args.quiet)
        if args.format != "table":
            print("\n".join(summary(result, groups, args.by, limit, args.quiet)), file=sys.stderr)

    return EXIT_OK if result.files else EXIT_NONE
