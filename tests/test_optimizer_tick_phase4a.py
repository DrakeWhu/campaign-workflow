from __future__ import annotations

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

from campaign_workflow.cli.optimizer_tick import main as optimizer_tick_main
from campaign_workflow.core.atomic_io import read_json
from campaign_workflow.core.state import (
    initial_state_document,
    initial_validation_document,
)
from campaign_workflow.core.tsv_cases import CaseRecord


class OptimizerTickPhase4ATests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmpdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tmpdir.name) / "clpu_capillary_guiding_bo_001"
        self.iter_root = self.root / "iterations" / "iter_000"
        self.iter_root.mkdir(parents=True)
        (self.root / "optimizer_runs" / "iter_000").mkdir(parents=True)
        self._write_iteration_fixture()

    def tearDown(self) -> None:
        self.tmpdir.cleanup()

    def test_dry_run_audits_valid_materialized_iteration(self) -> None:
        rc, stdout, stderr = self._run_cli("--dry-run")
        self.assertEqual(rc, 0, stderr)
        data = json.loads(stdout)
        self.assertEqual(data["mode"], "dry-run")
        self.assertFalse(data["state_written"])
        self.assertEqual(data["recommended_action"], "submit_iteration")
        self.assertEqual(data["n_iterations"], 1)

        iteration = data["iterations"][0]
        self.assertEqual(iteration["iteration"], 0)
        self.assertEqual(iteration["status"], "campaign_materialized")
        self.assertEqual(iteration["recommended_action"], "submit_iteration")
        self.assertEqual(iteration["n_cases"], 3)
        self.assertEqual(iteration["n_case_dirs"], 3)
        self.assertEqual(iteration["n_case_states"], 3)
        self.assertEqual(iteration["n_sim_done"], 0)
        self.assertEqual(iteration["n_sim_failed"], 0)
        self.assertEqual(iteration["n_reduced_valid"], 0)
        self.assertEqual(iteration["n_raw_deleted"], 0)
        self.assertEqual(iteration["errors"], [])

    def test_dry_run_with_iteration_filter_audits_only_that_iteration(self) -> None:
        rc, stdout, stderr = self._run_cli("--iteration", "0", "--dry-run")
        self.assertEqual(rc, 0, stderr)
        data = json.loads(stdout)
        self.assertEqual(data["iteration_filter"], 0)
        self.assertEqual(len(data["iterations"]), 1)
        self.assertEqual(data["iterations"][0]["iteration"], 0)

    def test_dry_run_does_not_create_or_modify_optimization_state(self) -> None:
        state_path = self.root / "optimization_state.json"
        self.assertFalse(state_path.exists())
        rc, _stdout, stderr = self._run_cli("--dry-run")
        self.assertEqual(rc, 0, stderr)
        self.assertFalse(state_path.exists())

        state_path.write_text(
            '{"schema_version": 1, "sentinel": "keep"}\n', encoding="utf-8"
        )
        before = state_path.read_text(encoding="utf-8")
        rc, _stdout, stderr = self._run_cli("--dry-run")
        self.assertEqual(rc, 0, stderr)
        self.assertEqual(state_path.read_text(encoding="utf-8"), before)

    def test_init_state_creates_optimization_state_explicitly(self) -> None:
        state_path = self.root / "optimization_state.json"
        rc, stdout, stderr = self._run_cli("--init-state")
        self.assertEqual(rc, 0, stderr)
        self.assertTrue(state_path.is_file())
        data = json.loads(stdout)
        written = read_json(state_path)
        self.assertEqual(data["mode"], "init-state")
        self.assertTrue(data["state_written"])
        self.assertEqual(written["schema_version"], 1)
        self.assertEqual(written["optimization_name"], "clpu_capillary_guiding_bo_001")
        self.assertEqual(written["latest_iteration"], 0)
        self.assertEqual(
            written["iterations"][0]["recommended_action"], "submit_iteration"
        )

    def test_write_state_updates_optimization_state_explicitly(self) -> None:
        state_path = self.root / "optimization_state.json"
        state_path.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "optimization_name": "old",
                    "status": "planned",
                    "latest_iteration": None,
                    "iterations": [],
                }
            )
            + "\n",
            encoding="utf-8",
        )
        rc, stdout, stderr = self._run_cli("--write-state")
        self.assertEqual(rc, 0, stderr)
        data = json.loads(stdout)
        written = read_json(state_path)
        self.assertEqual(data["mode"], "write-state")
        self.assertTrue(data["state_written"])
        self.assertEqual(written["optimization_name"], "clpu_capillary_guiding_bo_001")
        self.assertEqual(written["latest_iteration"], 0)
        self.assertEqual(written["iterations"][0]["n_cases"], 3)

    def test_pause_file_recommends_paused(self) -> None:
        (self.root / "PAUSE_OPTIMIZATION").write_text(
            "manual pause\n", encoding="utf-8"
        )
        rc, stdout, stderr = self._run_cli("--dry-run")
        self.assertEqual(rc, 0, stderr)
        data = json.loads(stdout)
        self.assertTrue(data["pause_file_exists"])
        self.assertEqual(data["recommended_action"], "paused")
        self.assertEqual(data["iterations"][0]["recommended_action"], "paused")
        self.assertEqual(data["iterations"][0]["status"], "paused")

    def test_missing_campaign_json_is_detected(self) -> None:
        (self.iter_root / "campaign.json").unlink()
        rc, stdout, stderr = self._run_cli("--dry-run")
        self.assertEqual(rc, 0, stderr)
        iteration = json.loads(stdout)["iterations"][0]
        self.assertFalse(iteration["checks"]["campaign_json_exists"])
        self.assertIn("missing required file: campaign.json", iteration["errors"])

    def test_missing_cases_tsv_is_detected(self) -> None:
        (self.iter_root / "cases.tsv").unlink()
        rc, stdout, stderr = self._run_cli("--dry-run")
        self.assertEqual(rc, 0, stderr)
        iteration = json.loads(stdout)["iterations"][0]
        self.assertFalse(iteration["checks"]["cases_tsv_exists"])
        self.assertIn("missing required file: cases.tsv", iteration["errors"])

    def test_missing_input_template_is_detected(self) -> None:
        (self.iter_root / "input_template.py").unlink()
        rc, stdout, stderr = self._run_cli("--dry-run")
        self.assertEqual(rc, 0, stderr)
        iteration = json.loads(stdout)["iterations"][0]
        self.assertFalse(iteration["checks"]["input_template_exists"])
        self.assertIn("missing required file: input_template.py", iteration["errors"])

    def test_missing_array_logs_is_detected(self) -> None:
        (self.iter_root / "array_logs").rmdir()
        rc, stdout, stderr = self._run_cli("--dry-run")
        self.assertEqual(rc, 0, stderr)
        iteration = json.loads(stdout)["iterations"][0]
        self.assertFalse(iteration["checks"]["array_logs_exists"])
        self.assertIn("missing required directory: array_logs", iteration["errors"])

    def test_counts_cases_from_cases_tsv(self) -> None:
        rc, stdout, stderr = self._run_cli("--dry-run")
        self.assertEqual(rc, 0, stderr)
        self.assertEqual(json.loads(stdout)["iterations"][0]["n_cases"], 3)

    def test_counts_case_dirs(self) -> None:
        case_dir = self.iter_root / "002_case"
        for child in sorted(case_dir.rglob("*"), reverse=True):
            if child.is_file():
                child.unlink()
            else:
                child.rmdir()
        case_dir.rmdir()
        rc, stdout, stderr = self._run_cli("--dry-run")
        self.assertEqual(rc, 0, stderr)
        iteration = json.loads(stdout)["iterations"][0]
        self.assertEqual(iteration["n_case_dirs"], 2)
        self.assertIn("002_case", iteration["missing_case_dirs"])

    def test_counts_state_json_files(self) -> None:
        (self.iter_root / "001_case" / "state.json").unlink()
        rc, stdout, stderr = self._run_cli("--dry-run")
        self.assertEqual(rc, 0, stderr)
        iteration = json.loads(stdout)["iterations"][0]
        self.assertEqual(iteration["n_case_states"], 2)
        self.assertIn("001_case", iteration["missing_state_json"])

    def test_counts_sim_done_and_sim_failed_markers(self) -> None:
        (self.iter_root / "000_case" / "post" / "sim_done.json").write_text(
            "{}\n", encoding="utf-8"
        )
        (self.iter_root / "001_case" / "post" / "sim_failed.json").write_text(
            "{}\n", encoding="utf-8"
        )
        rc, stdout, stderr = self._run_cli("--dry-run")
        self.assertEqual(rc, 0, stderr)
        iteration = json.loads(stdout)["iterations"][0]
        self.assertEqual(iteration["n_sim_done"], 1)
        self.assertEqual(iteration["n_sim_failed"], 1)
        self.assertEqual(iteration["recommended_action"], "inspect_failures")

    def test_counts_reduced_valid_and_raw_deleted_markers(self) -> None:
        validation_path = self.iter_root / "000_case" / "validation.json"
        validation = read_json(validation_path)
        validation["reduced"]["guiding_metrics"] = {"ok": True, "required": True}
        validation_path.write_text(
            json.dumps(validation, indent=2) + "\n", encoding="utf-8"
        )
        (self.iter_root / "000_case" / "post" / "raw_deleted.json").write_text(
            "{}\n", encoding="utf-8"
        )

        rc, stdout, stderr = self._run_cli("--dry-run")
        self.assertEqual(rc, 0, stderr)
        iteration = json.loads(stdout)["iterations"][0]
        self.assertEqual(iteration["n_reduced_valid"], 1)
        self.assertEqual(iteration["n_raw_deleted"], 1)

    def test_new_code_does_not_call_slurm_or_subprocess(self) -> None:
        text = self._read_optimizer_tick_code_text()
        self.assertNotIn("import subprocess", text)
        self.assertNotIn("os.system", text)
        self.assertNotIn("subprocess.", text)
        self.assertNotIn("sbatch", text)
        self.assertNotIn("srun", text)
        self.assertNotIn("mpiexec", text)
        self.assertNotIn("mpirun", text)

    def test_new_code_does_not_import_campaign_optimizer_or_optimizer_packages(
        self,
    ) -> None:
        lowered = self._read_optimizer_tick_code_text().lower()
        for token in [
            "campaign_optimizer",
            "campaign-optimizer",
            "import optimas",
            "from optimas",
            "import botorch",
            "from botorch",
            "import torch",
            "from torch",
            "import ax",
            "from ax",
        ]:
            self.assertNotIn(token, lowered)

    def test_new_code_does_not_read_raw_binary_diagnostics(self) -> None:
        text = self._read_optimizer_tick_code_text()
        self.assertNotIn("import h5py", text)
        self.assertNotIn("openpmd_api", text)
        self.assertNotIn("OpenPMDTimeSeries", text)
        self.assertNotIn("*.h5", text)
        self.assertNotIn("*.hdf5", text)

    def test_missing_optimization_root_fails_clearly(self) -> None:
        missing = Path(self.tmpdir.name) / "missing_root"
        stdout = io.StringIO()
        stderr = io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            rc = optimizer_tick_main(["--optimization-root", str(missing), "--dry-run"])
        self.assertEqual(rc, 1)
        self.assertIn("optimization root does not exist", stderr.getvalue())
        self.assertEqual(stdout.getvalue(), "")

    def test_existing_lock_is_detected(self) -> None:
        (self.root / ".optimizer_tick.lock").mkdir()
        rc, stdout, stderr = self._run_cli("--dry-run")
        self.assertEqual(rc, 1)
        self.assertEqual(stdout, "")
        self.assertIn("optimizer tick lock already exists", stderr)

    def test_materialized_unsubmitted_iteration_recommends_submit_iteration(
        self,
    ) -> None:
        rc, stdout, stderr = self._run_cli("--dry-run")
        self.assertEqual(rc, 0, stderr)
        data = json.loads(stdout)
        self.assertEqual(data["recommended_action"], "submit_iteration")
        self.assertEqual(
            data["iterations"][0]["recommended_action"], "submit_iteration"
        )

    def test_paused_iteration_recommends_paused(self) -> None:
        (self.root / "PAUSE_OPTIMIZATION").write_text("pause\n", encoding="utf-8")
        rc, stdout, stderr = self._run_cli("--dry-run")
        self.assertEqual(rc, 0, stderr)
        iteration = json.loads(stdout)["iterations"][0]
        self.assertEqual(iteration["recommended_action"], "paused")
        self.assertEqual(iteration["status"], "paused")

    def _write_iteration_fixture(self) -> None:
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
        (self.iter_root / "cases.tsv").write_text(
            "CASE_ID\tCASE_NAME\tLASER_CASE\tPLASMA_KIND\tN0_CM3\tPLATEAU_LENGTH_MM\n"
            "0\t000_case\tf20\tchan\t4e18\t25\n"
            "1\t001_case\tf20\tchan\t4e18\t25\n"
            "2\t002_case\tf20\tchan\t4e18\t25\n",
            encoding="utf-8",
        )
        (self.iter_root / "input_template.py").write_text(
            "# template\n", encoding="utf-8"
        )
        (self.iter_root / "array_logs").mkdir()
        (self.iter_root / "materialize_cases.log").write_text("ok\n", encoding="utf-8")
        (self.iter_root / "init_case_states.log").write_text("ok\n", encoding="utf-8")
        (self.iter_root / "materialize_cases_dry_run.log").write_text(
            "ok\n", encoding="utf-8"
        )
        (self.iter_root / "init_case_states_dry_run.log").write_text(
            "ok\n", encoding="utf-8"
        )

        for case_id, case_name in [(0, "000_case"), (1, "001_case"), (2, "002_case")]:
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

    def _run_cli(self, *extra_args: str) -> tuple[int, str, str]:
        stdout = io.StringIO()
        stderr = io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            rc = optimizer_tick_main(
                ["--optimization-root", str(self.root), *extra_args]
            )
        return rc, stdout.getvalue(), stderr.getvalue()

    def _read_optimizer_tick_code_text(self) -> str:
        repo_root = Path(__file__).resolve().parents[1]
        paths = [
            repo_root / "campaign_workflow" / "optimization_state.py",
            repo_root / "campaign_workflow" / "optimizer_tick.py",
        ]
        return "\n".join(path.read_text(encoding="utf-8") for path in paths)


if __name__ == "__main__":
    unittest.main()
