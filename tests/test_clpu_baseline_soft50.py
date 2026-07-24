from __future__ import annotations

import importlib.util
import math
import os
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = (
    ROOT
    / "examples"
    / "sunrise"
    / "corrected_capillary"
    / "baseline_input_template.py"
)


def load_template():
    spec = importlib.util.spec_from_file_location(
        "clpu_baseline_input_template",
        TEMPLATE,
    )
    if spec is None or spec.loader is None:
        raise ImportError(f"could not import {TEMPLATE}")
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
