from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from campaign_workflow.core.atomic_io import read_json
from campaign_workflow.core.state import get_state_layout
from campaign_workflow.core.tsv_cases import (
    CaseRecord,
    load_campaign_config,
    load_cases,
)
from campaign_workflow.optimization_state import (
    OptimizationStateInfo,
    build_optimization_state_document,
    pause_file_path,
    read_optimization_state,
)

_ITERATION_DIR_RE = re.compile(r"^iter_(\d+)$")


class OptimizerTickError(RuntimeError):
    """Raised when the optimization root cannot be audited."""


def run_optimizer_tick(
    *,
    optimization_root: Path,
    iteration: int | None = None,
) -> dict[str, Any]:
    optimization_root = optimization_root.resolve()

    if not optimization_root.exists():
        raise OptimizerTickError(
            f"optimization root does not exist: {optimization_root}"
        )
    if not optimization_root.is_dir():
        raise OptimizerTickError(
            f"optimization root is not a directory: {optimization_root}"
        )

    state_info = read_optimization_state(optimization_root)
    paused = pause_file_path(optimization_root).exists()

    iteration_dirs = discover_iteration_dirs(optimization_root, iteration=iteration)
    existing_iteration_state = _iteration_state_index(state_info)

    iteration_summaries = [
        audit_iteration(
            optimization_root=optimization_root,
            campaign_root=campaign_root,
            existing_iteration_state=existing_iteration_state.get(iteration_number, {}),
            paused=paused,
        )
        for iteration_number, campaign_root in iteration_dirs
    ]

    proposed_state = build_optimization_state_document(
        optimization_root=optimization_root,
        iteration_summaries=iteration_summaries,
        paused=paused,
    )

    recommended_action = recommend_global_action(
        paused=paused,
        state_info=state_info,
        iteration_summaries=iteration_summaries,
    )

    return {
        "schema_version": 1,
        "optimization_root": str(optimization_root),
        "optimization_state_path": str(state_info.path),
        "optimization_state_exists": state_info.exists,
        "pause_file_exists": paused,
        "iteration_filter": iteration,
        "n_iterations": len(iteration_summaries),
        "recommended_action": recommended_action,
        "iterations": iteration_summaries,
        "proposed_optimization_state": proposed_state,
        "destructive_operations": 0,
    }


def discover_iteration_dirs(
    optimization_root: Path, *, iteration: int | None = None
) -> list[tuple[int, Path]]:
    iterations_root = optimization_root / "iterations"
    if not iterations_root.exists():
        return []
    if not iterations_root.is_dir():
        raise OptimizerTickError(
            f"iterations path exists but is not a directory: {iterations_root}"
        )

    found: list[tuple[int, Path]] = []
    for child in iterations_root.iterdir():
        if not child.is_dir():
            continue
        match = _ITERATION_DIR_RE.match(child.name)
        if match is None:
            continue
        iteration_number = int(match.group(1))
        if iteration is not None and iteration_number != iteration:
            continue
        found.append((iteration_number, child))

    found.sort(key=lambda item: item[0])
    return found


def audit_iteration(
    *,
    optimization_root: Path,
    campaign_root: Path,
    existing_iteration_state: dict[str, Any],
    paused: bool,
) -> dict[str, Any]:
    iteration_number = _iteration_number_from_dir(campaign_root)
    relative_campaign_root = _relative_posix(campaign_root, optimization_root)
    optimizer_run_dir = (
        optimization_root / "optimizer_runs" / f"iter_{iteration_number:03d}"
    )

    checks: dict[str, bool] = {
        "campaign_json_exists": (campaign_root / "campaign.json").is_file(),
        "cases_tsv_exists": (campaign_root / "cases.tsv").is_file(),
        "input_template_exists": (campaign_root / "input_template.py").is_file(),
        "array_logs_exists": (campaign_root / "array_logs").is_dir(),
        "materialize_cases_log_exists": (
            campaign_root / "materialize_cases.log"
        ).is_file(),
        "init_case_states_log_exists": (
            campaign_root / "init_case_states.log"
        ).is_file(),
        "materialize_cases_dry_run_log_exists": (
            campaign_root / "materialize_cases_dry_run.log"
        ).is_file(),
        "init_case_states_dry_run_log_exists": (
            campaign_root / "init_case_states_dry_run.log"
        ).is_file(),
        "optimizer_run_dir_exists": optimizer_run_dir.is_dir(),
    }

    errors: list[str] = []
    warnings: list[str] = []

    for filename, check_key in [
        ("campaign.json", "campaign_json_exists"),
        ("cases.tsv", "cases_tsv_exists"),
        ("input_template.py", "input_template_exists"),
    ]:
        if not checks[check_key]:
            errors.append(f"missing required file: {filename}")

    if not checks["array_logs_exists"]:
        errors.append("missing required directory: array_logs")

    config: dict[str, Any] | None = None
    cases: list[CaseRecord] = []
    if checks["campaign_json_exists"] and checks["cases_tsv_exists"]:
        try:
            config = load_campaign_config(campaign_root)
            cases = load_cases(campaign_root, config)
        except Exception as exc:
            errors.append(f"failed to load campaign config/cases: {exc}")
    elif checks["campaign_json_exists"]:
        try:
            config = load_campaign_config(campaign_root)
        except Exception as exc:
            errors.append(f"failed to load campaign config: {exc}")

    layout = get_state_layout(config or {})
    completion_marker = _simulation_marker(
        config, "completion_marker", "post/sim_done.json"
    )
    failure_marker = _simulation_marker(
        config, "failure_marker", "post/sim_failed.json"
    )

    n_case_dirs = 0
    n_case_states = 0
    n_sim_done = 0
    n_sim_failed = 0
    n_reduced_valid = 0
    n_raw_deleted = 0
    state_counts: dict[str, int] = {}
    missing_case_dirs: list[str] = []
    missing_state_json: list[str] = []

    for case in cases:
        case_dir = campaign_root / case.case_name
        if case_dir.is_dir():
            n_case_dirs += 1
        else:
            missing_case_dirs.append(case.case_name)
            continue

        state_path = case_dir / layout["state_file"]
        validation_path = case_dir / layout["validation_file"]
        raw_deleted_path = case_dir / layout["post_dir"] / "raw_deleted.json"

        if state_path.is_file():
            n_case_states += 1
            try:
                state_doc = read_json(state_path)
                state_name = str(state_doc.get("state", "<missing>"))
                state_counts[state_name] = state_counts.get(state_name, 0) + 1
            except Exception as exc:
                warnings.append(
                    f"failed to read state.json for {case.case_name}: {exc}"
                )
        else:
            missing_state_json.append(case.case_name)

        if (case_dir / completion_marker).is_file():
            n_sim_done += 1
        if (case_dir / failure_marker).is_file():
            n_sim_failed += 1
        if raw_deleted_path.is_file():
            n_raw_deleted += 1
        if validation_path.is_file() and _case_reduced_outputs_valid(
            config or {}, validation_path
        ):
            n_reduced_valid += 1

    n_cases = len(cases)
    submitted = bool(existing_iteration_state.get("submitted"))
    slurm_job_ids = existing_iteration_state.get("slurm_job_ids", [])
    if not isinstance(slurm_job_ids, list):
        slurm_job_ids = []

    status = classify_iteration_status(
        errors=errors,
        n_cases=n_cases,
        n_case_dirs=n_case_dirs,
        n_case_states=n_case_states,
        n_sim_done=n_sim_done,
        n_sim_failed=n_sim_failed,
        n_reduced_valid=n_reduced_valid,
        submitted=submitted,
        state_counts=state_counts,
        paused=paused,
    )
    recommended_action = recommend_iteration_action(
        status=status,
        paused=paused,
        errors=errors,
        n_cases=n_cases,
        n_case_dirs=n_case_dirs,
        n_case_states=n_case_states,
        n_sim_done=n_sim_done,
        n_sim_failed=n_sim_failed,
        n_reduced_valid=n_reduced_valid,
        submitted=submitted,
        state_counts=state_counts,
    )

    return {
        "iteration": iteration_number,
        "optimizer_run_dir": _relative_posix(optimizer_run_dir, optimization_root),
        "campaign_root": relative_campaign_root,
        "status": status,
        "submitted": submitted,
        "slurm_job_ids": slurm_job_ids,
        "n_cases": n_cases,
        "n_case_dirs": n_case_dirs,
        "n_case_states": n_case_states,
        "n_sim_done": n_sim_done,
        "n_sim_failed": n_sim_failed,
        "n_reduced_valid": n_reduced_valid,
        "n_raw_deleted": n_raw_deleted,
        "state_counts": dict(sorted(state_counts.items())),
        "checks": checks,
        "missing_case_dirs": missing_case_dirs,
        "missing_state_json": missing_state_json,
        "errors": errors,
        "warnings": warnings,
        "recommended_action": recommended_action,
    }


def classify_iteration_status(
    *,
    errors: list[str],
    n_cases: int,
    n_case_dirs: int,
    n_case_states: int,
    n_sim_done: int,
    n_sim_failed: int,
    n_reduced_valid: int,
    submitted: bool,
    state_counts: dict[str, int],
    paused: bool,
) -> str:
    if paused:
        return "paused"
    if errors:
        return "failed"
    if n_cases == 0:
        return "campaign_prepared"
    if n_reduced_valid == n_cases:
        return "reduced_ready"
    if n_sim_failed > 0:
        return "failed"
    if n_sim_done > 0 and n_sim_done < n_cases:
        return "running"
    if n_sim_done == n_cases:
        return "postprocessing"
    if submitted or state_counts.get("Submitted", 0) or state_counts.get("Running", 0):
        return "running"
    if n_case_dirs == n_cases and n_case_states == n_cases:
        return "campaign_materialized"
    if n_case_dirs == n_cases:
        return "campaign_prepared"
    return "planned"


def recommend_global_action(
    *,
    paused: bool,
    state_info: OptimizationStateInfo,
    iteration_summaries: list[dict[str, Any]],
) -> str:
    if paused:
        return "paused"
    if not iteration_summaries:
        return "initialize_optimization_state" if not state_info.exists else "no_action"

    actions = [
        str(item.get("recommended_action", "no_action")) for item in iteration_summaries
    ]
    for action in [
        "inspect_failures",
        "submit_iteration",
        "wait_for_jobs",
        "close_iteration",
        "propose_next_iteration",
    ]:
        if action in actions:
            return action

    if not state_info.exists:
        return "initialize_optimization_state"
    return "no_action"


def recommend_iteration_action(
    *,
    status: str,
    paused: bool,
    errors: list[str],
    n_cases: int,
    n_case_dirs: int,
    n_case_states: int,
    n_sim_done: int,
    n_sim_failed: int,
    n_reduced_valid: int,
    submitted: bool,
    state_counts: dict[str, int],
) -> str:
    if paused:
        return "paused"
    if errors:
        return "inspect_failures"
    if n_sim_failed > 0:
        return "inspect_failures"
    if n_cases > 0 and n_reduced_valid == n_cases:
        return "close_iteration"
    if n_cases > 0 and n_sim_done == n_cases:
        return "wait_for_jobs"
    if submitted or state_counts.get("Submitted", 0) or state_counts.get("Running", 0):
        return "wait_for_jobs"
    if (
        n_cases > 0
        and n_case_dirs == n_cases
        and n_case_states == n_cases
        and n_sim_done == 0
    ):
        return "submit_iteration"
    if status == "closed":
        return "propose_next_iteration"
    return "no_action"


def _iteration_state_index(
    state_info: OptimizationStateInfo,
) -> dict[int, dict[str, Any]]:
    if not state_info.exists or state_info.data is None:
        return {}

    raw_iterations = state_info.data.get("iterations", [])
    if not isinstance(raw_iterations, list):
        return {}

    indexed: dict[int, dict[str, Any]] = {}
    for item in raw_iterations:
        if not isinstance(item, dict):
            continue
        try:
            iteration_number = int(item["iteration"])
        except Exception:
            continue
        indexed[iteration_number] = item
    return indexed


def _iteration_number_from_dir(path: Path) -> int:
    match = _ITERATION_DIR_RE.match(path.name)
    if match is None:
        raise OptimizerTickError(f"invalid iteration directory name: {path.name}")
    return int(match.group(1))


def _relative_posix(path: Path, root: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return str(path)


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
