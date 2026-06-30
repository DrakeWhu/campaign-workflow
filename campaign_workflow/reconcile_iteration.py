from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence

from campaign_workflow.core.atomic_io import read_json
from campaign_workflow.core.state import get_state_layout, now_utc
from campaign_workflow.core.tsv_cases import load_campaign_config, load_cases


_SIMPLE_ARRAY_SPEC_RE = re.compile(r"^\d+(?:-\d+)?(?:,\d+(?:-\d+)?)*$")

_ACTIVE_SLURM_STATES = {
    "PENDING",
    "RUNNING",
    "CONFIGURING",
    "COMPLETING",
    "SUSPENDED",
    "RESIZING",
    "STAGE_OUT",
    "SIGNALING",
    "PD",
    "R",
    "CF",
    "CG",
    "S",
}


class ReconcileIterationError(RuntimeError):
    """Raised when a submitted optimization iteration cannot be reconciled safely."""


@dataclass(frozen=True)
class SubmittedCaseAudit:
    submitted_case_ids: list[int]
    submitted_case_count: int
    n_cases_materialized: int
    n_cases_unsubmitted: int
    n_submitted_case_dirs: int
    n_submitted_case_states: int
    n_submitted_sim_done: int
    n_submitted_sim_failed: int
    n_submitted_reduced_valid: int
    n_submitted_raw_deleted: int
    n_submitted_missing_state: int
    n_submitted_missing_case_dir: int
    n_submitted_unknown: int
    submitted_unknown_case_ids: list[int]
    submitted_missing_case_dirs: list[int]
    submitted_missing_state: list[int]
    submitted_sim_done_case_ids: list[int]
    submitted_sim_failed_case_ids: list[int]
    submitted_reduced_valid_case_ids: list[int]
    submitted_raw_deleted_case_ids: list[int]
    array_log_files: dict[str, list[str]]

    def to_dict(self) -> dict[str, Any]:
        return {
            "submitted_case_ids": self.submitted_case_ids,
            "submitted_case_count": self.submitted_case_count,
            "n_cases_materialized": self.n_cases_materialized,
            "n_cases_unsubmitted": self.n_cases_unsubmitted,
            "n_submitted_case_dirs": self.n_submitted_case_dirs,
            "n_submitted_case_states": self.n_submitted_case_states,
            "n_submitted_sim_done": self.n_submitted_sim_done,
            "n_submitted_sim_failed": self.n_submitted_sim_failed,
            "n_submitted_reduced_valid": self.n_submitted_reduced_valid,
            "n_submitted_raw_deleted": self.n_submitted_raw_deleted,
            "n_submitted_missing_state": self.n_submitted_missing_state,
            "n_submitted_missing_case_dir": self.n_submitted_missing_case_dir,
            "n_submitted_unknown": self.n_submitted_unknown,
            "submitted_unknown_case_ids": self.submitted_unknown_case_ids,
            "submitted_missing_case_dirs": self.submitted_missing_case_dirs,
            "submitted_missing_state": self.submitted_missing_state,
            "submitted_sim_done_case_ids": self.submitted_sim_done_case_ids,
            "submitted_sim_failed_case_ids": self.submitted_sim_failed_case_ids,
            "submitted_reduced_valid_case_ids": self.submitted_reduced_valid_case_ids,
            "submitted_raw_deleted_case_ids": self.submitted_raw_deleted_case_ids,
            "array_log_files": self.array_log_files,
        }


def parse_array_spec(array_spec: str) -> list[int]:
    if not isinstance(array_spec, str) or not array_spec:
        raise ReconcileIterationError(f"invalid array spec: {array_spec!r}")
    if array_spec.strip() != array_spec:
        raise ReconcileIterationError(
            f"invalid array spec with surrounding whitespace: {array_spec!r}"
        )
    if _SIMPLE_ARRAY_SPEC_RE.fullmatch(array_spec) is None:
        raise ReconcileIterationError(
            f"unsupported array spec for reconcile_iteration: {array_spec!r}. "
            "Supported examples: '0-9', '0,2,4', '0-3,7,9'."
        )

    ids: set[int] = set()
    for part in array_spec.split(","):
        if "-" in part:
            start_s, end_s = part.split("-", 1)
            start = int(start_s)
            end = int(end_s)
            if end < start:
                raise ReconcileIterationError(
                    f"invalid descending array range: {part!r}"
                )
            ids.update(range(start, end + 1))
        else:
            ids.add(int(part))

    return sorted(ids)


def reconcile_iteration(
    *,
    tick_summary: dict[str, Any],
    state_doc: dict[str, Any],
    optimization_root: Path,
    iteration: int,
    query_slurm: bool = True,
) -> dict[str, Any]:
    optimization_root = optimization_root.resolve()

    iteration_summary = _find_iteration_summary(tick_summary, iteration)
    if iteration_summary is None:
        raise ReconcileIterationError(f"iteration not found: iter_{iteration:03d}")

    iteration_state = _find_iteration_state(state_doc, iteration) or {}
    paused = bool(tick_summary.get("pause_file_exists"))

    campaign_root = optimization_root / str(iteration_summary["campaign_root"])
    campaign_root = campaign_root.resolve()
    if not campaign_root.is_dir():
        raise ReconcileIterationError(f"campaign root does not exist: {campaign_root}")

    submitted_case_ids = compute_submitted_case_ids(iteration_state)
    slurm_job_ids = _slurm_job_ids(iteration_state, iteration_summary)
    slurm_info = (
        query_slurm_job_state(slurm_job_ids) if query_slurm else _skipped_slurm_info()
    )

    case_audit = audit_submitted_cases(
        campaign_root=campaign_root,
        submitted_case_ids=submitted_case_ids,
    )

    status, recommended_action, warnings = classify_reconciliation(
        paused=paused,
        iteration_state=iteration_state,
        iteration_summary=iteration_summary,
        slurm_info=slurm_info,
        case_audit=case_audit,
    )

    reconciled_at = now_utc()

    result = {
        "schema_version": 1,
        "iteration": iteration,
        "campaign_root": str(campaign_root),
        "status": status,
        "recommended_action": recommended_action,
        "reconciled_at": reconciled_at,
        "submitted": bool(
            iteration_state.get("submitted", iteration_summary.get("submitted", False))
        ),
        "slurm_job_ids": slurm_job_ids,
        "array_spec": iteration_state.get("array_spec"),
        "slurm": slurm_info,
        "warnings": warnings,
        "errors": [],
        **case_audit.to_dict(),
    }

    result["state_update"] = {
        "status": status,
        "recommended_action": recommended_action,
        "reconciled_at": reconciled_at,
        "slurm": slurm_info,
        **case_audit.to_dict(),
    }
    return result


def compute_submitted_case_ids(iteration_state: dict[str, Any]) -> list[int]:
    raw_ids = iteration_state.get("submitted_case_ids")
    if isinstance(raw_ids, list):
        try:
            return sorted({int(item) for item in raw_ids})
        except Exception as exc:
            raise ReconcileIterationError(
                f"invalid submitted_case_ids: {raw_ids!r}"
            ) from exc

    array_spec = iteration_state.get("array_spec")
    if isinstance(array_spec, str) and array_spec:
        return parse_array_spec(array_spec)

    return []


def audit_submitted_cases(
    *, campaign_root: Path, submitted_case_ids: Sequence[int]
) -> SubmittedCaseAudit:
    try:
        campaign_config = load_campaign_config(campaign_root)
        cases = load_cases(campaign_root, campaign_config)
    except Exception as exc:
        raise ReconcileIterationError(
            f"failed to load campaign/cases for reconciliation: {exc}"
        ) from exc

    layout = get_state_layout(campaign_config)
    completion_marker = _simulation_marker(
        campaign_config, "completion_marker", "post/sim_done.json"
    )
    failure_marker = _simulation_marker(
        campaign_config, "failure_marker", "post/sim_failed.json"
    )

    case_by_id = {case.case_id: case for case in cases}
    materialized_ids = sorted(case_by_id)
    submitted_ids = sorted({int(item) for item in submitted_case_ids})

    unknown_case_ids: list[int] = []
    missing_case_dirs: list[int] = []
    missing_state: list[int] = []
    sim_done_ids: list[int] = []
    sim_failed_ids: list[int] = []
    reduced_valid_ids: list[int] = []
    raw_deleted_ids: list[int] = []
    array_log_files: dict[str, list[str]] = {}

    n_case_dirs = 0
    n_case_states = 0
    n_unknown = 0

    array_logs_dir = campaign_root / "array_logs"

    for case_id in submitted_ids:
        case = case_by_id.get(case_id)
        if case is None:
            unknown_case_ids.append(case_id)
            n_unknown += 1
            continue

        case_dir = campaign_root / case.case_name
        if not case_dir.is_dir():
            missing_case_dirs.append(case_id)
            n_unknown += 1
            continue
        n_case_dirs += 1

        state_path = case_dir / layout["state_file"]
        validation_path = case_dir / layout["validation_file"]
        raw_deleted_path = case_dir / layout["post_dir"] / "raw_deleted.json"

        if state_path.is_file():
            n_case_states += 1
        else:
            missing_state.append(case_id)

        has_done = (case_dir / completion_marker).is_file()
        has_failed = (case_dir / failure_marker).is_file()

        if has_done:
            sim_done_ids.append(case_id)
        if has_failed:
            sim_failed_ids.append(case_id)
        if validation_path.is_file() and _case_reduced_outputs_valid(
            campaign_config, validation_path
        ):
            reduced_valid_ids.append(case_id)
        if raw_deleted_path.is_file():
            raw_deleted_ids.append(case_id)

        if not has_done and not has_failed:
            n_unknown += 1

        if array_logs_dir.is_dir():
            matched_logs = sorted(
                path.name
                for path in array_logs_dir.glob(f"*_{case_id}.*")
                if path.is_file()
            )
            if matched_logs:
                array_log_files[str(case_id)] = matched_logs

    n_materialized = len(materialized_ids)
    n_submitted = len(submitted_ids)

    return SubmittedCaseAudit(
        submitted_case_ids=submitted_ids,
        submitted_case_count=n_submitted,
        n_cases_materialized=n_materialized,
        n_cases_unsubmitted=max(n_materialized - n_submitted, 0),
        n_submitted_case_dirs=n_case_dirs,
        n_submitted_case_states=n_case_states,
        n_submitted_sim_done=len(sim_done_ids),
        n_submitted_sim_failed=len(sim_failed_ids),
        n_submitted_reduced_valid=len(reduced_valid_ids),
        n_submitted_raw_deleted=len(raw_deleted_ids),
        n_submitted_missing_state=len(missing_state),
        n_submitted_missing_case_dir=len(missing_case_dirs),
        n_submitted_unknown=n_unknown,
        submitted_unknown_case_ids=unknown_case_ids,
        submitted_missing_case_dirs=missing_case_dirs,
        submitted_missing_state=missing_state,
        submitted_sim_done_case_ids=sim_done_ids,
        submitted_sim_failed_case_ids=sim_failed_ids,
        submitted_reduced_valid_case_ids=reduced_valid_ids,
        submitted_raw_deleted_case_ids=raw_deleted_ids,
        array_log_files=array_log_files,
    )


def query_slurm_job_state(job_ids: Sequence[str]) -> dict[str, Any]:
    clean_job_ids = [str(item).strip() for item in job_ids if str(item).strip()]
    if not clean_job_ids:
        return {
            "queried": False,
            "available": True,
            "active": False,
            "reason": "no_slurm_job_ids",
            "job_ids": [],
            "states": {},
            "command": None,
            "return_code": None,
            "stderr": "",
        }

    command = ["squeue", "-h", "-j", ",".join(clean_job_ids), "-o", "%i %T"]

    try:
        completed = subprocess.run(
            command,
            text=True,
            capture_output=True,
            check=False,
        )
    except FileNotFoundError:
        return {
            "queried": True,
            "available": False,
            "active": False,
            "reason": "squeue_not_found",
            "job_ids": clean_job_ids,
            "states": {},
            "command": command,
            "return_code": None,
            "stderr": "squeue executable not found",
        }

    states: dict[str, str] = {}
    for line in completed.stdout.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        parts = stripped.split(None, 1)
        if len(parts) == 1:
            states[parts[0]] = "<unknown>"
        else:
            states[parts[0]] = parts[1].strip().upper()

    active = any(state in _ACTIVE_SLURM_STATES for state in states.values())

    return {
        "queried": True,
        "available": completed.returncode == 0,
        "active": active,
        "reason": "ok" if completed.returncode == 0 else "squeue_failed",
        "job_ids": clean_job_ids,
        "states": states,
        "command": command,
        "return_code": completed.returncode,
        "stderr": completed.stderr,
    }


def classify_reconciliation(
    *,
    paused: bool,
    iteration_state: dict[str, Any],
    iteration_summary: dict[str, Any],
    slurm_info: dict[str, Any],
    case_audit: SubmittedCaseAudit,
) -> tuple[str, str, list[str]]:
    warnings: list[str] = []
    existing_status = str(
        iteration_state.get("status", iteration_summary.get("status", ""))
    )

    if paused:
        return "paused", "paused", warnings

    if existing_status == "closed":
        return "closed", "no_action", warnings

    if not case_audit.submitted_case_ids:
        if bool(iteration_state.get("submitted")):
            warnings.append(
                "iteration is marked submitted but no submitted_case_ids/array_spec are registered"
            )
            return "needs_inspection", "inspect_failures", warnings
        return (
            str(iteration_summary.get("status", "campaign_materialized")),
            str(iteration_summary.get("recommended_action", "submit_iteration")),
            warnings,
        )

    if case_audit.submitted_unknown_case_ids:
        warnings.append(
            f"submitted case IDs not present in cases.tsv: {case_audit.submitted_unknown_case_ids}"
        )
        return "needs_inspection", "inspect_failures", warnings

    if case_audit.n_submitted_sim_failed > 0:
        return "needs_inspection", "inspect_failures", warnings

    if slurm_info.get("active"):
        return "running", "wait_for_jobs", warnings

    if not slurm_info.get("available", True):
        warnings.append(f"SLURM state unavailable: {slurm_info.get('reason')}")
        if case_audit.n_submitted_sim_done < case_audit.submitted_case_count:
            return "running", "wait_for_jobs", warnings

    all_done = case_audit.n_submitted_sim_done == case_audit.submitted_case_count
    all_reduced = (
        case_audit.n_submitted_reduced_valid == case_audit.submitted_case_count
    )

    if all_done and all_reduced:
        return "reduced_ready", "close_iteration", warnings

    if all_done and not all_reduced:
        warnings.append(
            "all submitted simulations are done but reduced validation is incomplete"
        )
        return "needs_inspection", "inspect_failures", warnings

    if (
        slurm_info.get("queried")
        and slurm_info.get("available")
        and not slurm_info.get("active")
    ):
        warnings.append(
            "SLURM no longer shows the job, but submitted case markers are incomplete"
        )
        return "needs_inspection", "inspect_failures", warnings

    return "running", "wait_for_jobs", warnings


def update_state_after_reconcile(
    *,
    state_doc: dict[str, Any],
    reconciliation: dict[str, Any],
) -> dict[str, Any]:
    updated = dict(state_doc)
    iteration_number = int(reconciliation["iteration"])
    iterations = []
    found = False

    for raw_item in state_doc.get("iterations", []):
        if not isinstance(raw_item, dict):
            continue

        item = dict(raw_item)
        if int(item.get("iteration", -1)) == iteration_number:
            found = True
            item.update(reconciliation["state_update"])
        iterations.append(item)

    if not found:
        raise ReconcileIterationError(
            f"cannot update state: iteration not found: {iteration_number}"
        )

    updated["iterations"] = iterations
    updated["latest_iteration"] = iteration_number
    updated["status"] = _top_level_status_from_iteration_status(
        str(reconciliation["status"])
    )
    updated["updated_at"] = str(reconciliation["reconciled_at"])
    return updated


def _top_level_status_from_iteration_status(iteration_status: str) -> str:
    if iteration_status == "running":
        return "running"
    if iteration_status == "reduced_ready":
        return "reduced_ready"
    if iteration_status == "closed":
        return "closed"
    if iteration_status == "paused":
        return "paused"
    if iteration_status == "needs_inspection":
        return "needs_inspection"
    return iteration_status


def _find_iteration_summary(
    tick_summary: dict[str, Any], iteration: int
) -> dict[str, Any] | None:
    for item in tick_summary.get("iterations", []):
        if isinstance(item, dict) and int(item.get("iteration", -1)) == iteration:
            return item
    return None


def _find_iteration_state(
    state_doc: dict[str, Any], iteration: int
) -> dict[str, Any] | None:
    for item in state_doc.get("iterations", []):
        if isinstance(item, dict) and int(item.get("iteration", -1)) == iteration:
            return item
    return None


def _slurm_job_ids(
    iteration_state: dict[str, Any], iteration_summary: dict[str, Any]
) -> list[str]:
    raw = iteration_state.get(
        "slurm_job_ids", iteration_summary.get("slurm_job_ids", [])
    )
    if not isinstance(raw, list):
        return []
    return [str(item) for item in raw if str(item).strip()]


def _simulation_marker(config: dict[str, Any] | None, key: str, default: str) -> Path:
    simulation = (config or {}).get("simulation", {})
    if not isinstance(simulation, dict):
        return Path(default)
    raw = simulation.get(key, default)
    if not isinstance(raw, str) or raw.strip() == "":
        return Path(default)
    return Path(raw)


def _case_reduced_outputs_valid(config: dict[str, Any], validation_path: Path) -> bool:
    try:
        validation = read_json(validation_path)
    except Exception:
        return False

    analysis = config.get("analysis", {})
    outputs = analysis.get("outputs", []) if isinstance(analysis, dict) else []
    required_names: list[str] = []
    if isinstance(outputs, list):
        for output in outputs:
            if isinstance(output, dict) and bool(output.get("required", True)):
                name = output.get("name")
                if isinstance(name, str) and name:
                    required_names.append(name)

    reduced = validation.get("reduced", {})
    if not isinstance(reduced, dict):
        return False

    if required_names:
        return all(
            isinstance(reduced.get(name), dict) and bool(reduced[name].get("ok"))
            for name in required_names
        )

    return any(
        isinstance(item, dict) and bool(item.get("ok")) for item in reduced.values()
    )


def _skipped_slurm_info() -> dict[str, Any]:
    return {
        "queried": False,
        "available": True,
        "active": False,
        "reason": "skipped",
        "job_ids": [],
        "states": {},
        "command": None,
        "return_code": None,
        "stderr": "",
    }
