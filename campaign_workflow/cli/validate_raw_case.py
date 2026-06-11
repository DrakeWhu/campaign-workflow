from __future__ import annotations

import argparse
import copy
import sys
from pathlib import Path
from typing import Any

from campaign_workflow.core.atomic_io import read_json, write_json_atomic
from campaign_workflow.core.manifests import manifest_path
from campaign_workflow.core.state import (
    get_state_layout,
    now_utc,
    validate_state_document,
    validate_validation_document,
)
from campaign_workflow.core.transitions import (
    ensure_raw_validation_compatible,
    raw_validation_failure_transition,
    raw_validation_success_transition,
)
from campaign_workflow.core.tsv_cases import CaseRecord, load_campaign_config, load_cases
from campaign_workflow.diagnostics.openpmd_hdf5 import validate_raw_diagnostic


OPERATION = "validate_raw_case"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Validate configured raw diagnostics for campaign cases.")

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
        help="Restrict validation to one case ID. Can be passed multiple times.",
    )

    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Run validation checks and print planned writes without writing manifests/state/validation files.",
    )

    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Print per-case diagnostic details.",
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

    raw_diagnostics = config.get("raw_diagnostics")
    if not isinstance(raw_diagnostics, list) or not raw_diagnostics:
        print("ERROR: campaign.json must define a non-empty raw_diagnostics list.", file=sys.stderr)
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
    cases_validated = 0
    manifests_written_or_planned = 0

    print(f"campaign_root={campaign_root}")
    print(f"campaign_name={config.get('campaign_name')}")
    print(f"selected_cases={len(cases)}")

    for case in cases:
        result = validate_one_case(
            campaign_root=campaign_root,
            config=config,
            case=case,
            raw_diagnostics=raw_diagnostics,
            dry_run=args.dry_run,
        )

        errors = result["errors"]
        total_errors += len(errors)
        manifests_written_or_planned += int(result.get("manifest_count", 0))

        if errors:
            cases_with_errors += 1
        if result.get("case_ok"):
            cases_validated += 1

        if args.verbose or errors:
            print()
            print(f"[case {case.case_id}] {case.case_name}")
            print(f"  case_ok={result.get('case_ok')}")
            print(f"  target_state={result.get('target_state')}")

            for action in result.get("actions", []):
                prefix = "WOULD" if args.dry_run else "OK"
                print(f"  {prefix}: {action}")

            for diag_result in result.get("diagnostics", []):
                print(
                    "  DIAG: "
                    f"{diag_result.get('diagnostic_name')} "
                    f"kind={diag_result.get('diagnostic_kind')} "
                    f"ok={diag_result.get('ok')} "
                    f"files={diag_result.get('file_count')} "
                    f"required={diag_result.get('required')}"
                )
                for error in diag_result.get("errors", []):
                    print(f"    ERROR: {error}")
                for warning in diag_result.get("warnings", []):
                    print(f"    WARNING: {warning}")

            for error in errors:
                print(f"  ERROR: {error}")

    print()
    print("=== SUMMARY ===")
    print(f"cases_processed={len(cases)}")
    print(f"cases_validated={cases_validated}")
    print(f"cases_with_errors={cases_with_errors}")
    print(f"errors={total_errors}")
    print(f"manifests={'planned' if args.dry_run else 'written'}={manifests_written_or_planned}")
    print(f"mode={'dry-run' if args.dry_run else 'write'}")
    print("destructive_operations=0")

    if total_errors:
        return 1
    return 0


def validate_one_case(
    *,
    campaign_root: Path,
    config: dict[str, Any],
    case: CaseRecord,
    raw_diagnostics: list[Any],
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
        "case_ok": False,
        "target_state": None,
        "manifest_count": 0,
        "diagnostics": [],
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
        ensure_raw_validation_compatible(state_doc)
    except Exception as exc:
        result["errors"].append(str(exc))
        return result

    updated_validation = copy.deepcopy(validation_doc)
    raw_section = updated_validation.setdefault("raw", {})
    if not isinstance(raw_section, dict):
        result["errors"].append("validation.json raw must be an object")
        return result

    required_failures = 0
    valid_required_count = 0
    required_count = 0
    manifest_writes: list[tuple[Path, dict[str, Any]]] = []

    for diagnostic in raw_diagnostics:
        if not isinstance(diagnostic, dict):
            result["errors"].append(f"raw diagnostic entry must be an object, got {type(diagnostic).__name__}")
            required_failures += 1
            continue

        required = bool(diagnostic.get("required", True))
        if required:
            required_count += 1

        try:
            diag_result = validate_raw_diagnostic(
                case_dir=case_dir,
                case_id=case.case_id,
                case_name=case.case_name,
                diagnostic=diagnostic,
            )
        except Exception as exc:
            name = str(diagnostic.get("name", "<unknown>"))
            kind = str(diagnostic.get("kind", "<unknown>"))
            diag_result = {
                "ok": False,
                "summary": {
                    "schema_version": 1,
                    "diagnostic_name": name,
                    "diagnostic_kind": kind,
                    "ok": False,
                    "validated_at": now_utc(),
                    "file_count": 0,
                    "total_size_bytes": 0,
                    "files": [],
                    "errors": [str(exc)],
                    "warnings": [],
                },
                "manifest": None,
                "errors": [str(exc)],
                "warnings": [],
            }

        summary = copy.deepcopy(diag_result["summary"])
        diagnostic_name = summary["diagnostic_name"]
        diagnostic_kind = summary["diagnostic_kind"]
        summary["required"] = required

        manifest_doc = diag_result.get("manifest")
        if diag_result.get("ok") and manifest_doc is not None:
            path = manifest_path(case_dir, layout["manifests_dir"], diagnostic_name)
            manifest_rel = path.relative_to(case_dir).as_posix()
            summary["manifest_path"] = manifest_rel
            manifest_writes.append((path, manifest_doc))
            if required:
                valid_required_count += 1
        else:
            summary["manifest_path"] = None
            if required:
                required_failures += 1

        raw_section[diagnostic_name] = summary
        result["diagnostics"].append(
            {
                "diagnostic_name": diagnostic_name,
                "diagnostic_kind": diagnostic_kind,
                "ok": bool(diag_result.get("ok")),
                "required": required,
                "file_count": summary.get("file_count", 0),
                "errors": list(summary.get("errors", [])),
                "warnings": list(summary.get("warnings", [])),
            }
        )

    case_ok = required_count > 0 and required_failures == 0 and valid_required_count == required_count
    result["case_ok"] = case_ok

    cleanup = updated_validation.setdefault("cleanup", {})
    if isinstance(cleanup, dict):
        cleanup["cleanup_allowed"] = False
        cleanup["reason"] = "Raw validation alone does not authorize cleanup. Reduced validation is still required."
    else:
        updated_validation["cleanup"] = {
            "cleanup_allowed": False,
            "reason": "Raw validation alone does not authorize cleanup. Reduced validation is still required.",
        }

    updated_validation["updated_at"] = now_utc()
    notes = updated_validation.setdefault("notes", [])
    if isinstance(notes, list):
        notes.append(
            {
                "timestamp": now_utc(),
                "operation": OPERATION,
                "message": "Raw validation succeeded." if case_ok else "Raw validation failed.",
            }
        )

    if case_ok:
        updated_state = raw_validation_success_transition(state_doc)
        result["target_state"] = "Raw_validated"
    else:
        updated_state = raw_validation_failure_transition(state_doc)
        result["target_state"] = "Validation_failed"
        result["errors"].append("one or more required raw diagnostics failed validation")

    for path, manifest_doc in manifest_writes:
        result["actions"].append(f"write raw manifest: {path}")
        write_json_atomic(path, manifest_doc, dry_run=dry_run)

    result["manifest_count"] = len(manifest_writes)
    result["actions"].append(f"write validation document: {validation_path}")
    result["actions"].append(f"transition state to {result['target_state']}: {state_path}")

    write_json_atomic(validation_path, updated_validation, dry_run=dry_run)
    write_json_atomic(state_path, updated_state, dry_run=dry_run)

    return result


if __name__ == "__main__":
    raise SystemExit(main())