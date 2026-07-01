from __future__ import annotations

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from campaign_workflow.cli.optimizer_tick import main as optimizer_tick_main


class OptimizerTickPhase6BStoppingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmpdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tmpdir.name) / "clpu_capillary_guiding_bo_001"
        self.iter_root = self.root / "iterations" / "iter_000"
        self.iter_root.mkdir(parents=True)

        self._write_iteration_files()
        self._write_optimization_config()
        self._write_optimization_state(status="closed")
        self._write_stopping_signals()

    def tearDown(self) -> None:
        self.tmpdir.cleanup()

    def test_check_stopping_dry_run_does_not_modify_state(self) -> None:
        state_path = self.root / "optimization_state.json"
        before = state_path.read_text(encoding="utf-8")

        rc, stdout, stderr = self._run_cli(
            "--action",
            "check_stopping",
            "--iteration",
            "0",
            "--dry-run",
        )

        self.assertEqual(rc, 0, stderr)
        self.assertEqual(state_path.read_text(encoding="utf-8"), before)

        data = json.loads(stdout)
        self.assertEqual(data["action"], "check_stopping")
        self.assertIn("stopping_report", data)
        self.assertTrue(data["stopping_report"]["can_propose_next_iteration"])

    def test_check_stopping_write_state_writes_report_and_updates_state(self) -> None:
        rc, stdout, stderr = self._run_cli(
            "--action",
            "check_stopping",
            "--iteration",
            "0",
            "--write-state",
        )

        self.assertEqual(rc, 0, stderr)
        data = json.loads(stdout)

        report_path = Path(data["stopping_report_written"])
        self.assertTrue(report_path.is_file())

        state = json.loads(
            (self.root / "optimization_state.json").read_text(encoding="utf-8")
        )
        self.assertIn("latest_stopping_decision", state)
        self.assertEqual(state["iterations"][0]["stopping_decision"], "continue")

    def test_pause_returns_paused(self) -> None:
        (self.root / "PAUSE_OPTIMIZATION").write_text("pause\n", encoding="utf-8")

        rc, stdout, stderr = self._run_cli(
            "--action",
            "check_stopping",
            "--iteration",
            "0",
            "--dry-run",
        )

        self.assertEqual(rc, 0, stderr)
        data = json.loads(stdout)
        report = data["stopping_report"]
        self.assertEqual(report["overall_decision"], "paused")
        self.assertFalse(report["can_propose_next_iteration"])

    def test_running_iteration_returns_wait_for_jobs(self) -> None:
        self._write_optimization_state(status="running", slurm_active=True)

        rc, stdout, stderr = self._run_cli(
            "--action",
            "check_stopping",
            "--iteration",
            "0",
            "--dry-run",
        )

        self.assertEqual(rc, 0, stderr)
        data = json.loads(stdout)
        report = data["stopping_report"]
        self.assertEqual(report["overall_decision"], "wait_for_jobs")
        self.assertFalse(report["can_propose_next_iteration"])

    def test_max_iterations_reached_returns_stop_budget(self) -> None:
        self._update_stopping({"max_iterations": 1})

        rc, stdout, stderr = self._run_cli(
            "--action",
            "check_stopping",
            "--iteration",
            "0",
            "--dry-run",
        )

        self.assertEqual(rc, 0, stderr)
        report = json.loads(stdout)["stopping_report"]
        self.assertEqual(report["overall_decision"], "stop_budget")
        self.assertFalse(report["can_propose_next_iteration"])

    def test_failure_fraction_high_returns_stop_failure_rate(self) -> None:
        self._write_optimization_state(
            status="closed",
            submitted=10,
            valid=5,
            failed=5,
        )

        rc, stdout, stderr = self._run_cli(
            "--action",
            "check_stopping",
            "--iteration",
            "0",
            "--dry-run",
        )

        self.assertEqual(rc, 0, stderr)
        report = json.loads(stdout)["stopping_report"]
        self.assertEqual(report["overall_decision"], "stop_failure_rate")
        self.assertFalse(report["can_propose_next_iteration"])

    def test_no_new_valid_observations_returns_stop(self) -> None:
        self._write_stopping_signals(n_new_valid=2)

        rc, stdout, stderr = self._run_cli(
            "--action",
            "check_stopping",
            "--iteration",
            "0",
            "--dry-run",
        )

        self.assertEqual(rc, 0, stderr)
        report = json.loads(stdout)["stopping_report"]
        self.assertEqual(
            report["overall_decision"],
            "stop_no_new_valid_observations",
        )

    def test_stale_stopping_signals_are_reported_but_not_used_to_block(self) -> None:
        self._write_stopping_signals(
            n_new_valid=0,
            target_history_iteration=999,
        )

        rc, stdout, stderr = self._run_cli(
            "--action",
            "check_stopping",
            "--iteration",
            "0",
            "--dry-run",
        )

        self.assertEqual(rc, 0, stderr)
        report = json.loads(stdout)["stopping_report"]
        self.assertEqual(report["overall_decision"], "continue")
        self.assertTrue(report["can_propose_next_iteration"])
        self.assertEqual(
            report["details"]["signals_context"]["reason"],
            "stale_target_history_iteration",
        )
        self.assertFalse(report["details"]["signals_context"]["usable_for_iteration"])

    def test_no_improvement_returns_stop_converged(self) -> None:
        self._write_stopping_signals(
            n_new_valid=10,
            improvements={
                "score_guiding_v1": 0.0,
                "score_beamlike_v1": -0.1,
                "score_transverse_v1": 0.005,
            },
            median_dist=0.02,
        )

        rc, stdout, stderr = self._run_cli(
            "--action",
            "check_stopping",
            "--iteration",
            "0",
            "--dry-run",
        )

        self.assertEqual(rc, 0, stderr)
        report = json.loads(stdout)["stopping_report"]
        self.assertEqual(report["overall_decision"], "stop_converged")

    def test_boundary_saturation_warns_but_allows_when_block_false(self) -> None:
        self._write_stopping_signals(boundary_fraction=0.95)

        rc, stdout, stderr = self._run_cli(
            "--action",
            "check_stopping",
            "--iteration",
            "0",
            "--dry-run",
        )

        self.assertEqual(rc, 0, stderr)
        report = json.loads(stdout)["stopping_report"]
        self.assertEqual(report["overall_decision"], "needs_human_review")
        self.assertTrue(report["can_propose_next_iteration"])
        self.assertEqual(report["overall_status"], "warn")

    def test_propose_execute_does_not_call_optimizer_when_stopping_blocks(self) -> None:
        self._write_stopping_signals(
            n_new_valid=10,
            improvements={
                "score_guiding_v1": 0.0,
                "score_beamlike_v1": 0.0,
                "score_transverse_v1": 0.0,
            },
            median_dist=0.02,
        )

        with patch(
            "campaign_workflow.cli.optimizer_tick.run_external_optimizer_command"
        ) as optimizer_mock:
            rc, stdout, stderr = self._run_cli(
                "--action",
                "propose_next_iteration",
                "--from-iteration",
                "0",
                "--next-iteration",
                "1",
                "--execute",
            )

        self.assertEqual(rc, 0, stderr)
        self.assertEqual(stderr, "")
        optimizer_mock.assert_not_called()

        data = json.loads(stdout)
        self.assertTrue(data["propose_blocked_by_stopping"])
        self.assertTrue(data["stopped_before_optimizer"])
        self.assertTrue(data["state_written"])
        self.assertIn("stopping_report_written", data)
        self.assertFalse((self.root / "iterations" / "iter_001").exists())

        state = json.loads(
            (self.root / "optimization_state.json").read_text(encoding="utf-8")
        )
        self.assertEqual(state["latest_stopping_decision"], "stop_converged")

    def test_imports_do_not_pull_optimizer_or_heavy_dependencies(self) -> None:
        root = Path(__file__).resolve().parents[1]
        text = (root / "campaign_workflow" / "stopping.py").read_text(encoding="utf-8")
        import_lines = "\n".join(
            line for line in text.splitlines() if line.startswith(("import ", "from "))
        )

        forbidden = [
            "campaign_optimizer",
            "optimas",
            "botorch",
            "torch",
            "ax",
            "h5py",
            "openpmd",
            "subprocess",
        ]
        for token in forbidden:
            with self.subTest(token=token):
                self.assertNotIn(token, import_lines)

    def _run_cli(self, *args: str) -> tuple[int, str, str]:
        argv = ["--optimization-root", str(self.root), *args]
        stdout = io.StringIO()
        stderr = io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            rc = optimizer_tick_main(argv)
        return rc, stdout.getvalue(), stderr.getvalue()

    def _write_iteration_files(self) -> None:
        campaign = {
            "schema_version": 1,
            "campaign_name": "iter_000",
            "case_manifest": "cases.tsv",
            "case_manifest_format": "tsv",
            "case_id_column": "CASE_ID",
            "case_name_column": "CASE_NAME",
            "simulation": {
                "backend": "warpx_picmi",
                "scheduler": "slurm",
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
            "state": {
                "state_file": "state.json",
                "validation_file": "validation.json",
                "locks_dir": "locks",
                "manifests_dir": "manifests",
                "post_dir": "post",
                "logs_dir": "logs",
            },
        }
        (self.iter_root / "campaign.json").write_text(
            json.dumps(campaign), encoding="utf-8"
        )
        (self.iter_root / "cases.tsv").write_text(
            "CASE_ID\tCASE_NAME\n",
            encoding="utf-8",
        )
        (self.iter_root / "input_template.py").write_text(
            "# template\n", encoding="utf-8"
        )
        (self.iter_root / "array_logs").mkdir(exist_ok=True)

    def _write_optimization_config(self) -> None:
        data = {
            "schema_version": 1,
            "optimization_name": "clpu_capillary_guiding_bo_001",
            "optimizer": {
                "working_directory": ".",
                "command": ["python", "-m", "fake_optimizer"],
                "optimizer_config": "optimizer.json",
            },
            "campaign_preparation": {
                "template_campaign_root": "iterations/iter_000",
                "iterations_dir": "iterations",
                "optimizer_runs_dir": "optimizer_runs",
            },
            "policy": {
                "max_iterations": 20,
                "max_total_submitted_cases": 200,
                "min_reduced_valid_to_continue": 5,
                "min_valid_fraction_to_continue": 0.6,
                "max_failed_fraction_to_continue": 0.4,
            },
            "stopping": {
                "enabled": True,
                "mode": "hard",
                "max_iterations": 20,
                "max_total_submitted_cases": 200,
                "min_new_valid_observations": 5,
                "min_valid_fraction_to_continue": 0.6,
                "max_failed_fraction_to_continue": 0.4,
                "no_improvement": {
                    "enabled": True,
                    "window": 3,
                    "min_delta": 0.01,
                    "objectives": [
                        "score_guiding_v1",
                        "score_beamlike_v1",
                        "score_transverse_v1",
                    ],
                    "aggregation": "any",
                },
                "candidate_novelty": {
                    "enabled": True,
                    "min_median_nearest_known_scaled_dist": 0.025,
                },
                "boundary_saturation": {
                    "enabled": True,
                    "review_threshold": 0.85,
                    "block": False,
                },
            },
        }
        (self.root / "optimization.json").write_text(
            json.dumps(data, indent=2), encoding="utf-8"
        )

    def _update_stopping(self, updates: dict) -> None:
        path = self.root / "optimization.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        data["stopping"].update(updates)
        path.write_text(json.dumps(data, indent=2), encoding="utf-8")

    def _write_optimization_state(
        self,
        *,
        status: str,
        submitted: int = 10,
        valid: int = 10,
        failed: int = 0,
        slurm_active: bool = False,
    ) -> None:
        state = {
            "schema_version": 1,
            "optimization_name": "clpu_capillary_guiding_bo_001",
            "status": status,
            "latest_iteration": 0,
            "updated_at": "2026-01-01T00:00:00Z",
            "iterations": [
                {
                    "iteration": 0,
                    "optimizer_run_dir": "optimizer_runs/iter_000",
                    "campaign_root": "iterations/iter_000",
                    "status": status,
                    "recommended_action": (
                        "wait_for_jobs"
                        if status in {"running", "submitted", "postprocessing"}
                        else "propose_next_iteration"
                    ),
                    "submitted": True,
                    "submitted_case_count": submitted,
                    "n_submitted_case_dirs": submitted,
                    "n_submitted_reduced_valid": valid,
                    "n_submitted_sim_failed": failed,
                    "n_cases": submitted,
                    "n_reduced_valid": valid,
                    "n_sim_failed": failed,
                    "slurm": {"active": slurm_active},
                }
            ],
            "recommended_action": "propose_next_iteration",
        }
        (self.root / "optimization_state.json").write_text(
            json.dumps(state, indent=2), encoding="utf-8"
        )

    def _write_stopping_signals(
        self,
        *,
        n_new_valid: int = 10,
        improvements: dict[str, float] | None = None,
        median_dist: float = 0.1,
        boundary_fraction: float = 0.1,
        target_history_iteration: int | None = None,
    ) -> None:
        if improvements is None:
            improvements = {
                "score_guiding_v1": 0.1,
                "score_beamlike_v1": 0.0,
                "score_transverse_v1": 0.0,
            }
        signals = {
            "n_fit_eligible_total": 100,
            "n_new_valid_observations": n_new_valid,
            "best_scores": {
                "score_guiding_v1": 1.0,
                "score_beamlike_v1": 2.0,
                "score_transverse_v1": 3.0,
            },
            "best_score_improvement": improvements,
            "best_score_improvement_window": {
                "window": 3,
                "status": "ok",
                "values": improvements,
            },
            "candidate_novelty": {
                "status": "ok",
                "median_nearest_known_scaled_dist": median_dist,
                "min_nearest_known_scaled_dist": median_dist / 2.0,
            },
            "boundary_saturation": {
                "status": "ok",
                "fraction_candidates_near_boundary": boundary_fraction,
                "parameters": {"diameter_um_num": boundary_fraction},
            },
            "surrogate_reliability": {
                "status": "unknown",
                "reason": "test fixture",
            },
        }
        if target_history_iteration is not None:
            signals["target_history_iteration"] = target_history_iteration

        report = {
            "schema_version": 1,
            "iteration": 0,
            "objective_config_id": "capillary_objectives_v1",
            "signals": signals,
            "recommendation": {
                "optimizer_view": "continue",
                "reasons": [],
            },
        }

        path = (
            self.root
            / "optimizer_runs"
            / "iter_000"
            / "reports"
            / "stopping_signals.json"
        )
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(report, indent=2), encoding="utf-8")


if __name__ == "__main__":
    unittest.main()
