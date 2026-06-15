from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any, Callable

from campaign_workflow.core.tsv_cases import load_campaign_config, load_cases
from campaign_workflow.simulation.lifecycle_markers import mark_one_case_simulation_lifecycle

TransitionBuilder = Callable[[str], Callable[[dict[str, Any]], dict[str, Any]]]
MetadataBuilder = Callable[[argparse.Namespace, dict[str, Any]], tuple[dict[str, Any], dict[str, Any]]]


def add_common_arguments(parser: argparse.ArgumentParser) -> None:
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
        help="Check transitions and print planned writes without modifying files.",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Print per-case details.",
    )


def run_marker_cli(
    args: argparse.Namespace,
    *,
    operation: str,
    target_state: str,
    marker_filename: str,
    timestamp_field: str,
    ok: bool,
    transition_builder: TransitionBuilder,
    metadata_builder: MetadataBuilder,
) -> int:
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

    marker_extra, evidence_extra = metadata_builder(args, config)

    total_errors = 0
    cases_with_errors = 0
    cases_marked = 0
    total_actions = 0

    print(f"campaign_root={campaign_root}")
    print(f"campaign_name={config.get('campaign_name')}")
    print(f"selected_cases={len(cases)}")
    print(f"operation={operation}")

    for case in cases:
        result = mark_one_case_simulation_lifecycle(
            campaign_root=campaign_root,
            config=config,
            case=case,
            operation=operation,
            target_state=target_state,
            marker_filename=marker_filename,
            timestamp_field=timestamp_field,
            ok=ok,
            transition=transition_builder(operation),
            marker_extra=marker_extra,
            evidence_extra=evidence_extra,
            dry_run=args.dry_run,
        )

        errors = result["errors"]
        actions = result["actions"]
        total_errors += len(errors)
        total_actions += len(actions)

        if errors:
            cases_with_errors += 1
        if result.get("would_mark") or result.get("marked"):
            cases_marked += 1

        if args.verbose or errors:
            print()
            print(f"[case {case.case_id}] {case.case_name}")
            print(f"  current_state={result.get('current_state')}")
            print(f"  target_state={result.get('target_state')}")
            print(f"  marker_path={result.get('marker_path')}")

            for action in actions:
                prefix = "WOULD" if args.dry_run else "OK"
                print(f"  {prefix}: {action}")

            for error in errors:
                print(f"  ERROR: {error}")

    print()
    print("=== SUMMARY ===")
    print(f"cases_processed={len(cases)}")
    print(f"cases_marked={'planned' if args.dry_run else 'written'}={cases_marked}")
    print(f"actions={'planned' if args.dry_run else 'performed'}={total_actions}")
    print(f"cases_with_errors={cases_with_errors}")
    print(f"errors={total_errors}")
    print(f"mode={'dry-run' if args.dry_run else 'write'}")
    print("destructive_operations=0")

    if total_errors:
        return 1
    return 0