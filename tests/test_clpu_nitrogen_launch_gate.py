from __future__ import annotations

import contextlib
import importlib.util
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

from campaign_workflow.clpu_n2_submission import (
    ClpuN2SubmissionError,
    launch_fingerprint,
    launch_identity,
    require_launch_gate,
    sha256_file,
    submission_manifest_path,
    transactional_submit_finite_chain,
)


SCRIPT = Path("examples/sunrise/submit_clpu_n2_morbo_chain.py")


def _load_launcher():
    path = SCRIPT.resolve()
    spec = importlib.util.spec_from_file_location("submit_clpu_n2_morbo_chain_gate_test", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"could not import {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class ClpuNitrogenLaunchGateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.launcher = _load_launcher()
        self.base = self.launcher._load_base_chain()
        self.workflow_root = Path.cwd().resolve()
        self.tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmpdir.cleanup)
        self.tmp = Path(self.tmpdir.name)
        self.root = self.tmp / "optimization"
        self.root.mkdir()
        self.workflow_env = self.tmp / "campaign-workflow.sh"
        self.workflow_env.write_text("# test env\n", encoding="utf-8")
        (self.root / "optimization.json").write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "optimization_name": "clpu_n2_test",
                    "policy": {"raw_retention_required": True},
                }
            )
            + "\n",
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

    def _resolved_args(self):
        raw = self.base.build_parser().parse_args(self._argv() + ["--execute"])
        return self.base.resolve_args(raw)

    def _write_gate(self, **overrides) -> Path:
        path = self.root / "provenance" / "clpu_n2_launch_gate.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "schema_version": 1,
            "contract_id": "clpu_n2_launch_gate_v1",
            "status": "pass",
            "allow_sbatch": True,
            "gate_a_status": "pass",
            "gate_b_status": "pass",
            "f01_f03_status": "pass",
            "raw_retention_status": "pass",
            "raw_retention_capacity_status": "pass",
            "cleanup_execute": False,
            "iteration": 0,
            "optimization_root": str(self.root.resolve()),
        }
        payload.update(overrides)
        path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        return path

    def _install_retention(self) -> None:
        self.launcher._install_retention_contract(self.base)

    def test_failed_launch_gate_blocks_sbatch_before_any_publication(self) -> None:
        self._write_gate(gate_b_status="fail")
        stderr = io.StringIO()
        with patch.object(self.launcher, "_load_base_chain", return_value=self.base):
            with patch.object(self.base.subprocess, "run") as run_mock:
                with contextlib.redirect_stderr(stderr):
                    rc = self.launcher.main(self._argv() + ["--execute"])
        self.assertEqual(rc, 2)
        run_mock.assert_not_called()
        self.assertIn("gate_b_status='fail'", stderr.getvalue())
        self.assertFalse((self.root / "loop_logs").exists())

    def test_partial_acceptance_is_journaled_and_retry_resumes_without_duplicate(self) -> None:
        gate = self._write_gate()
        args = self._resolved_args()
        self._install_retention()
        gate_sha = sha256_file(gate)

        with patch.object(
            self.base,
            "execute_sbatch",
            side_effect=["100", self.base.MorboChainSubmitError("synthetic tick rejection")],
        ) as submit_mock:
            with self.assertRaisesRegex(self.base.MorboChainSubmitError, "synthetic tick rejection"):
                transactional_submit_finite_chain(
                    base=self.base,
                    args=args,
                    launch_gate_path=gate,
                    launch_gate_sha256=gate_sha,
                )
        self.assertEqual(submit_mock.call_count, 2)

        identity = launch_identity(args=args, launch_gate_sha256=gate_sha)
        fingerprint = launch_fingerprint(identity)
        manifest_path = submission_manifest_path(
            optimization_root=args.optimization_root,
            fingerprint=fingerprint,
        )
        partial = json.loads(manifest_path.read_text(encoding="utf-8"))
        self.assertEqual(partial["submission_status"], "partial_failure")
        self.assertEqual(partial["accepted_job_count"], 1)
        self.assertEqual(partial["jobs"][0]["job_id"], "100")
        first_receipt = Path(partial["jobs"][0]["receipt_path"])
        self.assertTrue(first_receipt.is_file())

        with patch.object(
            self.base,
            "execute_sbatch",
            side_effect=["101", "102", "103"],
        ) as retry_mock:
            completed = transactional_submit_finite_chain(
                base=self.base,
                args=args,
                launch_gate_path=gate,
                launch_gate_sha256=gate_sha,
            )

        self.assertEqual(retry_mock.call_count, 3)
        self.assertEqual(completed["submission_status"], "complete")
        self.assertTrue(completed["resumed"])
        self.assertEqual(
            [job["job_id"] for job in completed["jobs"]],
            ["100", "101", "102", "103"],
        )
        self.assertEqual(completed["jobs"][1]["dependency"], "afterok:100")
        self.assertEqual(completed["jobs"][2]["dependency"], "afterok:101")
        self.assertEqual(completed["jobs"][3]["dependency"], "afterok:102")
        self.assertEqual(Path(completed["jobs"][0]["receipt_path"]), first_receipt)

    def test_every_accepted_job_has_durable_receipt(self) -> None:
        gate = self._write_gate()
        args = self._resolved_args()
        self._install_retention()
        gate_sha = sha256_file(gate)
        with patch.object(
            self.base,
            "execute_sbatch",
            side_effect=["200", "201", "202", "203"],
        ):
            manifest = transactional_submit_finite_chain(
                base=self.base,
                args=args,
                launch_gate_path=gate,
                launch_gate_sha256=gate_sha,
            )

        self.assertEqual(manifest["accepted_job_count"], 4)
        for index, job in enumerate(manifest["jobs"]):
            receipt_path = Path(job["receipt_path"])
            self.assertTrue(receipt_path.is_file())
            receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
            self.assertEqual(receipt["sequence_index"], index)
            self.assertEqual(receipt["job_id"], job["job_id"])
            self.assertEqual(receipt["launch_fingerprint"], manifest["launch_fingerprint"])

    def test_retry_after_complete_chain_refuses_all_new_sbatch_calls(self) -> None:
        gate = self._write_gate()
        args = self._resolved_args()
        self._install_retention()
        gate_sha = sha256_file(gate)
        with patch.object(
            self.base,
            "execute_sbatch",
            side_effect=["300", "301", "302", "303"],
        ):
            transactional_submit_finite_chain(
                base=self.base,
                args=args,
                launch_gate_path=gate,
                launch_gate_sha256=gate_sha,
            )

        with patch.object(self.base, "execute_sbatch") as duplicate_mock:
            with self.assertRaisesRegex(ClpuN2SubmissionError, "already fully submitted"):
                transactional_submit_finite_chain(
                    base=self.base,
                    args=args,
                    launch_gate_path=gate,
                    launch_gate_sha256=gate_sha,
                )
        duplicate_mock.assert_not_called()

    def test_existing_submitted_iteration_without_matching_journal_blocks_launch(self) -> None:
        gate = self._write_gate()
        args = self._resolved_args()
        self._install_retention()
        (self.root / "optimization_state.json").write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "optimization_name": "clpu_n2_test",
                    "status": "running",
                    "updated_at": "2026-09-10T00:00:00Z",
                    "latest_iteration": 0,
                    "iterations": [
                        {
                            "iteration": 0,
                            "status": "submitted",
                            "submitted": True,
                            "slurm_job_ids": ["777"],
                            "submitted_case_ids": [0, 1, 2, 3, 4, 5, 6, 7],
                        }
                    ],
                }
            )
            + "\n",
            encoding="utf-8",
        )

        with patch.object(self.base, "execute_sbatch") as submit_mock:
            with self.assertRaisesRegex(ClpuN2SubmissionError, "already submitted"):
                transactional_submit_finite_chain(
                    base=self.base,
                    args=args,
                    launch_gate_path=gate,
                    launch_gate_sha256=sha256_file(gate),
                )
        submit_mock.assert_not_called()

    def test_changed_gate_after_partial_acceptance_cannot_start_second_overlapping_chain(self) -> None:
        gate = self._write_gate()
        args = self._resolved_args()
        self._install_retention()
        first_sha = sha256_file(gate)
        with patch.object(
            self.base,
            "execute_sbatch",
            side_effect=["400", self.base.MorboChainSubmitError("stop")],
        ):
            with self.assertRaises(self.base.MorboChainSubmitError):
                transactional_submit_finite_chain(
                    base=self.base,
                    args=args,
                    launch_gate_path=gate,
                    launch_gate_sha256=first_sha,
                )

        self._write_gate(evidence_revision="changed-after-partial")
        changed_sha = sha256_file(gate)
        self.assertNotEqual(first_sha, changed_sha)
        with patch.object(self.base, "execute_sbatch") as submit_mock:
            with self.assertRaisesRegex(ClpuN2SubmissionError, "overlap requested iterations"):
                transactional_submit_finite_chain(
                    base=self.base,
                    args=args,
                    launch_gate_path=gate,
                    launch_gate_sha256=changed_sha,
                )
        submit_mock.assert_not_called()


if __name__ == "__main__":
    unittest.main()
