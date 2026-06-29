from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from campaign_workflow.optimization_state import (
    OptimizationStateError,
    optimizer_tick_lock,
    write_optimization_state,
)
from campaign_workflow.optimizer_tick import OptimizerTickError, run_optimizer_tick


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Audit an optimization root and recommend the next optimization action. "
            "This command never submits jobs, runs simulations, runs analysis, or deletes data."
        )
    )
    parser.add_argument(
        "--optimization-root",
        type=Path,
        required=True,
        help="Optimization root containing iterations/iter_XXX and optionally optimizer_runs/iter_XXX.",
    )
    parser.add_argument(
        "--iteration",
        type=int,
        default=None,
        help="Restrict the audit to one iteration number, for example 0 for iter_000.",
    )

    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument(
        "--dry-run",
        action="store_true",
        help="Audit only. Do not create or update optimization_state.json and do not acquire a write lock.",
    )
    mode.add_argument(
        "--init-state",
        action="store_true",
        help="Create optimization_state.json explicitly. Fails if it already exists.",
    )
    mode.add_argument(
        "--write-state",
        action="store_true",
        help="Write or replace optimization_state.json explicitly from the current finite audit.",
    )

    return parser


def main(argv: list[str] | None = None) -> int:
    try:
        args = build_parser().parse_args(argv)
    except SystemExit as exc:
        if isinstance(exc.code, int):
            return exc.code
        return 2

    optimization_root = args.optimization_root.resolve()
    write_mode = bool(args.init_state or args.write_state)

    try:
        with optimizer_tick_lock(optimization_root, acquire=write_mode):
            summary = run_optimizer_tick(
                optimization_root=optimization_root,
                iteration=args.iteration,
            )

            state_path = Path(summary["optimization_state_path"])
            if args.init_state:
                if state_path.exists():
                    print(
                        f"ERROR: optimization_state.json already exists: {state_path}",
                        file=sys.stderr,
                    )
                    return 1
                write_optimization_state(
                    optimization_root,
                    summary["proposed_optimization_state"],
                )
                summary["mode"] = "init-state"
                summary["state_written"] = True
            elif args.write_state:
                write_optimization_state(
                    optimization_root,
                    summary["proposed_optimization_state"],
                )
                summary["mode"] = "write-state"
                summary["state_written"] = True
            else:
                summary["mode"] = "dry-run"
                summary["state_written"] = False

    except (OptimizerTickError, OptimizationStateError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:
        print(f"ERROR: optimizer tick failed: {exc}", file=sys.stderr)
        return 1

    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
