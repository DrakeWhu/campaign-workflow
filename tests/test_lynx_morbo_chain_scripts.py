from __future__ import annotations

import unittest
from pathlib import Path


class LynxMorboChainScriptsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.array_script = Path("examples/lynx/run_iteration_array_lynx.sh")
        self.tick_script = Path(
            "examples/lynx/run_optimizer_tick_materialize_only_lynx.sh"
        )
        self.array_text = self.array_script.read_text(encoding="utf-8")
        self.tick_text = self.tick_script.read_text(encoding="utf-8")

    def test_scripts_exist_and_use_strict_bash(self) -> None:
        for path, text in (
            (self.array_script, self.array_text),
            (self.tick_script, self.tick_text),
        ):
            with self.subTest(path=str(path)):
                self.assertTrue(path.is_file())
                self.assertTrue(text.startswith("#!/usr/bin/env bash\n"))
                self.assertNotIn("\r\n", text)
                self.assertIn("set -Eeuo pipefail", text)
                self.assertIn("trap '", text)

    def test_scripts_use_lynx_novas_partition_only(self) -> None:
        for text in (self.array_text, self.tick_text):
            with self.subTest(script=text.splitlines()[0]):
                self.assertIn("#SBATCH --partition=novas", text)
                self.assertIn(
                    'EXPECTED_SLURM_PARTITION="${EXPECTED_SLURM_PARTITION:-novas}"',
                    text,
                )
                self.assertIn("SLURM_JOB_PARTITION", text)
                self.assertIn("Expected partition", text)

                forbidden_partitions = [
                    "#SBATCH --partition=T6H",
                    "#SBATCH --partition=T12H",
                    "#SBATCH --partition=T24H",
                    "#SBATCH --partition=T48H",
                ]
                for fragment in forbidden_partitions:
                    self.assertNotIn(fragment, text)

    def test_iteration_array_delegates_to_lynx_case_cycle(self) -> None:
        self.assertIn("CW_OPTIMIZATION_ROOT", self.array_text)
        self.assertIn("CW_ITERATION", self.array_text)
        self.assertIn("CW_WORKFLOW_ROOT", self.array_text)
        self.assertIn("CW_WORKFLOW_ENV", self.array_text)
        self.assertIn("SLURM_ARRAY_TASK_ID", self.array_text)

        self.assertIn("examples/lynx/submit_case_cycle_array_lynx.sh", self.array_text)
        self.assertIn('exec bash "${CASE_CYCLE_SCRIPT}"', self.array_text)

        self.assertNotIn("examples/sunrise", self.array_text)
        self.assertNotIn("submit_case_cycle_array.sh", self.array_text)
        self.assertNotIn("run_warpx_case_sunrise.sh", self.array_text)

    def test_iteration_array_requires_warpx_lynx_module(self) -> None:
        self.assertIn("WARPX_LYNX_MODULE:?", self.array_text)
        self.assertIn("26.03_lynx_cpu_rz_yee_openpmd_py311", self.array_text)
        self.assertIn("export WARPX_LYNX_MODULE", self.array_text)

    def test_iteration_array_has_stop_guards_and_noop_for_missing_case(self) -> None:
        self.assertIn("STOP_OPTIMIZATION", self.array_text)
        self.assertIn("optimization_state.json", self.array_text)
        self.assertIn("status=stopped", self.array_text)
        self.assertIn("skipping as no-op", self.array_text)
        self.assertIn("cases.tsv", self.array_text)
        self.assertIn("CASE_ID", self.array_text)
        self.assertIn("CASE_NAME", self.array_text)

    def test_tick_script_materializes_only_and_uses_lynx_workflow_env(self) -> None:
        self.assertIn("campaign_workflow.cli.optimizer_tick", self.tick_text)
        self.assertIn("--action run_loop_once", self.tick_text)
        self.assertIn("--stop-after-materialization", self.tick_text)
        self.assertIn("--execute", self.tick_text)
        self.assertIn("CW_WORKFLOW_ENV", self.tick_text)
        self.assertIn('source "${WORKFLOW_ENV}"', self.tick_text)

        self.assertNotIn("examples/sunrise", self.tick_text)
        self.assertNotIn("warpx-26.05-py314", self.tick_text)
        self.assertNotIn("campaign-workflow.sh", self.tick_text)

    def test_static_array_and_tick_scripts_do_not_submit_jobs(self) -> None:
        for text in (self.array_text, self.tick_text):
            with self.subTest(script=text.splitlines()[0]):
                self.assertNotIn("sbatch ", text)
                self.assertNotIn("subprocess.run", text)
                self.assertNotIn("parse_sbatch_job_id", text)

    def test_scripts_do_not_embed_destructive_or_physics_generation_logic(self) -> None:
        forbidden_fragments = [
            "rm -rf",
            "sed -i",
            "perl -pi",
            "cat > input.py",
            "tee input.py",
            "module load WarpX/",
            "CAP_DRY_RUN",
            "python input.py",
            "BoTorch",
        ]

        for text in (self.array_text, self.tick_text):
            with self.subTest(script=text.splitlines()[0]):
                for fragment in forbidden_fragments:
                    self.assertNotIn(fragment, text)


if __name__ == "__main__":
    unittest.main()
