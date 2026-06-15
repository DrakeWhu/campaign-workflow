from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from campaign_workflow.cli.analyze_case import main as analyze_case_main
from campaign_workflow.cli.cleanup_raw_case import main as cleanup_raw_case_main
from campaign_workflow.cli.init_case_states import main as init_case_states_main
from campaign_workflow.cli.mark_raw_delete_eligible import main as mark_raw_delete_eligible_main
from campaign_workflow.cli.mark_sim_done import main as mark_sim_done_main
from campaign_workflow.cli.mark_sim_running import main as mark_sim_running_main
from campaign_workflow.cli.mark_sim_submitted import main as mark_sim_submitted_main
from campaign_workflow.cli.storage_snapshot import main as storage_snapshot_main
from campaign_workflow.cli.validate_raw_case import main as validate_raw_case_main
from campaign_workflow.core.atomic_io import read_json


class FullFakeWorkflowIntegrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmpdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tmpdir.name) / "fake_campaign"
        self.root.mkdir(parents=True)
        self._write_fake_campaign()

    def tearDown(self) -> None:
        self.tmpdir.cleanup()

    def test_fake_simulation_markers_to_cleanup_execute_happy_path(self) -> None:
        case_dir = self.root / "000_fake_case"
        raw_file = case_dir / "diags/raw_000.fake"

        rc = init_case_states_main(
            [
                "--campaign-root",
                str(self.root),
                "--create-missing-case-dirs",
            ]
        )
        self.assertEqual(rc, 0)
        self.assertEqual(read_json(case_dir / "state.json")["state"], "Created")

        rc = mark_sim_submitted_main(
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
        self.assertEqual(rc, 0)
        self.assertEqual(read_json(case_dir / "state.json")["state"], "Submitted")
        self.assertTrue((case_dir / "post/sim_submitted.json").exists())

        rc = mark_sim_running_main(
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
        self.assertEqual(rc, 0)
        self.assertEqual(read_json(case_dir / "state.json")["state"], "Running")
        self.assertTrue((case_dir / "post/sim_running.json").exists())

        self._simulate_external_raw_output(case_dir)
        self.assertTrue(raw_file.exists())

        rc = mark_sim_done_main(["--campaign-root", str(self.root), "--case-id", "0"])
        self.assertEqual(rc, 0)
        self.assertEqual(read_json(case_dir / "state.json")["state"], "Sim_done")
        self.assertTrue((case_dir / "post/sim_done.json").exists())

        rc = validate_raw_case_main(["--campaign-root", str(self.root), "--case-id", "0"])
        self.assertEqual(rc, 0)
        self.assertEqual(read_json(case_dir / "state.json")["state"], "Raw_validated")
        self.assertTrue((case_dir / "manifests/raw_fake_raw.json").exists())

        rc = analyze_case_main(["--campaign-root", str(self.root), "--case-id", "0"])
        self.assertEqual(rc, 0)
        self.assertEqual(read_json(case_dir / "state.json")["state"], "Reduced_validated")
        self.assertTrue((case_dir / "post/fake_metrics.csv").exists())
        self.assertTrue((case_dir / "post/analysis_done.json").exists())

        rc = storage_snapshot_main(["--campaign-root", str(self.root)])
        self.assertEqual(rc, 0)
        snapshot = read_json(self.root / "snapshots/storage_snapshot_latest.json")
        self.assertEqual(snapshot["case_count"], 1)
        self.assertEqual(snapshot["validated_cases"], 1)
        self.assertEqual(snapshot["submitted_cases"], 1)
        self.assertEqual(snapshot["raw_live_bytes"], raw_file.stat().st_size)
        self.assertEqual(snapshot["safe_cleanup_candidate_bytes"], raw_file.stat().st_size)
        self.assertEqual(snapshot["destructive_operations"], 0)

        rc = mark_raw_delete_eligible_main(["--campaign-root", str(self.root), "--case-id", "0"])
        self.assertEqual(rc, 0)
        self.assertEqual(read_json(case_dir / "state.json")["state"], "Raw_delete_eligible")
        self.assertTrue((case_dir / "post/raw_delete_eligible.json").exists())

        rc = cleanup_raw_case_main(["--campaign-root", str(self.root), "--case-id", "0", "--dry-run"])
        self.assertEqual(rc, 0)
        self.assertEqual(read_json(case_dir / "state.json")["state"], "Raw_delete_eligible")
        self.assertTrue(raw_file.exists())
        self.assertTrue((case_dir / "manifests/raw_delete_manifest.json").exists())

        rc = cleanup_raw_case_main(["--campaign-root", str(self.root), "--case-id", "0", "--execute"])
        self.assertEqual(rc, 0)

        state = read_json(case_dir / "state.json")
        validation = read_json(case_dir / "validation.json")
        raw_deleted = read_json(case_dir / "post/raw_deleted.json")

        self.assertEqual(state["state"], "Raw_deleted")
        self.assertFalse(raw_file.exists())
        self.assertTrue(case_dir.exists())
        self.assertTrue((case_dir / "diags").exists())
        self.assertTrue((case_dir / "post/fake_metrics.csv").exists())
        self.assertTrue((case_dir / "manifests/raw_delete_manifest.json").exists())

        self.assertFalse(validation["cleanup"]["cleanup_allowed"])
        self.assertTrue(validation["cleanup"]["raw_deleted"])
        self.assertEqual(validation["cleanup"]["deleted_file_count"], 1)
        self.assertEqual(validation["cleanup"]["destructive_operations"], 1)

        self.assertEqual(raw_deleted["deleted_file_count"], 1)
        self.assertEqual(raw_deleted["destructive_operations"], 1)
        self.assertEqual(raw_deleted["directory_delete_allowed"], False)

        history_edges = [(item.get("from"), item.get("to")) for item in state["history"]]
        self.assertIn((None, "Created"), history_edges)
        self.assertIn(("Created", "Submitted"), history_edges)
        self.assertIn(("Submitted", "Running"), history_edges)
        self.assertIn(("Running", "Sim_done"), history_edges)
        self.assertIn(("Sim_done", "Raw_validated"), history_edges)
        self.assertIn(("Raw_validated", "Analyzing"), history_edges)
        self.assertIn(("Analyzing", "Reduced_validated"), history_edges)
        self.assertIn(("Reduced_validated", "Raw_delete_eligible"), history_edges)
        self.assertIn(("Raw_delete_eligible", "Raw_deleted"), history_edges)

    def _simulate_external_raw_output(self, case_dir: Path) -> None:
        (case_dir / "logs").mkdir(parents=True, exist_ok=True)
        (case_dir / "diags").mkdir(parents=True, exist_ok=True)
        (case_dir / "logs/sim.out").write_text("fake simulation stdout\n", encoding="utf-8")
        (case_dir / "logs/sim.err").write_text("", encoding="utf-8")
        (case_dir / "diags/raw_000.fake").write_bytes(b"fake raw diagnostic payload")

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
                        "required_columns": ["iteration", "score"],
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
            "storage": {
                "safe_quota_GB": 100.0,
                "reserved_quota_GB": 20.0,
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
            "0\t000_fake_case\talpha\n",
            encoding="utf-8",
        )


if __name__ == "__main__":
    unittest.main()