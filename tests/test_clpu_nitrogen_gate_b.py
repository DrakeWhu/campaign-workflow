from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from campaign_workflow import clpu_n2_gate_b as gate_b


ROOT = Path(__file__).resolve().parents[1]
EXAMPLE = ROOT / "examples" / "sunrise" / "corrected_capillary"
CAMPAIGN = EXAMPLE / "campaign_nitrogen_uniform_soft50.json"
RUNNER = EXAMPLE / "run_nitrogen_warpx_case_sunrise.sh"
ORCHESTRATOR = EXAMPLE / "run_nitrogen_gate_b_sunrise.sh"


class ClpuNitrogenGateBTests(unittest.TestCase):
    def test_campaign_contract_is_dual_exit_unfiltered_and_uses_nitrogen_adapter(self) -> None:
        data = json.loads(CAMPAIGN.read_text(encoding="utf-8"))
        materialization = data["case_materialization"]
        constants = materialization["env_constants"]
        columns = {item["column"]: item for item in materialization["env_columns"]}

        self.assertEqual(materialization["input_template"], "input_template.py")
        self.assertIn("NITROGEN_DOPANT_FRACTION", columns)
        self.assertIn("PULSE_RESONANCE_FACTOR", columns)
        self.assertIn("LASER_DURATION_FWHM_FS", columns)
        self.assertEqual(constants["CAP_MAX_NITROGEN_DOPANT_FRACTION"], "0.01")
        self.assertEqual(
            constants["CAP_INPUT_CONVENTIONS_ACK"],
            "clpu_document_spot_values_are_picmi_w0_and_30fs_intensity_fwhm_v2",
        )
        self.assertEqual(
            constants["CAP_LASER_SPOT_DEFINITION"],
            "picmi_waist_w0_1e2_intensity",
        )
        self.assertNotIn("CAP_PARTICLE_DIAG_MIN_ENERGY_MEV", constants)
        self.assertNotIn("CAP_PARTICLE_DIAG_FORWARD_ONLY", constants)
        self.assertIn(
            "run_nitrogen_case_analysis_sunrise.sh",
            " ".join(data["analysis"]["command"]),
        )
        raw = {item["name"]: item for item in data["raw_diagnostics"]}
        self.assertEqual(raw["particles_openpmd"]["min_files"], 2)
        outputs = {item["name"]: item for item in data["analysis"]["outputs"]}
        self.assertEqual(outputs["particle_plateau_summary"]["min_rows"], 3)
        self.assertEqual(outputs["particle_capillary_summary"]["min_rows"], 3)
        self.assertIn("particle_multispecies_validation", outputs)

    def test_nitrogen_runner_pins_reviewed_base_and_revalidates_gate_b_before_generic_runner(self) -> None:
        text = RUNNER.read_text(encoding="utf-8")
        self.assertIn('export WFLOW_SRC="${WORKFLOW_ROOT}"', text)
        self.assertIn('export CAP_CORRECTED_INPUT_TEMPLATE="${BASE_INPUT}"', text)
        self.assertIn("validate_nitrogen_gate_b.py", text)
        self.assertIn("verify-runtime", text)
        self.assertIn("clpu_n2_gate_b.json", text)
        self.assertIn("run_warpx_case_sunrise.sh", text)
        self.assertNotIn("sbatch ", text)
        self.assertNotIn("srun ", text)

    def test_gate_b_orchestrator_materializes_all_candidates_without_submission_or_evolution_commands(self) -> None:
        text = ORCHESTRATOR.read_text(encoding="utf-8")
        self.assertIn("campaign_workflow.cli.prepare_batch_campaign", text)
        self.assertIn("campaign_workflow.cli.materialize_cases", text)
        self.assertIn("campaign_workflow.cli.init_case_states", text)
        self.assertIn("CAP_DRY_RUN=1 python ./input.py 2", text)
        self.assertIn("validate_nitrogen_gate_b.py", text)
        self.assertIn("GATE_B_STATUS=pass", text)
        self.assertNotIn("sbatch ", text)
        self.assertNotIn("srun ", text)
        self.assertNotIn("mpiexec ", text)
        self.assertNotIn("mpirun ", text)
        self.assertNotIn("cleanup_raw_case", text)

    def _fixture(self) -> tuple[tempfile.TemporaryDirectory[str], dict[str, object]]:
        tmp = tempfile.TemporaryDirectory()
        root = Path(tmp.name)
        workflow = root / "workflow"
        campaign = root / "campaign"
        case_name = "000_f32_chan_n4e18cm3_L5mm_d300um_foc0mm_N2pct0p5_tau30fs_rz"
        case_dir = campaign / case_name
        base = workflow / "examples/sunrise/corrected_capillary/input_template.py"
        base.parent.mkdir(parents=True)
        base.write_text("# corrected base\n", encoding="utf-8")
        campaign.mkdir()
        wrapper = campaign / "input_template.py"
        wrapper.write_text("# nitrogen wrapper\n", encoding="utf-8")
        case_dir.mkdir()
        input_path = case_dir / "input.py"
        input_path.write_bytes(wrapper.read_bytes())

        fraction = 0.005
        resonance = 1.2
        env_lines = {
            "CAP_CASE_ID": "0",
            "CAP_CASE_NAME": case_name,
            "CAP_LASER_CASE": "f32",
            "CAP_PLASMA_KIND": "chan",
            "CAP_N0_CM3": "4e18",
            "CAP_PLATEAU_LENGTH_M": "0.005",
            "CAP_RADIUS_M": "0.00015",
            "CAP_FOCUS_OFFSET_FROM_PLATEAU_START_MM": "0",
            "CAP_NITROGEN_DOPANT_FRACTION": str(fraction),
            "CAP_PULSE_RESONANCE_FACTOR": str(resonance),
            "CAP_LASER_INTENSITY_FWHM_S": "3e-14",
            "CAP_RMAX_M": "0.00018",
            "CAP_NR": "192",
            "CAP_LONG_PROFILE": "both",
            "CAP_REQUIRE_CONVENTION_ACK": "true",
            "CAP_INPUT_CONVENTIONS_ACK": (
                "clpu_document_spot_values_are_picmi_w0_"
                "and_30fs_intensity_fwhm_v2"
            ),
            "CAP_LASER_SPOT_DEFINITION": "picmi_waist_w0_1e2_intensity",
            "CAP_MAX_NITROGEN_DOPANT_FRACTION": "0.01",
            "CAP_TARGET_FIELD_FRAMES": "48",
            "CAP_MAX_RADIAL_CELL_M": "1.5e-6",
        }
        env_path = case_dir / "case.env"
        env_path.write_text(
            "".join(f'export {name}="{value}"\n' for name, value in env_lines.items()),
            encoding="utf-8",
        )
        manifest_dir = case_dir / "manifests"
        manifest_dir.mkdir()
        (manifest_dir / "input_materialization.json").write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "operation": "materialize_cases",
                    "source_input_template": {
                        "path": str(wrapper.resolve()),
                        "sha256": gate_b.sha256_file(wrapper),
                    },
                    "materialized_input": {
                        "path": "input.py",
                        "sha256": gate_b.sha256_file(input_path),
                    },
                    "case_env": {
                        "path": "case.env",
                        "sha256": gate_b.sha256_file(env_path),
                    },
                }
            ),
            encoding="utf-8",
        )
        (case_dir / "state.json").write_text(json.dumps({"state": "Created"}), encoding="utf-8")

        nuclei = 1.0 / (1.0 + 4.0 * fraction)
        picmi_duration = 30e-15 / math.sqrt(2.0 * math.log(2.0))
        base_sha = gate_b.sha256_file(base)
        resolved = {
            "schema_version": 6,
            "physics_model_id": gate_b.EXPECTED_PHYSICS_MODEL,
            "nitrogen_fraction_semantics": gate_b.EXPECTED_NITROGEN_SEMANTICS,
            "nitrogen_profile": {
                "profile_id": gate_b.EXPECTED_PROFILE_ID,
                "longitudinal_shape": "uniform",
            },
            "nitrogen_fraction_atomic_nuclei": fraction,
            "ionization_model": "ADK",
            "nitrogen_initial_charge_state": 5,
            "mixture_density_factors_relative_to_initial_electron_density": {
                "background_electron": 1.0,
                "hydrogen_ion": (1.0 - fraction) * nuclei,
                "nitrogen_ion": fraction * nuclei,
                "initial_charge_balance": 1.0,
            },
            "laser_waist_radius_m": 42e-6,
            "laser_intensity_fwhm_s": 30e-15,
            "laser_picmi_duration_s": picmi_duration,
            "laser_pulse_resonance_factor": resonance,
            "grid": {
                "nr": 192,
                "nz": 1536,
                "rmax_m": 180e-6,
                "n_azimuthal_modes": 2,
            },
            "cfl": 1.0,
            "time_step_model": "WarpX_CylindricalYeeAlgorithm_ComputeMaxDt",
            "time_step_s": 5.0e-16,
            "max_steps": 150,
            "particle_diagnostic_policy": gate_b.EXPECTED_PARTICLE_POLICY,
            "particle_diagnostic_min_energy_MeV": 0.0,
            "particle_diagnostic_forward_only": False,
            "particle_diagnostic_filter_expression": None,
            "particle_diagnostic_dump_last_timestep": False,
            "particle_diagnostic_targets": {
                "plateau_exit": {"iteration": 100},
                "capillary_exit": {"iteration": 150},
            },
            "particle_diagnostic_intervals": "100:100,150:150",
            "input_closure": {
                "wrapper_path": str(input_path.resolve()),
                "wrapper_sha256": gate_b.sha256_file(input_path),
                "base_resolution_mode": "explicit_override",
                "base_path": str(base.resolve()),
                "base_sha256": base_sha,
            },
        }
        resolved_path = case_dir / "resolved_parameters.json"
        resolved_path.write_text(json.dumps(resolved), encoding="utf-8")
        serialized_path = case_dir / f"inputs_capillary_{case_name}"
        serialized_path.write_text(
            "\n".join(
                [
                    'plasma_electrons.intervals = "100:100,150:150"',
                    "plasma_electrons.dump_last_timestep = 0",
                    "diagnostics.species = preionized_background_electrons nitrogen_ionized_electrons",
                    "laser1.profile_waist = 4.2e-05",
                    f"laser1.profile_duration = {picmi_duration:.17g}",
                    "warpx.cfl = 1",
                    "nitrogen_ions.do_field_ionization = 1",
                    "nitrogen_ions.ionization_initial_level = 5",
                    'nitrogen_ions.ionization_product_species = "nitrogen_ionized_electrons"',
                    'nitrogen_ions.physical_element = "N"',
                    "",
                ]
            ),
            encoding="utf-8",
        )
        row = {
            "CASE_ID": "0",
            "CASE_NAME": case_name,
            "LASER_CASE": "f32",
            "PLASMA_KIND": "chan",
            "N0_CM3": "4e18",
            "PLATEAU_LENGTH_MM": "5",
            "RADIUS_UM": "150",
            "FOCUS_OFFSET_FROM_PLATEAU_START_MM": "0",
            "NITROGEN_DOPANT_FRACTION": str(fraction),
            "PULSE_RESONANCE_FACTOR": str(resonance),
            "LASER_DURATION_FWHM_FS": "30",
            "CAP_RMAX_UM": "180",
            "CAP_NR": "192",
        }
        return tmp, {
            "row": row,
            "campaign": campaign,
            "workflow": workflow,
            "base_sha": base_sha,
            "serialized": serialized_path,
        }

    def test_materialized_case_accepts_exact_requested_resolved_serialized_contract(self) -> None:
        tmp, fixture = self._fixture()
        self.addCleanup(tmp.cleanup)
        with patch.object(gate_b, "EXPECTED_BASE_SHA256", fixture["base_sha"]):
            result = gate_b.validate_materialized_case(
                row=fixture["row"],
                campaign_root=fixture["campaign"],
                workflow_root=fixture["workflow"],
            )
        self.assertEqual(result["case_id"], 0)
        self.assertEqual(result["nitrogen_fraction"], 0.005)
        self.assertEqual(result["particle_iterations"], [100, 150])
        self.assertEqual(result["ionization_model"], "ADK")

    def test_materialized_case_rejects_reintroduced_particle_filter(self) -> None:
        tmp, fixture = self._fixture()
        self.addCleanup(tmp.cleanup)
        serialized = fixture["serialized"]
        serialized.write_text(
            serialized.read_text(encoding="utf-8")
            + 'plasma_electrons.preionized_background_electrons.plot_filter_function(t,x,y,z,ux,uy,uz) = "uz>0"\n',
            encoding="utf-8",
        )
        with patch.object(gate_b, "EXPECTED_BASE_SHA256", fixture["base_sha"]):
            with self.assertRaisesRegex(gate_b.GateBError, "filter unexpectedly present"):
                gate_b.validate_materialized_case(
                    row=fixture["row"],
                    campaign_root=fixture["campaign"],
                    workflow_root=fixture["workflow"],
                )

    def test_materialized_case_rejects_base_closure_drift(self) -> None:
        tmp, fixture = self._fixture()
        self.addCleanup(tmp.cleanup)
        base = fixture["workflow"] / "examples/sunrise/corrected_capillary/input_template.py"
        base.write_text("# changed base\n", encoding="utf-8")
        with patch.object(gate_b, "EXPECTED_BASE_SHA256", fixture["base_sha"]):
            with self.assertRaisesRegex(gate_b.GateBError, "base hash mismatch"):
                gate_b.validate_materialized_case(
                    row=fixture["row"],
                    campaign_root=fixture["campaign"],
                    workflow_root=fixture["workflow"],
                )


if __name__ == "__main__":
    unittest.main()
