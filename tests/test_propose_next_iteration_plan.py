from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from campaign_workflow.propose_next_iteration import (
    ProposeNextIterationError,
    build_propose_next_iteration_plan,
)


class ProposeNextIterationPlanTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmpdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tmpdir.name) / "clpu_capillary_guiding_bo_001"
        (self.root / "iterations" / "iter_000").mkdir(parents=True)
        self._write_optimization_config()
        self.state_doc = {
            "schema_version": 1,
            "optimization_name": "clpu_capillary_guiding_bo_001",
            "status": "running",
            "latest_iteration": 0,
            "iterations": [
                {
                    "iteration": 0,
                    "campaign_root": "iterations/iter_000",
                    "status": "reduced_ready",
                    "recommended_action": "close_iteration",
                    "submitted_case_count": 10,
                    "n_submitted_reduced_valid": 10,
                    "n_submitted_sim_failed": 0,
                }
            ],
        }
        self.tick_summary = {
            "pause_file_exists": False,
            "iterations": [
                {
                    "iteration": 0,
                    "campaign_root": "iterations/iter_000",
                    "status": "reduced_ready",
                    "recommended_action": "close_iteration",
                }
            ],
        }

    def tearDown(self) -> None:
        self.tmpdir.cleanup()

    def test_builds_dry_run_plan_without_executing_optimizer(self) -> None:
        plan = build_propose_next_iteration_plan(
            tick_summary=self.tick_summary,
            state_doc=self.state_doc,
            optimization_root=self.root,
            from_iteration=0,
            next_iteration=1,
        )

        data = plan.to_dict()
        self.assertEqual(data["from_iteration"], 0)
        self.assertEqual(data["next_iteration"], 1)
        candidate_batch = Path(data["expected_outputs"]["candidate_batch"])
        batch_campaign_plan = Path(data["expected_outputs"]["batch_campaign_plan"])
        output_campaign_root = Path(data["output_campaign_root"])

        self.assertEqual(
            candidate_batch.parts[-4:],
            ("optimizer_runs", "iter_001", "outputs", "candidate_batch.tsv"),
        )
        self.assertEqual(
            batch_campaign_plan.parts[-4:],
            ("optimizer_runs", "iter_001", "outputs", "batch_campaign_plan.json"),
        )
        self.assertEqual(
            output_campaign_root.parts[-2:],
            ("iterations", "iter_001"),
        )
        self.assertIn("materialize_next_campaign_cases", data["planned_steps"])
        self.assertIn("init_next_campaign_case_states", data["planned_steps"])
        self.assertIn("stop_before_submit", data["planned_steps"])
        self.assertEqual(
            data["external_command"]["raw_command"],
            [
                "python",
                "-m",
                "campaign_optimizer.cli.run_iteration",
                "--config",
                str((self.root / "optimizer.json").resolve(strict=False)),
                "--iteration",
                "1",
                "--build-candidate-batch",
            ],
        )

    def test_pause_file_blocks_plan(self) -> None:
        (self.root / "PAUSE_OPTIMIZATION").write_text("pause\n", encoding="utf-8")

        with self.assertRaisesRegex(ProposeNextIterationError, "PAUSE_OPTIMIZATION"):
            build_propose_next_iteration_plan(
                tick_summary=self.tick_summary,
                state_doc=self.state_doc,
                optimization_root=self.root,
                from_iteration=0,
                next_iteration=1,
            )

    def test_running_iteration_blocks_plan_with_wait_for_jobs_reason(self) -> None:
        self.state_doc["iterations"][0]["status"] = "running"
        self.state_doc["iterations"][0]["recommended_action"] = "wait_for_jobs"
        self.state_doc["iterations"][0]["slurm"] = {"active": True}

        with self.assertRaisesRegex(ProposeNextIterationError, "wait_for_jobs"):
            build_propose_next_iteration_plan(
                tick_summary=self.tick_summary,
                state_doc=self.state_doc,
                optimization_root=self.root,
                from_iteration=0,
                next_iteration=1,
            )

    def test_too_many_failures_blocks_plan(self) -> None:
        self.state_doc["iterations"][0]["n_submitted_sim_failed"] = 5

        with self.assertRaisesRegex(ProposeNextIterationError, "failed fraction"):
            build_propose_next_iteration_plan(
                tick_summary=self.tick_summary,
                state_doc=self.state_doc,
                optimization_root=self.root,
                from_iteration=0,
                next_iteration=1,
            )

    def test_max_iterations_blocks_plan(self) -> None:
        config = json.loads(
            (self.root / "optimization.json").read_text(encoding="utf-8")
        )
        config["policy"]["max_iterations"] = 1
        (self.root / "optimization.json").write_text(
            json.dumps(config, indent=2) + "\n", encoding="utf-8"
        )

        with self.assertRaisesRegex(ProposeNextIterationError, "max_iterations"):
            build_propose_next_iteration_plan(
                tick_summary=self.tick_summary,
                state_doc=self.state_doc,
                optimization_root=self.root,
                from_iteration=0,
                next_iteration=1,
            )

    def test_missing_optimization_json_fails_clearly(self) -> None:
        (self.root / "optimization.json").unlink()

        with self.assertRaisesRegex(
            ProposeNextIterationError, "missing optimization config"
        ):
            build_propose_next_iteration_plan(
                tick_summary=self.tick_summary,
                state_doc=self.state_doc,
                optimization_root=self.root,
                from_iteration=0,
                next_iteration=1,
            )

    def test_forbidden_direct_submit_command_is_rejected(self) -> None:
        config = json.loads(
            (self.root / "optimization.json").read_text(encoding="utf-8")
        )
        config["optimizer"]["command"] = ["sbatch", "bad.sh"]
        (self.root / "optimization.json").write_text(
            json.dumps(config, indent=2) + "\n", encoding="utf-8"
        )

        with self.assertRaisesRegex(
            ProposeNextIterationError, "unsafe optimizer command"
        ):
            build_propose_next_iteration_plan(
                tick_summary=self.tick_summary,
                state_doc=self.state_doc,
                optimization_root=self.root,
                from_iteration=0,
                next_iteration=1,
            )

    def _write_optimization_config(self) -> None:
        config = {
            "schema_version": 1,
            "optimization_name": "clpu_capillary_guiding_bo_001",
            "optimizer": {
                "working_directory": ".",
                "command": [
                    "python",
                    "-m",
                    "campaign_optimizer.cli.run_iteration",
                    "--config",
                    "{optimizer_config}",
                    "--iteration",
                    "{next_iteration}",
                    "--build-candidate-batch",
                ],
                "optimizer_config": "optimizer.json",
            },
            "campaign_preparation": {
                "template_campaign_root": "iterations/iter_000",
                "iterations_dir": "iterations",
                "optimizer_runs_dir": "optimizer_runs",
                "materialize_after_prepare": True,
                "init_case_states_after_materialize": True,
            },
            "policy": {
                "max_iterations": 20,
                "max_total_submitted_cases": 200,
                "min_reduced_valid_to_continue": 5,
                "min_valid_fraction_to_continue": 0.6,
                "max_failed_fraction_to_continue": 0.4,
            },
        }
        self.root.mkdir(parents=True, exist_ok=True)
        (self.root / "optimization.json").write_text(
            json.dumps(config, indent=2) + "\n", encoding="utf-8"
        )


if __name__ == "__main__":
    unittest.main()
