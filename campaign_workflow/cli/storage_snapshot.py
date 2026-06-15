from __future__ import annotations

import argparse
import sys
from pathlib import Path

from campaign_workflow.core.atomic_io import write_json_atomic
from campaign_workflow.core.storage import build_storage_snapshot, bytes_to_gb
from campaign_workflow.core.tsv_cases import load_campaign_config, load_cases


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Compute a campaign-level storage snapshot without deleting anything."
    )

    parser.add_argument(
        "--campaign-root",
        type=Path,
        default=Path("."),
        help="Campaign root containing campaign.json and the case manifest.",
    )

    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Snapshot output path. Defaults to snapshots/storage_snapshot_latest.json under campaign root.",
    )

    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Compute and print summary without writing the snapshot file.",
    )

    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Print per-case storage information.",
    )

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    campaign_root = args.campaign_root.resolve()

    try:
        config = load_campaign_config(campaign_root)
        cases = load_cases(campaign_root, config)
        snapshot = build_storage_snapshot(
            campaign_root=campaign_root,
            config=config,
            cases=cases,
        )
    except Exception as exc:
        print(f"ERROR: failed to build storage snapshot: {exc}", file=sys.stderr)
        return 1

    output_path = args.output
    if output_path is None:
        output_path = campaign_root / "snapshots" / "storage_snapshot_latest.json"
    elif not output_path.is_absolute():
        output_path = campaign_root / output_path

    print(f"campaign_root={campaign_root}")
    print(f"campaign_name={snapshot.get('campaign_name')}")
    print(f"case_count={snapshot.get('case_count')}")
    print(f"validated_cases={snapshot.get('validated_cases')}")
    print(f"submitted_cases={snapshot.get('submitted_cases')}")
    print(f"case_total_GB={snapshot.get('case_total_GB')}")
    print(f"raw_live_GB={snapshot.get('raw_live_GB')}")
    print(f"safe_cleanup_candidate_GB={snapshot.get('safe_cleanup_candidate_GB')}")
    print(f"raw_delete_eligible_GB={snapshot.get('raw_delete_eligible_GB')}")
    print(f"raw_deleted_GB={snapshot.get('raw_deleted_GB')}")
    print(f"avg_raw_per_case_GB={snapshot.get('avg_raw_per_case_GB')}")
    print(f"cases_by_state={snapshot.get('cases_by_state')}")
    print(f"errors={len(snapshot.get('errors', []))}")

    if args.verbose:
        for case in snapshot.get("cases", []):
            print()
            print(f"[case {case.get('case_id')}] {case.get('case_name')}")
            print(f"  state={case.get('state')}")
            print(f"  case_total_GB={bytes_to_gb(int(case.get('case_total_bytes', 0)))}")
            print(f"  raw_live_GB={bytes_to_gb(int(case.get('raw_live_bytes', 0)))}")
            print(
                "  safe_cleanup_candidate_GB="
                f"{bytes_to_gb(int(case.get('safe_cleanup_candidate_bytes', 0)))}"
            )
            print(f"  raw_validation_ok={case.get('raw_validation_ok')}")
            print(f"  reduced_validation_ok={case.get('reduced_validation_ok')}")
            print(f"  cleanup_allowed={case.get('cleanup_allowed')}")
            print(f"  cleanup_files={len(case.get('cleanup_files', []))}")

            for error in case.get("errors", []):
                print(f"  ERROR: {error}")
            for warning in case.get("warnings", []):
                print(f"  WARNING: {warning}")

    if args.dry_run:
        print(f"WOULD: write storage snapshot: {output_path}")
        print("mode=dry-run")
        print("destructive_operations=0")
    else:
        write_json_atomic(output_path, snapshot)
        print(f"OK: wrote storage snapshot: {output_path}")
        print("mode=write")
        print("destructive_operations=0")

    if snapshot.get("errors"):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())