from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from campaign_workflow.cli.init_case_states import main as init_case_states_main
from campaign_workflow.cli.mark_sim_done import main as mark_sim_done_main
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

    def test_marks_created_case_sim_done_from_raw_diagnostic_evidence(self) -> None:
        case_dir = self.root / "000_fake_case"
        self._write_raw(case_dir, "diags/raw_000.fake", b"fake payload")

        rc = mark_sim_done_main(["--campaign-root", str(self.root), "--case-id", "0"])

        self.assertEqual(rc, 0)

        state = read_json(case_dir / "state.json")
        validation = read_json(case_dir / "validation.json")

        self.assertEqual(state["state"], "Sim_done")
        self.assertEqual(state["history"][-1]["from"], "Created")
        self.assertEqual(state["history"][-1]["to"], "Sim_done")
        self.assertEqual(state["history"][-1]["operation"], "mark_sim_done")

        self.assertTrue(validation["simulation"]["ok"])
        self.assertEqual(validation["simulation"]["operation"], "mark_sim_done")
        self.assertGreaterEqual(len(validation["simulation"]["evidence"]), 1)
        self.assertEqual(validation["raw"], {})
        self.assertFalse(validation["cleanup"]["cleanup_allowed"])

    def test_dry_run_does_not_change_state_or_validation(self) -> None:
        case_dir = self.root / "000_fake_case"
        self._write_raw(case_dir, "diags/raw_000.fake", b"fake payload")

        rc = mark_sim_done_main(["--campaign-root", str(self.root), "--case-id", "0", "--dry-run"])

        self.assertEqual(rc, 0)

        state = read_json(case_dir / "state.json")
        validation = read_json(case_dir / "validation.json")

        self.assertEqual(state["state"], "Created")
        self.assertNotIn("simulation", validation)
        self.assertEqual(validation["raw"], {})
        self.assertFalse(validation["cleanup"]["cleanup_allowed"])

    def test_missing_completion_evidence_fails_without_writing(self) -> None:
        case_dir = self.root / "000_fake_case"

        rc = mark_sim_done_main(["--campaign-root", str(self.root), "--case-id", "0"])

        self.assertEqual(rc, 1)

        state = read_json(case_dir / "state.json")
        validation = read_json(case_dir / "validation.json")

        self.assertEqual(state["state"], "Created")
        self.assertNotIn("simulation", validation)
        self.assertEqual(validation["raw"], {})

    def test_completion_marker_is_sufficient_evidence(self) -> None:
        case_dir = self.root / "000_fake_case"
        marker = case_dir / "post/sim_done.json"
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text('{"ok": true}\n', encoding="utf-8")

        rc = mark_sim_done_main(["--campaign-root", str(self.root), "--case-id", "0"])

        self.assertEqual(rc, 0)

        state = read_json(case_dir / "state.json")
        validation = read_json(case_dir / "validation.json")

        self.assertEqual(state["state"], "Sim_done")
        joined_evidence = "\n".join(validation["simulation"]["evidence"])
        self.assertIn("completion marker exists", joined_evidence)

    def test_already_sim_done_case_is_noop_success(self) -> None:
        case_dir = self.root / "000_fake_case"
        self._write_raw(case_dir, "diags/raw_000.fake", b"fake payload")

        first = mark_sim_done_main(["--campaign-root", str(self.root), "--case-id", "0"])
        self.assertEqual(first, 0)

        state_before = read_json(case_dir / "state.json")
        history_len_before = len(state_before["history"])

        second = mark_sim_done_main(["--campaign-root", str(self.root), "--case-id", "0"])
        self.assertEqual(second, 0)

        state_after = read_json(case_dir / "state.json")
        self.assertEqual(state_after["state"], "Sim_done")
        self.assertEqual(len(state_after["history"]), history_len_before)

    def test_running_state_is_rejected_without_writing(self) -> None:
        case_dir = self.root / "000_fake_case"
        state_path = case_dir / "state.json"
        state = read_json(state_path)
        state["state"] = "Running"
        state_path.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")
        self._write_raw(case_dir, "diags/raw_000.fake", b"fake payload")

        rc = mark_sim_done_main(["--campaign-root", str(self.root), "--case-id", "0"])

        self.assertEqual(rc, 1)

        state_after = read_json(state_path)
        validation = read_json(case_dir / "validation.json")

        self.assertEqual(state_after["state"], "Running")
        self.assertNotIn("simulation", validation)

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