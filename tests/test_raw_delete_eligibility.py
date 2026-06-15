from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path

from campaign_workflow.cli.init_case_states import main as init_case_states_main
from campaign_workflow.cli.mark_raw_delete_eligible import main as mark_raw_delete_eligible_main
from campaign_workflow.core.atomic_io import read_json, write_json_atomic
from campaign_workflow.core.state import now_utc


class RawDeleteEligibilityTests(unittest.TestCase):
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

    def test_marks_reduced_validated_case_as_raw_delete_eligible(self) -> None:
        case_dir = self.root / "000_fake_case"
        self._write_file(case_dir, "diags/raw_000.fake", b"a" * 10)
        self._force_state(case_dir, "Reduced_validated")
        self._mark_raw_and_reduced_ok(case_dir)

        rc = mark_raw_delete_eligible_main(
            [
                "--campaign-root",
                str(self.root),
                "--case-id",
                "0",
            ]
        )

        self.assertEqual(rc, 0)

        state = read_json(case_dir / "state.json")
        validation = read_json(case_dir / "validation.json")
        marker = read_json(case_dir / "post/raw_delete_eligible.json")

        self.assertEqual(state["state"], "Raw_delete_eligible")
        self.assertEqual(state["history"][-1]["operation"], "mark_raw_delete_eligible")
        self.assertTrue(validation["cleanup"]["cleanup_allowed"])
        self.assertEqual(validation["cleanup"]["candidate_file_count"], 1)
        self.assertEqual(validation["cleanup"]["candidate_total_size_bytes"], 10)
        self.assertEqual(validation["cleanup"]["destructive_operations"], 0)

        self.assertTrue(marker["cleanup_allowed"])
        self.assertTrue(marker["cleanup_requires_dry_run_manifest"])
        self.assertTrue(marker["cleanup_requires_explicit_execute"])
        self.assertEqual(marker["candidate_file_count"], 1)
        self.assertEqual(marker["candidate_total_size_bytes"], 10)
        self.assertEqual(marker["destructive_operations"], 0)

        self.assertTrue((case_dir / "diags/raw_000.fake").exists())

    def test_dry_run_does_not_write_or_change_state(self) -> None:
        case_dir = self.root / "000_fake_case"
        self._write_file(case_dir, "diags/raw_000.fake", b"a" * 10)
        self._force_state(case_dir, "Reduced_validated")
        self._mark_raw_and_reduced_ok(case_dir)

        rc = mark_raw_delete_eligible_main(
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

        self.assertEqual(state["state"], "Reduced_validated")
        self.assertFalse(validation["cleanup"]["cleanup_allowed"])
        self.assertFalse((case_dir / "post/raw_delete_eligible.json").exists())
        self.assertTrue((case_dir / "diags/raw_000.fake").exists())

    def test_missing_raw_evidence_is_rejected(self) -> None:
        case_dir = self.root / "000_fake_case"
        self._write_file(case_dir, "diags/raw_000.fake", b"a" * 10)
        self._force_state(case_dir, "Reduced_validated")
        self._mark_reduced_ok_only(case_dir)

        rc = mark_raw_delete_eligible_main(
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

        self.assertEqual(state["state"], "Reduced_validated")
        self.assertFalse(validation["cleanup"]["cleanup_allowed"])
        self.assertFalse((case_dir / "post/raw_delete_eligible.json").exists())
        self.assertTrue((case_dir / "diags/raw_000.fake").exists())

    def test_missing_reduced_evidence_is_rejected(self) -> None:
        case_dir = self.root / "000_fake_case"
        self._write_file(case_dir, "diags/raw_000.fake", b"a" * 10)
        self._force_state(case_dir, "Reduced_validated")
        self._mark_raw_ok_only(case_dir)

        rc = mark_raw_delete_eligible_main(
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

        self.assertEqual(state["state"], "Reduced_validated")
        self.assertFalse(validation["cleanup"]["cleanup_allowed"])
        self.assertFalse((case_dir / "post/raw_delete_eligible.json").exists())
        self.assertTrue((case_dir / "diags/raw_000.fake").exists())

    def test_legacy_reduced_only_is_rejected(self) -> None:
        case_dir = self.root / "000_fake_case"
        self._write_file(case_dir, "diags/raw_000.fake", b"a" * 10)
        self._force_state(case_dir, "Reduced_validated")
        self._mark_raw_and_reduced_ok(case_dir)

        validation = read_json(case_dir / "validation.json")
        validation["legacy"] = {
            "schema_version": 1,
            "legacy_reduced_only": True,
            "raw_evidence_mode": "legacy_reduced_only",
            "raw_evidence_ok": False,
            "cleanup_allowed": False,
            "operation": "validate_reduced_case",
            "reason": "test legacy marker",
        }
        write_json_atomic(case_dir / "validation.json", validation)

        rc = mark_raw_delete_eligible_main(
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

        self.assertEqual(state["state"], "Reduced_validated")
        self.assertFalse(validation["cleanup"]["cleanup_allowed"])
        self.assertFalse((case_dir / "post/raw_delete_eligible.json").exists())
        self.assertTrue((case_dir / "diags/raw_000.fake").exists())

    def test_no_cleanup_files_is_rejected(self) -> None:
        case_dir = self.root / "000_fake_case"
        self._force_state(case_dir, "Reduced_validated")
        self._mark_raw_and_reduced_ok(case_dir)

        rc = mark_raw_delete_eligible_main(
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

        self.assertEqual(state["state"], "Reduced_validated")
        self.assertFalse(validation["cleanup"]["cleanup_allowed"])
        self.assertFalse((case_dir / "post/raw_delete_eligible.json").exists())

    def test_created_state_is_rejected(self) -> None:
        case_dir = self.root / "000_fake_case"
        self._write_file(case_dir, "diags/raw_000.fake", b"a" * 10)
        self._mark_raw_and_reduced_ok(case_dir)

        rc = mark_raw_delete_eligible_main(
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
        self.assertFalse(validation["cleanup"]["cleanup_allowed"])
        self.assertFalse((case_dir / "post/raw_delete_eligible.json").exists())
        self.assertTrue((case_dir / "diags/raw_000.fake").exists())

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

    def _write_file(self, case_dir: Path, relative_path: str, payload: bytes) -> None:
        path = case_dir / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(payload)

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

    def _mark_raw_ok_only(self, case_dir: Path) -> None:
        validation = read_json(case_dir / "validation.json")
        validation["raw"] = {
            "fake_raw": {
                "schema_version": 1,
                "diagnostic_name": "fake_raw",
                "diagnostic_kind": "fake",
                "ok": True,
                "validated_at": now_utc(),
                "manifest_path": "manifests/raw_fake_raw.json",
                "file_count": 1,
                "total_size_bytes": 10,
                "errors": [],
                "warnings": [],
            }
        }
        validation["cleanup"] = {
            "cleanup_allowed": False,
            "reason": "test fixture",
        }
        validation["updated_at"] = now_utc()
        write_json_atomic(case_dir / "validation.json", validation)

    def _mark_reduced_ok_only(self, case_dir: Path) -> None:
        validation = read_json(case_dir / "validation.json")
        validation["reduced"] = {
            "fake_metrics": {
                "schema_version": 1,
                "output_name": "fake_metrics",
                "output_kind": "csv",
                "ok": True,
                "validated_at": now_utc(),
                "path": "post/fake_metrics.csv",
                "row_count": 1,
                "columns": ["iteration", "score"],
                "errors": [],
                "warnings": [],
            }
        }
        validation["cleanup"] = {
            "cleanup_allowed": False,
            "reason": "test fixture",
        }
        validation["updated_at"] = now_utc()
        write_json_atomic(case_dir / "validation.json", validation)

    def _mark_raw_and_reduced_ok(self, case_dir: Path) -> None:
        self._mark_raw_ok_only(case_dir)

        validation = read_json(case_dir / "validation.json")
        validation["reduced"] = {
            "fake_metrics": {
                "schema_version": 1,
                "output_name": "fake_metrics",
                "output_kind": "csv",
                "ok": True,
                "validated_at": now_utc(),
                "path": "post/fake_metrics.csv",
                "row_count": 1,
                "columns": ["iteration", "score"],
                "errors": [],
                "warnings": [],
            }
        }
        validation["cleanup"] = {
            "cleanup_allowed": False,
            "reason": "Reduced validation succeeded, but cleanup requires a later explicit eligibility phase.",
        }
        validation["updated_at"] = now_utc()
        write_json_atomic(case_dir / "validation.json", validation)


if __name__ == "__main__":
    unittest.main()