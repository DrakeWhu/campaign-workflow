from __future__ import annotations

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

from campaign_workflow.cli.materialize_cases import main as materialize_cases_main


class MaterializeCasesTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmpdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tmpdir.name) / "fake_campaign"
        self.root.mkdir(parents=True)
        self._write_campaign()
        self._write_template("#!/usr/bin/env python3\nprint('template')\n")

    def tearDown(self) -> None:
        self.tmpdir.cleanup()

    def test_creates_input_py(self) -> None:
        self._write_cases(
            "CASE_ID\tCASE_NAME\tLASER_CASE\tPLASMA_KIND\tN0_CM3\tPLATEAU_LENGTH_MM\tRADIUS_UM\tFOCUS_OFFSET_FROM_PLATEAU_START_MM\tCAP_RMAX_UM\tCAP_NR\n"
            "0\t000_case\tf20\tchan\t7e+17\t5\t75\t-5\t90\t192\n"
        )

        rc, _stdout, stderr = self._run_cli("--verbose")

        self.assertEqual(rc, 0, stderr)
        input_path = self.root / "000_case" / "input.py"
        self.assertTrue(input_path.is_file())
        self.assertEqual(input_path.read_text(encoding="utf-8"), "#!/usr/bin/env python3\nprint('template')\n")

    def test_creates_case_env(self) -> None:
        self._write_cases(
            "CASE_ID\tCASE_NAME\tLASER_CASE\tPLASMA_KIND\tN0_CM3\tPLATEAU_LENGTH_MM\tRADIUS_UM\tFOCUS_OFFSET_FROM_PLATEAU_START_MM\tCAP_RMAX_UM\tCAP_NR\n"
            "0\t000_case\tf20\tchan\t7e+17\t5\t75\t-5\t90\t192\n"
        )

        rc, _stdout, stderr = self._run_cli("--verbose")

        self.assertEqual(rc, 0, stderr)
        env_text = (self.root / "000_case" / "case.env").read_text(encoding="utf-8")
        self.assertIn('export CAP_CASE_ID="0"', env_text)
        self.assertIn('export CAP_CASE_NAME="000_case"', env_text)
        self.assertIn('export CAP_LASER_CASE="f20"', env_text)
        self.assertIn('export CAP_PLASMA_KIND="chan"', env_text)
        self.assertIn('export CAP_N0_CM3="7e+17"', env_text)
        self.assertIn('export CAP_DIAG_PRESET="guiding_rhoe"', env_text)
        self.assertIn('export CAP_LONG_PROFILE="both"', env_text)

    def test_converts_plateau_length_mm_to_m(self) -> None:
        self._write_cases(
            "CASE_ID\tCASE_NAME\tLASER_CASE\tPLASMA_KIND\tN0_CM3\tPLATEAU_LENGTH_MM\tRADIUS_UM\tFOCUS_OFFSET_FROM_PLATEAU_START_MM\tCAP_RMAX_UM\tCAP_NR\n"
            "0\t000_case\tf20\tchan\t7e+17\t5\t75\t-5\t90\t192\n"
        )

        rc, _stdout, stderr = self._run_cli("--verbose")

        self.assertEqual(rc, 0, stderr)
        env_text = (self.root / "000_case" / "case.env").read_text(encoding="utf-8")
        self.assertIn('export CAP_PLATEAU_LENGTH_M="5e-3"', env_text)

    def test_materializes_optional_nitrogen_fraction(self) -> None:
        self._write_cases(
            "CASE_ID\tCASE_NAME\tLASER_CASE\tPLASMA_KIND\tN0_CM3\tPLATEAU_LENGTH_MM\tRADIUS_UM\tFOCUS_OFFSET_FROM_PLATEAU_START_MM\tNITROGEN_DOPANT_FRACTION\tCAP_RMAX_UM\tCAP_NR\n"
            "0\t000_case\tf20\tchan\t7e+17\t5\t75\t-5\t0.005\t90\t192\n"
        )

        rc, _stdout, stderr = self._run_cli("--verbose")

        self.assertEqual(rc, 0, stderr)
        env_text = (self.root / "000_case" / "case.env").read_text(
            encoding="utf-8"
        )
        self.assertIn(
            'export CAP_NITROGEN_DOPANT_FRACTION="0.005"', env_text
        )

    def test_converts_radius_um_to_m_when_present(self) -> None:
        self._write_cases(
            "CASE_ID\tCASE_NAME\tLASER_CASE\tPLASMA_KIND\tN0_CM3\tPLATEAU_LENGTH_MM\tRADIUS_UM\tFOCUS_OFFSET_FROM_PLATEAU_START_MM\tCAP_RMAX_UM\tCAP_NR\n"
            "0\t000_case\tf20\tchan\t7e+17\t5\t75\t-5\t90\t192\n"
        )

        rc, _stdout, stderr = self._run_cli("--verbose")

        self.assertEqual(rc, 0, stderr)
        env_text = (self.root / "000_case" / "case.env").read_text(encoding="utf-8")
        self.assertIn('export CAP_RADIUS_M="7.5e-5"', env_text)
        self.assertIn('export CAP_RMAX_M="9e-5"', env_text)

    def test_omits_radius_m_when_radius_um_is_empty(self) -> None:
        self._write_cases(
            "CASE_ID\tCASE_NAME\tLASER_CASE\tPLASMA_KIND\tN0_CM3\tPLATEAU_LENGTH_MM\tDIAMETER_UM\tRADIUS_UM\tFOCUS_OFFSET_FROM_PLATEAU_START_MM\tCAP_RMAX_UM\tCAP_NR\n"
            "0\t000_uniform\tf20\tuni\t7e+17\t5\t\t\t-5\t300\t192\n"
        )

        rc, _stdout, stderr = self._run_cli("--verbose")

        self.assertEqual(rc, 0, stderr)
        env_text = (self.root / "000_uniform" / "case.env").read_text(encoding="utf-8")
        self.assertNotIn("CAP_RADIUS_M", env_text)
        self.assertIn('export CAP_RMAX_M="3e-4"', env_text)

    def test_dry_run_does_not_write_input_or_env(self) -> None:
        self._write_cases(
            "CASE_ID\tCASE_NAME\tLASER_CASE\tPLASMA_KIND\tN0_CM3\tPLATEAU_LENGTH_MM\tRADIUS_UM\tFOCUS_OFFSET_FROM_PLATEAU_START_MM\tCAP_RMAX_UM\tCAP_NR\n"
            "0\t000_case\tf20\tchan\t7e+17\t5\t75\t-5\t90\t192\n"
        )

        rc, stdout, stderr = self._run_cli("--dry-run", "--verbose")

        self.assertEqual(rc, 0, stderr)
        self.assertIn("WOULD: copy input template", stdout)
        self.assertFalse((self.root / "000_case" / "input.py").exists())
        self.assertFalse((self.root / "000_case" / "case.env").exists())

    def test_refuses_to_overwrite_existing_files_without_overwrite(self) -> None:
        self._write_cases(
            "CASE_ID\tCASE_NAME\tLASER_CASE\tPLASMA_KIND\tN0_CM3\tPLATEAU_LENGTH_MM\tRADIUS_UM\tFOCUS_OFFSET_FROM_PLATEAU_START_MM\tCAP_RMAX_UM\tCAP_NR\n"
            "0\t000_case\tf20\tchan\t7e+17\t5\t75\t-5\t90\t192\n"
        )
        case_dir = self.root / "000_case"
        case_dir.mkdir()
        (case_dir / "input.py").write_text("old input\n", encoding="utf-8")
        (case_dir / "case.env").write_text("old env\n", encoding="utf-8")

        rc, _stdout, stderr = self._run_cli("--verbose")

        self.assertEqual(rc, 1)
        self.assertEqual(stderr, "")
        self.assertEqual((case_dir / "input.py").read_text(encoding="utf-8"), "old input\n")
        self.assertEqual((case_dir / "case.env").read_text(encoding="utf-8"), "old env\n")

    def test_overwrite_replaces_existing_files_when_requested(self) -> None:
        self._write_cases(
            "CASE_ID\tCASE_NAME\tLASER_CASE\tPLASMA_KIND\tN0_CM3\tPLATEAU_LENGTH_MM\tRADIUS_UM\tFOCUS_OFFSET_FROM_PLATEAU_START_MM\tCAP_RMAX_UM\tCAP_NR\n"
            "0\t000_case\tf20\tchan\t7e+17\t5\t75\t-5\t90\t192\n"
        )
        case_dir = self.root / "000_case"
        case_dir.mkdir()
        (case_dir / "input.py").write_text("old input\n", encoding="utf-8")
        (case_dir / "case.env").write_text("old env\n", encoding="utf-8")

        rc, _stdout, stderr = self._run_cli("--overwrite", "--verbose")

        self.assertEqual(rc, 0, stderr)
        self.assertEqual((case_dir / "input.py").read_text(encoding="utf-8"), "#!/usr/bin/env python3\nprint('template')\n")
        env_text = (case_dir / "case.env").read_text(encoding="utf-8")
        self.assertIn('export CAP_CASE_ID="0"', env_text)

    def test_missing_required_column_fails_cleanly(self) -> None:
        self._write_cases(
            "CASE_ID\tCASE_NAME\tLASER_CASE\tPLASMA_KIND\tN0_CM3\tRADIUS_UM\tFOCUS_OFFSET_FROM_PLATEAU_START_MM\tCAP_RMAX_UM\tCAP_NR\n"
            "0\t000_case\tf20\tchan\t7e+17\t75\t-5\t90\t192\n"
        )

        rc, stdout, stderr = self._run_cli("--verbose")

        self.assertEqual(rc, 1)
        self.assertEqual(stderr, "")
        self.assertIn("missing required column 'PLATEAU_LENGTH_MM'", stdout)
        self.assertFalse((self.root / "000_case" / "input.py").exists())
        self.assertFalse((self.root / "000_case" / "case.env").exists())

    def test_new_code_does_not_contain_shell_recursive_delete(self) -> None:
        repo_root = Path(__file__).resolve().parents[1]
        for relative_path in [
            "campaign_workflow/core/case_materialization.py",
            "campaign_workflow/cli/materialize_cases.py",
        ]:
            text = (repo_root / relative_path).read_text(encoding="utf-8")
            self.assertNotIn("rm -rf", text)
            self.assertNotIn("shutil.rmtree", text)

    def _write_campaign(self) -> None:
        campaign = {
            "schema_version": 1,
            "campaign_name": "fake_campaign",
            "case_manifest": "cases.tsv",
            "case_manifest_format": "tsv",
            "case_id_column": "CASE_ID",
            "case_name_column": "CASE_NAME",
            "simulation": {
                "backend": "warpx_picmi",
                "scheduler": "slurm",
                "input_script": "input.py",
                "completion_marker": "post/sim_done.json",
                "failure_marker": "post/sim_failed.json",
            },
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

    def _write_template(self, text: str) -> None:
        (self.root / "input_template.py").write_text(text, encoding="utf-8")

    def _write_cases(self, text: str) -> None:
        (self.root / "cases.tsv").write_text(text, encoding="utf-8")

    def _run_cli(self, *extra_args: str) -> tuple[int, str, str]:
        stdout = io.StringIO()
        stderr = io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            rc = materialize_cases_main(["--campaign-root", str(self.root), *extra_args])
        return rc, stdout.getvalue(), stderr.getvalue()


if __name__ == "__main__":
    unittest.main()
