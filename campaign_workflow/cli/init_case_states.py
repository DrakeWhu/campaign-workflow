from __future__ import annotations

import argparse
import sys
from pathlib import Path

from campaign_workflow.core.state import ensure_case_state
from campaign_workflow.core.tsv_cases import load_campaign_config, load_cases


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Initialize or check per-case state.json and validation.json files."
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
        help="Print planned actions without writing anything.",
    )

    parser.add_argument(
        "--check",
        action="store_true",
        help="Check that expected state files already exist and are valid. Does not create files.",
    )

    parser.add_argument(
        "--create-missing-case-dirs",
        action="store_true",
        help=(
            "Create missing case directories. Intended for fake/local campaigns or deliberate "
            "initialization only. Real production campaigns should normally already have case directories."
        ),
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

    if args.dry_run and args.check:
        print("ERROR: --dry-run and --check are mutually exclusive.", file=sys.stderr)
        return 2

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

    total_actions = 0
    total_errors = 0
    cases_with_errors = 0

    print(f"campaign_root={campaign_root}")
    print(f"campaign_name={config.get('campaign_name')}")
    print(f"selected_cases={len(cases)}")

    for case in cases:
        case_dir = campaign_root / case.case_name

        result = ensure_case_state(
            case_dir=case_dir,
            case=case,
            config=config,
            dry_run=args.dry_run,
            create_missing_case_dirs=args.create_missing_case_dirs,
            check_only=args.check,
        )

        actions = result["actions"]
        errors = result["errors"]

        total_actions += len(actions)
        total_errors += len(errors)

        if errors:
            cases_with_errors += 1

        if args.verbose or errors:
            print()
            print(f"[case {case.case_id}] {case.case_name}")

            for action in actions:
                prefix = "WOULD" if args.dry_run else "OK"
                if args.check:
                    prefix = "CHECK"
                print(f"  {prefix}: {action}")

            for error in errors:
                print(f"  ERROR: {error}")

    print()
    print("=== SUMMARY ===")
    print(f"cases_processed={len(cases)}")
    print(f"actions={'planned' if args.dry_run else 'performed_or_checked'}={total_actions}")
    print(f"cases_with_errors={cases_with_errors}")
    print(f"errors={total_errors}")
    print(f"mode={'check' if args.check else 'dry-run' if args.dry_run else 'write'}")
    print("destructive_operations=0")

    if total_errors:
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
