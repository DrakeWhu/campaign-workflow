from __future__ import annotations

import argparse
import copy
import sys
from dataclasses import asdict
from pathlib import Path
from typing import Any

from campaign_workflow.analysis.base import AnalysisAdapterResult, run_analysis_adapter
from campaign_workflow.analysis.csv_contract import validate_reduced_output
from campaign_workflow.core.atomic_io import read_json, write_json_atomic
from campaign_workflow.core.state import (
    get_state_layout,
    now_utc,
    validate_state_document,
    validate_validation_document,
)
from campaign_workflow.core.transitions import (
    analysis_failure_transition,
    analysis_start_transition,
    analysis_success_transition,
    ensure_analysis_start_compatible,
)
from campaign_workflow.core.tsv_cases import CaseRecord, load_campaign_config, load_cases
from campaign_workflow.core.validation_evidence import validate_required_raw_evidence


OPERATION = "analyze_case"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run configured case-local analysis adapters.")

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
        help="Restrict analysis to one case ID. Can be passed multiple times.",
    )

    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Check preconditions and print planned actions without running analysis or writing files.",
    )

    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Print per-case analysis and reduced-output details.",
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

    analysis = _configured_analysis(config)
    if analysis is None:
        print("ERROR: campaign.json must define an analysis object.", file=sys.stderr)
        return 1

    reduced_outputs = _configured_reduced_outputs(analysis)
    if not reduced_outputs:
        print("ERROR: campaign.json analysis.outputs must be a non-empty list.", file=sys.stderr)
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
    cases_ok = 0

    print(f"campaign_root={campaign_root}")
    print(f"campaign_name={config.get('campaign_name')}")
    print(f"selected_cases={len(cases)}")
    print(f"analysis_name={analysis.get('name')}")
    print(f"analysis_adapter={analysis.get('adapter', analysis.get('kind'))}")

    for case in cases:
        result = analyze_one_case(
            campaign_root=campaign_root,
            config=config,
            case=case,
            analysis=analysis,
            reduced_outputs=reduced_outputs,
            dry_run=args.dry_run,
        )

        errors = result["errors"]
        total_errors += len(errors)
        if errors:
            cases_with_errors += 1
        if result.get("case_ok"):
            cases_ok += 1

        if args.verbose or errors:
            print()
            print(f"[case {case.case_id}] {case.case_name}")
            print(f"  case_ok={result.get('case_ok')}")
            print(f"  target_state={result.get('target_state')}")
            print(f"  adapter_ok={result.get('adapter_ok')}")

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

    if total_errors:
        return 1
    return 0


def analyze_one_case(
    *,
    campaign_root: Path,
    config: dict[str, Any],
    case: CaseRecord,
    analysis: dict[str, Any],
    reduced_outputs: list[Any],
    dry_run: bool,
) -> dict[str, Any]:
    layout = get_state_layout(config)
    case_dir = campaign_root / case.case_name
    state_path = case_dir / layout["state_file"]
    validation_path = case_dir / layout["validation_file"]
    analysis_name = _analysis_name(analysis)

    result: dict[str, Any] = {
        "case_id": case.case_id,
        "case_name": case.case_name,
        "case_dir": str(case_dir),
        "case_ok": False,
        "target_state": None,
        "adapter_ok": None,
        "outputs": [],
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

    try:
        ensure_analysis_start_compatible(state_doc)
    except Exception as exc:
        result["errors"].append(str(exc))
        return result

    raw_evidence_errors = validate_required_raw_evidence(config, validation_doc)
    if raw_evidence_errors:
        result["errors"].extend(raw_evidence_errors)
        return result

    if dry_run:
        result["case_ok"] = True
        result["target_state"] = "Analyzing"
        result["actions"].append(f"transition state to Analyzing: {state_path}")
        result["actions"].append(f"run analysis adapter {analysis.get('adapter', analysis.get('kind'))!r} in {case_dir}")
        result["actions"].append(f"write analysis logs under: {case_dir / layout['logs_dir']}")
        result["actions"].append(f"write analysis metadata under validation document: {validation_path}")
        result["actions"].append("validate configured reduced outputs after analysis completes")
        result["warnings"].append("dry-run does not execute the analysis adapter, so final output validity is not known")
        return result

    started_state = analysis_start_transition(state_doc)
    result["actions"].append(f"transition state to Analyzing: {state_path}")
    write_json_atomic(state_path, started_state)

    adapter_result = run_analysis_adapter(
        campaign_root=campaign_root,
        case_dir=case_dir,
        case=case,
        config=config,
        analysis=analysis,
    )
    result["adapter_ok"] = adapter_result.ok
    result["warnings"].extend(adapter_result.warnings)
    result["actions"].append(f"run analysis adapter {adapter_result.adapter!r} in {case_dir}")

    log_info = _write_analysis_logs(
        case_dir=case_dir,
        logs_dir=layout["logs_dir"],
        analysis_name=analysis_name,
        adapter_result=adapter_result,
    )
    if log_info.get("stdout_path"):
        result["actions"].append(f"write analysis stdout log: {case_dir / log_info['stdout_path']}")
    if log_info.get("stderr_path"):
        result["actions"].append(f"write analysis stderr log: {case_dir / log_info['stderr_path']}")

    updated_validation = copy.deepcopy(validation_doc)
    analysis_summary = _analysis_summary(
        analysis_name=analysis_name,
        analysis=analysis,
        adapter_result=adapter_result,
        log_info=log_info,
    )

    if not adapter_result.ok:
        result["errors"].extend(adapter_result.errors)
        analysis_summary["ok"] = False
        analysis_summary["final_state"] = "Analysis_failed"
        analysis_summary["reduced_validation_ok"] = False
        _store_analysis_summary(updated_validation, analysis_name, analysis_summary)
        _set_cleanup_blocked(updated_validation, "Analysis failed. Cleanup is not allowed.")
        _append_analysis_note(updated_validation, analysis_name=analysis_name, ok=False)

        failed_marker = _analysis_marker(
            case=case,
            analysis_name=analysis_name,
            analysis_summary=analysis_summary,
            reduced_summaries=[],
            ok=False,
        )
        failed_marker_path = case_dir / layout["post_dir"] / "analysis_failed.json"
        result["actions"].append(f"write analysis failure marker: {failed_marker_path}")
        write_json_atomic(failed_marker_path, failed_marker)

        final_state = analysis_failure_transition(started_state, reason="analysis adapter failed")
        result["target_state"] = "Analysis_failed"
        result["actions"].append(f"write validation document: {validation_path}")
        result["actions"].append(f"transition state to Analysis_failed: {state_path}")
        write_json_atomic(validation_path, updated_validation)
        write_json_atomic(state_path, final_state)
        return result

    reduced_summaries, output_results, required_errors = _validate_reduced_outputs(
        case_dir=case_dir,
        reduced_outputs=reduced_outputs,
    )
    result["outputs"].extend(output_results)

    reduced_section = updated_validation.setdefault("reduced", {})
    if not isinstance(reduced_section, dict):
        result["errors"].append("validation.json reduced must be an object")
        required_errors.append("validation.json reduced must be an object")
    else:
        for summary in reduced_summaries:
            reduced_section[summary["output_name"]] = summary

    reduced_ok = not required_errors
    analysis_summary["ok"] = reduced_ok
    analysis_summary["reduced_validation_ok"] = reduced_ok
    analysis_summary["final_state"] = "Reduced_validated" if reduced_ok else "Analysis_failed"
    _store_analysis_summary(updated_validation, analysis_name, analysis_summary)
    _append_analysis_note(updated_validation, analysis_name=analysis_name, ok=reduced_ok)

    marker = _analysis_marker(
        case=case,
        analysis_name=analysis_name,
        analysis_summary=analysis_summary,
        reduced_summaries=reduced_summaries,
        ok=reduced_ok,
    )

    if reduced_ok:
        _set_cleanup_blocked(
            updated_validation,
            "Analysis and reduced validation succeeded, but cleanup requires a later explicit eligibility phase.",
        )
        done_marker_path = case_dir / layout["post_dir"] / "analysis_done.json"
        result["actions"].append(f"write analysis done marker: {done_marker_path}")
        write_json_atomic(done_marker_path, marker)

        final_state = analysis_success_transition(started_state)
        result["case_ok"] = True
        result["target_state"] = "Reduced_validated"
        result["actions"].append(f"write validation document: {validation_path}")
        result["actions"].append(f"transition state to Reduced_validated: {state_path}")
        write_json_atomic(validation_path, updated_validation)
        write_json_atomic(state_path, final_state)
        return result

    result["errors"].extend(required_errors)
    result["errors"].append("analysis completed, but one or more required reduced outputs failed validation")
    _set_cleanup_blocked(updated_validation, "Analysis output validation failed. Cleanup is not allowed.")

    failed_marker_path = case_dir / layout["post_dir"] / "analysis_failed.json"
    result["actions"].append(f"write analysis failure marker: {failed_marker_path}")
    write_json_atomic(failed_marker_path, marker)

    final_state = analysis_failure_transition(started_state, reason="analysis completed but reduced output validation failed")
    result["target_state"] = "Analysis_failed"
    result["actions"].append(f"write validation document: {validation_path}")
    result["actions"].append(f"transition state to Analysis_failed: {state_path}")
    write_json_atomic(validation_path, updated_validation)
    write_json_atomic(state_path, final_state)
    return result


def _configured_analysis(config: dict[str, Any]) -> dict[str, Any] | None:
    analysis = config.get("analysis")
    if not isinstance(analysis, dict):
        return None
    return analysis


def _configured_reduced_outputs(analysis: dict[str, Any]) -> list[Any]:
    outputs = analysis.get("outputs")
    if not isinstance(outputs, list):
        return []
    return outputs


def _analysis_name(analysis: dict[str, Any]) -> str:
    name = analysis.get("name", "analysis")
    if not isinstance(name, str) or not name.strip():
        return "analysis"
    return name.strip()


def _validate_reduced_outputs(*, case_dir: Path, reduced_outputs: list[Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[str]]:
    summaries: list[dict[str, Any]] = []
    output_results: list[dict[str, Any]] = []
    required_errors: list[str] = []

    required_count = 0
    valid_required_count = 0

    for output in reduced_outputs:
        if not isinstance(output, dict):
            message = f"reduced output entry must be an object, got {type(output).__name__}"
            required_errors.append(message)
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
        summary["required"] = required
        summary["legacy_reduced_only"] = False
        summaries.append(summary)

        if summary.get("ok"):
            if required:
                valid_required_count += 1
        else:
            if required:
                required_errors.extend(str(error) for error in summary.get("errors", []))

        output_results.append(
            {
                "output_name": summary["output_name"],
                "output_kind": summary["output_kind"],
                "ok": bool(summary.get("ok")),
                "required": required,
                "row_count": summary.get("row_count", 0),
                "errors": list(summary.get("errors", [])),
                "warnings": list(summary.get("warnings", [])),
            }
        )

    if required_count == 0:
        required_errors.append("analysis.outputs must contain at least one required output")
    elif valid_required_count != required_count:
        required_errors.append("one or more required reduced outputs failed validation")

    return summaries, output_results, required_errors


def _analysis_summary(
    *,
    analysis_name: str,
    analysis: dict[str, Any],
    adapter_result: AnalysisAdapterResult,
    log_info: dict[str, Any],
) -> dict[str, Any]:
    summary = asdict(adapter_result)
    summary.update(
        {
            "schema_version": 1,
            "analysis_name": analysis_name,
            "analysis_kind": analysis.get("kind"),
            "analysis_adapter": analysis.get("adapter", analysis.get("kind")),
            "updated_at": now_utc(),
            "operation": OPERATION,
            "logs": log_info,
            "cleanup_allowed": False,
        }
    )
    summary.pop("stdout", None)
    summary.pop("stderr", None)
    return summary


def _write_analysis_logs(
    *,
    case_dir: Path,
    logs_dir: str,
    analysis_name: str,
    adapter_result: AnalysisAdapterResult,
) -> dict[str, Any]:
    logs_path = case_dir / logs_dir
    logs_path.mkdir(parents=True, exist_ok=True)

    stdout_rel = Path(logs_dir) / f"analysis_{analysis_name}_stdout.txt"
    stderr_rel = Path(logs_dir) / f"analysis_{analysis_name}_stderr.txt"

    (case_dir / stdout_rel).write_text(adapter_result.stdout, encoding="utf-8", newline="\n")
    (case_dir / stderr_rel).write_text(adapter_result.stderr, encoding="utf-8", newline="\n")

    return {
        "stdout_path": stdout_rel.as_posix(),
        "stderr_path": stderr_rel.as_posix(),
        "stdout_bytes": len(adapter_result.stdout.encode("utf-8")),
        "stderr_bytes": len(adapter_result.stderr.encode("utf-8")),
    }


def _store_analysis_summary(validation_doc: dict[str, Any], analysis_name: str, summary: dict[str, Any]) -> None:
    section = validation_doc.setdefault("analysis", {})
    if not isinstance(section, dict):
        validation_doc["analysis"] = {}
        section = validation_doc["analysis"]
    section[analysis_name] = summary
    validation_doc["updated_at"] = now_utc()


def _set_cleanup_blocked(validation_doc: dict[str, Any], reason: str) -> None:
    cleanup = validation_doc.setdefault("cleanup", {})
    if not isinstance(cleanup, dict):
        validation_doc["cleanup"] = {}
        cleanup = validation_doc["cleanup"]
    cleanup["cleanup_allowed"] = False
    cleanup["reason"] = reason


def _append_analysis_note(validation_doc: dict[str, Any], *, analysis_name: str, ok: bool) -> None:
    notes = validation_doc.setdefault("notes", [])
    if isinstance(notes, list):
        notes.append(
            {
                "timestamp": now_utc(),
                "operation": OPERATION,
                "analysis_name": analysis_name,
                "message": "Analysis and reduced validation succeeded." if ok else "Analysis failed.",
            }
        )


def _analysis_marker(
    *,
    case: CaseRecord,
    analysis_name: str,
    analysis_summary: dict[str, Any],
    reduced_summaries: list[dict[str, Any]],
    ok: bool,
) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "created_at": now_utc(),
        "operation": OPERATION,
        "case_id": case.case_id,
        "case_name": case.case_name,
        "analysis_name": analysis_name,
        "ok": ok,
        "analysis": analysis_summary,
        "reduced_outputs": [
            {
                "output_name": summary.get("output_name"),
                "output_kind": summary.get("output_kind"),
                "ok": summary.get("ok"),
                "path": summary.get("path"),
                "row_count": summary.get("row_count"),
                "required": summary.get("required"),
                "errors": summary.get("errors", []),
            }
            for summary in reduced_summaries
        ],
        "destructive_operations": 0,
    }


if __name__ == "__main__":
    raise SystemExit(main())