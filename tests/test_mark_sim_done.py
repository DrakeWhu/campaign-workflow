from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from campaign_workflow.cli.init_case_states import main as init_case_states_main
from campaign_workflow.cli.mark_sim_done import main as mark_sim_done_main
from campaign_workflow.cli.mark_sim_running import main as mark_sim_running_main
from campaign_workflow.cli.mark_sim_submitted import main as mark_sim_submitted_main
from campaign_workflow.core.atomic_io import read_json


class MarkSimDoneTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmpdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tmpdir.name) / "fake_campaign"
        self.root.mkdir(parents=True)
        self._write_fake_campaign()

        rc = init_case_states_main(
            [
                "--campaign-root",
                str(self.root),
                "--create-missing-case-dirs",
            ]
        )
        self.assertEqual(rc, 0)

    def tearDown(self) -> None:
        self.tmpdir.cleanup()

    def _adopt_argv(self, case_id: int = 0, *, dry_run: bool = False) -> list[str]:
        argv = [
            "--campaign-root",
            str(self.root),
            "--case-id",
            str(case_id),
            "--adopt-existing-evidence",
        ]
        if dry_run:
            argv.append("--dry-run")
        return argv

    def _mark_running(self) -> None:
        submitted = mark_sim_submitted_main(
            [
                "--campaign-root",
                str(self.root),
                "--case-id",
                "0",
                "--scheduler",
                "fake-slurm",
                "--scheduler-job-id",
                "12345",
                "--scheduler-array-task-id",
                "0",
                "--submit-command",
                "sbatch submit_fake_array.sh",
                "--environment-name",
                "campaign-workflow-py310",
            ]
        )
        self.assertEqual(submitted, 0)

        running = mark_sim_running_main(
            [
                "--campaign-root",
                str(self.root),
                "--case-id",
                "0",
                "--scheduler",
                "fake-slurm",
                "--scheduler-job-id",
                "12345",
                "--scheduler-array-task-id",
                "0",
                "--run-command",
                "fake-srun python input.py",
                "--environment-name",
                "fake-warpx-env",
                "--stdout-log",
                "logs/sim.out",
                "--stderr-log",
                "logs/sim.err",
            ]
        )
        self.assertEqual(running, 0)

    def _runtime_argv(
        self,
        *,
        return_code: int = 0,
        scheduler_job_id: str = "12345",
        scheduler_array_task_id: str = "0",
        run_command: str = "fake-srun python input.py",
    ) -> list[str]:
        return [
            "--campaign-root",
            str(self.root),
            "--case-id",
            "0",
            "--runtime-success-receipt",
            "--scheduler",
            "fake-slurm",
            "--scheduler-job-id",
            scheduler_job_id,
            "--scheduler-array-task-id",
            scheduler_array_task_id,
            "--run-command",
            run_command,
            "--environment-name",
            "fake-warpx-env",
            "--stdout-log",
            "logs/sim.out",
            "--stderr-log",
            "logs/sim.err",
            "--return-code",
            str(return_code),
        ]

    def test_adopts_created_case_from_raw_diagnostic_evidence(self) -> None:
        case_dir = self.root / "000_fake_case"
        self._write_raw(case_dir, "diags/raw_000.fake", b"fake payload")

        rc = mark_sim_done_main(self._adopt_argv())
        self.assertEqual(rc, 0)

        state = read_json(case_dir / "state.json")
        validation = read_json(case_dir / "validation.json")
        marker = read_json(case_dir / "post/sim_done.json")

        self.assertEqual(state["state"], "Sim_done")
        self.assertEqual(state["history"][-1]["from"], "Created")
        self.assertEqual(state["history"][-1]["to"], "Sim_done")
        self.assertEqual(state["history"][-1]["operation"], "mark_sim_done")
        self.assertEqual(validation["simulation"]["evidence_mode"], "historical_adoption")
        self.assertTrue(validation["simulation"]["adopted_existing_evidence"])
        self.assertEqual(marker["evidence_mode"], "historical_adoption")
        self.assertTrue(marker["adopted_existing_evidence"])
        self.assertNotIn("return_code", marker)
        self.assertFalse(validation["cleanup"]["cleanup_allowed"])

    def test_adoption_dry_run_does_not_change_state_validation_or_marker(self) -> None:
        case_dir = self.root / "000_fake_case"
        self._write_raw(case_dir, "diags/raw_000.fake", b"fake payload")

        rc = mark_sim_done_main(self._adopt_argv(dry_run=True))
        self.assertEqual(rc, 0)

        state = read_json(case_dir / "state.json")
        validation = read_json(case_dir / "validation.json")
        self.assertEqual(state["state"], "Created")
        self.assertNotIn("simulation", validation)
        self.assertFalse((case_dir / "post/sim_done.json").exists())
        self.assertEqual(validation["raw"], {})
        self.assertFalse(validation["cleanup"]["cleanup_allowed"])

    def test_adoption_missing_completion_evidence_fails_without_writing(self) -> None:
        case_dir = self.root / "000_fake_case"

        rc = mark_sim_done_main(self._adopt_argv())
        self.assertEqual(rc, 1)

        state = read_json(case_dir / "state.json")
        validation = read_json(case_dir / "validation.json")
        self.assertEqual(state["state"], "Created")
        self.assertNotIn("simulation", validation)
        self.assertFalse((case_dir / "post/sim_done.json").exists())

    def test_valid_historical_completion_marker_can_be_adopted(self) -> None:
        case_dir = self.root / "000_fake_case"
        marker_path = case_dir / "post/sim_done.json"
        marker_path.parent.mkdir(parents=True, exist_ok=True)
        marker_path.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "ok": True,
                    "operation": "mark_sim_done",
                    "case_id": 0,
                    "case_name": "000_fake_case",
                    "finished_at": "2026-01-01T00:00:00Z",
                    "return_code": 0,
                }
            )
            + "\n",
            encoding="utf-8",
        )

        rc = mark_sim_done_main(self._adopt_argv())
        self.assertEqual(rc, 0)

        validation = read_json(case_dir / "validation.json")
        marker = read_json(marker_path)
        joined_evidence = "\n".join(validation["simulation"]["evidence"])
        self.assertIn("valid historical completion marker", joined_evidence)
        self.assertEqual(marker["evidence_mode"], "historical_adoption")
        self.assertTrue(marker["adopted_existing_evidence"])
        self.assertNotIn("return_code", marker)

    def test_empty_or_foreign_historical_marker_is_not_adopted(self) -> None:
        case_dir = self.root / "000_fake_case"
        marker_path = case_dir / "post/sim_done.json"
        marker_path.parent.mkdir(parents=True, exist_ok=True)

        for payload in (
            {"ok": True},
            {
                "schema_version": 1,
                "ok": True,
                "operation": "mark_sim_done",
                "case_id": 1,
                "case_name": "001_fake_case",
                "return_code": 0,
            },
        ):
            with self.subTest(payload=payload):
                marker_path.write_text(json.dumps(payload) + "\n", encoding="utf-8")
                rc = mark_sim_done_main(self._adopt_argv())
                self.assertEqual(rc, 1)
                self.assertEqual(read_json(case_dir / "state.json")["state"], "Created")
                self.assertNotIn("simulation", read_json(case_dir / "validation.json"))

    def test_already_sim_done_adoption_is_noop_success(self) -> None:
        case_dir = self.root / "000_fake_case"
        self._write_raw(case_dir, "diags/raw_000.fake", b"fake payload")

        first = mark_sim_done_main(self._adopt_argv())
        self.assertEqual(first, 0)

        state_before = read_json(case_dir / "state.json")
        validation_before = read_json(case_dir / "validation.json")
        marker_before = read_json(case_dir / "post/sim_done.json")

        second = mark_sim_done_main(self._adopt_argv())
        self.assertEqual(second, 0)

        state_after = read_json(case_dir / "state.json")
        validation_after = read_json(case_dir / "validation.json")
        marker_after = read_json(case_dir / "post/sim_done.json")

        self.assertEqual(state_after["state"], "Sim_done")
        self.assertEqual(len(state_after["history"]), len(state_before["history"]))
        self.assertEqual(validation_after["simulation"], validation_before["simulation"])
        self.assertEqual(marker_after, marker_before)

    def test_runtime_success_receipt_marks_matching_running_execution_done(self) -> None:
        case_dir = self.root / "000_fake_case"
        self._mark_running()
        self._write_raw(case_dir, "diags/raw_000.fake", b"fake payload")

        rc = mark_sim_done_main(self._runtime_argv())
        self.assertEqual(rc, 0)

        state = read_json(case_dir / "state.json")
        validation = read_json(case_dir / "validation.json")
        marker = read_json(case_dir / "post/sim_done.json")
        self.assertEqual(state["state"], "Sim_done")
        self.assertEqual(validation["simulation"]["evidence_mode"], "runtime_success_receipt")
        self.assertEqual(validation["simulation"]["return_code"], 0)
        self.assertEqual(marker["evidence_mode"], "runtime_success_receipt")
        self.assertEqual(marker["return_code"], 0)
        self.assertEqual(marker["runtime_receipt"]["scheduler_job_id"], "12345")

    def test_failed_runtime_receipt_cannot_be_rescued_by_residual_raw(self) -> None:
        case_dir = self.root / "000_fake_case"
        self._mark_running()
        self._write_raw(case_dir, "diags/raw_000.fake", b"partial raw that satisfies min_files")

        rc = mark_sim_done_main(self._runtime_argv(return_code=37))
        self.assertEqual(rc, 1)
        self.assertEqual(read_json(case_dir / "state.json")["state"], "Running")
        self.assertFalse((case_dir / "post/sim_done.json").exists())

    def test_runtime_receipt_from_other_execution_is_rejected_even_with_raw(self) -> None:
        case_dir = self.root / "000_fake_case"
        self._mark_running()
        self._write_raw(case_dir, "diags/raw_000.fake", b"partial raw that satisfies min_files")

        rc = mark_sim_done_main(self._runtime_argv(scheduler_job_id="99999"))
        self.assertEqual(rc, 1)
        self.assertEqual(read_json(case_dir / "state.json")["state"], "Running")
        self.assertFalse((case_dir / "post/sim_done.json").exists())

    def _write_fake_campaign(self) -> None:
        campaign = {
            "schema_version": 1,
            "campaign_name": "fake_campaign",
            "case_manifest": "cases.tsv",
            "case_manifest_format": "tsv",
            "case_id_column": "CASE_ID",
            "case_name_column": "CASE_NAME",
            "simulation": {
                "backend": "fake",
                "scheduler": "none",
                "input_script": "input.py",
                "completion_marker": "post/sim_done.json",
                "failure_marker": "post/sim_failed.json",
            },
            "raw_diagnostics": [
                {
                    "name": "fake_raw",
                    "kind": "fake",
                    "path": "diags",
                    "glob": "diags/**/*.fake",
                    "min_files": 1,
                    "min_age_seconds": 0,
                    "required": True,
                }
            ],
            "analysis": {
                "name": "fake_analysis",
                "kind": "fake",
                "adapter": "fake",
                "inputs": ["fake_raw"],
                "outputs": [
                    {
                        "name": "fake_metrics",
                        "kind": "csv",
                        "path": "post/fake_metrics.csv",
                        "min_rows": 1,
                        "required_columns": ["iteration"],
                        "required": True,
                    }
                ],
            },
            "cleanup": {
                "raw_delete_globs": ["diags/**/*.fake"],
                "require_raw_validated": True,
                "require_reduced_validated": True,
                "require_delete_manifest": True,
                "allow_directory_delete": False,
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

        (self.root / "campaign.json").write_text(json.dumps(campaign, indent=2) + "\n", encoding="utf-8")
        (self.root / "cases.tsv").write_text(
            "CASE_ID\tCASE_NAME\tKIND\n"
            "0\t000_fake_case\talpha\n"
            "1\t001_fake_case\tbeta\n"
            "2\t002_fake_case\tgamma\n",
            encoding="utf-8",
        )

    def _write_raw(self, case_dir: Path, relative_path: str, payload: bytes) -> None:
        path = case_dir / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(payload)


if __name__ == "__main__":
    unittest.main()
