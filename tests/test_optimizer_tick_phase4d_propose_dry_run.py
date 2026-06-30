from __future__ import annotations

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

from campaign_workflow.cli.optimizer_tick import main as optimizer_tick_main


class OptimizerTickPhase4DProposeDryRunTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmpdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tmpdir.name) / "clpu_capillary_guiding_bo_001"
        self.iter_root = self.root / "iterations" / "iter_000"
        self.iter_root.mkdir(parents=True)
        self._write_minimal_iteration()
        self._write_optimization_state()
        self._write_optimization_config()

    def tearDown(self) -> None:
        self.tmpdir.cleanup()

    def test_propose_next_iteration_dry_run_outputs_plan_and_does_not_write_state(
        self,
    ) -> None:
        state_path = self.root / "optimization_state.json"
        before = state_path.read_text(encoding="utf-8")

        rc, stdout, stderr = self._run_cli(
            "--action",
            "propose_next_iteration",
            "--from-iteration",
            "0",
            "--next-iteration",
            "1",
            "--dry-run",
        )

        self.assertEqual(rc, 0, stderr)
        self.assertEqual(state_path.read_text(encoding="utf-8"), before)

        data = json.loads(stdout)
        self.assertEqual(data["action"], "propose_next_iteration")
        self.assertEqual(data["mode"], "dry-run")
        self.assertFalse(data["state_written"])
        self.assertTrue(data["execution_enabled_in_this_phase"])

        plan = data["propose_next_iteration_plan"]
        self.assertEqual(plan["from_iteration"], 0)
        self.assertEqual(plan["next_iteration"], 1)

        candidate_batch = Path(plan["expected_outputs"]["candidate_batch"])
        batch_campaign_plan = Path(plan["expected_outputs"]["batch_campaign_plan"])
        output_campaign_root = Path(plan["output_campaign_root"])

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

        self.assertIn("run_external_optimizer_command", plan["planned_steps"])
        self.assertIn("verify_optimizer_outputs", plan["planned_steps"])
        self.assertIn("prepare_next_campaign_root", plan["planned_steps"])
        self.assertIn("stop_before_submit", plan["planned_steps"])

        self.assertFalse((self.root / "optimizer_runs" / "iter_001").exists())
        self.assertFalse((self.root / "iterations" / "iter_001").exists())

    def test_propose_next_iteration_requires_from_iteration(self) -> None:
        rc, stdout, stderr = self._run_cli(
            "--action",
            "propose_next_iteration",
            "--next-iteration",
            "1",
            "--dry-run",
        )

        self.assertEqual(rc, 2)
        self.assertEqual(stdout, "")
        self.assertIn("requires --from-iteration", stderr)

    def test_propose_next_iteration_rejects_write_state_mode(self) -> None:
        rc, stdout, stderr = self._run_cli(
            "--action",
            "propose_next_iteration",
            "--from-iteration",
            "0",
            "--next-iteration",
            "1",
            "--write-state",
        )

        self.assertEqual(rc, 2)
        self.assertEqual(stdout, "")
        self.assertIn("supports only --dry-run or --execute", stderr)

    def test_propose_next_iteration_requires_optimization_state(self) -> None:
        (self.root / "optimization_state.json").unlink()

        rc, stdout, stderr = self._run_cli(
            "--action",
            "propose_next_iteration",
            "--from-iteration",
            "0",
            "--next-iteration",
            "1",
            "--dry-run",
        )

        self.assertEqual(rc, 1)
        self.assertEqual(stdout, "")
        self.assertIn("optimization_state.json is required", stderr)

    def test_propose_next_iteration_rejects_iteration_filter(self) -> None:
        rc, stdout, stderr = self._run_cli(
            "--iteration",
            "0",
            "--action",
            "propose_next_iteration",
            "--from-iteration",
            "0",
            "--next-iteration",
            "1",
            "--dry-run",
        )

        self.assertEqual(rc, 2)
        self.assertEqual(stdout, "")
        self.assertIn("--iteration is not used", stderr)

    def _run_cli(self, *args: str) -> tuple[int, str, str]:
        argv = ["--optimization-root", str(self.root), *args]
        stdout = io.StringIO()
        stderr = io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            rc = optimizer_tick_main(argv)
        return rc, stdout.getvalue(), stderr.getvalue()

    def _write_minimal_iteration(self) -> None:
        (self.iter_root / "campaign.json").write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "campaign_name": "clpu_capillary_guiding_bo_001_iter_000",
                    "case_manifest": "cases.tsv",
                    "case_manifest_format": "tsv",
                    "case_id_column": "CASE_ID",
                    "case_name_column": "CASE_NAME",
                    "state": {
                        "state_file": "state.json",
                        "validation_file": "validation.json",
                        "locks_dir": "locks",
                        "manifests_dir": "manifests",
                        "post_dir": "post",
                        "logs_dir": "logs",
                    },
                    "simulation": {
                        "backend": "warpx_picmi",
                        "scheduler": "slurm",
                        "input_script": "input.py",
                        "completion_marker": "post/sim_done.json",
                        "failure_marker": "post/sim_failed.json",
                    },
                    "analysis": {
                        "outputs": [
                            {
                                "name": "guiding_metrics",
                                "kind": "csv",
                                "path": "guiding_metrics.csv",
                                "required": True,
                            }
                        ]
                    },
                }
            )
            + "\n",
            encoding="utf-8",
        )
        (self.iter_root / "cases.tsv").write_text(
            "CASE_ID\tCASE_NAME\n0\t000_case\n1\t001_case\n2\t002_case\n",
            encoding="utf-8",
        )
        (self.iter_root / "input_template.py").write_text(
            "# fake template\n", encoding="utf-8"
        )
        (self.iter_root / "array_logs").mkdir()

    def _write_optimization_state(self) -> None:
        state = {
            "schema_version": 1,
            "optimization_name": "clpu_capillary_guiding_bo_001",
            "status": "running",
            "latest_iteration": 0,
            "iterations": [
                {
                    "iteration": 0,
                    "optimizer_run_dir": "optimizer_runs/iter_000",
                    "campaign_root": "iterations/iter_000",
                    "status": "reduced_ready",
                    "submitted": True,
                    "slurm_job_ids": ["611084"],
                    "submitted_case_count": 10,
                    "n_cases": 30,
                    "n_submitted_reduced_valid": 10,
                    "n_submitted_sim_failed": 0,
                    "recommended_action": "close_iteration",
                    "slurm": {
                        "queried": True,
                        "available": True,
                        "active": False,
                        "reason": "ok",
                        "job_ids": ["611084"],
                        "states": {},
                    },
                }
            ],
        }
        self.root.mkdir(parents=True, exist_ok=True)
        (self.root / "optimization_state.json").write_text(
            json.dumps(state, indent=2) + "\n",
            encoding="utf-8",
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
        (self.root / "optimization.json").write_text(
            json.dumps(config, indent=2) + "\n",
            encoding="utf-8",
        )


if __name__ == "__main__":
    unittest.main()
