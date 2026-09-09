from __future__ import annotations

import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from campaign_workflow.cli.init_case_states import main as init_case_states_main
from campaign_workflow.core.atomic_io import read_json


class CaseCycleRuntimeIntegrationTests(unittest.TestCase):
    SITES = (
        ("sunrise", Path("examples/sunrise/submit_case_cycle_array.sh")),
        ("lynx", Path("examples/lynx/submit_case_cycle_array_lynx.sh")),
    )

    def setUp(self) -> None:
        self.tmpdir = tempfile.TemporaryDirectory()
        self.tmp = Path(self.tmpdir.name)
        self.repo = Path.cwd().resolve()

    def tearDown(self) -> None:
        self.tmpdir.cleanup()

    def _make_campaign(self, site: str, *, runner_rc: int) -> tuple[Path, Path]:
        root = self.tmp / f"campaign_{site}_{runner_rc}"
        root.mkdir(parents=True)
        campaign = {
            "schema_version": 1,
            "campaign_name": f"fake_{site}_{runner_rc}",
            "case_manifest": "cases.tsv",
            "case_manifest_format": "tsv",
            "case_id_column": "CASE_ID",
            "case_name_column": "CASE_NAME",
            "simulation": {
                "backend": "fake",
                "scheduler": "slurm",
                "input_script": "input.py",
                "completion_marker": "post/sim_done.json",
                "failure_marker": "post/sim_failed.json",
            },
            "raw_diagnostics": [
                {
                    "name": "fake_raw",
                    "kind": "fake",
                    "path": "diags",
                    "glob": "diags/**/*.fake",
                    "min_files": 1,
                    "min_age_seconds": 0,
                    "required": True,
                }
            ],
            "analysis": {
                "name": "fake_analysis",
                "kind": "fake",
                "adapter": "fake",
                "inputs": ["fake_raw"],
                "outputs": [
                    {
                        "name": "fake_metrics",
                        "kind": "csv",
                        "path": "post/fake_metrics.csv",
                        "min_rows": 1,
                        "required_columns": ["iteration", "score"],
                        "required": True,
                    }
                ],
            },
            "cleanup": {
                "raw_delete_globs": ["diags/**/*.fake"],
                "require_raw_validated": True,
                "require_reduced_validated": True,
                "require_delete_manifest": True,
                "allow_directory_delete": False,
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
        (root / "campaign.json").write_text(json.dumps(campaign, indent=2) + "\n", encoding="utf-8")
        (root / "cases.tsv").write_text(
            "CASE_ID\tCASE_NAME\tKIND\n0\t000_fake_case\talpha\n",
            encoding="utf-8",
        )

        rc = init_case_states_main(
            [
                "--campaign-root",
                str(root),
                "--create-missing-case-dirs",
            ]
        )
        self.assertEqual(rc, 0)

        runner = self.tmp / f"runner_{site}_{runner_rc}.sh"
        runner.write_text(
            "#!/usr/bin/env bash\n"
            "set +e\n"
            "case_dir=\"$1\"\n"
            "mkdir -p \"${case_dir}/diags\"\n"
            "printf partial-or-complete > \"${case_dir}/diags/raw_000.fake\"\n"
            f"exit {runner_rc}\n",
            encoding="utf-8",
        )
        runner.chmod(0o755)
        return root, runner

    def _run_cycle(self, site: str, script: Path, *, runner_rc: int) -> tuple[subprocess.CompletedProcess[str], Path]:
        root, runner = self._make_campaign(site, runner_rc=runner_rc)
        workflow_env = self.tmp / f"workflow_env_{site}_{runner_rc}.sh"
        workflow_env.write_text(
            f"export PYTHONPATH={str(self.repo)!r}:${{PYTHONPATH:-}}\n",
            encoding="utf-8",
        )

        env = os.environ.copy()
        env.update(
            {
                "CAMPAIGN_ROOT": str(root),
                "WORKFLOW_ROOT": str(self.repo),
                "WORKFLOW_ENV": str(workflow_env),
                "CASE_RUNNER": str(runner),
                "SLURM_JOB_ID": "4242",
                "SLURM_ARRAY_JOB_ID": "4242",
                "SLURM_ARRAY_TASK_ID": "0",
                "SLURM_JOB_NAME": f"test_{site}",
                "SLURM_JOB_PARTITION": "novas" if site == "lynx" else "T6H",
                "EXPECTED_SLURM_PARTITION": "novas",
                "WARPX_LYNX_MODULE": "fake-warpx-module",
                "CONFIRM_CLEANUP_EXECUTE": "0",
            }
        )
        result = subprocess.run(
            ["bash", str(script.resolve())],
            cwd=root,
            env=env,
            check=False,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        return result, root

    def test_failed_runner_with_residual_raw_stops_before_sim_done_analysis_and_cleanup(self) -> None:
        for site, script in self.SITES:
            with self.subTest(site=site):
                result, root = self._run_cycle(site, script, runner_rc=37)
                case_dir = root / "000_fake_case"

                self.assertEqual(result.returncode, 37, result.stdout + result.stderr)
                self.assertTrue((case_dir / "diags/raw_000.fake").is_file())
                self.assertTrue((case_dir / "post/sim_failed.json").is_file())
                self.assertFalse((case_dir / "post/sim_done.json").exists())
                self.assertFalse((case_dir / "post/analysis_done.json").exists())
                self.assertFalse((case_dir / "post/raw_delete_eligible.json").exists())
                self.assertFalse((case_dir / "post/raw_deleted.json").exists())
                self.assertFalse((case_dir / "manifests/raw_delete_manifest.json").exists())
                self.assertEqual(read_json(case_dir / "state.json")["state"], "Failed")
                self.assertIn("return_code=37", result.stdout + result.stderr)

    def test_zero_runner_allows_normal_route_and_runtime_receipt(self) -> None:
        for site, script in self.SITES:
            with self.subTest(site=site):
                result, root = self._run_cycle(site, script, runner_rc=0)
                case_dir = root / "000_fake_case"

                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                sim_done = read_json(case_dir / "post/sim_done.json")
                self.assertEqual(sim_done["evidence_mode"], "runtime_success_receipt")
                self.assertEqual(sim_done["return_code"], 0)
                self.assertEqual(sim_done["runtime_receipt"]["scheduler_job_id"], "4242")
                self.assertTrue((case_dir / "post/analysis_done.json").is_file())
                self.assertTrue((case_dir / "post/raw_delete_eligible.json").is_file())
                self.assertTrue((case_dir / "manifests/raw_delete_manifest.json").is_file())
                self.assertFalse((case_dir / "post/raw_deleted.json").exists())
                self.assertEqual(read_json(case_dir / "state.json")["state"], "Raw_delete_eligible")


if __name__ == "__main__":
    unittest.main()
