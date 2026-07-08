from __future__ import annotations

import unittest
from pathlib import Path


class LynxWarpXRunnerScriptTests(unittest.TestCase):
    def setUp(self) -> None:
        self.script_path = Path("examples/lynx/run_warpx_case_lynx.sh")
        self.text = self.script_path.read_text(encoding="utf-8")

    def test_runner_script_exists_and_is_bash(self) -> None:
        self.assertTrue(self.script_path.exists())
        self.assertTrue(self.text.startswith("#!/usr/bin/env bash\n"))
        self.assertNotIn("\r\n", self.text)
        self.assertIn("set -Eeuo pipefail", self.text)
        self.assertIn("trap '", self.text)

    def test_runner_preserves_case_local_execution_pattern(self) -> None:
        self.assertIn("source ./case.env", self.text)
        self.assertIn("mkdir -p logs diags checkpoints post", self.text)
        self.assertIn("existing_h5=(diags/**/*.h5 diags/**/*.hdf5)", self.text)
        self.assertIn("WARPX_SKIP_IF_H5_EXISTS", self.text)

    def test_runner_uses_lynx_clean_environment_and_explicit_warpx_module(self) -> None:
        self.assertIn('source "${HOME}/apps/env/lynx_clean_base.sh"', self.text)
        self.assertIn("WARPX_LYNX_MODULE:?", self.text)
        self.assertIn('module load "WarpX/${WARPX_LYNX_MODULE}"', self.text)

        # The concrete Python venv is owned by the Lynx WarpX modulefile,
        # not hardcoded in this runner.
        self.assertIn("WARPX_LYNX_BUILD_TAG", self.text)
        self.assertIn("WARPX_HOME", self.text)
        self.assertIn("WARPX_DIM", self.text)
        self.assertIn("python=$(which python)", self.text)

    def test_runner_uses_picmi_preflight_and_slurm_execution(self) -> None:
        self.assertIn('CAP_DRY_RUN=1 python input.py "${WARPX_INPUT_ARG}"', self.text)
        self.assertIn(
            'srun -n "${SLURM_NTASKS}" python input.py "${WARPX_INPUT_ARG}"', self.text
        )
        self.assertIn("SLURM_NTASKS is not set", self.text)

    def test_runner_is_case_local_and_does_not_run_workflow_phases(self) -> None:
        forbidden_fragments = [
            "rm -rf",
            "cleanup_raw_case",
            "validate_raw_case",
            "validate_reduced_case",
            "analyze_case",
            "mark_raw_delete_eligible",
            "campaign_workflow.cli",
            "optimizer_tick",
            "sbatch",
        ]

        for fragment in forbidden_fragments:
            with self.subTest(fragment=fragment):
                self.assertNotIn(fragment, self.text)

    def test_runner_requires_slurm_and_novas_partition_before_preflight(self) -> None:
        self.assertIn("SLURM_JOB_ID is not set", self.text)
        self.assertIn("SLURM_NTASKS is not set", self.text)
        self.assertIn(
            'EXPECTED_SLURM_PARTITION="${EXPECTED_SLURM_PARTITION:-novas}"', self.text
        )
        self.assertIn("SLURM_JOB_PARTITION", self.text)
        self.assertIn("Expected partition", self.text)

        slurm_guard_index = self.text.index("SLURM_JOB_ID is not set")
        preflight_index = self.text.index("PICMI preflight")
        self.assertLess(slurm_guard_index, preflight_index)

        # This is not an sbatch script. The #SBATCH partition belongs in
        # examples/lynx/submit_case_cycle_array_lynx.sh.
        self.assertNotIn("#SBATCH", self.text)


if __name__ == "__main__":
    unittest.main()
