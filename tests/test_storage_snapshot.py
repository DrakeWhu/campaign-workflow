from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path

from campaign_workflow.cli.init_case_states import main as init_case_states_main
from campaign_workflow.cli.storage_snapshot import main as storage_snapshot_main
from campaign_workflow.core.atomic_io import read_json, write_json_atomic
from campaign_workflow.core.state import now_utc
from campaign_workflow.core.storage import build_storage_snapshot
from campaign_workflow.core.tsv_cases import load_campaign_config, load_cases


class StorageSnapshotTests(unittest.TestCase):
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

    def test_snapshot_counts_raw_live_and_safe_candidates_without_writing(self) -> None:
        case0 = self.root / "000_fake_case"
        case1 = self.root / "001_fake_case"

        self._write_file(case0, "diags/raw_000.fake", b"a" * 10)
        self._write_file(case0, "post/fake_metrics.csv", b"iteration,score\n0,1\n")
        self._force_state(case0, "Reduced_validated")
        self._mark_raw_and_reduced_ok(case0)

        self._write_file(case1, "diags/raw_001.fake", b"b" * 20)
        self._force_state(case1, "Raw_validated")
        self._mark_raw_ok(case1)

        config = load_campaign_config(self.root)
        cases = load_cases(self.root, config)
        snapshot = build_storage_snapshot(campaign_root=self.root, config=config, cases=cases)

        self.assertEqual(snapshot["case_count"], 3)
        self.assertEqual(snapshot["validated_cases"], 1)
        self.assertEqual(snapshot["cases_by_state"]["Reduced_validated"], 1)
        self.assertEqual(snapshot["cases_by_state"]["Raw_validated"], 1)
        self.assertEqual(snapshot["cases_by_state"]["Created"], 1)
        self.assertEqual(snapshot["raw_live_bytes"], 30)
        self.assertEqual(snapshot["safe_cleanup_candidate_bytes"], 10)
        self.assertEqual(snapshot["raw_delete_eligible_bytes"], 0)
        self.assertEqual(snapshot["destructive_operations"], 0)
        self.assertEqual(snapshot["errors"], [])

    def test_cli_dry_run_does_not_write_snapshot(self) -> None:
        case0 = self.root / "000_fake_case"
        self._write_file(case0, "diags/raw_000.fake", b"a" * 10)
        self._force_state(case0, "Reduced_validated")
        self._mark_raw_and_reduced_ok(case0)

        rc = storage_snapshot_main(["--campaign-root", str(self.root), "--dry-run"])

        self.assertEqual(rc, 0)
        self.assertFalse((self.root / "snapshots/storage_snapshot_latest.json").exists())

    def test_cli_write_creates_snapshot(self) -> None:
        case0 = self.root / "000_fake_case"
        self._write_file(case0, "diags/raw_000.fake", b"a" * 10)
        self._force_state(case0, "Reduced_validated")
        self._mark_raw_and_reduced_ok(case0)

        rc = storage_snapshot_main(["--campaign-root", str(self.root)])

        self.assertEqual(rc, 0)
        snapshot = read_json(self.root / "snapshots/storage_snapshot_latest.json")
        self.assertEqual(snapshot["raw_live_bytes"], 10)
        self.assertEqual(snapshot["safe_cleanup_candidate_bytes"], 10)
        self.assertEqual(snapshot["destructive_operations"], 0)

    def test_snapshot_reports_invalid_cleanup_glob_as_error(self) -> None:
        config_path = self.root / "campaign.json"
        config = json.loads(config_path.read_text(encoding="utf-8"))
        config["cleanup"]["raw_delete_globs"] = ["../outside.fake"]
        config_path.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")

        case0 = self.root / "000_fake_case"
        self._write_file(case0, "diags/raw_000.fake", b"a" * 10)
        self._force_state(case0, "Reduced_validated")
        self._mark_raw_and_reduced_ok(case0)

        config = load_campaign_config(self.root)
        cases = load_cases(self.root, config)
        snapshot = build_storage_snapshot(campaign_root=self.root, config=config, cases=cases)

        self.assertGreater(len(snapshot["errors"]), 0)
        joined = "\n".join(item["error"] for item in snapshot["errors"])
        self.assertIn("must not contain '..'", joined)

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

    def _mark_raw_ok(self, case_dir: Path) -> None:
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
        validation["updated_at"] = now_utc()
        write_json_atomic(case_dir / "validation.json", validation)

    def _mark_raw_and_reduced_ok(self, case_dir: Path) -> None:
        self._mark_raw_ok(case_dir)

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