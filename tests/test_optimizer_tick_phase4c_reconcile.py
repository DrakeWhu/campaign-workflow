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
from campaign_workflow.reconcile_iteration import (
    ReconcileIterationError,
    parse_array_spec,
)


class CompletedProcessStub:
    def __init__(self, returncode: int, stdout: str = "", stderr: str = "") -> None:
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


class OptimizerTickPhase4CReconcileTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmpdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tmpdir.name) / "clpu_capillary_guiding_bo_001"
        self.iter_root = self.root / "iterations" / "iter_000"
        self.iter_root.mkdir(parents=True)
        (self.root / "optimizer" / "iter_000").mkdir(parents=True)
        self._write_iteration_fixture(n_cases=30)
        self._write_submitted_state(array_spec="0-9", job_ids=["611084"])

    def tearDown(self) -> None:
        self.tmpdir.cleanup()

    def test_parse_array_spec_range(self) -> None:
        self.assertEqual(parse_array_spec("0-9"), list(range(10)))

    def test_parse_array_spec_list(self) -> None:
        self.assertEqual(parse_array_spec("0,2,4"), [0, 2, 4])

    def test_parse_array_spec_mixed(self) -> None:
        self.assertEqual(parse_array_spec("0-3,7,9"), [0, 1, 2, 3, 7, 9])

    def test_parse_array_spec_invalid_fails_clearly(self) -> None:
        for bad in ["", "9-0", "0--9", "0-9%2", "0:9", "a-b", " 0-9"]:
            with self.subTest(bad=bad):
                with self.assertRaises(ReconcileIterationError):
                    parse_array_spec(bad)

    def test_reconcile_dry_run_does_not_modify_optimization_state(self) -> None:
        state_path = self.root / "optimization_state.json"
        before = state_path.read_text(encoding="utf-8")

        with self._mock_squeue("611084_0 RUNNING\n"):
            rc, stdout, stderr = self._run_cli(
                "--iteration", "0", "--action", "reconcile_iteration", "--dry-run"
            )

        self.assertEqual(rc, 0, stderr)
        self.assertEqual(state_path.read_text(encoding="utf-8"), before)
        data = json.loads(stdout)
        self.assertEqual(data["mode"], "dry-run")
        self.assertFalse(data["state_written"])
        self.assertEqual(data["reconciliation"]["recommended_action"], "wait_for_jobs")

    def test_reconcile_write_state_updates_optimization_state(self) -> None:
        with self._mock_squeue("611084_0 RUNNING\n"):
            rc, _stdout, stderr = self._run_cli(
                "--iteration", "0", "--action", "reconcile_iteration", "--write-state"
            )

        self.assertEqual(rc, 0, stderr)
        state = read_json(self.root / "optimization_state.json")
        iteration = state["iterations"][0]
        self.assertEqual(state["status"], "running")
        self.assertEqual(iteration["status"], "running")
        self.assertEqual(iteration["recommended_action"], "wait_for_jobs")
        self.assertEqual(iteration["submitted_case_count"], 10)
        self.assertEqual(iteration["n_cases_materialized"], 30)
        self.assertEqual(iteration["n_cases_unsubmitted"], 20)

    def test_slurm_running_or_pending_recommends_wait_for_jobs(self) -> None:
        for squeue_out in ["611084_0 RUNNING\n", "611084_[0-9] PENDING\n"]:
            with self.subTest(squeue_out=squeue_out):
                with self._mock_squeue(squeue_out):
                    rc, stdout, stderr = self._run_cli(
                        "--iteration",
                        "0",
                        "--action",
                        "reconcile_iteration",
                        "--dry-run",
                    )
                self.assertEqual(rc, 0, stderr)
                rec = json.loads(stdout)["reconciliation"]
                self.assertEqual(rec["status"], "running")
                self.assertEqual(rec["recommended_action"], "wait_for_jobs")

    def test_all_submitted_cases_done_and_reduced_valid_are_reduced_ready(self) -> None:
        self._mark_done_and_reduced_valid(case_ids=range(10))

        with self._mock_squeue(""):
            rc, stdout, stderr = self._run_cli(
                "--iteration", "0", "--action", "reconcile_iteration", "--dry-run"
            )

        self.assertEqual(rc, 0, stderr)
        rec = json.loads(stdout)["reconciliation"]
        self.assertEqual(rec["status"], "reduced_ready")
        self.assertEqual(rec["recommended_action"], "close_iteration")
        self.assertEqual(rec["n_submitted_sim_done"], 10)
        self.assertEqual(rec["n_submitted_reduced_valid"], 10)

    def test_unsubmitted_cases_do_not_block_reduced_ready(self) -> None:
        self._mark_done_and_reduced_valid(case_ids=range(10))

        with self._mock_squeue(""):
            rc, stdout, stderr = self._run_cli(
                "--iteration", "0", "--action", "reconcile_iteration", "--dry-run"
            )

        self.assertEqual(rc, 0, stderr)
        rec = json.loads(stdout)["reconciliation"]
        self.assertEqual(rec["n_cases_materialized"], 30)
        self.assertEqual(rec["submitted_case_count"], 10)
        self.assertEqual(rec["n_cases_unsubmitted"], 20)
        self.assertEqual(rec["status"], "reduced_ready")

    def test_sim_failed_recommends_inspect_failures(self) -> None:
        (self.iter_root / "003_case" / "post" / "sim_failed.json").write_text(
            "{}\n", encoding="utf-8"
        )

        with self._mock_squeue(""):
            rc, stdout, stderr = self._run_cli(
                "--iteration", "0", "--action", "reconcile_iteration", "--dry-run"
            )

        self.assertEqual(rc, 0, stderr)
        rec = json.loads(stdout)["reconciliation"]
        self.assertEqual(rec["status"], "needs_inspection")
        self.assertEqual(rec["recommended_action"], "inspect_failures")
        self.assertEqual(rec["n_submitted_sim_failed"], 1)

    def test_missing_markers_and_job_absent_recommends_inspect_failures(self) -> None:
        with self._mock_squeue(""):
            rc, stdout, stderr = self._run_cli(
                "--iteration", "0", "--action", "reconcile_iteration", "--dry-run"
            )

        self.assertEqual(rc, 0, stderr)
        rec = json.loads(stdout)["reconciliation"]
        self.assertEqual(rec["status"], "needs_inspection")
        self.assertEqual(rec["recommended_action"], "inspect_failures")
        self.assertEqual(rec["n_submitted_unknown"], 10)

    def test_pause_file_dominates(self) -> None:
        (self.root / "PAUSE_OPTIMIZATION").write_text("pause\n", encoding="utf-8")

        with self._mock_squeue("611084_0 RUNNING\n"):
            rc, stdout, stderr = self._run_cli(
                "--iteration", "0", "--action", "reconcile_iteration", "--dry-run"
            )

        self.assertEqual(rc, 0, stderr)
        rec = json.loads(stdout)["reconciliation"]
        self.assertEqual(rec["status"], "paused")
        self.assertEqual(rec["recommended_action"], "paused")

    def test_missing_iteration_fails_clearly(self) -> None:
        with self._mock_squeue(""):
            rc, stdout, stderr = self._run_cli(
                "--iteration", "1", "--action", "reconcile_iteration", "--dry-run"
            )

        self.assertEqual(rc, 1)
        self.assertEqual(stdout, "")
        self.assertIn("iteration not found: iter_001", stderr)

    def test_state_without_job_id_is_clear(self) -> None:
        self._write_submitted_state(array_spec="0-9", job_ids=[])

        with self._mock_squeue(""):
            rc, stdout, stderr = self._run_cli(
                "--iteration", "0", "--action", "reconcile_iteration", "--dry-run"
            )

        self.assertEqual(rc, 0, stderr)
        rec = json.loads(stdout)["reconciliation"]
        self.assertEqual(rec["slurm"]["reason"], "no_slurm_job_ids")
        self.assertEqual(rec["status"], "needs_inspection")
        self.assertEqual(rec["recommended_action"], "inspect_failures")

    def test_reconcile_only_calls_squeue_not_submission_commands(self) -> None:
        with self._mock_squeue("611084_0 RUNNING\n") as run_mock:
            rc, _stdout, stderr = self._run_cli(
                "--iteration", "0", "--action", "reconcile_iteration", "--dry-run"
            )

        self.assertEqual(rc, 0, stderr)
        run_mock.assert_called_once()
        command = run_mock.call_args.args[0]
        self.assertEqual(command[0], "squeue")
        self.assertNotIn("sbatch", command)
        self.assertNotIn("srun", command)

    def test_reconcile_code_does_not_contain_forbidden_runners(self) -> None:
        text = self._read_reconcile_code_text().lower()
        for token in [
            "sbatch",
            "srun",
            "mpiexec",
            "mpirun",
            "warpx",
            "python input.py",
        ]:
            with self.subTest(token=token):
                self.assertNotIn(token, text)

    def test_reconcile_code_does_not_import_campaign_optimizer(self) -> None:
        text = self._read_reconcile_code_text().lower()
        for token in [
            "campaign_optimizer",
            "campaign-optimizer",
            "optimas",
            "botorch",
            "import torch",
            "from torch",
            "import ax",
            "from ax",
        ]:
            with self.subTest(token=token):
                self.assertNotIn(token, text)

    def test_reconcile_code_does_not_read_hdf5_or_openpmd(self) -> None:
        text = self._read_reconcile_code_text().lower()
        for token in ["h5py", "openpmd", "*.h5", "*.hdf5"]:
            with self.subTest(token=token):
                self.assertNotIn(token, text)

    def test_phase4a_plain_dry_run_still_works(self) -> None:
        with self._mock_squeue("611084_0 RUNNING\n"):
            rc, stdout, stderr = self._run_cli("--dry-run")

        self.assertEqual(rc, 0, stderr)
        data = json.loads(stdout)
        self.assertEqual(data["mode"], "dry-run")
        self.assertFalse(data["state_written"])
        self.assertIn(data["recommended_action"], {"wait_for_jobs", "submit_iteration"})

    def test_phase4b_submit_iteration_dry_run_still_works(self) -> None:
        # Use a non-submitted optimization state for this compatibility check.
        (self.root / "optimization_state.json").unlink()

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
        self.assertEqual(data["submit_plan"]["array_spec"], "0-9")
        self.assertFalse(data["state_written"])

    def _write_iteration_fixture(self, *, n_cases: int) -> None:
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

    def _write_submitted_state(self, *, array_spec: str, job_ids: list[str]) -> None:
        submitted_case_ids = parse_array_spec(array_spec)
        state = {
            "schema_version": 1,
            "optimization_name": "clpu_capillary_guiding_bo_001",
            "status": "running",
            "latest_iteration": 0,
            "updated_at": "2026-06-30T00:00:00Z",
            "iterations": [
                {
                    "iteration": 0,
                    "campaign_root": "iterations/iter_000",
                    "status": "submitted",
                    "submitted": True,
                    "slurm_job_ids": job_ids,
                    "array_spec": array_spec,
                    "submitted_case_ids": submitted_case_ids,
                    "submitted_case_count": len(submitted_case_ids),
                    "n_cases": 30,
                    "n_case_dirs": 30,
                    "n_case_states": 30,
                    "n_sim_done": 0,
                    "n_sim_failed": 0,
                    "n_reduced_valid": 0,
                    "n_raw_deleted": 0,
                    "recommended_action": "wait_for_jobs",
                }
            ],
        }
        (self.root / "optimization_state.json").write_text(
            json.dumps(state, indent=2) + "\n", encoding="utf-8"
        )

    def _mark_done_and_reduced_valid(self, *, case_ids) -> None:
        for case_id in case_ids:
            case_dir = self.iter_root / f"{case_id:03d}_case"
            (case_dir / "post" / "sim_done.json").write_text("{}\n", encoding="utf-8")
            validation_path = case_dir / "validation.json"
            validation = read_json(validation_path)
            validation["reduced"]["guiding_metrics"] = {"ok": True, "required": True}
            validation_path.write_text(
                json.dumps(validation, indent=2) + "\n", encoding="utf-8"
            )

    def _run_cli(self, *extra_args: str) -> tuple[int, str, str]:
        stdout = io.StringIO()
        stderr = io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            rc = optimizer_tick_main(
                ["--optimization-root", str(self.root), *extra_args]
            )
        return rc, stdout.getvalue(), stderr.getvalue()

    @contextlib.contextmanager
    def _mock_squeue(self, stdout: str):
        with patch("campaign_workflow.reconcile_iteration.subprocess.run") as run_mock:
            run_mock.return_value = CompletedProcessStub(
                returncode=0, stdout=stdout, stderr=""
            )
            yield run_mock

    def _read_reconcile_code_text(self) -> str:
        repo_root = Path(__file__).resolve().parents[1]
        return (repo_root / "campaign_workflow" / "reconcile_iteration.py").read_text(
            encoding="utf-8"
        )


if __name__ == "__main__":
    unittest.main()
