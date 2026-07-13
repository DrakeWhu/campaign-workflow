#!/usr/bin/env python3
from __future__ import annotations

import json
import math
import os
from pathlib import Path
from typing import Any, Mapping


C_LIGHT = 299792458.0
Q_E = 1.602176634e-19
EPS0 = 8.8541878188e-12
M_E = 9.1093837139e-31


def env_float(
    environ: Mapping[str, str], name: str, default: float | None = None
) -> float:
    raw = environ.get(name)
    if raw is None or str(raw).strip() == "":
        if default is None:
            raise ValueError(f"missing required environment variable {name}")
        return float(default)
    try:
        value = float(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be numeric, got {raw!r}") from exc
    if not math.isfinite(value):
        raise ValueError(f"{name} must be finite, got {raw!r}")
    return value


def env_bool(
    environ: Mapping[str, str], name: str, default: bool = False
) -> bool:
    raw = environ.get(name)
    if raw is None or str(raw).strip() == "":
        return bool(default)
    normalized = str(raw).strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"{name} must be boolean-like, got {raw!r}")


def wrap_degrees(value: float, period: float) -> float:
    return float(value) % float(period)


def honeycomb_fill_fraction(cnt_radius: float, cnt_gap: float) -> float:
    spacing = 2.0 * cnt_radius + cnt_gap
    unit_cell_area = math.sqrt(3.0) * spacing**2 / 2.0
    return math.pi * cnt_radius**2 / unit_cell_area


def effective_electron_density(
    plasma_ion_density: float,
    cnt_radius: float,
    cnt_gap: float,
    carbon_charge_state: int = 3,
) -> float:
    return (
        float(carbon_charge_state)
        * plasma_ion_density
        * honeycomb_fill_fraction(cnt_radius, cnt_gap)
    )


def yee_cfl_timestep(
    dx: float, dy: float, dz: float, *, cfl: float = 1.0
) -> float:
    return float(cfl) / (
        C_LIGHT * math.sqrt(dx**-2 + dy**-2 + dz**-2)
    )


def final_step_count(
    *,
    plasma_zmax: float,
    post_plasma_margin: float,
    initial_window_front: float,
    moving_window_velocity: float,
    timestep: float,
) -> int:
    requested_front = plasma_zmax + post_plasma_margin
    travel = max(requested_front - initial_window_front, 0.0)
    if moving_window_velocity <= 0.0 or timestep <= 0.0:
        raise ValueError("moving-window velocity and timestep must be positive")
    return max(1, int(math.ceil(travel / (moving_window_velocity * timestep))))


def jones_components(
    polarization_angle_deg: float,
    ellipticity_angle_deg: float,
) -> dict[str, complex]:
    """Return normalized x/y Jones components.

    The orientation angle is measured from +x. The ellipticity angle is in
    [-45, 45] degrees: zero is linear and either endpoint is circular with
    opposite handedness.
    """

    psi = math.radians(wrap_degrees(polarization_angle_deg, 180.0))
    chi = math.radians(float(ellipticity_angle_deg))
    if chi < -math.pi / 4.0 - 1.0e-12 or chi > math.pi / 4.0 + 1.0e-12:
        raise ValueError("ellipticity angle must be within [-45, 45] degrees")

    return {
        "x": complex(
            math.cos(psi) * math.cos(chi),
            -math.sin(psi) * math.sin(chi),
        ),
        "y": complex(
            math.sin(psi) * math.cos(chi),
            math.cos(psi) * math.sin(chi),
        ),
    }


def laser_component_parameters(
    polarization_angle_deg: float,
    ellipticity_angle_deg: float,
    *,
    amplitude_cutoff: float = 1.0e-12,
) -> list[dict[str, Any]]:
    components = jones_components(
        polarization_angle_deg,
        ellipticity_angle_deg,
    )
    directions = {
        "x": [1.0, 0.0, 0.0],
        "y": [0.0, 1.0, 0.0],
    }
    resolved: list[dict[str, Any]] = []
    for axis in ("x", "y"):
        value = components[axis]
        amplitude = abs(value)
        if amplitude <= amplitude_cutoff:
            continue
        resolved.append(
            {
                "axis": axis,
                "name": f"laser_{axis}",
                "polarization_direction": directions[axis],
                "amplitude_fraction": amplitude,
                # WarpX uses cos(carrier_phase - phi0), so the PICMI CEP is
                # the negative complex Jones phase.
                "cep_rad": -math.atan2(value.imag, value.real),
            }
        )
    return resolved


def hexagonal_density_expression() -> str:
    x_rot = "(ct*x + st*y)"
    y_rot = "(-st*x + ct*y)"
    row = f"floor(({y_rot}) / h + 0.5)"
    stagger = f"D * 0.5 * (({row}) - 2 * floor(({row}) / 2))"
    center_x = (
        f"D * floor((({x_rot}) - ({stagger})) / D + 0.5) + ({stagger})"
    )
    center_y = f"h * floor(({y_rot}) / h + 0.5)"
    return (
        "n0 * if("
        f"((({x_rot}) - ({center_x}))^2 + "
        f"(({y_rot}) - ({center_y}))^2) <= r^2, "
        "1, 0)"
    )


def resolve_parameters(environ: Mapping[str, str] | None = None) -> dict[str, Any]:
    env = os.environ if environ is None else environ

    plasma_ion_density = env_float(
        env, "MC_PLASMA_ION_DENSITY_M3", 1.0e25
    )
    cnt_radius = env_float(env, "MC_CNT_RADIUS_M", 2.0e-6)
    cnt_gap = env_float(env, "MC_CNT_GAP_M", 0.75e-6)
    laser_waist = env_float(env, "MC_LASER_WAIST_M", 1.0e-6)
    plasma_length = env_float(env, "MC_PLASMA_LENGTH_M", 100.0e-6)
    halfwidth_x = env_float(env, "MC_PLASMA_HALF_WIDTH_X_M", 16.0e-6)
    halfwidth_y = env_float(env, "MC_PLASMA_HALF_WIDTH_Y_M", 16.0e-6)
    focus_offset = env_float(
        env, "MC_FOCUS_OFFSET_FROM_PLASMA_START_M", 5.0e-6
    )
    honeycomb_angle_deg = wrap_degrees(
        env_float(env, "MC_HONEYCOMB_ANGLE_DEG", 0.0), 60.0
    )
    polarization_angle_deg = wrap_degrees(
        env_float(env, "MC_POLARIZATION_ANGLE_DEG", 90.0), 180.0
    )
    ellipticity_angle_deg = env_float(
        env, "MC_ELLIPTICITY_ANGLE_DEG", 0.0
    )

    plasma_zmin = env_float(env, "MC_PLASMA_ZMIN_M", 40.0e-6)
    antenna_z = env_float(env, "MC_LASER_ANTENNA_Z_M", 37.0e-6)
    profile_t_peak = env_float(env, "MC_PROFILE_T_PEAK_S", 30.0e-15)
    post_plasma_margin = env_float(
        env, "MC_POST_PLASMA_MARGIN_M", 30.0e-6
    )

    positive = {
        "plasma_ion_density": plasma_ion_density,
        "cnt_radius": cnt_radius,
        "cnt_gap": cnt_gap,
        "laser_waist": laser_waist,
        "plasma_length": plasma_length,
        "halfwidth_x": halfwidth_x,
        "halfwidth_y": halfwidth_y,
        "post_plasma_margin": post_plasma_margin,
    }
    invalid = [name for name, value in positive.items() if value <= 0.0]
    if invalid:
        raise ValueError(f"positive parameters required: {invalid}")
    if halfwidth_x > 16.0e-6 + 1.0e-15 or halfwidth_y > 16.0e-6 + 1.0e-15:
        raise ValueError("plasma half-widths must not exceed 16 um for this grid/PML")
    if not -45.0 <= ellipticity_angle_deg <= 45.0:
        raise ValueError("MC_ELLIPTICITY_ANGLE_DEG must be within [-45, 45]")

    carbon_charge_state = 3
    spacing = 2.0 * cnt_radius + cnt_gap
    lattice_height = math.sqrt(3.0) * spacing / 2.0
    fill_fraction = honeycomb_fill_fraction(cnt_radius, cnt_gap)
    effective_density = effective_electron_density(
        plasma_ion_density,
        cnt_radius,
        cnt_gap,
        carbon_charge_state,
    )
    omega_p = math.sqrt(effective_density * Q_E**2 / (EPS0 * M_E))
    lambda_p = 2.0 * math.pi * C_LIGHT / omega_p

    wavelength = 0.8e-6
    omega_laser = 2.0 * math.pi * C_LIGHT / wavelength
    dispersion_term = 1.0 - (omega_p / omega_laser) ** 2
    if dispersion_term <= 0.0:
        raise ValueError("effective density is above the supported laser cutoff")
    moving_window_velocity = 0.95 * C_LIGHT * math.sqrt(dispersion_term)

    nx, ny, nz = 128, 128, 1024
    xmin, xmax = -20.0e-6, 20.0e-6
    ymin, ymax = -20.0e-6, 20.0e-6
    zmin, zmax = 0.0, 41.0e-6
    dx = (xmax - xmin) / nx
    dy = (ymax - ymin) / ny
    dz = (zmax - zmin) / nz
    timestep = yee_cfl_timestep(dx, dy, dz)
    plasma_zmax = plasma_zmin + plasma_length
    max_steps = final_step_count(
        plasma_zmax=plasma_zmax,
        post_plasma_margin=post_plasma_margin,
        initial_window_front=zmax,
        moving_window_velocity=moving_window_velocity,
        timestep=timestep,
    )

    return {
        "plasma_ion_density_m3": plasma_ion_density,
        "carbon_charge_state": carbon_charge_state,
        "effective_electron_density_m3": effective_density,
        "honeycomb_fill_fraction": fill_fraction,
        "cnt_radius_m": cnt_radius,
        "cnt_gap_m": cnt_gap,
        "lattice_spacing_m": spacing,
        "lattice_height_m": lattice_height,
        "honeycomb_angle_deg": honeycomb_angle_deg,
        "honeycomb_cos": math.cos(math.radians(honeycomb_angle_deg)),
        "honeycomb_sin": math.sin(math.radians(honeycomb_angle_deg)),
        "laser_waist_m": laser_waist,
        "wavelength_m": wavelength,
        "pulse_length_m": lambda_p,
        "pulse_duration_s": lambda_p / C_LIGHT,
        "profile_t_peak_s": profile_t_peak,
        "laser_antenna_z_m": antenna_z,
        "laser_centroid_z_m": antenna_z - C_LIGHT * profile_t_peak,
        "laser_focus_z_m": plasma_zmin + focus_offset,
        "polarization_angle_deg": polarization_angle_deg,
        "ellipticity_angle_deg": ellipticity_angle_deg,
        "laser_components": laser_component_parameters(
            polarization_angle_deg,
            ellipticity_angle_deg,
        ),
        "plasma_zmin_m": plasma_zmin,
        "plasma_zmax_m": plasma_zmax,
        "plasma_length_m": plasma_length,
        "plasma_halfwidth_x_m": halfwidth_x,
        "plasma_halfwidth_y_m": halfwidth_y,
        "post_plasma_margin_m": post_plasma_margin,
        "moving_window_velocity_m_s": moving_window_velocity,
        "timestep_s": timestep,
        "max_steps": max_steps,
        "grid": {
            "number_of_cells": [nx, ny, nz],
            "lower_bound": [xmin, ymin, zmin],
            "upper_bound": [xmax, ymax, zmax],
            "cell_size": [dx, dy, dz],
        },
        "write_field_diagnostic": env_bool(
            env, "MC_WRITE_FIELD_DIAGNOSTIC", False
        ),
        "dry_run": env_bool(env, "MC_DRY_RUN", False),
    }


def write_resolved_parameters(path: Path, resolved: Mapping[str, Any]) -> None:
    path.write_text(
        json.dumps(dict(resolved), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def main() -> None:
    from pywarpx import picmi

    resolved = resolve_parameters()
    grid_info = resolved["grid"]
    max_steps = int(resolved["max_steps"])

    grid = picmi.Cartesian3DGrid(
        number_of_cells=grid_info["number_of_cells"],
        lower_bound=grid_info["lower_bound"],
        upper_bound=grid_info["upper_bound"],
        lower_boundary_conditions=["open", "open", "open"],
        upper_boundary_conditions=["open", "open", "open"],
        lower_boundary_conditions_particles=["open", "open", "open"],
        upper_boundary_conditions_particles=["open", "open", "open"],
        moving_window_velocity=[
            0.0,
            0.0,
            resolved["moving_window_velocity_m_s"],
        ],
        warpx_start_moving_window_step=0,
        warpx_max_grid_size=64,
        warpx_blocking_factor=32,
        pml_cells=[8, 8, 16],
    )

    distribution_kwargs = {
        "density_expression": hexagonal_density_expression(),
        "D": resolved["lattice_spacing_m"],
        "r": resolved["cnt_radius_m"],
        "h": resolved["lattice_height_m"],
        "ct": resolved["honeycomb_cos"],
        "st": resolved["honeycomb_sin"],
        "lower_bound": [
            -resolved["plasma_halfwidth_x_m"],
            -resolved["plasma_halfwidth_y_m"],
            resolved["plasma_zmin_m"],
        ],
        "upper_bound": [
            resolved["plasma_halfwidth_x_m"],
            resolved["plasma_halfwidth_y_m"],
            resolved["plasma_zmax_m"],
        ],
        "fill_in": True,
    }
    electron_distribution = picmi.AnalyticDistribution(
        n0=(
            resolved["carbon_charge_state"]
            * resolved["plasma_ion_density_m3"]
        ),
        **distribution_kwargs,
    )
    ion_distribution = picmi.AnalyticDistribution(
        n0=resolved["plasma_ion_density_m3"],
        **distribution_kwargs,
    )

    electrons = picmi.Species(
        particle_type="electron",
        name="electrons",
        charge=-picmi.constants.q_e,
        initial_distribution=electron_distribution,
        warpx_add_real_attributes={"thermal_energy": "ux*ux+uy*uy"},
        warpx_add_int_attributes={
            "regionofinterest": (
                f"(x*x + y*y < {resolved['laser_waist_m']**2})"
            )
        },
    )
    carbon_ions = picmi.Species(
        particle_type="C",
        name="carbon_ions",
        initial_distribution=ion_distribution,
        charge_state=resolved["carbon_charge_state"],
        warpx_do_not_push=True,
    )

    laser_antenna = picmi.LaserAntenna(
        position=[0.0, 0.0, resolved["laser_antenna_z_m"]],
        normal_vector=[0.0, 0.0, 1.0],
    )
    intensity_si = 5.0e20 * 1.0e4
    e_max = math.sqrt(2.0 * intensity_si / (picmi.constants.ep0 * picmi.constants.c))
    lasers = []
    for component in resolved["laser_components"]:
        lasers.append(
            picmi.GaussianLaser(
                wavelength=resolved["wavelength_m"],
                waist=resolved["laser_waist_m"],
                duration=resolved["pulse_duration_s"],
                focal_position=[0.0, 0.0, resolved["laser_focus_z_m"]],
                centroid_position=[0.0, 0.0, resolved["laser_centroid_z_m"]],
                propagation_direction=[0.0, 0.0, 1.0],
                polarization_direction=component["polarization_direction"],
                E0=e_max * component["amplitude_fraction"],
                phi0=component["cep_rad"],
                name=component["name"],
                fill_in=False,
            )
        )

    solver = picmi.ElectromagneticSolver(
        grid=grid,
        method="Yee",
        cfl=1.0,
        divE_cleaning=0,
        warpx_do_pml_in_domain=True,
        warpx_pml_has_particles=False,
        warpx_do_pml_j_damping=True,
    )
    particle_diag = picmi.ParticleDiagnostic(
        name="diag_particles",
        period=0,
        species=[electrons],
        data_list=["position", "momentum", "weighting"],
        write_dir=".",
        warpx_file_prefix="3D",
        warpx_format="openpmd",
        warpx_openpmd_backend="h5",
        warpx_dump_last_timestep=True,
    )

    sim = picmi.Simulation(
        solver=solver,
        max_steps=max_steps,
        verbose=1,
        particle_shape="cubic",
        warpx_use_filter=1,
    )
    layout = picmi.GriddedLayout(
        grid=grid,
        n_macroparticle_per_cell=[1, 1, 1],
    )
    sim.add_species(electrons, layout=layout)
    sim.add_species(carbon_ions, layout=layout)
    for laser in lasers:
        sim.add_laser(laser, injection_method=laser_antenna)
    sim.add_diagnostic(particle_diag)

    if resolved["write_field_diagnostic"]:
        sim.add_diagnostic(
            picmi.FieldDiagnostic(
                name="diag_fields",
                grid=grid,
                period=0,
                data_list=["B", "E", "rho"],
                write_dir=".",
                warpx_file_prefix="fields3D",
                warpx_format="openpmd",
                warpx_openpmd_backend="h5",
                warpx_dump_last_timestep=True,
            )
        )

    if resolved["dry_run"]:
        sim.write_input_file(file_name="inputs_3d_picmi")
        write_resolved_parameters(Path("resolved_parameters.json"), resolved)
        print("[MULTICHANNEL] PICMI preflight completed; simulation not started")
        return

    sim.step(max_steps)


if __name__ == "__main__":
    main()
