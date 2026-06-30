from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from campaign_workflow.propose_next_iteration import (
    ProposeNextIterationError,
    build_propose_next_iteration_plan,
    init_next_case_states,
    materialize_next_campaign,
    prepare_next_campaign_from_optimizer_outputs,
)


class ProposeNextIterationPrepareCampaignTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmpdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tmpdir.name) / "clpu_capillary_guiding_bo_001"
        self.template_root = self.root / "iterations" / "iter_000"
        self.template_root.mkdir(parents=True)
        (self.root / "optimizer_runs" / "iter_001" / "outputs").mkdir(parents=True)

        self._write_template_campaign()
        self._write_optimizer_outputs()
        self._write_optimization_config()

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

    def test_prepare_next_campaign_creates_campaign_root_only(self) -> None:
        plan = self._build_plan()

        result = prepare_next_campaign_from_optimizer_outputs(plan)

        self.assertTrue(result["ok"])
        iter_001 = self.root / "iterations" / "iter_001"
        self.assertTrue((iter_001 / "campaign.json").is_file())
        self.assertTrue((iter_001 / "cases.tsv").is_file())
        self.assertTrue((iter_001 / "input_template.py").is_file())
        self.assertTrue((iter_001 / "array_logs").is_dir())
        self.assertTrue((iter_001 / "optimizer_batch_provenance.json").is_file())

        campaign_json = json.loads(
            (iter_001 / "campaign.json").read_text(encoding="utf-8")
        )
        self.assertEqual(
            campaign_json["campaign_name"],
            "clpu_capillary_guiding_bo_001_iter_001",
        )

        self.assertFalse((iter_001 / "000_case").exists())
        self.assertFalse((iter_001 / "001_case").exists())

    def test_materialize_and_init_next_campaign_create_case_files_and_states(
        self,
    ) -> None:
        plan = self._build_plan()

        prepare_next_campaign_from_optimizer_outputs(plan)
        materialized = materialize_next_campaign(plan)
        initialized = init_next_case_states(plan)

        self.assertTrue(materialized["ok"])
        self.assertEqual(materialized["cases_processed"], 2)
        self.assertEqual(materialized["inputs_written"], 2)
        self.assertEqual(materialized["envs_written"], 2)

        self.assertTrue(initialized["ok"])
        self.assertEqual(initialized["cases_processed"], 2)

        iter_001 = self.root / "iterations" / "iter_001"
        for case_name in ("000_case", "001_case"):
            case_dir = iter_001 / case_name
            self.assertTrue((case_dir / "input.py").is_file())
            self.assertTrue((case_dir / "case.env").is_file())
            self.assertTrue((case_dir / "state.json").is_file())
            self.assertTrue((case_dir / "validation.json").is_file())
            self.assertTrue((case_dir / "logs").is_dir())
            self.assertTrue((case_dir / "post").is_dir())
            self.assertTrue((case_dir / "manifests").is_dir())
            self.assertTrue((case_dir / "locks").is_dir())

        state = json.loads(
            (iter_001 / "000_case" / "state.json").read_text(encoding="utf-8")
        )
        self.assertEqual(state["state"], "Created")
        self.assertEqual(state["case_id"], 0)
        self.assertEqual(state["case_name"], "000_case")

    def test_prepare_fails_if_output_campaign_root_already_exists(self) -> None:
        plan = self._build_plan()
        plan.output_campaign_root.mkdir(parents=True)

        with self.assertRaisesRegex(
            ProposeNextIterationError,
            "failed to prepare next campaign root",
        ):
            prepare_next_campaign_from_optimizer_outputs(plan)

    def test_materialize_requires_prepared_campaign_root(self) -> None:
        plan = self._build_plan()

        with self.assertRaisesRegex(
            ProposeNextIterationError,
            "campaign root does not exist",
        ):
            materialize_next_campaign(plan)

    def _build_plan(self):
        return build_propose_next_iteration_plan(
            tick_summary=self.tick_summary,
            state_doc=self.state_doc,
            optimization_root=self.root,
            from_iteration=0,
            next_iteration=1,
        )

    def _write_template_campaign(self) -> None:
        campaign = {
            "schema_version": 1,
            "campaign_name": "clpu_capillary_guiding_bo_001_iter_000",
            "case_manifest": "cases.tsv",
            "case_manifest_format": "tsv",
            "case_id_column": "CASE_ID",
            "case_name_column": "CASE_NAME",
            "case_materialization": {
                "input_template": "input_template.py",
                "input_name": "input.py",
                "env_name": "case.env",
                "env_columns": [
                    {
                        "column": "CASE_ID",
                        "env": "CAP_CASE_ID",
                        "required": True,
                    },
                    {
                        "column": "CASE_NAME",
                        "env": "CAP_CASE_NAME",
                        "required": True,
                    },
                    {
                        "column": "LASER_CASE",
                        "env": "CAP_LASER_CASE",
                        "required": True,
                    },
                    {
                        "column": "PLASMA_KIND",
                        "env": "CAP_PLASMA_KIND",
                        "required": True,
                    },
                    {
                        "column": "N0_CM3",
                        "env": "CAP_N0_CM3",
                        "required": True,
                    },
                    {
                        "column": "PLATEAU_LENGTH_MM",
                        "env": "CAP_PLATEAU_LENGTH_M",
                        "required": True,
                        "scale": "1e-3",
                    },
                    {
                        "column": "RADIUS_UM",
                        "env": "CAP_RADIUS_M",
                        "required": False,
                        "scale": "1e-6",
                    },
                    {
                        "column": "FOCUS_OFFSET_FROM_PLATEAU_START_MM",
                        "env": "CAP_FOCUS_OFFSET_FROM_PLATEAU_START_MM",
                        "required": True,
                    },
                    {
                        "column": "CAP_RMAX_UM",
                        "env": "CAP_RMAX_M",
                        "required": True,
                        "scale": "1e-6",
                    },
                    {
                        "column": "CAP_NR",
                        "env": "CAP_NR",
                        "required": True,
                    },
                ],
                "env_constants": {
                    "CAP_DIAG_PRESET": "guiding_rhoe",
                    "CAP_LONG_PROFILE": "both",
                },
            },
            "simulation": {
                "backend": "warpx_picmi",
                "scheduler": "slurm",
                "input_script": "input.py",
                "completion_marker": "post/sim_done.json",
                "failure_marker": "post/sim_failed.json",
            },
            "analysis": {
                "outputs": [
                    {
                        "name": "guiding_metrics",
                        "kind": "csv",
                        "path": "guiding_metrics.csv",
                        "required": True,
                    }
                ]
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
        (self.template_root / "campaign.json").write_text(
            json.dumps(campaign, indent=2) + "\n",
            encoding="utf-8",
        )
        (self.template_root / "cases.tsv").write_text(
            "CASE_ID\tCASE_NAME\n0\told_case\n",
            encoding="utf-8",
        )
        (self.template_root / "input_template.py").write_text(
            "# template copied without physics edits\n",
            encoding="utf-8",
        )
        (self.template_root / "array_logs").mkdir()

    def _write_optimizer_outputs(self) -> None:
        outputs = self.root / "optimizer_runs" / "iter_001" / "outputs"
        candidate_header = [
            "CASE_ID",
            "CASE_NAME",
            "LASER_CASE",
            "PLASMA_KIND",
            "N0_CM3",
            "PLATEAU_LENGTH_MM",
            "DIAMETER_UM",
            "RADIUS_UM",
            "FOCUS_OFFSET_FROM_PLATEAU_START_MM",
            "CAP_RMAX_UM",
            "CAP_NR",
            "OPT_ITERATION",
            "OPT_CANDIDATE_ID",
        ]
        rows = [
            [
                "0",
                "000_case",
                "f20",
                "chan",
                "4e18",
                "25",
                "500",
                "250",
                "5",
                "300",
                "192",
                "1",
                "opt_001_000",
            ],
            [
                "1",
                "001_case",
                "f32",
                "chan",
                "3.5e18",
                "20",
                "400",
                "200",
                "0",
                "240",
                "192",
                "1",
                "opt_001_001",
            ],
        ]
        text = "\t".join(candidate_header) + "\n"
        text += "\n".join("\t".join(row) for row in rows) + "\n"
        (outputs / "candidate_batch.tsv").write_text(text, encoding="utf-8")

        batch_plan = {
            "schema_version": 1,
            "plan_type": "optimizer_candidate_batch",
            "optimizer_iteration": 1,
            "objective_config_id": "capillary_objectives_v1",
            "campaign_template": {
                "campaign_json": "campaign.json",
                "input_template": "input_template.py",
            },
            "source_campaigns": [
                {
                    "campaign_root": str(self.template_root),
                    "campaign_json": "campaign.json",
                    "cases_tsv": "cases.tsv",
                }
            ],
        }
        (outputs / "batch_campaign_plan.json").write_text(
            json.dumps(batch_plan, indent=2) + "\n",
            encoding="utf-8",
        )

    def _write_optimization_config(self) -> None:
        config = {
            "schema_version": 1,
            "optimization_name": "clpu_capillary_guiding_bo_001",
            "optimizer": {
                "working_directory": ".",
                "command": [
                    "python",
                    "-m",
                    "campaign_optimizer.cli.run_iteration",
                    "--config",
                    "{optimizer_config}",
                    "--iteration",
                    "{next_iteration}",
                    "--build-candidate-batch",
                ],
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
