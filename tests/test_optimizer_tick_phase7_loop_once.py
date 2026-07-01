from __future__ import annotations

import contextlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from campaign_workflow.cli.optimizer_tick import main as optimizer_tick_main
from campaign_workflow.core.state import now_utc
from campaign_workflow.optimizer_loop import DependentOptimizerTickResult
from campaign_workflow.submit_iteration import SubmitIterationResult


class CompletedProcessStub:
    def __init__(self, returncode: int, stdout: str = "", stderr: str = "") -> None:
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


class OptimizerTickPhase7LoopOnceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmpdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tmpdir.name) / "clpu_capillary_guiding_bo_002"
        self.iter_root = self.root / "iterations" / "iter_000"
        self.iter_root.mkdir(parents=True)
        self.fake_optimizer = self.root / "fake_optimizer.py"
        self._write_completed_iteration_000(n_cases=2)
        self._write_optimization_state(max_iterations=5)
        self._write_fake_optimizer_success()
        self._write_optimization_config(
            command=[
                sys.executable,
                str(self.fake_optimizer),
                "{optimizer_run_dir}",
                "{next_iteration}",
            ],
            max_iterations=5,
        )

    def tearDown(self) -> None:
        self.tmpdir.cleanup()

    def test_run_loop_once_dry_run_plans_without_sbatch_or_optimizer_execution(
        self,
    ) -> None:
        before_state = (self.root / "optimization_state.json").read_text(
            encoding="utf-8"
        )
        with (
            patch(
                "campaign_workflow.optimizer_loop.execute_submit_iteration"
            ) as submit_mock,
            patch(
                "campaign_workflow.optimizer_loop.execute_dependent_optimizer_tick_submit"
            ) as tick_submit_mock,
            patch(
                "campaign_workflow.optimizer_loop.run_external_optimizer_command"
            ) as optimizer_mock,
        ):
            rc, stdout, stderr = self._run_cli(
                "--action",
                "run_loop_once",
                "--iteration",
                "0",
                "--next-iteration",
                "1",
                "--array-spec",
                "0-1",
                "--job-name-prefix",
                "cw_bo002",
                "--dry-run",
            )

        self.assertEqual(rc, 0, stderr)
        submit_mock.assert_not_called()
        tick_submit_mock.assert_not_called()
        optimizer_mock.assert_not_called()
        self.assertEqual(
            (self.root / "optimization_state.json").read_text(encoding="utf-8"),
            before_state,
        )
        self.assertFalse((self.root / "iterations" / "iter_001").exists())

        data = json.loads(stdout)
        self.assertEqual(data["action"], "run_loop_once")
        self.assertEqual(data["mode"], "dry-run")
        self.assertFalse(data["state_written"])
        self.assertEqual(data["reconciliation"]["status"], "reduced_ready")
        self.assertEqual(data["stopping_report"]["overall_decision"], "continue")
        self.assertEqual(data["propose_next_iteration_plan"]["next_iteration"], 1)
        self.assertEqual(
            data["submit_plan_after_materialization"]["job_name"],
            "cw_bo002_iter001_cycle",
        )
        self.assertIn(
            "--dependency=afterany:<next-array-job-id>",
            data["dependent_optimizer_tick_plan"]["submit_command"],
        )
        self.assertIn(
            "--action run_loop_once", data["dependent_optimizer_tick_script_preview"]
        )
        self.assertIn("--iteration 1", data["dependent_optimizer_tick_script_preview"])
        self.assertIn(
            "--next-iteration 2", data["dependent_optimizer_tick_script_preview"]
        )

    def test_run_loop_once_execute_proposes_submits_next_array_and_dependent_tick(
        self,
    ) -> None:
        with (
            patch(
                "campaign_workflow.optimizer_loop.execute_submit_iteration"
            ) as submit_mock,
            patch(
                "campaign_workflow.optimizer_loop.execute_dependent_optimizer_tick_submit"
            ) as tick_submit_mock,
        ):
            submit_mock.return_value = SubmitIterationResult(
                job_id="700001",
                return_code=0,
                stdout="700001\n",
                stderr="",
                submitted_at=now_utc(),
            )
            tick_submit_mock.return_value = DependentOptimizerTickResult(
                job_id="700002",
                return_code=0,
                stdout="700002;cluster\n",
                stderr="",
                submitted_at=now_utc(),
            )

            rc, stdout, stderr = self._run_cli(
                "--action",
                "run_loop_once",
                "--iteration",
                "0",
                "--next-iteration",
                "1",
                "--array-spec",
                "0-1",
                "--workflow-root",
                str(Path.cwd()),
                "--workflow-env",
                str(self.root / "workflow-env.sh"),
                "--job-name-prefix",
                "cw_bo002",
                "--execute",
            )

        self.assertEqual(rc, 0, stderr)
        submit_mock.assert_called_once()
        tick_submit_mock.assert_called_once()

        data = json.loads(stdout)
        self.assertEqual(data["submission_result"]["job_id"], "700001")
        self.assertEqual(data["dependent_optimizer_tick_result"]["job_id"], "700002")
        self.assertEqual(data["recommended_action"], "wait_for_jobs")

        state = json.loads(
            (self.root / "optimization_state.json").read_text(encoding="utf-8")
        )
        iterations = {int(item["iteration"]): item for item in state["iterations"]}
        self.assertEqual(iterations[0]["status"], "closed")
        self.assertEqual(iterations[1]["status"], "submitted")
        self.assertTrue(iterations[1]["submitted"])
        self.assertEqual(iterations[1]["slurm_job_ids"], ["700001"])
        self.assertEqual(iterations[1]["optimizer_tick_job_ids"], ["700002"])
        self.assertEqual(
            iterations[1]["dependent_optimizer_tick"]["dependency"],
            "afterany:700001",
        )
        self.assertEqual(
            iterations[1]["dependent_optimizer_tick"]["next_iteration"],
            2,
        )

        script_path = Path(iterations[1]["dependent_optimizer_tick"]["script_path"])
        self.assertTrue(script_path.is_file())
        script_text = script_path.read_text(encoding="utf-8")
        self.assertIn("--action run_loop_once", script_text)
        self.assertIn("--iteration 1", script_text)
        self.assertIn("--next-iteration 2", script_text)
        self.assertIn("--array-spec 0-1", script_text)
        self.assertIn("--job-name-prefix cw_bo002", script_text)
        self.assertIn("--execute", script_text)

        array_command = data["submit_plan"]["submit_command"]
        self.assertIn("--array=0-1", array_command)
        self.assertIn("--job-name=cw_bo002_iter001_cycle", array_command)

        tick_command = data["dependent_optimizer_tick_plan"]["submit_command"]
        self.assertIn("--dependency=afterany:700001", tick_command)
        self.assertIn("--job-name=cw_bo002_tick001", tick_command)

    def test_run_loop_once_execute_stopping_blocks_before_submits(self) -> None:
        self._write_optimization_state(max_iterations=1)
        self._write_optimization_config(
            command=[
                sys.executable,
                str(self.fake_optimizer),
                "{optimizer_run_dir}",
                "{next_iteration}",
            ],
            max_iterations=1,
            stopping_enabled=True,
        )

        with (
            patch(
                "campaign_workflow.optimizer_loop.execute_submit_iteration"
            ) as submit_mock,
            patch(
                "campaign_workflow.optimizer_loop.execute_dependent_optimizer_tick_submit"
            ) as tick_submit_mock,
        ):
            rc, stdout, stderr = self._run_cli(
                "--action",
                "run_loop_once",
                "--iteration",
                "0",
                "--next-iteration",
                "1",
                "--array-spec",
                "0-1",
                "--execute",
            )

        self.assertEqual(rc, 0, stderr)
        submit_mock.assert_not_called()
        tick_submit_mock.assert_not_called()
        data = json.loads(stdout)
        self.assertTrue(data["loop_stopped_before_propose"])
        self.assertTrue(data["propose_blocked_by_stopping"])
        self.assertEqual(data["recommended_action"], "no_action")
        self.assertTrue(
            (self.root / "stopping_reports" / "iter_000_stopping_report.json").is_file()
        )
        self.assertFalse((self.root / "iterations" / "iter_001").exists())

    def test_run_loop_once_execute_legacy_policy_limit_stops_without_submits(
        self,
    ) -> None:
        self._write_optimization_state(max_iterations=1)
        self._write_optimization_config(
            command=[
                sys.executable,
                str(self.fake_optimizer),
                "{optimizer_run_dir}",
                "{next_iteration}",
            ],
            max_iterations=1,
        )

        with (
            patch(
                "campaign_workflow.optimizer_loop.execute_submit_iteration"
            ) as submit_mock,
            patch(
                "campaign_workflow.optimizer_loop.execute_dependent_optimizer_tick_submit"
            ) as tick_submit_mock,
        ):
            rc, stdout, stderr = self._run_cli(
                "--action",
                "run_loop_once",
                "--iteration",
                "0",
                "--next-iteration",
                "1",
                "--array-spec",
                "0-1",
                "--execute",
            )

        self.assertEqual(rc, 0, stderr)
        submit_mock.assert_not_called()
        tick_submit_mock.assert_not_called()
        data = json.loads(stdout)
        self.assertTrue(data["loop_stopped_before_propose"])
        self.assertTrue(data["propose_blocked_by_policy_or_idempotence"])
        self.assertEqual(data["recommended_action"], "no_action")
        self.assertIn("max_iterations reached", data["propose_error"])
        self.assertFalse((self.root / "iterations" / "iter_001").exists())

    def test_run_loop_once_rejects_write_state_mode(self) -> None:
        rc, stdout, stderr = self._run_cli(
            "--action",
            "run_loop_once",
            "--iteration",
            "0",
            "--next-iteration",
            "1",
            "--write-state",
        )
        self.assertEqual(rc, 2)
        self.assertEqual(stdout, "")
        self.assertIn("supports only --dry-run or --execute", stderr)

    def _run_cli(self, *args: str) -> tuple[int, str, str]:
        argv = ["--optimization-root", str(self.root), *args]
        stdout = io.StringIO()
        stderr = io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            rc = optimizer_tick_main(argv)
        return rc, stdout.getvalue(), stderr.getvalue()

    def _write_completed_iteration_000(self, *, n_cases: int) -> None:
        campaign = {
            "schema_version": 1,
            "campaign_name": "clpu_capillary_guiding_bo_002_iter_000",
            "case_manifest": "cases.tsv",
            "case_manifest_format": "tsv",
            "case_id_column": "CASE_ID",
            "case_name_column": "CASE_NAME",
            "case_materialization": {
                "input_template": "input_template.py",
                "input_name": "input.py",
                "env_name": "case.env",
                "env_columns": [
                    {"column": "CASE_ID", "env": "CAP_CASE_ID", "required": True},
                    {"column": "CASE_NAME", "env": "CAP_CASE_NAME", "required": True},
                    {"column": "LASER_CASE", "env": "CAP_LASER_CASE", "required": True},
                    {
                        "column": "PLASMA_KIND",
                        "env": "CAP_PLASMA_KIND",
                        "required": True,
                    },
                    {"column": "N0_CM3", "env": "CAP_N0_CM3", "required": True},
                    {
                        "column": "PLATEAU_LENGTH_MM",
                        "env": "CAP_PLATEAU_LENGTH_MM",
                        "required": True,
                    },
                    {
                        "column": "DIAMETER_UM",
                        "env": "CAP_DIAMETER_UM",
                        "required": True,
                    },
                    {"column": "RADIUS_UM", "env": "CAP_RADIUS_UM", "required": True},
                    {
                        "column": "FOCUS_OFFSET_FROM_PLATEAU_START_MM",
                        "env": "CAP_FOCUS_OFFSET_FROM_PLATEAU_START_MM",
                        "required": True,
                    },
                    {"column": "CAP_RMAX_UM", "env": "CAP_RMAX_UM", "required": True},
                    {"column": "CAP_NR", "env": "CAP_NR", "required": True},
                ],
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
            json.dumps(campaign, indent=2) + "\n", encoding="utf-8"
        )
        header = [
            "CASE_ID",
            "CASE_NAME",
            "LASER_CASE",
            "PLASMA_KIND",
            "N0_CM3",
            "PLATEAU_LENGTH_MM",
            "DIAMETER_UM",
            "RADIUS_UM",
            "FOCUS_OFFSET_FROM_PLATEAU_START_MM",
            "CAP_RMAX_UM",
            "CAP_NR",
        ]
        rows = []
        for i in range(n_cases):
            rows.append(
                [
                    str(i),
                    f"{i:03d}_case",
                    "f20",
                    "chan",
                    "4e18",
                    "25",
                    "500",
                    "250",
                    "5",
                    "300",
                    "192",
                ]
            )
        (self.iter_root / "cases.tsv").write_text(
            "\t".join(header) + "\n" + "\n".join("\t".join(row) for row in rows) + "\n",
            encoding="utf-8",
        )
        (self.iter_root / "input_template.py").write_text(
            "# fake template\n", encoding="utf-8"
        )
        (self.iter_root / "array_logs").mkdir()
        (self.root / "optimizer_runs" / "iter_000").mkdir(parents=True)
        (self.root / "workflow-env.sh").write_text("# fake env\n", encoding="utf-8")

        for i in range(n_cases):
            case_dir = self.iter_root / f"{i:03d}_case"
            (case_dir / "post").mkdir(parents=True)
            (case_dir / "post" / "sim_done.json").write_text(
                '{"ok": true}\n', encoding="utf-8"
            )
            (case_dir / "state.json").write_text(
                '{"schema_version": 1, "state": "Raw_deleted"}\n', encoding="utf-8"
            )
            (case_dir / "validation.json").write_text(
                json.dumps(
                    {"schema_version": 1, "reduced": {"guiding_metrics": {"ok": True}}}
                )
                + "\n",
                encoding="utf-8",
            )

    def _write_optimization_state(self, *, max_iterations: int) -> None:
        state = {
            "schema_version": 1,
            "optimization_name": "clpu_capillary_guiding_bo_002",
            "status": "running",
            "latest_iteration": 0,
            "iterations": [
                {
                    "iteration": 0,
                    "optimizer_run_dir": "optimizer_runs/iter_000",
                    "campaign_root": "iterations/iter_000",
                    "status": "submitted",
                    "submitted": True,
                    "slurm_job_ids": ["611202"],
                    "array_spec": "0-1",
                    "submitted_case_ids": [0, 1],
                    "submitted_case_count": 2,
                    "n_cases": 2,
                    "recommended_action": "wait_for_jobs",
                }
            ],
        }
        self.root.mkdir(parents=True, exist_ok=True)
        (self.root / "optimization_state.json").write_text(
            json.dumps(state, indent=2) + "\n", encoding="utf-8"
        )

    def _write_fake_optimizer_success(self) -> None:
        self.fake_optimizer.write_text(
            "import json\n"
            "import sys\n"
            "from pathlib import Path\n"
            "run_dir = Path(sys.argv[1])\n"
            "iteration = int(sys.argv[2])\n"
            "outputs = run_dir / 'outputs'\n"
            "outputs.mkdir(parents=True, exist_ok=True)\n"
            "header = ['CASE_ID', 'CASE_NAME', 'LASER_CASE', 'PLASMA_KIND', 'N0_CM3', 'PLATEAU_LENGTH_MM', 'DIAMETER_UM', 'RADIUS_UM', 'FOCUS_OFFSET_FROM_PLATEAU_START_MM', 'CAP_RMAX_UM', 'CAP_NR', 'OPT_ITERATION']\n"
            "rows = [\n"
            "    ['0', '000_case', 'f20', 'chan', '4e18', '25', '500', '250', '5', '300', '192', str(iteration)],\n"
            "    ['1', '001_case', 'f32', 'chan', '3.5e18', '20', '400', '200', '0', '240', '192', str(iteration)],\n"
            "]\n"
            "(outputs / 'candidate_batch.tsv').write_text('\\t'.join(header) + '\\n' + '\\n'.join('\\t'.join(row) for row in rows) + '\\n', encoding='utf-8')\n"
            "(outputs / 'batch_campaign_plan.json').write_text(json.dumps({\n"
            "    'schema_version': 1,\n"
            "    'plan_type': 'optimizer_candidate_batch',\n"
            "    'optimizer_iteration': iteration,\n"
            "    'campaign_template': {'campaign_json': 'campaign.json', 'input_template': 'input_template.py'},\n"
            "    'source_campaigns': [],\n"
            "}, indent=2) + '\\n', encoding='utf-8')\n"
            "print('fake optimizer done')\n",
            encoding="utf-8",
        )

    def _write_optimization_config(
        self,
        *,
        command: list[str],
        max_iterations: int,
        stopping_enabled: bool = False,
    ) -> None:
        config = {
            "schema_version": 1,
            "optimization_name": "clpu_capillary_guiding_bo_002",
            "optimizer": {
                "working_directory": ".",
                "command": command,
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
                "max_iterations": max_iterations,
                "max_total_submitted_cases": 100,
                "min_reduced_valid_to_continue": 1,
                "min_valid_fraction_to_continue": 0.5,
                "max_failed_fraction_to_continue": 0.5,
            },
        }
        if stopping_enabled:
            config["stopping"] = {
                "enabled": True,
                "mode": "hard",
                "max_iterations": max_iterations,
            }
        (self.root / "optimization.json").write_text(
            json.dumps(config, indent=2) + "\n", encoding="utf-8"
        )


if __name__ == "__main__":
    unittest.main()
