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
from campaign_workflow.submit_iteration import (
    SubmitIterationError,
    build_submit_iteration_plan,
    execute_submit_iteration,
    state_updates_preview,
    update_state_after_submit,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Audit an optimization root and optionally execute one explicit optimizer action. "
            "Without --execute this command never submits jobs, runs simulations, runs analysis, or deletes data."
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
        help="Restrict the audit/action to one iteration number, for example 0 for iter_000.",
    )
    parser.add_argument(
        "--action",
        choices=["submit_iteration"],
        default=None,
        help="Explicit action to plan or execute after the finite audit.",
    )
    parser.add_argument(
        "--array-spec",
        default=None,
        help="SLURM array spec for submit_iteration, for example 0-9. Defaults to all cases.",
    )
    parser.add_argument(
        "--max-cases",
        type=int,
        default=None,
        help="Submit only the first N case IDs by SLURM array spec. Mutually exclusive with --array-spec.",
    )
    parser.add_argument(
        "--submit-script",
        type=Path,
        default=None,
        help="Optional explicit submit script. Defaults to examples/sunrise/submit_case_cycle_array.sh.",
    )
    parser.add_argument(
        "--workflow-root",
        type=Path,
        default=None,
        help="Optional workflow root exported to the SLURM job.",
    )
    parser.add_argument(
        "--workflow-env",
        type=Path,
        default=None,
        help="Optional workflow environment script exported to the SLURM job.",
    )
    parser.add_argument(
        "--job-name",
        default=None,
        help="Optional SLURM job name. Defaults to cw_iter_XXX_cycle.",
    )

    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument(
        "--dry-run",
        action="store_true",
        help="Audit only, or plan an explicit action. Do not call sbatch and do not write optimization_state.json.",
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
    mode.add_argument(
        "--execute",
        action="store_true",
        help="Execute the explicit action. In Fase 4B this is limited to exactly one sbatch for submit_iteration.",
    )

    return parser


def main(argv: list[str] | None = None) -> int:
    try:
        args = build_parser().parse_args(argv)
    except SystemExit as exc:
        if isinstance(exc.code, int):
            return exc.code
        return 2

    arg_error = _validate_args(args)
    if arg_error:
        print(f"ERROR: {arg_error}", file=sys.stderr)
        return 2

    optimization_root = args.optimization_root.resolve()
    write_mode = bool(args.init_state or args.write_state or args.execute)

    try:
        with optimizer_tick_lock(optimization_root, acquire=write_mode):
            summary = run_optimizer_tick(
                optimization_root=optimization_root,
                iteration=args.iteration,
            )

            state_path = Path(summary["optimization_state_path"])
            if args.action == "submit_iteration":
                plan = build_submit_iteration_plan(
                    tick_summary=summary,
                    optimization_root=optimization_root,
                    iteration=int(args.iteration),
                    array_spec=args.array_spec,
                    max_cases=args.max_cases,
                    submit_script=args.submit_script,
                    workflow_root=args.workflow_root,
                    workflow_env=args.workflow_env,
                    job_name=args.job_name,
                )
                summary["action"] = "submit_iteration"
                summary["submit_plan"] = plan.to_dict()
                summary["state_updates_if_executed"] = state_updates_preview(plan)

                if args.execute:
                    result = execute_submit_iteration(plan)
                    new_state = update_state_after_submit(
                        state_doc=summary["proposed_optimization_state"],
                        plan=plan,
                        result=result,
                    )
                    write_optimization_state(optimization_root, new_state)
                    summary["mode"] = "execute"
                    summary["state_written"] = True
                    summary["submission_result"] = result.to_dict()
                    summary["optimization_state_after_submit"] = new_state
                else:
                    summary["mode"] = "dry-run"
                    summary["state_written"] = False

            elif args.init_state:
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

    except (OptimizerTickError, OptimizationStateError, SubmitIterationError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:
        print(f"ERROR: optimizer tick failed: {exc}", file=sys.stderr)
        return 1

    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


def _validate_args(args: argparse.Namespace) -> str | None:
    action_args = [
        args.array_spec,
        args.max_cases,
        args.submit_script,
        args.workflow_root,
        args.workflow_env,
        args.job_name,
    ]
    if any(value is not None for value in action_args) and args.action is None:
        return "submit options require --action submit_iteration"

    if args.action == "submit_iteration":
        if args.iteration is None:
            return "--action submit_iteration requires --iteration"
        if args.init_state or args.write_state:
            return "--action submit_iteration supports only --dry-run or --execute"
        if args.array_spec is not None and args.max_cases is not None:
            return "--array-spec and --max-cases are mutually exclusive"

    if args.execute and args.action != "submit_iteration":
        return "--execute requires --action submit_iteration in Fase 4B"

    return None


if __name__ == "__main__":
    raise SystemExit(main())
