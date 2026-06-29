from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from campaign_workflow.batch_campaign import (
    build_batch_campaign_plan,
    execute_batch_campaign_plan,
    summarize_batch_campaign_plan,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Prepare a new campaign root from a reviewed optimizer candidate_batch.tsv "
            "and batch_campaign_plan.json. This only writes campaign setup files; it "
            "does not materialize cases, submit SLURM jobs, run WarpX, run analysis, or cleanup data."
        )
    )

    parser.add_argument("--candidate-batch", type=Path, required=True)
    parser.add_argument("--batch-plan", type=Path, required=True)
    parser.add_argument("--template-campaign-root", type=Path, required=True)
    parser.add_argument("--output-campaign-root", type=Path, required=True)
    parser.add_argument("--campaign-name", required=True)

    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate and print the preparation plan without writing files.",
    )
    mode.add_argument(
        "--execute",
        action="store_true",
        help="Create the new campaign root and write setup files.",
    )

    return parser


def main(argv: list[str] | None = None) -> int:
    try:
        args = build_parser().parse_args(argv)
    except SystemExit as exc:
        if isinstance(exc.code, int):
            return exc.code
        return 2

    try:
        plan = build_batch_campaign_plan(
            candidate_batch=args.candidate_batch,
            batch_plan=args.batch_plan,
            template_campaign_root=args.template_campaign_root,
            output_campaign_root=args.output_campaign_root,
            campaign_name=args.campaign_name,
        )
        summary = (
            execute_batch_campaign_plan(plan)
            if args.execute
            else summarize_batch_campaign_plan(plan, execute=False)
        )
    except Exception as exc:
        print(f"ERROR: failed to prepare batch campaign: {exc}", file=sys.stderr)
        return 1

    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
