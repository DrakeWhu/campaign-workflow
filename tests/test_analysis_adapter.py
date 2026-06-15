from __future__ import annotations

import json
import os
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

from campaign_workflow.cli.analyze_case import main as analyze_case_main
from campaign_workflow.cli.init_case_states import main as init_case_states_main
from campaign_workflow.cli.validate_raw_case import main as validate_raw_case_main
from campaign_workflow.core.atomic_io import read_json, write_json_atomic
from campaign_workflow.core.state import now_utc


class AnalysisAdapterTests(unittest.TestCase):
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

    def test_fake_analysis_runs_and_validates_reduced_outputs(self) -> None:
        case_dir = self.root / "000_fake_case"
        self._prepare_raw_validated_case(case_dir)

        rc = analyze_case_main(["--campaign-root", str(self.root), "--case-id", "0"])

        self.assertEqual(rc, 0)

        state = read_json(case_dir / "state.json")
        validation = read_json(case_dir / "validation.json")
        done = read_json(case_dir / "post/analysis_done.json")

        self.assertEqual(state["state"], "Reduced_validated")
        self.assertEqual(state["history"][-2]["to"], "Analyzing")
        self.assertEqual(state["history"][-1]["to"], "Reduced_validated")
        self.assertEqual(state["history"][-1]["operation"], "analyze_case")

        self.assertTrue(validation["analysis"]["fake_analysis"]["ok"])
        self.assertEqual(validation["analysis"]["fake_analysis"]["analysis_adapter"], "fake")
        self.assertTrue(validation["analysis"]["fake_analysis"]["reduced_validation_ok"])
        self.assertFalse(validation["analysis"]["fake_analysis"]["cleanup_allowed"])

        self.assertTrue(validation["reduced"]["fake_metrics"]["ok"])
        self.assertEqual(validation["reduced"]["fake_metrics"]["row_count"], 1)
        self.assertFalse(validation["cleanup"]["cleanup_allowed"])

        self.assertTrue((case_dir / "post/fake_metrics.csv").exists())
        self.assertTrue((case_dir / "logs/analysis_fake_analysis_stdout.txt").exists())
        self.assertTrue(done["ok"])
        self.assertEqual(done["analysis_name"], "fake_analysis")
        self.assertEqual(done["destructive_operations"], 0)

    def test_dry_run_does_not_execute_or_write(self) -> None:
        case_dir = self.root / "000_fake_case"
        self._prepare_raw_validated_case(case_dir)

        rc = analyze_case_main(["--campaign-root", str(self.root), "--case-id", "0", "--dry-run"])

        self.assertEqual(rc, 0)

        state = read_json(case_dir / "state.json")
        validation = read_json(case_dir / "validation.json")

        self.assertEqual(state["state"], "Raw_validated")
        self.assertNotIn("analysis", validation)
        self.assertEqual(validation["reduced"], {})
        self.assertFalse((case_dir / "post/fake_metrics.csv").exists())
        self.assertFalse((case_dir / "post/analysis_done.json").exists())

    def test_created_state_is_rejected_without_writing(self) -> None:
        case_dir = self.root / "000_fake_case"

        rc = analyze_case_main(["--campaign-root", str(self.root), "--case-id", "0"])

        self.assertEqual(rc, 1)

        state = read_json(case_dir / "state.json")
        validation = read_json(case_dir / "validation.json")

        self.assertEqual(state["state"], "Created")
        self.assertNotIn("analysis", validation)
        self.assertEqual(validation["reduced"], {})
        self.assertFalse((case_dir / "post/fake_metrics.csv").exists())

    def test_missing_raw_evidence_is_rejected_without_writing(self) -> None:
        case_dir = self.root / "000_fake_case"
        self._force_state(case_dir, "Raw_validated")

        rc = analyze_case_main(["--campaign-root", str(self.root), "--case-id", "0"])

        self.assertEqual(rc, 1)

        state = read_json(case_dir / "state.json")
        validation = read_json(case_dir / "validation.json")

        self.assertEqual(state["state"], "Raw_validated")
        self.assertNotIn("analysis", validation)
        self.assertEqual(validation["reduced"], {})
        self.assertFalse((case_dir / "post/fake_metrics.csv").exists())

    def test_adapter_failure_marks_analysis_failed(self) -> None:
        case_dir = self.root / "000_fake_case"
        self._prepare_raw_validated_case(case_dir)
        self._set_analysis_fake_fail(True)

        rc = analyze_case_main(["--campaign-root", str(self.root), "--case-id", "0"])

        self.assertEqual(rc, 1)

        state = read_json(case_dir / "state.json")
        validation = read_json(case_dir / "validation.json")
        failed = read_json(case_dir / "post/analysis_failed.json")

        self.assertEqual(state["state"], "Analysis_failed")
        self.assertEqual(state["history"][-2]["to"], "Analyzing")
        self.assertEqual(state["history"][-1]["to"], "Analysis_failed")
        self.assertFalse(validation["analysis"]["fake_analysis"]["ok"])
        self.assertFalse(validation["analysis"]["fake_analysis"]["reduced_validation_ok"])
        self.assertEqual(validation["reduced"], {})
        self.assertFalse(validation["cleanup"]["cleanup_allowed"])
        self.assertFalse(failed["ok"])

    def test_command_adapter_can_wrap_existing_script(self) -> None:
        case_dir = self.root / "000_fake_case"
        self._prepare_raw_validated_case(case_dir)

        script = self.root / "write_metrics.py"
        script.write_text(
            textwrap.dedent(
                """
                from pathlib import Path
                import os

                case_dir = Path(os.environ["CASE_DIR"])
                out = case_dir / "post" / "fake_metrics.csv"
                out.parent.mkdir(parents=True, exist_ok=True)
                out.write_text("iteration,score\\n0,2.5\\n", encoding="utf-8")
                """
            ).strip()
            + "\n",
            encoding="utf-8",
        )
        self._set_analysis_command([sys.executable, str(script)])

        rc = analyze_case_main(["--campaign-root", str(self.root), "--case-id", "0"])

        self.assertEqual(rc, 0)

        state = read_json(case_dir / "state.json")
        validation = read_json(case_dir / "validation.json")

        self.assertEqual(state["state"], "Reduced_validated")
        self.assertTrue(validation["analysis"]["fake_analysis"]["ok"])
        self.assertEqual(validation["analysis"]["fake_analysis"]["analysis_adapter"], "command")
        self.assertTrue(validation["reduced"]["fake_metrics"]["ok"])
        self.assertEqual(validation["reduced"]["fake_metrics"]["row_count"], 1)

    def test_raw_delete_eligible_requires_explicit_rerun_flag(self) -> None:
        case_dir = self.root / "000_fake_case"
        self._prepare_raw_validated_case(case_dir)
        self._mark_raw_delete_eligible_like(case_dir)

        rc = analyze_case_main(["--campaign-root", str(self.root), "--case-id", "0"])

        self.assertEqual(rc, 1)

        state = read_json(case_dir / "state.json")
        validation = read_json(case_dir / "validation.json")

        self.assertEqual(state["state"], "Raw_delete_eligible")
        self.assertNotIn("analysis", validation)
        self.assertEqual(validation["reduced"], {})
        self.assertFalse((case_dir / "post/fake_metrics.csv").exists())


    def test_raw_delete_eligible_rerun_succeeds_with_flag_and_blocks_cleanup(self) -> None:
        case_dir = self.root / "000_fake_case"
        self._prepare_raw_validated_case(case_dir)
        self._mark_raw_delete_eligible_like(case_dir)

        rc = analyze_case_main(
            [
                "--campaign-root",
                str(self.root),
                "--case-id",
                "0",
                "--allow-rerun-from-raw-delete-eligible",
            ]
        )

        self.assertEqual(rc, 0)

        state = read_json(case_dir / "state.json")
        validation = read_json(case_dir / "validation.json")

        self.assertEqual(state["state"], "Reduced_validated")
        self.assertEqual(state["history"][-2]["from"], "Raw_delete_eligible")
        self.assertEqual(state["history"][-2]["to"], "Analyzing")
        self.assertEqual(state["history"][-1]["to"], "Reduced_validated")

        self.assertTrue(validation["analysis"]["fake_analysis"]["ok"])
        self.assertTrue(validation["analysis"]["fake_analysis"]["rerun_from_raw_delete_eligible"])
        self.assertTrue(validation["reduced"]["fake_metrics"]["ok"])

        cleanup = validation["cleanup"]
        self.assertFalse(cleanup["cleanup_allowed"])
        self.assertFalse(cleanup["delete_manifest_ready"])
        self.assertFalse(cleanup["execute_required"])
        self.assertTrue(cleanup["previous_cleanup_evidence_invalidated"])
        self.assertEqual(cleanup["previous_cleanup_evidence_invalidated_by"], "analyze_case")

        self.assertTrue((case_dir / "diags/raw_000.fake").exists())
        self.assertTrue((case_dir / "post/fake_metrics.csv").exists())
        self.assertTrue((case_dir / "post/analysis_done.json").exists())


    def test_raw_delete_eligible_rerun_rejects_missing_preserved_raw(self) -> None:
        case_dir = self.root / "000_fake_case"
        self._prepare_raw_validated_case(case_dir)
        self._mark_raw_delete_eligible_like(case_dir)
        (case_dir / "diags/raw_000.fake").unlink()

        rc = analyze_case_main(
            [
                "--campaign-root",
                str(self.root),
                "--case-id",
                "0",
                "--allow-rerun-from-raw-delete-eligible",
            ]
        )

        self.assertEqual(rc, 1)

        state = read_json(case_dir / "state.json")
        validation = read_json(case_dir / "validation.json")

        self.assertEqual(state["state"], "Raw_delete_eligible")
        self.assertNotIn("analysis", validation)
        self.assertEqual(validation["reduced"], {})
        self.assertFalse((case_dir / "post/fake_metrics.csv").exists())


    def test_raw_delete_eligible_rerun_rejects_raw_deleted_marker(self) -> None:
        case_dir = self.root / "000_fake_case"
        self._prepare_raw_validated_case(case_dir)
        self._mark_raw_delete_eligible_like(case_dir)
        (case_dir / "post/raw_deleted.json").write_text(
            json.dumps({"operation": "cleanup_raw_case", "raw_deleted": True}) + "\n",
            encoding="utf-8",
        )

        rc = analyze_case_main(
            [
                "--campaign-root",
                str(self.root),
                "--case-id",
                "0",
                "--allow-rerun-from-raw-delete-eligible",
            ]
        )

        self.assertEqual(rc, 1)

        state = read_json(case_dir / "state.json")
        validation = read_json(case_dir / "validation.json")

        self.assertEqual(state["state"], "Raw_delete_eligible")
        self.assertNotIn("analysis", validation)
        self.assertFalse((case_dir / "post/fake_metrics.csv").exists())

    
    def test_reduced_validated_requires_explicit_rerun_flag(self) -> None:
        case_dir = self.root / "000_fake_case"
        self._prepare_raw_validated_case(case_dir)

        rc = analyze_case_main(["--campaign-root", str(self.root), "--case-id", "0"])
        self.assertEqual(rc, 0)
        self.assertEqual(read_json(case_dir / "state.json")["state"], "Reduced_validated")

        self._set_analysis_fake_fail(True)
        rc = analyze_case_main(["--campaign-root", str(self.root), "--case-id", "0"])

        self.assertEqual(rc, 1)

        state = read_json(case_dir / "state.json")
        validation = read_json(case_dir / "validation.json")

        self.assertEqual(state["state"], "Reduced_validated")
        self.assertTrue(validation["analysis"]["fake_analysis"]["ok"])
        self.assertTrue(validation["reduced"]["fake_metrics"]["ok"])


    def test_reduced_validated_rerun_succeeds_with_flag(self) -> None:
        case_dir = self.root / "000_fake_case"
        self._prepare_raw_validated_case(case_dir)

        rc = analyze_case_main(["--campaign-root", str(self.root), "--case-id", "0"])
        self.assertEqual(rc, 0)
        self.assertEqual(read_json(case_dir / "state.json")["state"], "Reduced_validated")

        rc = analyze_case_main(
            [
                "--campaign-root",
                str(self.root),
                "--case-id",
                "0",
                "--allow-rerun-from-reduced-validated",
            ]
        )

        self.assertEqual(rc, 0)

        state = read_json(case_dir / "state.json")
        validation = read_json(case_dir / "validation.json")

        self.assertEqual(state["state"], "Reduced_validated")
        self.assertEqual(state["history"][-2]["from"], "Reduced_validated")
        self.assertEqual(state["history"][-2]["to"], "Analyzing")
        self.assertEqual(state["history"][-1]["to"], "Reduced_validated")

        analysis = validation["analysis"]["fake_analysis"]
        self.assertTrue(analysis["ok"])
        self.assertFalse(analysis["rerun_from_raw_delete_eligible"])
        self.assertTrue(analysis["rerun_from_reduced_validated"])
        self.assertEqual(analysis["rerun_from_state"], "Reduced_validated")
        self.assertTrue(validation["reduced"]["fake_metrics"]["ok"])
        self.assertFalse(validation["cleanup"]["cleanup_allowed"])


    def test_reduced_validated_rerun_rejects_missing_preserved_raw(self) -> None:
        case_dir = self.root / "000_fake_case"
        self._prepare_raw_validated_case(case_dir)

        rc = analyze_case_main(["--campaign-root", str(self.root), "--case-id", "0"])
        self.assertEqual(rc, 0)
        self.assertEqual(read_json(case_dir / "state.json")["state"], "Reduced_validated")

        (case_dir / "diags/raw_000.fake").unlink()

        rc = analyze_case_main(
            [
                "--campaign-root",
                str(self.root),
                "--case-id",
                "0",
                "--allow-rerun-from-reduced-validated",
            ]
        )

        self.assertEqual(rc, 1)

        state = read_json(case_dir / "state.json")
        validation = read_json(case_dir / "validation.json")

        self.assertEqual(state["state"], "Reduced_validated")
        self.assertTrue(validation["analysis"]["fake_analysis"]["ok"])
        self.assertTrue(validation["reduced"]["fake_metrics"]["ok"])


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

        (self.root / "campaign.json").write_text(json.dumps(campaign, indent=2) + "\n", encoding="utf-8")
        (self.root / "cases.tsv").write_text(
            "CASE_ID\tCASE_NAME\tKIND\n"
            "0\t000_fake_case\talpha\n"
            "1\t001_fake_case\tbeta\n"
            "2\t002_fake_case\tgamma\n",
            encoding="utf-8",
        )

    def _prepare_raw_validated_case(self, case_dir: Path) -> None:
        self._force_state(case_dir, "Sim_done")
        self._write_raw(case_dir, "diags/raw_000.fake", b"fake payload")

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

    def _write_raw(self, case_dir: Path, relative_path: str, payload: bytes) -> None:
        path = case_dir / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(payload)

    def _set_analysis_fake_fail(self, value: bool) -> None:
        config_path = self.root / "campaign.json"
        config = json.loads(config_path.read_text(encoding="utf-8"))
        config["analysis"]["fake_fail"] = value
        config_path.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")

    def _set_analysis_command(self, command: list[str]) -> None:
        config_path = self.root / "campaign.json"
        config = json.loads(config_path.read_text(encoding="utf-8"))
        config["analysis"]["adapter"] = "command"
        config["analysis"]["kind"] = "command"
        config["analysis"]["command"] = command
        config_path.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")

    def _mark_raw_delete_eligible_like(self, case_dir: Path) -> None:
        self._force_state(case_dir, "Raw_delete_eligible")

        validation_path = case_dir / "validation.json"
        validation = read_json(validation_path)
        cleanup = validation.setdefault("cleanup", {})
        cleanup.update(
            {
                "cleanup_allowed": True,
                "operation": "mark_raw_delete_eligible",
                "reason": "test fixture cleanup eligibility",
                "candidate_file_count": 1,
                "candidate_total_size_bytes": 12,
                "delete_manifest_ready": True,
                "delete_manifest_mode": "dry-run",
                "execute_required": True,
                "destructive_operations": 0,
            }
        )
        write_json_atomic(validation_path, validation)

        post_dir = case_dir / "post"
        post_dir.mkdir(parents=True, exist_ok=True)
        (post_dir / "raw_delete_eligible.json").write_text(
            json.dumps({"operation": "mark_raw_delete_eligible", "state": "Raw_delete_eligible"}) + "\n",
            encoding="utf-8",
        )




if __name__ == "__main__":
    unittest.main()