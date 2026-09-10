from __future__ import annotations

import importlib.util
import json
import math
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
EXAMPLE = ROOT / "examples" / "sunrise" / "corrected_capillary"
BASELINE = EXAMPLE / "baseline_input_template.py"
NITROGEN = EXAMPLE / "nitrogen_input_template.py"


def load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"could not import {path}")
    module = importlib.util.module_from_spec(spec)
    with patch.dict(os.environ, {}, clear=False):
        spec.loader.exec_module(module)
    return module


class ClpuNitrogenProfileTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.baseline = load_module(BASELINE, "clpu_baseline_for_nitrogen_test")
        cls.nitrogen = load_module(NITROGEN, "clpu_nitrogen_profile_test")

    def base_env(self, fraction: str = "0") -> dict[str, str]:
        return {
            "CAP_CASE_ID": "0",
            "CAP_CASE_NAME": "000_contract",
            "CAP_REQUIRE_CONVENTION_ACK": "true",
            "CAP_INPUT_CONVENTIONS_ACK": (
                "clpu_document_spot_values_are_picmi_w0_"
                "and_30fs_intensity_fwhm_v2"
            ),
            "CAP_LASER_SPOT_DEFINITION": "picmi_waist_w0_1e2_intensity",
            "CAP_NITROGEN_DOPANT_FRACTION": fraction,
            "CAP_MAX_NITROGEN_DOPANT_FRACTION": "0.01",
            "CAP_LASER_CASE": "f32",
            "CAP_N0_CM3": "4e18",
            "CAP_RADIUS_M": "150e-6",
            "CAP_RMAX_M": "180e-6",
            "CAP_NR": "192",
            "CAP_PLATEAU_LENGTH_M": "5e-3",
            "CAP_LONG_PROFILE": "both",
            "CAP_RAMP_LENGTH_M": "5e-3",
            "CAP_LASER_INTENSITY_FWHM_S": "30e-15",
        }

    def test_zero_fraction_preserves_historical_baseline_physics(self) -> None:
        env = self.base_env("0")
        historical = self.baseline.resolve_parameters(env)
        new = self.nitrogen.resolve_parameters(env)

        invariant_keys = (
            "laser_case",
            "plasma_kind",
            "laser_spot_definition",
            "laser_spot_document_value_m",
            "laser_waist_radius_m",
            "laser_a0_reference_30fs",
            "laser_a0_constant_energy_scaled",
            "laser_a0",
            "laser_intensity_fwhm_s",
            "laser_picmi_duration_s",
            "laser_wavelength_m",
            "laser_antenna_z_m",
            "laser_profile_t_peak_s",
            "n0_cm3",
            "n0_m3",
            "radius_m",
            "diameter_m",
            "channel_profile_model",
            "channel_profile_longitudinal_scope",
            "ramp_radial_model",
            "channel_profile_quadratic_coefficient",
            "channel_profile_quartic_coefficient",
            "channel_quadratic_density_coefficient_m5",
            "n_edge_m3",
            "density_expression",
            "long_profile",
            "plateau_length_m",
            "plasma_start_z",
            "plateau_start_z",
            "plateau_end_z",
            "plasma_end_z",
            "front_ramp_length",
            "back_ramp_length",
            "particle_load_zmin_m",
            "focus_offset_from_plateau_start_mm",
            "focus_z_m",
            "grid",
            "max_steps",
            "max_steps_grid_cfl_derived",
            "steps_per_5mm_grid_cfl_ceil",
            "time_step_model",
            "cfl",
            "time_step_s",
            "moving_window_velocity_m_s",
            "moving_window_step_distance_m",
            "field_diagnostic_period",
            "target_field_frames",
            "particle_diagnostic_policy",
            "particle_diagnostic_primary_target",
            "particle_diagnostic_targets",
            "particle_diagnostic_intervals",
            "particle_diagnostic_min_energy_MeV",
            "particle_diagnostic_forward_only",
            "particle_diagnostic_filter_expression",
            "particle_diagnostic_dump_last_timestep",
            "electron_temperature_eV",
            "electron_thermal_speed_m_s",
            "macroparticles_per_cell_r_theta_z",
        )
        for key in invariant_keys:
            with self.subTest(key=key):
                self.assertEqual(new[key], historical[key])

        self.assertEqual(new["laser_waist_radius_m"], 42.0e-6)
        self.assertEqual(
            new["particle_diagnostic_intervals"],
            historical["particle_diagnostic_intervals"],
        )

    def test_zero_fraction_keeps_nitrogen_profile_schema_without_enabling_adk(self) -> None:
        resolved = self.nitrogen.resolve_parameters(self.base_env("0"))
        mixture = resolved[
            "mixture_density_factors_relative_to_initial_electron_density"
        ]

        self.assertEqual(resolved["schema_version"], 6)
        self.assertEqual(
            resolved["physics_model_id"],
            "clpu_carlos_plateau_quasiparabolic_h_n5_adk_uniform_"
            "soft50_dual_exit_picmi_w0_v1",
        )
        self.assertEqual(resolved["nitrogen_fraction_atomic_nuclei"], 0.0)
        self.assertEqual(resolved["ionization_model"], "disabled_zero_fraction")
        self.assertEqual(resolved["nitrogen_initial_charge_state"], 5)
        self.assertEqual(resolved["nitrogen_profile"]["longitudinal_shape"], "uniform")
        self.assertEqual(
            resolved["nitrogen_profile"]["profile_id"],
            "uniform_nitrogen_fraction_v1",
        )
        self.assertEqual(mixture["background_electron"], 1.0)
        self.assertEqual(mixture["hydrogen_ion"], 1.0)
        self.assertEqual(mixture["nitrogen_ion"], 0.0)
        self.assertEqual(mixture["initial_charge_balance"], 1.0)
        self.assertEqual(mixture["maximum_extra_electron"], 0.0)
        self.assertEqual(
            resolved["electron_species_provenance"]["combined_metric_scope"],
            "all_electrons",
        )

    def test_positive_fraction_uses_charge_neutral_h_n5_mixture_and_adk(self) -> None:
        fraction = 0.005
        resolved = self.nitrogen.resolve_parameters(self.base_env(str(fraction)))
        mixture = resolved[
            "mixture_density_factors_relative_to_initial_electron_density"
        ]
        nuclei_per_initial_electron = 1.0 / (1.0 + 4.0 * fraction)
        expected_h = (1.0 - fraction) * nuclei_per_initial_electron
        expected_n = fraction * nuclei_per_initial_electron

        self.assertEqual(resolved["ionization_model"], "ADK")
        self.assertTrue(
            math.isclose(mixture["hydrogen_ion"], expected_h, rel_tol=1e-14)
        )
        self.assertTrue(
            math.isclose(mixture["nitrogen_ion"], expected_n, rel_tol=1e-14)
        )
        self.assertTrue(
            math.isclose(
                mixture["initial_charge_balance"],
                expected_h + 5.0 * expected_n,
                rel_tol=0.0,
                abs_tol=1e-14,
            )
        )
        self.assertTrue(
            math.isclose(
                mixture["initial_charge_balance"],
                1.0,
                rel_tol=0.0,
                abs_tol=1e-14,
            )
        )
        self.assertTrue(
            math.isclose(
                mixture["maximum_extra_electron"],
                2.0 * expected_n,
                rel_tol=1e-14,
            )
        )

    def test_upper_bound_0p01_is_accepted_without_truncation(self) -> None:
        fraction = 0.01
        resolved = self.nitrogen.resolve_parameters(self.base_env("0.01"))
        mixture = resolved[
            "mixture_density_factors_relative_to_initial_electron_density"
        ]

        self.assertEqual(resolved["nitrogen_fraction_atomic_nuclei"], fraction)
        self.assertEqual(resolved["ionization_model"], "ADK")
        self.assertTrue(
            math.isclose(
                mixture["hydrogen_ion"],
                0.99 / 1.04,
                rel_tol=1e-14,
            )
        )
        self.assertTrue(
            math.isclose(
                mixture["nitrogen_ion"],
                0.01 / 1.04,
                rel_tol=1e-14,
            )
        )

    def test_fraction_and_configured_maximum_outside_contract_are_rejected(self) -> None:
        for fraction in ("-1e-9", "0.010000001", "1"):
            with self.subTest(fraction=fraction):
                with self.assertRaisesRegex(
                    ValueError,
                    "CAP_NITROGEN_DOPANT_FRACTION must lie within",
                ):
                    self.nitrogen.resolve_parameters(self.base_env(fraction))

        env = self.base_env("0.005")
        env["CAP_MAX_NITROGEN_DOPANT_FRACTION"] = "0.02"
        with self.assertRaisesRegex(
            ValueError,
            "requires CAP_MAX_NITROGEN_DOPANT_FRACTION=0.01",
        ):
            self.nitrogen.resolve_parameters(env)

    def test_fraction_semantics_and_uniform_profile_are_separate_metadata(self) -> None:
        resolved = self.nitrogen.resolve_parameters(self.base_env("0.007"))

        self.assertEqual(
            resolved["nitrogen_fraction_semantics"],
            "fraction_of_atomic_nuclei_nitrogen_equal_H2_N2_molecular_fraction",
        )
        self.assertEqual(
            resolved["nitrogen_profile"],
            {
                "profile_id": "uniform_nitrogen_fraction_v1",
                "longitudinal_shape": "uniform",
                "fraction_source": "CAP_NITROGEN_DOPANT_FRACTION",
            },
        )

    def test_source_declares_n5_adk_product_species_and_unfiltered_dual_dump(self) -> None:
        text = NITROGEN.read_text(encoding="utf-8")

        self.assertIn('name="preionized_background_electrons"', text)
        self.assertIn('name="nitrogen_ionized_electrons"', text)
        self.assertIn('name="nitrogen_ions"', text)
        self.assertIn('particle_type="N"', text)
        self.assertIn('charge_state=resolved["nitrogen_initial_charge_state"]', text)
        self.assertIn('model="ADK"', text)
        self.assertIn("ionized_species=nitrogen_ions", text)
        self.assertIn("product_species=nitrogen_ionized_electrons", text)
        self.assertIn("n_macroparticle_per_cell=[0, 0, 0]", text)
        self.assertIn(
            "[preionized_background_electrons, nitrogen_ionized_electrons]",
            text,
        )
        self.assertIn('"rho_preionized_background_electrons"', text)
        self.assertIn('"rho_nitrogen_ionized_electrons"', text)
        self.assertIn('period=resolved["particle_diagnostic_intervals"]', text)
        self.assertIn("warpx_dump_last_timestep=False", text)
        self.assertNotIn("warpx_plot_filter_function", text)

    def test_historical_baseline_remains_h_only(self) -> None:
        with self.assertRaisesRegex(
            ValueError,
            "requires CAP_NITROGEN_DOPANT_FRACTION=0",
        ):
            self.baseline.resolve_parameters(self.base_env("0.005"))

    def test_warpx_picmi_serializes_zero_positive_and_upper_bound_profiles(self) -> None:
        if importlib.util.find_spec("pywarpx") is None:
            self.skipTest(
                "pywarpx is only available in the WarpX 26.05 preflight environment"
            )

        baseline_resolved, baseline_serialized = self._serialize(
            BASELINE,
            fraction="0",
            case_name="serialize_baseline_h",
        )
        zero_resolved, zero_serialized = self._serialize(
            NITROGEN,
            fraction="0",
            case_name="serialize_nitrogen_h",
        )

        for key in (
            "laser_waist_radius_m",
            "laser_picmi_duration_s",
            "laser_a0",
            "time_step_s",
            "moving_window_step_distance_m",
            "max_steps",
            "particle_diagnostic_intervals",
        ):
            with self.subTest(serialized_zero_invariant=key):
                self.assertEqual(zero_resolved[key], baseline_resolved[key])

        self.assertIn("laser1.profile_waist = 4.2e-05", zero_serialized)
        self.assertIn(
            f'plasma_electrons.intervals = "{zero_resolved["particle_diagnostic_intervals"]}"',
            zero_serialized,
        )
        self.assertIn("nitrogen_ionized_electrons", zero_serialized)
        self.assertNotIn("nitrogen_ions.do_field_ionization", zero_serialized)
        self.assertIn("laser1.profile_waist = 4.2e-05", baseline_serialized)

        for fraction in ("0.005", "0.01"):
            with self.subTest(fraction=fraction):
                resolved, serialized = self._serialize(
                    NITROGEN,
                    fraction=fraction,
                    case_name=f"serialize_nitrogen_{fraction.replace('.', 'p')}",
                )
                self.assertEqual(resolved["ionization_model"], "ADK")
                self.assertIn("nitrogen_ions.do_field_ionization = 1", serialized)
                self.assertIn("nitrogen_ions.ionization_initial_level = 5", serialized)
                self.assertIn(
                    'nitrogen_ions.ionization_product_species = "nitrogen_ionized_electrons"',
                    serialized,
                )
                self.assertIn('nitrogen_ions.physical_element = "N"', serialized)
                self.assertIn("nitrogen_ionized_electrons", serialized)
                self.assertIn("laser1.profile_waist = 4.2e-05", serialized)
                self.assertIn(
                    f'plasma_electrons.intervals = "{resolved["particle_diagnostic_intervals"]}"',
                    serialized,
                )

    def _serialize(
        self,
        script: Path,
        *,
        fraction: str,
        case_name: str,
    ) -> tuple[dict[str, object], str]:
        env = os.environ.copy()
        env.update(self.base_env(fraction))
        env["CAP_CASE_NAME"] = case_name
        env["CAP_DRY_RUN"] = "true"

        with tempfile.TemporaryDirectory() as tmp_name:
            result = subprocess.run(
                [sys.executable, str(script)],
                cwd=tmp_name,
                env=env,
                capture_output=True,
                text=True,
                timeout=120,
                check=False,
            )
            self.assertEqual(
                result.returncode,
                0,
                msg=f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}",
            )
            tmp = Path(tmp_name)
            resolved = json.loads(
                (tmp / "resolved_parameters.json").read_text(encoding="utf-8")
            )
            serialized = (
                tmp / f"inputs_capillary_{case_name}"
            ).read_text(encoding="utf-8")
        return resolved, serialized


if __name__ == "__main__":
    unittest.main()
