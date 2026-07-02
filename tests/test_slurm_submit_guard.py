from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from campaign_workflow.slurm_submit_guard import (
    assert_not_inside_slurm_job_for_sbatch,
    running_inside_slurm_job,
    slurm_job_environment,
)
from campaign_workflow.submit_iteration import (
    SubmitIterationError,
    SubmitIterationPlan,
    execute_submit_iteration,
)


class SlurmSubmitGuardTests(unittest.TestCase):
    def test_running_inside_slurm_job_detects_job_id(self) -> None:
        self.assertFalse(running_inside_slurm_job({}))
        self.assertTrue(running_inside_slurm_job({"SLURM_JOB_ID": "611305"}))
        self.assertTrue(running_inside_slurm_job({"SLURM_ARRAY_JOB_ID": "611305_2"}))

    def test_slurm_job_environment_keeps_only_relevant_identifiers(self) -> None:
        env = {
            "SLURM_JOB_ID": "611305",
            "SLURM_ARRAY_JOB_ID": "611305_2",
            "PATH": "/usr/bin",
        }
        self.assertEqual(
            slurm_job_environment(env),
            {
                "SLURM_JOB_ID": "611305",
                "SLURM_ARRAY_JOB_ID": "611305_2",
            },
        )

    def test_guard_error_message_is_clear(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "Refusing to call sbatch") as cm:
            assert_not_inside_slurm_job_for_sbatch(
                {"SLURM_JOB_ID": "611305", "SLURM_ARRAY_JOB_ID": "611305_2"}
            )

        message = str(cm.exception)
        self.assertIn("Use submit_morbo_chain.py from login/control instead", message)
        self.assertIn("SLURM_JOB_ID=611305", message)
        self.assertIn("SLURM_ARRAY_JOB_ID=611305_2", message)

    def test_execute_submit_iteration_rejects_nested_sbatch_before_subprocess(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            plan = SubmitIterationPlan(
                optimization_root=root,
                iteration=2,
                campaign_root=root / "iterations" / "iter_002",
                n_cases=30,
                array_spec="0-29",
                submitted_case_ids=list(range(30)),
                submit_script=root
                / "examples"
                / "sunrise"
                / "submit_case_cycle_array.sh",
                workflow_root=root,
                workflow_env=root / "campaign-workflow.sh",
                working_directory=root,
                submit_command=["sbatch", "--parsable", "--array=0-29", "dummy.sbatch"],
                submitted_case_count=30,
            )

            with patch.dict(os.environ, {"SLURM_JOB_ID": "611305"}, clear=False):
                with patch(
                    "campaign_workflow.submit_iteration.subprocess.run"
                ) as run_mock:
                    with self.assertRaisesRegex(
                        SubmitIterationError, "Refusing to call sbatch"
                    ) as cm:
                        execute_submit_iteration(plan)

        self.assertIn("SLURM_JOB_ID=611305", str(cm.exception))
        run_mock.assert_not_called()


if __name__ == "__main__":
    unittest.main()
