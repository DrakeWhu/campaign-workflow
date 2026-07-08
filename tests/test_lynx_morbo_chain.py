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


def load_submit_morbo_chain_lynx_module():
    script_path = Path("examples/lynx/submit_morbo_chain_lynx.py").resolve()
    spec = importlib.util.spec_from_file_location(
        "submit_morbo_chain_lynx", script_path
    )
    if spec is None or spec.loader is None:
        raise RuntimeError(f"could not import {script_path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class LynxMorboChainTests(unittest.TestCase):
    def setUp(self) -> None:
        self.workflow_root = Path.cwd().resolve()
        self.module = load_submit_morbo_chain_lynx_module()

    def _make_root(self) -> tuple[tempfile.TemporaryDirectory[str], Path, Path]:
        tmp = tempfile.TemporaryDirectory()
        root = Path(tmp.name) / "bo_root"
        root.mkdir()
        (root / "optimization.json").write_text("{}\n", encoding="utf-8")
        workflow_env = Path(tmp.name) / "campaign_workflow_lynx.sh"
        workflow_env.write_text(
            "# fake Lynx workflow env for tests\n", encoding="utf-8"
        )
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
            "cw_lynx_bo002",
            "--partition",
            "novas",
            "--expected-slurm-partition",
            "novas",
            "--time",
            "06:00:00",
            "--nodes",
            "1",
            "--ntasks",
            "24",
            "--mem",
            "64G",
            "--warpx-lynx-module",
            "26.03_lynx_cpu_rz_yee_openpmd_py311",
        ]

    def test_static_lynx_sbatch_scripts_exist_and_do_not_call_sbatch(self) -> None:
        array_script = Path("examples/lynx/run_iteration_array_lynx.sh")
        tick_script = Path("examples/lynx/run_optimizer_tick_materialize_only_lynx.sh")
        submit_script = Path("examples/lynx/submit_morbo_chain_lynx.py")

        for path in (array_script, tick_script, submit_script):
            with self.subTest(path=str(path)):
                self.assertTrue(path.is_file())
                text = path.read_text(encoding="utf-8")
                self.assertNotIn("\r\n", text)

        array_text = array_script.read_text(encoding="utf-8")
        tick_text = tick_script.read_text(encoding="utf-8")

        self.assertIn("#SBATCH --partition=novas", array_text)
        self.assertIn("#SBATCH --partition=novas", tick_text)
        self.assertIn("STOP_OPTIMIZATION", array_text)
        self.assertIn("status=stopped", array_text)
        self.assertIn("skipping as no-op", array_text)
        self.assertIn("submit_case_cycle_array_lynx.sh", array_text)
        self.assertNotIn("sbatch ", array_text)
        self.assertNotIn("subprocess.run", array_text)

        self.assertIn("--stop-after-materialization", tick_text)
        self.assertIn("campaign_workflow.cli.optimizer_tick", tick_text)
        self.assertNotIn("sbatch ", tick_text)
        self.assertNotIn("subprocess.run", tick_text)

    def test_dry_run_prints_lynx_finite_chain_without_sbatch(self) -> None:
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
        self.assertEqual(data["platform"], "lynx")
        self.assertEqual(data["partition"], "novas")
        self.assertEqual(data["expected_slurm_partition"], "novas")
        self.assertEqual(
            data["warpx_lynx_module"],
            "26.03_lynx_cpu_rz_yee_openpmd_py311",
        )
        self.assertEqual(data["start_iteration"], 2)
        self.assertEqual(data["final_iteration"], 4)
        self.assertEqual(data["chain_text"], "A_2 -> T_2 -> A_3 -> T_3 -> A_4 -> T_4")
        self.assertEqual(len(data["jobs"]), 6)

        first_command = " ".join(data["jobs"][0]["submit_command"])
        self.assertIn("--partition=novas", first_command)
        self.assertIn("EXPECTED_SLURM_PARTITION=novas", first_command)
        self.assertIn(
            "WARPX_LYNX_MODULE=26.03_lynx_cpu_rz_yee_openpmd_py311",
            first_command,
        )
        self.assertIn("CONFIRM_CLEANUP_EXECUTE=1", first_command)
        self.assertIn("run_iteration_array_lynx.sh", first_command)

        tick_command = " ".join(data["jobs"][1]["submit_command"])
        self.assertIn("run_optimizer_tick_materialize_only_lynx.sh", tick_command)
        self.assertIn("EXPECTED_SLURM_PARTITION=novas", tick_command)
        self.assertIn(
            "WARPX_LYNX_MODULE=26.03_lynx_cpu_rz_yee_openpmd_py311",
            tick_command,
        )
        self.assertNotIn("CONFIRM_CLEANUP_EXECUTE=1", tick_command)

    def test_execute_submits_lynx_chain_with_afterok_dependencies_and_manifest(
        self,
    ) -> None:
        tmp, root, workflow_env = self._make_root()
        self.addCleanup(tmp.cleanup)
        stdout = io.StringIO()
        submitted_ids = iter(["100", "101", "102", "103", "104", "105"])

        def fake_run(command, **kwargs):
            job_id = next(submitted_ids)
            return SimpleNamespace(returncode=0, stdout=f"{job_id}\n", stderr="")

        with patch.dict(os.environ, {}, clear=True):
            with patch.object(
                self.module.subprocess,
                "run",
                side_effect=fake_run,
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

        self.assertTrue(commands[0][-1].endswith("run_iteration_array_lynx.sh"))
        self.assertTrue(
            commands[1][-1].endswith("run_optimizer_tick_materialize_only_lynx.sh")
        )
        self.assertIn("--array=0-29", commands[0])
        self.assertIn("--array=0-29", commands[2])
        self.assertIn("--partition=novas", commands[0])
        self.assertIn("--partition=novas", commands[1])
        self.assertIn("CW_ITERATION=2", " ".join(commands[0]))
        self.assertIn("CW_NEXT_ITERATION=3", " ".join(commands[1]))
        self.assertIn(
            "WARPX_LYNX_MODULE=26.03_lynx_cpu_rz_yee_openpmd_py311",
            " ".join(commands[0]),
        )
        self.assertIn("EXPECTED_SLURM_PARTITION=novas", " ".join(commands[0]))

        data = json.loads(stdout.getvalue())
        self.assertFalse(data["dry_run"])
        self.assertEqual(
            [job["job_id"] for job in data["jobs"]],
            ["100", "101", "102", "103", "104", "105"],
        )

        manifest_path = Path(data["manifest_path"])
        self.assertTrue(manifest_path.is_file())
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        self.assertEqual(manifest["platform"], "lynx")
        self.assertEqual(manifest["jobs"][2]["dependency"], "afterok:101")

    def test_refuses_to_run_from_inside_slurm_job(self) -> None:
        tmp, root, workflow_env = self._make_root()
        self.addCleanup(tmp.cleanup)
        stderr = io.StringIO()

        with patch.dict(os.environ, {"SLURM_JOB_ID": "999"}, clear=True):
            with patch.object(self.module.subprocess, "run") as run_mock:
                with contextlib.redirect_stderr(stderr):
                    rc = self.module.main(self._base_argv(root, workflow_env))

        self.assertEqual(rc, 2)
        run_mock.assert_not_called()
        self.assertIn("Refusing to call sbatch", stderr.getvalue())
        self.assertIn("SLURM_JOB_ID=999", stderr.getvalue())

    def test_missing_optimization_json_blocks_chain(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)

        root = Path(tmp.name) / "bo_root"
        root.mkdir()
        workflow_env = Path(tmp.name) / "campaign_workflow_lynx.sh"
        workflow_env.write_text("# fake env\n", encoding="utf-8")
        stderr = io.StringIO()

        with patch.dict(os.environ, {}, clear=True):
            with patch.object(self.module.subprocess, "run") as run_mock:
                with contextlib.redirect_stderr(stderr):
                    rc = self.module.main(self._base_argv(root, workflow_env))

        self.assertEqual(rc, 2)
        run_mock.assert_not_called()
        self.assertIn("missing optimization.json", stderr.getvalue())

    def test_missing_warpx_lynx_module_blocks_chain(self) -> None:
        tmp, root, workflow_env = self._make_root()
        self.addCleanup(tmp.cleanup)
        stderr = io.StringIO()
        argv = self._base_argv(root, workflow_env)

        module_flag_index = argv.index("--warpx-lynx-module")
        del argv[module_flag_index : module_flag_index + 2]

        with patch.dict(os.environ, {}, clear=True):
            with patch.object(self.module.subprocess, "run") as run_mock:
                with contextlib.redirect_stderr(stderr):
                    rc = self.module.main(argv)

        self.assertEqual(rc, 2)
        run_mock.assert_not_called()

    def test_non_novas_partition_blocks_chain(self) -> None:
        tmp, root, workflow_env = self._make_root()
        self.addCleanup(tmp.cleanup)
        stderr = io.StringIO()
        argv = self._base_argv(root, workflow_env)
        argv[argv.index("--partition") + 1] = "debug"

        with patch.dict(os.environ, {}, clear=True):
            with patch.object(self.module.subprocess, "run") as run_mock:
                with contextlib.redirect_stderr(stderr):
                    rc = self.module.main(argv)

        self.assertEqual(rc, 2)
        run_mock.assert_not_called()
        self.assertIn("partition mismatch", stderr.getvalue())

    def test_execute_records_start_iteration_submit_metadata(self) -> None:
        tmp, root, workflow_env = self._make_root()
        self.addCleanup(tmp.cleanup)

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

        self.assertEqual(state["status"], "running")
        self.assertEqual(iteration["iteration"], 2)
        self.assertEqual(iteration["status"], "submitted")
        self.assertEqual(iteration["recommended_action"], "wait_for_jobs")
        self.assertTrue(iteration["submitted"])
        self.assertEqual(iteration["slurm_job_ids"], ["100"])
        self.assertEqual(iteration["array_spec"], "0-29")
        self.assertEqual(iteration["submitted_case_count"], 30)
        self.assertEqual(iteration["submitted_case_ids"][0], 0)
        self.assertEqual(iteration["submitted_case_ids"][-1], 29)
        self.assertTrue(
            iteration["submit_script"].endswith("run_iteration_array_lynx.sh")
        )


if __name__ == "__main__":
    unittest.main()
