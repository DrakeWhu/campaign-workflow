from __future__ import annotations

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from campaign_workflow.cli.optimizer_tick import main as optimizer_tick_main
from campaign_workflow.core.atomic_io import read_json
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


class OptimizerTickPhase4BSubmitTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmpdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tmpdir.name) / "clpu_capillary_guiding_bo_001"
        self.iter_root = self.root / "iterations" / "iter_000"
        self.iter_root.mkdir(parents=True)
        (self.root / "optimizer" / "iter_000").mkdir(parents=True)
        self.submit_script = Path(
            "examples/sunrise/submit_case_cycle_array.sh"
        ).resolve()
        self.case_runner = Path(
            "examples/sunrise/multichannel/run_warpx_multichannel_case_sunrise.sh"
        ).resolve()
        self._write_iteration_fixture(n_cases=12)

    def tearDown(self) -> None:
        self.tmpdir.cleanup()

    def test_submit_iteration_dry_run_does_not_call_sbatch(self) -> None:
        with patch("campaign_workflow.submit_iteration.subprocess.run") as run_mock:
            rc, stdout, stderr = self._run_cli(
                "--iteration",
                "0",
                "--action",
                "submit_iteration",
                "--array-spec",
                "0-9",
                "--dry-run",
            )
        self.assertEqual(rc, 0, stderr)
        run_mock.assert_not_called()
        data = json.loads(stdout)
        self.assertEqual(data["mode"], "dry-run")
        self.assertFalse(data["state_written"])
        self.assertEqual(data["submit_plan"]["array_spec"], "0-9")
        self.assertEqual(data["submit_plan"]["submitted_case_count"], 10)

    def test_submit_iteration_dry_run_does_not_modify_optimization_state(self) -> None:
        state_path = self.root / "optimization_state.json"
        state_path.write_text(
            '{"schema_version": 1, "sentinel": "keep"}\n', encoding="utf-8"
        )
        before = state_path.read_text(encoding="utf-8")
        rc, _stdout, stderr = self._run_cli(
            "--iteration",
            "0",
            "--action",
            "submit_iteration",
            "--array-spec",
            "0-9",
            "--dry-run",
        )
        self.assertEqual(rc, 0, stderr)
        self.assertEqual(state_path.read_text(encoding="utf-8"), before)

    def test_submit_iteration_execute_calls_sbatch_once_with_correct_cwd(self) -> None:
        with patch("campaign_workflow.submit_iteration.subprocess.run") as run_mock:
            run_mock.return_value = CompletedProcessStub(
                returncode=0, stdout="123456\n", stderr=""
            )
            rc, stdout, stderr = self._run_cli(
                "--iteration",
                "0",
                "--action",
                "submit_iteration",
                "--array-spec",
                "0-9",
                "--execute",
            )
        self.assertEqual(rc, 0, stderr)
        run_mock.assert_called_once()
        _args, kwargs = run_mock.call_args
        self.assertEqual(kwargs["cwd"], str(self.iter_root.resolve()))
        data = json.loads(stdout)
        self.assertEqual(data["submission_result"]["job_id"], "123456")

    def test_submit_iteration_execute_uses_existing_case_cycle_script(self) -> None:
        with patch("campaign_workflow.submit_iteration.subprocess.run") as run_mock:
            run_mock.return_value = CompletedProcessStub(
                returncode=0, stdout="123456;cluster\n", stderr=""
            )
            rc, stdout, stderr = self._run_cli(
                "--iteration",
                "0",
                "--action",
                "submit_iteration",
                "--array-spec",
                "0-9",
                "--execute",
            )
        self.assertEqual(rc, 0, stderr)
        command = run_mock.call_args.args[0]
        self.assertIn(str(self.submit_script), command)
        self.assertEqual(command[0], "sbatch")
        self.assertIn("--parsable", command)
        self.assertIn("--array=0-9", command)
        self.assertNotIn("submit_campaign_pipeline_sunrise.sh", " ".join(command))

    def test_submit_iteration_execute_captures_parsable_job_id(self) -> None:
        with patch("campaign_workflow.submit_iteration.subprocess.run") as run_mock:
            run_mock.return_value = CompletedProcessStub(
                returncode=0, stdout="123456;cluster\n", stderr=""
            )
            rc, stdout, stderr = self._run_cli(
                "--iteration",
                "0",
                "--action",
                "submit_iteration",
                "--array-spec",
                "0-9",
                "--execute",
            )
        self.assertEqual(rc, 0, stderr)
        self.assertEqual(json.loads(stdout)["submission_result"]["job_id"], "123456")

    def test_submit_iteration_execute_updates_optimization_state(self) -> None:
        with patch("campaign_workflow.submit_iteration.subprocess.run") as run_mock:
            run_mock.return_value = CompletedProcessStub(
                returncode=0, stdout="123456\n", stderr=""
            )
            rc, stdout, stderr = self._run_cli(
                "--iteration",
                "0",
                "--action",
                "submit_iteration",
                "--array-spec",
                "0-9",
                "--execute",
            )
        self.assertEqual(rc, 0, stderr)
        state = read_json(self.root / "optimization_state.json")
        iteration = state["iterations"][0]
        self.assertEqual(state["status"], "running")
        self.assertEqual(iteration["status"], "submitted")
        self.assertTrue(iteration["submitted"])
        self.assertEqual(iteration["slurm_job_ids"], ["123456"])
        self.assertEqual(iteration["array_spec"], "0-9")
        self.assertEqual(iteration["submitted_case_count"], 10)
        self.assertEqual(iteration["submitted_case_ids"], list(range(10)))
        self.assertEqual(iteration["submit_command"][0], "sbatch")

    def test_sbatch_failure_does_not_mark_iteration_as_submitted(self) -> None:
        with patch("campaign_workflow.submit_iteration.subprocess.run") as run_mock:
            run_mock.return_value = CompletedProcessStub(
                returncode=1, stdout="", stderr="bad partition"
            )
            rc, stdout, stderr = self._run_cli(
                "--iteration",
                "0",
                "--action",
                "submit_iteration",
                "--array-spec",
                "0-9",
                "--execute",
            )
        self.assertEqual(rc, 1)
        self.assertEqual(stdout, "")
        self.assertIn("sbatch failed", stderr)
        self.assertFalse((self.root / "optimization_state.json").exists())

    def test_pause_file_blocks_submit_execute(self) -> None:
        (self.root / "PAUSE_OPTIMIZATION").write_text("pause\n", encoding="utf-8")
        with patch("campaign_workflow.submit_iteration.subprocess.run") as run_mock:
            rc, stdout, stderr = self._run_cli(
                "--iteration",
                "0",
                "--action",
                "submit_iteration",
                "--array-spec",
                "0-9",
                "--execute",
            )
        self.assertEqual(rc, 1)
        self.assertEqual(stdout, "")
        self.assertIn("PAUSE_OPTIMIZATION", stderr)
        run_mock.assert_not_called()

    def test_existing_lock_blocks_submit_execute(self) -> None:
        (self.root / ".optimizer_tick.lock").mkdir()
        with patch("campaign_workflow.submit_iteration.subprocess.run") as run_mock:
            rc, stdout, stderr = self._run_cli(
                "--iteration",
                "0",
                "--action",
                "submit_iteration",
                "--array-spec",
                "0-9",
                "--execute",
            )
        self.assertEqual(rc, 1)
        self.assertEqual(stdout, "")
        self.assertIn("optimizer tick lock already exists", stderr)
        run_mock.assert_not_called()

    def test_missing_iteration_fails_clearly(self) -> None:
        rc, stdout, stderr = self._run_cli(
            "--iteration",
            "1",
            "--action",
            "submit_iteration",
            "--array-spec",
            "0-9",
            "--dry-run",
        )
        self.assertEqual(rc, 1)
        self.assertEqual(stdout, "")
        self.assertIn("iteration not found: iter_001", stderr)

    def test_missing_campaign_json_or_cases_tsv_fails_clearly(self) -> None:
        (self.iter_root / "campaign.json").unlink()
        rc, stdout, stderr = self._run_cli(
            "--iteration",
            "0",
            "--action",
            "submit_iteration",
            "--array-spec",
            "0-9",
            "--dry-run",
        )
        self.assertEqual(rc, 1)
        self.assertEqual(stdout, "")
        self.assertIn("missing required file: campaign.json", stderr)

        self._write_iteration_fixture(n_cases=12)
        (self.iter_root / "cases.tsv").unlink()
        rc, stdout, stderr = self._run_cli(
            "--iteration",
            "0",
            "--action",
            "submit_iteration",
            "--array-spec",
            "0-9",
            "--dry-run",
        )
        self.assertEqual(rc, 1)
        self.assertEqual(stdout, "")
        self.assertIn("missing required file: cases.tsv", stderr)

    def test_already_submitted_iteration_is_not_resubmitted(self) -> None:
        state = self._existing_state_document(submitted=True)
        (self.root / "optimization_state.json").write_text(
            json.dumps(state, indent=2) + "\n", encoding="utf-8"
        )
        with patch("campaign_workflow.submit_iteration.subprocess.run") as run_mock:
            rc, stdout, stderr = self._run_cli(
                "--iteration",
                "0",
                "--action",
                "submit_iteration",
                "--array-spec",
                "0-9",
                "--execute",
            )
        self.assertEqual(rc, 1)
        self.assertEqual(stdout, "")
        self.assertIn("iteration already submitted", stderr)
        run_mock.assert_not_called()

    def test_array_spec_is_recorded(self) -> None:
        with patch("campaign_workflow.submit_iteration.subprocess.run") as run_mock:
            run_mock.return_value = CompletedProcessStub(
                returncode=0, stdout="123456\n", stderr=""
            )
            rc, _stdout, stderr = self._run_cli(
                "--iteration",
                "0",
                "--action",
                "submit_iteration",
                "--array-spec",
                "0-9",
                "--execute",
            )
        self.assertEqual(rc, 0, stderr)
        state = read_json(self.root / "optimization_state.json")
        self.assertEqual(state["iterations"][0]["array_spec"], "0-9")

    def test_staged_disjoint_submissions_accumulate_case_and_job_ids(self) -> None:
        with patch("campaign_workflow.submit_iteration.subprocess.run") as run_mock:
            run_mock.side_effect = [
                CompletedProcessStub(returncode=0, stdout="111111\n"),
                CompletedProcessStub(returncode=0, stdout="222222\n"),
            ]
            first_rc, _first_stdout, first_stderr = self._run_cli(
                "--iteration",
                "0",
                "--action",
                "submit_iteration",
                "--array-spec",
                "0",
                "--case-runner",
                str(self.case_runner),
                "--confirm-cleanup-execute",
                "--execute",
            )
            second_rc, second_stdout, second_stderr = self._run_cli(
                "--iteration",
                "0",
                "--action",
                "submit_iteration",
                "--array-spec",
                "1-11%2",
                "--case-runner",
                str(self.case_runner),
                "--allow-additional-cases",
                "--confirm-cleanup-execute",
                "--execute",
            )

        self.assertEqual(first_rc, 0, first_stderr)
        self.assertEqual(second_rc, 0, second_stderr)
        self.assertEqual(run_mock.call_count, 2)

        second = json.loads(second_stdout)
        self.assertTrue(second["submit_plan"]["additional_submission"])
        self.assertEqual(
            second["submit_plan"]["previous_submitted_case_ids"], [0]
        )
        self.assertEqual(
            second["submit_plan"]["cumulative_submitted_case_ids"],
            list(range(12)),
        )

        state = read_json(self.root / "optimization_state.json")
        iteration = state["iterations"][0]
        self.assertEqual(iteration["slurm_job_ids"], ["111111", "222222"])
        self.assertEqual(iteration["array_specs"], ["0", "1-11%2"])
        self.assertEqual(iteration["array_spec"], "0-11")
        self.assertEqual(iteration["submitted_case_ids"], list(range(12)))
        self.assertEqual(iteration["submitted_case_count"], 12)
        self.assertEqual(len(iteration["submission_history"]), 2)
        self.assertEqual(iteration["case_runner"], str(self.case_runner))
        self.assertIn(
            f"CASE_RUNNER={self.case_runner}",
            " ".join(run_mock.call_args.args[0]),
        )
        self.assertIn(
            "CONFIRM_CLEANUP_EXECUTE=1",
            " ".join(run_mock.call_args.args[0]),
        )

    def test_additional_submission_rejects_previously_submitted_case_ids(self) -> None:
        with patch("campaign_workflow.submit_iteration.subprocess.run") as run_mock:
            run_mock.return_value = CompletedProcessStub(
                returncode=0, stdout="111111\n"
            )
            first_rc, _first_stdout, first_stderr = self._run_cli(
                "--iteration",
                "0",
                "--action",
                "submit_iteration",
                "--array-spec",
                "0-2",
                "--execute",
            )
        self.assertEqual(first_rc, 0, first_stderr)

        second_rc, second_stdout, second_stderr = self._run_cli(
            "--iteration",
            "0",
            "--action",
            "submit_iteration",
            "--array-spec",
            "2-4",
            "--allow-additional-cases",
            "--dry-run",
        )
        self.assertEqual(second_rc, 1)
        self.assertEqual(second_stdout, "")
        self.assertIn("overlaps previously submitted", second_stderr)

    def test_invalid_array_spec_fails_clearly(self) -> None:
        for bad in ["", "9-0", "0--9", "a-b", "0-9%0", " 0-9"]:
            with self.subTest(bad=bad):
                rc, stdout, stderr = self._run_cli(
                    "--iteration",
                    "0",
                    "--action",
                    "submit_iteration",
                    "--array-spec",
                    bad,
                    "--dry-run",
                )
                self.assertEqual(rc, 1)
                self.assertEqual(stdout, "")
                self.assertIn("invalid", stderr.lower())

    def test_max_cases_produces_array_spec(self) -> None:
        rc, stdout, stderr = self._run_cli(
            "--iteration",
            "0",
            "--action",
            "submit_iteration",
            "--max-cases",
            "10",
            "--dry-run",
        )
        self.assertEqual(rc, 0, stderr)
        data = json.loads(stdout)
        self.assertEqual(data["submit_plan"]["array_spec"], "0-9")
        self.assertEqual(data["submit_plan"]["submitted_case_count"], 10)

    def test_new_submit_code_does_not_call_forbidden_runners(self) -> None:
        text = self._read_submit_code_text()
        self.assertNotIn("srun", text)
        self.assertNotIn("mpiexec", text)
        self.assertNotIn("mpirun", text)
        self.assertNotIn("warpx", text.lower())
        self.assertNotIn("python input.py", text)

    def test_new_submit_code_does_not_import_campaign_optimizer(self) -> None:
        lowered = self._read_submit_code_text().lower()
        for token in [
            "campaign_optimizer",
            "campaign-optimizer",
            "import optimas",
            "from optimas",
            "botorch",
            "import torch",
            "from torch",
            "import ax",
            "from ax",
        ]:
            with self.subTest(token=token):
                self.assertNotIn(token, lowered)

    def test_new_submit_code_does_not_read_hdf5_or_openpmd(self) -> None:
        text = self._read_submit_code_text().lower()
        for token in ["h5py", "openpmd", "*.h5", "*.hdf5"]:
            with self.subTest(token=token):
                self.assertNotIn(token, text)

    def test_phase4a_plain_dry_run_audit_still_works(self) -> None:
        rc, stdout, stderr = self._run_cli("--dry-run")
        self.assertEqual(rc, 0, stderr)
        data = json.loads(stdout)
        self.assertEqual(data["mode"], "dry-run")
        self.assertFalse(data["state_written"])
        self.assertEqual(data["recommended_action"], "submit_iteration")
        self.assertNotIn("submit_plan", data)

    def test_all_tests_use_unittest(self) -> None:
        self.assertTrue(issubclass(type(self), unittest.TestCase))

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
            "analysis": {
                "name": "guiding",
                "kind": "command",
                "outputs": [
                    {
                        "name": "guiding_metrics",
                        "kind": "csv",
                        "path": "guiding_metrics.csv",
                        "min_rows": 1,
                        "required_columns": ["iteration", "propagation_mm"],
                        "required": True,
                    }
                ],
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
        lines = [
            "CASE_ID\tCASE_NAME\tLASER_CASE\tPLASMA_KIND\tN0_CM3\tPLATEAU_LENGTH_MM"
        ]
        for case_id in range(n_cases):
            lines.append(f"{case_id}\t{case_id:03d}_case\tf20\tchan\t4e18\t25")
        (self.iter_root / "cases.tsv").write_text(
            "\n".join(lines) + "\n", encoding="utf-8"
        )
        (self.iter_root / "input_template.py").write_text(
            "# template\n", encoding="utf-8"
        )
        (self.iter_root / "array_logs").mkdir(exist_ok=True)
        (self.iter_root / "materialize_cases.log").write_text("ok\n", encoding="utf-8")
        (self.iter_root / "init_case_states.log").write_text("ok\n", encoding="utf-8")
        (self.iter_root / "materialize_cases_dry_run.log").write_text(
            "ok\n", encoding="utf-8"
        )
        (self.iter_root / "init_case_states_dry_run.log").write_text(
            "ok\n", encoding="utf-8"
        )

        for case_id in range(n_cases):
            case_name = f"{case_id:03d}_case"
            case = CaseRecord(case_id=case_id, case_name=case_name, row={})
            case_dir = self.iter_root / case_name
            for subdir in ["post", "logs", "locks", "manifests"]:
                (case_dir / subdir).mkdir(parents=True, exist_ok=True)
            (case_dir / "state.json").write_text(
                json.dumps(initial_state_document(case), indent=2) + "\n",
                encoding="utf-8",
            )
            (case_dir / "validation.json").write_text(
                json.dumps(initial_validation_document(case), indent=2) + "\n",
                encoding="utf-8",
            )

    def _existing_state_document(self, *, submitted: bool) -> dict:
        rc, stdout, stderr = self._run_cli("--dry-run")
        self.assertEqual(rc, 0, stderr)
        state = json.loads(stdout)["proposed_optimization_state"]
        state["iterations"][0]["submitted"] = submitted
        state["iterations"][0]["status"] = (
            "submitted" if submitted else "campaign_materialized"
        )
        state["iterations"][0]["slurm_job_ids"] = ["999999"] if submitted else []
        return state

    def _run_cli(self, *extra_args: str) -> tuple[int, str, str]:
        stdout = io.StringIO()
        stderr = io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            rc = optimizer_tick_main(
                ["--optimization-root", str(self.root), *extra_args]
            )
        return rc, stdout.getvalue(), stderr.getvalue()

    def _read_submit_code_text(self) -> str:
        repo_root = Path(__file__).resolve().parents[1]
        paths = [
            repo_root / "campaign_workflow" / "submit_iteration.py",
            repo_root / "campaign_workflow" / "cli" / "optimizer_tick.py",
        ]
        return "\n".join(path.read_text(encoding="utf-8") for path in paths)


if __name__ == "__main__":
    unittest.main()
