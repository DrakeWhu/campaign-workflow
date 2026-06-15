from __future__ import annotations

import unittest
from pathlib import Path


class SunriseWarpXRunnerScriptTests(unittest.TestCase):
    def setUp(self) -> None:
        self.script_path = Path("examples/sunrise/run_warpx_case_sunrise.sh")
        self.text = self.script_path.read_text(encoding="utf-8")

    def test_runner_script_exists_and_is_bash(self) -> None:
        self.assertTrue(self.script_path.exists())
        self.assertTrue(self.text.startswith("#!/usr/bin/env bash\n"))
        self.assertNotIn("\r\n", self.text)

    def test_runner_preserves_real_sunrise_warpx_execution_pattern(self) -> None:
        self.assertIn("source ./case.env", self.text)
        self.assertIn("mkdir -p logs diags checkpoints post", self.text)
        self.assertIn("existing_h5=(diags/**/*.h5 diags/**/*.hdf5)", self.text)

        self.assertIn("module load GCC/12.1.0", self.text)
        self.assertIn("module load Python/3.14.3", self.text)
        self.assertIn("module load OpenBLAS/0.3.31", self.text)
        self.assertIn("module load warpx/26.05-gcc12-openmpi413-all-dims", self.text)
        self.assertIn("source ~/apps/venvs/warpx-26.05-py314/bin/activate", self.text)

        self.assertIn('CAP_DRY_RUN=1 python input.py "${WARPX_INPUT_ARG}"', self.text)
        self.assertIn('srun -n "${SLURM_NTASKS}" python input.py "${WARPX_INPUT_ARG}"', self.text)

    def test_runner_is_case_local_and_does_not_run_workflow_phases(self) -> None:
        forbidden_fragments = [
            "rm -rf",
            "cleanup_raw_case",
            "validate_raw_case",
            "validate_reduced_case",
            "analyze_case",
            "mark_raw_delete_eligible",
            "campaign_workflow.cli",
        ]

        for fragment in forbidden_fragments:
            with self.subTest(fragment=fragment):
                self.assertNotIn(fragment, self.text)

    def test_runner_keeps_workflow_environment_separation(self) -> None:
        self.assertIn("warpx-26.05-py314", self.text)
        self.assertNotIn("campaign-workflow-py310/bin/activate", self.text)
        self.assertNotIn("guiding-analysis-py310/bin/activate", self.text)


if __name__ == "__main__":
    unittest.main()