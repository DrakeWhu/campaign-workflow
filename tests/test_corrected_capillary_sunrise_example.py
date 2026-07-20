from __future__ import annotations

import importlib.util
import json
import math
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import types
import unittest
from unittest import mock

import numpy as np

from campaign_workflow.core.case_materialization import (
    build_env_values,
    get_case_materialization_config,
)
from campaign_workflow.core.tsv_cases import CaseRecord


EXAMPLE = Path("examples/sunrise/corrected_capillary")


def load_input_module():
    path = EXAMPLE / "input_template.py"
    spec = importlib.util.spec_from_file_location("corrected_capillary_input", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def load_animation_module():
    path = EXAMPLE / "animate_guiding_fields.py"
    imageio_package = types.ModuleType("imageio")
    imageio_v2 = types.ModuleType("imageio.v2")
    imageio_package.v2 = imageio_v2
    openpmd = types.ModuleType("openpmd_viewer")
    openpmd.OpenPMDTimeSeries = object
    spec = importlib.util.spec_from_file_location("corrected_capillary_animation", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    replacements = {
        "imageio": imageio_package,
        "imageio.v2": imageio_v2,
        "openpmd_viewer": openpmd,
    }
    missing = object()
    previous = {name: sys.modules.get(name, missing) for name in replacements}
    sys.modules.update(replacements)
    try:
        spec.loader.exec_module(module)
    finally:
        for name, value in previous.items():
            if value is missing:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = value
    return module


class CorrectedCapillarySunriseExampleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.physics = load_input_module()
        cls.campaign = json.loads(
            (EXAMPLE / "campaign.json").read_text(encoding="utf-8")
        )
        cls.optimization = json.loads(
            (EXAMPLE / "optimization.json").read_text(encoding="utf-8")
        )

    def test_rebuild_uses_a_fresh_root_and_campaign_identity(self) -> None:
        expected = "clpu_capillary_guiding_bo_004_corrected_n2_soft50_v2"
        self.assertEqual(self.optimization["optimization_name"], expected)
        self.assertIn(expected, self.optimization["optimizer"]["optimizer_config"])
        self.assertTrue(
            self.optimization["campaign_preparation"]["campaign_name_template"]
            .startswith(expected)
        )

    def test_rebuild_script_is_syntax_checked_and_stops_before_canary(self) -> None:
        script = EXAMPLE / "rebuild_v2_before_canary_sunrise.sh"
        text = script.read_text(encoding="utf-8")
        result = subprocess.run(
            ["bash", "-n", str(script)],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("clpu_capillary_guiding_bo_004_corrected_n2_soft50_v2", text)
        self.assertIn("prepare_batch_campaign.py", text)
        self.assertIn("materialize_cases.py", text)
        self.assertIn("init_case_states.py", text)
        self.assertIn("READY_FOR_CANARY=1", text)
        self.assertIn("NO_SBATCH_CALLED=1", text)
        unsafe = re.compile(
            r"(^|[;|&()\s])"
            r"(sbatch|srun|mpiexec|mpirun|rm|logout)"
            r"([;|&()\s]|$)",
            flags=re.MULTILINE,
        )
        self.assertIsNone(unsafe.search(text))

    def test_documented_reference_recovers_40p5_um(self) -> None:
        diameter = self.physics.channel_matched_spot_diameter_m(150.0e-6, 4.0e18)
        self.assertAlmostEqual(diameter * 1.0e6, 40.5, places=12)
        self.assertAlmostEqual(
            self.physics.resonant_intensity_fwhm_s(4.0e18) * 1.0e15,
            27.843789790135165,
        )

    def test_channel_curvature_changes_with_radius_and_density(self) -> None:
        w_200 = self.physics.channel_matched_spot_diameter_m(100.0e-6, 4.0e18)
        w_300 = self.physics.channel_matched_spot_diameter_m(150.0e-6, 4.0e18)
        w_dense = self.physics.channel_matched_spot_diameter_m(150.0e-6, 6.0e18)
        self.assertAlmostEqual(w_200 / w_300, math.sqrt(2.0 / 3.0))
        self.assertLess(w_dense, w_300)

        curvature_200 = 0.33 * 4.0e24 / (100e-6) ** 2
        curvature_300 = 0.33 * 4.0e24 / (150e-6) ** 2
        self.assertAlmostEqual(curvature_200 / curvature_300, 2.25)
        self.assertAlmostEqual(self.physics.channel_profile_multiplier(1.0), 1.73)

    def test_channel_profile_exists_only_in_plateau_and_ramps_are_uniform(self) -> None:
        resolved = self.physics.resolve_parameters(
            {
                "CAP_PLASMA_KIND": "chan",
                "CAP_LONG_PROFILE": "both",
                "CAP_RADIUS_M": "1.5e-4",
                "CAP_RMAX_M": "1.8e-4",
            }
        )
        expression = resolved["density_expression"]
        self.assertEqual(resolved["channel_profile_longitudinal_scope"], "plateau_only")
        self.assertEqual(resolved["ramp_radial_model"], "uniform_inside_capillary")
        self.assertEqual(expression.count("profile_c2"), 1)
        self.assertEqual(expression.count("profile_c4"), 1)
        self.assertIn(
            "((z-plasma_start_z)/front_ramp_length)*n0",
            expression,
        )
        self.assertIn(
            "((plasma_end_z-z)/back_ramp_length)*n0",
            expression,
        )
        self.assertIn(
            "if((z >= plateau_start_z) and (z < plateau_end_z), "
            "n0*(1.0+profile_c2",
            expression,
        )
        self.assertNotIn("front_ramp_length)*n0*(1.0+", expression)
        self.assertNotIn("back_ramp_length)*n0*(1.0+", expression)

    def test_n5_mixture_is_initially_charge_neutral(self) -> None:
        factors = self.physics.mixture_density_factors(0.005)
        self.assertAlmostEqual(factors["initial_charge_balance"], 1.0)
        self.assertGreater(factors["maximum_extra_electron"], 0.0)
        self.assertLess(factors["maximum_extra_electron"], 0.01)

    def test_laser_document_diameter_is_converted_to_picmi_radius(self) -> None:
        resolved = self.physics.resolve_parameters(
            {
                "CAP_LASER_CASE": "f20",
                "CAP_N0_CM3": "4e18",
                "CAP_RADIUS_M": "1.5e-4",
                "CAP_RMAX_M": "1.8e-4",
                "CAP_NR": "192",
                "CAP_NITROGEN_DOPANT_FRACTION": "0.005",
            }
        )
        self.assertEqual(
            resolved["laser_spot_definition"], "diameter_1e2_intensity"
        )
        self.assertAlmostEqual(resolved["laser_waist_radius_m"], 13.0e-6)
        self.assertAlmostEqual(
            resolved["laser_picmi_duration_s"],
            30.0e-15 / math.sqrt(2.0 * math.log(2.0)),
        )
        self.assertAlmostEqual(resolved["laser_a0"], 1.30)
        self.assertAlmostEqual(resolved["n_edge_m3"] / resolved["n0_m3"], 1.73)
        self.assertIn("profile_c4", resolved["density_expression"])
        self.assertEqual(resolved["ionization_model"], "ADK")
        self.assertLessEqual(resolved["grid"]["radial_cell_m"], 1.5e-6)

    def test_longer_pulse_keeps_reference_energy_by_scaling_a0(self) -> None:
        resolved = self.physics.resolve_parameters(
            {
                "CAP_LASER_CASE": "f32",
                "CAP_LASER_INTENSITY_FWHM_S": "6e-14",
            }
        )
        self.assertAlmostEqual(resolved["laser_a0"], 0.91 / math.sqrt(2.0))
        self.assertAlmostEqual(resolved["laser_profile_t_peak_s"], 60.0e-15)

    def test_domain_that_clips_capillary_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "CAP_RMAX_M must exceed"):
            self.physics.resolve_parameters(
                {
                    "CAP_RADIUS_M": "2.5e-4",
                    "CAP_RMAX_M": "1.8e-4",
                    "CAP_NR": "192",
                }
            )

    def test_production_preflight_is_blocked_until_laser_conventions_are_acknowledged(self) -> None:
        with self.assertRaisesRegex(ValueError, "production preflight blocked"):
            self.physics.resolve_parameters(
                {
                    "CAP_REQUIRE_CONVENTION_ACK": "true",
                    "CAP_INPUT_CONVENTIONS_ACK": "PENDING_CARLOS_CONFIRMATION",
                }
            )

    def test_campaign_materializes_nitrogen_and_validates_soft50_outputs(self) -> None:
        row = {
            "CASE_ID": "0",
            "CASE_NAME": "case",
            "LASER_CASE": "f32",
            "PLASMA_KIND": "chan",
            "N0_CM3": "4e18",
            "PLATEAU_LENGTH_MM": "5",
            "RADIUS_UM": "150",
            "FOCUS_OFFSET_FROM_PLATEAU_START_MM": "0",
            "NITROGEN_DOPANT_FRACTION": "0.005",
            "PULSE_RESONANCE_FACTOR": "1.0774395377251684",
            "LASER_DURATION_FWHM_FS": "30",
            "CAP_RMAX_UM": "180",
            "CAP_NR": "192",
        }
        values, errors = build_env_values(
            CaseRecord(case_id=0, case_name="case", row=row),
            get_case_materialization_config(self.campaign),
        )
        self.assertEqual(errors, [])
        env = dict(values)
        self.assertEqual(env["CAP_NITROGEN_DOPANT_FRACTION"], "0.005")
        self.assertEqual(env["CAP_PULSE_RESONANCE_FACTOR"], "1.0774395377251684")
        self.assertEqual(env["CAP_LASER_INTENSITY_FWHM_S"], "3e-14")
        self.assertEqual(env["CAP_RADIUS_M"], "1.5e-4")
        output_names = {
            output["name"] for output in self.campaign["analysis"]["outputs"]
        }
        self.assertIn("particle_summary", output_names)
        self.assertIn("particle_soft50_curves", output_names)
        self.assertIn("particle_species_validation", output_names)
        self.assertIn("guiding_plot_waist", output_names)
        self.assertIn("guiding_plot_summary", output_names)
        self.assertIn("animation_eperp2", output_names)
        self.assertIn("animation_ez_wake", output_names)
        self.assertIn("animation_validation", output_names)
        self.assertTrue(self.campaign["cleanup"]["require_reduced_validated"])

    def test_particle_snapshot_is_filtered_and_aligned_with_plateau_exit(self) -> None:
        resolved = self.physics.resolve_parameters(
            {
                "CAP_PLASMA_KIND": "chan",
                "CAP_LONG_PROFILE": "both",
                "CAP_PLATEAU_LENGTH_M": "5e-3",
                "CAP_RADIUS_M": "1.5e-4",
                "CAP_RMAX_M": "1.8e-4",
            }
        )
        self.assertEqual(resolved["max_steps"], 192000)
        self.assertEqual(resolved["field_diagnostic_period"], 4086)
        self.assertEqual(
            resolved["particle_diagnostic_target_iteration_unaligned"], 128000
        )
        self.assertEqual(resolved["particle_diagnostic_iteration"], 126666)
        self.assertEqual(resolved["particle_diagnostic_intervals"], "126666:126666")
        self.assertEqual(
            resolved["particle_diagnostic_iteration"]
            % resolved["field_diagnostic_period"],
            0,
        )
        self.assertFalse(resolved["particle_diagnostic_dump_last_timestep"])
        self.assertEqual(resolved["particle_diagnostic_min_energy_MeV"], 5.0)
        self.assertTrue(resolved["particle_diagnostic_forward_only"])
        self.assertIn("uz > 0.0", resolved["particle_diagnostic_filter_expression"])
        self.assertIn(">= 5", resolved["particle_diagnostic_filter_expression"])

    def test_input_uses_n5_adk_and_single_particle_diagnostic(self) -> None:
        text = (EXAMPLE / "input_template.py").read_text(encoding="utf-8")
        self.assertIn('charge_state=resolved["nitrogen_initial_charge_state"]', text)
        self.assertIn('model="ADK"', text)
        self.assertIn('name="preionized_background_electrons"', text)
        self.assertIn('name="nitrogen_ionized_electrons"', text)
        self.assertIn('name="plasma_electrons"', text)
        self.assertIn('period=resolved["particle_diagnostic_intervals"]', text)
        self.assertIn("warpx_plot_filter_function=resolved[", text)
        self.assertIn("warpx_dump_last_timestep=False", text)
        self.assertEqual(text.count("warpx_dump_last_timestep=True"), 1)

    def test_warpx_picmi_serializes_exact_filtered_particle_interval(self) -> None:
        try:
            import pywarpx  # noqa: F401
        except ModuleNotFoundError:
            self.skipTest("pywarpx is only available in the WarpX preflight environment")

        env = {
            "CAP_CASE_ID": "preflight",
            "CAP_CASE_NAME": "serialization_preflight",
            "CAP_PLASMA_KIND": "chan",
            "CAP_NITROGEN_DOPANT_FRACTION": "0.005",
            "CAP_RADIUS_M": "1.5e-4",
            "CAP_RMAX_M": "1.8e-4",
            "CAP_DRY_RUN": "true",
        }
        old_cwd = Path.cwd()
        with tempfile.TemporaryDirectory() as tmp:
            try:
                os.chdir(tmp)
                with mock.patch.dict(os.environ, env, clear=False):
                    self.physics.main()
                resolved = json.loads(
                    Path("resolved_parameters.json").read_text(encoding="utf-8")
                )
                serialized = Path("inputs_capillary_serialization_preflight").read_text(
                    encoding="utf-8"
                )
            finally:
                os.chdir(old_cwd)

        iteration = resolved["particle_diagnostic_iteration"]
        self.assertIn(
            f"plasma_electrons.intervals = {iteration}:{iteration}", serialized
        )
        self.assertIn("plasma_electrons.dump_last_timestep = 0", serialized)
        self.assertNotIn("plasma_electrons.dump_last_timestep = 1", serialized)
        self.assertIn(
            "plasma_electrons.preionized_background_electrons."
            "plot_filter_function(t,x,y,z,ux,uy,uz)",
            serialized,
        )
        self.assertIn(">= 5", serialized)

    def test_animation_generation_and_decode_validation_precede_cleanup(self) -> None:
        script = (EXAMPLE / "run_case_analysis_sunrise.sh").read_text(
            encoding="utf-8"
        )
        self.assertIn("animate_guiding_fields.py", script)
        self.assertIn("validate_animations.py", script)
        self.assertIn("validate_particle_species_outputs.py", script)
        self.assertIn("preionized_background_electrons", script)
        self.assertIn("nitrogen_ionized_electrons", script)
        self.assertIn("CAMPAIGN_PARTICLE_MAX_TARGET_ITERATION_DELTA=\"0\"", script)
        self.assertIn("--resolved-parameters", script)
        self.assertNotIn("--no-plots", script)
        self.assertLess(
            script.index("animate_guiding_fields.py"),
            script.index("validate_animations.py"),
        )
        self.assertIn("animations/validation.json", script)

    def test_animation_rz_orientation_and_renderer(self) -> None:
        try:
            animation = load_animation_module()
        except ModuleNotFoundError as exc:
            if exc.name == "matplotlib":
                self.skipTest(
                    "animation rendering is validated in the guiding-analysis "
                    "environment, where matplotlib is a runtime dependency"
                )
            raise
        info = types.SimpleNamespace(
            axes={0: "z", 1: "r"},
            z=np.asarray([-1.0e-6, 1.0e-6]),
            r=np.asarray([-1.0e-6, 0.0, 1.0e-6]),
        )
        array, r_um, z_um = animation.get_rz_signed(
            np.arange(6.0).reshape(2, 3), info
        )
        self.assertEqual(array.shape, (3, 2))
        np.testing.assert_allclose(r_um, [-1.0, 0.0, 1.0])
        np.testing.assert_allclose(z_um, [-1.0, 1.0])
        frame = animation.render_frame(
            array**2,
            r_um,
            z_um,
            xlim_um=(-2.0, 2.0),
            rlim_um=(-2.0, 2.0),
            symmetric=False,
            colorbar_label="test",
            title="test",
        )
        self.assertEqual(frame.shape, (600, 1080, 3))
        self.assertEqual(frame.dtype, np.uint8)


if __name__ == "__main__":
    unittest.main()
