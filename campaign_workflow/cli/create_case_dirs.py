from __future__ import annotations

import argparse
import sys
from pathlib import Path

from campaign_workflow.core.case_dirs import build_case_dir_plan, materialize_one_case_dir
from campaign_workflow.core.tsv_cases import load_campaign_config, load_cases


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Create campaign case directories from the configured case manifest."
    )

    parser.add_argument(
        "--campaign-root",
        type=Path,
        default=Path("."),
        help="Campaign root containing campaign.json and the case manifest.",
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
        help="Print planned directory creation without writing anything.",
    )

    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Print per-case actions.",
    )

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    campaign_root = args.campaign_root.resolve()

    try:
        config = load_campaign_config(campaign_root)
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
        plans = build_case_dir_plan(campaign_root=campaign_root, cases=cases, config=config)
    except Exception as exc:
        print(f"ERROR: failed to build case directory plan: {exc}", file=sys.stderr)
        return 1

    total_errors = 0
    cases_with_errors = 0
    total_actions = 0
    case_dirs_created = 0
    case_dirs_existing = 0
    subdirs_created = 0

    print(f"campaign_root={campaign_root}")
    print(f"campaign_name={config.get('campaign_name')}")
    print(f"selected_cases={len(plans)}")

    for plan in plans:
        result = materialize_one_case_dir(plan=plan, dry_run=args.dry_run)

        errors = result["errors"]
        actions = result["actions"]

        total_errors += len(errors)
        total_actions += len(actions)
        case_dirs_created += int(result["case_dir_created"])
        case_dirs_existing += int(result["case_dir_existing"])
        subdirs_created += int(result["subdirs_created"])

        if errors:
            cases_with_errors += 1

        if args.verbose or errors:
            print()
            print(f"[case {result['case_id']}] {result['case_name']}")
            print(f"  case_dir={result['case_dir']}")

            for action in actions:
                prefix = "WOULD" if args.dry_run else "OK"
                print(f"  {prefix}: {action}")

            if not actions and not errors:
                print("  OK: case directory layout already exists")

            for error in errors:
                print(f"  ERROR: {error}")

    print()
    print("=== SUMMARY ===")
    print(f"cases_processed={len(plans)}")
    print(f"case_dirs_created={'planned' if args.dry_run else 'written'}={case_dirs_created}")
    print(f"case_dirs_existing={case_dirs_existing}")
    print(f"subdirs_created={'planned' if args.dry_run else 'written'}={subdirs_created}")
    print(f"actions={'planned' if args.dry_run else 'performed'}={total_actions}")
    print(f"cases_with_errors={cases_with_errors}")
    print(f"errors={total_errors}")
    print(f"mode={'dry-run' if args.dry_run else 'write'}")
    print("destructive_operations=0")

    if total_errors:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())