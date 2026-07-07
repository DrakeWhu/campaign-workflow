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
    def test_finds_latest_matching_array_and_updates_existing_iteration(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_s:
            root = Path(tmp_s)
            (root / "loop_logs").mkdir()

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


if __name__ == "__main__":
    unittest.main()
