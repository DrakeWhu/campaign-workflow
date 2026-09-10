from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

from campaign_workflow.reconcile_iteration import (
    SubmittedCaseAudit,
    classify_reconciliation,
)


SCRIPT = Path("examples/sunrise/submit_clpu_n2_morbo_chain.py")


def _load_launcher():
    path = SCRIPT.resolve()
    spec = importlib.util.spec_from_file_location("submit_clpu_n2_morbo_chain_test", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"could not import {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class ClpuNitrogenRawRetentionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.module = _load_launcher()
        self.workflow_root = Path.cwd().resolve()
        self.tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmpdir.cleanup)
        self.tmp = Path(self.tmpdir.name)
        self.root = self.tmp / "optimization"
        self.root.mkdir()
        self.workflow_env = self.tmp / "campaign-workflow.sh"
        self.workflow_env.write_text("# test env\n", encoding="utf-8")

    def _write_policy(self, value) -> None:
        payload = {
            "schema_version": 1,
            "optimization_name": "clpu_n2_test",
            "policy": {"raw_retention_required": value},
        }
        (self.root / "optimization.json").write_text(
            json.dumps(payload) + "\n",
            encoding="utf-8",
        )

    def _argv(self) -> list[str]:
        return [
            "--optimization-root",
            str(self.root),
            "--start-iteration",
            "0",
            "--num-additional-iterations",
            "2",
            "--array-spec",
            "0-7",
            "--workflow-root",
            str(self.workflow_root),
            "--workflow-env",
            str(self.workflow_env),
            "--job-name-prefix",
            "clpu_n2",
        ]

    def test_dry_run_launch_command_forces_cleanup_off_even_if_environment_requests_one(self) -> None:
        self._write_policy(True)
        stdout = io.StringIO()
        with patch.dict(
            os.environ,
            {
                "CONFIRM_CLEANUP_EXECUTE": "1",
                "CW_RAW_RETENTION_REQUIRED": "0",
            },
            clear=False,
        ):
            with contextlib.redirect_stdout(stdout):
                rc = self.module.main(self._argv())

        self.assertEqual(rc, 0)
        payload = json.loads(stdout.getvalue())
        self.assertTrue(payload["dry_run"])
        self.assertEqual(
            payload["raw_retention_policy"],
            {
                "contract_id": "clpu_n2_retain_raw_v1",
                "required": True,
                "cleanup_execute": False,
            },
        )

        array_jobs = [job for job in payload["jobs"] if job["kind"] == "array"]
        self.assertEqual(len(array_jobs), 2)
        for job in array_jobs:
            command = " ".join(job["submit_command"])
            self.assertIn("CW_RAW_RETENTION_REQUIRED=1", command)
            self.assertIn("CONFIRM_CLEANUP_EXECUTE=0", command)
            self.assertNotIn("CONFIRM_CLEANUP_EXECUTE=1", command)

    def test_missing_or_false_retention_policy_is_rejected_before_chain_planning(self) -> None:
        stderr = io.StringIO()
        for value in (False, None):
            with self.subTest(value=value):
                if value is None:
                    (self.root / "optimization.json").write_text(
                        json.dumps({"schema_version": 1, "policy": {}}) + "\n",
                        encoding="utf-8",
                    )
                else:
                    self._write_policy(value)
                stderr.seek(0)
                stderr.truncate(0)
                with contextlib.redirect_stderr(stderr):
                    rc = self.module.main(self._argv())
                self.assertEqual(rc, 2)
                self.assertIn(
                    "policy.raw_retention_required=true",
                    stderr.getvalue(),
                )

    def test_retention_launcher_does_not_call_sbatch_without_execute(self) -> None:
        self._write_policy(True)
        stdout = io.StringIO()
        base = self.module._load_base_chain()
        with patch.object(base.subprocess, "run") as run_mock:
            self.module._require_retention_policy(base, self._argv())
            self.module._install_retention_contract(base)
            with contextlib.redirect_stdout(stdout):
                rc = base.main(self._argv())
        self.assertEqual(rc, 0)
        run_mock.assert_not_called()
        payload = json.loads(stdout.getvalue())
        self.assertTrue(payload["dry_run"])

    def test_reduced_ready_does_not_require_raw_deleted_state(self) -> None:
        audit = SubmittedCaseAudit(
            submitted_case_ids=[0],
            submitted_case_count=1,
            n_cases_materialized=1,
            n_cases_unsubmitted=0,
            n_submitted_case_dirs=1,
            n_submitted_case_states=1,
            n_submitted_sim_done=1,
            n_submitted_sim_failed=0,
            n_submitted_reduced_valid=1,
            n_submitted_raw_deleted=0,
            n_submitted_missing_state=0,
            n_submitted_missing_case_dir=0,
            n_submitted_unknown=0,
            submitted_unknown_case_ids=[],
            submitted_missing_case_dirs=[],
            submitted_missing_state=[],
            submitted_sim_done_case_ids=[0],
            submitted_sim_failed_case_ids=[],
            submitted_reduced_valid_case_ids=[0],
            submitted_raw_deleted_case_ids=[],
            array_log_files={},
        )
        status, action, warnings = classify_reconciliation(
            paused=False,
            iteration_state={"submitted": True, "status": "submitted"},
            iteration_summary={"status": "submitted"},
            slurm_info={
                "queried": True,
                "available": True,
                "active": False,
                "reason": "ok",
            },
            case_audit=audit,
        )
        self.assertEqual(status, "reduced_ready")
        self.assertEqual(action, "close_iteration")
        self.assertEqual(warnings, [])


if __name__ == "__main__":
    unittest.main()
