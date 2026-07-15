#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import hashlib
import json
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
    parser.add_argument("--output", required=True)
    parser.add_argument("--expected-scope", action="append", required=True)
    args = parser.parse_args()

    scopes = list(args.expected_scope)
    if len(scopes) != len(set(scopes)) or scopes[0] != "all_electrons":
        raise ValueError("expected scopes must be unique and start with all_electrons")

    outdir = Path(args.particle_outdir)
    tables = [
        validate_scope_table(
            outdir / "particle_summary.csv",
            expected_scopes=scopes,
            exactly_one_row_per_scope=True,
        ),
        validate_scope_table(
            outdir / "particle_soft50_curves.csv",
            expected_scopes=scopes,
            exactly_one_row_per_scope=False,
            require_equal_rows_per_scope=True,
        ),
        validate_scope_table(
            outdir / "particle_acceptance_curves.csv",
            expected_scopes=scopes,
            exactly_one_row_per_scope=False,
            require_equal_rows_per_scope=True,
        ),
    ]
    plots = validate_scope_plots(outdir / "plots", expected_scopes=scopes)
    payload = {
        "schema_version": 1,
        "status": "ok",
        "expected_scopes": scopes,
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
