from __future__ import annotations

import argparse
import sys
from pathlib import Path

from campaign_workflow.core.case_materialization import (
    build_case_materialization_plan,
    get_case_materialization_config,
    materialize_one_case,
)
from campaign_workflow.core.tsv_cases import load_campaign_config, load_cases


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Materialize case-local simulation inputs from cases.tsv and input_template.py. "
            "Creates CASE_DIR/input.py and CASE_DIR/case.env."
        )
    )

    parser.add_argument(
        "--campaign-root",
        type=Path,
        default=Path("."),
        help="Campaign root containing campaign.json, cases.tsv and input_template.py.",
    )

    parser.add_argument(
        "--case-id",
        type=int,
        action="append",
        default=None,
        help="Restrict operation to one case ID. Can be passed multiple times.",
    )

    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print planned materialization without writing anything.",
    )

    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Allow overwriting existing CASE_DIR/input.py and CASE_DIR/case.env.",
    )

    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Print per-case actions.",
    )

    return parser


def main(argv: list[str] | None = None) -> int:
    try:
        args = build_parser().parse_args(argv)
    except SystemExit as exc:
        if isinstance(exc.code, int):
            return exc.code
        return 2

    campaign_root = args.campaign_root.resolve()

    try:
        config = load_campaign_config(campaign_root)
        mat_config = get_case_materialization_config(config)
        cases = load_cases(campaign_root, config)
    except Exception as exc:
        print(f"ERROR: failed to load campaign configuration/cases: {exc}", file=sys.stderr)
        return 1

    selected_ids = set(args.case_id or [])
    if selected_ids:
        known_ids = {case.case_id for case in cases}
        unknown = sorted(selected_ids - known_ids)
        if unknown:
            print(f"ERROR: requested unknown case IDs: {unknown}", file=sys.stderr)
            return 1
        cases = [case for case in cases if case.case_id in selected_ids]

    try:
        plans = build_case_materialization_plan(
            campaign_root=campaign_root,
            cases=cases,
            config=config,
            mat_config=mat_config,
        )
    except Exception as exc:
        print(f"ERROR: failed to build materialization plan: {exc}", file=sys.stderr)
        return 1

    total_errors = 0
    cases_with_errors = 0
    total_actions = 0
    inputs_written = 0
    envs_written = 0

    print(f"campaign_root={campaign_root}")
    print(f"campaign_name={config.get('campaign_name')}")
    print(f"selected_cases={len(plans)}")
    print(f"input_template={mat_config.input_template.as_posix()}")
    print(f"input_name={mat_config.input_name.as_posix()}")
    print(f"env_name={mat_config.env_name.as_posix()}")

    for plan in plans:
        result = materialize_one_case(plan=plan, dry_run=args.dry_run, overwrite=args.overwrite)

        errors = result["errors"]
        actions = result["actions"]

        total_errors += len(errors)
        total_actions += len(actions)
        inputs_written += int(result["input_written"])
        envs_written += int(result["env_written"])

        if errors:
            cases_with_errors += 1

        if args.verbose or errors:
            print()
            print(f"[case {result['case_id']}] {result['case_name']}")
            print(f"  case_dir={result['case_dir']}")
            print(f"  input={result['input_path']}")
            print(f"  env={result['env_path']}")

            for action in actions:
                prefix = "WOULD" if args.dry_run else "OK"
                print(f"  {prefix}: {action}")

            if not actions and not errors:
                print("  OK: materialized files already exist")

            for error in errors:
                print(f"  ERROR: {error}")

    print()
    print("=== SUMMARY ===")
    print(f"cases_processed={len(plans)}")
    print(f"inputs={'planned' if args.dry_run else 'written'}={inputs_written}")
    print(f"envs={'planned' if args.dry_run else 'written'}={envs_written}")
    print(f"actions={'planned' if args.dry_run else 'performed'}={total_actions}")
    print(f"cases_with_errors={cases_with_errors}")
    print(f"errors={total_errors}")
    print(f"mode={'dry-run' if args.dry_run else 'write'}")
    print(f"overwrite={1 if args.overwrite else 0}")
    print("destructive_operations=0")

    if total_errors:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())