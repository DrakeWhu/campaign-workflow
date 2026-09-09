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
from campaign_workflow.core.state import (
    get_state_layout,
    now_utc,
    validate_state_document,
    validate_validation_document,
)
from campaign_workflow.core.transitions import mark_sim_done_transition, state_name
from campaign_workflow.core.tsv_cases import CaseRecord, load_campaign_config, load_cases

OPERATION = "mark_sim_done"
RUNTIME_EVIDENCE_MODE = "runtime_success_receipt"
ADOPTION_EVIDENCE_MODE = "historical_adoption"

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

RUNTIME_IDENTITY_FIELDS = (
    "scheduler",
    "scheduler_job_id",
    "scheduler_array_task_id",
    "run_command",
    "environment_name",
    "stdout_log",
    "stderr_log",
)

REQUIRED_RUNTIME_RECEIPT_FIELDS = (
    "scheduler_job_id",
    "scheduler_array_task_id",
    "run_command",
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Mark Sim_done either from a matching runtime-success receipt or by "
            "explicit historical adoption of existing completion evidence."
        )
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
        help="Restrict operation to one case ID. Can be passed multiple times for adoption.",
    )

    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument(
        "--runtime-success-receipt",
        action="store_true",
        help=(
            "Finalize the current Running execution. Requires return_code=0 and "
            "identity matching the existing mark_sim_running evidence."
        ),
    )
    mode.add_argument(
        "--adopt-existing-evidence",
        action="store_true",
        help=(
            "Explicitly backfill/adopt historical completion evidence. Existing raw "
            "diagnostics or a valid legacy completion marker may be used."
        ),
    )

    parser.add_argument("--scheduler", default=None, help="Scheduler/backend name, e.g. slurm.")
    parser.add_argument("--scheduler-job-id", default=None, help="Scheduler job ID for the current execution.")
    parser.add_argument(
        "--scheduler-array-task-id",
        default=None,
        help="Scheduler array task ID for the current execution.",
    )
    parser.add_argument(
        "--run-command",
        default=None,
        help="Command used to run the current simulation execution.",
    )
    parser.add_argument("--environment-name", default=None, help="Simulation environment name.")
    parser.add_argument("--stdout-log", default=None, help="Case-relative stdout log path.")
    parser.add_argument("--stderr-log", default=None, help="Case-relative stderr log path.")
    parser.add_argument(
        "--return-code",
        type=int,
        default=None,
        help="Simulation process return code. Runtime success requires exactly 0.",
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

    if args.runtime_success_receipt and len(cases) != 1:
        print(
            "ERROR: --runtime-success-receipt requires exactly one selected case",
            file=sys.stderr,
        )
        return 1

    runtime_receipt: dict[str, Any] | None = None
    evidence_mode = ADOPTION_EVIDENCE_MODE
    if args.runtime_success_receipt:
        evidence_mode = RUNTIME_EVIDENCE_MODE
        runtime_receipt = _runtime_receipt_from_args(args, config)
        receipt_errors = _validate_runtime_receipt_shape(runtime_receipt)
        if receipt_errors:
            for error in receipt_errors:
                print(f"ERROR: {error}", file=sys.stderr)
            return 1

    total_errors = 0
    cases_with_errors = 0
    cases_marked = 0
    cases_already_done = 0
    cases_with_evidence = 0
    total_actions = 0

    print(f"campaign_root={campaign_root}")
    print(f"campaign_name={config.get('campaign_name')}")
    print(f"selected_cases={len(cases)}")
    print(f"evidence_mode={evidence_mode}")

    for case in cases:
        result = mark_one_case_sim_done(
            campaign_root=campaign_root,
            config=config,
            case=case,
            dry_run=args.dry_run,
            evidence_mode=evidence_mode,
            runtime_receipt=runtime_receipt,
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
            print(f"  evidence_mode={result.get('evidence_mode')}")
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
    evidence_mode: str,
    runtime_receipt: dict[str, Any] | None,
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
        "evidence_mode": evidence_mode,
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

    if evidence_mode == RUNTIME_EVIDENCE_MODE:
        if current != "Running":
            result["errors"].append(
                f"runtime completion requires state 'Running', got {current!r}"
            )
            return result
        if runtime_receipt is None:
            result["errors"].append("runtime completion is missing its success receipt")
            return result
        evidence = collect_runtime_success_evidence(
            case_dir=case_dir,
            case=case,
            validation_doc=validation_doc,
            runtime_receipt=runtime_receipt,
        )
    elif evidence_mode == ADOPTION_EVIDENCE_MODE:
        if current not in {"Created", "Running"}:
            result["errors"].append(
                f"historical adoption cannot mark simulation done from state {current!r}; "
                "expected one of: Created, Running"
            )
            return result
        evidence = collect_sim_done_evidence(
            case_dir=case_dir,
            config=config,
            case=case,
        )
    else:
        result["errors"].append(f"unknown completion evidence mode: {evidence_mode!r}")
        return result

    result["evidence_ok"] = evidence["ok"]
    result["evidence"] = evidence["evidence"]
    result["warnings"] = evidence["warnings"]

    if not evidence["ok"]:
        result["errors"].extend(evidence["errors"])
        return result

    timestamp = now_utc()
    marker_rel = _completion_marker_path(config)
    marker_path = case_dir / marker_rel

    if evidence_mode == RUNTIME_EVIDENCE_MODE:
        reason = "matching runtime execution receipt confirms successful simulation completion"
        note_message = "Simulation marked as done from matching runtime success evidence."
    else:
        reason = "historical completion evidence deliberately adopted"
        note_message = "Simulation marked as done by explicit historical evidence adoption."

    updated_state = mark_sim_done_transition(
        state_doc,
        reason=reason,
    )

    marker_doc: dict[str, Any] = {
        "schema_version": 1,
        "ok": True,
        "operation": OPERATION,
        "case_id": case.case_id,
        "case_name": case.case_name,
        "finished_at": timestamp,
        "evidence_mode": evidence_mode,
        "evidence": evidence["evidence"],
        "warnings": evidence["warnings"],
        "errors": [],
        "destructive_operations": 0,
    }
    if evidence_mode == RUNTIME_EVIDENCE_MODE:
        marker_doc["return_code"] = 0
        marker_doc["runtime_receipt"] = dict(runtime_receipt or {})
    else:
        marker_doc["adopted_existing_evidence"] = True

    updated_validation = copy.deepcopy(validation_doc)
    updated_validation["updated_at"] = timestamp
    simulation_evidence: dict[str, Any] = {
        "schema_version": 1,
        "ok": True,
        "marked_at": timestamp,
        "operation": OPERATION,
        "state_from": current,
        "state_to": "Sim_done",
        "marker_path": marker_rel.as_posix(),
        "evidence_mode": evidence_mode,
        "evidence": evidence["evidence"],
        "warnings": evidence["warnings"],
        "errors": [],
        "destructive_operations": 0,
        "latest": {
            "schema_version": 1,
            "ok": True,
            "operation": OPERATION,
            "state_from": current,
            "state_to": "Sim_done",
            "timestamp": timestamp,
            "marker_path": marker_rel.as_posix(),
            "evidence_mode": evidence_mode,
        },
    }
    if evidence_mode == RUNTIME_EVIDENCE_MODE:
        simulation_evidence["return_code"] = 0
        simulation_evidence["runtime_receipt"] = dict(runtime_receipt or {})
    else:
        simulation_evidence["adopted_existing_evidence"] = True

    updated_validation["simulation"] = simulation_evidence

    cleanup = updated_validation.setdefault("cleanup", {})
    if isinstance(cleanup, dict):
        cleanup["cleanup_allowed"] = False
        cleanup["reason"] = (
            "Simulation completion alone does not authorize cleanup. "
            "Raw and reduced validation are required."
        )
    else:
        updated_validation["cleanup"] = {
            "cleanup_allowed": False,
            "reason": (
                "Simulation completion alone does not authorize cleanup. "
                "Raw and reduced validation are required."
            ),
        }

    notes = updated_validation.setdefault("notes", [])
    if isinstance(notes, list):
        notes.append(
            {
                "timestamp": timestamp,
                "operation": OPERATION,
                "evidence_mode": evidence_mode,
                "message": note_message,
            }
        )

    result["target_state"] = "Sim_done"
    result["actions"].append(f"write marker: {marker_path}")
    result["actions"].append(f"write state transition {current} -> Sim_done: {state_path}")
    result["actions"].append(f"write simulation evidence: {validation_path}")
    result["would_mark"] = dry_run
    result["marked"] = not dry_run

    write_json_atomic(marker_path, marker_doc, dry_run=dry_run)
    write_json_atomic(state_path, updated_state, dry_run=dry_run)
    write_json_atomic(validation_path, updated_validation, dry_run=dry_run)

    return result


def collect_runtime_success_evidence(
    *,
    case_dir: Path,
    case: CaseRecord,
    validation_doc: dict[str, Any],
    runtime_receipt: dict[str, Any],
) -> dict[str, Any]:
    evidence: list[str] = []
    warnings: list[str] = []
    errors: list[str] = []

    if runtime_receipt.get("return_code") != 0:
        errors.append(
            f"runtime success receipt requires return_code=0, got {runtime_receipt.get('return_code')!r}"
        )

    simulation = validation_doc.get("simulation")
    running_evidence = simulation.get("mark_sim_running") if isinstance(simulation, dict) else None
    if not isinstance(running_evidence, dict):
        errors.append("missing validation.simulation.mark_sim_running runtime evidence")
        return _evidence_result(evidence, warnings, errors)

    if running_evidence.get("ok") is not True:
        errors.append("validation.simulation.mark_sim_running is not successful")
    if running_evidence.get("operation") != "mark_sim_running":
        errors.append("validation.simulation.mark_sim_running has unexpected operation")
    if running_evidence.get("state_to") != "Running":
        errors.append("validation.simulation.mark_sim_running does not target Running")

    marker_raw = running_evidence.get("marker_path", "post/sim_running.json")
    try:
        marker_rel = validate_relative_path(marker_raw, label="mark_sim_running.marker_path")
        marker_path = resolve_existing_path_inside_case(
            case_dir,
            marker_rel,
            label="mark_sim_running.marker_path",
        )
        running_marker = read_json(marker_path)
    except Exception as exc:
        errors.append(f"failed to read current running marker: {exc}")
        return _evidence_result(evidence, warnings, errors)

    if not isinstance(running_marker, dict):
        errors.append("current running marker is not a JSON object")
        return _evidence_result(evidence, warnings, errors)

    if running_marker.get("ok") is not True:
        errors.append("current running marker is not successful")
    if running_marker.get("operation") != "mark_sim_running":
        errors.append("current running marker has unexpected operation")
    if running_marker.get("case_id") != case.case_id:
        errors.append(
            f"current running marker case_id mismatch: expected {case.case_id}, "
            f"got {running_marker.get('case_id')!r}"
        )
    if running_marker.get("case_name") != case.case_name:
        errors.append(
            f"current running marker case_name mismatch: expected {case.case_name!r}, "
            f"got {running_marker.get('case_name')!r}"
        )

    running_started = running_evidence.get("started_at")
    marker_started = running_marker.get("started_at")
    if running_started is not None and marker_started is not None and running_started != marker_started:
        errors.append("running marker timestamp does not match validation runtime evidence")

    for field in RUNTIME_IDENTITY_FIELDS:
        observed = runtime_receipt.get(field)
        if observed is None:
            continue
        validation_value = running_evidence.get(field)
        marker_value = running_marker.get(field)
        if validation_value != observed:
            errors.append(
                f"runtime receipt {field} mismatch with validation mark_sim_running: "
                f"receipt={observed!r}, running={validation_value!r}"
            )
        if marker_value != observed:
            errors.append(
                f"runtime receipt {field} mismatch with running marker: "
                f"receipt={observed!r}, marker={marker_value!r}"
            )

    if not errors:
        evidence.append("runtime return_code=0")
        evidence.append(
            "runtime identity matches mark_sim_running evidence: "
            f"scheduler_job_id={runtime_receipt.get('scheduler_job_id')} "
            f"scheduler_array_task_id={runtime_receipt.get('scheduler_array_task_id')}"
        )
        evidence.append(f"running marker identity matches case: {marker_rel.as_posix()}")

    return _evidence_result(evidence, warnings, errors)


def collect_sim_done_evidence(
    *,
    case_dir: Path,
    config: dict[str, Any],
    case: CaseRecord,
) -> dict[str, Any]:
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
                resolved = resolve_existing_path_inside_case(
                    case_dir,
                    marker_rel,
                    label="simulation.completion_marker",
                )
                if not resolved.is_file():
                    warnings.append(
                        f"completion marker exists but is not a regular file: {marker_rel.as_posix()}"
                    )
                else:
                    marker_doc = read_json(resolved)
                    marker_errors = _validate_adoptable_completion_marker(marker_doc, case)
                    if marker_errors:
                        for error in marker_errors:
                            warnings.append(
                                f"completion marker rejected: {marker_rel.as_posix()}: {error}"
                            )
                    else:
                        marker_ok = True
                        evidence.append(
                            f"valid historical completion marker: {marker_rel.as_posix()}"
                        )
            else:
                warnings.append(f"completion marker not found: {marker_rel.as_posix()}")
        except PathSafetyError as exc:
            errors.append(str(exc))
        except Exception as exc:
            warnings.append(f"completion marker rejected: {marker!r}: {exc}")

    raw_ok = False
    raw_diagnostics = config.get("raw_diagnostics")
    if isinstance(raw_diagnostics, list) and raw_diagnostics:
        required_diagnostics = [
            diag
            for diag in raw_diagnostics
            if isinstance(diag, dict) and bool(diag.get("required", True))
        ]
        if required_diagnostics:
            raw_failures = 0
            for diagnostic in required_diagnostics:
                name = diagnostic.get("name", "<unnamed>")
                raw_glob = diagnostic.get("glob")
                min_files = _nonnegative_int(
                    diagnostic.get("min_files", 1),
                    f"raw_diagnostics[{name!r}].min_files",
                )

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
        errors.append("no acceptable historical simulation completion evidence found")

    return _evidence_result(evidence, warnings, errors)


def _runtime_receipt_from_args(
    args: argparse.Namespace,
    config: dict[str, Any],
) -> dict[str, Any]:
    sim = config.get("simulation")
    sim = sim if isinstance(sim, dict) else {}
    return {
        "scheduler": args.scheduler or sim.get("scheduler"),
        "scheduler_job_id": args.scheduler_job_id,
        "scheduler_array_task_id": args.scheduler_array_task_id,
        "run_command": args.run_command,
        "environment_name": args.environment_name or sim.get("environment_name"),
        "stdout_log": args.stdout_log,
        "stderr_log": args.stderr_log,
        "return_code": args.return_code,
    }


def _validate_runtime_receipt_shape(receipt: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    if receipt.get("return_code") != 0:
        errors.append(
            f"--runtime-success-receipt requires --return-code 0, got {receipt.get('return_code')!r}"
        )
    for field in REQUIRED_RUNTIME_RECEIPT_FIELDS:
        value = receipt.get(field)
        if value is None or str(value).strip() == "":
            errors.append(f"--runtime-success-receipt requires non-empty {field}")
    return errors


def _validate_adoptable_completion_marker(
    marker_doc: Any,
    case: CaseRecord,
) -> list[str]:
    errors: list[str] = []
    if not isinstance(marker_doc, dict):
        return ["marker is not a JSON object"]
    if marker_doc.get("ok") is not True:
        errors.append("marker ok is not true")
    if marker_doc.get("operation") != OPERATION:
        errors.append(f"marker operation is not {OPERATION!r}")
    if marker_doc.get("case_id") != case.case_id:
        errors.append(
            f"marker case_id mismatch: expected {case.case_id}, got {marker_doc.get('case_id')!r}"
        )
    if marker_doc.get("case_name") != case.case_name:
        errors.append(
            f"marker case_name mismatch: expected {case.case_name!r}, "
            f"got {marker_doc.get('case_name')!r}"
        )
    if "return_code" in marker_doc and marker_doc.get("return_code") != 0:
        errors.append(
            f"marker return_code is not zero: {marker_doc.get('return_code')!r}"
        )
    return errors


def _completion_marker_path(config: dict[str, Any]) -> Path:
    marker = config.get("simulation", {}).get("completion_marker") if isinstance(config.get("simulation"), dict) else None
    if not isinstance(marker, str) or not marker.strip():
        marker = "post/sim_done.json"
    return validate_relative_path(marker, label="simulation.completion_marker")


def _evidence_result(
    evidence: list[str],
    warnings: list[str],
    errors: list[str],
) -> dict[str, Any]:
    return {
        "ok": not errors,
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
