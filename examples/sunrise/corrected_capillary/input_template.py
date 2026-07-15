#!/usr/bin/env python3
from __future__ import annotations

import json
import math
import os
import re
from pathlib import Path
from typing import Any, Mapping


C_LIGHT = 299792458.0
Q_E = 1.602176634e-19
M_E = 9.1093837139e-31
EPSILON_0 = 8.8541878128e-12

# Carlos's reference point: D=300 um, n0=4e18 cm^-3, matched spot
# diameter W_M=40.5 um. W_M is diagnostic only; the density uses the direct
# quasi-parabolic profile supplied by Carlos.
CHANNEL_REFERENCE_RADIUS_M = 150.0e-6
CHANNEL_REFERENCE_DENSITY_CM3 = 4.0e18
CHANNEL_REFERENCE_MATCHED_SPOT_DIAMETER_M = 40.5e-6
CHANNEL_QUADRATIC_COEFFICIENT = 0.33
CHANNEL_QUARTIC_COEFFICIENT = 0.4
LASER_REFERENCE_INTENSITY_FWHM_S = 30.0e-15

LASER_CASES = {
    # The spot values in Carlos's document are treated literally as 1/e^2
    # intensity diameters. PICMI GaussianLaser.waist is a radius.
    "f20": {"spot_diameter_m": 26.0e-6, "a0": 1.30},
    "f32": {"spot_diameter_m": 42.0e-6, "a0": 0.91},
    "f40": {"spot_diameter_m": 52.0e-6, "a0": 0.73},
}

REQUIRED_CONVENTION_ACK = "clpu_spot_diameter_and_30fs_intensity_fwhm_v1"


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


def env_int(
    environ: Mapping[str, str], name: str, default: int | None = None
) -> int:
    value = env_float(environ, name, None if default is None else float(default))
    if value != int(value):
        raise ValueError(f"{name} must be integer-like, got {value!r}")
    return int(value)


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


def channel_matched_spot_diameter_m(radius_m: float, n0_cm3: float) -> float:
    """Scale Carlos's diagnostic matched-spot diameter from its reference."""

    if radius_m <= 0.0 or n0_cm3 <= 0.0:
        raise ValueError("channel radius and on-axis density must be positive")
    return CHANNEL_REFERENCE_MATCHED_SPOT_DIAMETER_M * math.sqrt(
        radius_m / CHANNEL_REFERENCE_RADIUS_M
    ) * (CHANNEL_REFERENCE_DENSITY_CM3 / n0_cm3) ** 0.25


def channel_profile_multiplier(normalized_radius: float) -> float:
    u = float(normalized_radius)
    if not math.isfinite(u) or u < 0.0:
        raise ValueError("normalized_radius must be finite and non-negative")
    return (
        1.0
        + CHANNEL_QUADRATIC_COEFFICIENT * u**2
        + CHANNEL_QUARTIC_COEFFICIENT * u**4
    )


def resonant_intensity_fwhm_s(n0_cm3: float) -> float:
    density_m3 = float(n0_cm3) * 1.0e6
    if not math.isfinite(density_m3) or density_m3 <= 0.0:
        raise ValueError("n0_cm3 must be finite and positive")
    omega_p = math.sqrt(density_m3 * Q_E**2 / (M_E * EPSILON_0))
    return math.pi / omega_p


def mixture_density_factors(nitrogen_fraction: float) -> dict[str, float]:
    """Return charge-neutral H+/N5+ density factors at fixed initial n_e.

    The scan variable is the fraction of atomic nuclei that are nitrogen. It is
    numerically equal to the N2 molecular fraction in an H2/N2 mixture. The
    initial nitrogen state is N5+; ADK can release two further electrons.
    """

    fraction = float(nitrogen_fraction)
    if not 0.0 <= fraction <= 1.0:
        raise ValueError("nitrogen fraction must be within [0, 1]")
    nuclei_per_initial_electron = 1.0 / (1.0 + 4.0 * fraction)
    hydrogen = (1.0 - fraction) * nuclei_per_initial_electron
    nitrogen = fraction * nuclei_per_initial_electron
    return {
        "background_electron": 1.0,
        "hydrogen_ion": hydrogen,
        "nitrogen_ion": nitrogen,
        "initial_charge_balance": hydrogen + 5.0 * nitrogen,
        "maximum_extra_electron": 2.0 * nitrogen,
    }


def build_longitudinal_expression(
    front_ramp_length: float,
    plateau_length: float,
    back_ramp_length: float,
) -> tuple[str, dict[str, float]]:
    plasma_start_z = 0.0
    plateau_start_z = plasma_start_z + front_ramp_length
    plateau_end_z = plateau_start_z + plateau_length
    plasma_end_z = plateau_end_z + back_ramp_length
    expression = "0.0"
    if back_ramp_length > 0.0:
        expression = (
            "if((z >= plateau_end_z) and (z < plasma_end_z), "
            "(plasma_end_z-z)/back_ramp_length, "
            f"{expression})"
        )
    expression = (
        "if((z >= plateau_start_z) and (z < plateau_end_z), 1.0, "
        f"{expression})"
    )
    if front_ramp_length > 0.0:
        expression = (
            "if((z >= plasma_start_z) and (z < plateau_start_z), "
            "(z-plasma_start_z)/front_ramp_length, "
            f"{expression})"
        )
    return expression, {
        "plasma_start_z": plasma_start_z,
        "plateau_start_z": plateau_start_z,
        "plateau_end_z": plateau_end_z,
        "plasma_end_z": plasma_end_z,
        "front_ramp_length": front_ramp_length,
        "back_ramp_length": back_ramp_length,
    }


def build_electron_density_expression(
    *,
    plasma_kind: str,
    longitudinal_factor: str,
    has_front_ramp: bool,
    has_back_ramp: bool,
) -> str:
    if plasma_kind == "uni":
        profile = f"({longitudinal_factor})*n0"
    elif plasma_kind == "chan":
        plateau_profile = (
            "n0*"
            "(1.0+profile_c2*(x*x)/(r_cap*r_cap)+"
            "profile_c4*(x*x*x*x)/(r_cap*r_cap*r_cap*r_cap))"
        )
        profile = "0.0"
        if has_back_ramp:
            profile = (
                "if((z >= plateau_end_z) and (z < plasma_end_z), "
                "((plasma_end_z-z)/back_ramp_length)*n0, "
                f"{profile})"
            )
        profile = (
            "if((z >= plateau_start_z) and (z < plateau_end_z), "
            f"{plateau_profile}, {profile})"
        )
        if has_front_ramp:
            profile = (
                "if((z >= plasma_start_z) and (z < plateau_start_z), "
                "((z-plasma_start_z)/front_ramp_length)*n0, "
                f"{profile})"
            )
    else:
        raise ValueError("density expression requires plasma_kind=uni or chan")
    return f"density_scale*if((x*x <= r_cap*r_cap), {profile}, 0.0)"


def expression_constants(expression: str, constants: Mapping[str, float]) -> dict[str, float]:
    return {
        name: value
        for name, value in constants.items()
        if re.search(rf"\b{re.escape(name)}\b", expression)
    }


def resolve_parameters(environ: Mapping[str, str] | None = None) -> dict[str, Any]:
    env = os.environ if environ is None else environ
    case_id = str(env.get("CAP_CASE_ID", "manual"))
    case_name = str(env.get("CAP_CASE_NAME", f"manual_{case_id}"))
    laser_case = str(env.get("CAP_LASER_CASE", "f32")).strip().lower()
    plasma_kind = str(env.get("CAP_PLASMA_KIND", "chan")).strip().lower()
    if laser_case not in LASER_CASES:
        raise ValueError(f"CAP_LASER_CASE must be one of {sorted(LASER_CASES)}")
    if plasma_kind not in {"vac", "uni", "chan"}:
        raise ValueError("CAP_PLASMA_KIND must be vac, uni, or chan")

    convention_ack = str(env.get("CAP_INPUT_CONVENTIONS_ACK", "")).strip()
    if env_bool(env, "CAP_REQUIRE_CONVENTION_ACK", False) and (
        convention_ack != REQUIRED_CONVENTION_ACK
    ):
        raise ValueError(
            "production preflight blocked: acknowledge that 26/42/52 um are "
            "1/e^2 intensity diameters and 30 fs is interpreted as intensity FWHM; "
            f"then set CAP_INPUT_CONVENTIONS_ACK={REQUIRED_CONVENTION_ACK}"
        )

    spot_definition = str(
        env.get("CAP_LASER_SPOT_DEFINITION", "diameter_1e2_intensity")
    ).strip().lower()
    if spot_definition != "diameter_1e2_intensity":
        raise ValueError(
            "this corrected campaign requires CAP_LASER_SPOT_DEFINITION="
            "diameter_1e2_intensity"
        )
    laser = LASER_CASES[laser_case]
    laser_waist_m = 0.5 * float(laser["spot_diameter_m"])
    laser_waist_m = env_float(env, "CAP_LASER_WAIST_M", laser_waist_m)
    intensity_fwhm_s = env_float(
        env,
        "CAP_LASER_INTENSITY_FWHM_S",
        LASER_REFERENCE_INTENSITY_FWHM_S,
    )
    if intensity_fwhm_s <= 0.0:
        raise ValueError("CAP_LASER_INTENSITY_FWHM_S must be positive")
    laser_a0_reference = float(laser["a0"])
    laser_a0_energy_scaled = laser_a0_reference * math.sqrt(
        LASER_REFERENCE_INTENSITY_FWHM_S / intensity_fwhm_s
    )
    laser_a0 = env_float(env, "CAP_LASER_A0", laser_a0_energy_scaled)
    picmi_duration_s = intensity_fwhm_s / math.sqrt(2.0 * math.log(2.0))

    n0_cm3 = env_float(env, "CAP_N0_CM3", 4.0e18)
    radius_m = env_float(env, "CAP_RADIUS_M", 150.0e-6)
    nitrogen_fraction = env_float(env, "CAP_NITROGEN_DOPANT_FRACTION", 0.0)
    maximum_nitrogen_fraction = env_float(
        env, "CAP_MAX_NITROGEN_DOPANT_FRACTION", 0.01
    )
    if n0_cm3 <= 0.0 or radius_m <= 0.0:
        raise ValueError("CAP_N0_CM3 and CAP_RADIUS_M must be positive")
    if not 0.0 <= nitrogen_fraction <= maximum_nitrogen_fraction:
        raise ValueError(
            "CAP_NITROGEN_DOPANT_FRACTION must lie within "
            f"[0, {maximum_nitrogen_fraction:g}]"
        )

    n0_m3 = n0_cm3 * 1.0e6
    matched_spot_diameter_m = channel_matched_spot_diameter_m(radius_m, n0_cm3)
    n_edge_m3 = (
        n0_m3 * channel_profile_multiplier(1.0)
        if plasma_kind == "chan"
        else n0_m3
    )
    quadratic_density_coefficient_m5 = (
        n0_m3 * CHANNEL_QUADRATIC_COEFFICIENT / radius_m**2
        if plasma_kind == "chan"
        else 0.0
    )
    resonant_fwhm_s = resonant_intensity_fwhm_s(n0_cm3)
    pulse_resonance_factor = intensity_fwhm_s / resonant_fwhm_s
    requested_resonance_factor = env_float(
        env, "CAP_PULSE_RESONANCE_FACTOR", pulse_resonance_factor
    )
    if not math.isclose(
        requested_resonance_factor,
        pulse_resonance_factor,
        rel_tol=1.0e-8,
        abs_tol=1.0e-12,
    ):
        raise ValueError(
            "CAP_PULSE_RESONANCE_FACTOR is inconsistent with density and "
            "CAP_LASER_INTENSITY_FWHM_S"
        )
    mixture = mixture_density_factors(nitrogen_fraction)
    if abs(mixture["initial_charge_balance"] - 1.0) > 1.0e-12:
        raise RuntimeError("internal H+/N5+ charge-neutrality failure")

    long_profile = str(env.get("CAP_LONG_PROFILE", "both")).strip().lower()
    plateau_length_m = env_float(env, "CAP_PLATEAU_LENGTH_M", 5.0e-3)
    ramp_length_m = env_float(env, "CAP_RAMP_LENGTH_M", 5.0e-3)
    if long_profile == "both":
        front_ramp_m = env_float(env, "CAP_FRONT_RAMP_LENGTH_M", ramp_length_m)
        back_ramp_m = env_float(env, "CAP_BACK_RAMP_LENGTH_M", ramp_length_m)
    elif long_profile == "front":
        front_ramp_m = env_float(env, "CAP_FRONT_RAMP_LENGTH_M", ramp_length_m)
        back_ramp_m = 0.0
    elif long_profile == "square":
        front_ramp_m = 0.0
        back_ramp_m = 0.0
    else:
        raise ValueError("CAP_LONG_PROFILE must be both, front, or square")
    if min(plateau_length_m, front_ramp_m, back_ramp_m) < 0.0 or plateau_length_m == 0.0:
        raise ValueError("longitudinal lengths must be non-negative with a positive plateau")

    longitudinal_factor, longitudinal = build_longitudinal_expression(
        front_ramp_m, plateau_length_m, back_ramp_m
    )
    density_expression = (
        "0.0"
        if plasma_kind == "vac"
        else build_electron_density_expression(
            plasma_kind=plasma_kind,
            longitudinal_factor=longitudinal_factor,
            has_front_ramp=front_ramp_m > 0.0,
            has_back_ramp=back_ramp_m > 0.0,
        )
    )

    focus_offset_mm = env_float(
        env, "CAP_FOCUS_OFFSET_FROM_PLATEAU_START_MM", 0.0
    )
    focus_z_m = longitudinal["plateau_start_z"] + focus_offset_mm * 1.0e-3
    particle_load_offset_m = env_float(env, "CAP_PARTICLE_LOAD_Z_OFFSET_M", 10e-6)
    particle_load_zmin_m = longitudinal["plasma_start_z"] + particle_load_offset_m

    nr = env_int(env, "CAP_NR", 192)
    nz = env_int(env, "CAP_NZ", 1536)
    rmax_m = env_float(env, "CAP_RMAX_M", 180.0e-6)
    zmin_m = env_float(env, "CAP_ZMIN_M", -240.0e-6)
    zmax_m = env_float(env, "CAP_ZMAX_M", 20.0e-6)
    blocking_factor = env_int(env, "CAP_BLOCKING_FACTOR", 32)
    max_grid_size = env_int(env, "CAP_MAX_GRID_SIZE", 64)
    if rmax_m <= radius_m:
        raise ValueError("CAP_RMAX_M must exceed CAP_RADIUS_M")
    if nr <= 0 or nz <= 0 or blocking_factor <= 0:
        raise ValueError("grid cell counts and blocking factor must be positive")
    if nr % blocking_factor != 0:
        raise ValueError("CAP_NR must be divisible by CAP_BLOCKING_FACTOR")
    radial_cell_m = rmax_m / nr
    if radial_cell_m > env_float(env, "CAP_MAX_RADIAL_CELL_M", 1.5e-6):
        raise ValueError("radial cell size exceeds CAP_MAX_RADIAL_CELL_M")

    total_profile_length_m = (
        longitudinal["plasma_end_z"] - longitudinal["plasma_start_z"]
    )
    baseline_steps = env_int(env, "CAP_BASELINE_STEPS_PER_5MM", 64000)
    default_steps = int(math.ceil(baseline_steps * total_profile_length_m / 5.0e-3))
    max_steps = env_int(env, "CAP_MAX_STEPS", default_steps)
    target_field_frames = env_int(env, "CAP_TARGET_FIELD_FRAMES", 48)
    if max_steps <= 0 or target_field_frames < 2:
        raise ValueError("max_steps must be positive and target field frames >= 2")
    field_period = env_int(
        env,
        "CAP_FIELD_DIAG_PERIOD",
        int(math.ceil(max_steps / (target_field_frames - 1))),
    )

    te_ev = env_float(env, "CAP_TE_EV", 5.0)
    use_te = env_bool(env, "CAP_USE_TE", True)
    electron_thermal_speed_m_s = math.sqrt(te_ev * Q_E / M_E) if use_te else 0.0
    n_modes = env_int(env, "CAP_NMODES", 2)
    ppc = [
        env_int(env, "CAP_PPC_R", 1),
        env_int(env, "CAP_PPC_THETA", max(2 * n_modes, 4)),
        env_int(env, "CAP_PPC_Z", 1),
    ]

    resolved = {
        "schema_version": 1,
        "physics_model_id": "clpu_carlos_plateau_quasiparabolic_n5_adk_v3",
        "case_id": case_id,
        "case_name": case_name,
        "laser_case": laser_case,
        "plasma_kind": plasma_kind,
        "laser_spot_definition": spot_definition,
        "input_conventions_ack": convention_ack,
        "laser_spot_document_value_m": laser["spot_diameter_m"],
        "laser_waist_radius_m": laser_waist_m,
        "laser_a0_reference_30fs": laser_a0_reference,
        "laser_a0_constant_energy_scaled": laser_a0_energy_scaled,
        "laser_a0": laser_a0,
        "laser_intensity_fwhm_s": intensity_fwhm_s,
        "laser_picmi_duration_s": picmi_duration_s,
        "laser_resonant_intensity_fwhm_s": resonant_fwhm_s,
        "laser_pulse_resonance_factor": pulse_resonance_factor,
        "laser_pulse_energy_policy": "constant_0p6J_a0_scales_tau_minus_half",
        "laser_wavelength_m": env_float(env, "CAP_LAMBDA0_M", 0.8e-6),
        "laser_antenna_z_m": env_float(env, "CAP_ANTENNA_Z_M", -20.0e-6),
        "laser_profile_t_peak_s": env_float(
            env, "CAP_PROFILE_T_PEAK_S", intensity_fwhm_s
        ),
        "n0_cm3": n0_cm3,
        "n0_m3": n0_m3,
        "radius_m": radius_m,
        "diameter_m": 2.0 * radius_m,
        "matched_spot_model": "Carlos_WM_diameter_reference_diagnostic_only",
        "matched_spot_diameter_m": matched_spot_diameter_m,
        "channel_profile_model": "n0_times_1_plus_0p33_r2_plus_0p4_r4",
        "channel_profile_longitudinal_scope": "plateau_only",
        "ramp_radial_model": "uniform_inside_capillary",
        "channel_profile_quadratic_coefficient": CHANNEL_QUADRATIC_COEFFICIENT,
        "channel_profile_quartic_coefficient": CHANNEL_QUARTIC_COEFFICIENT,
        "channel_quadratic_density_coefficient_m5": quadratic_density_coefficient_m5,
        "n_edge_m3": n_edge_m3,
        "density_expression": density_expression,
        "nitrogen_fraction_atomic_nuclei": nitrogen_fraction,
        "nitrogen_initial_charge_state": 5,
        "ionization_model": "ADK" if nitrogen_fraction > 0.0 else "disabled_zero_fraction",
        "electron_species_provenance": {
            "combined_metric_scope": "all_electrons",
            "initial_free_electrons": "preionized_background_electrons",
            "nitrogen_adk_products": "nitrogen_ionized_electrons",
        },
        "mixture_density_factors_relative_to_initial_electron_density": mixture,
        "long_profile": long_profile,
        "plateau_length_m": plateau_length_m,
        **longitudinal,
        "particle_load_zmin_m": particle_load_zmin_m,
        "focus_offset_from_plateau_start_mm": focus_offset_mm,
        "focus_z_m": focus_z_m,
        "grid": {
            "nr": nr,
            "nz": nz,
            "rmax_m": rmax_m,
            "zmin_m": zmin_m,
            "zmax_m": zmax_m,
            "radial_cell_m": radial_cell_m,
            "blocking_factor": blocking_factor,
            "max_grid_size": max_grid_size,
            "n_azimuthal_modes": n_modes,
        },
        "max_steps": max_steps,
        "field_diagnostic_period": field_period,
        "target_field_frames": target_field_frames,
        "particle_diagnostic_policy": "final_timestep_only",
        "electron_temperature_eV": te_ev if use_te else 0.0,
        "electron_thermal_speed_m_s": electron_thermal_speed_m_s,
        "macroparticles_per_cell_r_theta_z": ppc,
        "dry_run": env_bool(env, "CAP_DRY_RUN", False),
    }
    return resolved


def write_resolved_parameters(path: Path, resolved: Mapping[str, Any]) -> None:
    path.write_text(
        json.dumps(dict(resolved), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


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
        cfl=1.0,
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
    all_species: list[Any] = []
    if resolved["plasma_kind"] != "vac":
        expression = resolved["density_expression"]
        constants = {
            "n0": resolved["n0_m3"],
            "profile_c2": resolved["channel_profile_quadratic_coefficient"],
            "profile_c4": resolved["channel_profile_quartic_coefficient"],
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
            **expression_constants(expression, constants),
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

        preionized_background_electrons = picmi.Species(
            particle_type="electron",
            name="preionized_background_electrons",
            initial_distribution=distribution(1.0, thermal=True),
        )
        hydrogen_ions = picmi.Species(
            particle_type="H",
            name="hydrogen_ions",
            charge_state=1,
            initial_distribution=distribution(
                resolved[
                    "mixture_density_factors_relative_to_initial_electron_density"
                ]["hydrogen_ion"]
            ),
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
        all_species.extend(
            [
                preionized_background_electrons,
                hydrogen_ions,
                nitrogen_ionized_electrons,
            ]
        )

        if resolved["nitrogen_fraction_atomic_nuclei"] > 0.0:
            nitrogen_ions = picmi.Species(
                particle_type="N",
                name="nitrogen_ions",
                charge_state=resolved["nitrogen_initial_charge_state"],
                initial_distribution=distribution(
                    resolved[
                        "mixture_density_factors_relative_to_initial_electron_density"
                    ]["nitrogen_ion"]
                ),
                warpx_do_not_push=True,
            )
            sim.add_species(nitrogen_ions, layout=layout)
            all_species.append(nitrogen_ions)
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
                period=0,
                species=electron_species,
                data_list=["position", "momentum", "weighting"],
                write_dir="diags",
                warpx_format="openpmd",
                warpx_openpmd_backend="h5",
                warpx_dump_last_timestep=True,
            )
        )

    write_resolved_parameters(Path("resolved_parameters.json"), resolved)
    sim.write_input_file(file_name=f"inputs_capillary_{resolved['case_name']}")
    print(json.dumps(resolved, indent=2, sort_keys=True))
    if resolved["dry_run"]:
        print("[CLPU] PICMI preflight completed; simulation not started")
        return
    sim.initialize_inputs()
    sim.initialize_warpx()
    sim.step(resolved["max_steps"])


if __name__ == "__main__":
    main()
