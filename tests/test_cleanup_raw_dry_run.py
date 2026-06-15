from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path

from campaign_workflow.cli.cleanup_raw_case import main as cleanup_raw_case_main
from campaign_workflow.cli.init_case_states import main as init_case_states_main
from campaign_workflow.core.atomic_io import read_json, write_json_atomic
from campaign_workflow.core.state import now_utc


class CleanupRawDryRunTests(unittest.TestCase):
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

    def test_dry_run_manifest_is_written_without_deleting_files(self) -> None:
        case_dir = self.root / "000_fake_case"
        self._write_file(case_dir, "diags/raw_000.fake", b"a" * 10)
        self._make_raw_delete_eligible(case_dir, candidate_count=1, candidate_bytes=10)

        rc = cleanup_raw_case_main(
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
        manifest = read_json(case_dir / "manifests/raw_delete_manifest.json")

        self.assertEqual(state["state"], "Raw_delete_eligible")
        self.assertTrue((case_dir / "diags/raw_000.fake").exists())

        self.assertTrue(validation["cleanup"]["cleanup_allowed"])
        self.assertTrue(validation["cleanup"]["delete_manifest_ready"])
        self.assertEqual(validation["cleanup"]["delete_manifest_path"], "manifests/raw_delete_manifest.json")
        self.assertEqual(validation["cleanup"]["delete_manifest_file_count"], 1)
        self.assertEqual(validation["cleanup"]["delete_manifest_total_size_bytes"], 10)
        self.assertEqual(validation["cleanup"]["delete_manifest_mode"], "dry-run")
        self.assertTrue(validation["cleanup"]["execute_required"])
        self.assertEqual(validation["cleanup"]["destructive_operations"], 0)

        self.assertEqual(manifest["manifest_type"], "raw_cleanup_dry_run")
        self.assertEqual(manifest["case_id"], 0)
        self.assertEqual(manifest["case_name"], "000_fake_case")
        self.assertEqual(manifest["source_state"], "Raw_delete_eligible")
        self.assertEqual(manifest["file_count"], 1)
        self.assertEqual(manifest["total_size_bytes"], 10)
        self.assertEqual(manifest["destructive_operations"], 0)
        self.assertTrue(manifest["execute_allowed"])
        self.assertTrue(manifest["execute_requires_revalidation"])
        self.assertTrue(manifest["execute_must_delete_only_manifest_files"])
        self.assertFalse(manifest["directory_delete_allowed"])
        self.assertEqual(manifest["files"][0]["relative_path"], "diags/raw_000.fake")

    def test_command_requires_dry_run_flag(self) -> None:
        case_dir = self.root / "000_fake_case"
        self._write_file(case_dir, "diags/raw_000.fake", b"a" * 10)
        self._make_raw_delete_eligible(case_dir, candidate_count=1, candidate_bytes=10)

        rc = cleanup_raw_case_main(
            [
                "--campaign-root",
                str(self.root),
                "--case-id",
                "0",
            ]
        )

        self.assertEqual(rc, 2)
        self.assertFalse((case_dir / "manifests/raw_delete_manifest.json").exists())
        self.assertTrue((case_dir / "diags/raw_000.fake").exists())

    def test_non_eligible_state_is_rejected(self) -> None:
        case_dir = self.root / "000_fake_case"
        self._write_file(case_dir, "diags/raw_000.fake", b"a" * 10)
        self._force_state(case_dir, "Reduced_validated")
        self._mark_raw_and_reduced_ok(case_dir)

        rc = cleanup_raw_case_main(
            [
                "--campaign-root",
                str(self.root),
                "--case-id",
                "0",
                "--dry-run",
            ]
        )

        self.assertEqual(rc, 1)
        self.assertFalse((case_dir / "manifests/raw_delete_manifest.json").exists())
        self.assertTrue((case_dir / "diags/raw_000.fake").exists())

    def test_cleanup_allowed_false_is_rejected(self) -> None:
        case_dir = self.root / "000_fake_case"
        self._write_file(case_dir, "diags/raw_000.fake", b"a" * 10)
        self._force_state(case_dir, "Raw_delete_eligible")
        self._mark_raw_and_reduced_ok(case_dir)

        rc = cleanup_raw_case_main(
            [
                "--campaign-root",
                str(self.root),
                "--case-id",
                "0",
                "--dry-run",
            ]
        )

        self.assertEqual(rc, 1)
        self.assertFalse((case_dir / "manifests/raw_delete_manifest.json").exists())
        self.assertTrue((case_dir / "diags/raw_000.fake").exists())

    def test_candidate_count_mismatch_is_rejected(self) -> None:
        case_dir = self.root / "000_fake_case"
        self._write_file(case_dir, "diags/raw_000.fake", b"a" * 10)
        self._make_raw_delete_eligible(case_dir, candidate_count=2, candidate_bytes=10)

        rc = cleanup_raw_case_main(
            [
                "--campaign-root",
                str(self.root),
                "--case-id",
                "0",
                "--dry-run",
            ]
        )

        self.assertEqual(rc, 1)
        self.assertFalse((case_dir / "manifests/raw_delete_manifest.json").exists())
        self.assertTrue((case_dir / "diags/raw_000.fake").exists())

    def test_legacy_reduced_only_is_rejected(self) -> None:
        case_dir = self.root / "000_fake_case"
        self._write_file(case_dir, "diags/raw_000.fake", b"a" * 10)
        self._make_raw_delete_eligible(case_dir, candidate_count=1, candidate_bytes=10)

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

        rc = cleanup_raw_case_main(
            [
                "--campaign-root",
                str(self.root),
                "--case-id",
                "0",
                "--dry-run",
            ]
        )

        self.assertEqual(rc, 1)
        self.assertFalse((case_dir / "manifests/raw_delete_manifest.json").exists())
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

    def _mark_raw_and_reduced_ok(self, case_dir: Path) -> None:
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

    def _make_raw_delete_eligible(
        self,
        case_dir: Path,
        *,
        candidate_count: int,
        candidate_bytes: int,
    ) -> None:
        self._force_state(case_dir, "Raw_delete_eligible")
        self._mark_raw_and_reduced_ok(case_dir)

        validation = read_json(case_dir / "validation.json")
        validation["cleanup"] = {
            "cleanup_allowed": True,
            "cleanup_allowed_at": now_utc(),
            "operation": "mark_raw_delete_eligible",
            "reason": (
                "Raw and reduced validation evidence passed. "
                "Raw cleanup may proceed only through dry-run manifest and explicit execute phase."
            ),
            "eligibility_marker_path": "post/raw_delete_eligible.json",
            "candidate_file_count": candidate_count,
            "candidate_total_size_bytes": candidate_bytes,
            "candidate_total_size_GB": round(candidate_bytes / 1024**3, 6),
            "destructive_operations": 0,
        }
        validation["updated_at"] = now_utc()
        write_json_atomic(case_dir / "validation.json", validation)

        marker = {
            "schema_version": 1,
            "created_at": now_utc(),
            "operation": "mark_raw_delete_eligible",
            "case_id": 0,
            "case_name": "000_fake_case",
            "state": "Raw_delete_eligible",
            "cleanup_allowed": True,
            "cleanup_requires_dry_run_manifest": True,
            "cleanup_requires_explicit_execute": True,
            "candidate_file_count": candidate_count,
            "candidate_total_size_bytes": candidate_bytes,
            "candidate_total_size_GB": round(candidate_bytes / 1024**3, 6),
            "candidate_files_preview": [],
            "destructive_operations": 0,
        }
        write_json_atomic(case_dir / "post/raw_delete_eligible.json", marker)

    def test_execute_deletes_only_manifest_files_and_marks_raw_deleted(self) -> None:
        case_dir = self.root / "000_fake_case"
        self._write_file(case_dir, "diags/raw_000.fake", b"a" * 10)
        self._write_file(case_dir, "keep/reduced.csv", b"iteration,score\n0,1\n")
        self._make_raw_delete_eligible(case_dir, candidate_count=1, candidate_bytes=10)

        rc = cleanup_raw_case_main(
            [
                "--campaign-root",
                str(self.root),
                "--case-id",
                "0",
                "--dry-run",
            ]
        )
        self.assertEqual(rc, 0)

        rc = cleanup_raw_case_main(
            [
                "--campaign-root",
                str(self.root),
                "--case-id",
                "0",
                "--execute",
            ]
        )
        self.assertEqual(rc, 0)

        state = read_json(case_dir / "state.json")
        validation = read_json(case_dir / "validation.json")
        deleted = read_json(case_dir / "post/raw_deleted.json")

        self.assertEqual(state["state"], "Raw_deleted")
        self.assertFalse((case_dir / "diags/raw_000.fake").exists())
        self.assertTrue((case_dir / "diags").exists())
        self.assertTrue((case_dir / "keep/reduced.csv").exists())
        self.assertTrue((case_dir / "manifests/raw_delete_manifest.json").exists())

        self.assertFalse(validation["cleanup"]["cleanup_allowed"])
        self.assertTrue(validation["cleanup"]["raw_deleted"])
        self.assertEqual(validation["cleanup"]["deleted_file_count"], 1)
        self.assertEqual(validation["cleanup"]["deleted_total_size_bytes"], 10)
        self.assertEqual(validation["cleanup"]["delete_manifest_mode"], "executed")
        self.assertFalse(validation["cleanup"]["execute_required"])
        self.assertEqual(validation["cleanup"]["destructive_operations"], 1)

        self.assertEqual(deleted["deleted_file_count"], 1)
        self.assertEqual(deleted["deleted_total_size_bytes"], 10)
        self.assertEqual(deleted["destructive_operations"], 1)
        self.assertFalse(deleted["directory_delete_allowed"])

    def test_execute_without_manifest_is_rejected(self) -> None:
        case_dir = self.root / "000_fake_case"
        self._write_file(case_dir, "diags/raw_000.fake", b"a" * 10)
        self._make_raw_delete_eligible(case_dir, candidate_count=1, candidate_bytes=10)

        rc = cleanup_raw_case_main(
            [
                "--campaign-root",
                str(self.root),
                "--case-id",
                "0",
                "--execute",
            ]
        )

        self.assertEqual(rc, 1)
        self.assertTrue((case_dir / "diags/raw_000.fake").exists())
        self.assertFalse((case_dir / "post/raw_deleted.json").exists())

    def test_execute_rejects_manifest_size_mismatch(self) -> None:
        case_dir = self.root / "000_fake_case"
        self._write_file(case_dir, "diags/raw_000.fake", b"a" * 10)
        self._make_raw_delete_eligible(case_dir, candidate_count=1, candidate_bytes=10)

        rc = cleanup_raw_case_main(
            [
                "--campaign-root",
                str(self.root),
                "--case-id",
                "0",
                "--dry-run",
            ]
        )
        self.assertEqual(rc, 0)

        self._write_file(case_dir, "diags/raw_000.fake", b"changed-size")

        rc = cleanup_raw_case_main(
            [
                "--campaign-root",
                str(self.root),
                "--case-id",
                "0",
                "--execute",
            ]
        )

        self.assertEqual(rc, 1)
        self.assertTrue((case_dir / "diags/raw_000.fake").exists())
        self.assertFalse((case_dir / "post/raw_deleted.json").exists())

    def test_execute_rejects_extra_reinterpreted_files(self) -> None:
        case_dir = self.root / "000_fake_case"
        self._write_file(case_dir, "diags/raw_000.fake", b"a" * 10)
        self._make_raw_delete_eligible(case_dir, candidate_count=1, candidate_bytes=10)

        rc = cleanup_raw_case_main(
            [
                "--campaign-root",
                str(self.root),
                "--case-id",
                "0",
                "--dry-run",
            ]
        )
        self.assertEqual(rc, 0)

        self._write_file(case_dir, "diags/raw_001.fake", b"b" * 5)

        rc = cleanup_raw_case_main(
            [
                "--campaign-root",
                str(self.root),
                "--case-id",
                "0",
                "--execute",
            ]
        )

        self.assertEqual(rc, 0)
        self.assertFalse((case_dir / "diags/raw_000.fake").exists())
        self.assertTrue((case_dir / "diags/raw_001.fake").exists())


if __name__ == "__main__":
    unittest.main()