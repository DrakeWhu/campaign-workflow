from __future__ import annotations

import copy
from pathlib import Path
from typing import Any, Callable

from campaign_workflow.core.atomic_io import read_json, write_json_atomic
from campaign_workflow.core.path_safety import PathSafetyError, validate_relative_path
from campaign_workflow.core.state import (
    get_state_layout,
    now_utc,
    validate_state_document,
    validate_validation_document,
)
from campaign_workflow.core.transitions import state_name
from campaign_workflow.core.tsv_cases import CaseRecord


def mark_one_case_simulation_lifecycle(
    *,
    campaign_root: Path,
    config: dict[str, Any],
    case: CaseRecord,
    operation: str,
    target_state: str,
    marker_filename: str,
    timestamp_field: str,
    ok: bool,
    transition: Callable[[dict[str, Any]], dict[str, Any]],
    marker_extra: dict[str, Any] | None = None,
    evidence_extra: dict[str, Any] | None = None,
    dry_run: bool,
) -> dict[str, Any]:
    """Write a simulation lifecycle marker, update validation evidence, and transition state.

    This helper does not run simulations and performs no destructive operations.
    """
    layout = get_state_layout(config)
    case_dir = campaign_root / case.case_name
    state_path = case_dir / layout["state_file"]
    validation_path = case_dir / layout["validation_file"]

    result: dict[str, Any] = {
        "case_id": case.case_id,
        "case_name": case.case_name,
        "case_dir": str(case_dir),
        "current_state": None,
        "target_state": target_state,
        "marker_path": None,
        "would_mark": False,
        "marked": False,
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

    try:
        marker_rel = validate_relative_path(marker_filename, label=f"{operation}.marker")
    except PathSafetyError as exc:
        result["errors"].append(str(exc))
        return result

    marker_path = case_dir / marker_rel
    result["marker_path"] = str(marker_path)

    timestamp = now_utc()
    marker_extra = dict(marker_extra or {})
    evidence_extra = dict(evidence_extra or {})

    try:
        updated_state = transition(state_doc)
    except Exception as exc:
        result["errors"].append(str(exc))
        return result

    marker_doc: dict[str, Any] = {
        "schema_version": 1,
        "ok": ok,
        "operation": operation,
        "case_id": case.case_id,
        "case_name": case.case_name,
        timestamp_field: timestamp,
        "destructive_operations": 0,
    }
    marker_doc.update(_drop_none(marker_extra))

    updated_validation = copy.deepcopy(validation_doc)
    updated_validation["updated_at"] = timestamp

    simulation = updated_validation.setdefault("simulation", {})
    if not isinstance(simulation, dict):
        simulation = {}
        updated_validation["simulation"] = simulation

    simulation[operation] = {
        "schema_version": 1,
        "ok": ok,
        "operation": operation,
        "state_from": current,
        "state_to": target_state,
        timestamp_field: timestamp,
        "marker_path": marker_rel.as_posix(),
        "destructive_operations": 0,
        **_drop_none(evidence_extra),
    }

    simulation["latest"] = {
        "schema_version": 1,
        "ok": ok,
        "operation": operation,
        "state_from": current,
        "state_to": target_state,
        "timestamp": timestamp,
        "marker_path": marker_rel.as_posix(),
    }

    cleanup = updated_validation.setdefault("cleanup", {})
    if isinstance(cleanup, dict):
        cleanup["cleanup_allowed"] = False
        cleanup["reason"] = (
            "Simulation lifecycle evidence alone does not authorize cleanup. "
            "Raw and reduced validation are required."
        )
    else:
        updated_validation["cleanup"] = {
            "cleanup_allowed": False,
            "reason": (
                "Simulation lifecycle evidence alone does not authorize cleanup. "
                "Raw and reduced validation are required."
            ),
        }

    notes = updated_validation.setdefault("notes", [])
    if isinstance(notes, list):
        notes.append(
            {
                "timestamp": timestamp,
                "operation": operation,
                "message": f"Simulation lifecycle marker written: {marker_rel.as_posix()}",
            }
        )

    result["actions"].append(f"write marker: {marker_path}")
    result["actions"].append(f"write state transition {current} -> {target_state}: {state_path}")
    result["actions"].append(f"write simulation lifecycle evidence: {validation_path}")
    result["would_mark"] = dry_run
    result["marked"] = not dry_run

    write_json_atomic(marker_path, marker_doc, dry_run=dry_run)
    write_json_atomic(state_path, updated_state, dry_run=dry_run)
    write_json_atomic(validation_path, updated_validation, dry_run=dry_run)

    return result


def simulation_config(config: dict[str, Any]) -> dict[str, Any]:
    sim = config.get("simulation", {})
    return sim if isinstance(sim, dict) else {}


def _drop_none(data: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in data.items() if value is not None}