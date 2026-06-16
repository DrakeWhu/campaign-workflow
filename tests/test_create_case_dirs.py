from __future__ import annotations

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

from campaign_workflow.cli.create_case_dirs import main as create_case_dirs_main


class CreateCaseDirsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmpdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tmpdir.name) / "fake_campaign"
        self.root.mkdir(parents=True)

    def tearDown(self) -> None:
        self.tmpdir.cleanup()

    def test_dry_run_does_not_create_case_directories(self) -> None:
        self._write_campaign()
        self._write_cases("CASE_ID\tCASE_NAME\n0\t000_case\n")

        rc, stdout, stderr = self._run_cli("--dry-run", "--verbose")

        self.assertEqual(rc, 0, stderr)
        self.assertIn("WOULD: create case directory", stdout)
        self.assertFalse((self.root / "000_case").exists())

    def test_write_mode_creates_case_directories_by_case_name(self) -> None:
        self._write_campaign()
        self._write_cases("CASE_ID\tCASE_NAME\n0\t000_case\n1\t001_case\n")

        rc, _stdout, stderr = self._run_cli("--verbose")

        self.assertEqual(rc, 0, stderr)
        self.assertTrue((self.root / "000_case").is_dir())
        self.assertTrue((self.root / "001_case").is_dir())

    def test_write_mode_creates_standard_subdirectories(self) -> None:
        self._write_campaign()
        self._write_cases("CASE_ID\tCASE_NAME\n0\t000_case\n")

        rc, _stdout, stderr = self._run_cli("--verbose")

        self.assertEqual(rc, 0, stderr)
        for subdir in ["logs", "post", "manifests", "locks", "diags", "checkpoints"]:
            self.assertTrue((self.root / "000_case" / subdir).is_dir(), subdir)

    def test_write_mode_is_idempotent(self) -> None:
        self._write_campaign()
        self._write_cases("CASE_ID\tCASE_NAME\n0\t000_case\n")

        first_rc, _first_stdout, first_stderr = self._run_cli("--verbose")
        second_rc, second_stdout, second_stderr = self._run_cli("--verbose")

        self.assertEqual(first_rc, 0, first_stderr)
        self.assertEqual(second_rc, 0, second_stderr)
        self.assertIn("case_dirs_existing=1", second_stdout)
        self.assertIn("subdirs_created=written=0", second_stdout)

    def test_rejects_case_name_with_parent_reference(self) -> None:
        self._write_campaign()
        self._write_cases("CASE_ID\tCASE_NAME\n0\t../escape\n")

        rc, _stdout, stderr = self._run_cli("--verbose")

        self.assertEqual(rc, 1)
        self.assertIn("must not contain '..'", stderr)
        self.assertFalse((self.root.parent / "escape").exists())

    def test_rejects_absolute_case_name(self) -> None:
        self._write_campaign()
        absolute_case_name = str(Path(self.root.anchor) / "outside_case")
        self._write_cases(f"CASE_ID\tCASE_NAME\n0\t{absolute_case_name}\n")

        rc, _stdout, stderr = self._run_cli("--verbose")

        self.assertEqual(rc, 1)
        self.assertIn("must be relative", stderr)

    def test_rejects_duplicate_case_names(self) -> None:
        self._write_campaign()
        self._write_cases("CASE_ID\tCASE_NAME\n0\tdup_case\n1\tdup_case\n")

        rc, _stdout, stderr = self._run_cli("--verbose")

        self.assertEqual(rc, 1)
        self.assertIn("Duplicate case name", stderr)

    def test_rejects_empty_case_name(self) -> None:
        self._write_campaign()
        self._write_cases("CASE_ID\tCASE_NAME\n0\t\n")

        rc, _stdout, stderr = self._run_cli("--verbose")

        self.assertEqual(rc, 1)
        self.assertIn("Empty case name", stderr)

    def test_rejects_cases_manifest_without_rows(self) -> None:
        self._write_campaign()
        self._write_cases("CASE_ID\tCASE_NAME\n")

        rc, _stdout, stderr = self._run_cli("--verbose")

        self.assertEqual(rc, 1)
        self.assertIn("case manifest contains no cases", stderr)

    def test_does_not_delete_existing_files_inside_case_directory(self) -> None:
        self._write_campaign()
        self._write_cases("CASE_ID\tCASE_NAME\n0\t000_case\n")
        case_dir = self.root / "000_case"
        case_dir.mkdir()
        keep_path = case_dir / "keep.txt"
        keep_path.write_text("keep me\n", encoding="utf-8")

        rc, _stdout, stderr = self._run_cli("--verbose")

        self.assertEqual(rc, 0, stderr)
        self.assertTrue(keep_path.exists())
        self.assertEqual(keep_path.read_text(encoding="utf-8"), "keep me\n")

    def test_new_code_does_not_contain_shell_recursive_delete(self) -> None:
        repo_root = Path(__file__).resolve().parents[1]
        for relative_path in [
            "campaign_workflow/core/case_dirs.py",
            "campaign_workflow/cli/create_case_dirs.py",
        ]:
            text = (repo_root / relative_path).read_text(encoding="utf-8")
            self.assertNotIn("rm -rf", text)
            self.assertNotIn("shutil.rmtree", text)

    def test_summary_reports_zero_destructive_operations(self) -> None:
        self._write_campaign()
        self._write_cases("CASE_ID\tCASE_NAME\n0\t000_case\n")

        rc, stdout, stderr = self._run_cli("--verbose")

        self.assertEqual(rc, 0, stderr)
        self.assertIn("destructive_operations=0", stdout)

    def _write_campaign(self) -> None:
        campaign = {
            "schema_version": 1,
            "campaign_name": "fake_campaign",
            "case_manifest": "cases.tsv",
            "case_manifest_format": "tsv",
            "case_id_column": "CASE_ID",
            "case_name_column": "CASE_NAME",
            "state": {
                "state_file": "state.json",
                "validation_file": "validation.json",
                "locks_dir": "locks",
                "manifests_dir": "manifests",
                "post_dir": "post",
                "logs_dir": "logs",
            },
        }
        (self.root / "campaign.json").write_text(
            json.dumps(campaign, indent=2) + "\n",
            encoding="utf-8",
        )

    def _write_cases(self, text: str) -> None:
        (self.root / "cases.tsv").write_text(text, encoding="utf-8")

    def _run_cli(self, *extra_args: str) -> tuple[int, str, str]:
        stdout = io.StringIO()
        stderr = io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            rc = create_case_dirs_main(["--campaign-root", str(self.root), *extra_args])
        return rc, stdout.getvalue(), stderr.getvalue()


if __name__ == "__main__":
    unittest.main()