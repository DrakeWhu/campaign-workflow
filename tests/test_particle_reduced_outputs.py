from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path

from campaign_workflow.cli.init_case_states import main as init_case_states_main
from campaign_workflow.cli.validate_raw_case import main as validate_raw_case_main
from campaign_workflow.cli.validate_reduced_case import main as validate_reduced_case_main
from campaign_workflow.core.atomic_io import read_json, write_json_atomic
from campaign_workflow.core.state import now_utc


class ParticleReducedOutputsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmpdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tmpdir.name) / "fake_campaign"
        self.root.mkdir(parents=True)

    def tearDown(self) -> None:
        self.tmpdir.cleanup()

    def test_particle_summary_can_be_declared_required_and_validated(self) -> None:
        self._write_campaign(include_particle_output=True)
        self._init_states()
        case_dir = self.root / "000_fake_case"
        self._prepare_raw_validated_case(case_dir)
        self._write_csv(case_dir, "guiding_metrics.csv", "iteration,waist_um\n0,42.0\n")
        self._write_csv(
            case_dir,
            "particle_analysis/particle_summary.csv",
            "case_id,n_selected,energy_min_mev\n0,3,1.0\n",
        )

        rc = validate_reduced_case_main(["--campaign-root", str(self.root), "--case-id", "0"])

        self.assertEqual(rc, 0)
        validation = read_json(case_dir / "validation.json")
        self.assertTrue(validation["reduced"]["guiding_metrics"]["ok"])
        self.assertTrue(validation["reduced"]["particle_summary"]["ok"])
        self.assertTrue(validation["reduced"]["particle_summary"]["required"])
        self.assertEqual(validation["reduced"]["particle_summary"]["path"], "particle_analysis/particle_summary.csv")

    def test_legacy_campaign_without_particle_summary_declaration_still_validates(self) -> None:
        self._write_campaign(include_particle_output=False)
        self._init_states()
        case_dir = self.root / "000_fake_case"
        self._prepare_raw_validated_case(case_dir)
        self._write_csv(case_dir, "guiding_metrics.csv", "iteration,waist_um\n0,42.0\n")

        rc = validate_reduced_case_main(["--campaign-root", str(self.root), "--case-id", "0"])

        self.assertEqual(rc, 0)
        validation = read_json(case_dir / "validation.json")
        self.assertTrue(validation["reduced"]["guiding_metrics"]["ok"])
        self.assertNotIn("particle_summary", validation["reduced"])

    def _write_campaign(self, *, include_particle_output: bool) -> None:
        outputs = [
            {
                "name": "guiding_metrics",
                "kind": "csv",
                "path": "guiding_metrics.csv",
                "min_rows": 1,
                "required_columns": ["iteration", "waist_um"],
                "required": True,
            }
        ]

        if include_particle_output:
            outputs.append(
                {
                    "name": "particle_summary",
                    "kind": "csv",
                    "path": "particle_analysis/particle_summary.csv",
                    "min_rows": 1,
                    "required_columns": ["case_id", "n_selected"],
                    "required": True,
                }
            )

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
                "name": "guiding",
                "kind": "command",
                "adapter": "command",
                "inputs": ["fake_raw"],
                "command": ["python", "-c", "print('not used by validate_reduced_case')"],
                "outputs": outputs,
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
            "CASE_ID\tCASE_NAME\n0\t000_fake_case\n",
            encoding="utf-8",
        )

    def _init_states(self) -> None:
        rc = init_case_states_main(
            [
                "--campaign-root",
                str(self.root),
                "--create-missing-case-dirs",
            ]
        )
        self.assertEqual(rc, 0)

    def _prepare_raw_validated_case(self, case_dir: Path) -> None:
        self._force_state(case_dir, "Sim_done")
        path = case_dir / "diags" / "raw_000.fake"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"fake payload")

        rc = validate_raw_case_main(["--campaign-root", str(self.root), "--case-id", "0"])
        self.assertEqual(rc, 0)
        self.assertEqual(read_json(case_dir / "state.json")["state"], "Raw_validated")

    def _force_state(self, case_dir: Path, target_state: str) -> None:
        state_path = case_dir / "state.json"
        state = read_json(state_path)

        previous = state["state"]
        timestamp = now_utc()

        state["state"] = target_state
        state["updated_at"] = timestamp
        state.setdefault("history", []).append(
            {
                "timestamp": timestamp,
                "from": previous,
                "to": target_state,
                "operation": "test_force_state",
                "reason": "test fixture state setup",
                "actor": {
                    "user": "test",
                    "pid": os.getpid(),
                    "hostname": "test",
                },
            }
        )

        write_json_atomic(state_path, state)

    def _write_csv(self, case_dir: Path, relative_path: str, text: str) -> None:
        path = case_dir / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")


if __name__ == "__main__":
    unittest.main()