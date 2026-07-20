from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path


PARTICLE_DIAG_RELATIVE = Path("diags/plasma_electrons")
VALID_MODES = {"auto", "always", "never"}


def particle_analysis_mode(value: str | None = None) -> str:
    raw = value if value is not None else os.environ.get("CAMPAIGN_RUN_PARTICLE_ANALYSIS", "auto")
    mode = raw.strip().lower()
    if mode not in VALID_MODES:
        raise ValueError(
            "invalid CAMPAIGN_RUN_PARTICLE_ANALYSIS="
            f"{raw!r}; expected one of: auto, always, never"
        )
    return mode


def particle_diag_dir(case_dir: Path | str) -> Path:
    return Path(case_dir) / PARTICLE_DIAG_RELATIVE


def should_run_particle_analysis(case_dir: Path | str, mode: str) -> tuple[bool, Path, str]:
    resolved_mode = particle_analysis_mode(mode)
    diag_dir = particle_diag_dir(case_dir)

    if resolved_mode == "never":
        return False, diag_dir, "particle analysis disabled by CAMPAIGN_RUN_PARTICLE_ANALYSIS=never"

    if diag_dir.is_dir():
        return True, diag_dir, f"particle diagnostic found: {diag_dir}"

    if resolved_mode == "auto":
        return False, diag_dir, f"particle diagnostic not found, skipping optional particle analysis: {diag_dir}"

    raise FileNotFoundError(
        "CAMPAIGN_RUN_PARTICLE_ANALYSIS=always requested particle analysis, "
        f"but particle diagnostic directory does not exist: {diag_dir}"
    )


def build_particle_analysis_argv(
    *,
    python_executable: str,
    analysis_root: Path,
    case_dir: Path,
    diag_dir: Path,
) -> list[str]:
    script = Path(os.environ.get("CAMPAIGN_PARTICLE_ANALYSIS_SCRIPT", "scripts/analyze_particle_case.py"))
    if not script.is_absolute():
        script = analysis_root / script

    species = os.environ.get("CAMPAIGN_PARTICLE_SPECIES", "electrons")
    which = os.environ.get("CAMPAIGN_PARTICLE_WHICH", "exit")
    exit_kind = os.environ.get("CAMPAIGN_PARTICLE_EXIT_KIND", "plateau")
    spectrum_emin_mev = os.environ.get("CAMPAIGN_PARTICLE_SPECTRUM_EMIN_MEV", "1")
    maximum_target_delta = os.environ.get(
        "CAMPAIGN_PARTICLE_MAX_TARGET_ITERATION_DELTA", ""
    ).strip()
    outdir = Path(os.environ.get("CAMPAIGN_PARTICLE_OUTDIR", str(case_dir / "particle_analysis")))

    argv = [
        python_executable,
        str(script),
        "--diag",
        str(diag_dir),
        "--outdir",
        str(outdir),
        "--species",
        species,
        "--which",
        which,
        "--exit-kind",
        exit_kind,
        "--overwrite",
        "--spectrum-emin-mev",
        spectrum_emin_mev,
    ]

    if os.environ.get("CAMPAIGN_PARTICLE_SPECTRUM_LOG_Y", "1").strip().lower() not in {
        "0",
        "false",
        "no",
        "off",
    }:
        argv.append("--spectrum-log-y")

    if maximum_target_delta:
        argv.extend(
            ["--maximum-target-iteration-delta", maximum_target_delta]
        )

    return argv


def run_particle_analysis(
    *,
    case_dir: Path,
    analysis_root: Path,
    mode: str,
    python_executable: str,
) -> int:
    run, diag_dir, reason = should_run_particle_analysis(case_dir, mode)
    print(f"[PARTICLE] mode={particle_analysis_mode(mode)}")
    print(f"[PARTICLE] {reason}")

    if not run:
        return 0

    argv = build_particle_analysis_argv(
        python_executable=python_executable,
        analysis_root=analysis_root,
        case_dir=case_dir,
        diag_dir=diag_dir,
    )

    completed = subprocess.run(argv, cwd=analysis_root, check=False)
    if completed.returncode != 0:
        print(
            f"[PARTICLE] ERROR: particle analysis failed with return code {completed.returncode}",
            file=sys.stderr,
        )
        return completed.returncode

    summary_path = case_dir / "particle_analysis" / "particle_summary.csv"
    if not summary_path.is_file() or summary_path.stat().st_size == 0:
        print(
            "[PARTICLE] ERROR: particle analysis returned success but did not create "
            f"a non-empty summary file: {summary_path}",
            file=sys.stderr,
        )
        return 1

    print(f"[PARTICLE] OK: wrote {summary_path}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run optional capillary particle analysis when plasma_electrons diagnostics exist."
    )
    parser.add_argument("case_dir", type=Path)
    parser.add_argument(
        "--analysis-root",
        type=Path,
        default=Path(os.environ.get("GUIDING_ANALYSIS_ROOT", Path.home() / "apps/src/guiding_analysis_module")),
    )
    parser.add_argument(
        "--mode",
        default=os.environ.get("CAMPAIGN_RUN_PARTICLE_ANALYSIS", "auto"),
        help="auto, always, or never. Defaults to CAMPAIGN_RUN_PARTICLE_ANALYSIS or auto.",
    )
    parser.add_argument(
        "--python-executable",
        default=sys.executable,
        help="Python executable used to run the external guiding_analysis particle script.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    try:
        mode = particle_analysis_mode(args.mode)
        analysis_root = args.analysis_root.resolve()
        if not analysis_root.is_dir():
            raise FileNotFoundError(f"guiding analysis root does not exist: {analysis_root}")
        return run_particle_analysis(
            case_dir=args.case_dir.resolve(),
            analysis_root=analysis_root,
            mode=mode,
            python_executable=args.python_executable,
        )
    except Exception as exc:
        print(f"[PARTICLE] ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
