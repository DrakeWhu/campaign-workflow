from __future__ import annotations

import argparse
import copy
import sys
from pathlib import Path
from typing import Any

from campaign_workflow.core.atomic_io import read_json, write_json_atomic
from campaign_workflow.core.state import (
    get_state_layout,
    now_utc,
    validate_state_document,
    validate_validation_document,
)
from campaign_workflow.core.storage import (
    bytes_to_gb,
    required_raw_evidence_ok,
    required_reduced_evidence_ok,
    resolve_cleanup_files,
)
from campaign_workflow.core.transitions import (
    ensure_raw_delete_eligibility_compatible,
    raw_delete_eligible_transition,
)
from campaign_workflow.core.tsv_cases import CaseRecord, load_campaign_config, load_cases


OPERATION = "mark_raw_delete_eligible"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Mark cases as eligible for raw cleanup without deleting anything."
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
        help="Check eligibility and print planned actions without writing files.",
    )

    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Print per-case eligibility details.",
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

    cases_ok = 0
    cases_with_errors = 0
    total_errors = 0

    print(f"campaign_root={campaign_root}")
    print(f"campaign_name={config.get('campaign_name')}")
    print(f"selected_cases={len(cases)}")

    for case in cases:
        result = mark_one_case_raw_delete_eligible(
            campaign_root=campaign_root,
            config=config,
            case=case,
            dry_run=args.dry_run,
        )

        errors = result["errors"]
        total_errors += len(errors)

        if errors:
            cases_with_errors += 1
        if result["case_ok"]:
            cases_ok += 1

        if args.verbose or errors:
            print()
            print(f"[case {case.case_id}] {case.case_name}")
            print(f"  case_ok={result['case_ok']}")
            print(f"  current_state={result.get('current_state')}")
            print(f"  target_state={result.get('target_state')}")
            print(f"  raw_validation_ok={result.get('raw_validation_ok')}")
            print(f"  reduced_validation_ok={result.get('reduced_validation_ok')}")
            print(f"  cleanup_files={result.get('cleanup_file_count')}")
            print(f"  cleanup_bytes={result.get('cleanup_total_size_bytes')}")
            print(f"  cleanup_GB={result.get('cleanup_total_size_GB')}")

            for action in result.get("actions", []):
                prefix = "WOULD" if args.dry_run else "OK"
                print(f"  {prefix}: {action}")

            for warning in result.get("warnings", []):
                print(f"  WARNING: {warning}")

            for error in errors:
                print(f"  ERROR: {error}")

    print()
    print("=== SUMMARY ===")
    print(f"cases_processed={len(cases)}")
    print(f"cases_ok={cases_ok}")
    print(f"cases_with_errors={cases_with_errors}")
    print(f"errors={total_errors}")
    print(f"mode={'dry-run' if args.dry_run else 'write'}")
    print("destructive_operations=0")

    return 1 if total_errors else 0


def mark_one_case_raw_delete_eligible(
    *,
    campaign_root: Path,
    config: dict[str, Any],
    case: CaseRecord,
    dry_run: bool,
) -> dict[str, Any]:
    layout = get_state_layout(config)
    case_dir = campaign_root / case.case_name
    state_path = case_dir / layout["state_file"]
    validation_path = case_dir / layout["validation_file"]
    post_dir = case_dir / layout["post_dir"]
    marker_path = post_dir / "raw_delete_eligible.json"

    result: dict[str, Any] = {
        "case_id": case.case_id,
        "case_name": case.case_name,
        "case_ok": False,
        "current_state": None,
        "target_state": None,
        "raw_validation_ok": False,
        "reduced_validation_ok": False,
        "cleanup_file_count": 0,
        "cleanup_total_size_bytes": 0,
        "cleanup_total_size_GB": 0.0,
        "actions": [],
        "warnings": [],
        "errors": [],
    }

    if not case_dir.exists() or not case_dir.is_dir():
        result["errors"].append(f"missing case directory: {case_dir}")
        return result

    try:
        state_doc = read_json(state_path)
        validation_doc = read_json(validation_path)
    except Exception as exc:
        result["errors"].append(f"failed to read state/validation files: {exc}")
        return result

    state_errors = validate_state_document(state_doc, case)
    validation_errors = validate_validation_document(validation_doc, case)

    if state_errors or validation_errors:
        result["errors"].extend(state_errors)
        result["errors"].extend(validation_errors)
        return result

    result["current_state"] = state_doc.get("state")

    try:
        ensure_raw_delete_eligibility_compatible(state_doc)
    except Exception as exc:
        result["errors"].append(str(exc))
        return result

    raw_ok = required_raw_evidence_ok(config, validation_doc)
    reduced_ok = required_reduced_evidence_ok(config, validation_doc)

    result["raw_validation_ok"] = raw_ok
    result["reduced_validation_ok"] = reduced_ok

    if not raw_ok:
        result["errors"].append("required raw validation evidence is missing or not ok")
    if not reduced_ok:
        result["errors"].append("required reduced validation evidence is missing or not ok")

    legacy = validation_doc.get("legacy")
    if isinstance(legacy, dict) and legacy.get("legacy_reduced_only") is True:
        result["errors"].append("legacy reduced-only cases cannot become raw-delete eligible")

    try:
        cleanup_files = resolve_cleanup_files(case_dir=case_dir, config=config)
    except Exception as exc:
        result["errors"].append(f"failed to resolve cleanup files: {exc}")
        cleanup_files = []

    total_size_bytes = int(sum(int(item["size_bytes"]) for item in cleanup_files))
    result["cleanup_file_count"] = len(cleanup_files)
    result["cleanup_total_size_bytes"] = total_size_bytes
    result["cleanup_total_size_GB"] = bytes_to_gb(total_size_bytes)

    if not cleanup_files:
        result["errors"].append("no cleanup candidate files were found")

    if result["errors"]:
        return result

    marker = build_raw_delete_eligible_marker(
        case=case,
        cleanup_files=cleanup_files,
        total_size_bytes=total_size_bytes,
    )

    result["target_state"] = "Raw_delete_eligible"

    if dry_run:
        result["case_ok"] = True
        result["actions"].append(f"write eligibility marker: {marker_path}")
        result["actions"].append(f"write cleanup eligibility evidence into: {validation_path}")
        result["actions"].append(f"transition state to Raw_delete_eligible: {state_path}")
        return result

    updated_validation = copy.deepcopy(validation_doc)
    cleanup = updated_validation.setdefault("cleanup", {})
    if not isinstance(cleanup, dict):
        updated_validation["cleanup"] = {}
        cleanup = updated_validation["cleanup"]

    cleanup.update(
        {
            "cleanup_allowed": True,
            "cleanup_allowed_at": now_utc(),
            "operation": OPERATION,
            "reason": (
                "Raw and reduced validation evidence passed. "
                "Raw cleanup may proceed only through dry-run manifest and explicit execute phase."
            ),
            "eligibility_marker_path": f"{layout['post_dir']}/raw_delete_eligible.json",
            "candidate_file_count": len(cleanup_files),
            "candidate_total_size_bytes": total_size_bytes,
            "candidate_total_size_GB": bytes_to_gb(total_size_bytes),
            "destructive_operations": 0,
        }
    )
    updated_validation["updated_at"] = now_utc()

    next_state = raw_delete_eligible_transition(state_doc)

    write_json_atomic(marker_path, marker)
    write_json_atomic(validation_path, updated_validation)
    write_json_atomic(state_path, next_state)

    result["case_ok"] = True
    result["actions"].append(f"write eligibility marker: {marker_path}")
    result["actions"].append(f"write cleanup eligibility evidence into: {validation_path}")
    result["actions"].append(f"transition state to Raw_delete_eligible: {state_path}")

    return result


def build_raw_delete_eligible_marker(
    *,
    case: CaseRecord,
    cleanup_files: list[dict[str, Any]],
    total_size_bytes: int,
) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "created_at": now_utc(),
        "operation": OPERATION,
        "case_id": case.case_id,
        "case_name": case.case_name,
        "state": "Raw_delete_eligible",
        "cleanup_allowed": True,
        "cleanup_requires_dry_run_manifest": True,
        "cleanup_requires_explicit_execute": True,
        "candidate_file_count": len(cleanup_files),
        "candidate_total_size_bytes": total_size_bytes,
        "candidate_total_size_GB": bytes_to_gb(total_size_bytes),
        "candidate_files_preview": cleanup_files[:10],
        "destructive_operations": 0,
    }


if __name__ == "__main__":
    raise SystemExit(main())