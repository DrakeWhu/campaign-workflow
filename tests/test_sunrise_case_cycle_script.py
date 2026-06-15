from __future__ import annotations

import unittest
from pathlib import Path


class SunriseCaseCycleScriptTests(unittest.TestCase):
    def setUp(self) -> None:
        self.script_path = Path("examples/sunrise/submit_case_cycle_array.sh")
        self.text = self.script_path.read_text(encoding="utf-8")

    def test_script_exists_and_uses_strict_bash(self) -> None:
        self.assertTrue(self.script_path.exists())
        self.assertTrue(self.text.startswith("#!/usr/bin/env bash\n"))
        self.assertNotIn("\r\n", self.text)
        self.assertIn("set -Eeuo pipefail", self.text)
        self.assertIn("trap '", self.text)
        self.assertIn("#SBATCH --array=0-0", self.text)

    def test_selects_case_from_cases_tsv_using_array_task_id(self) -> None:
        self.assertIn("SLURM_ARRAY_TASK_ID", self.text)
        self.assertIn("cases.tsv", self.text)
        self.assertIn("CASE_ID", self.text)
        self.assertIn("CASE_NAME", self.text)
        self.assertIn("CASE_ROW", self.text)
        self.assertIn("awk -v task_id=", self.text)
        self.assertIn("CASE_DIR=", self.text)

    def test_calls_expected_case_local_cycle_phases(self) -> None:
        expected_fragments = [
            "campaign_workflow.cli.mark_sim_submitted",
            "campaign_workflow.cli.mark_sim_running",
            "run_warpx_case_sunrise.sh",
            "campaign_workflow.cli.mark_sim_failed",
            "campaign_workflow.cli.mark_sim_done",
            "campaign_workflow.cli.validate_raw_case",
            "campaign_workflow.cli.analyze_case",
            "campaign_workflow.cli.mark_raw_delete_eligible",
            "campaign_workflow.cli.cleanup_raw_case",
            "--dry-run",
        ]

        for fragment in expected_fragments:
            with self.subTest(fragment=fragment):
                self.assertIn(fragment, self.text)

    def test_cleanup_execute_requires_explicit_confirmation(self) -> None:
        confirm_index = self.text.index('CONFIRM_CLEANUP_EXECUTE:-0')
        execute_index = self.text.index('--execute')
        self.assertLess(confirm_index, execute_index)
        self.assertIn('[[ "${CONFIRM_CLEANUP_EXECUTE:-0}" == "1" ]]', self.text)
        self.assertIn("cleanup_raw_case_execute", self.text)
        self.assertIn("cleanup_raw_case_dry_run", self.text)

    def test_keeps_environments_separated(self) -> None:
        self.assertIn("WORKFLOW_ENV", self.text)
        self.assertIn("campaign-workflow-py310", self.text)
        self.assertIn("warpx-26.05-py314", self.text)
        self.assertIn("load_workflow_env", self.text)
        self.assertNotIn("guiding-analysis-py310/bin/activate", self.text)
        self.assertNotIn("guiding_analysis_module", self.text)

    def test_does_not_embed_forbidden_logic_or_destructive_shell_ops(self) -> None:
        forbidden_fragments = [
            "rm -rf",
            "optimizer_tick",
            "optimizer",
            "MORBO",
            "BoTorch",
            "sed -i",
            "perl -pi",
            "cat > input.py",
            "tee input.py",
            "python input.py",
        ]

        for fragment in forbidden_fragments:
            with self.subTest(fragment=fragment):
                self.assertNotIn(fragment, self.text)


if __name__ == "__main__":
    unittest.main()