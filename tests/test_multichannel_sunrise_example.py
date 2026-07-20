from __future__ import annotations

import json
import os
import unittest
from pathlib import Path
from unittest.mock import patch

from campaign_workflow.analysis.base import _expand_template
from campaign_workflow.core.case_materialization import (
    build_env_values,
    get_case_materialization_config,
)
from campaign_workflow.core.tsv_cases import CaseRecord


class MultichannelSunriseExampleTests(unittest.TestCase):
    def setUp(self) -> None:
        self.example = Path("examples/sunrise/multichannel")
        self.config = json.loads(
            (self.example / "campaign.json").read_text(encoding="utf-8")
        )
        self.optimization = json.loads(
            (self.example / "optimization.json").read_text(encoding="utf-8")
        )

    def test_campaign_materializes_all_multichannel_parameters(self) -> None:
        row = {
            "CASE_ID": "0",
            "CASE_NAME": "mc_i000_c000_reference",
            "PLASMA_ION_DENSITY_M3": "1e25",
            "CNT_RADIUS_UM": "2",
            "CNT_GAP_UM": "0.75",
            "LASER_WAIST_UM": "1",
            "PLASMA_LENGTH_UM": "100",
            "PLASMA_HALF_WIDTH_X_UM": "16",
            "PLASMA_HALF_WIDTH_Y_UM": "16",
            "FOCUS_OFFSET_FROM_PLASMA_START_UM": "5",
            "HONEYCOMB_ANGLE_DEG": "0",
            "POLARIZATION_ANGLE_DEG": "90",
            "ELLIPTICITY_ANGLE_DEG": "0",
            "WRITE_FIELD_DIAGNOSTIC": "1",
        }
        case = CaseRecord(case_id=0, case_name=row["CASE_NAME"], row=row)
        values, errors = build_env_values(
            case, get_case_materialization_config(self.config)
        )
        env = dict(values)

        self.assertEqual(errors, [])
        self.assertEqual(env["MC_CNT_RADIUS_M"], "2e-6")
        self.assertEqual(env["MC_CNT_GAP_M"], "7.5e-7")
        self.assertEqual(env["MC_PLASMA_LENGTH_M"], "1e-4")
        self.assertEqual(env["MC_POLARIZATION_ANGLE_DEG"], "90")
        self.assertEqual(env["MC_ELLIPTICITY_ANGLE_DEG"], "0")
        self.assertEqual(env["MC_PLASMA_ZMIN_M"], "4.0e-5")

    def test_input_and_runner_use_real_dry_run_and_final_only_diagnostics(self) -> None:
        input_text = (self.example / "input_template.py").read_text(encoding="utf-8")
        runner_text = (
            self.example / "run_warpx_multichannel_case_sunrise.sh"
        ).read_text(encoding="utf-8")

        self.assertIn('period=0', input_text)
        self.assertIn('warpx_dump_last_timestep=True', input_text)
        self.assertIn('species=[electrons]', input_text)
        self.assertIn('if resolved["dry_run"]:', input_text)
        self.assertIn('MC_DRY_RUN=1 python -u input.py', runner_text)
        self.assertIn('srun -n "${SLURM_NTASKS}" python -u input.py', runner_text)
        self.assertNotIn("CAP_DRY_RUN", runner_text)

    def test_analysis_command_can_address_shared_workflow_root(self) -> None:
        case = CaseRecord(case_id=0, case_name="case", row={})
        with patch.dict(os.environ, {"WORKFLOW_ROOT": "/shared/campaign-workflow"}):
            expanded = _expand_template(
                "{workflow_root}/examples/analyze.sh",
                campaign_root=Path("/campaign"),
                case_dir=Path("/campaign/case"),
                case=case,
            )
        self.assertEqual(expanded, "/shared/campaign-workflow/examples/analyze.sh")

    def test_cleanup_is_limited_to_explicit_hdf5_globs(self) -> None:
        globs = self.config["cleanup"]["raw_delete_globs"]
        self.assertEqual(
            globs,
            [
                "3D/*.h5",
                "3D/*.hdf5",
                "fields3D/*.h5",
                "fields3D/*.hdf5",
            ],
        )
        for glob in globs:
            self.assertNotIn("**", glob)

    def test_reduced_contract_requires_soft100_optimizer_metrics(self) -> None:
        particle_summary = next(
            output
            for output in self.config["analysis"]["outputs"]
            if output["name"] == "particle_summary"
        )
        required = particle_summary["required_columns"]

        for name in (
            "soft100_schema_version",
            "soft100_status",
            "charge_soft100_pC",
            "n_effective_soft100",
            "energy_p95_soft100_MeV",
            "energy_relative_spread_rms_soft100",
            "theta_r_p95_soft100_mrad",
            "emitn_xy_soft100_um_rad",
            "halo_fraction_soft100",
        ):
            self.assertIn(name, required)

        self.assertIn(
            "multichannel_honeycomb_3d_energy_soft_v2",
            self.optimization["campaign_preparation"]["campaign_name_template"],
        )

    def test_staged_pilot_is_registered_and_guards_use_runtime_schema(self) -> None:
        readme = (self.example / "README.md").read_text(encoding="utf-8")
        guards = self.optimization["guards"]
        policy = self.optimization["policy"]

        self.assertIn("campaign_size", guards)
        self.assertIn("quota", guards)
        self.assertNotIn("iteration_limits", guards)
        self.assertNotIn("storage_quota", guards)
        self.assertTrue(policy["require_all_materialized_cases_submitted"])
        self.assertIn("--allow-additional-cases", readme)
        self.assertIn("--case-runner", readme)
        self.assertIn("--confirm-cleanup-execute", readme)


if __name__ == "__main__":
    unittest.main()
