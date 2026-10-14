"""Tests for winutils.recent."""

import argparse
import csv
import io
import json
import ntpath
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime

from winutils.common import normalize, to_extended
from winutils.recent import cli
from winutils.recent.owners import UNKNOWN, file_owner
from winutils.recent.scanner import NO_EXTENSION, group_files, name_matches, owner_counts, scan

try:
    import _winapi
except ImportError:
    _winapi = None

DAY = 86400


def make_temp_dir(test: unittest.TestCase) -> str:
    path = normalize(tempfile.mkdtemp(prefix="recent-"))
    test.addCleanup(shutil.rmtree, to_extended(path))
    return path


def make_file(root: str, rel: str, age_days: float, size: int = 0) -> str:
    """Create root\\rel with size bytes, last modified age_days ago."""
    path = ntpath.join(root, rel)
    os.makedirs(to_extended(ntpath.dirname(path)), exist_ok=True)
    with open(to_extended(path), "wb") as fh:
        fh.write(b"x" * size)
    mtime = time.time() - age_days * DAY
    os.utime(to_extended(path), (mtime, mtime))
    return path


def fake_owner(path: str) -> str:
    return "zed" if path.endswith(".csv") else "bob"


class ParseSinceTests(unittest.TestCase):
    def test_ages(self):
        now = 1_000_000_000.0
        self.assertEqual(cli.parse_since("45s", now), now - 45)
        self.assertEqual(cli.parse_since("30m", now), now - 30 * 60)
        self.assertEqual(cli.parse_since("12h", now), now - 12 * 3600)
        self.assertEqual(cli.parse_since("7d", now), now - 7 * DAY)
        self.assertEqual(cli.parse_since("7D", now), now - 7 * DAY)
        self.assertEqual(cli.parse_since("2w", now), now - 14 * DAY)

    def test_local_dates(self):
        self.assertEqual(cli.parse_since("2026-10-01"), datetime(2026, 10, 1).timestamp())
        self.assertEqual(cli.parse_since("2026-10-01T08:30"), datetime(2026, 10, 1, 8, 30).timestamp())

    def test_rejects_other_text(self):
        for text in ("7x", "abc", "", "-3d", "7 days"):
            with self.subTest(text=text), self.assertRaises(argparse.ArgumentTypeError):
                cli.parse_since(text)


class NameMatchTests(unittest.TestCase):
    def test_ignores_case(self):
        self.assertTrue(name_matches("Report.CSV", ["*.csv"]))
        self.assertTrue(name_matches("report.csv", ["*.CSV"]))

    def test_any_pattern(self):
        self.assertTrue(name_matches("a.xlsx", ["*.csv", "*.xlsx"]))
        self.assertFalse(name_matches("a.txt", ["*.csv", "*.xlsx"]))
        self.assertFalse(name_matches("a.txt", []))


class ScanTests(unittest.TestCase):
    def setUp(self):
        self.root = make_temp_dir(self)
        make_file(self.root, ".git\\HEAD", 0)
        make_file(self.root, "new.csv", 1)
        make_file(self.root, "notes.TXT", 2)
        make_file(self.root, "README", 3)
        make_file(self.root, "sub\\data.csv", 4)
        make_file(self.root, "old.csv", 10)

    def paths(self, **kwargs) -> list[str]:
        return [f.relative_path for f in scan(self.root, owner_of=None, **kwargs).files]

    def test_newest_first(self):
        result = scan(self.root, owner_of=None)
        self.assertEqual(
            [f.relative_path for f in result.files],
            [".git\\HEAD", "new.csv", "notes.TXT", "README", "sub\\data.csv", "old.csv"],
        )
        self.assertEqual((result.files_scanned, result.dirs_scanned), (6, 2))
        self.assertTrue(all(f.owner is None for f in result.files))

    def test_include_ignores_case_and_exclude_wins(self):
        self.assertEqual(
            self.paths(include=["*.CSV", "*.txt"], exclude=["old.*"]),
            ["new.csv", "notes.TXT", "sub\\data.csv"],
        )

    def test_excluded_folder_is_not_entered(self):
        result = scan(self.root, exclude=[".git"], owner_of=None)
        self.assertNotIn(".git\\HEAD", [f.relative_path for f in result.files])
        self.assertEqual((result.files_scanned, result.dirs_scanned), (5, 1))

    def test_since_keeps_files_modified_at_or_after_cutoff(self):
        self.assertEqual(
            self.paths(since=time.time() - 3.5 * DAY),
            [".git\\HEAD", "new.csv", "notes.TXT", "README"],
        )

    def test_owners_looked_up_for_matching_files_only(self):
        looked_up = []
        result = scan(self.root, include=["*.csv"], owner_of=lambda path: looked_up.append(path) or "me")
        self.assertEqual(sorted(looked_up), sorted(f.path for f in result.files))
        self.assertEqual(len(looked_up), 3)

    def test_group_by_type(self):
        groups = group_files(scan(self.root, owner_of=None).files, "type")
        self.assertEqual([key for key, _ in groups], [NO_EXTENSION, ".csv", ".txt"])
        self.assertEqual([f.relative_path for f in groups[0][1]], [".git\\HEAD", "README"])
        self.assertEqual([f.relative_path for f in groups[1][1]], ["new.csv", "sub\\data.csv", "old.csv"])

    def test_group_by_dir(self):
        groups = group_files(scan(self.root, owner_of=None).files, "dir")
        self.assertEqual([key for key, _ in groups], [".git", ".", "sub"])
        self.assertEqual(len(groups[1][1]), 4)

    def test_no_files_gives_no_groups(self):
        for by in ("file", "type", "dir"):
            self.assertEqual(group_files([], by), [])

    def test_owner_counts_most_files_first_then_name(self):
        files = scan(self.root, owner_of=fake_owner).files
        self.assertEqual(owner_counts(files), [("bob", 3), ("zed", 3)])
        files = scan(self.root, exclude=[".git"], owner_of=fake_owner).files
        self.assertEqual(owner_counts(files), [("zed", 3), ("bob", 2)])

    @unittest.skipUnless(_winapi and hasattr(_winapi, "CreateJunction"), "needs Windows junctions")
    def test_does_not_follow_junctions(self):
        loop = ntpath.join(self.root, "loop")
        _winapi.CreateJunction(self.root, loop)  # points back at root
        self.addCleanup(os.rmdir, loop)

        result = scan(self.root, owner_of=None)

        self.assertEqual(result.skipped_links, 1)
        self.assertEqual(len(result.files), 6)


@unittest.skipUnless(sys.platform == "win32", "Windows owner lookup")
class FileOwnerTests(unittest.TestCase):
    def setUp(self):
        self.root = make_temp_dir(self)

    def test_resolves_account_name(self):
        owner = file_owner(make_file(self.root, "a.txt", 0))
        self.assertIn("\\", owner)  # DOMAIN\name, not "(unknown)" or a bare SID
        self.assertFalse(owner.startswith("S-1-"))

    def test_path_longer_than_max_path(self):
        rel = "\\".join(["level-" + "x" * 40] * 7) + "\\deep.txt"
        path = make_file(self.root, rel, 0)
        self.assertGreater(len(path), 260)
        self.assertIn("\\", file_owner(path))

    def test_missing_file_is_unknown(self):
        self.assertEqual(file_owner(ntpath.join(self.root, "missing.txt")), UNKNOWN)


class CliTests(unittest.TestCase):
    def setUp(self):
        self.root = make_temp_dir(self)
        make_file(self.root, "a.csv", 1, size=2048)
        make_file(self.root, "b.csv", 2)
        make_file(self.root, "sub\\c.txt", 10)

    def run_cli(self, *argv: str) -> tuple[int, str, str]:
        stdout, stderr = io.StringIO(), io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            code = cli.main(list(argv))
        return code, stdout.getvalue(), stderr.getvalue()

    def test_table_newest_first_with_owners(self):
        code, out, _ = self.run_cli(self.root)
        self.assertEqual(code, cli.EXIT_OK)
        self.assertLess(out.index("a.csv"), out.index("b.csv"))
        self.assertLess(out.index("b.csv"), out.index("sub\\c.txt"))
        self.assertIn("2.0 KB", out)
        self.assertIn("Matched 3 files.", out)
        self.assertIn("Owners of the 3 matching files:", out)

    def test_exit_1_when_nothing_matches(self):
        code, out, _ = self.run_cli(self.root, "--since", "5d", "--include", "*.txt")
        self.assertEqual(code, cli.EXIT_NONE)
        self.assertIn("No files matched.", out)

    def test_exit_2_when_root_missing(self):
        code, _, err = self.run_cli(ntpath.join(self.root, "missing"))
        self.assertEqual(code, cli.EXIT_ERROR)
        self.assertIn("not a folder", err)

    def test_rejects_bad_since_and_negative_top(self):
        for argv in ([self.root, "--since", "soon"], [self.root, "--top", "-1"]):
            with self.subTest(argv=argv), redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as ctx:
                cli.main(argv)
            self.assertEqual(ctx.exception.code, 2)

    def test_comma_separated_patterns(self):
        code, out, _ = self.run_cli(self.root, "-i", "a.*, *.txt", "--no-owners")
        self.assertEqual(code, cli.EXIT_OK)
        self.assertIn("Matched 2 files.", out)
        self.assertNotIn("b.csv", out)

    def test_no_owners(self):
        _, out, _ = self.run_cli(self.root, "--no-owners")
        self.assertNotIn("OWNER", out)
        self.assertNotIn("Owners of", out)

    def test_top_limits_listing_but_not_owner_counts(self):
        code, out, err = self.run_cli(self.root, "--top", "1", "--format", "json")
        self.assertEqual(code, cli.EXIT_OK)
        report = json.loads(out)
        self.assertEqual(report["files_matched"], 3)
        self.assertEqual([f["relative_path"] for f in report["groups"][0]["files"]], ["a.csv"])
        self.assertEqual(sum(o["files"] for o in report["owners"]), 3)
        self.assertIn("showing the newest 1", err)  # summary kept off stdout

    def test_top_0_lists_all(self):
        _, out, _ = self.run_cli(self.root, "--by", "type", "--top", "0", "--format", "json")
        report = json.loads(out)
        self.assertEqual([len(g["files"]) for g in report["groups"]], [2, 1])

    def test_table_grouped_by_dir(self):
        code, out, _ = self.run_cli(self.root, "--by", "dir", "--top", "1")
        self.assertEqual(code, cli.EXIT_OK)
        self.assertIn(".  2 files, newest", out)
        self.assertIn("sub  1 file, newest", out)
        self.assertNotIn("b.csv", out)  # only the newest file of each group
        self.assertIn("Matched 3 files in 2 folders; showing the newest 1 of each.", out)

    def test_json_grouped_by_type(self):
        _, out, _ = self.run_cli(self.root, "--by", "type", "--format", "json", "--no-owners")
        report = json.loads(out)
        self.assertEqual([g["key"] for g in report["groups"]], [".csv", ".txt"])
        self.assertEqual(report["groups"][0]["files_matched"], 2)
        self.assertEqual(report["groups"][0]["files"][0]["size"], 2048)
        self.assertIsNone(report["groups"][0]["files"][0]["owner"])
        self.assertEqual(report["owners"], [])

    def test_csv_report_to_file(self):
        report = ntpath.join(make_temp_dir(self), "report.csv")
        code, out, _ = self.run_cli(self.root, "--by", "dir", "--format", "csv", "--output", report)
        self.assertEqual(code, cli.EXIT_OK)
        self.assertIn("Report written to", out)
        with open(report, encoding="utf-8-sig", newline="") as fh:
            rows = list(csv.DictReader(fh))
        self.assertEqual([row["group"] for row in rows], [".", ".", "sub"])
        self.assertEqual([row["relative_path"] for row in rows], ["a.csv", "b.csv", "sub\\c.txt"])
        self.assertEqual(rows[0]["size"], "2048")
        self.assertTrue(rows[0]["owner"])


class LauncherTests(unittest.TestCase):
    def test_runs_from_another_folder_without_install(self):
        root = make_temp_dir(self)
        make_file(root, "a.txt", 0)
        launcher = ntpath.join(ntpath.dirname(ntpath.dirname(ntpath.abspath(__file__))), "recent.py")
        env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}

        proc = subprocess.run(
            [sys.executable, launcher, root], cwd=root, env=env, capture_output=True, text=True
        )

        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("Matched 1 file.", proc.stdout)


if __name__ == "__main__":
    unittest.main()
