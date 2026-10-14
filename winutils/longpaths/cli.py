"""Command line for longpaths: report paths too long to copy between Windows disks."""

from __future__ import annotations

import argparse
import csv
import dataclasses
import json
import os
import sys
from typing import TextIO

from .scanner import (
    DIR_RESERVE,
    MAX_NAME,
    MAX_PATH,
    Finding,
    ScanResult,
    normalize,
    path_limit,
    scan,
    to_extended,
)

EXIT_OK = 0
EXIT_FOUND = 1
EXIT_ERROR = 2

FIELDS = (
    "kind",
    "length",
    "over_by",
    "limit",
    "problems",
    "relative_path",
    "source_length",
    "source_path",
    "target_length",
    "target_path",
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="longpaths",
        description="Find files and folders whose paths are too long to copy safely "
        "between Windows disks.",
        epilog="DEST is the folder that receives the contents of ROOT. To copy "
        "C:\\data\\Photos into E:\\Backup (ending up as E:\\Backup\\Photos), pass "
        "--target E:\\Backup\\Photos. Exit status: 0 nothing too long, 1 something "
        "too long, 2 error.",
    )
    parser.add_argument("root", help="folder to scan")
    parser.add_argument(
        "-t",
        "--target",
        metavar="DEST",
        help="also check each path as it would be after copying the contents of ROOT "
        "into DEST (DEST does not need to exist)",
    )
    parser.add_argument(
        "--max-path",
        type=int,
        default=MAX_PATH,
        metavar="N",
        help="path length that fails, counting the terminating null like Windows "
        f"MAX_PATH; folders fail at N-{DIR_RESERVE} (default: %(default)s)",
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


def plural(count: int, singular: str, plural_form: str | None = None) -> str:
    word = singular if count == 1 else (plural_form or singular + "s")
    return f"{count:,} {word}"


def summary(result: ScanResult, quiet: bool) -> list[str]:
    files = sum(f.kind == "file" for f in result.findings)
    dirs = len(result.findings) - files
    lines = [
        f"Scanned {plural(result.files_scanned, 'file')} and "
        f"{plural(result.dirs_scanned, 'folder')}."
    ]
    if result.findings:
        lines.append(f"Too long: {plural(files, 'file')} and {plural(dirs, 'folder')}.")
    else:
        lines.append("Nothing is too long.")
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
    return lines


def finding_dict(finding: Finding) -> dict:
    data = dataclasses.asdict(finding)
    data["length"] = finding.length
    data["over_by"] = finding.over_by
    return {name: data[name] for name in FIELDS}


def write_table(result: ScanResult, out: TextIO, quiet: bool) -> None:
    lines = [f"Source  {result.root}"]
    if result.target:
        lines.append(f"Target  {result.target}  (receives the contents of Source)")
    lines.append(
        f"Limits  files fail at {path_limit('file', result.max_path)} characters, "
        f"folders at {path_limit('dir', result.max_path)}, names over {MAX_NAME}"
    )
    lines.append("")

    if result.findings:
        whys = ["+".join(f.problems) for f in result.findings]
        why_width = max(len("WHY"), *map(len, whys))
        relative_to = "Source and Target" if result.target else "Source"
        lines.append(f"{'LEN':>5}  {'OVER':>5}  KIND  {'WHY':<{why_width}}  PATH (relative to {relative_to})")
        for finding, why in zip(result.findings, whys):
            lines.append(
                f"{finding.length:>5}  {finding.over_by:>5}  {finding.kind:<4}  "
                f"{why:<{why_width}}  {finding.relative_path}"
            )
        lines.append("")

    lines.extend(summary(result, quiet))
    out.write("\n".join(lines) + "\n")


def write_csv(result: ScanResult, out: TextIO) -> None:
    writer = csv.writer(out, lineterminator="\n")
    writer.writerow(FIELDS)
    for finding in result.findings:
        row = finding_dict(finding)
        row["problems"] = "+".join(row["problems"])
        writer.writerow("" if row[name] is None else row[name] for name in FIELDS)


def write_json(result: ScanResult, out: TextIO) -> None:
    report = {
        "root": result.root,
        "target": result.target,
        "max_path": result.max_path,
        "files_scanned": result.files_scanned,
        "dirs_scanned": result.dirs_scanned,
        "skipped_links": result.skipped_links,
        "findings": [finding_dict(f) for f in result.findings],
        "errors": [{"path": path, "message": message} for path, message in result.errors],
    }
    json.dump(report, out, indent=2)
    out.write("\n")


def write_report(fmt: str, result: ScanResult, out: TextIO, quiet: bool) -> None:
    if fmt == "csv":
        write_csv(result, out)
    elif fmt == "json":
        write_json(result, out)
    else:
        write_table(result, out, quiet)


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.max_path <= DIR_RESERVE:
        parser.error(f"--max-path must be greater than {DIR_RESERVE}")

    # Odd file names must not crash the report when the console encoding can't show them.
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace")

    root = normalize(args.root)
    if not os.path.isdir(to_extended(root)):
        print(f"longpaths: not a folder: {root}", file=sys.stderr)
        return EXIT_ERROR

    try:
        result = scan(root, args.target, args.max_path)
    except KeyboardInterrupt:
        print("longpaths: interrupted", file=sys.stderr)
        return 130
    result.findings.sort(key=lambda f: (-f.length, f.relative_path.lower()))

    if args.output:
        encoding = "utf-8-sig" if args.format == "csv" else "utf-8"  # BOM lets Excel detect UTF-8
        try:
            with open(args.output, "w", encoding=encoding, errors="replace") as out:
                write_report(args.format, result, out, args.quiet)
        except OSError as exc:
            print(f"longpaths: cannot write {args.output}: {exc.strerror or exc}", file=sys.stderr)
            return EXIT_ERROR
        print(f"Report written to {normalize(args.output)}")
        print("\n".join(summary(result, args.quiet)))
    else:
        write_report(args.format, result, sys.stdout, args.quiet)
        if args.format != "table":
            print("\n".join(summary(result, args.quiet)), file=sys.stderr)

    return EXIT_FOUND if result.findings else EXIT_OK
