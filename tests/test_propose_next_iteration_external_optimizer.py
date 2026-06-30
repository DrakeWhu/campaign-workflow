from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

from campaign_workflow.propose_next_iteration import (
    ProposeNextIterationError,
    build_propose_next_iteration_plan,
    run_external_optimizer_command,
    verify_optimizer_outputs,
)


class ProposeNextIterationExternalOptimizerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmpdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tmpdir.name) / "clpu_capillary_guiding_bo_001"
        (self.root / "iterations" / "iter_000").mkdir(parents=True)
        self.fake_optimizer = self.root / "fake_optimizer.py"
        self.state_doc = {
            "schema_version": 1,
            "optimization_name": "clpu_capillary_guiding_bo_001",
            "status": "running",
            "latest_iteration": 0,
            "iterations": [
                {
                    "iteration": 0,
                    "campaign_root": "iterations/iter_000",
                    "status": "reduced_ready",
                    "recommended_action": "close_iteration",
                    "submitted_case_count": 10,
                    "n_submitted_reduced_valid": 10,
                    "n_submitted_sim_failed": 0,
                }
            ],
        }
        self.tick_summary = {
            "pause_file_exists": False,
            "iterations": [
                {
                    "iteration": 0,
                    "campaign_root": "iterations/iter_000",
                    "status": "reduced_ready",
                    "recommended_action": "close_iteration",
                }
            ],
        }

    def tearDown(self) -> None:
        self.tmpdir.cleanup()

    def test_execute_external_optimizer_once_and_verify_outputs(self) -> None:
        self._write_fake_optimizer_success()
        self._write_optimization_config(
            [
                sys.executable,
                str(self.fake_optimizer),
                "{optimizer_run_dir}",
                "{next_iteration}",
            ]
        )
        plan = self._build_plan()

        result = run_external_optimizer_command(plan)
        self.assertEqual(result.return_code, 0)
        self.assertIn("fake optimizer done", result.stdout)

        verification = verify_optimizer_outputs(plan)
        self.assertTrue(verification["ok"])
        self.assertEqual(
            Path(verification["candidate_batch"]["path"]).parts[-4:],
            ("optimizer_runs", "iter_001", "outputs", "candidate_batch.tsv"),
        )
        self.assertEqual(
            verification["batch_campaign_plan"]["schema_version"],
            1,
        )
        self.assertEqual(
            verification["batch_campaign_plan"]["plan_type"],
            "optimizer_candidate_batch",
        )
        self.assertEqual(
            verification["batch_campaign_plan"]["optimizer_iteration"],
            1,
        )

    def test_external_optimizer_failure_raises_clear_error(self) -> None:
        self.fake_optimizer.write_text(
            "import sys\n"
            "print('optimizer stdout before failure')\n"
            "print('optimizer stderr failure', file=sys.stderr)\n"
            "raise SystemExit(17)\n",
            encoding="utf-8",
        )
        self._write_optimization_config(
            [sys.executable, str(self.fake_optimizer), "{optimizer_run_dir}"]
        )
        plan = self._build_plan()

        with self.assertRaisesRegex(
            ProposeNextIterationError,
            "external optimizer command failed",
        ):
            run_external_optimizer_command(plan)

        self.assertFalse(plan.candidate_batch.exists())
        self.assertFalse(plan.batch_campaign_plan.exists())

    def test_verify_outputs_fails_if_candidate_batch_missing(self) -> None:
        self._write_optimization_config([sys.executable, "-c", "print('noop')"])
        plan = self._build_plan()
        plan.batch_campaign_plan.parent.mkdir(parents=True)
        plan.batch_campaign_plan.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "plan_type": "optimizer_candidate_batch",
                    "optimizer_iteration": 1,
                }
            )
            + "\n",
            encoding="utf-8",
        )

        with self.assertRaisesRegex(
            ProposeNextIterationError,
            "missing optimizer output candidate_batch.tsv",
        ):
            verify_optimizer_outputs(plan)

    def test_verify_outputs_fails_if_batch_campaign_plan_missing(self) -> None:
        self._write_optimization_config([sys.executable, "-c", "print('noop')"])
        plan = self._build_plan()
        plan.candidate_batch.parent.mkdir(parents=True)
        plan.candidate_batch.write_text(
            "CASE_ID\tCASE_NAME\n0\t000_case\n",
            encoding="utf-8",
        )

        with self.assertRaisesRegex(
            ProposeNextIterationError,
            "missing optimizer output batch_campaign_plan.json",
        ):
            verify_optimizer_outputs(plan)

    def test_verify_outputs_fails_if_batch_campaign_plan_is_not_json_object(
        self,
    ) -> None:
        self._write_optimization_config([sys.executable, "-c", "print('noop')"])
        plan = self._build_plan()
        plan.candidate_batch.parent.mkdir(parents=True)
        plan.candidate_batch.write_text(
            "CASE_ID\tCASE_NAME\n0\t000_case\n",
            encoding="utf-8",
        )
        plan.batch_campaign_plan.write_text("[1, 2, 3]\n", encoding="utf-8")

        with self.assertRaisesRegex(
            ProposeNextIterationError,
            "JSON file does not contain an object",
        ):
            verify_optimizer_outputs(plan)

    def test_external_optimizer_requires_existing_working_directory(self) -> None:
        self._write_optimization_config(
            [sys.executable, "-c", "print('noop')"],
            working_directory="does_not_exist",
        )
        plan = self._build_plan()

        with self.assertRaisesRegex(
            ProposeNextIterationError,
            "working_directory does not exist",
        ):
            run_external_optimizer_command(plan)

    def _build_plan(self):
        return build_propose_next_iteration_plan(
            tick_summary=self.tick_summary,
            state_doc=self.state_doc,
            optimization_root=self.root,
            from_iteration=0,
            next_iteration=1,
        )

    def _write_fake_optimizer_success(self) -> None:
        self.fake_optimizer.write_text(
            "import json\n"
            "import sys\n"
            "from pathlib import Path\n"
            "run_dir = Path(sys.argv[1])\n"
            "iteration = int(sys.argv[2])\n"
            "outputs = run_dir / 'outputs'\n"
            "outputs.mkdir(parents=True, exist_ok=True)\n"
            "(outputs / 'candidate_batch.tsv').write_text(\n"
            "    'CASE_ID\\tCASE_NAME\\tLASER_CASE\\tPLASMA_KIND\\tN0_CM3\\tPLATEAU_LENGTH_MM\\tDIAMETER_UM\\tRADIUS_UM\\tFOCUS_OFFSET_FROM_PLATEAU_START_MM\\tCAP_RMAX_UM\\tCAP_NR\\n'\n"
            "    '0\\t000_case\\tf20\\tchan\\t4e18\\t25\\t500\\t250\\t5\\t300\\t192\\n',\n"
            "    encoding='utf-8',\n"
            ")\n"
            "(outputs / 'batch_campaign_plan.json').write_text(\n"
            "    json.dumps({\n"
            "        'schema_version': 1,\n"
            "        'plan_type': 'optimizer_candidate_batch',\n"
            "        'optimizer_iteration': iteration,\n"
            "        'campaign_template': {\n"
            "            'campaign_json': 'campaign.json',\n"
            "            'input_template': 'input_template.py',\n"
            "        },\n"
            "    }, indent=2) + '\\n',\n"
            "    encoding='utf-8',\n"
            ")\n"
            "print('fake optimizer done')\n",
            encoding="utf-8",
        )

    def _write_optimization_config(
        self,
        command: list[str],
        *,
        working_directory: str = ".",
    ) -> None:
        config = {
            "schema_version": 1,
            "optimization_name": "clpu_capillary_guiding_bo_001",
            "optimizer": {
                "working_directory": working_directory,
                "command": command,
                "optimizer_config": "optimizer.json",
            },
            "campaign_preparation": {
                "template_campaign_root": "iterations/iter_000",
                "iterations_dir": "iterations",
                "optimizer_runs_dir": "optimizer_runs",
                "materialize_after_prepare": True,
                "init_case_states_after_materialize": True,
            },
            "policy": {
                "max_iterations": 20,
                "max_total_submitted_cases": 200,
                "min_reduced_valid_to_continue": 5,
                "min_valid_fraction_to_continue": 0.6,
                "max_failed_fraction_to_continue": 0.4,
            },
        }
        self.root.mkdir(parents=True, exist_ok=True)
        (self.root / "optimization.json").write_text(
            json.dumps(config, indent=2) + "\n",
            encoding="utf-8",
        )


if __name__ == "__main__":
    unittest.main()
