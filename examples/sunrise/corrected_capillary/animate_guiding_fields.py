#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
from pathlib import Path
from typing import Any

import imageio.v2 as imageio
import matplotlib.pyplot as plt
import numpy as np
from openpmd_viewer import OpenPMDTimeSeries


def get_rz_signed(data: Any, info: Any) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Return field[r,z], signed r [um] and z [um] for a WarpX RZ dump."""

    array = np.squeeze(np.asarray(data))
    if array.ndim != 2:
        raise ValueError(f"expected a 2-D RZ field, got shape={array.shape}")
    axes = getattr(info, "axes", None)
    try:
        axis_order = tuple(str(axes[index]) for index in range(2))
    except Exception as exc:
        raise ValueError(f"cannot interpret openPMD axes={axes!r}") from exc
    if axis_order == ("z", "r"):
        array = array.T
    elif axis_order != ("r", "z"):
        raise ValueError(f"expected RZ axes, got {axis_order!r}")
    r_um = np.asarray(info.r, dtype=float) * 1.0e6
    z_um = np.asarray(info.z, dtype=float) * 1.0e6
    if array.shape != (len(r_um), len(z_um)):
        raise ValueError(
            f"field/coordinate shape mismatch: {array.shape}, r={len(r_um)}, z={len(z_um)}"
        )
    return array, r_um, z_um


def laser_metrics(
    ex_rz: np.ndarray,
    ey_rz: np.ndarray,
    r_um: np.ndarray,
    z_um: np.ndarray,
) -> tuple[float, float, float]:
    positive = r_um >= 0.0
    r_m = r_um[positive] * 1.0e-6
    intensity = ex_rz[positive] ** 2 + ey_rz[positive] ** 2
    axial = np.sum(intensity * r_m[:, None], axis=0)
    peak_index = int(np.nanargmax(axial))
    z_peak_um = float(z_um[peak_index])
    z_window = np.abs(z_um - z_peak_um) <= 5.0
    radial = np.sum(intensity[:, z_window], axis=1)
    denominator = float(np.sum(radial * r_m))
    if denominator > 0.0:
        r2 = float(np.sum(r_m**2 * radial * r_m) / denominator)
        waist_um = np.sqrt(max(2.0 * r2, 0.0)) * 1.0e6
    else:
        waist_um = float("nan")
    return z_peak_um, float(waist_um), float(np.nanmax(intensity))


def robust_limit(values: np.ndarray, *, symmetric: bool) -> float:
    finite = np.asarray(values)[np.isfinite(values)]
    if finite.size == 0:
        return 1.0
    sample = np.abs(finite) if symmetric else finite
    limit = float(np.percentile(sample, 99.5 if symmetric else 99.7))
    return limit if np.isfinite(limit) and limit > 0.0 else 1.0


def render_frame(
    values: np.ndarray,
    r_um: np.ndarray,
    z_relative_um: np.ndarray,
    *,
    xlim_um: tuple[float, float],
    rlim_um: tuple[float, float],
    symmetric: bool,
    colorbar_label: str,
    title: str,
) -> np.ndarray:
    z_mask = (z_relative_um >= xlim_um[0]) & (z_relative_um <= xlim_um[1])
    r_mask = (r_um >= rlim_um[0]) & (r_um <= rlim_um[1])
    if np.count_nonzero(z_mask) < 2 or np.count_nonzero(r_mask) < 2:
        raise ValueError("requested animation crop contains fewer than two cells")
    image = values[np.ix_(r_mask, z_mask)]
    z_selected = z_relative_um[z_mask]
    r_selected = r_um[r_mask]
    limit = robust_limit(image, symmetric=symmetric)
    vmin, vmax = (-limit, limit) if symmetric else (0.0, limit)

    figure, axis = plt.subplots(figsize=(9, 5), dpi=120)
    plotted = axis.imshow(
        image,
        origin="lower",
        aspect="auto",
        extent=[
            float(z_selected.min()),
            float(z_selected.max()),
            float(r_selected.min()),
            float(r_selected.max()),
        ],
        vmin=vmin,
        vmax=vmax,
        interpolation="nearest",
        cmap="RdBu_r" if symmetric else "viridis",
    )
    axis.axvline(0.0, linestyle="--", linewidth=1.0, color="white", alpha=0.8)
    axis.axhline(0.0, linestyle=":", linewidth=1.0, color="white", alpha=0.8)
    axis.set_xlabel("z - z_peak [um]")
    axis.set_ylabel("r [um]")
    axis.set_title(title)
    figure.colorbar(plotted, ax=axis, label=colorbar_label)
    figure.tight_layout()
    figure.canvas.draw()
    frame = np.asarray(figure.canvas.buffer_rgba(), dtype=np.uint8)[..., :3].copy()
    plt.close(figure)
    return frame


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--diag", required=True)
    parser.add_argument("--outdir", required=True)
    parser.add_argument("--stride", type=int, default=1)
    parser.add_argument("--fps", type=int, default=8)
    parser.add_argument("--laser-xlim-um", nargs=2, type=float, default=(-100.0, 40.0))
    parser.add_argument("--wake-xlim-um", nargs=2, type=float, default=(-230.0, 50.0))
    parser.add_argument("--rlim-um", nargs=2, type=float, default=(-120.0, 120.0))
    args = parser.parse_args()
    if args.stride <= 0 or args.fps <= 0:
        raise ValueError("stride and fps must be positive")

    output = Path(args.outdir)
    output.mkdir(parents=True, exist_ok=True)
    eperp_path = output / "eperp2.mp4"
    ez_path = output / "ez_wake.mp4"
    metrics_path = output / "animation_frame_metrics.csv"

    series = OpenPMDTimeSeries(str(Path(args.diag)))
    iterations = [int(value) for value in series.iterations[:: args.stride]]
    if len(iterations) < 2:
        raise ValueError("at least two field dumps are required for an animation")

    rows: list[dict[str, Any]] = []
    initial_zmax_um: float | None = None
    initial_peak: float | None = None
    with imageio.get_writer(
        eperp_path, fps=args.fps, codec="libx264", quality=8, macro_block_size=2
    ) as eperp_writer, imageio.get_writer(
        ez_path, fps=args.fps, codec="libx264", quality=8, macro_block_size=2
    ) as ez_writer:
        for iteration in iterations:
            ex, info = series.get_field(iteration=iteration, field="E", coord="x")
            ey, ey_info = series.get_field(iteration=iteration, field="E", coord="y")
            ez, ez_info = series.get_field(iteration=iteration, field="E", coord="z")
            ex_rz, r_um, z_um = get_rz_signed(ex, info)
            ey_rz, ey_r_um, ey_z_um = get_rz_signed(ey, ey_info)
            ez_rz, ez_r_um, ez_z_um = get_rz_signed(ez, ez_info)
            if not (
                np.array_equal(r_um, ey_r_um)
                and np.array_equal(r_um, ez_r_um)
                and np.array_equal(z_um, ey_z_um)
                and np.array_equal(z_um, ez_z_um)
            ):
                raise ValueError("Ex, Ey and Ez coordinates do not match")

            z_peak_um, waist_um, peak = laser_metrics(ex_rz, ey_rz, r_um, z_um)
            if initial_zmax_um is None:
                initial_zmax_um = float(z_um.max())
            if initial_peak is None:
                initial_peak = peak if peak > 0.0 else 1.0
            propagation_mm = (float(z_um.max()) - initial_zmax_um) * 1.0e-3
            peak_relative = peak / initial_peak
            ez_absmax_gvm = float(np.nanmax(np.abs(ez_rz)) / 1.0e9)
            z_relative_um = z_um - z_peak_um
            eperp2 = ex_rz**2 + ey_rz**2

            eperp_writer.append_data(
                render_frame(
                    eperp2,
                    r_um,
                    z_relative_um,
                    xlim_um=tuple(args.laser_xlim_um),
                    rlim_um=tuple(args.rlim_um),
                    symmetric=False,
                    colorbar_label="E_perp^2 [frame-scaled]",
                    title=(
                        f"E_perp^2 | it={iteration} | s={propagation_mm:.2f} mm | "
                        f"w={waist_um:.2f} um | peak/initial={peak_relative:.2f}"
                    ),
                )
            )
            ez_writer.append_data(
                render_frame(
                    ez_rz,
                    r_um,
                    z_relative_um,
                    xlim_um=tuple(args.wake_xlim_um),
                    rlim_um=tuple(args.rlim_um),
                    symmetric=True,
                    colorbar_label="Ez [V/m, frame-scaled]",
                    title=(
                        f"Ez wake | it={iteration} | s={propagation_mm:.2f} mm | "
                        f"max|Ez|={ez_absmax_gvm:.2f} GV/m"
                    ),
                )
            )
            rows.append(
                {
                    "iteration": iteration,
                    "propagation_mm": propagation_mm,
                    "z_peak_um": z_peak_um,
                    "waist_um": waist_um,
                    "peak_eperp2": peak,
                    "peak_eperp2_relative": peak_relative,
                    "ez_absmax_GVm": ez_absmax_gvm,
                }
            )

    with metrics_path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    print(f"[ANIMATION] wrote {eperp_path}, {ez_path} and {metrics_path}")


if __name__ == "__main__":
    main()
