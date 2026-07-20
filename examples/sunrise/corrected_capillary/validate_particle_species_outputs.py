#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import re
from pathlib import Path
from typing import Any

import imageio.v2 as imageio
import numpy as np


PLOT_PREFIXES = (
    "energy_spectrum_",
    "longitudinal_phase_space_hot_",
    "longitudinal_energy_space_hot_",
    "transverse_phase_space_x_thetax_hot_",
    "transverse_phase_space_y_thetay_hot_",
    "transverse_real_space_xy_hot_",
    "transverse_divergence_thetax_thetay_hot_",
)


def filename_token(value: str) -> str:
    token = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(value)).strip("._-")
    if not token:
        raise ValueError(f"scope has no filename-safe characters: {value!r}")
    return token


def read_scope_rows(path: Path) -> list[dict[str, str]]:
    if not path.is_file() or path.stat().st_size <= 0:
        raise ValueError(f"missing or empty particle table: {path}")
    with path.open(newline="", encoding="utf-8-sig") as stream:
        rows = list(csv.DictReader(stream))
    if not rows or "species_scope" not in rows[0]:
        raise ValueError(f"particle table lacks species_scope rows: {path}")
    return rows


def validate_scope_table(
    path: Path,
    *,
    expected_scopes: list[str],
    exactly_one_row_per_scope: bool,
    require_equal_rows_per_scope: bool = False,
) -> dict[str, Any]:
    rows = read_scope_rows(path)
    counts = {
        scope: sum(row.get("species_scope", "") == scope for row in rows)
        for scope in expected_scopes
    }
    observed = {row.get("species_scope", "") for row in rows}
    expected = set(expected_scopes)
    if observed != expected:
        raise ValueError(
            f"unexpected species scopes in {path}: observed={sorted(observed)}, "
            f"expected={sorted(expected)}"
        )
    if exactly_one_row_per_scope and any(count != 1 for count in counts.values()):
        raise ValueError(f"expected one row per species scope in {path}: {counts}")
    if any(count < 1 for count in counts.values()):
        raise ValueError(f"missing species rows in {path}: {counts}")
    if require_equal_rows_per_scope and len(set(counts.values())) != 1:
        raise ValueError(f"species scopes have unequal row counts in {path}: {counts}")
    if exactly_one_row_per_scope and rows[0].get("species_scope") != "all_electrons":
        raise ValueError(f"first summary row is not all_electrons in {path}")
    return {
        "path": str(path),
        "rows": len(rows),
        "scope_counts": counts,
        "status": "ok",
    }


def _single_text_value(
    rows: list[dict[str, str]],
    *,
    column: str,
    path: Path,
) -> str:
    values = {str(row.get(column, "")).strip() for row in rows}
    if "" in values or len(values) != 1:
        raise ValueError(
            f"particle table has missing or inconsistent {column!r} in {path}: "
            f"{sorted(values)}"
        )
    return next(iter(values))


def _single_int_value(
    rows: list[dict[str, str]],
    *,
    column: str,
    path: Path,
) -> int:
    text = _single_text_value(rows, column=column, path=path)
    try:
        value = float(text)
    except ValueError as exc:
        raise ValueError(f"particle table has non-numeric {column!r} in {path}") from exc
    if not np.isfinite(value) or value != int(value):
        raise ValueError(f"particle table has non-integer {column!r} in {path}")
    return int(value)


def validate_selection_contract(
    *,
    summary_path: Path,
    related_table_paths: list[Path],
    resolved_parameters_path: Path,
) -> dict[str, Any]:
    """Validate that reduced metrics describe the intended plateau-exit dump.

    Empty particle populations remain physically valid.  This contract checks
    timing and provenance only, so a real zero-particle result passes while a
    distant final dump cannot masquerade as the plateau-exit measurement.
    """

    if not resolved_parameters_path.is_file():
        raise ValueError(
            f"missing resolved simulation parameters: {resolved_parameters_path}"
        )
    try:
        resolved = json.loads(resolved_parameters_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(
            f"invalid resolved simulation parameters: {resolved_parameters_path}"
        ) from exc

    required_resolved = [
        "particle_diagnostic_policy",
        "particle_diagnostic_iteration",
        "particle_diagnostic_intervals",
        "particle_diagnostic_dump_last_timestep",
        "particle_diagnostic_filter_expression",
        "particle_diagnostic_min_energy_MeV",
        "particle_diagnostic_forward_only",
        "particle_diagnostic_target",
        "particle_diagnostic_target_distance_m",
        "particle_diagnostic_target_iteration_unaligned",
        "particle_diagnostic_alignment_error_steps",
        "particle_diagnostic_aligned_distance_m",
        "moving_window_step_distance_m",
        "time_step_model",
        "field_diagnostic_period",
        "plasma_start_z",
        "plateau_end_z",
    ]
    missing = [key for key in required_resolved if key not in resolved]
    if missing:
        raise ValueError(
            f"resolved simulation parameters lack particle provenance {missing}"
        )
    if resolved["particle_diagnostic_policy"] != (
        "single_plateau_exit_field_aligned_filtered_v1"
    ):
        raise ValueError("unexpected particle diagnostic policy")
    if resolved["particle_diagnostic_dump_last_timestep"] is not False:
        raise ValueError("particle diagnostic must not force a final-timestep dump")

    if resolved["particle_diagnostic_target"] != "plateau_exit":
        raise ValueError("particle diagnostic target must be the plateau exit")
    minimum_energy_mev = float(resolved["particle_diagnostic_min_energy_MeV"])
    if minimum_energy_mev != 5.0:
        raise ValueError("particle diagnostic must use the reviewed 5 MeV threshold")
    if resolved["particle_diagnostic_forward_only"] is not True:
        raise ValueError("particle diagnostic must retain only forward electrons")

    expected_filter = (
        "(uz > 0.0)*((sqrt(1.0+ux*ux+uy*uy+uz*uz)-1.0)*"
        "0.51099895 >= 5)"
    )
    if str(resolved["particle_diagnostic_filter_expression"]) != expected_filter:
        raise ValueError("resolved particle diagnostic filter is not the reviewed filter")

    expected_iteration = int(resolved["particle_diagnostic_iteration"])
    expected_intervals = f"{expected_iteration}:{expected_iteration}"
    if str(resolved["particle_diagnostic_intervals"]) != expected_intervals:
        raise ValueError(
            "resolved particle diagnostic interval is inconsistent with its iteration"
        )
    field_period = int(resolved["field_diagnostic_period"])
    if field_period <= 0 or expected_iteration % field_period != 0:
        raise ValueError(
            "resolved particle iteration is not a regular field-diagnostic frame"
        )

    start_m = float(resolved["plasma_start_z"])
    plateau_end_m = float(resolved["plateau_end_z"])
    target_distance_m = plateau_end_m - start_m
    if not math.isclose(
        float(resolved["particle_diagnostic_target_distance_m"]),
        target_distance_m,
        rel_tol=0.0,
        abs_tol=1.0e-15,
    ):
        raise ValueError("resolved particle target distance is not the plateau exit")
    if resolved["time_step_model"] != (
        "WarpX_CylindricalYeeAlgorithm_ComputeMaxDt"
    ):
        raise ValueError("unexpected simulation timestep model")
    step_distance_m = float(resolved["moving_window_step_distance_m"])
    if not math.isfinite(step_distance_m) or step_distance_m <= 0.0:
        raise ValueError("resolved moving-window step distance is invalid")
    expected_unaligned = int(math.ceil(target_distance_m / step_distance_m))
    unaligned = int(resolved["particle_diagnostic_target_iteration_unaligned"])
    if unaligned != expected_unaligned:
        raise ValueError("resolved unaligned particle target iteration is inconsistent")
    if int(resolved["particle_diagnostic_alignment_error_steps"]) != (
        expected_iteration - unaligned
    ):
        raise ValueError("resolved particle alignment error is inconsistent")
    expected_aligned_distance_m = expected_iteration * step_distance_m
    if not math.isclose(
        float(resolved["particle_diagnostic_aligned_distance_m"]),
        expected_aligned_distance_m,
        rel_tol=1.0e-14,
        abs_tol=1.0e-15,
    ):
        raise ValueError("resolved aligned particle distance is inconsistent")

    summary_rows = read_scope_rows(summary_path)
    selection_mode = _single_text_value(
        summary_rows,
        column="selection_mode",
        path=summary_path,
    )
    if selection_mode != "exit":
        raise ValueError(
            f"particle summary selection_mode must be 'exit', got {selection_mode!r}"
        )

    selected = _single_int_value(
        summary_rows,
        column="selected_particle_iteration",
        path=summary_path,
    )
    target = _single_int_value(
        summary_rows,
        column="target_guiding_iteration",
        path=summary_path,
    )
    delta = _single_int_value(
        summary_rows,
        column="target_iteration_delta",
        path=summary_path,
    )
    maximum_delta = _single_int_value(
        summary_rows,
        column="maximum_target_iteration_delta",
        path=summary_path,
    )
    alignment_status = _single_text_value(
        summary_rows,
        column="target_iteration_alignment_status",
        path=summary_path,
    )

    if selected != expected_iteration:
        raise ValueError(
            "particle analysis selected an iteration other than the resolved "
            f"diagnostic: selected={selected}, expected={expected_iteration}"
        )
    if selected - target != delta:
        raise ValueError(
            "particle summary has inconsistent target iteration metadata: "
            f"selected={selected}, target={target}, delta={delta}"
        )
    if maximum_delta < 0 or abs(delta) > maximum_delta:
        raise ValueError(
            "particle analysis is not aligned with the requested exit frame: "
            f"delta={delta}, allowed={maximum_delta}"
        )
    if alignment_status != "ok":
        raise ValueError(
            f"particle target alignment status is not ok: {alignment_status!r}"
        )

    available_count = _single_int_value(
        summary_rows,
        column="n_available_particle_iterations",
        path=summary_path,
    )
    available_min = _single_int_value(
        summary_rows,
        column="available_particle_iterations_min",
        path=summary_path,
    )
    available_max = _single_int_value(
        summary_rows,
        column="available_particle_iterations_max",
        path=summary_path,
    )
    if available_count != 1 or available_min != selected or available_max != selected:
        raise ValueError(
            "particle diagnostic must contain exactly the one resolved exit iteration: "
            f"count={available_count}, min={available_min}, max={available_max}, "
            f"selected={selected}"
        )

    for path in related_table_paths:
        rows = read_scope_rows(path)
        mode = _single_text_value(rows, column="selection_mode", path=path)
        iteration = _single_int_value(
            rows,
            column="selected_particle_iteration",
            path=path,
        )
        if mode != selection_mode or iteration != selected:
            raise ValueError(
                f"particle table selection does not match summary in {path}: "
                f"mode={mode!r}, iteration={iteration}"
            )

    return {
        "status": "ok",
        "selection_mode": selection_mode,
        "target_guiding_iteration": target,
        "selected_particle_iteration": selected,
        "target_iteration_delta": delta,
        "maximum_target_iteration_delta": maximum_delta,
        "n_available_particle_iterations": available_count,
        "resolved_parameters": str(resolved_parameters_path),
        "particle_diagnostic_policy": resolved["particle_diagnostic_policy"],
        "empty_particle_populations_allowed": True,
    }


def validate_png(path: Path) -> dict[str, Any]:
    if not path.is_file() or path.stat().st_size < 10_000:
        raise ValueError(f"missing or implausibly small particle plot: {path}")
    array = np.asarray(imageio.imread(path))
    if array.ndim not in {2, 3} or array.size == 0:
        raise ValueError(f"invalid decoded PNG shape in {path}: {array.shape}")
    if array.shape[0] < 100 or array.shape[1] < 100:
        raise ValueError(f"decoded PNG is implausibly small in {path}: {array.shape}")
    if not np.isfinite(array).all():
        raise ValueError(f"invalid decoded PNG values in {path}")
    return {
        "path": str(path),
        "bytes": path.stat().st_size,
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "shape": [int(value) for value in array.shape],
        "status": "ok",
    }


def validate_scope_plots(
    plots_dir: Path,
    *,
    expected_scopes: list[str],
) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    iteration_tokens: set[str] = set()
    for scope in expected_scopes:
        scope_prefix = "" if scope == "all_electrons" else f"{filename_token(scope)}_"
        for plot_prefix in PLOT_PREFIXES:
            matches = sorted(plots_dir.glob(f"{plot_prefix}{scope_prefix}it????????.png"))
            if len(matches) != 1:
                raise ValueError(
                    f"expected exactly one plot for scope={scope!r}, "
                    f"kind={plot_prefix!r}; found {len(matches)} in {plots_dir}"
                )
            match = matches[0]
            token_match = re.search(r"(it\d{8})\.png$", match.name)
            if token_match is None:
                raise ValueError(f"plot lacks an iteration token: {match}")
            iteration_tokens.add(token_match.group(1))
            records.append(
                {
                    "species_scope": scope,
                    "plot_kind": plot_prefix.rstrip("_"),
                    "iteration": int(token_match.group(1)[2:]),
                    **validate_png(match),
                }
            )
    if len(iteration_tokens) != 1:
        raise ValueError(
            f"particle plots do not share one selected iteration: {sorted(iteration_tokens)}"
        )
    return records


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--particle-outdir", required=True)
    parser.add_argument("--resolved-parameters", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--expected-scope", action="append", required=True)
    args = parser.parse_args()

    scopes = list(args.expected_scope)
    if len(scopes) != len(set(scopes)) or scopes[0] != "all_electrons":
        raise ValueError("expected scopes must be unique and start with all_electrons")

    outdir = Path(args.particle_outdir)
    summary_path = outdir / "particle_summary.csv"
    soft50_path = outdir / "particle_soft50_curves.csv"
    acceptance_path = outdir / "particle_acceptance_curves.csv"
    tables = [
        validate_scope_table(
            summary_path,
            expected_scopes=scopes,
            exactly_one_row_per_scope=True,
        ),
        validate_scope_table(
            soft50_path,
            expected_scopes=scopes,
            exactly_one_row_per_scope=False,
            require_equal_rows_per_scope=True,
        ),
        validate_scope_table(
            acceptance_path,
            expected_scopes=scopes,
            exactly_one_row_per_scope=False,
            require_equal_rows_per_scope=True,
        ),
    ]
    selection = validate_selection_contract(
        summary_path=summary_path,
        related_table_paths=[soft50_path, acceptance_path],
        resolved_parameters_path=Path(args.resolved_parameters),
    )
    plots = validate_scope_plots(outdir / "plots", expected_scopes=scopes)
    plot_iterations = {int(record["iteration"]) for record in plots}
    if plot_iterations != {selection["selected_particle_iteration"]}:
        raise ValueError(
            "particle plot iteration does not match validated selection: "
            f"plots={sorted(plot_iterations)}, "
            f"selected={selection['selected_particle_iteration']}"
        )
    payload = {
        "schema_version": 2,
        "status": "ok",
        "expected_scopes": scopes,
        "selection": selection,
        "tables": tables,
        "plots": plots,
    }
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(
        f"[PARTICLE] validated {len(scopes)} scopes and {len(plots)} plots -> {output}"
    )


if __name__ == "__main__":
    main()
