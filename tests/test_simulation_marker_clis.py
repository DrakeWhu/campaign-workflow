from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from campaign_workflow.cli.init_case_states import main as init_case_states_main
from campaign_workflow.cli.mark_sim_failed import main as mark_sim_failed_main
from campaign_workflow.cli.mark_sim_running import main as mark_sim_running_main
from campaign_workflow.cli.mark_sim_submitted import main as mark_sim_submitted_main
from campaign_workflow.core.atomic_io import read_json


class SimulationMarkerCliTests(unittest.TestCase):
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

    def test_mark_submitted_writes_marker_state_and_validation_evidence(self) -> None:
        case_dir = self.root / "000_fake_case"

        rc = mark_sim_submitted_main(
            [
                "--campaign-root",
                str(self.root),
                "--case-id",
                "0",
                "--scheduler",
                "slurm",
                "--scheduler-job-id",
                "12345",
                "--submit-command",
                "sbatch submit_fake_array.sh",
                "--environment-name",
                "campaign-workflow-py310",
            ]
        )

        self.assertEqual(rc, 0)

        state = read_json(case_dir / "state.json")
        validation = read_json(case_dir / "validation.json")
        marker = read_json(case_dir / "post/sim_submitted.json")

        self.assertEqual(state["state"], "Submitted")
        self.assertEqual(state["history"][-1]["from"], "Created")
        self.assertEqual(state["history"][-1]["to"], "Submitted")
        self.assertEqual(state["history"][-1]["operation"], "mark_sim_submitted")

        self.assertTrue(marker["ok"])
        self.assertEqual(marker["operation"], "mark_sim_submitted")
        self.assertEqual(marker["scheduler"], "slurm")
        self.assertEqual(marker["scheduler_job_id"], "12345")
        self.assertEqual(marker["submit_command"], "sbatch submit_fake_array.sh")
        self.assertEqual(marker["environment_name"], "campaign-workflow-py310")
        self.assertEqual(marker["destructive_operations"], 0)
        self.assertIn("submitted_at", marker)

        submitted = validation["simulation"]["mark_sim_submitted"]
        self.assertTrue(submitted["ok"])
        self.assertEqual(submitted["operation"], "mark_sim_submitted")
        self.assertEqual(submitted["state_from"], "Created")
        self.assertEqual(submitted["state_to"], "Submitted")
        self.assertEqual(submitted["marker_path"], "post/sim_submitted.json")
        self.assertEqual(submitted["destructive_operations"], 0)

        latest = validation["simulation"]["latest"]
        self.assertTrue(latest["ok"])
        self.assertEqual(latest["operation"], "mark_sim_submitted")
        self.assertEqual(latest["state_from"], "Created")
        self.assertEqual(latest["state_to"], "Submitted")
        self.assertEqual(latest["marker_path"], "post/sim_submitted.json")

        self.assertFalse(validation["cleanup"]["cleanup_allowed"])

    def test_mark_running_after_submitted_writes_marker_state_and_validation_evidence(self) -> None:
        case_dir = self.root / "000_fake_case"
        self._mark_submitted(0)

        rc = mark_sim_running_main(
            [
                "--campaign-root",
                str(self.root),
                "--case-id",
                "0",
                "--scheduler",
                "slurm",
                "--scheduler-job-id",
                "12345",
                "--scheduler-array-task-id",
                "0",
                "--run-command",
                "srun -n 24 python input.py 2",
                "--environment-name",
                "warpx-26.05-py314",
                "--stdout-log",
                "logs/fake.out",
                "--stderr-log",
                "logs/fake.err",
            ]
        )

        self.assertEqual(rc, 0)

        state = read_json(case_dir / "state.json")
        validation = read_json(case_dir / "validation.json")
        marker = read_json(case_dir / "post/sim_running.json")

        self.assertEqual(state["state"], "Running")
        self.assertEqual(state["history"][-1]["from"], "Submitted")
        self.assertEqual(state["history"][-1]["to"], "Running")
        self.assertEqual(state["history"][-1]["operation"], "mark_sim_running")

        self.assertTrue(marker["ok"])
        self.assertEqual(marker["operation"], "mark_sim_running")
        self.assertEqual(marker["scheduler"], "slurm")
        self.assertEqual(marker["scheduler_job_id"], "12345")
        self.assertEqual(marker["scheduler_array_task_id"], "0")
        self.assertEqual(marker["run_command"], "srun -n 24 python input.py 2")
        self.assertEqual(marker["environment_name"], "warpx-26.05-py314")
        self.assertEqual(marker["stdout_log"], "logs/fake.out")
        self.assertEqual(marker["stderr_log"], "logs/fake.err")
        self.assertEqual(marker["destructive_operations"], 0)
        self.assertIn("started_at", marker)

        running = validation["simulation"]["mark_sim_running"]
        self.assertTrue(running["ok"])
        self.assertEqual(running["operation"], "mark_sim_running")
        self.assertEqual(running["state_from"], "Submitted")
        self.assertEqual(running["state_to"], "Running")
        self.assertEqual(running["marker_path"], "post/sim_running.json")
        self.assertEqual(running["run_command"], "srun -n 24 python input.py 2")

        latest = validation["simulation"]["latest"]
        self.assertEqual(latest["operation"], "mark_sim_running")
        self.assertEqual(latest["state_from"], "Submitted")
        self.assertEqual(latest["state_to"], "Running")
        self.assertEqual(latest["marker_path"], "post/sim_running.json")

        self.assertFalse(validation["cleanup"]["cleanup_allowed"])

    def test_mark_failed_after_running_writes_failure_marker_state_and_validation_evidence(self) -> None:
        case_dir = self.root / "000_fake_case"
        self._mark_submitted(0)
        self._mark_running(0)

        rc = mark_sim_failed_main(
            [
                "--campaign-root",
                str(self.root),
                "--case-id",
                "0",
                "--scheduler",
                "slurm",
                "--scheduler-job-id",
                "12345",
                "--scheduler-array-task-id",
                "0",
                "--run-command",
                "srun -n 24 python input.py 2",
                "--environment-name",
                "warpx-26.05-py314",
                "--stdout-log",
                "logs/fake.out",
                "--stderr-log",
                "logs/fake.err",
                "--return-code",
                "42",
                "--error",
                "fake simulation failure",
            ]
        )

        self.assertEqual(rc, 0)

        state = read_json(case_dir / "state.json")
        validation = read_json(case_dir / "validation.json")
        marker = read_json(case_dir / "post/sim_failed.json")

        self.assertEqual(state["state"], "Failed")
        self.assertEqual(state["history"][-1]["from"], "Running")
        self.assertEqual(state["history"][-1]["to"], "Failed")
        self.assertEqual(state["history"][-1]["operation"], "mark_sim_failed")

        self.assertFalse(marker["ok"])
        self.assertEqual(marker["operation"], "mark_sim_failed")
        self.assertEqual(marker["return_code"], 42)
        self.assertEqual(marker["errors"], ["fake simulation failure"])
        self.assertEqual(marker["scheduler"], "slurm")
        self.assertEqual(marker["scheduler_job_id"], "12345")
        self.assertEqual(marker["scheduler_array_task_id"], "0")
        self.assertEqual(marker["run_command"], "srun -n 24 python input.py 2")
        self.assertEqual(marker["environment_name"], "warpx-26.05-py314")
        self.assertEqual(marker["stdout_log"], "logs/fake.out")
        self.assertEqual(marker["stderr_log"], "logs/fake.err")
        self.assertEqual(marker["destructive_operations"], 0)
        self.assertIn("finished_at", marker)

        failed = validation["simulation"]["mark_sim_failed"]
        self.assertFalse(failed["ok"])
        self.assertEqual(failed["operation"], "mark_sim_failed")
        self.assertEqual(failed["state_from"], "Running")
        self.assertEqual(failed["state_to"], "Failed")
        self.assertEqual(failed["marker_path"], "post/sim_failed.json")
        self.assertEqual(failed["return_code"], 42)
        self.assertEqual(failed["errors"], ["fake simulation failure"])

        latest = validation["simulation"]["latest"]
        self.assertFalse(latest["ok"])
        self.assertEqual(latest["operation"], "mark_sim_failed")
        self.assertEqual(latest["state_from"], "Running")
        self.assertEqual(latest["state_to"], "Failed")
        self.assertEqual(latest["marker_path"], "post/sim_failed.json")

        self.assertFalse(validation["cleanup"]["cleanup_allowed"])

    def test_dry_run_does_not_write_submitted_marker_state_or_validation(self) -> None:
        case_dir = self.root / "000_fake_case"

        rc = mark_sim_submitted_main(
            [
                "--campaign-root",
                str(self.root),
                "--case-id",
                "0",
                "--dry-run",
            ]
        )

        self.assertEqual(rc, 0)

        state = read_json(case_dir / "state.json")
        validation = read_json(case_dir / "validation.json")

        self.assertEqual(state["state"], "Created")
        self.assertFalse((case_dir / "post/sim_submitted.json").exists())
        self.assertNotIn("simulation", validation)
        self.assertFalse(validation["cleanup"]["cleanup_allowed"])

    def test_running_from_created_is_rejected_without_writing(self) -> None:
        case_dir = self.root / "000_fake_case"

        rc = mark_sim_running_main(
            [
                "--campaign-root",
                str(self.root),
                "--case-id",
                "0",
            ]
        )

        self.assertEqual(rc, 1)

        state = read_json(case_dir / "state.json")
        validation = read_json(case_dir / "validation.json")

        self.assertEqual(state["state"], "Created")
        self.assertFalse((case_dir / "post/sim_running.json").exists())
        self.assertNotIn("simulation", validation)
        self.assertFalse(validation["cleanup"]["cleanup_allowed"])

    def test_failed_from_submitted_is_rejected_without_writing(self) -> None:
        case_dir = self.root / "000_fake_case"
        self._mark_submitted(0)

        rc = mark_sim_failed_main(
            [
                "--campaign-root",
                str(self.root),
                "--case-id",
                "0",
                "--return-code",
                "1",
                "--error",
                "failed before running",
            ]
        )

        self.assertEqual(rc, 1)

        state = read_json(case_dir / "state.json")
        validation = read_json(case_dir / "validation.json")

        self.assertEqual(state["state"], "Submitted")
        self.assertFalse((case_dir / "post/sim_failed.json").exists())
        self.assertNotIn("mark_sim_failed", validation["simulation"])
        self.assertEqual(validation["simulation"]["latest"]["operation"], "mark_sim_submitted")

    def _mark_submitted(self, case_id: int) -> None:
        rc = mark_sim_submitted_main(
            [
                "--campaign-root",
                str(self.root),
                "--case-id",
                str(case_id),
            ]
        )
        self.assertEqual(rc, 0)

    def _mark_running(self, case_id: int) -> None:
        rc = mark_sim_running_main(
            [
                "--campaign-root",
                str(self.root),
                "--case-id",
                str(case_id),
            ]
        )
        self.assertEqual(rc, 0)

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

        (self.root / "campaign.json").write_text(
            json.dumps(campaign, indent=2) + "\n",
            encoding="utf-8",
        )
        (self.root / "cases.tsv").write_text(
            "CASE_ID\tCASE_NAME\tKIND\n"
            "0\t000_fake_case\talpha\n"
            "1\t001_fake_case\tbeta\n"
            "2\t002_fake_case\tgamma\n",
            encoding="utf-8",
        )


if __name__ == "__main__":
    unittest.main()