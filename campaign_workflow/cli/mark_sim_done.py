from __future__ import annotations

import argparse
import copy
import sys
from pathlib import Path
from typing import Any

from campaign_workflow.core.atomic_io import read_json, write_json_atomic
from campaign_workflow.core.path_safety import (
    PathSafetyError,
    glob_existing_files_inside_case,
    resolve_existing_path_inside_case,
    validate_relative_path,
)
from campaign_workflow.core.state import get_state_layout, now_utc, validate_state_document, validate_validation_document
from campaign_workflow.core.transitions import mark_sim_done_transition, state_name
from campaign_workflow.core.tsv_cases import CaseRecord, load_campaign_config, load_cases

OPERATION = "mark_sim_done"

ALREADY_SIM_DONE_OR_LATER_STATES = {
    "Sim_done",
    "Raw_validated",
    "Analyzing",
    "Reduced_validated",
    "Raw_delete_eligible",
    "Raw_deleted",
    "Validation_failed",
    "Analysis_failed",
    "Cleanup_failed",
}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Backfill Sim_done state for cases that already have completion evidence."
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
        help="Check evidence and print planned writes without modifying state/validation files.",
    )

    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Print per-case evidence details.",
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

    total_errors = 0
    cases_with_errors = 0
    cases_marked = 0
    cases_already_done = 0
    cases_with_evidence = 0
    total_actions = 0

    print(f"campaign_root={campaign_root}")
    print(f"campaign_name={config.get('campaign_name')}")
    print(f"selected_cases={len(cases)}")

    for case in cases:
        result = mark_one_case_sim_done(
            campaign_root=campaign_root,
            config=config,
            case=case,
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
        if result.get("already_done"):
            cases_already_done += 1
        if result.get("evidence_ok"):
            cases_with_evidence += 1

        if args.verbose or errors:
            print()
            print(f"[case {case.case_id}] {case.case_name}")
            print(f"  current_state={result.get('current_state')}")
            print(f"  target_state={result.get('target_state')}")
            print(f"  evidence_ok={result.get('evidence_ok')}")
            print(f"  already_done={result.get('already_done')}")

            for action in actions:
                prefix = "WOULD" if args.dry_run else "OK"
                print(f"  {prefix}: {action}")

            for item in result.get("evidence", []):
                print(f"  EVIDENCE: {item}")

            for warning in result.get("warnings", []):
                print(f"  WARNING: {warning}")

            for error in errors:
                print(f"  ERROR: {error}")

    print()
    print("=== SUMMARY ===")
    print(f"cases_processed={len(cases)}")
    print(f"cases_with_evidence={cases_with_evidence}")
    print(f"cases_marked={'planned' if args.dry_run else 'written'}={cases_marked}")
    print(f"cases_already_done={cases_already_done}")
    print(f"actions={'planned' if args.dry_run else 'performed_or_checked'}={total_actions}")
    print(f"cases_with_errors={cases_with_errors}")
    print(f"errors={total_errors}")
    print(f"mode={'dry-run' if args.dry_run else 'write'}")
    print("destructive_operations=0")

    if total_errors:
        return 1
    return 0


def mark_one_case_sim_done(
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

    result: dict[str, Any] = {
        "case_id": case.case_id,
        "case_name": case.case_name,
        "case_dir": str(case_dir),
        "current_state": None,
        "target_state": None,
        "evidence_ok": False,
        "already_done": False,
        "would_mark": False,
        "marked": False,
        "evidence": [],
        "warnings": [],
        "actions": [],
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

    try:
        current = state_name(state_doc)
    except Exception as exc:
        result["errors"].append(str(exc))
        return result

    result["current_state"] = current

    if current in ALREADY_SIM_DONE_OR_LATER_STATES:
        result["already_done"] = True
        result["target_state"] = current
        result["actions"].append(f"state already at or after Sim_done: {current}")
        return result

    if current != "Created":
        result["errors"].append(f"cannot mark simulation done from state {current!r}; expected 'Created'")
        return result

    evidence = collect_sim_done_evidence(case_dir=case_dir, config=config)
    result["evidence_ok"] = evidence["ok"]
    result["evidence"] = evidence["evidence"]
    result["warnings"] = evidence["warnings"]

    if not evidence["ok"]:
        result["errors"].extend(evidence["errors"])
        return result

    timestamp = now_utc()
    updated_state = mark_sim_done_transition(
        state_doc,
        reason="completion evidence found for existing campaign case",
    )

    updated_validation = copy.deepcopy(validation_doc)
    updated_validation["updated_at"] = timestamp
    updated_validation["simulation"] = {
        "schema_version": 1,
        "ok": True,
        "marked_at": timestamp,
        "operation": OPERATION,
        "evidence": evidence["evidence"],
        "warnings": evidence["warnings"],
        "errors": [],
    }

    cleanup = updated_validation.setdefault("cleanup", {})
    if isinstance(cleanup, dict):
        cleanup["cleanup_allowed"] = False
        cleanup["reason"] = "Simulation completion alone does not authorize cleanup. Raw and reduced validation are required."
    else:
        updated_validation["cleanup"] = {
            "cleanup_allowed": False,
            "reason": "Simulation completion alone does not authorize cleanup. Raw and reduced validation are required.",
        }

    notes = updated_validation.setdefault("notes", [])
    if isinstance(notes, list):
        notes.append(
            {
                "timestamp": timestamp,
                "operation": OPERATION,
                "message": "Simulation marked as done from existing completion evidence.",
            }
        )

    result["target_state"] = "Sim_done"
    result["actions"].append(f"write state transition Created -> Sim_done: {state_path}")
    result["actions"].append(f"write simulation evidence: {validation_path}")
    result["would_mark"] = dry_run
    result["marked"] = not dry_run

    write_json_atomic(state_path, updated_state, dry_run=dry_run)
    write_json_atomic(validation_path, updated_validation, dry_run=dry_run)

    return result


def collect_sim_done_evidence(*, case_dir: Path, config: dict[str, Any]) -> dict[str, Any]:
    evidence: list[str] = []
    warnings: list[str] = []
    errors: list[str] = []

    marker_ok = False
    marker = config.get("simulation", {}).get("completion_marker") if isinstance(config.get("simulation"), dict) else None
    if isinstance(marker, str) and marker.strip():
        try:
            marker_rel = validate_relative_path(marker, label="simulation.completion_marker")
            marker_path = case_dir / marker_rel
            if marker_path.exists():
                resolved = resolve_existing_path_inside_case(case_dir, marker_rel, label="simulation.completion_marker")
                if resolved.is_file():
                    marker_ok = True
                    evidence.append(f"completion marker exists: {marker_rel.as_posix()}")
                else:
                    warnings.append(f"completion marker exists but is not a regular file: {marker_rel.as_posix()}")
            else:
                warnings.append(f"completion marker not found: {marker_rel.as_posix()}")
        except PathSafetyError as exc:
            errors.append(str(exc))
        except Exception as exc:
            errors.append(f"failed to check completion marker {marker!r}: {exc}")

    raw_ok = False
    raw_diagnostics = config.get("raw_diagnostics")
    if isinstance(raw_diagnostics, list) and raw_diagnostics:
        required_diagnostics = [diag for diag in raw_diagnostics if isinstance(diag, dict) and bool(diag.get("required", True))]
        if required_diagnostics:
            raw_failures = 0
            for diagnostic in required_diagnostics:
                name = diagnostic.get("name", "<unnamed>")
                raw_glob = diagnostic.get("glob")
                min_files = _nonnegative_int(diagnostic.get("min_files", 1), f"raw_diagnostics[{name!r}].min_files")

                if not isinstance(raw_glob, str) or not raw_glob.strip():
                    errors.append(f"raw diagnostic {name!r} is missing non-empty glob")
                    raw_failures += 1
                    continue

                try:
                    files = glob_existing_files_inside_case(case_dir, raw_glob)
                except Exception as exc:
                    errors.append(f"failed to resolve raw diagnostic evidence glob {raw_glob!r}: {exc}")
                    raw_failures += 1
                    continue

                if len(files) < min_files:
                    warnings.append(
                        f"raw diagnostic {name!r} has insufficient files for completion evidence: "
                        f"found {len(files)}, required {min_files}, glob={raw_glob!r}"
                    )
                    raw_failures += 1
                else:
                    evidence.append(
                        f"required raw diagnostic {name!r} has {len(files)} file(s) matching {raw_glob!r} "
                        f"(min_files={min_files})"
                    )

            raw_ok = raw_failures == 0
        else:
            warnings.append("no required raw diagnostics configured for raw-diagnostic completion evidence")
    else:
        warnings.append("no raw_diagnostics configured for raw-diagnostic completion evidence")

    ok = marker_ok or raw_ok
    if not ok:
        errors.append("no acceptable simulation completion evidence found")

    return {
        "ok": ok,
        "evidence": evidence,
        "warnings": warnings,
        "errors": errors,
    }


def _nonnegative_int(value: Any, label: str) -> int:
    if isinstance(value, bool):
        raise ValueError(f"{label} must be an integer, got boolean")
    try:
        parsed = int(value)
    except Exception as exc:
        raise ValueError(f"{label} must be an integer: {value!r}") from exc
    if parsed < 0:
        raise ValueError(f"{label} must be non-negative: {parsed}")
    return parsed


if __name__ == "__main__":
    raise SystemExit(main())