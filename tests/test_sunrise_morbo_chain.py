from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


def load_submit_morbo_chain_module():
    script_path = Path("examples/sunrise/submit_morbo_chain.py").resolve()
    spec = importlib.util.spec_from_file_location("submit_morbo_chain", script_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"could not import {script_path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class SunriseMorboChainTests(unittest.TestCase):
    def setUp(self) -> None:
        self.workflow_root = Path.cwd().resolve()
        self.module = load_submit_morbo_chain_module()

    def _make_root(self) -> tuple[tempfile.TemporaryDirectory[str], Path, Path]:
        tmp = tempfile.TemporaryDirectory()
        root = Path(tmp.name) / "bo_root"
        root.mkdir()
        (root / "optimization.json").write_text("{}\n", encoding="utf-8")
        workflow_env = Path(tmp.name) / "campaign-workflow.sh"
        workflow_env.write_text("# fake env for tests\n", encoding="utf-8")
        return tmp, root, workflow_env

    def _base_argv(self, root: Path, workflow_env: Path) -> list[str]:
        return [
            "--optimization-root",
            str(root),
            "--start-iteration",
            "2",
            "--num-additional-iterations",
            "3",
            "--array-spec",
            "0-29",
            "--workflow-root",
            str(self.workflow_root),
            "--workflow-env",
            str(workflow_env),
            "--job-name-prefix",
            "cw_bo002",
            "--partition",
            "T6H",
            "--time",
            "06:00:00",
            "--nodes",
            "1",
            "--ntasks",
            "24",
        ]

    def test_static_sbatch_scripts_exist_without_memory_limits_or_nested_sbatch(
        self,
    ) -> None:
        array_script = Path("examples/sunrise/run_iteration_array.sh")
        tick_script = Path("examples/sunrise/run_optimizer_tick_materialize_only.sh")
        case_cycle_script = Path("examples/sunrise/submit_case_cycle_array.sh")
        submit_script = Path("examples/sunrise/submit_morbo_chain.py")

        for path in (
            array_script,
            tick_script,
            case_cycle_script,
            submit_script,
        ):
            with self.subTest(path=str(path)):
                self.assertTrue(path.is_file())
                text = path.read_text(encoding="utf-8")
                self.assertNotIn("\r\n", text)
                self.assertNotIn("--mem", text)

        array_text = array_script.read_text(encoding="utf-8")
        tick_text = tick_script.read_text(encoding="utf-8")
        self.assertIn("STOP_OPTIMIZATION", array_text)
        self.assertIn("status=stopped", array_text)
        self.assertIn("skipping as no-op", array_text)
        self.assertIn("submit_case_cycle_array.sh", array_text)
        self.assertNotIn("sbatch ", array_text)
        self.assertNotIn("subprocess.run", array_text)

        self.assertIn("--stop-after-materialization", tick_text)
        self.assertIn("campaign_workflow.cli.optimizer_tick", tick_text)
        self.assertNotIn("sbatch ", tick_text)
        self.assertNotIn("subprocess.run", tick_text)

    def test_dry_run_prints_finite_chain_without_sbatch(self) -> None:
        tmp, root, workflow_env = self._make_root()
        self.addCleanup(tmp.cleanup)
        stdout = io.StringIO()

        with patch.dict(os.environ, {}, clear=True):
            with patch.object(self.module.subprocess, "run") as run_mock:
                with contextlib.redirect_stdout(stdout):
                    rc = self.module.main(self._base_argv(root, workflow_env))

        self.assertEqual(rc, 0)
        run_mock.assert_not_called()
        data = json.loads(stdout.getvalue())
        self.assertTrue(data["dry_run"])
        self.assertEqual(data["start_iteration"], 2)
        self.assertEqual(data["final_iteration"], 4)
        self.assertEqual(data["chain_text"], "A_2 -> T_2 -> A_3 -> T_3 -> A_4 -> T_4")
        self.assertEqual(len(data["jobs"]), 6)
        self.assertEqual(data["jobs"][0]["kind"], "array")
        self.assertIsNone(data["jobs"][0]["dependency"])
        self.assertEqual(data["jobs"][1]["dependency"], "afterok:A_002")
        self.assertEqual(data["jobs"][2]["dependency"], "afterok:T_002")
        for job in data["jobs"]:
            self.assertFalse(
                any(part.startswith("--mem") for part in job["submit_command"])
            )

    def test_custom_case_runner_and_lightweight_tick_resources_are_propagated(
        self,
    ) -> None:
        tmp, root, workflow_env = self._make_root()
        self.addCleanup(tmp.cleanup)
        case_runner = Path(tmp.name) / "run_multichannel.sh"
        case_runner.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
        case_runner.chmod(0o755)
        stdout = io.StringIO()

        argv = self._base_argv(root, workflow_env) + [
            "--case-runner",
            str(case_runner),
            "--tick-time",
            "00:30:00",
            "--tick-ntasks",
            "1",
        ]
        with patch.dict(os.environ, {}, clear=True):
            with contextlib.redirect_stdout(stdout):
                rc = self.module.main(argv)

        self.assertEqual(rc, 0)
        data = json.loads(stdout.getvalue())
        array_command = data["jobs"][0]["submit_command"]
        tick_command = data["jobs"][1]["submit_command"]
        self.assertIn(f"CW_CASE_RUNNER={case_runner}", " ".join(array_command))
        self.assertIn("--time=00:30:00", tick_command)
        self.assertIn("--ntasks=1", tick_command)
        self.assertIn("--ntasks=24", array_command)
        self.assertFalse(any(part.startswith("--mem") for part in array_command))
        self.assertFalse(any(part.startswith("--mem") for part in tick_command))

    def test_initial_dependency_gates_only_the_first_array(self) -> None:
        tmp, root, workflow_env = self._make_root()
        self.addCleanup(tmp.cleanup)
        stdout = io.StringIO()

        argv = self._base_argv(root, workflow_env) + [
            "--initial-dependency-job-id",
            "900001",
        ]
        with patch.dict(os.environ, {}, clear=True):
            with patch.object(self.module.subprocess, "run") as run_mock:
                with contextlib.redirect_stdout(stdout):
                    rc = self.module.main(argv)

        self.assertEqual(rc, 0)
        run_mock.assert_not_called()
        data = json.loads(stdout.getvalue())
        self.assertEqual(data["initial_dependency_job_id"], "900001")
        self.assertEqual(
            data["chain_text"],
            "J_900001 -> A_2 -> T_2 -> A_3 -> T_3 -> A_4 -> T_4",
        )
        self.assertEqual(data["jobs"][0]["dependency"], "afterok:900001")
        self.assertEqual(data["jobs"][1]["dependency"], "afterok:A_002")
        self.assertEqual(data["jobs"][2]["dependency"], "afterok:T_002")

    def test_invalid_initial_dependency_is_rejected_before_sbatch(self) -> None:
        tmp, root, workflow_env = self._make_root()
        self.addCleanup(tmp.cleanup)
        stderr = io.StringIO()

        argv = self._base_argv(root, workflow_env) + [
            "--initial-dependency-job-id",
            "afterok:900001",
        ]
        with patch.dict(os.environ, {}, clear=True):
            with patch.object(self.module.subprocess, "run") as run_mock:
                with contextlib.redirect_stderr(stderr):
                    rc = self.module.main(argv)

        self.assertEqual(rc, 2)
        run_mock.assert_not_called()
        self.assertIn("positive numeric SLURM job ID", stderr.getvalue())

    def test_execute_preserves_initial_dependency_then_chains_job_ids(self) -> None:
        tmp, root, workflow_env = self._make_root()
        self.addCleanup(tmp.cleanup)
        stdout = io.StringIO()
        submitted_ids = iter(["100", "101", "102", "103", "104", "105"])

        def fake_run(command, **kwargs):
            job_id = next(submitted_ids)
            return SimpleNamespace(returncode=0, stdout=f"{job_id}\n", stderr="")

        argv = self._base_argv(root, workflow_env) + [
            "--initial-dependency-job-id",
            "900001",
            "--execute",
        ]
        with patch.dict(os.environ, {}, clear=True):
            with patch.object(
                self.module.subprocess, "run", side_effect=fake_run
            ) as run_mock:
                with contextlib.redirect_stdout(stdout):
                    rc = self.module.main(argv)

        self.assertEqual(rc, 0)
        commands = [call.args[0] for call in run_mock.call_args_list]
        self.assertIn("--dependency=afterok:900001", commands[0])
        self.assertIn("--dependency=afterok:100", commands[1])
        self.assertIn("--dependency=afterok:101", commands[2])
        data = json.loads(stdout.getvalue())
        self.assertEqual(data["initial_dependency_job_id"], "900001")
        self.assertEqual(data["jobs"][0]["dependency"], "afterok:900001")

    def test_execute_submits_chain_with_afterok_dependencies_and_manifest(self) -> None:
        tmp, root, workflow_env = self._make_root()
        self.addCleanup(tmp.cleanup)
        stdout = io.StringIO()
        submitted_ids = iter(["100", "101", "102", "103", "104", "105"])

        def fake_run(command, **kwargs):
            job_id = next(submitted_ids)
            return SimpleNamespace(
                returncode=0,
                stdout=f"{job_id}\n",
                stderr="",
            )

        with patch.dict(os.environ, {}, clear=True):
            with patch.object(
                self.module.subprocess, "run", side_effect=fake_run
            ) as run_mock:
                with contextlib.redirect_stdout(stdout):
                    rc = self.module.main(
                        self._base_argv(root, workflow_env) + ["--execute"]
                    )

        self.assertEqual(rc, 0)
        self.assertEqual(run_mock.call_count, 6)
        commands = [call.args[0] for call in run_mock.call_args_list]

        self.assertNotIn("--dependency", " ".join(commands[0]))
        self.assertIn("--dependency=afterok:100", commands[1])
        self.assertIn("--dependency=afterok:101", commands[2])
        self.assertIn("--dependency=afterok:102", commands[3])
        self.assertIn("--dependency=afterok:103", commands[4])
        self.assertIn("--dependency=afterok:104", commands[5])

        for command in commands:
            self.assertFalse(any(part.startswith("--mem") for part in command))

        self.assertTrue(commands[0][-1].endswith("run_iteration_array.sh"))
        self.assertTrue(
            commands[1][-1].endswith("run_optimizer_tick_materialize_only.sh")
        )
        self.assertIn("--array=0-29", commands[0])
        self.assertIn("--array=0-29", commands[2])
        self.assertIn("CW_ITERATION=2", " ".join(commands[0]))
        self.assertIn("CW_NEXT_ITERATION=3", " ".join(commands[1]))
        self.assertIn("CONFIRM_CLEANUP_EXECUTE=1", " ".join(commands[0]))
        self.assertIn("CONFIRM_CLEANUP_EXECUTE=1", " ".join(commands[2]))
        self.assertIn("CONFIRM_CLEANUP_EXECUTE=1", " ".join(commands[4]))

        data = json.loads(stdout.getvalue())
        self.assertFalse(data["dry_run"])
        self.assertEqual(
            [job["job_id"] for job in data["jobs"]],
            ["100", "101", "102", "103", "104", "105"],
        )
        manifest_path = Path(data["manifest_path"])
        self.assertTrue(manifest_path.is_file())
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        self.assertEqual(manifest["jobs"][2]["dependency"], "afterok:101")
        self.assertNotIn("mem", manifest)
        self.assertNotIn("mem", manifest["tick_resources"])

    def test_refuses_to_run_from_inside_slurm_job(self) -> None:
        tmp, root, workflow_env = self._make_root()
        self.addCleanup(tmp.cleanup)
        stderr = io.StringIO()

        with patch.dict(os.environ, {"SLURM_JOB_ID": "611305"}, clear=True):
            with patch.object(self.module.subprocess, "run") as run_mock:
                with contextlib.redirect_stderr(stderr):
                    rc = self.module.main(self._base_argv(root, workflow_env))

        self.assertEqual(rc, 2)
        run_mock.assert_not_called()
        self.assertIn("Refusing to call sbatch", stderr.getvalue())
        self.assertIn("SLURM_JOB_ID=611305", stderr.getvalue())

    def test_missing_optimization_json_blocks_chain(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name) / "bo_root"
        root.mkdir()
        workflow_env = Path(tmp.name) / "campaign-workflow.sh"
        workflow_env.write_text("# fake env for tests\n", encoding="utf-8")
        stderr = io.StringIO()

        with patch.dict(os.environ, {}, clear=True):
            with patch.object(self.module.subprocess, "run") as run_mock:
                with contextlib.redirect_stderr(stderr):
                    rc = self.module.main(self._base_argv(root, workflow_env))

        self.assertEqual(rc, 2)
        run_mock.assert_not_called()
        self.assertIn("missing optimization.json", stderr.getvalue())

    def test_execute_records_start_iteration_submit_metadata(self) -> None:
        tmp, root, workflow_env = self._make_root()
        self.addCleanup(tmp.cleanup)

        campaign_root = root / "iterations" / "iter_002"
        campaign_root.mkdir(parents=True)
        (campaign_root / "campaign.json").write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "campaign_name": "bo_root_iter_002",
                    "case_manifest": "cases.tsv",
                    "case_manifest_format": "tsv",
                    "case_id_column": "CASE_ID",
                    "case_name_column": "CASE_NAME",
                }
            ),
            encoding="utf-8",
        )
        (campaign_root / "cases.tsv").write_text(
            "CASE_ID\tCASE_NAME\n"
            + "".join(
                f"{case_id}\tcase_{case_id:03d}\n" for case_id in range(30)
            ),
            encoding="utf-8",
        )

        state_path = root / "optimization_state.json"
        state_path.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "optimization_name": "bo_root",
                    "status": "campaign_materialized",
                    "updated_at": "2026-07-07T00:00:00Z",
                    "latest_iteration": 2,
                    "iterations": [
                        {
                            "iteration": 2,
                            "status": "campaign_materialized",
                            "recommended_action": "submit_iteration",
                            "submitted": False,
                            "slurm_job_ids": [],
                            "campaign_root": "iterations/iter_002",
                        }
                    ],
                }
            )
            + "\n",
            encoding="utf-8",
        )

        stdout = io.StringIO()
        submitted_ids = iter(["100", "101", "102", "103", "104", "105"])

        def fake_run(command, **kwargs):
            job_id = next(submitted_ids)
            return SimpleNamespace(returncode=0, stdout=f"{job_id}\n", stderr="")

        with patch.dict(os.environ, {}, clear=True):
            with patch.object(self.module.subprocess, "run", side_effect=fake_run):
                with contextlib.redirect_stdout(stdout):
                    rc = self.module.main(
                        self._base_argv(root, workflow_env) + ["--execute"]
                    )

        self.assertEqual(rc, 0)

        state = json.loads(state_path.read_text(encoding="utf-8"))
        iteration = state["iterations"][0]

        self.assertEqual(iteration["iteration"], 2)
        self.assertEqual(iteration["status"], "submitted")
        self.assertEqual(iteration["recommended_action"], "wait_for_jobs")
        self.assertTrue(iteration["submitted"])
        self.assertEqual(iteration["slurm_job_ids"], ["100"])
        self.assertEqual(iteration["array_spec"], "0-29")
        self.assertEqual(iteration["submitted_case_ids"], list(range(30)))
        self.assertEqual(iteration["submitted_case_count"], 30)
        self.assertIn("run_iteration_array.sh", iteration["submit_script"])


if __name__ == "__main__":
    unittest.main()
