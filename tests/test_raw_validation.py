from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path

from campaign_workflow.cli.init_case_states import main as init_case_states_main
from campaign_workflow.cli.validate_raw_case import main as validate_raw_case_main
from campaign_workflow.core.atomic_io import read_json, write_json_atomic
from campaign_workflow.core.state import now_utc


class RawValidationTests(unittest.TestCase):
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

    def test_successful_fake_raw_validation_writes_manifest_and_state(self) -> None:
        case_dir = self.root / "000_fake_case"
        self._mark_sim_done(case_dir)
        self._write_raw(case_dir, "diags/raw_000.fake", b"fake payload")

        rc = validate_raw_case_main(["--campaign-root", str(self.root), "--case-id", "0"])

        self.assertEqual(rc, 0)

        state = read_json(case_dir / "state.json")
        validation = read_json(case_dir / "validation.json")
        manifest = read_json(case_dir / "manifests/raw_fake_raw.json")

        self.assertEqual(state["state"], "Raw_validated")
        self.assertTrue(validation["raw"]["fake_raw"]["ok"])
        self.assertEqual(validation["raw"]["fake_raw"]["manifest_path"], "manifests/raw_fake_raw.json")
        self.assertFalse(validation["cleanup"]["cleanup_allowed"])
        self.assertEqual(manifest["manifest_type"], "raw_diagnostic")
        self.assertEqual(manifest["file_count"], 1)
        self.assertEqual(manifest["files"][0]["relative_path"], "diags/raw_000.fake")
        self.assertEqual(manifest["destructive_operations"], 0)

    def test_dry_run_does_not_write_manifest_or_change_state(self) -> None:
        case_dir = self.root / "000_fake_case"
        self._mark_sim_done(case_dir)
        self._write_raw(case_dir, "diags/raw_000.fake", b"fake payload")

        rc = validate_raw_case_main(["--campaign-root", str(self.root), "--case-id", "0", "--dry-run"])

        self.assertEqual(rc, 0)

        state = read_json(case_dir / "state.json")
        validation = read_json(case_dir / "validation.json")

        self.assertEqual(state["state"], "Sim_done")
        self.assertEqual(validation["raw"], {})
        self.assertFalse((case_dir / "manifests/raw_fake_raw.json").exists())

    def test_missing_required_raw_files_marks_validation_failed(self) -> None:
        case_dir = self.root / "000_fake_case"
        self._mark_sim_done(case_dir)

        rc = validate_raw_case_main(["--campaign-root", str(self.root), "--case-id", "0"])

        self.assertEqual(rc, 1)

        state = read_json(case_dir / "state.json")
        validation = read_json(case_dir / "validation.json")

        self.assertEqual(state["state"], "Validation_failed")
        self.assertFalse(validation["raw"]["fake_raw"]["ok"])
        self.assertGreaterEqual(len(validation["raw"]["fake_raw"]["errors"]), 1)
        self.assertFalse((case_dir / "manifests/raw_fake_raw.json").exists())

    def test_incompatible_state_is_rejected_without_writing(self) -> None:
        case_dir = self.root / "000_fake_case"
        self._write_raw(case_dir, "diags/raw_000.fake", b"fake payload")

        rc = validate_raw_case_main(["--campaign-root", str(self.root), "--case-id", "0"])

        self.assertEqual(rc, 1)

        state = read_json(case_dir / "state.json")
        validation = read_json(case_dir / "validation.json")

        self.assertEqual(state["state"], "Created")
        self.assertEqual(validation["raw"], {})
        self.assertFalse((case_dir / "manifests/raw_fake_raw.json").exists())

    def test_symlink_escape_is_rejected_when_supported(self) -> None:
        case_dir = self.root / "000_fake_case"
        self._mark_sim_done(case_dir)

        outside = Path(self.tmpdir.name) / "outside.fake"
        outside.write_bytes(b"outside payload")

        link_path = case_dir / "diags/link.fake"
        link_path.parent.mkdir(parents=True, exist_ok=True)

        try:
            link_path.symlink_to(outside)
        except (OSError, NotImplementedError) as exc:
            self.skipTest(f"symlink creation is not available in this environment: {exc}")

        rc = validate_raw_case_main(["--campaign-root", str(self.root), "--case-id", "0"])

        self.assertEqual(rc, 1)

        state = read_json(case_dir / "state.json")
        validation = read_json(case_dir / "validation.json")

        self.assertEqual(state["state"], "Validation_failed")
        self.assertFalse(validation["raw"]["fake_raw"]["ok"])

        joined_errors = "\n".join(validation["raw"]["fake_raw"]["errors"])
        self.assertIn("escapes case directory", joined_errors)

        self.assertFalse((case_dir / "manifests/raw_fake_raw.json").exists())

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

    def _mark_sim_done(self, case_dir: Path) -> None:
        state_path = case_dir / "state.json"
        state = read_json(state_path)

        previous = state["state"]
        timestamp = now_utc()

        state["state"] = "Sim_done"
        state["updated_at"] = timestamp
        state.setdefault("history", []).append(
            {
                "timestamp": timestamp,
                "from": previous,
                "to": "Sim_done",
                "operation": "test_mark_sim_done",
                "reason": "test fixture simulates completed simulation",
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


if __name__ == "__main__":
    unittest.main()