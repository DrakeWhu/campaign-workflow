from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from campaign_workflow.presubmitted_chain import (
    find_latest_chain_manifest_for_array,
    update_state_after_presubmitted_array,
)


class PreSubmittedChainTests(unittest.TestCase):
    def _write_campaign(self, root: Path, iteration: int, case_count: int) -> None:
        campaign_root = root / "iterations" / f"iter_{iteration:03d}"
        campaign_root.mkdir(parents=True)
        campaign = {
            "schema_version": 1,
            "campaign_name": f"campaign_iter_{iteration:03d}",
            "case_manifest": "cases.tsv",
            "case_manifest_format": "tsv",
            "case_id_column": "CASE_ID",
            "case_name_column": "CASE_NAME",
        }
        (campaign_root / "campaign.json").write_text(
            json.dumps(campaign), encoding="utf-8"
        )
        lines = ["CASE_ID\tCASE_NAME"]
        lines.extend(
            f"{case_id}\tcase_{case_id:03d}" for case_id in range(case_count)
        )
        (campaign_root / "cases.tsv").write_text(
            "\n".join(lines) + "\n", encoding="utf-8"
        )

    def test_finds_latest_matching_array_and_updates_existing_iteration(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_s:
            root = Path(tmp_s)
            (root / "loop_logs").mkdir()
            self._write_campaign(root, iteration=3, case_count=30)

            manifest_path = root / "loop_logs" / "morbo_chain_20260707_091822.json"
            manifest = {
                "schema_version": 1,
                "dry_run": False,
                "created_at": "2026-07-07T09:18:22Z",
                "job_name_prefix": "cw_bo002",
                "array_spec": "0-29",
                "array_script": str(root / "run_iteration_array.sh"),
                "jobs": [
                    {
                        "kind": "array",
                        "iteration": 3,
                        "job_id": "611536",
                        "submit_command": [
                            "sbatch",
                            "--array=0-29",
                            "run_iteration_array.sh",
                        ],
                    },
                    {"kind": "tick", "iteration": 3, "job_id": "611537"},
                ],
            }
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

            found = find_latest_chain_manifest_for_array(
                optimization_root=root,
                iteration=3,
                job_name_prefix="cw_bo002",
            )
            self.assertIsNotNone(found)

            found_path, found_manifest, array_job = found or (None, {}, {})
            self.assertEqual(found_path, manifest_path)
            self.assertEqual(array_job["job_id"], "611536")

            state = {
                "schema_version": 1,
                "optimization_name": "bo_root",
                "status": "campaign_materialized",
                "updated_at": "2026-07-07T09:20:00Z",
                "latest_iteration": 3,
                "iterations": [
                    {
                        "iteration": 3,
                        "status": "campaign_materialized",
                        "recommended_action": "submit_iteration",
                        "submitted": False,
                        "slurm_job_ids": [],
                    }
                ],
            }

            updated = update_state_after_presubmitted_array(
                state_doc=state,
                optimization_root=root,
                iteration=3,
                manifest_path=found_path,
                manifest=found_manifest,
                array_job=array_job,
            )

            iteration = updated["iterations"][0]
            self.assertEqual(iteration["status"], "submitted")
            self.assertEqual(iteration["recommended_action"], "wait_for_jobs")
            self.assertTrue(iteration["submitted"])
            self.assertEqual(iteration["slurm_job_ids"], ["611536"])
            self.assertEqual(iteration["array_spec"], "0-29")
            self.assertEqual(iteration["submitted_case_ids"], list(range(30)))
            self.assertEqual(iteration["submitted_case_count"], 30)
            self.assertEqual(
                iteration["pre_submitted_chain_manifest"], str(manifest_path)
            )

    def test_extra_array_tasks_are_recorded_as_noops_not_submitted_cases(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_s:
            root = Path(tmp_s)
            (root / "loop_logs").mkdir()
            self._write_campaign(root, iteration=2, case_count=4)
            state = {
                "schema_version": 1,
                "status": "campaign_materialized",
                "iterations": [{"iteration": 2, "submitted": False}],
            }
            manifest = {
                "created_at": "2026-07-13T12:00:00Z",
                "array_spec": "0-8%2",
                "array_script": "run_iteration_array.sh",
            }
            updated = update_state_after_presubmitted_array(
                state_doc=state,
                optimization_root=root,
                iteration=2,
                manifest_path=root / "loop_logs" / "chain.json",
                manifest=manifest,
                array_job={"job_id": "700002", "submit_command": ["sbatch"]},
            )

            iteration = updated["iterations"][0]
            self.assertEqual(iteration["submitted_case_ids"], [0, 1, 2, 3])
            self.assertEqual(iteration["submitted_case_count"], 4)
            self.assertEqual(iteration["array_noop_task_ids"], [4, 5, 6, 7, 8])


if __name__ == "__main__":
    unittest.main()
