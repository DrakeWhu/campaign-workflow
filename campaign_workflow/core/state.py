from __future__ import annotations

import getpass
import os
import socket
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from campaign_workflow.core.atomic_io import read_json, write_json_atomic
from campaign_workflow.core.tsv_cases import CaseRecord


VALID_STATES = {
    "Created",
    "Submitted",
    "Running",
    "Sim_done",
    "Raw_validated",
    "Analyzing",
    "Reduced_validated",
    "Raw_delete_eligible",
    "Raw_deleted",
    "Failed",
    "Retryable",
    "Stale",
    "Quarantined",
    "Validation_failed",
    "Analysis_failed",
    "Cleanup_failed",
    "Disk_wait",
}


def now_utc() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def actor() -> dict[str, Any]:
    return {
        "hostname": socket.gethostname(),
        "pid": os.getpid(),
        "user": getpass.getuser(),
    }


def get_state_layout(config: dict[str, Any]) -> dict[str, str]:
    state_cfg = config.get("state", {})

    return {
        "state_file": state_cfg.get("state_file", "state.json"),
        "validation_file": state_cfg.get("validation_file", "validation.json"),
        "locks_dir": state_cfg.get("locks_dir", "locks"),
        "manifests_dir": state_cfg.get("manifests_dir", "manifests"),
        "post_dir": state_cfg.get("post_dir", "post"),
        "logs_dir": state_cfg.get("logs_dir", "logs"),
    }


def initial_state_document(case: CaseRecord, *, operation: str = "init_case_states") -> dict[str, Any]:
    timestamp = now_utc()

    return {
        "schema_version": 1,
        "case_id": case.case_id,
        "case_name": case.case_name,
        "state": "Created",
        "created_at": timestamp,
        "updated_at": timestamp,
        "history": [
            {
                "timestamp": timestamp,
                "from": None,
                "to": "Created",
                "operation": operation,
                "reason": "initialized from case manifest",
                "actor": actor(),
            }
        ],
    }


def initial_validation_document(case: CaseRecord) -> dict[str, Any]:
    timestamp = now_utc()

    return {
        "schema_version": 1,
        "case_id": case.case_id,
        "case_name": case.case_name,
        "created_at": timestamp,
        "updated_at": timestamp,
        "raw": {},
        "reduced": {},
        "cleanup": {
            "cleanup_allowed": False,
            "reason": "No raw/reduced validation has been performed yet.",
        },
        "notes": [
            {
                "timestamp": timestamp,
                "message": "Initialized validation document. No validation has been performed yet.",
            }
        ],
    }


def validate_state_document(data: dict[str, Any], case: CaseRecord) -> list[str]:
    errors: list[str] = []

    if data.get("schema_version") != 1:
        errors.append("state.json schema_version must be 1")

    if data.get("case_id") != case.case_id:
        errors.append(f"state.json case_id mismatch: expected {case.case_id}, got {data.get('case_id')!r}")

    if data.get("case_name") != case.case_name:
        errors.append(f"state.json case_name mismatch: expected {case.case_name!r}, got {data.get('case_name')!r}")

    state = data.get("state")
    if state not in VALID_STATES:
        errors.append(f"state.json invalid state: {state!r}")

    history = data.get("history")
    if not isinstance(history, list) or not history:
        errors.append("state.json history must be a non-empty list")

    if "created_at" not in data:
        errors.append("state.json missing created_at")

    if "updated_at" not in data:
        errors.append("state.json missing updated_at")

    return errors


def validate_validation_document(data: dict[str, Any], case: CaseRecord) -> list[str]:
    errors: list[str] = []

    if data.get("schema_version") != 1:
        errors.append("validation.json schema_version must be 1")

    if data.get("case_id") != case.case_id:
        errors.append(f"validation.json case_id mismatch: expected {case.case_id}, got {data.get('case_id')!r}")

    if data.get("case_name") != case.case_name:
        errors.append(f"validation.json case_name mismatch: expected {case.case_name!r}, got {data.get('case_name')!r}")

    if not isinstance(data.get("raw"), dict):
        errors.append("validation.json raw must be an object")

    if not isinstance(data.get("reduced"), dict):
        errors.append("validation.json reduced must be an object")

    cleanup = data.get("cleanup")
    if not isinstance(cleanup, dict):
        errors.append("validation.json cleanup must be an object")
    elif not isinstance(cleanup.get("cleanup_allowed"), bool):
        errors.append("validation.json cleanup.cleanup_allowed must be boolean")

    if "created_at" not in data:
        errors.append("validation.json missing created_at")

    if "updated_at" not in data:
        errors.append("validation.json missing updated_at")

    return errors


def ensure_case_state(
    *,
    case_dir: Path,
    case: CaseRecord,
    config: dict[str, Any],
    dry_run: bool,
    create_missing_case_dirs: bool,
    check_only: bool,
) -> dict[str, Any]:
    """Initialize or check one case directory.

    This function performs no destructive operations.
    """
    layout = get_state_layout(config)

    result: dict[str, Any] = {
        "case_id": case.case_id,
        "case_name": case.case_name,
        "case_dir": str(case_dir),
        "actions": [],
        "errors": [],
    }

    state_path = case_dir / layout["state_file"]
    validation_path = case_dir / layout["validation_file"]

    runtime_dirs = [
        case_dir / layout["locks_dir"],
        case_dir / layout["manifests_dir"],
        case_dir / layout["post_dir"],
        case_dir / layout["logs_dir"],
    ]

    if not case_dir.exists():
        if check_only:
            result["errors"].append(f"missing case directory: {case_dir}")
            return result

        if create_missing_case_dirs:
            result["actions"].append(f"create directory: {case_dir}")
            if not dry_run:
                case_dir.mkdir(parents=True, exist_ok=True)
        else:
            result["errors"].append(
                f"missing case directory: {case_dir} "
                "(use --create-missing-case-dirs only for test/fake campaigns or deliberate initialization)"
            )
            return result

    if not case_dir.is_dir():
        result["errors"].append(f"case path exists but is not a directory: {case_dir}")
        return result

    if not check_only:
        for directory in runtime_dirs:
            if not directory.exists():
                result["actions"].append(f"create directory: {directory}")
                if not dry_run:
                    directory.mkdir(parents=True, exist_ok=True)
            elif not directory.is_dir():
                result["errors"].append(f"runtime path exists but is not a directory: {directory}")

    if result["errors"]:
        return result

    if state_path.exists():
        try:
            state_data = read_json(state_path)
            result["errors"].extend(validate_state_document(state_data, case))
            result["actions"].append(f"checked existing state file: {state_path}")
        except Exception as exc:
            result["errors"].append(f"failed to read/validate state file {state_path}: {exc}")
    else:
        if check_only:
            result["errors"].append(f"missing state file: {state_path}")
        else:
            result["actions"].append(f"create state file: {state_path}")
            write_json_atomic(state_path, initial_state_document(case), dry_run=dry_run)

    if validation_path.exists():
        try:
            validation_data = read_json(validation_path)
            result["errors"].extend(validate_validation_document(validation_data, case))
            result["actions"].append(f"checked existing validation file: {validation_path}")
        except Exception as exc:
            result["errors"].append(f"failed to read/validate validation file {validation_path}: {exc}")
    else:
        if check_only:
            result["errors"].append(f"missing validation file: {validation_path}")
        else:
            result["actions"].append(f"create validation file: {validation_path}")
            write_json_atomic(validation_path, initial_validation_document(case), dry_run=dry_run)

    return result
