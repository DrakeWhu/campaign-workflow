#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import importlib.util
import json
import math
import os
from pathlib import Path
from types import ModuleType
from typing import Any, Mapping


LASER_WAISTS_M = {
    "f20": 26.0e-6,
    "f32": 42.0e-6,
    "f40": 52.0e-6,
}
LASER_WAIST_CONVENTION = (
    "clpu_document_spot_values_are_picmi_w0_and_30fs_intensity_fwhm_v2"
)
LASER_SPOT_DEFINITION = "picmi_waist_w0_1e2_intensity"
MAX_NITROGEN_FRACTION = 0.01
NITROGEN_FRACTION_SEMANTICS = (
    "fraction_of_atomic_nuclei_nitrogen_equal_H2_N2_molecular_fraction"
)
NITROGEN_PROFILE_ID = "uniform_nitrogen_fraction_v1"
EXPECTED_CORRECTED_BASE_SHA256 = (
    "9dc068c3e43a2df2c92414e9d161618e752a83e1e6c67ef7b1ef97ccebcfdd3a"
)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _load_verified_base(
    path: Path,
    *,
    resolution_mode: str,
    wrapper_path: Path,
) -> tuple[ModuleType, dict[str, str]]:
    resolved_path = path.expanduser().resolve(strict=False)
    if resolved_path == wrapper_path:
        raise ImportError(
            "corrected capillary input template resolves to the wrapper itself: "
            f"{resolved_path}"
        )
    if not resolved_path.is_file():
        raise ImportError(
            "corrected capillary input template is missing for "
            f"{resolution_mode}: {resolved_path}"
        )

    actual_sha256 = _sha256_file(resolved_path)
    if actual_sha256 != EXPECTED_CORRECTED_BASE_SHA256:
        raise ImportError(
            "corrected capillary input template SHA256 mismatch for "
            f"{resolution_mode}: path={resolved_path} "
            f"expected={EXPECTED_CORRECTED_BASE_SHA256} got={actual_sha256}"
        )

    spec = importlib.util.spec_from_file_location(
        "clpu_corrected_capillary_input_template_nitrogen",
        resolved_path,
    )
    if spec is None or spec.loader is None:
        raise ImportError(
            "could not create import specification for corrected capillary "
            f"input template: {resolved_path}"
        )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module, {
        "resolution_mode": resolution_mode,
        "path": str(resolved_path),
        "sha256": actual_sha256,
    }


def _load_base_module() -> tuple[ModuleType, dict[str, str]]:
    this_file = Path(__file__).resolve()

    explicit = os.environ.get("CAP_CORRECTED_INPUT_TEMPLATE", "").strip()
    if explicit:
        return _load_verified_base(
            Path(explicit),
            resolution_mode="explicit_override",
            wrapper_path=this_file,
        )

    sibling = this_file.with_name("input_template.py")
    if sibling != this_file and sibling.exists():
        return _load_verified_base(
            sibling,
            resolution_mode="sibling",
            wrapper_path=this_file,
        )

    workflow_root = os.environ.get("WFLOW_SRC", "").strip()
    if workflow_root:
        workflow_candidate = (
            Path(workflow_root).expanduser()
            / "examples"
            / "sunrise"
            / "corrected_capillary"
            / "input_template.py"
        )
        return _load_verified_base(
            workflow_candidate,
            resolution_mode="workflow_root",
            wrapper_path=this_file,
        )

    raise ImportError(
        "could not locate corrected capillary input template: no explicit "
        "CAP_CORRECTED_INPUT_TEMPLATE, verified sibling, or WFLOW_SRC candidate"
    )


BASE, BASE_INPUT_CLOSURE = _load_base_module()


def _exact_target(
    *,
    target_distance_m: float,
    moving_window_step_distance_m: float,
    max_steps: int,
) -> dict[str, float | int]:
    iteration = BASE.steps_for_distance(
        target_distance_m,
        moving_window_step_distance_m,
    )
    if iteration > max_steps:
        raise ValueError(
            "simulation max_steps does not reach the requested particle target"
        )
    dump_distance_m = iteration * moving_window_step_distance_m
    return {
        "target_distance_m": float(target_distance_m),
        "iteration": int(iteration),
        "dump_distance_m": float(dump_distance_m),
        "distance_error_m": float(dump_distance_m - target_distance_m),
    }


def resolve_parameters(
    environ: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    env = dict(os.environ if environ is None else environ)

    convention_ack = str(env.get("CAP_INPUT_CONVENTIONS_ACK", "")).strip()
    if BASE.env_bool(env, "CAP_REQUIRE_CONVENTION_ACK", False) and (
        convention_ack != LASER_WAIST_CONVENTION
    ):
        raise ValueError(
            "nitrogen production preflight requires "
            f"CAP_INPUT_CONVENTIONS_ACK={LASER_WAIST_CONVENTION}"
        )

    spot_definition = str(
        env.get("CAP_LASER_SPOT_DEFINITION", "")
    ).strip().lower()
    if spot_definition != LASER_SPOT_DEFINITION:
        raise ValueError(
            "nitrogen campaign requires CAP_LASER_SPOT_DEFINITION="
            f"{LASER_SPOT_DEFINITION}"
        )

    nitrogen_fraction = BASE.env_float(
        env,
        "CAP_NITROGEN_DOPANT_FRACTION",
        0.0,
    )
    requested_max_fraction = BASE.env_float(
        env,
        "CAP_MAX_NITROGEN_DOPANT_FRACTION",
        MAX_NITROGEN_FRACTION,
    )
    if not math.isclose(
        requested_max_fraction,
        MAX_NITROGEN_FRACTION,
        rel_tol=0.0,
        abs_tol=1.0e-15,
    ):
        raise ValueError(
            "nitrogen campaign requires "
            f"CAP_MAX_NITROGEN_DOPANT_FRACTION={MAX_NITROGEN_FRACTION:g}"
        )
    if not 0.0 <= nitrogen_fraction <= MAX_NITROGEN_FRACTION:
        raise ValueError(
            "CAP_NITROGEN_DOPANT_FRACTION must lie within "
            f"[0, {MAX_NITROGEN_FRACTION:g}]"
        )
    env["CAP_NITROGEN_DOPANT_FRACTION"] = repr(nitrogen_fraction)
    env["CAP_MAX_NITROGEN_DOPANT_FRACTION"] = repr(MAX_NITROGEN_FRACTION)

    laser_case = str(env.get("CAP_LASER_CASE", "f32")).strip().lower()
    if laser_case not in LASER_WAISTS_M:
        raise ValueError(
            "nitrogen campaign requires CAP_LASER_CASE in "
            f"{sorted(LASER_WAISTS_M)}"
        )
    expected_waist_m = LASER_WAISTS_M[laser_case]
    requested_waist_m = BASE.env_float(
        env,
        "CAP_LASER_WAIST_M",
        expected_waist_m,
    )
    if not math.isclose(
        requested_waist_m,
        expected_waist_m,
        rel_tol=1.0e-12,
        abs_tol=1.0e-18,
    ):
        raise ValueError(
            "nitrogen campaign requires the CLPU document spot value to be "
            f"passed directly as PICMI w0: {laser_case}={expected_waist_m:.12g} m"
        )
    env["CAP_LASER_WAIST_M"] = repr(expected_waist_m)

    # The shared corrected-capillary template still exposes its historical
    # diameter convention. Adapt only its private input contract while this
    # profile records the corrected PICMI-w0 convention and the H/N5+ mixture.
    base_env = dict(env)
    base_env["CAP_INPUT_CONVENTIONS_ACK"] = BASE.REQUIRED_CONVENTION_ACK
    base_env["CAP_LASER_SPOT_DEFINITION"] = "diameter_1e2_intensity"

    resolved = dict(BASE.resolve_parameters(base_env))
    if not math.isclose(
        float(resolved["laser_waist_radius_m"]),
        expected_waist_m,
        rel_tol=1.0e-12,
        abs_tol=1.0e-18,
    ):
        raise RuntimeError("nitrogen PICMI waist contract was not preserved")

    mixture = resolved[
        "mixture_density_factors_relative_to_initial_electron_density"
    ]
    if not math.isclose(
        float(mixture["initial_charge_balance"]),
        1.0,
        rel_tol=0.0,
        abs_tol=1.0e-12,
    ):
        raise RuntimeError("H+/N5+ mixture does not preserve initial charge balance")

    step_distance_m = float(resolved["moving_window_step_distance_m"])
    max_steps = int(resolved["max_steps"])
    plateau = _exact_target(
        target_distance_m=(
            float(resolved["plateau_end_z"])
            - float(resolved["plasma_start_z"])
        ),
        moving_window_step_distance_m=step_distance_m,
        max_steps=max_steps,
    )
    capillary = _exact_target(
        target_distance_m=(
            float(resolved["plasma_end_z"])
            - float(resolved["plasma_start_z"])
        ),
        moving_window_step_distance_m=step_distance_m,
        max_steps=max_steps,
    )
    exact_iterations = sorted(
        {int(plateau["iteration"]), int(capillary["iteration"])}
    )
    intervals = ",".join(
        f"{iteration}:{iteration}" for iteration in exact_iterations
    )

    wrapper_path = Path(__file__).resolve()
    resolved.update(
        {
            "schema_version": 6,
            "physics_model_id": (
                "clpu_carlos_plateau_quasiparabolic_h_n5_adk_uniform_"
                "soft50_dual_exit_picmi_w0_v1"
            ),
            "input_closure": {
                "schema_version": 1,
                "wrapper_path": str(wrapper_path),
                "wrapper_sha256": _sha256_file(wrapper_path),
                "base_resolution_mode": BASE_INPUT_CLOSURE["resolution_mode"],
                "base_path": BASE_INPUT_CLOSURE["path"],
                "base_sha256": BASE_INPUT_CLOSURE["sha256"],
                "expected_base_sha256": EXPECTED_CORRECTED_BASE_SHA256,
            },
            "laser_spot_definition": LASER_SPOT_DEFINITION,
            "input_conventions_ack": LASER_WAIST_CONVENTION,
            "laser_spot_document_value_m": expected_waist_m,
            "laser_waist_radius_m": expected_waist_m,
            "laser_waist_contract": (
                "CLPU_26_42_52um_values_passed_directly_to_PICMI_waist_w0"
            ),
            "nitrogen_fraction_atomic_nuclei": nitrogen_fraction,
            "nitrogen_fraction_semantics": NITROGEN_FRACTION_SEMANTICS,
            "nitrogen_profile": {
                "profile_id": NITROGEN_PROFILE_ID,
                "longitudinal_shape": "uniform",
                "fraction_source": "CAP_NITROGEN_DOPANT_FRACTION",
            },
            "nitrogen_initial_charge_state": 5,
            "ionization_model": (
                "ADK" if nitrogen_fraction > 0.0 else "disabled_zero_fraction"
            ),
            "electron_species_provenance": {
                "combined_metric_scope": "all_electrons",
                "initial_free_electrons": "preionized_background_electrons",
                "nitrogen_adk_products": "nitrogen_ionized_electrons",
            },
            "particle_diagnostic_policy": (
                "dual_plateau_capillary_exit_exact_step_unfiltered_v1"
            ),
            "particle_diagnostic_primary_target": "plateau_exit",
            "particle_diagnostic_targets": {
                "plateau_exit": plateau,
                "capillary_exit": capillary,
            },
            "particle_diagnostic_intervals": intervals,
            "particle_diagnostic_target": "plateau_exit",
            "particle_diagnostic_target_distance_m": plateau[
                "target_distance_m"
            ],
            "particle_diagnostic_target_iteration_unaligned": plateau[
                "iteration"
            ],
            "particle_diagnostic_iteration": plateau["iteration"],
            "particle_diagnostic_aligned_distance_m": plateau[
                "dump_distance_m"
            ],
            "particle_diagnostic_alignment_error_steps": 0,
            "particle_diagnostic_alignment_error_m": plateau[
                "distance_error_m"
            ],
            "particle_diagnostic_min_energy_MeV": 0.0,
            "particle_diagnostic_forward_only": False,
            "particle_diagnostic_filter_expression": None,
            "particle_diagnostic_dump_last_timestep": False,
        }
    )
    return resolved


def main() -> None:
    from pywarpx import picmi

    resolved = resolve_parameters()
    grid_info = resolved["grid"]
    grid = picmi.CylindricalGrid(
        number_of_cells=[grid_info["nr"], grid_info["nz"]],
        n_azimuthal_modes=grid_info["n_azimuthal_modes"],
        lower_bound=[0.0, grid_info["zmin_m"]],
        upper_bound=[grid_info["rmax_m"], grid_info["zmax_m"]],
        lower_boundary_conditions=["none", "dirichlet"],
        upper_boundary_conditions=["dirichlet", "dirichlet"],
        lower_boundary_conditions_particles=["none", "absorbing"],
        upper_boundary_conditions_particles=["absorbing", "absorbing"],
        moving_window_velocity=[0.0, picmi.constants.c],
        warpx_max_grid_size=grid_info["max_grid_size"],
        warpx_blocking_factor=grid_info["blocking_factor"],
    )
    solver = picmi.ElectromagneticSolver(
        grid=grid,
        method="Yee",
        cfl=resolved["cfl"],
        divE_cleaning=0,
    )
    sim = picmi.Simulation(
        solver=solver,
        max_steps=resolved["max_steps"],
        verbose=1,
        particle_shape="cubic",
        warpx_use_filter=0,
        warpx_serialize_initial_conditions=1,
        warpx_do_dynamic_scheduling=0,
    )

    electron_species: list[Any] = []
    if resolved["plasma_kind"] != "vac":
        expression = resolved["density_expression"]
        constants = {
            "n0": resolved["n0_m3"],
            "profile_c2": resolved[
                "channel_profile_quadratic_coefficient"
            ],
            "profile_c4": resolved[
                "channel_profile_quartic_coefficient"
            ],
            "r_cap": resolved["radius_m"],
            "plasma_start_z": resolved["plasma_start_z"],
            "plateau_start_z": resolved["plateau_start_z"],
            "plateau_end_z": resolved["plateau_end_z"],
            "plasma_end_z": resolved["plasma_end_z"],
            "front_ramp_length": resolved["front_ramp_length"],
            "back_ramp_length": resolved["back_ramp_length"],
        }
        common = {
            "density_expression": expression,
            "lower_bound": [
                -resolved["radius_m"],
                None,
                resolved["particle_load_zmin_m"],
            ],
            "upper_bound": [resolved["radius_m"], None, None],
            "fill_in": True,
            "warpx_density_min": 1.0e10,
            **BASE.expression_constants(expression, constants),
        }

        def distribution(scale: float, *, thermal: bool = False) -> Any:
            kwargs = {
                **common,
                "density_scale": scale,
                "warpx_density_max": scale * resolved["n_edge_m3"],
            }
            if thermal:
                speed = resolved["electron_thermal_speed_m_s"]
                kwargs["rms_velocity"] = [speed, speed, speed]
            return picmi.AnalyticDistribution(**kwargs)

        mixture = resolved[
            "mixture_density_factors_relative_to_initial_electron_density"
        ]
        preionized_background_electrons = picmi.Species(
            particle_type="electron",
            name="preionized_background_electrons",
            initial_distribution=distribution(1.0, thermal=True),
        )
        hydrogen_ions = picmi.Species(
            particle_type="H",
            name="hydrogen_ions",
            charge_state=1,
            initial_distribution=distribution(mixture["hydrogen_ion"]),
            warpx_do_not_push=True,
        )
        nitrogen_ionized_electrons = picmi.Species(
            particle_type="electron",
            name="nitrogen_ionized_electrons",
        )
        layout = picmi.GriddedLayout(
            grid=grid,
            n_macroparticle_per_cell=resolved[
                "macroparticles_per_cell_r_theta_z"
            ],
        )
        empty_layout = picmi.GriddedLayout(
            grid=grid,
            n_macroparticle_per_cell=[0, 0, 0],
        )
        sim.add_species(preionized_background_electrons, layout=layout)
        sim.add_species(hydrogen_ions, layout=layout)
        sim.add_species(nitrogen_ionized_electrons, layout=empty_layout)
        electron_species.extend(
            [preionized_background_electrons, nitrogen_ionized_electrons]
        )

        if resolved["nitrogen_fraction_atomic_nuclei"] > 0.0:
            nitrogen_ions = picmi.Species(
                particle_type="N",
                name="nitrogen_ions",
                charge_state=resolved["nitrogen_initial_charge_state"],
                initial_distribution=distribution(mixture["nitrogen_ion"]),
                warpx_do_not_push=True,
            )
            sim.add_species(nitrogen_ions, layout=layout)
            sim.add_interaction(
                picmi.FieldIonization(
                    model="ADK",
                    ionized_species=nitrogen_ions,
                    product_species=nitrogen_ionized_electrons,
                )
            )

    antenna_z = resolved["laser_antenna_z_m"]
    laser = picmi.GaussianLaser(
        wavelength=resolved["laser_wavelength_m"],
        waist=resolved["laser_waist_radius_m"],
        duration=resolved["laser_picmi_duration_s"],
        focal_position=[0.0, 0.0, resolved["focus_z_m"]],
        centroid_position=[
            0.0,
            0.0,
            antenna_z
            - picmi.constants.c * resolved["laser_profile_t_peak_s"],
        ],
        propagation_direction=[0.0, 0.0, 1.0],
        polarization_direction=[1.0, 0.0, 0.0],
        a0=resolved["laser_a0"],
        fill_in=False,
    )
    antenna = picmi.LaserAntenna(
        position=[0.0, 0.0, antenna_z],
        normal_vector=[0.0, 0.0, 1.0],
    )
    sim.add_laser(laser, injection_method=antenna)

    field_data = ["E", "B"]
    if electron_species:
        field_data.extend(
            [
                "rho_preionized_background_electrons",
                "rho_nitrogen_ionized_electrons",
            ]
        )
    sim.add_diagnostic(
        picmi.FieldDiagnostic(
            name="fields",
            grid=grid,
            period=resolved["field_diagnostic_period"],
            data_list=field_data,
            write_dir="diags",
            warpx_format="openpmd",
            warpx_openpmd_backend="h5",
            warpx_dump_rz_modes=1,
            warpx_dump_last_timestep=True,
        )
    )
    if electron_species:
        sim.add_diagnostic(
            picmi.ParticleDiagnostic(
                name="plasma_electrons",
                period=resolved["particle_diagnostic_intervals"],
                species=electron_species,
                data_list=["position", "momentum", "weighting"],
                write_dir="diags",
                warpx_format="openpmd",
                warpx_openpmd_backend="h5",
                warpx_dump_last_timestep=False,
            )
        )

    BASE.write_resolved_parameters(
        Path("resolved_parameters.json"),
        resolved,
    )
    sim.write_input_file(
        file_name=f"inputs_capillary_{resolved['case_name']}"
    )
    print(json.dumps(resolved, indent=2, sort_keys=True))
    if resolved["dry_run"]:
        print("[CLPU] nitrogen PICMI preflight completed; simulation not started")
        return
    sim.initialize_inputs()
    sim.initialize_warpx()
    sim.step(resolved["max_steps"])


if __name__ == "__main__":
    main()
