from __future__ import annotations

import argparse
from typing import Any

from campaign_workflow.core.transitions import simulation_retry_transition
from campaign_workflow.simulation.lifecycle_markers import simulation_config
from campaign_workflow.simulation.marker_cli import add_common_arguments, run_marker_cli

OPERATION = "mark_sim_retryable"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Mark failed simulation cases as Retryable and write "
            "post/sim_retryable.json evidence."
        )
    )
    add_common_arguments(parser)
    parser.add_argument("--scheduler", default=None)
    parser.add_argument("--failed-job-id", default=None)
    parser.add_argument("--failed-array-task-id", default=None)
    parser.add_argument("--reason", required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return run_marker_cli(
        args,
        operation=OPERATION,
        target_state="Retryable",
        marker_filename="post/sim_retryable.json",
        timestamp_field="marked_retryable_at",
        ok=True,
        transition_builder=lambda operation: lambda state_doc: simulation_retry_transition(
            state_doc,
            reason=args.reason,
        ),
        metadata_builder=_metadata,
    )


def _metadata(
    args: argparse.Namespace, config: dict[str, Any]
) -> tuple[dict[str, Any], dict[str, Any]]:
    sim = simulation_config(config)
    data = {
        "scheduler": args.scheduler or sim.get("scheduler"),
        "failed_job_id": args.failed_job_id,
        "failed_array_task_id": args.failed_array_task_id,
        "reason": args.reason,
    }
    return data, data


if __name__ == "__main__":
    raise SystemExit(main())
