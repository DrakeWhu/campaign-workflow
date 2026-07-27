from __future__ import annotations

import importlib.util
import json
import math
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
EXAMPLE = (
    ROOT
    / "examples"
    / "sunrise"
    / "corrected_capillary"
)
TEMPLATE = EXAMPLE / "baseline_input_template.py"
CAMPAIGN = EXAMPLE / "campaign_baseline_soft50.json"


def load_template(path: Path = TEMPLATE):
    spec = importlib.util.spec_from_file_location(
        "clpu_baseline_input_template",
        path,
    )
    if spec is None or spec.loader is None:
        raise ImportError(f"could not import {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class ClpuBaselineSoft50Test(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.module = load_template()

    def base_env(self) -> dict[str, str]:
        return {
            "CAP_REQUIRE_CONVENTION_ACK": "true",
            "CAP_INPUT_CONVENTIONS_ACK": (
                "clpu_spot_diameter_and_30fs_intensity_fwhm_v1"
            ),
            "CAP_LASER_SPOT_DEFINITION": "diameter_1e2_intensity",
            "CAP_NITROGEN_DOPANT_FRACTION": "0",
            "CAP_LASER_CASE": "f32",
            "CAP_N0_CM3": "5.5e18",
            "CAP_RADIUS_M": "225e-6",
            "CAP_RMAX_M": "270e-6",
            "CAP_NR": "192",
            "CAP_PLATEAU_LENGTH_M": "7e-3",
            "CAP_LONG_PROFILE": "both",
            "CAP_RAMP_LENGTH_M": "5e-3",
            "CAP_LASER_INTENSITY_FWHM_S": "30e-15",
        }

    def test_single_species_soft50_curve_contract(self) -> None:
        campaign = json.loads(CAMPAIGN.read_text(encoding="utf-8"))
        outputs = {
            output["name"]: output
            for output in campaign["analysis"]["outputs"]
        }

        for name in (
            "particle_plateau_soft50_curves",
            "particle_capillary_soft50_curves",
        ):
            self.assertEqual(outputs[name]["min_rows"], 2)
            self.assertEqual(
                outputs[name]["required_columns"],
                [
                    "species_scope",
                    "soft50_energy_low_MeV",
                    "charge_soft50_pC",
                    "n_effective_soft50",
                    "energy_p90_soft50_MeV",
                    "energy_relative_spread_rms_soft50",
                ],
            )

    def test_campaign_uses_prepared_input_template_name(self) -> None:
        campaign = json.loads(CAMPAIGN.read_text(encoding="utf-8"))
        self.assertEqual(
            campaign["case_materialization"]["input_template"],
            "input_template.py",
        )
        self.assertEqual(
            campaign["case_materialization"]["input_name"],
            "input.py",
        )

    def test_materialized_input_resolves_base_via_workflow_root(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_name:
            case_input = Path(tmp_name) / "input.py"
            shutil.copyfile(TEMPLATE, case_input)
            env = {
                **self.base_env(),
                "WFLOW_SRC": str(ROOT),
            }
            with patch.dict(os.environ, env, clear=True):
                module = load_template(case_input)
                resolved = module.resolve_parameters()

        self.assertEqual(
            resolved["physics_model_id"],
            "clpu_carlos_plateau_quasiparabolic_hydrogen_baseline_soft50_dual_exit_v1",
        )
        self.assertEqual(
            module.BASE.__file__,
            str((TEMPLATE.parent / "input_template.py").resolve()),
        )

    def test_rejects_nonzero_nitrogen(self) -> None:
        env = self.base_env()
        env["CAP_NITROGEN_DOPANT_FRACTION"] = "0.005"

        with self.assertRaisesRegex(
            ValueError,
            "requires CAP_NITROGEN_DOPANT_FRACTION=0",
        ):
            self.module.resolve_parameters(env)

    def test_resolves_hydrogen_only_dual_exit(self) -> None:
        resolved = self.module.resolve_parameters(self.base_env())
        targets = resolved["particle_diagnostic_targets"]
        plateau = targets["plateau_exit"]
        capillary = targets["capillary_exit"]
        step_distance = resolved["moving_window_step_distance_m"]

        self.assertEqual(
            resolved["ionization_model"],
            "disabled_hydrogen_baseline",
        )
        self.assertEqual(
            resolved["electron_species_provenance"],
            {
                "combined_metric_scope": (
                    "preionized_background_electrons"
                ),
                "initial_free_electrons": (
                    "preionized_background_electrons"
                ),
                "ionization_products": None,
            },
        )
        self.assertEqual(
            resolved["particle_diagnostic_primary_target"],
            "plateau_exit",
        )
        self.assertLess(plateau["iteration"], capillary["iteration"])
        self.assertLessEqual(capillary["iteration"], resolved["max_steps"])

        for target in (plateau, capillary):
            self.assertGreaterEqual(target["distance_error_m"], 0.0)
            self.assertLess(target["distance_error_m"], step_distance)

        expected_intervals = (
            f"{plateau['iteration']}:{plateau['iteration']},"
            f"{capillary['iteration']}:{capillary['iteration']}"
        )
        self.assertEqual(
            resolved["particle_diagnostic_intervals"],
            expected_intervals,
        )

    def test_default_density_profile_remains_corrected(self) -> None:
        resolved = self.module.resolve_parameters(self.base_env())

        self.assertEqual(
            resolved["channel_profile_longitudinal_scope"],
            "plateau_only",
        )
        self.assertEqual(
            resolved["ramp_radial_model"],
            "uniform_inside_capillary",
        )
        expected = (
            resolved["n0_m3"]
            * resolved["channel_profile_quadratic_coefficient"]
            / resolved["radius_m"] ** 2
        )
        self.assertTrue(
            math.isclose(
                resolved["channel_quadratic_density_coefficient_m5"],
                expected,
                rel_tol=1.0e-12,
            )
        )


if __name__ == "__main__":
    unittest.main()
