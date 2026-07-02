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
            "--mem",
            "64G",
        ]

    def test_static_sbatch_scripts_exist_and_do_not_call_sbatch(self) -> None:
        array_script = Path("examples/sunrise/run_iteration_array.sh")
        tick_script = Path("examples/sunrise/run_optimizer_tick_materialize_only.sh")
        submit_script = Path("examples/sunrise/submit_morbo_chain.py")

        for path in (array_script, tick_script, submit_script):
            with self.subTest(path=str(path)):
                self.assertTrue(path.is_file())
                text = path.read_text(encoding="utf-8")
                self.assertNotIn("\r\n", text)

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

        self.assertTrue(commands[0][-1].endswith("run_iteration_array.sh"))
        self.assertTrue(
            commands[1][-1].endswith("run_optimizer_tick_materialize_only.sh")
        )
        self.assertIn("--array=0-29", commands[0])
        self.assertIn("--array=0-29", commands[2])
        self.assertIn("CW_ITERATION=2", " ".join(commands[0]))
        self.assertIn("CW_NEXT_ITERATION=3", " ".join(commands[1]))

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


if __name__ == "__main__":
    unittest.main()
