"""Tests for winutils.longpaths."""

import csv
import io
import json
import ntpath
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout

from winutils.longpaths import cli
from winutils.longpaths.scanner import MAX_NAME, check, normalize, scan, to_extended, win_len

try:
    import _winapi
except ImportError:
    _winapi = None


def make_temp_dir(test: unittest.TestCase) -> str:
    path = normalize(tempfile.mkdtemp(prefix="longpaths-"))
    test.addCleanup(shutil.rmtree, to_extended(path))
    return path


class WinLenTests(unittest.TestCase):
    def test_counts_utf16_code_units(self):
        self.assertEqual(win_len("abc"), 3)
        self.assertEqual(win_len("café"), 4)
        self.assertEqual(win_len("\U0001F600"), 2)  # emoji is a surrogate pair


class CheckTests(unittest.TestCase):
    def test_file_fails_at_max_path(self):
        self.assertIsNone(check("file", "a.txt", "C:\\" + "a" * 256))  # 259 characters
        finding = check("file", "a.txt", "C:\\" + "a" * 257)  # 260 characters
        self.assertEqual(finding.problems, ["source"])
        self.assertEqual(finding.over_by, 1)

    def test_dir_fails_twelve_earlier(self):
        self.assertIsNone(check("dir", "d", "C:\\" + "d" * 244))  # 247 characters
        finding = check("dir", "d", "C:\\" + "d" * 245)  # 248 characters
        self.assertEqual(finding.limit, 248)
        self.assertEqual(finding.problems, ["source"])

    def test_long_name_fails_even_in_short_path(self):
        name = "n" * (MAX_NAME + 1)
        finding = check("file", name, "C:\\" + name)
        self.assertEqual(finding.problems, ["name"])

    def test_target_checked_separately(self):
        target = "Z:\\" + "t" * 251 + "\\a.txt"  # 260 characters
        finding = check("file", "a.txt", "C:\\src\\a.txt", target)
        self.assertEqual(finding.problems, ["target"])
        self.assertEqual(finding.length, 260)

    def test_custom_max_path(self):
        self.assertIsNotNone(check("file", "a.txt", "C:\\a.txt", max_path=8))
        self.assertIsNone(check("file", "a.txt", "C:\\a.txt", max_path=9))


class ScanTests(unittest.TestCase):
    def setUp(self):
        self.root = make_temp_dir(self)
        if len(self.root) > 200:
            self.skipTest("temp folder path is too long for these tests")

    def name_for_length(self, char: str, total: int) -> str:
        """Return a name that makes root\\name exactly total characters long."""
        return char * (total - len(self.root) - 1)

    def test_flags_items_at_and_over_the_limits(self):
        for total in (259, 260, 261):
            open(to_extended(ntpath.join(self.root, self.name_for_length("f", total))), "w").close()
        for total in (247, 248):
            os.mkdir(to_extended(ntpath.join(self.root, self.name_for_length("d", total))))

        result = scan(self.root)

        flagged = {(f.kind, len(f.source_path)) for f in result.findings}
        self.assertEqual(flagged, {("file", 260), ("file", 261), ("dir", 248)})
        self.assertEqual((result.files_scanned, result.dirs_scanned), (3, 2))

    def test_target_projection(self):
        for name in ("a.txt", "bb.txt"):
            open(ntpath.join(self.root, name), "w").close()
        target = "Z:\\" + "t" * 250  # a.txt lands at 259 characters, bb.txt at 260

        result = scan(self.root, target)

        self.assertEqual([f.relative_path for f in result.findings], ["bb.txt"])
        finding = result.findings[0]
        self.assertEqual(finding.problems, ["target"])
        self.assertEqual(finding.target_path, target + "\\bb.txt")

    def test_scans_tree_that_is_already_too_long(self):
        rel = ""
        while len(self.root) + len(rel) < 300:
            rel = ntpath.join(rel, "level-" + "x" * 40)
            os.mkdir(to_extended(ntpath.join(self.root, rel)))
        open(to_extended(ntpath.join(self.root, rel, "deep.txt")), "w").close()

        result = scan(self.root)

        self.assertEqual(result.files_scanned, 1)
        self.assertIn(ntpath.join(rel, "deep.txt"), [f.relative_path for f in result.findings])
        self.assertEqual(result.errors, [])

    @unittest.skipUnless(_winapi and hasattr(_winapi, "CreateJunction"), "needs Windows junctions")
    def test_does_not_follow_junctions(self):
        loop = ntpath.join(self.root, "loop")
        _winapi.CreateJunction(self.root, loop)  # points back at root
        self.addCleanup(os.rmdir, loop)
        open(ntpath.join(self.root, "a.txt"), "w").close()

        result = scan(self.root)

        self.assertEqual(result.skipped_links, 1)
        self.assertEqual((result.files_scanned, result.dirs_scanned), (1, 0))


class CliTests(unittest.TestCase):
    def setUp(self):
        self.root = make_temp_dir(self)
        open(ntpath.join(self.root, "a.txt"), "w").close()

    def run_cli(self, *argv: str) -> tuple[int, str, str]:
        stdout, stderr = io.StringIO(), io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            code = cli.main(list(argv))
        return code, stdout.getvalue(), stderr.getvalue()

    def test_exit_0_when_nothing_too_long(self):
        code, out, _ = self.run_cli(self.root)
        self.assertEqual(code, cli.EXIT_OK)
        self.assertIn("Nothing is too long.", out)

    def test_exit_1_and_table_when_too_long(self):
        code, out, _ = self.run_cli(self.root, "--target", "Z:\\" + "t" * 260)
        self.assertEqual(code, cli.EXIT_FOUND)
        self.assertIn("a.txt", out)
        self.assertIn("Too long: 1 file and 0 folders.", out)

    def test_exit_2_when_root_missing(self):
        code, _, err = self.run_cli(ntpath.join(self.root, "missing"))
        self.assertEqual(code, cli.EXIT_ERROR)
        self.assertIn("not a folder", err)

    def test_rejects_tiny_max_path(self):
        with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as ctx:
            cli.main([self.root, "--max-path", "5"])
        self.assertEqual(ctx.exception.code, 2)

    def test_json_report(self):
        code, out, err = self.run_cli(self.root, "--target", "Z:\\" + "t" * 260, "--format", "json")
        self.assertEqual(code, cli.EXIT_FOUND)
        report = json.loads(out)
        self.assertEqual(report["files_scanned"], 1)
        self.assertEqual(report["findings"][0]["relative_path"], "a.txt")
        self.assertEqual(report["findings"][0]["problems"], ["target"])
        self.assertIn("Scanned 1 file", err)  # summary kept off stdout

    def test_csv_report_to_file(self):
        report = ntpath.join(make_temp_dir(self), "report.csv")
        code, out, _ = self.run_cli(self.root, "--max-path", "13", "--format", "csv", "--output", report)
        self.assertEqual(code, cli.EXIT_FOUND)
        self.assertIn("Report written to", out)
        with open(report, encoding="utf-8-sig", newline="") as fh:
            rows = list(csv.DictReader(fh))
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["relative_path"], "a.txt")
        self.assertEqual(rows[0]["problems"], "source")
        self.assertEqual(rows[0]["target_path"], "")


class LauncherTests(unittest.TestCase):
    def test_runs_from_another_folder_without_install(self):
        root = make_temp_dir(self)
        launcher = ntpath.join(ntpath.dirname(ntpath.dirname(ntpath.abspath(__file__))), "longpaths.py")
        env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}

        proc = subprocess.run(
            [sys.executable, launcher, root], cwd=root, env=env, capture_output=True, text=True
        )

        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("Nothing is too long.", proc.stdout)


if __name__ == "__main__":
    unittest.main()
