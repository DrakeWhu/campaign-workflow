from __future__ import annotations

import contextlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path

from campaign_workflow.cli.optimizer_tick import main as optimizer_tick_main
from campaign_workflow.propose_next_iteration import (
    evaluate_from_iteration_readiness,
)


class OptimizerTickPhase4DProposeExecuteTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmpdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tmpdir.name) / "clpu_capillary_guiding_bo_001"
        self.iter_root = self.root / "iterations" / "iter_000"
        self.iter_root.mkdir(parents=True)
        self.fake_optimizer = self.root / "fake_optimizer.py"

        self._write_template_iteration()
        self._write_optimization_state()
        self._write_fake_optimizer_success()
        self._write_optimization_config(
            [
                sys.executable,
                str(self.fake_optimizer),
                "{optimizer_run_dir}",
                "{next_iteration}",
            ]
        )

    def tearDown(self) -> None:
        self.tmpdir.cleanup()

    def test_readiness_can_require_every_materialized_case_to_be_submitted(
        self,
    ) -> None:
        readiness = evaluate_from_iteration_readiness(
            from_state={
                "status": "reduced_ready",
                "recommended_action": "close_iteration",
                "n_cases": 9,
                "submitted_case_count": 1,
                "n_submitted_reduced_valid": 1,
                "n_submitted_sim_failed": 0,
            },
            from_summary={"n_cases": 9},
            policy={
                "require_all_materialized_cases_submitted": True,
                "min_reduced_valid_to_continue": 1,
            },
        )

        self.assertFalse(readiness["ok"])
        self.assertEqual(readiness["recommended_action"], "submit_iteration")
        self.assertIn("submitted=1, materialized=9", readiness["reason"])

    def test_execute_propose_next_iteration_runs_optimizer_prepares_materializes_and_updates_state(
        self,
    ) -> None:
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
        data = json.loads(stdout)

        self.assertEqual(data["action"], "propose_next_iteration")
        self.assertEqual(data["mode"], "execute")
        self.assertTrue(data["state_written"])
        self.assertTrue(data["stopped_before_submit"])
        self.assertEqual(data["external_optimizer_result"]["return_code"], 0)

        self.assertTrue(
            (
                self.root
                / "optimizer_runs"
                / "iter_001"
                / "outputs"
                / "candidate_batch.tsv"
            ).is_file()
        )
        self.assertTrue(
            (
                self.root
                / "optimizer_runs"
                / "iter_001"
                / "outputs"
                / "batch_campaign_plan.json"
            ).is_file()
        )

        iter_001 = self.root / "iterations" / "iter_001"
        self.assertTrue((iter_001 / "campaign.json").is_file())
        self.assertTrue((iter_001 / "cases.tsv").is_file())
        self.assertTrue((iter_001 / "input_template.py").is_file())
        self.assertTrue((iter_001 / "array_logs").is_dir())

        for case_name in ("000_case", "001_case"):
            self.assertTrue((iter_001 / case_name / "input.py").is_file())
            self.assertTrue((iter_001 / case_name / "case.env").is_file())
            self.assertTrue((iter_001 / case_name / "state.json").is_file())
            self.assertTrue((iter_001 / case_name / "validation.json").is_file())

        state = json.loads(
            (self.root / "optimization_state.json").read_text(encoding="utf-8")
        )
        self.assertEqual(state["latest_iteration"], 1)
        self.assertEqual(state["status"], "campaign_materialized")
        self.assertEqual(state["recommended_action"], "submit_iteration")

        iterations = {int(item["iteration"]): item for item in state["iterations"]}
        self.assertEqual(iterations[0]["status"], "closed")
        self.assertEqual(iterations[0]["recommended_action"], "no_action")

        self.assertEqual(iterations[1]["status"], "campaign_materialized")
        self.assertEqual(iterations[1]["recommended_action"], "submit_iteration")
        self.assertFalse(iterations[1]["submitted"])
        self.assertEqual(iterations[1]["slurm_job_ids"], [])
        self.assertEqual(iterations[1]["created_by_action"], "propose_next_iteration")
        self.assertEqual(iterations[1]["created_from_iteration"], 0)

        command_text = " ".join(iterations[1]["external_optimizer_result"]["command"])
        self.assertNotIn("sbatch", command_text)
        self.assertNotIn("srun", command_text)
        self.assertNotIn("mpiexec", command_text)
        self.assertNotIn("mpirun", command_text)

    def test_execute_optimizer_failure_does_not_update_state_or_prepare_iter001(
        self,
    ) -> None:
        self.fake_optimizer.write_text(
            "import sys\n"
            "print('fake optimizer failed', file=sys.stderr)\n"
            "raise SystemExit(13)\n",
            encoding="utf-8",
        )
        before = (self.root / "optimization_state.json").read_text(encoding="utf-8")

        rc, stdout, stderr = self._run_cli(
            "--action",
            "propose_next_iteration",
            "--from-iteration",
            "0",
            "--next-iteration",
            "1",
            "--execute",
        )

        self.assertEqual(rc, 1)
        self.assertEqual(stdout, "")
        self.assertIn("external optimizer command failed", stderr)
        self.assertEqual(
            (self.root / "optimization_state.json").read_text(encoding="utf-8"),
            before,
        )
        self.assertFalse((self.root / "iterations" / "iter_001").exists())

    def test_execute_missing_optimizer_outputs_does_not_update_state(self) -> None:
        self.fake_optimizer.write_text(
            "print('success but no files')\n",
            encoding="utf-8",
        )
        before = (self.root / "optimization_state.json").read_text(encoding="utf-8")

        rc, stdout, stderr = self._run_cli(
            "--action",
            "propose_next_iteration",
            "--from-iteration",
            "0",
            "--next-iteration",
            "1",
            "--execute",
        )

        self.assertEqual(rc, 1)
        self.assertEqual(stdout, "")
        self.assertIn("missing optimizer output candidate_batch.tsv", stderr)
        self.assertEqual(
            (self.root / "optimization_state.json").read_text(encoding="utf-8"),
            before,
        )
        self.assertFalse((self.root / "iterations" / "iter_001").exists())

    def _run_cli(self, *args: str) -> tuple[int, str, str]:
        argv = ["--optimization-root", str(self.root), *args]
        stdout = io.StringIO()
        stderr = io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            rc = optimizer_tick_main(argv)
        return rc, stdout.getvalue(), stderr.getvalue()

    def _write_template_iteration(self) -> None:
        campaign = {
            "schema_version": 1,
            "campaign_name": "clpu_capillary_guiding_bo_001_iter_000",
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
                        "env": "CAP_PLATEAU_LENGTH_M",
                        "required": True,
                        "scale": "1e-3",
                    },
                    {
                        "column": "RADIUS_UM",
                        "env": "CAP_RADIUS_M",
                        "required": False,
                        "scale": "1e-6",
                    },
                    {
                        "column": "FOCUS_OFFSET_FROM_PLATEAU_START_MM",
                        "env": "CAP_FOCUS_OFFSET_FROM_PLATEAU_START_MM",
                        "required": True,
                    },
                    {
                        "column": "CAP_RMAX_UM",
                        "env": "CAP_RMAX_M",
                        "required": True,
                        "scale": "1e-6",
                    },
                    {"column": "CAP_NR", "env": "CAP_NR", "required": True},
                ],
                "env_constants": {
                    "CAP_DIAG_PRESET": "guiding_rhoe",
                    "CAP_LONG_PROFILE": "both",
                },
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
            json.dumps(campaign, indent=2) + "\n",
            encoding="utf-8",
        )
        (self.iter_root / "cases.tsv").write_text(
            "CASE_ID\tCASE_NAME\n0\told_case\n",
            encoding="utf-8",
        )
        (self.iter_root / "input_template.py").write_text(
            "# template copied without physics edits\n",
            encoding="utf-8",
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

    def _write_fake_optimizer_success(self) -> None:
        self.fake_optimizer.write_text(
            "import json\n"
            "import sys\n"
            "from pathlib import Path\n"
            "run_dir = Path(sys.argv[1])\n"
            "iteration = int(sys.argv[2])\n"
            "outputs = run_dir / 'outputs'\n"
            "outputs.mkdir(parents=True, exist_ok=True)\n"
            "header = [\n"
            "    'CASE_ID', 'CASE_NAME', 'LASER_CASE', 'PLASMA_KIND', 'N0_CM3',\n"
            "    'PLATEAU_LENGTH_MM', 'DIAMETER_UM', 'RADIUS_UM',\n"
            "    'FOCUS_OFFSET_FROM_PLATEAU_START_MM', 'CAP_RMAX_UM', 'CAP_NR',\n"
            "    'OPT_ITERATION', 'OPT_CANDIDATE_ID'\n"
            "]\n"
            "rows = [\n"
            "    ['0', '000_case', 'f20', 'chan', '4e18', '25', '500', '250', '5', '300', '192', str(iteration), 'opt_001_000'],\n"
            "    ['1', '001_case', 'f32', 'chan', '3.5e18', '20', '400', '200', '0', '240', '192', str(iteration), 'opt_001_001'],\n"
            "]\n"
            "(outputs / 'candidate_batch.tsv').write_text(\n"
            "    '\\t'.join(header) + '\\n' + '\\n'.join('\\t'.join(row) for row in rows) + '\\n',\n"
            "    encoding='utf-8',\n"
            ")\n"
            "(outputs / 'batch_campaign_plan.json').write_text(\n"
            "    json.dumps({\n"
            "        'schema_version': 1,\n"
            "        'plan_type': 'optimizer_candidate_batch',\n"
            "        'optimizer_iteration': iteration,\n"
            "        'objective_config_id': 'capillary_objectives_v1',\n"
            "        'campaign_template': {\n"
            "            'campaign_json': 'campaign.json',\n"
            "            'input_template': 'input_template.py',\n"
            "        },\n"
            "        'source_campaigns': [],\n"
            "    }, indent=2) + '\\n',\n"
            "    encoding='utf-8',\n"
            ")\n"
            "print('fake optimizer done')\n",
            encoding="utf-8",
        )

    def _write_optimization_config(self, command: list[str]) -> None:
        config = {
            "schema_version": 1,
            "optimization_name": "clpu_capillary_guiding_bo_001",
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
