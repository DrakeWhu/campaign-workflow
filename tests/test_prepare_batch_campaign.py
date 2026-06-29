from __future__ import annotations

import contextlib
import csv
import io
import json
import tempfile
import unittest
from pathlib import Path

from campaign_workflow.batch_campaign import build_batch_campaign_plan
from campaign_workflow.cli.prepare_batch_campaign import (
    main as prepare_batch_campaign_main,
)


FIELDNAMES = [
    "CASE_ID",
    "CASE_NAME",
    "LASER_CASE",
    "PLASMA_KIND",
    "N0_CM3",
    "PLATEAU_LENGTH_MM",
    "DIAMETER_UM",
    "RADIUS_UM",
    "FOCUS_OFFSET_FROM_PLATEAU_START_MM",
    "CAP_RMAX_UM",
    "CAP_NR",
    "OPT_ITERATION",
    "OPT_CANDIDATE_ID",
    "OPT_RECOMMENDATION_ID",
]


class PrepareBatchCampaignTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmpdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tmpdir.name)
        self.template_root = self.root / "template_campaign"
        self.template_root.mkdir()
        self.output_root = self.root / "new_campaign"
        self.candidate_batch = self.root / "candidate_batch.tsv"
        self.batch_plan = self.root / "batch_campaign_plan.json"
        self._write_template_campaign()
        self._write_batch_plan()
        self._write_candidate_batch()

    def tearDown(self) -> None:
        self.tmpdir.cleanup()

    def test_valid_candidate_batch_produces_preparation_plan(self) -> None:
        plan = build_batch_campaign_plan(
            candidate_batch=self.candidate_batch,
            batch_plan=self.batch_plan,
            template_campaign_root=self.template_root,
            output_campaign_root=self.output_root,
            campaign_name="capillaries_bo_opt_iter_000",
        )
        self.assertEqual(plan.campaign_name, "capillaries_bo_opt_iter_000")
        self.assertEqual(len(plan.rows), 2)
        self.assertEqual(
            plan.campaign_config_output["campaign_name"], "capillaries_bo_opt_iter_000"
        )
        self.assertEqual(plan.campaign_config_output["case_manifest"], "cases.tsv")

    def test_dry_run_does_not_create_output_campaign_root(self) -> None:
        rc, stdout, stderr = self._run_cli("--dry-run")
        self.assertEqual(rc, 0, stderr)
        self.assertIn('"mode": "dry-run"', stdout)
        self.assertFalse(self.output_root.exists())

    def test_execute_creates_expected_campaign_files_without_materializing_cases(
        self,
    ) -> None:
        rc, stdout, stderr = self._run_cli("--execute")
        self.assertEqual(rc, 0, stderr)
        self.assertIn('"mode": "execute"', stdout)
        self.assertTrue((self.output_root / "campaign.json").is_file())
        self.assertTrue((self.output_root / "cases.tsv").is_file())
        self.assertTrue((self.output_root / "input_template.py").is_file())
        self.assertTrue((self.output_root / "array_logs").is_dir())
        self.assertTrue(
            (self.output_root / "optimizer_batch_provenance.json").is_file()
        )
        self.assertFalse((self.output_root / "000_f20_chan_case").exists())

    def test_cases_tsv_preserves_required_and_optimizer_metadata_columns(self) -> None:
        rc, _stdout, stderr = self._run_cli("--execute")
        self.assertEqual(rc, 0, stderr)
        with (self.output_root / "cases.tsv").open(
            "r", encoding="utf-8", newline=""
        ) as handle:
            reader = csv.DictReader(handle, delimiter="\t")
            rows = list(reader)
        self.assertEqual(reader.fieldnames, FIELDNAMES)
        self.assertEqual(rows[0]["CASE_NAME"], "000_f20_chan_case")
        self.assertEqual(rows[0]["OPT_CANDIDATE_ID"], "opt_000_000")
        self.assertEqual(rows[1]["OPT_RECOMMENDATION_ID"], "iter_000_rank_002")

    def test_campaign_json_updates_campaign_name(self) -> None:
        rc, _stdout, stderr = self._run_cli("--execute", campaign_name="new_name")
        self.assertEqual(rc, 0, stderr)
        data = json.loads(
            (self.output_root / "campaign.json").read_text(encoding="utf-8")
        )
        self.assertEqual(data["campaign_name"], "new_name")
        self.assertEqual(data["case_manifest"], "cases.tsv")
        self.assertEqual(data["case_id_column"], "CASE_ID")
        self.assertEqual(data["case_name_column"], "CASE_NAME")

    def test_fails_if_required_column_is_missing(self) -> None:
        self._write_candidate_batch(omit_columns={"N0_CM3"})
        rc, _stdout, stderr = self._run_cli("--dry-run")
        self.assertEqual(rc, 1)
        self.assertIn("missing required columns", stderr)
        self.assertFalse(self.output_root.exists())

    def test_fails_if_case_id_is_repeated(self) -> None:
        self._write_candidate_batch(second_overrides={"CASE_ID": "0"})
        rc, _stdout, stderr = self._run_cli("--dry-run")
        self.assertEqual(rc, 1)
        self.assertIn("duplicate CASE_ID", stderr)

    def test_fails_if_case_name_is_repeated(self) -> None:
        self._write_candidate_batch(second_overrides={"CASE_NAME": "000_f20_chan_case"})
        rc, _stdout, stderr = self._run_cli("--dry-run")
        self.assertEqual(rc, 1)
        self.assertIn("duplicate CASE_NAME", stderr)

    def test_fails_if_case_name_contains_parent_reference(self) -> None:
        self._write_candidate_batch(first_overrides={"CASE_NAME": ".."})
        rc, _stdout, stderr = self._run_cli("--dry-run")
        self.assertEqual(rc, 1)
        self.assertIn("must not contain '..'", stderr)

    def test_fails_if_case_name_is_absolute(self) -> None:
        self._write_candidate_batch(first_overrides={"CASE_NAME": "/tmp/bad_case"})
        rc, _stdout, stderr = self._run_cli("--dry-run")
        self.assertEqual(rc, 1)
        self.assertIn("path separators", stderr)

    def test_fails_if_laser_case_is_not_allowed(self) -> None:
        self._write_candidate_batch(first_overrides={"LASER_CASE": "f99"})
        rc, _stdout, stderr = self._run_cli("--dry-run")
        self.assertEqual(rc, 1)
        self.assertIn("invalid LASER_CASE", stderr)

    def test_fails_if_plasma_kind_is_not_allowed(self) -> None:
        self._write_candidate_batch(first_overrides={"PLASMA_KIND": "gas"})
        rc, _stdout, stderr = self._run_cli("--dry-run")
        self.assertEqual(rc, 1)
        self.assertIn("invalid PLASMA_KIND", stderr)

    def test_fails_if_numeric_field_is_not_numeric(self) -> None:
        self._write_candidate_batch(first_overrides={"CAP_NR": "not_an_int"})
        rc, _stdout, stderr = self._run_cli("--dry-run")
        self.assertEqual(rc, 1)
        self.assertIn("CAP_NR must be numeric", stderr)

    def test_execute_fails_if_output_campaign_root_already_exists(self) -> None:
        self.output_root.mkdir()
        rc, _stdout, stderr = self._run_cli("--execute")
        self.assertEqual(rc, 1)
        self.assertIn("already exists", stderr)

    def test_new_cli_does_not_call_slurm_or_subprocess(self) -> None:
        text = self._read_new_code_text()
        self.assertNotIn("import subprocess", text)
        self.assertNotIn("os.system", text)
        self.assertNotIn("subprocess.", text)
        self.assertNotIn("sbatch ", text)
        self.assertNotIn("srun ", text)
        self.assertNotIn("mpiexec ", text)
        self.assertNotIn("mpirun ", text)

    def test_new_code_does_not_import_campaign_optimizer(self) -> None:
        lowered = self._read_new_code_text().lower()
        for token in [
            "campaign_optimizer",
            "campaign-optimizer",
            "import optimas",
            "from optimas",
            "import botorch",
            "from botorch",
            "import torch",
            "from torch",
            "import ax",
            "from ax",
        ]:
            self.assertNotIn(token, lowered)

    def test_new_code_does_not_read_hdf5_or_openpmd(self) -> None:
        text = self._read_new_code_text()
        self.assertNotIn("import h5py", text)
        self.assertNotIn("openpmd_api", text)
        self.assertNotIn("OpenPMDTimeSeries", text)
        self.assertNotIn("*.h5", text)
        self.assertNotIn("*.hdf5", text)
        self.assertNotIn("diags/fields", text)
        self.assertNotIn("diags/plasma_electrons", text)

    def _write_template_campaign(self) -> None:
        campaign = {
            "schema_version": 1,
            "campaign_name": "template_campaign",
            "case_manifest": "cases.tsv",
            "case_manifest_format": "tsv",
            "case_id_column": "CASE_ID",
            "case_name_column": "CASE_NAME",
            "simulation": {"backend": "warpx_picmi", "scheduler": "slurm"},
        }
        (self.template_root / "campaign.json").write_text(
            json.dumps(campaign, indent=2) + "\n", encoding="utf-8"
        )
        (self.template_root / "input_template.py").write_text(
            "#!/usr/bin/env python3\nprint('template')\n", encoding="utf-8"
        )

    def _write_batch_plan(self) -> None:
        plan = {
            "schema_version": 1,
            "plan_type": "optimizer_candidate_batch",
            "created_at": "2026-06-29T13:36:39Z",
            "optimizer_iteration": 0,
            "objective_config_id": "capillary_objectives_v1",
            "candidate_batch": "outputs/candidate_batch.tsv",
            "recommended_candidates": "outputs/recommended_candidates.tsv",
            "campaign_template": {
                "campaign_json": "campaign.json",
                "input_template": "input_template.py",
            },
            "source_campaigns": [
                {"campaign_name": "capillaries_bo_transverse_campaign"}
            ],
        }
        self.batch_plan.write_text(json.dumps(plan, indent=2) + "\n", encoding="utf-8")

    def _base_rows(self) -> list[dict[str, str]]:
        return [
            {
                "CASE_ID": "0",
                "CASE_NAME": "000_f20_chan_case",
                "LASER_CASE": "f20",
                "PLASMA_KIND": "chan",
                "N0_CM3": "4.183756962833051e+18",
                "PLATEAU_LENGTH_MM": "24.739226621684832",
                "DIAMETER_UM": "500.0",
                "RADIUS_UM": "250.0",
                "FOCUS_OFFSET_FROM_PLATEAU_START_MM": "5.0",
                "CAP_RMAX_UM": "300.0",
                "CAP_NR": "192",
                "OPT_ITERATION": "0",
                "OPT_CANDIDATE_ID": "opt_000_000",
                "OPT_RECOMMENDATION_ID": "iter_000_rank_001",
            },
            {
                "CASE_ID": "1",
                "CASE_NAME": "001_f32_chan_case",
                "LASER_CASE": "f32",
                "PLASMA_KIND": "chan",
                "N0_CM3": "3.7783730374452367e+18",
                "PLATEAU_LENGTH_MM": "25.0",
                "DIAMETER_UM": "500.0",
                "RADIUS_UM": "250.0",
                "FOCUS_OFFSET_FROM_PLATEAU_START_MM": "4.5",
                "CAP_RMAX_UM": "300.0",
                "CAP_NR": "192",
                "OPT_ITERATION": "0",
                "OPT_CANDIDATE_ID": "opt_000_001",
                "OPT_RECOMMENDATION_ID": "iter_000_rank_002",
            },
        ]

    def _write_candidate_batch(
        self,
        *,
        omit_columns: set[str] | None = None,
        first_overrides: dict[str, str] | None = None,
        second_overrides: dict[str, str] | None = None,
    ) -> None:
        omit_columns = omit_columns or set()
        fieldnames = [name for name in FIELDNAMES if name not in omit_columns]
        rows = self._base_rows()
        if first_overrides:
            rows[0].update(first_overrides)
        if second_overrides:
            rows[1].update(second_overrides)
        with self.candidate_batch.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(
                handle, fieldnames=fieldnames, delimiter="\t", lineterminator="\n"
            )
            writer.writeheader()
            for row in rows:
                writer.writerow({name: row.get(name, "") for name in fieldnames})

    def _run_cli(
        self, mode: str, *, campaign_name: str = "capillaries_bo_opt_iter_000"
    ) -> tuple[int, str, str]:
        stdout = io.StringIO()
        stderr = io.StringIO()
        args = [
            "--candidate-batch",
            str(self.candidate_batch),
            "--batch-plan",
            str(self.batch_plan),
            "--template-campaign-root",
            str(self.template_root),
            "--output-campaign-root",
            str(self.output_root),
            "--campaign-name",
            campaign_name,
            mode,
        ]
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            rc = prepare_batch_campaign_main(args)
        return rc, stdout.getvalue(), stderr.getvalue()

    def _read_new_code_text(self) -> str:
        repo_root = Path(__file__).resolve().parents[1]
        paths = [
            repo_root / "campaign_workflow" / "batch_campaign.py",
            repo_root / "campaign_workflow" / "cli" / "prepare_batch_campaign.py",
        ]
        return "\n".join(path.read_text(encoding="utf-8") for path in paths)


if __name__ == "__main__":
    unittest.main()
