from __future__ import annotations

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from campaign_workflow.cli.optimizer_tick import main as optimizer_tick_main
from campaign_workflow.core.state import (
    initial_state_document,
    initial_validation_document,
)
from campaign_workflow.core.tsv_cases import CaseRecord


class CompletedProcessStub:
    def __init__(self, returncode: int, stdout: str = "", stderr: str = "") -> None:
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


class OptimizerTickPhase5BGuardsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmpdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tmpdir.name) / "clpu_capillary_guiding_bo_001"
        self.iter_root = self.root / "iterations" / "iter_000"
        self._write_iteration_fixture(n_cases=12)
        self._write_optimization_config()

    def tearDown(self) -> None:
        self.tmpdir.cleanup()

    def test_check_guards_dry_run_does_not_modify_state(self) -> None:
        state_path = self.root / "optimization_state.json"
        state_path.write_text(
            '{"schema_version": 1, "sentinel": "keep"}\n',
            encoding="utf-8",
        )
        before = state_path.read_text(encoding="utf-8")

        rc, stdout, stderr = self._run_cli(
            "--action",
            "check_guards",
            "--iteration",
            "0",
            "--array-spec",
            "0-9",
            "--dry-run",
        )

        self.assertEqual(rc, 0, stderr)
        self.assertEqual(state_path.read_text(encoding="utf-8"), before)
        data = json.loads(stdout)
        self.assertEqual(data["action"], "check_guards")
        self.assertEqual(data["guard_report"]["overall_status"], "pass")

    def test_check_guards_write_state_writes_guard_report_only(self) -> None:
        rc, stdout, stderr = self._run_cli(
            "--action",
            "check_guards",
            "--iteration",
            "0",
            "--array-spec",
            "0-9",
            "--write-state",
        )

        self.assertEqual(rc, 0, stderr)
        data = json.loads(stdout)
        report_path = Path(data["guard_report_written"])
        self.assertTrue(report_path.is_file())
        self.assertFalse((self.root / "optimization_state.json").exists())

    def test_pause_guard_blocks(self) -> None:
        (self.root / "PAUSE_OPTIMIZATION").write_text("pause\n", encoding="utf-8")

        rc, stdout, stderr = self._run_cli(
            "--action",
            "check_guards",
            "--iteration",
            "0",
            "--array-spec",
            "0-9",
            "--dry-run",
        )

        self.assertEqual(rc, 0, stderr)
        data = json.loads(stdout)
        self.assertEqual(data["guard_report"]["overall_status"], "blocked")
        self.assertEqual(data["guard_report"]["recommended_action"], "paused")

    def test_campaign_size_blocks_too_many_cases_without_array_spec(self) -> None:
        self._update_guards(
            {"campaign_size": {"enabled": True, "max_cases_per_submit": 10}}
        )

        rc, stdout, stderr = self._run_cli(
            "--action",
            "check_guards",
            "--iteration",
            "0",
            "--dry-run",
        )

        self.assertEqual(rc, 0, stderr)
        data = json.loads(stdout)
        self.assertEqual(data["guard_report"]["overall_status"], "blocked")
        self.assertIn("reduce_array", data["guard_report"]["recommended_action"])

    def test_campaign_size_allows_reduced_array_spec(self) -> None:
        self._update_guards(
            {"campaign_size": {"enabled": True, "max_cases_per_submit": 10}}
        )

        rc, stdout, stderr = self._run_cli(
            "--action",
            "check_guards",
            "--iteration",
            "0",
            "--array-spec",
            "0-9",
            "--dry-run",
        )

        self.assertEqual(rc, 0, stderr)
        data = json.loads(stdout)
        self.assertEqual(data["guard_report"]["overall_status"], "pass")

    def test_quota_guard_passes_with_space(self) -> None:
        self._update_guards(
            {"quota": {"enabled": True, "mode": "hard", "min_free_bytes": 100}}
        )
        usage = type("Usage", (), {"total": 1000, "used": 100, "free": 900})()

        with patch("campaign_workflow.guards.shutil.disk_usage", return_value=usage):
            rc, stdout, stderr = self._run_cli(
                "--action",
                "check_guards",
                "--iteration",
                "0",
                "--array-spec",
                "0-9",
                "--dry-run",
            )

        self.assertEqual(rc, 0, stderr)
        data = json.loads(stdout)
        quota = self._guard(data, "quota_guard")
        self.assertEqual(quota["status"], "pass")

    def test_quota_guard_blocks_in_hard_mode(self) -> None:
        self._update_guards(
            {"quota": {"enabled": True, "mode": "hard", "hard_used_fraction": 0.90}}
        )
        usage = type("Usage", (), {"total": 1000, "used": 950, "free": 50})()

        with patch("campaign_workflow.guards.shutil.disk_usage", return_value=usage):
            rc, stdout, stderr = self._run_cli(
                "--action",
                "check_guards",
                "--iteration",
                "0",
                "--array-spec",
                "0-9",
                "--dry-run",
            )

        self.assertEqual(rc, 0, stderr)
        data = json.loads(stdout)
        self.assertEqual(data["guard_report"]["overall_status"], "blocked")

    def test_quota_guard_unknown_in_warn_mode_if_probe_fails(self) -> None:
        self._update_guards({"quota": {"enabled": True, "mode": "warn"}})

        with patch(
            "campaign_workflow.guards.shutil.disk_usage",
            side_effect=OSError("df failed"),
        ):
            rc, stdout, stderr = self._run_cli(
                "--action",
                "check_guards",
                "--iteration",
                "0",
                "--array-spec",
                "0-9",
                "--dry-run",
            )

        self.assertEqual(rc, 0, stderr)
        data = json.loads(stdout)
        quota = self._guard(data, "quota_guard")
        self.assertEqual(quota["status"], "unknown")

    def test_walltime_guard_blocks_only_selected_case(self) -> None:
        self._update_guards(
            {
                "walltime": {
                    "enabled": True,
                    "mode": "hard",
                    "partition_time_limit_seconds": 90,
                    "max_runtime_fraction": 0.5,
                    "baseline_steps_per_5mm": 10,
                    "empirical_seconds_per_step": 1.0,
                    "front_ramp_mm": 0.0,
                    "back_ramp_mm": 0.0,
                    "safety_factor": 1.0,
                }
            }
        )

        rc, stdout, stderr = self._run_cli(
            "--action",
            "check_guards",
            "--iteration",
            "0",
            "--array-spec",
            "0-0",
            "--dry-run",
        )
        self.assertEqual(rc, 0, stderr)
        data = json.loads(stdout)
        walltime = self._guard(data, "walltime_guard")
        self.assertEqual(walltime["status"], "pass")

        rc, stdout, stderr = self._run_cli(
            "--action",
            "check_guards",
            "--iteration",
            "0",
            "--array-spec",
            "11-11",
            "--dry-run",
        )
        self.assertEqual(rc, 0, stderr)
        data = json.loads(stdout)
        walltime = self._guard(data, "walltime_guard")
        self.assertEqual(walltime["status"], "blocked")
        self.assertEqual(walltime["details"]["blocked_case_ids"], [11])

    def test_submit_execute_does_not_call_sbatch_when_guard_blocks(self) -> None:
        self._update_guards(
            {"campaign_size": {"enabled": True, "max_cases_per_submit": 5}}
        )

        with patch("campaign_workflow.submit_iteration.subprocess.run") as run_mock:
            rc, stdout, stderr = self._run_cli(
                "--action",
                "submit_iteration",
                "--iteration",
                "0",
                "--array-spec",
                "0-9",
                "--execute",
            )

        self.assertEqual(rc, 1)
        self.assertEqual(stdout, "")
        self.assertIn("blocked by guards", stderr)
        run_mock.assert_not_called()

    def test_submit_execute_calls_sbatch_when_guards_pass(self) -> None:
        self._update_guards(
            {"campaign_size": {"enabled": True, "max_cases_per_submit": 10}}
        )

        with patch("campaign_workflow.submit_iteration.subprocess.run") as run_mock:
            run_mock.return_value = CompletedProcessStub(
                returncode=0, stdout="123456\n"
            )
            rc, stdout, stderr = self._run_cli(
                "--action",
                "submit_iteration",
                "--iteration",
                "0",
                "--array-spec",
                "0-9",
                "--execute",
            )

        self.assertEqual(rc, 0, stderr)
        run_mock.assert_called_once()

    def test_guard_code_does_not_import_forbidden_packages(self) -> None:
        text = (
            (Path(__file__).resolve().parents[1] / "campaign_workflow" / "guards.py")
            .read_text(encoding="utf-8")
            .lower()
        )

        import_lines = "\n".join(
            line for line in text.splitlines() if line.startswith(("import ", "from "))
        )

        for token in [
            "campaign_optimizer",
            "optimas",
            "botorch",
            "torch",
            "h5py",
            "openpmd",
        ]:
            with self.subTest(token=token):
                self.assertNotIn(token, import_lines)

        for token in ["sbatch", "srun", "mpiexec", "mpirun", "python input.py"]:
            with self.subTest(token=token):
                self.assertNotIn(token, text)

    def _guard(self, data: dict, name: str) -> dict:
        for guard in data["guard_report"]["guards"]:
            if guard["name"] == name:
                return guard
        raise AssertionError(f"missing guard {name}")

    def _update_guards(self, guards: dict) -> None:
        cfg = json.loads((self.root / "optimization.json").read_text(encoding="utf-8"))
        cfg["guards"] = {"enabled": True, **guards}
        (self.root / "optimization.json").write_text(
            json.dumps(cfg, indent=2) + "\n",
            encoding="utf-8",
        )

    def _write_optimization_config(self) -> None:
        config = {
            "schema_version": 1,
            "optimization_name": "clpu_capillary_guiding_bo_001",
            "optimizer": {
                "working_directory": ".",
                "command": ["python", "-m", "dummy"],
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
            },
            "guards": {
                "enabled": True,
                "campaign_size": {
                    "enabled": True,
                    "max_cases_per_submit": 20,
                },
            },
        }

        self.root.mkdir(parents=True, exist_ok=True)
        (self.root / "optimization.json").write_text(
            json.dumps(config, indent=2) + "\n",
            encoding="utf-8",
        )

    def _write_iteration_fixture(self, *, n_cases: int) -> None:
        self.iter_root.mkdir(parents=True, exist_ok=True)

        campaign = {
            "schema_version": 1,
            "campaign_name": "clpu_capillary_guiding_bo_001_iter_000",
            "case_manifest": "cases.tsv",
            "case_manifest_format": "tsv",
            "case_id_column": "CASE_ID",
            "case_name_column": "CASE_NAME",
            "simulation": {
                "backend": "warpx_picmi",
                "scheduler": "slurm",
                "input_script": "input.py",
                "completion_marker": "post/sim_done.json",
                "failure_marker": "post/sim_failed.json",
            },
            "analysis": {"outputs": []},
            "state": {
                "state_file": "state.json",
                "validation_file": "validation.json",
                "locks_dir": "locks",
                "manifests_dir": "manifests",
                "post_dir": "post",
                "logs_dir": "logs",
            },
            "cleanup": {
                "raw_delete_globs": ["diags/fields/*.h5"],
            },
        }

        (self.iter_root / "campaign.json").write_text(
            json.dumps(campaign, indent=2) + "\n",
            encoding="utf-8",
        )

        lines = [
            "CASE_ID\tCASE_NAME\tLASER_CASE\tPLASMA_KIND\tN0_CM3\tPLATEAU_LENGTH_MM"
        ]
        for case_id in range(n_cases):
            plateau = 5 if case_id == 0 else 25
            lines.append(f"{case_id}\t{case_id:03d}_case\tf20\tchan\t4e18\t{plateau}")

        (self.iter_root / "cases.tsv").write_text(
            "\n".join(lines) + "\n",
            encoding="utf-8",
        )

        (self.iter_root / "input_template.py").write_text(
            "# template\n",
            encoding="utf-8",
        )
        (self.iter_root / "array_logs").mkdir(exist_ok=True)

        for name in [
            "materialize_cases.log",
            "init_case_states.log",
            "materialize_cases_dry_run.log",
            "init_case_states_dry_run.log",
        ]:
            (self.iter_root / name).write_text("ok\n", encoding="utf-8")

        for case_id in range(n_cases):
            case_name = f"{case_id:03d}_case"
            case = CaseRecord(case_id=case_id, case_name=case_name, row={})
            case_dir = self.iter_root / case_name

            for subdir in [
                "post",
                "logs",
                "locks",
                "manifests",
                "diags",
                "diags/fields",
            ]:
                (case_dir / subdir).mkdir(parents=True, exist_ok=True)

            (case_dir / "state.json").write_text(
                json.dumps(initial_state_document(case), indent=2) + "\n",
                encoding="utf-8",
            )
            (case_dir / "validation.json").write_text(
                json.dumps(initial_validation_document(case), indent=2) + "\n",
                encoding="utf-8",
            )

    def _run_cli(self, *extra_args: str) -> tuple[int, str, str]:
        stdout = io.StringIO()
        stderr = io.StringIO()

        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            rc = optimizer_tick_main(
                ["--optimization-root", str(self.root), *extra_args]
            )

        return rc, stdout.getvalue(), stderr.getvalue()


if __name__ == "__main__":
    unittest.main()
