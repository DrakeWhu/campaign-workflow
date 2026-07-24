#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import json
import math
import os
from pathlib import Path
from types import ModuleType
from typing import Any, Mapping


def _load_base_module() -> ModuleType:
    this_file = Path(__file__).resolve()
    candidates: list[Path] = []

    explicit = os.environ.get("CAP_CORRECTED_INPUT_TEMPLATE", "").strip()
    if explicit:
        candidates.append(Path(explicit).expanduser())

    candidates.append(this_file.with_name("input_template.py"))

    workflow_root = os.environ.get("WFLOW_SRC", "").strip()
    if workflow_root:
        candidates.append(
            Path(workflow_root).expanduser()
            / "examples"
            / "sunrise"
            / "corrected_capillary"
            / "input_template.py"
        )

    checked: list[str] = []
    for candidate in candidates:
        path = candidate.resolve(strict=False)
        checked.append(str(path))
        if path == this_file or not path.is_file():
            continue
        spec = importlib.util.spec_from_file_location(
            "clpu_corrected_capillary_input_template",
            path,
        )
        if spec is None or spec.loader is None:
            continue
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    raise ImportError(
        "could not locate corrected capillary input template; checked: "
        + ", ".join(checked)
    )


BASE = _load_base_module()


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
    requested_fraction = float(env.get("CAP_NITROGEN_DOPANT_FRACTION", "0") or 0.0)
    if not math.isclose(requested_fraction, 0.0, abs_tol=0.0):
        raise ValueError(
            "baseline campaign requires CAP_NITROGEN_DOPANT_FRACTION=0"
        )
    env["CAP_NITROGEN_DOPANT_FRACTION"] = "0"

    resolved = dict(BASE.resolve_parameters(env))
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

    resolved.update(
        {
            "schema_version": 4,
            "physics_model_id": (
                "clpu_carlos_plateau_quasiparabolic_hydrogen_"
                "baseline_soft50_dual_exit_v1"
            ),
            "nitrogen_fraction_atomic_nuclei": 0.0,
            "nitrogen_initial_charge_state": None,
            "ionization_model": "disabled_hydrogen_baseline",
            "electron_species_provenance": {
                "combined_metric_scope": "preionized_background_electrons",
                "initial_free_electrons": "preionized_background_electrons",
                "ionization_products": None,
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

        def distribution(*, thermal: bool = False) -> Any:
            kwargs = {
                **common,
                "density_scale": 1.0,
                "warpx_density_max": resolved["n_edge_m3"],
            }
            if thermal:
                speed = resolved["electron_thermal_speed_m_s"]
                kwargs["rms_velocity"] = [speed, speed, speed]
            return picmi.AnalyticDistribution(**kwargs)

        background_electrons = picmi.Species(
            particle_type="electron",
            name="preionized_background_electrons",
            initial_distribution=distribution(thermal=True),
        )
        hydrogen_ions = picmi.Species(
            particle_type="H",
            name="hydrogen_ions",
            charge_state=1,
            initial_distribution=distribution(),
            warpx_do_not_push=True,
        )
        layout = picmi.GriddedLayout(
            grid=grid,
            n_macroparticle_per_cell=resolved[
                "macroparticles_per_cell_r_theta_z"
            ],
        )
        sim.add_species(background_electrons, layout=layout)
        sim.add_species(hydrogen_ions, layout=layout)
        electron_species.append(background_electrons)

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
        field_data.append("rho_preionized_background_electrons")
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
        print("[CLPU] baseline PICMI preflight completed; simulation not started")
        return
    sim.initialize_inputs()
    sim.initialize_warpx()
    sim.step(resolved["max_steps"])


if __name__ == "__main__":
    main()
