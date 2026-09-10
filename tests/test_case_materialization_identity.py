from __future__ import annotations

import contextlib
import hashlib
import io
import json
import tempfile
import unittest
from pathlib import Path

from campaign_workflow.cli.materialize_cases import main as materialize_cases_main


class CaseMaterializationIdentityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmpdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tmpdir.name) / "campaign"
        self.root.mkdir(parents=True)
        self._write_campaign()
        self._write_cases()
        self.template = self.root / "input_template.py"
        self.template.write_text(
            "#!/usr/bin/env python3\nprint('v1')\n",
            encoding="utf-8",
        )

    def tearDown(self) -> None:
        self.tmpdir.cleanup()

    def sha256_file(self, path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()

    def run_cli(self, *args: str) -> tuple[int, str, str]:
        stdout = io.StringIO()
        stderr = io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            rc = materialize_cases_main(
                ["--campaign-root", str(self.root), *args]
            )
        return rc, stdout.getvalue(), stderr.getvalue()

    def test_materialization_manifest_pins_source_input_env_and_materialized_input(self) -> None:
        rc, _stdout, stderr = self.run_cli()
        self.assertEqual(rc, 0, stderr)

        case_dir = self.root / "000_case"
        input_path = case_dir / "input.py"
        env_path = case_dir / "case.env"
        manifest_path = case_dir / "manifests" / "input_materialization.json"

        self.assertTrue(manifest_path.is_file())
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

        self.assertEqual(manifest["schema_version"], 1)
        self.assertEqual(manifest["operation"], "materialize_cases")
        self.assertEqual(manifest["case_id"], 0)
        self.assertEqual(manifest["case_name"], "000_case")
        self.assertEqual(
            manifest["source_input_template"]["path"],
            str(self.template.resolve()),
        )
        self.assertEqual(
            manifest["source_input_template"]["sha256"],
            self.sha256_file(self.template),
        )
        self.assertEqual(manifest["materialized_input"]["path"], "input.py")
        self.assertEqual(
            manifest["materialized_input"]["sha256"],
            self.sha256_file(input_path),
        )
        self.assertEqual(
            manifest["materialized_input"]["sha256"],
            manifest["source_input_template"]["sha256"],
        )
        self.assertEqual(manifest["case_env"]["path"], "case.env")
        self.assertEqual(
            manifest["case_env"]["sha256"],
            self.sha256_file(env_path),
        )
        self.assertEqual(manifest["destructive_operations"], 0)

    def test_dry_run_does_not_write_materialization_manifest(self) -> None:
        rc, _stdout, stderr = self.run_cli("--dry-run")
        self.assertEqual(rc, 0, stderr)
        self.assertFalse(
            (
                self.root
                / "000_case"
                / "manifests"
                / "input_materialization.json"
            ).exists()
        )

    def test_overwrite_updates_manifest_to_new_template_identity(self) -> None:
        rc, _stdout, stderr = self.run_cli()
        self.assertEqual(rc, 0, stderr)

        self.template.write_text(
            "#!/usr/bin/env python3\nprint('v2')\n",
            encoding="utf-8",
        )
        expected_sha256 = self.sha256_file(self.template)

        rc, _stdout, stderr = self.run_cli("--overwrite")
        self.assertEqual(rc, 0, stderr)

        case_dir = self.root / "000_case"
        manifest = json.loads(
            (
                case_dir
                / "manifests"
                / "input_materialization.json"
            ).read_text(encoding="utf-8")
        )
        self.assertEqual(
            manifest["source_input_template"]["sha256"],
            expected_sha256,
        )
        self.assertEqual(
            manifest["materialized_input"]["sha256"],
            expected_sha256,
        )
        self.assertEqual(self.sha256_file(case_dir / "input.py"), expected_sha256)

    def _write_campaign(self) -> None:
        campaign = {
            "schema_version": 1,
            "campaign_name": "materialization_identity",
            "case_manifest": "cases.tsv",
            "case_manifest_format": "tsv",
            "case_id_column": "CASE_ID",
            "case_name_column": "CASE_NAME",
            "simulation": {
                "backend": "warpx_picmi",
                "scheduler": "slurm",
                "input_script": "input.py",
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

    def _write_cases(self) -> None:
        (self.root / "cases.tsv").write_text(
            "CASE_ID\tCASE_NAME\tLASER_CASE\tPLASMA_KIND\tN0_CM3\t"
            "PLATEAU_LENGTH_MM\tRADIUS_UM\t"
            "FOCUS_OFFSET_FROM_PLATEAU_START_MM\tCAP_RMAX_UM\tCAP_NR\n"
            "0\t000_case\tf32\tchan\t4e18\t5\t150\t0\t180\t192\n",
            encoding="utf-8",
        )


if __name__ == "__main__":
    unittest.main()
