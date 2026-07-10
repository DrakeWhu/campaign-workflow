from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path

from campaign_workflow.cli.init_case_states import main as init_case_states_main
from campaign_workflow.cli.validate_raw_case import main as validate_raw_case_main
from campaign_workflow.cli.validate_reduced_case import (
    main as validate_reduced_case_main,
)
from campaign_workflow.core.atomic_io import read_json, write_json_atomic
from campaign_workflow.core.state import now_utc


class ReducedValidationTests(unittest.TestCase):
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

    def test_successful_reduced_validation_writes_summary_and_state(self) -> None:
        case_dir = self.root / "000_fake_case"
        self._prepare_raw_validated_case(case_dir)
        self._write_csv(case_dir, "post/fake_metrics.csv", "iteration,score\n0,1.5\n")

        rc = validate_reduced_case_main(
            ["--campaign-root", str(self.root), "--case-id", "0"]
        )

        self.assertEqual(rc, 0)

        state = read_json(case_dir / "state.json")
        validation = read_json(case_dir / "validation.json")

        self.assertEqual(state["state"], "Reduced_validated")
        self.assertEqual(state["history"][-1]["operation"], "validate_reduced_case")
        self.assertTrue(validation["reduced"]["fake_metrics"]["ok"])
        self.assertEqual(validation["reduced"]["fake_metrics"]["output_kind"], "csv")
        self.assertEqual(validation["reduced"]["fake_metrics"]["row_count"], 1)
        self.assertEqual(
            validation["reduced"]["fake_metrics"]["columns"], ["iteration", "score"]
        )
        self.assertEqual(
            validation["reduced"]["fake_metrics"]["file"]["relative_path"],
            "post/fake_metrics.csv",
        )
        self.assertFalse(validation["cleanup"]["cleanup_allowed"])

    def test_required_png_file_validates_by_fixed_file_contract(self) -> None:
        self._add_required_png_output()
        case_dir = self.root / "000_fake_case"
        self._prepare_raw_validated_case(case_dir)
        self._write_csv(case_dir, "post/fake_metrics.csv", "iteration,score\n0,1.5\n")
        self._write_bytes(
            case_dir,
            "post/plots/electrons/longitudinal_phase_space_z_pz.png",
            b"\x89PNG\r\n\x1a\n",
        )

        rc = validate_reduced_case_main(
            ["--campaign-root", str(self.root), "--case-id", "0"]
        )

        self.assertEqual(rc, 0)
        state = read_json(case_dir / "state.json")
        validation = read_json(case_dir / "validation.json")
        plot = validation["reduced"]["particle_phase_space_z_pz"]

        self.assertEqual(state["state"], "Reduced_validated")
        self.assertTrue(plot["ok"])
        self.assertEqual(plot["output_kind"], "file")
        self.assertEqual(plot["min_bytes"], 1)
        self.assertEqual(plot["row_count"], 0)
        self.assertEqual(
            plot["file"]["relative_path"],
            "post/plots/electrons/longitudinal_phase_space_z_pz.png",
        )
        self.assertFalse(validation["cleanup"]["cleanup_allowed"])

    def test_empty_required_png_file_marks_validation_failed(self) -> None:
        self._add_required_png_output()
        case_dir = self.root / "000_fake_case"
        self._prepare_raw_validated_case(case_dir)
        self._write_csv(case_dir, "post/fake_metrics.csv", "iteration,score\n0,1.5\n")
        self._write_bytes(
            case_dir,
            "post/plots/electrons/longitudinal_phase_space_z_pz.png",
            b"",
        )

        rc = validate_reduced_case_main(
            ["--campaign-root", str(self.root), "--case-id", "0"]
        )

        self.assertEqual(rc, 1)
        validation = read_json(case_dir / "validation.json")
        plot = validation["reduced"]["particle_phase_space_z_pz"]
        self.assertFalse(plot["ok"])
        self.assertIn("empty reduced output", "\n".join(plot["errors"]))

    def test_dry_run_does_not_change_state_or_validation(self) -> None:
        case_dir = self.root / "000_fake_case"
        self._prepare_raw_validated_case(case_dir)
        self._write_csv(case_dir, "post/fake_metrics.csv", "iteration,score\n0,1.5\n")

        rc = validate_reduced_case_main(
            ["--campaign-root", str(self.root), "--case-id", "0", "--dry-run"]
        )

        self.assertEqual(rc, 0)

        state = read_json(case_dir / "state.json")
        validation = read_json(case_dir / "validation.json")

        self.assertEqual(state["state"], "Raw_validated")
        self.assertEqual(validation["reduced"], {})
        self.assertFalse(validation["cleanup"]["cleanup_allowed"])

    def test_missing_required_csv_marks_validation_failed(self) -> None:
        case_dir = self.root / "000_fake_case"
        self._prepare_raw_validated_case(case_dir)

        rc = validate_reduced_case_main(
            ["--campaign-root", str(self.root), "--case-id", "0"]
        )

        self.assertEqual(rc, 1)

        state = read_json(case_dir / "state.json")
        validation = read_json(case_dir / "validation.json")

        self.assertEqual(state["state"], "Validation_failed")
        self.assertFalse(validation["reduced"]["fake_metrics"]["ok"])
        joined_errors = "\n".join(validation["reduced"]["fake_metrics"]["errors"])
        self.assertIn("does not exist", joined_errors)

    def test_missing_required_column_marks_validation_failed(self) -> None:
        case_dir = self.root / "000_fake_case"
        self._prepare_raw_validated_case(case_dir)
        self._write_csv(case_dir, "post/fake_metrics.csv", "iteration,energy\n0,1.5\n")

        rc = validate_reduced_case_main(
            ["--campaign-root", str(self.root), "--case-id", "0"]
        )

        self.assertEqual(rc, 1)

        state = read_json(case_dir / "state.json")
        validation = read_json(case_dir / "validation.json")

        self.assertEqual(state["state"], "Validation_failed")
        self.assertFalse(validation["reduced"]["fake_metrics"]["ok"])
        joined_errors = "\n".join(validation["reduced"]["fake_metrics"]["errors"])
        self.assertIn("missing required column", joined_errors)
        self.assertIn("score", joined_errors)

    def test_created_state_is_rejected_without_writing(self) -> None:
        case_dir = self.root / "000_fake_case"
        self._write_csv(case_dir, "post/fake_metrics.csv", "iteration,score\n0,1.5\n")

        rc = validate_reduced_case_main(
            ["--campaign-root", str(self.root), "--case-id", "0"]
        )

        self.assertEqual(rc, 1)

        state = read_json(case_dir / "state.json")
        validation = read_json(case_dir / "validation.json")

        self.assertEqual(state["state"], "Created")
        self.assertEqual(validation["reduced"], {})

    def test_legacy_reduced_only_validates_created_case_without_raw_evidence(
        self,
    ) -> None:
        case_dir = self.root / "000_fake_case"
        self._write_csv(case_dir, "post/fake_metrics.csv", "iteration,score\n0,1.5\n")

        rc = validate_reduced_case_main(
            [
                "--campaign-root",
                str(self.root),
                "--case-id",
                "0",
                "--legacy-reduced-only",
            ]
        )

        self.assertEqual(rc, 0)

        state = read_json(case_dir / "state.json")
        validation = read_json(case_dir / "validation.json")

        self.assertEqual(state["state"], "Reduced_validated")
        self.assertEqual(state["history"][-1]["operation"], "validate_reduced_case")
        self.assertIn("legacy reduced-only", state["history"][-1]["reason"])

        self.assertEqual(validation["raw"], {})
        self.assertTrue(validation["reduced"]["fake_metrics"]["ok"])
        self.assertTrue(validation["reduced"]["fake_metrics"]["legacy_reduced_only"])
        self.assertEqual(validation["reduced"]["fake_metrics"]["row_count"], 1)

        self.assertTrue(validation["legacy"]["legacy_reduced_only"])
        self.assertEqual(
            validation["legacy"]["raw_evidence_mode"], "legacy_reduced_only"
        )
        self.assertFalse(validation["legacy"]["raw_evidence_ok"])
        self.assertFalse(validation["legacy"]["cleanup_allowed"])

        self.assertFalse(validation["cleanup"]["cleanup_allowed"])
        self.assertIn("Legacy reduced-only", validation["cleanup"]["reason"])

    def test_legacy_reduced_only_dry_run_does_not_write(self) -> None:
        case_dir = self.root / "000_fake_case"
        self._write_csv(case_dir, "post/fake_metrics.csv", "iteration,score\n0,1.5\n")

        rc = validate_reduced_case_main(
            [
                "--campaign-root",
                str(self.root),
                "--case-id",
                "0",
                "--legacy-reduced-only",
                "--dry-run",
            ]
        )

        self.assertEqual(rc, 0)

        state = read_json(case_dir / "state.json")
        validation = read_json(case_dir / "validation.json")

        self.assertEqual(state["state"], "Created")
        self.assertEqual(validation["raw"], {})
        self.assertEqual(validation["reduced"], {})
        self.assertNotIn("legacy", validation)
        self.assertFalse(validation["cleanup"]["cleanup_allowed"])

    def test_legacy_reduced_only_missing_required_csv_marks_validation_failed(
        self,
    ) -> None:
        case_dir = self.root / "000_fake_case"

        rc = validate_reduced_case_main(
            [
                "--campaign-root",
                str(self.root),
                "--case-id",
                "0",
                "--legacy-reduced-only",
            ]
        )

        self.assertEqual(rc, 1)

        state = read_json(case_dir / "state.json")
        validation = read_json(case_dir / "validation.json")

        self.assertEqual(state["state"], "Validation_failed")
        self.assertEqual(validation["raw"], {})
        self.assertFalse(validation["reduced"]["fake_metrics"]["ok"])
        self.assertTrue(validation["reduced"]["fake_metrics"]["legacy_reduced_only"])

        joined_errors = "\n".join(validation["reduced"]["fake_metrics"]["errors"])
        self.assertIn("does not exist", joined_errors)

        self.assertTrue(validation["legacy"]["legacy_reduced_only"])
        self.assertFalse(validation["legacy"]["raw_evidence_ok"])
        self.assertFalse(validation["cleanup"]["cleanup_allowed"])
        self.assertIn(
            "Legacy reduced-only validation failed", validation["cleanup"]["reason"]
        )

    def test_legacy_reduced_only_revalidates_existing_reduced_validated_case(
        self,
    ) -> None:
        case_dir = self.root / "000_fake_case"
        self._write_csv(case_dir, "post/fake_metrics.csv", "iteration,score\n0,1.5\n")

        rc_first = validate_reduced_case_main(
            [
                "--campaign-root",
                str(self.root),
                "--case-id",
                "0",
                "--legacy-reduced-only",
            ]
        )
        self.assertEqual(rc_first, 0)

        rc_second = validate_reduced_case_main(
            [
                "--campaign-root",
                str(self.root),
                "--case-id",
                "0",
                "--legacy-reduced-only",
            ]
        )
        self.assertEqual(rc_second, 0)

        state = read_json(case_dir / "state.json")
        validation = read_json(case_dir / "validation.json")

        self.assertEqual(state["state"], "Reduced_validated")
        self.assertTrue(validation["reduced"]["fake_metrics"]["ok"])
        self.assertTrue(validation["legacy"]["legacy_reduced_only"])
        self.assertFalse(validation["cleanup"]["cleanup_allowed"])

    def test_missing_raw_validation_evidence_is_rejected_without_writing(self) -> None:
        case_dir = self.root / "000_fake_case"
        self._force_state(case_dir, "Raw_validated")
        self._write_csv(case_dir, "post/fake_metrics.csv", "iteration,score\n0,1.5\n")

        rc = validate_reduced_case_main(
            ["--campaign-root", str(self.root), "--case-id", "0"]
        )

        self.assertEqual(rc, 1)

        state = read_json(case_dir / "state.json")
        validation = read_json(case_dir / "validation.json")

        self.assertEqual(state["state"], "Raw_validated")
        self.assertEqual(validation["reduced"], {})

    def test_optional_csv_missing_does_not_fail_case(self) -> None:
        case_dir = self.root / "000_fake_case"
        self._prepare_raw_validated_case(case_dir)
        self._write_csv(case_dir, "post/fake_metrics.csv", "iteration,score\n0,1.5\n")

        rc = validate_reduced_case_main(
            ["--campaign-root", str(self.root), "--case-id", "0"]
        )

        self.assertEqual(rc, 0)

        state = read_json(case_dir / "state.json")
        validation = read_json(case_dir / "validation.json")

        self.assertEqual(state["state"], "Reduced_validated")
        self.assertTrue(validation["reduced"]["fake_metrics"]["ok"])
        self.assertFalse(validation["reduced"]["optional_metrics"]["ok"])
        self.assertFalse(validation["reduced"]["optional_metrics"]["required"])

    def test_symlink_escape_is_rejected_when_supported(self) -> None:
        case_dir = self.root / "000_fake_case"
        self._prepare_raw_validated_case(case_dir)

        outside = Path(self.tmpdir.name) / "outside.csv"
        outside.write_text("iteration,score\n0,1.5\n", encoding="utf-8")

        link_path = case_dir / "post/fake_metrics.csv"
        link_path.parent.mkdir(parents=True, exist_ok=True)

        try:
            link_path.symlink_to(outside)
        except (OSError, NotImplementedError) as exc:
            self.skipTest(
                f"symlink creation is not available in this environment: {exc}"
            )

        rc = validate_reduced_case_main(
            ["--campaign-root", str(self.root), "--case-id", "0"]
        )

        self.assertEqual(rc, 1)

        state = read_json(case_dir / "state.json")
        validation = read_json(case_dir / "validation.json")

        self.assertEqual(state["state"], "Validation_failed")
        self.assertFalse(validation["reduced"]["fake_metrics"]["ok"])
        joined_errors = "\n".join(validation["reduced"]["fake_metrics"]["errors"])
        self.assertIn("escapes case directory", joined_errors)

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
                    },
                    {
                        "name": "optional_metrics",
                        "kind": "csv",
                        "path": "post/optional_metrics.csv",
                        "min_rows": 1,
                        "required_columns": ["iteration"],
                        "required": False,
                    },
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
            json.dumps(campaign, indent=2) + "\n", encoding="utf-8"
        )
        (self.root / "cases.tsv").write_text(
            "CASE_ID\tCASE_NAME\tKIND\n"
            "0\t000_fake_case\talpha\n"
            "1\t001_fake_case\tbeta\n"
            "2\t002_fake_case\tgamma\n",
            encoding="utf-8",
        )

    def _add_required_png_output(self) -> None:
        campaign_path = self.root / "campaign.json"
        campaign = json.loads(campaign_path.read_text(encoding="utf-8"))
        campaign["analysis"]["outputs"].append(
            {
                "name": "particle_phase_space_z_pz",
                "kind": "file",
                "path": "post/plots/electrons/longitudinal_phase_space_z_pz.png",
                "allowed_suffixes": [".png"],
                "min_bytes": 1,
                "required": True,
            }
        )
        campaign_path.write_text(
            json.dumps(campaign, indent=2) + "\n", encoding="utf-8"
        )

    def _prepare_raw_validated_case(self, case_dir: Path) -> None:
        self._force_state(case_dir, "Sim_done")
        self._write_raw(case_dir, "diags/raw_000.fake", b"fake payload")

        rc = validate_raw_case_main(
            ["--campaign-root", str(self.root), "--case-id", "0"]
        )
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

    def _write_raw(self, case_dir: Path, relative_path: str, payload: bytes) -> None:
        path = case_dir / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(payload)

    def _write_csv(self, case_dir: Path, relative_path: str, text: str) -> None:
        path = case_dir / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")

    def _write_bytes(self, case_dir: Path, relative_path: str, payload: bytes) -> None:
        path = case_dir / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(payload)


if __name__ == "__main__":
    unittest.main()
