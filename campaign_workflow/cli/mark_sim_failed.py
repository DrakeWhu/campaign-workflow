from __future__ import annotations

import argparse
from typing import Any

from campaign_workflow.core.transitions import simulation_failure_transition
from campaign_workflow.simulation.lifecycle_markers import simulation_config
from campaign_workflow.simulation.marker_cli import add_common_arguments, run_marker_cli

OPERATION = "mark_sim_failed"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Mark simulation cases as Failed and write post/sim_failed.json evidence."
    )
    add_common_arguments(parser)
    parser.add_argument("--scheduler", default=None, help="Scheduler/backend name, e.g. slurm.")
    parser.add_argument("--scheduler-job-id", default=None, help="Scheduler job ID, if known.")
    parser.add_argument("--scheduler-array-task-id", default=None, help="Scheduler array task ID, if applicable.")
    parser.add_argument(
        "--run-command",
        default=None,
        help="Command used to run the simulation, e.g. 'srun -n 24 python input.py 2'.",
    )
    parser.add_argument("--environment-name", default=None, help="Simulation environment name.")
    parser.add_argument("--stdout-log", default=None, help="Case-relative stdout log path.")
    parser.add_argument("--stderr-log", default=None, help="Case-relative stderr log path.")
    parser.add_argument("--return-code", type=int, default=None, help="Simulation process return code.")
    parser.add_argument(
        "--error",
        action="append",
        default=None,
        help="Human-readable failure reason. Can be passed multiple times.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return run_marker_cli(
        args,
        operation=OPERATION,
        target_state="Failed",
        marker_filename="post/sim_failed.json",
        timestamp_field="finished_at",
        ok=False,
        transition_builder=lambda operation: lambda state_doc: simulation_failure_transition(
            state_doc,
            reason="external simulation command failed",
        ),
        metadata_builder=_metadata,
    )


def _metadata(args: argparse.Namespace, config: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    sim = simulation_config(config)
    scheduler = args.scheduler or sim.get("scheduler")
    environment_name = args.environment_name or sim.get("environment_name")
    errors = args.error or []

    data = {
        "scheduler": scheduler,
        "scheduler_job_id": args.scheduler_job_id,
        "scheduler_array_task_id": args.scheduler_array_task_id,
        "run_command": args.run_command,
        "environment_name": environment_name,
        "stdout_log": args.stdout_log,
        "stderr_log": args.stderr_log,
        "return_code": args.return_code,
        "errors": errors,
    }
    return data, data


if __name__ == "__main__":
    raise SystemExit(main())