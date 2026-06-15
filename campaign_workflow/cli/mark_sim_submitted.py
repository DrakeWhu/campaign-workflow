from __future__ import annotations

import argparse
from typing import Any

from campaign_workflow.core.transitions import simulation_submit_transition
from campaign_workflow.simulation.lifecycle_markers import simulation_config
from campaign_workflow.simulation.marker_cli import add_common_arguments, run_marker_cli

OPERATION = "mark_sim_submitted"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Mark simulation cases as Submitted and write post/sim_submitted.json evidence."
    )
    add_common_arguments(parser)
    parser.add_argument("--scheduler", default=None, help="Scheduler/backend name, e.g. slurm.")
    parser.add_argument("--scheduler-job-id", default=None, help="Scheduler job ID, if already known.")
    parser.add_argument("--scheduler-array-task-id", default=None, help="Scheduler array task ID, if applicable.")
    parser.add_argument(
        "--submit-command",
        default=None,
        help="Command used to submit the job, e.g. 'sbatch submit_array.sh'.",
    )
    parser.add_argument("--environment-name", default=None, help="Environment used by this marker command.")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return run_marker_cli(
        args,
        operation=OPERATION,
        target_state="Submitted",
        marker_filename="post/sim_submitted.json",
        timestamp_field="submitted_at",
        ok=True,
        transition_builder=lambda operation: lambda state_doc: simulation_submit_transition(
            state_doc,
            reason="simulation submitted to external execution backend",
        ),
        metadata_builder=_metadata,
    )


def _metadata(args: argparse.Namespace, config: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    sim = simulation_config(config)
    scheduler = args.scheduler or sim.get("scheduler")
    environment_name = args.environment_name or sim.get("environment_name")

    data = {
        "scheduler": scheduler,
        "scheduler_job_id": args.scheduler_job_id,
        "scheduler_array_task_id": args.scheduler_array_task_id,
        "submit_command": args.submit_command,
        "environment_name": environment_name,
    }
    return data, data


if __name__ == "__main__":
    raise SystemExit(main())