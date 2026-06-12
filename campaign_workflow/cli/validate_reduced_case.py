from __future__ import annotations

import argparse
import copy
import sys
from pathlib import Path
from typing import Any

from campaign_workflow.analysis.csv_contract import validate_reduced_output
from campaign_workflow.core.atomic_io import read_json, write_json_atomic
from campaign_workflow.core.state import (
    get_state_layout,
    now_utc,
    validate_state_document,
    validate_validation_document,
)
from campaign_workflow.core.transitions import (
    ensure_reduced_validation_compatible,
    reduced_validation_failure_transition,
    reduced_validation_success_transition,
)
from campaign_workflow.core.tsv_cases import CaseRecord, load_campaign_config, load_cases


OPERATION = "validate_reduced_case"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Validate configured reduced outputs for campaign cases.")

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
        help="Run validation checks and print planned writes without writing state/validation files.",
    )

    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Print per-case reduced output details.",
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

    reduced_outputs = _configured_reduced_outputs(config)
    if not reduced_outputs:
        print("ERROR: campaign.json must define a non-empty analysis.outputs list.", file=sys.stderr)
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

    print(f"campaign_root={campaign_root}")
    print(f"campaign_name={config.get('campaign_name')}")
    print(f"selected_cases={len(cases)}")

    for case in cases:
        result = validate_one_case(
            campaign_root=campaign_root,
            config=config,
            case=case,
            reduced_outputs=reduced_outputs,
            dry_run=args.dry_run,
        )

        errors = result["errors"]
        total_errors += len(errors)

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

            for output_result in result.get("outputs", []):
                print(
                    "  OUTPUT: "
                    f"{output_result.get('output_name')} "
                    f"kind={output_result.get('output_kind')} "
                    f"ok={output_result.get('ok')} "
                    f"rows={output_result.get('row_count')} "
                    f"required={output_result.get('required')}"
                )
                for error in output_result.get("errors", []):
                    print(f"    ERROR: {error}")
                for warning in output_result.get("warnings", []):
                    print(f"    WARNING: {warning}")

            for error in errors:
                print(f"  ERROR: {error}")

    print()
    print("=== SUMMARY ===")
    print(f"cases_processed={len(cases)}")
    print(f"cases_validated={cases_validated}")
    print(f"cases_with_errors={cases_with_errors}")
    print(f"errors={total_errors}")
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
    reduced_outputs: list[Any],
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
        "outputs": [],
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
        ensure_reduced_validation_compatible(state_doc)
    except Exception as exc:
        result["errors"].append(str(exc))
        return result

    raw_evidence_errors = _validate_required_raw_evidence(config, validation_doc)
    if raw_evidence_errors:
        result["errors"].extend(raw_evidence_errors)
        return result

    updated_validation = copy.deepcopy(validation_doc)
    reduced_section = updated_validation.setdefault("reduced", {})
    if not isinstance(reduced_section, dict):
        result["errors"].append("validation.json reduced must be an object")
        return result

    required_failures = 0
    valid_required_count = 0
    required_count = 0

    for output in reduced_outputs:
        if not isinstance(output, dict):
            result["errors"].append(f"reduced output entry must be an object, got {type(output).__name__}")
            required_failures += 1
            continue

        required = bool(output.get("required", True))
        if required:
            required_count += 1

        try:
            summary = validate_reduced_output(case_dir=case_dir, output=output)
        except Exception as exc:
            name = str(output.get("name", "<unknown>"))
            kind = str(output.get("kind", "<unknown>"))
            summary = {
                "schema_version": 1,
                "output_name": name,
                "output_kind": kind,
                "ok": False,
                "validated_at": now_utc(),
                "path": str(output.get("path", "")),
                "allowed_suffixes": [],
                "min_rows": 0,
                "required_columns": [],
                "row_count": 0,
                "columns": [],
                "file": None,
                "errors": [str(exc)],
                "warnings": [],
            }

        summary = copy.deepcopy(summary)
        output_name = summary["output_name"]
        output_kind = summary["output_kind"]
        summary["required"] = required

        if summary.get("ok"):
            if required:
                valid_required_count += 1
        else:
            if required:
                required_failures += 1

        reduced_section[output_name] = summary
        result["outputs"].append(
            {
                "output_name": output_name,
                "output_kind": output_kind,
                "ok": bool(summary.get("ok")),
                "required": required,
                "row_count": summary.get("row_count", 0),
                "errors": list(summary.get("errors", [])),
                "warnings": list(summary.get("warnings", [])),
            }
        )

    case_ok = required_count > 0 and required_failures == 0 and valid_required_count == required_count
    result["case_ok"] = case_ok

    cleanup_reason = (
        "Reduced validation succeeded, but cleanup requires a later explicit eligibility phase."
        if case_ok
        else "Reduced validation failed. Cleanup is not allowed."
    )
    cleanup = updated_validation.setdefault("cleanup", {})
    if isinstance(cleanup, dict):
        cleanup["cleanup_allowed"] = False
        cleanup["reason"] = cleanup_reason
    else:
        updated_validation["cleanup"] = {
            "cleanup_allowed": False,
            "reason": cleanup_reason,
        }

    updated_validation["updated_at"] = now_utc()
    notes = updated_validation.setdefault("notes", [])
    if isinstance(notes, list):
        notes.append(
            {
                "timestamp": now_utc(),
                "operation": OPERATION,
                "message": "Reduced validation succeeded." if case_ok else "Reduced validation failed.",
            }
        )

    if case_ok:
        updated_state = reduced_validation_success_transition(state_doc)
        result["target_state"] = "Reduced_validated"
    else:
        updated_state = reduced_validation_failure_transition(state_doc)
        result["target_state"] = "Validation_failed"
        result["errors"].append("one or more required reduced outputs failed validation")

    result["actions"].append(f"write validation document: {validation_path}")
    result["actions"].append(f"transition state to {result['target_state']}: {state_path}")

    write_json_atomic(validation_path, updated_validation, dry_run=dry_run)
    write_json_atomic(state_path, updated_state, dry_run=dry_run)

    return result


def _configured_reduced_outputs(config: dict[str, Any]) -> list[Any]:
    analysis = config.get("analysis")
    if not isinstance(analysis, dict):
        return []

    outputs = analysis.get("outputs")
    if not isinstance(outputs, list):
        return []

    return outputs


def _validate_required_raw_evidence(config: dict[str, Any], validation_doc: dict[str, Any]) -> list[str]:
    errors: list[str] = []

    raw_diagnostics = config.get("raw_diagnostics")
    if not isinstance(raw_diagnostics, list) or not raw_diagnostics:
        return ["campaign.json must define a non-empty raw_diagnostics list before reduced validation"]

    raw_section = validation_doc.get("raw")
    if not isinstance(raw_section, dict):
        return ["validation.json raw must be an object before reduced validation"]

    required_count = 0
    for diagnostic in raw_diagnostics:
        if not isinstance(diagnostic, dict):
            errors.append(f"raw diagnostic entry must be an object, got {type(diagnostic).__name__}")
            continue

        if not bool(diagnostic.get("required", True)):
            continue

        required_count += 1
        name = diagnostic.get("name")
        if not isinstance(name, str) or not name.strip():
            errors.append("required raw diagnostic is missing a non-empty name")
            continue

        summary = raw_section.get(name)
        if not isinstance(summary, dict):
            errors.append(f"missing raw validation evidence for required diagnostic {name!r}")
            continue

        if summary.get("ok") is not True:
            errors.append(f"required raw diagnostic {name!r} is not validated ok")
            continue

        manifest_path = summary.get("manifest_path")
        if not isinstance(manifest_path, str) or not manifest_path.strip():
            errors.append(f"required raw diagnostic {name!r} has no manifest_path evidence")

    if required_count == 0:
        errors.append("campaign.json must define at least one required raw diagnostic before reduced validation")

    return errors


if __name__ == "__main__":
    raise SystemExit(main())