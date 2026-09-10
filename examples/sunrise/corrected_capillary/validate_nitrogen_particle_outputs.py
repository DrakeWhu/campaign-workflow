#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import math
import re
from pathlib import Path
from typing import Any, Iterable


EXPECTED_SCOPES = [
    "all_electrons",
    "preionized_background_electrons",
    "nitrogen_ionized_electrons",
]
EXIT_TARGET_KEYS = {
    "plateau_exit": "plateau_exit",
    "capillary_exit": "capillary_exit",
}
PLOT_PREFIXES = (
    "energy_spectrum_",
    "longitudinal_phase_space_hot_",
    "longitudinal_energy_space_hot_",
    "transverse_phase_space_x_thetax_hot_",
    "transverse_phase_space_y_thetay_hot_",
    "transverse_real_space_xy_hot_",
    "transverse_divergence_thetax_thetay_hot_",
)


def _read_csv(path: Path) -> list[dict[str, str]]:
    if not path.is_file() or path.stat().st_size <= 0:
        raise ValueError(f"missing or empty particle table: {path}")
    with path.open(newline="", encoding="utf-8-sig") as stream:
        rows = list(csv.DictReader(stream))
    if not rows:
        raise ValueError(f"particle table has no rows: {path}")
    return rows


def _text(row: dict[str, str], column: str, *, path: Path) -> str:
    value = str(row.get(column, "")).strip()
    if not value:
        raise ValueError(f"particle table lacks {column!r}: {path}")
    return value


def _integer(row: dict[str, str], column: str, *, path: Path) -> int:
    text = _text(row, column, path=path)
    try:
        value = float(text)
    except ValueError as exc:
        raise ValueError(f"particle table has non-numeric {column!r}: {path}") from exc
    if not math.isfinite(value) or value != int(value):
        raise ValueError(f"particle table has non-integer {column!r}: {path}")
    return int(value)


def _float(row: dict[str, str], column: str, *, path: Path) -> float:
    text = _text(row, column, path=path)
    try:
        value = float(text)
    except ValueError as exc:
        raise ValueError(f"particle table has non-numeric {column!r}: {path}") from exc
    if not math.isfinite(value):
        raise ValueError(f"particle table has non-finite {column!r}: {path}")
    return value


def _scope_order(rows: Iterable[dict[str, str]]) -> list[str]:
    order: list[str] = []
    for row in rows:
        scope = str(row.get("species_scope", "")).strip()
        if scope and scope not in order:
            order.append(scope)
    return order


def _require_scope_set(rows: list[dict[str, str]], *, path: Path) -> None:
    observed = _scope_order(rows)
    if observed != EXPECTED_SCOPES:
        raise ValueError(
            f"unexpected species scope order in {path}: "
            f"observed={observed}, expected={EXPECTED_SCOPES}"
        )
    counts = {
        scope: sum(str(row.get("species_scope", "")).strip() == scope for row in rows)
        for scope in EXPECTED_SCOPES
    }
    if any(count < 1 for count in counts.values()):
        raise ValueError(f"missing species scope rows in {path}: {counts}")


def _require_table_selection(
    rows: list[dict[str, str]],
    *,
    path: Path,
    expected_iteration: int,
) -> None:
    for row in rows:
        if _text(row, "selection_mode", path=path) != "exit":
            raise ValueError(f"selection_mode must remain 'exit' in {path}")
        if _integer(row, "selected_particle_iteration", path=path) != expected_iteration:
            raise ValueError(
                f"particle table does not describe exact target iteration {expected_iteration}: {path}"
            )


def _require_summary(
    path: Path,
    *,
    expected_iteration: int,
    expected_target_key: str,
) -> list[dict[str, str]]:
    rows = _read_csv(path)
    _require_scope_set(rows, path=path)
    if len(rows) != len(EXPECTED_SCOPES):
        raise ValueError(f"expected exactly one summary row per scope in {path}")
    _require_table_selection(rows, path=path, expected_iteration=expected_iteration)

    for row in rows:
        if _text(row, "particle_exit_selection_policy", path=path) != "exact_resolved_v1":
            raise ValueError(f"unexpected particle exit selection policy in {path}")
        if _text(row, "resolved_particle_target_key", path=path) != expected_target_key:
            raise ValueError(f"wrong resolved particle target key in {path}")
        target = _integer(row, "target_particle_iteration", path=path)
        delta = _integer(row, "target_iteration_delta", path=path)
        if target != expected_iteration or delta != 0:
            raise ValueError(
                f"particle summary does not preserve exact target metadata {expected_iteration}: {path}"
            )

    times = [_float(row, "time_fs", path=path) for row in rows]
    if not all(math.isclose(value, times[0], rel_tol=0.0, abs_tol=1.0e-9) for value in times):
        raise ValueError(f"species summary rows disagree on particle time in {path}")
    return rows


def _threshold_signature(
    rows: list[dict[str, str]],
    *,
    columns: tuple[str, ...],
    path: Path,
) -> dict[str, set[tuple[float, ...]]]:
    result: dict[str, set[tuple[float, ...]]] = {}
    for scope in EXPECTED_SCOPES:
        scope_rows = [row for row in rows if str(row.get("species_scope", "")).strip() == scope]
        values = {
            tuple(_float(row, column, path=path) for column in columns)
            for row in scope_rows
        }
        result[scope] = values
    return result


def _require_curve_contract(
    path: Path,
    *,
    expected_iteration: int,
    kind: str,
) -> None:
    rows = _read_csv(path)
    _require_scope_set(rows, path=path)
    _require_table_selection(rows, path=path, expected_iteration=expected_iteration)

    if kind == "soft50":
        signatures = _threshold_signature(
            rows,
            columns=("soft50_energy_low_MeV",),
            path=path,
        )
        reference = signatures["all_electrons"]
        if (10.0,) not in reference:
            raise ValueError(f"Soft50 curve lacks required E_low=10 MeV row in {path}")
    elif kind == "acceptance":
        signatures = _threshold_signature(
            rows,
            columns=("theta_cut_mrad", "E_min_MeV"),
            path=path,
        )
        reference = signatures["all_electrons"]
        if not reference:
            raise ValueError(f"acceptance curve has no threshold grid in {path}")
    else:
        raise ValueError(f"unknown curve kind: {kind}")

    for scope in EXPECTED_SCOPES[1:]:
        if signatures[scope] != reference:
            raise ValueError(
                f"{kind} threshold grid differs across species scopes in {path}: "
                f"scope={scope}"
            )


def _filename_token(scope: str) -> str:
    token = re.sub(r"[^A-Za-z0-9_.-]+", "_", scope).strip("._-")
    if not token:
        raise ValueError(f"invalid scope for filename: {scope!r}")
    return token


def _require_plots(plots_dir: Path, *, iteration: int) -> list[str]:
    records: list[str] = []
    for scope in EXPECTED_SCOPES:
        scope_token = "" if scope == "all_electrons" else f"{_filename_token(scope)}_"
        for prefix in PLOT_PREFIXES:
            path = plots_dir / f"{prefix}{scope_token}it{iteration:08d}.png"
            if not path.is_file() or path.stat().st_size <= 0:
                raise ValueError(f"missing or empty particle plot: {path}")
            records.append(str(path))
    return records


def validate_exit(
    *,
    exit_name: str,
    outdir: Path,
    resolved: dict[str, Any],
) -> dict[str, Any]:
    if exit_name not in EXIT_TARGET_KEYS:
        raise ValueError(f"unknown exit: {exit_name}")
    target_key = EXIT_TARGET_KEYS[exit_name]
    targets = resolved.get("particle_diagnostic_targets")
    if not isinstance(targets, dict) or not isinstance(targets.get(target_key), dict):
        raise ValueError(f"resolved parameters lack target {target_key!r}")
    expected_iteration = int(targets[target_key]["iteration"])

    summary = outdir / "particle_summary.csv"
    soft50 = outdir / "particle_soft50_curves.csv"
    acceptance = outdir / "particle_acceptance_curves.csv"
    summary_rows = _require_summary(
        summary,
        expected_iteration=expected_iteration,
        expected_target_key=target_key,
    )
    _require_curve_contract(
        soft50,
        expected_iteration=expected_iteration,
        kind="soft50",
    )
    _require_curve_contract(
        acceptance,
        expected_iteration=expected_iteration,
        kind="acceptance",
    )
    plots = _require_plots(outdir / "plots", iteration=expected_iteration)

    scope_counts = {
        row["species_scope"]: _integer(row, "n_macroparticles_total", path=summary)
        for row in summary_rows
    }
    return {
        "status": "ok",
        "exit": exit_name,
        "target_key": target_key,
        "selected_particle_iteration": expected_iteration,
        "species_scope_order": list(EXPECTED_SCOPES),
        "n_macroparticles_total_by_scope": scope_counts,
        "nitrogen_empty_population_allowed": True,
        "plots": plots,
    }


def validate_analysis(
    *,
    particle_analysis_root: Path,
    resolved_parameters_path: Path,
) -> dict[str, Any]:
    if not resolved_parameters_path.is_file():
        raise ValueError(f"missing resolved parameters: {resolved_parameters_path}")
    resolved = json.loads(resolved_parameters_path.read_text(encoding="utf-8"))
    if resolved.get("particle_diagnostic_policy") != "dual_plateau_capillary_exit_exact_step_unfiltered_v1":
        raise ValueError("unexpected nitrogen particle diagnostic policy")
    if resolved.get("particle_diagnostic_dump_last_timestep") is not False:
        raise ValueError("nitrogen particle diagnostic must not force final-timestep dump")
    if float(resolved.get("particle_diagnostic_min_energy_MeV")) != 0.0:
        raise ValueError("nitrogen particle diagnostic must remain unfiltered")
    if resolved.get("particle_diagnostic_forward_only") is not False:
        raise ValueError("nitrogen raw particle diagnostic must not apply forward-only filtering")
    if resolved.get("particle_diagnostic_filter_expression") is not None:
        raise ValueError("nitrogen raw particle diagnostic must not apply a filter expression")

    exits = {
        name: validate_exit(
            exit_name=name,
            outdir=particle_analysis_root / name,
            resolved=resolved,
        )
        for name in ("plateau_exit", "capillary_exit")
    }
    if exits["plateau_exit"]["selected_particle_iteration"] == exits["capillary_exit"]["selected_particle_iteration"]:
        raise ValueError("plateau and capillary exits must resolve to distinct particle iterations")
    return {
        "schema_version": 1,
        "status": "ok",
        "particle_diagnostic_policy": resolved["particle_diagnostic_policy"],
        "selection_mode": "exit",
        "particle_exit_selection_policy": "exact_resolved_v1",
        "species_scope_order": list(EXPECTED_SCOPES),
        "exits": exits,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--particle-analysis-root", required=True)
    parser.add_argument("--resolved-parameters", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    result = validate_analysis(
        particle_analysis_root=Path(args.particle_analysis_root),
        resolved_parameters_path=Path(args.resolved_parameters),
    )
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
