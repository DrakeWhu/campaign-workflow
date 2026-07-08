from __future__ import annotations

import os
import subprocess
import math
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from campaign_workflow.core.atomic_io import read_json, write_json_atomic
from campaign_workflow.core.state import now_utc
from campaign_workflow.core.storage import build_storage_snapshot
from campaign_workflow.core.tsv_cases import load_campaign_config, load_cases
from campaign_workflow.optimization_state import PAUSE_FILENAME, pause_file_path
from campaign_workflow.submit_iteration import expand_array_spec

OPTIMIZATION_CONFIG_FILENAME = "optimization.json"
GUARD_REPORTS_DIRNAME = "guard_reports"

_STATUS_ORDER = {"pass": 0, "warn": 1, "unknown": 2, "blocked": 3}


class GuardError(RuntimeError):
    """Raised when guard evaluation itself cannot be performed safely."""


@dataclass(frozen=True)
class GuardContext:
    optimization_root: Path
    tick_summary: dict[str, Any]
    action: str
    iteration: int | None = None
    array_spec: str | None = None
    max_cases: int | None = None
    from_iteration: int | None = None
    next_iteration: int | None = None
    optimization_config_path: Path | None = None


def load_guard_config(
    optimization_root: Path,
    optimization_config_path: Path | None = None,
) -> tuple[Path | None, dict[str, Any]]:
    path = optimization_config_path
    if path is None:
        candidate = optimization_root / OPTIMIZATION_CONFIG_FILENAME
        if not candidate.is_file():
            return None, {}
        path = candidate

    path = Path(path).expanduser()
    if not path.is_absolute():
        path = optimization_root / path
    path = path.resolve(strict=False)

    if not path.is_file():
        return path, {}

    data = read_json(path)
    guards = data.get("guards", {})
    if guards is None:
        return path, {}
    if not isinstance(guards, dict):
        raise GuardError(f"{path} field 'guards' must be an object")
    return path, guards


def evaluate_guards(
    *,
    optimization_root: Path,
    tick_summary: dict[str, Any],
    action: str,
    iteration: int | None = None,
    array_spec: str | None = None,
    max_cases: int | None = None,
    from_iteration: int | None = None,
    next_iteration: int | None = None,
    optimization_config_path: Path | None = None,
) -> dict[str, Any]:
    optimization_root = optimization_root.resolve()
    config_path, guards_config = load_guard_config(
        optimization_root, optimization_config_path
    )

    # Backward compatibility:
    # If optimization.json has no "guards" section, only the historical pause guard
    # is active. New guards must not change Phase 4A–4D behavior unless configured.
    enabled = bool(guards_config.get("enabled", bool(guards_config)))

    ctx = GuardContext(
        optimization_root=optimization_root,
        tick_summary=tick_summary,
        action=action,
        iteration=iteration,
        array_spec=array_spec,
        max_cases=max_cases,
        from_iteration=from_iteration,
        next_iteration=next_iteration,
        optimization_config_path=config_path,
    )

    guards: list[dict[str, Any]] = []
    guards.append(evaluate_pause_guard(ctx, guards_config))

    if enabled:
        guards.append(evaluate_campaign_size_guard(ctx, guards_config))
        guards.append(evaluate_quota_guard(ctx, guards_config))
        guards.append(evaluate_walltime_guard(ctx, guards_config))
    else:
        guards.append(_guard("campaign_size_guard", "pass", "guards_disabled"))
        guards.append(_guard("quota_guard", "pass", "guards_disabled"))
        guards.append(_guard("walltime_guard", "pass", "guards_disabled"))

    overall_status = _overall_status(guards)
    recommended_action = _recommended_action(overall_status, guards, action)

    return {
        "schema_version": 1,
        "created_at": now_utc(),
        "optimization_root": str(optimization_root),
        "optimization_config": None if config_path is None else str(config_path),
        "iteration": iteration,
        "from_iteration": from_iteration,
        "next_iteration": next_iteration,
        "action": action,
        "array_spec": array_spec,
        "max_cases": max_cases,
        "overall_status": overall_status,
        "recommended_action": recommended_action,
        "guards": guards,
        "destructive_operations": 0,
        "will_not": [
            "submit jobs",
            "run simulations",
            "run analysis adapters",
            "read raw HDF5/openPMD diagnostics",
            "delete files",
            "cleanup raw diagnostics",
            "modify physics inputs",
        ],
    }


def write_guard_report(optimization_root: Path, report: dict[str, Any]) -> Path:
    action = str(report.get("action", "guard_check"))
    iteration = report.get("iteration")
    iteration_label = "global" if iteration is None else f"iter_{int(iteration):03d}"
    path = (
        optimization_root / GUARD_REPORTS_DIRNAME / f"{action}__{iteration_label}.json"
    )
    write_json_atomic(path, report, dry_run=False)
    return path


def guard_report_blocks(report: dict[str, Any]) -> bool:
    return str(report.get("overall_status")) == "blocked"


def evaluate_pause_guard(
    ctx: GuardContext, guards_config: dict[str, Any]
) -> dict[str, Any]:
    raw = guards_config.get("pause_file", PAUSE_FILENAME)
    pause_name = raw if isinstance(raw, str) and raw.strip() else PAUSE_FILENAME
    pause_path = ctx.optimization_root / pause_name
    default_pause_path = pause_file_path(ctx.optimization_root)

    exists = pause_path.exists() or default_pause_path.exists()
    if exists or ctx.tick_summary.get("pause_file_exists"):
        return _guard(
            "pause_guard",
            "blocked",
            "pause_file_present_PAUSE_OPTIMIZATION",
            {"pause_file": str(pause_path)},
        )

    return _guard(
        "pause_guard",
        "pass",
        "pause_file_absent",
        {"pause_file": str(pause_path)},
    )


def evaluate_campaign_size_guard(
    ctx: GuardContext,
    guards_config: dict[str, Any],
) -> dict[str, Any]:
    cfg = _section(guards_config, "campaign_size")
    if not bool(cfg.get("enabled", True)):
        return _guard("campaign_size_guard", "pass", "disabled")
    policy_keys = {
        "max_iterations",
        "max_cases_per_submit",
        "max_total_materialized_cases",
        "max_total_submitted_cases",
        "max_unsubmitted_materialized_cases",
    }
    if not any(key in cfg for key in policy_keys):
        return _guard("campaign_size_guard", "pass", "no_policy_configured")

    state = _state_from_tick(ctx.tick_summary)
    iterations = [
        item for item in state.get("iterations", []) if isinstance(item, dict)
    ]

    details: dict[str, Any] = {
        "configured_limits": dict(cfg),
        "n_iterations_in_state": len(iterations),
        "total_materialized_cases": sum(
            _int(item.get("n_cases"), 0) for item in iterations
        ),
        "total_submitted_cases": sum(
            _int(
                item.get("submitted_case_count"),
                _int(item.get("n_submitted_case_dirs"), 0),
            )
            for item in iterations
        ),
    }

    if cfg.get("max_iterations") is not None:
        limit = int(cfg["max_iterations"])
        target_next = ctx.next_iteration
        if (
            target_next is None
            and ctx.action == "propose_next_iteration"
            and ctx.from_iteration is not None
        ):
            target_next = ctx.from_iteration + 1

        if target_next is not None:
            details["target_next_iteration"] = target_next
            if int(target_next) >= limit:
                return _guard(
                    "campaign_size_guard",
                    "blocked",
                    "max_iterations_reached",
                    details,
                )
        elif len(iterations) >= limit:
            return _guard(
                "campaign_size_guard",
                "blocked",
                "max_iterations_reached",
                details,
            )

    if cfg.get("max_total_materialized_cases") is not None:
        limit = int(cfg["max_total_materialized_cases"])
        if int(details["total_materialized_cases"]) >= limit:
            return _guard(
                "campaign_size_guard",
                "blocked",
                "max_total_materialized_cases_reached",
                details,
            )

    if cfg.get("max_total_submitted_cases") is not None:
        limit = int(cfg["max_total_submitted_cases"])
        if int(details["total_submitted_cases"]) >= limit:
            return _guard(
                "campaign_size_guard",
                "blocked",
                "max_total_submitted_cases_reached",
                details,
            )

    if ctx.action in {"submit_iteration", "check_guards"} and ctx.iteration is not None:
        iteration_summary = _find_iteration(ctx.tick_summary, ctx.iteration)
        if iteration_summary is not None:
            details["iteration"] = ctx.iteration

            if iteration_summary.get("errors"):
                details["iteration_errors"] = iteration_summary.get("errors")
                return _guard(
                    "campaign_size_guard",
                    "pass",
                    "iteration_audit_errors_defer_to_submit_plan",
                    details,
                )

            case_ids = _case_ids_for_iteration(ctx.optimization_root, iteration_summary)
            chosen = _select_case_ids(case_ids, ctx.array_spec, ctx.max_cases)

            details["iteration_case_count"] = len(case_ids)
            details["selected_case_count"] = len(chosen)
            details["selected_case_ids"] = chosen

            if cfg.get("max_cases_per_submit") is not None:
                limit = int(cfg["max_cases_per_submit"])
                details["max_cases_per_submit"] = limit
                if len(chosen) > limit:
                    return _guard(
                        "campaign_size_guard",
                        "blocked",
                        "max_cases_per_submit_exceeded",
                        details,
                    )

    if cfg.get("max_unsubmitted_materialized_cases") is not None:
        limit = int(cfg["max_unsubmitted_materialized_cases"])
        unsubmitted = 0
        for item in iterations:
            if not bool(item.get("submitted")):
                unsubmitted += _int(item.get("n_cases"), 0)

        details["unsubmitted_materialized_cases"] = unsubmitted
        if unsubmitted > limit:
            return _guard(
                "campaign_size_guard",
                "blocked",
                "max_unsubmitted_materialized_cases_exceeded",
                details,
            )

    return _guard(
        "campaign_size_guard",
        "pass",
        "campaign_size_within_policy",
        details,
    )


def evaluate_quota_guard(
    ctx: GuardContext, guards_config: dict[str, Any]
) -> dict[str, Any]:
    cfg = _section(guards_config, "quota")
    if not bool(cfg.get("enabled", False)):
        return _guard("quota_guard", "pass", "disabled")

    mode = str(cfg.get("mode", "warn")).lower()
    details: dict[str, Any] = {"mode": mode, "configured_policy": dict(cfg)}

    probe = cfg.get("filesystem_probe", {})
    probe_path_raw = None
    if isinstance(probe, dict):
        probe_path_raw = probe.get("path")

    probe_path = _render_root_path(
        ctx.optimization_root,
        str(probe_path_raw or "{optimization_root}"),
    )

    try:
        usage = shutil.disk_usage(probe_path)
    except Exception as exc:
        return _unknown_or_blocked(
            "quota_guard",
            mode,
            "filesystem_probe_failed",
            {"probe_path": str(probe_path), "error": str(exc), **details},
        )

    total = int(usage.total)
    used = int(usage.used)
    free = int(usage.free)
    used_fraction = (used / total) if total > 0 else math.nan

    quota_probe = cfg.get("quota_probe")
    if quota_probe is not None:
        try:
            quota_details = _run_quota_probe(ctx.optimization_root, quota_probe)
        except Exception as exc:
            return _unknown_or_blocked(
                "quota_guard",
                mode,
                "quota_probe_failed",
                {"error": str(exc), **details},
            )

        details.update(quota_details)

        quota_limit = details.get("quota_limit_bytes")
        quota_used = details.get("quota_used_bytes")

        if quota_limit is None or int(quota_limit) <= 0:
            return _unknown_or_blocked(
                "quota_guard",
                mode,
                "quota_limit_unavailable",
                details,
            )

        quota_limit_i = int(quota_limit)
        quota_used_i = int(quota_used or 0)
        quota_free_i = max(quota_limit_i - quota_used_i, 0)
        quota_used_fraction = quota_used_i / quota_limit_i

        details["quota_free_bytes"] = quota_free_i
        details["quota_used_fraction"] = quota_used_fraction

        hard = cfg.get("hard_used_fraction")
        if hard is not None and quota_used_fraction >= float(hard):
            details["threshold"] = float(hard)
            return _guard(
                "quota_guard",
                "blocked" if mode == "hard" else "warn",
                "quota_used_fraction_exceeds_hard_threshold",
                details,
            )

        soft = cfg.get("soft_used_fraction")
        if soft is not None and quota_used_fraction >= float(soft):
            details["threshold"] = float(soft)
            return _guard(
                "quota_guard",
                "warn",
                "quota_used_fraction_exceeds_soft_threshold",
                details,
            )

        min_free = cfg.get("min_free_bytes")
        if min_free is not None:
            min_free_i = int(min_free)
            details["min_free_bytes"] = min_free_i

            if quota_free_i < min_free_i:
                deficit = min_free_i - quota_free_i
                details["quota_free_bytes_deficit"] = deficit

                if int(details.get("safe_cleanup_candidate_bytes", 0)) >= deficit:
                    reason = (
                        "insufficient_quota_free_space_but_cleanup_candidates_exist"
                    )
                    recommended = "cleanup_then_retry"
                else:
                    reason = "insufficient_quota_free_space"
                    recommended = "pause_or_cleanup"

                guard = _guard(
                    "quota_guard",
                    "blocked" if mode == "hard" else "warn",
                    reason,
                    details,
                )
                guard["recommended_action"] = recommended
                return guard

        return _guard("quota_guard", "pass", "quota_within_policy", details)

    details.update(
        {
            "probe_path": str(probe_path),
            "filesystem_total_bytes": total,
            "filesystem_used_bytes": used,
            "filesystem_free_bytes": free,
            "filesystem_used_fraction": used_fraction,
        }
    )

    details.update(_cleanup_capacity_summary(ctx.optimization_root, ctx.tick_summary))

    hard = cfg.get("hard_used_fraction")
    if hard is not None and used_fraction >= float(hard):
        details["threshold"] = float(hard)
        return _guard(
            "quota_guard",
            "blocked" if mode == "hard" else "warn",
            "filesystem_used_fraction_exceeds_hard_threshold",
            details,
        )

    soft = cfg.get("soft_used_fraction")
    if soft is not None and used_fraction >= float(soft):
        details["threshold"] = float(soft)
        return _guard(
            "quota_guard",
            "warn",
            "filesystem_used_fraction_exceeds_soft_threshold",
            details,
        )

    min_free = cfg.get("min_free_bytes")
    if min_free is not None:
        min_free_i = int(min_free)
        details["min_free_bytes"] = min_free_i

        if free < min_free_i:
            deficit = min_free_i - free
            details["free_bytes_deficit"] = deficit

            if int(details.get("safe_cleanup_candidate_bytes", 0)) >= deficit:
                reason = "insufficient_free_space_but_cleanup_candidates_exist"
                recommended = "cleanup_then_retry"
            else:
                reason = "insufficient_free_space"
                recommended = "pause_or_cleanup"

            guard = _guard(
                "quota_guard",
                "blocked" if mode == "hard" else "warn",
                reason,
                details,
            )
            guard["recommended_action"] = recommended
            return guard

    return _guard("quota_guard", "pass", "quota_within_policy", details)


def evaluate_walltime_guard(
    ctx: GuardContext, guards_config: dict[str, Any]
) -> dict[str, Any]:
    cfg = _section(guards_config, "walltime")
    if not bool(cfg.get("enabled", False)):
        return _guard("walltime_guard", "pass", "disabled")

    mode = str(cfg.get("mode", "warn")).lower()

    if ctx.action not in {"submit_iteration", "check_guards"} or ctx.iteration is None:
        return _guard(
            "walltime_guard",
            "pass",
            "not_applicable_for_action",
            {"action": ctx.action},
        )

    iteration_summary = _find_iteration(ctx.tick_summary, ctx.iteration)
    if iteration_summary is None:
        return _unknown_or_blocked(
            "walltime_guard",
            mode,
            "iteration_not_found",
            {"iteration": ctx.iteration},
        )

    try:
        campaign_root = ctx.optimization_root / str(iteration_summary["campaign_root"])
        campaign_config = load_campaign_config(campaign_root)
        cases = load_cases(campaign_root, campaign_config)
        case_ids = [case.case_id for case in cases]
        selected_ids = set(_select_case_ids(case_ids, ctx.array_spec, ctx.max_cases))
        selected_cases = [case for case in cases if case.case_id in selected_ids]
    except Exception as exc:
        return _unknown_or_blocked(
            "walltime_guard",
            mode,
            "failed_to_load_cases_for_walltime",
            {"error": str(exc)},
        )

    max_fraction = float(cfg.get("max_runtime_fraction", 0.85))
    partition_limit = cfg.get("partition_time_limit_seconds")
    max_allowed_runtime = None
    if partition_limit is not None:
        max_allowed_runtime = float(partition_limit) * max_fraction

    case_reports = []
    blocked = []
    unknown = []

    for case in selected_cases:
        report = _estimate_case_walltime(case.row, cfg)
        report.update({"case_id": case.case_id, "case_name": case.case_name})

        if (
            max_allowed_runtime is not None
            and report.get("estimated_runtime_seconds") is not None
        ):
            report["max_allowed_runtime_seconds"] = max_allowed_runtime
            if float(report["estimated_runtime_seconds"]) > max_allowed_runtime:
                report["status"] = "blocked"
                blocked.append(case.case_id)
            else:
                report["status"] = "pass"
        elif report.get("estimated_runtime_seconds") is None:
            report["status"] = "unknown"
            unknown.append(case.case_id)
        else:
            report["status"] = "pass"

        case_reports.append(report)

    details = {
        "mode": mode,
        "selected_case_count": len(selected_cases),
        "selected_case_ids": sorted(selected_ids),
        "partition_time_limit_seconds": partition_limit,
        "max_runtime_fraction": max_fraction,
        "max_allowed_runtime_seconds": max_allowed_runtime,
        "case_estimates": case_reports,
        "note": (
            "pre-submit walltime is a configurable budget guard. Running-job log-based "
            "running-job log-based walltime/resubmission decisions are outside this guard."
        ),
    }

    if blocked:
        details["blocked_case_ids"] = blocked
        return _guard(
            "walltime_guard",
            "blocked",
            "estimated_runtime_exceeds_policy",
            details,
        )

    if unknown:
        details["unknown_case_ids"] = unknown
        return _unknown_or_blocked(
            "walltime_guard",
            mode,
            "walltime_estimate_unknown",
            details,
        )

    return _guard("walltime_guard", "pass", "walltime_within_policy", details)


def _estimate_case_walltime(row: dict[str, str], cfg: dict[str, Any]) -> dict[str, Any]:
    plateau = _float_from_row(row, "PLATEAU_LENGTH_MM", "plateau_mm_num")
    if plateau is None:
        return {"estimated_runtime_seconds": None, "reason": "missing_plateau_length"}

    front = float(cfg.get("front_ramp_mm", 5.0))
    back = float(cfg.get("back_ramp_mm", 5.0))
    profile = plateau + front + back

    baseline_steps_per_5mm = cfg.get("baseline_steps_per_5mm")
    seconds_per_step = cfg.get("empirical_seconds_per_step")
    safety = float(cfg.get("safety_factor", 1.0))

    out: dict[str, Any] = {
        "plateau_length_mm": plateau,
        "front_ramp_mm": front,
        "back_ramp_mm": back,
        "profile_length_mm": profile,
        "safety_factor": safety,
    }

    if baseline_steps_per_5mm is None:
        out.update(
            {
                "estimated_steps": None,
                "estimated_runtime_seconds": None,
                "reason": "missing_baseline_steps_per_5mm",
            }
        )
        return out

    estimated_steps = int(math.ceil(float(baseline_steps_per_5mm) * profile / 5.0))
    out["estimated_steps"] = estimated_steps

    if seconds_per_step is None:
        out.update(
            {
                "estimated_runtime_seconds": None,
                "reason": "missing_empirical_seconds_per_step",
            }
        )
        return out

    out["empirical_seconds_per_step"] = float(seconds_per_step)
    out["estimated_runtime_seconds"] = (
        estimated_steps * float(seconds_per_step) * safety
    )
    return out


def _cleanup_capacity_summary(
    optimization_root: Path,
    tick_summary: dict[str, Any],
) -> dict[str, Any]:
    total_safe = 0
    total_live = 0
    campaigns = []

    for item in tick_summary.get("iterations", []):
        if not isinstance(item, dict):
            continue

        campaign_root = optimization_root / str(item.get("campaign_root", ""))
        if not campaign_root.is_dir():
            continue

        try:
            cfg = load_campaign_config(campaign_root)
            cases = load_cases(campaign_root, cfg)
            snapshot = build_storage_snapshot(
                campaign_root=campaign_root,
                config=cfg,
                cases=cases,
            )
        except Exception as exc:
            campaigns.append(
                {
                    "iteration": item.get("iteration"),
                    "campaign_root": str(campaign_root),
                    "status": "unknown",
                    "reason": str(exc),
                }
            )
            continue

        safe = int(snapshot.get("safe_cleanup_candidate_bytes", 0))
        live = int(snapshot.get("raw_live_bytes", 0))

        total_safe += safe
        total_live += live

        campaigns.append(
            {
                "iteration": item.get("iteration"),
                "campaign_root": str(campaign_root),
                "status": "ok",
                "case_count": snapshot.get("case_count"),
                "raw_live_bytes": live,
                "safe_cleanup_candidate_bytes": safe,
                "destructive_operations": 0,
            }
        )

    return {
        "raw_live_bytes": total_live,
        "safe_cleanup_candidate_bytes": total_safe,
        "cleanup_probe_campaigns": campaigns,
        "cleanup_probe_destructive_operations": 0,
    }


def _state_from_tick(tick_summary: dict[str, Any]) -> dict[str, Any]:
    state = tick_summary.get("proposed_optimization_state")
    if isinstance(state, dict):
        iterations = state.get("iterations", [])
        if isinstance(iterations, list):
            return state

    iterations = tick_summary.get("iterations", [])
    if not isinstance(iterations, list):
        iterations = []

    return {"iterations": iterations}


def _find_iteration(
    tick_summary: dict[str, Any],
    iteration: int | None,
) -> dict[str, Any] | None:
    if iteration is None:
        return None

    try:
        wanted = int(iteration)
    except Exception:
        return None

    for item in tick_summary.get("iterations", []):
        if not isinstance(item, dict):
            continue
        try:
            current = int(item.get("iteration"))
        except Exception:
            continue
        if current == wanted:
            return item

    return None


def _case_ids_for_iteration(
    optimization_root: Path,
    iteration_summary: dict[str, Any],
) -> list[int]:
    campaign_root = optimization_root / str(iteration_summary["campaign_root"])
    cfg = load_campaign_config(campaign_root)
    cases = load_cases(campaign_root, cfg)
    return sorted(case.case_id for case in cases)


def _select_case_ids(
    case_ids: list[int],
    array_spec: str | None,
    max_cases: int | None,
) -> list[int]:
    if array_spec is not None and max_cases is not None:
        raise GuardError("array_spec and max_cases are mutually exclusive")

    sorted_ids = sorted(case_ids)

    if array_spec is not None:
        selected = expand_array_spec(array_spec)
        missing = sorted(set(selected) - set(sorted_ids))
        if missing:
            raise GuardError(f"array spec references unknown case IDs: {missing}")
        return selected

    if max_cases is not None:
        if max_cases <= 0:
            raise GuardError("max_cases must be positive")
        return sorted_ids[: int(max_cases)]

    return sorted_ids


def _section(config: dict[str, Any], name: str) -> dict[str, Any]:
    raw = config.get(name, {})
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise GuardError(f"guards.{name} must be an object")
    return raw


def _guard(
    name: str,
    status: str,
    reason: str,
    details: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "name": name,
        "status": status,
        "reason": reason,
        "details": details or {},
    }


def _unknown_or_blocked(
    name: str,
    mode: str,
    reason: str,
    details: dict[str, Any],
) -> dict[str, Any]:
    status = "blocked" if mode == "hard" else "unknown"
    return _guard(name, status, reason, details)


def _overall_status(guards: list[dict[str, Any]]) -> str:
    status = "pass"
    for guard in guards:
        current = str(guard.get("status", "unknown"))
        if _STATUS_ORDER.get(current, 2) > _STATUS_ORDER.get(status, 0):
            status = current
    return status


def _recommended_action(
    overall_status: str,
    guards: list[dict[str, Any]],
    action: str,
) -> str:
    if overall_status == "pass":
        return action if action != "check_guards" else "no_action"

    for guard in guards:
        rec = guard.get("recommended_action")
        if isinstance(rec, str) and rec:
            return rec

    reasons = {str(guard.get("reason", "")) for guard in guards}

    if "pause_file_present_PAUSE_OPTIMIZATION" in reasons:
        return "paused"

    if any(
        "quota" in str(guard.get("name", ""))
        for guard in guards
        if guard.get("status") == "blocked"
    ):
        return "pause_or_cleanup"

    if any(
        "walltime" in str(guard.get("name", ""))
        for guard in guards
        if guard.get("status") == "blocked"
    ):
        return "change_partition"

    if any(
        "campaign_size" in str(guard.get("name", ""))
        for guard in guards
        if guard.get("status") == "blocked"
    ):
        return "reduce_array"

    return "inspect_config"


def _render_root_path(optimization_root: Path, text: str) -> Path:
    rendered = text.replace("{optimization_root}", str(optimization_root))
    path = Path(rendered).expanduser()
    if not path.is_absolute():
        path = optimization_root / path
    return path.resolve(strict=False)


def _int(value: Any, default: int = 0) -> int:
    try:
        if value is None:
            return default
        return int(value)
    except Exception:
        return default


def _float_from_row(row: dict[str, str], *names: str) -> float | None:
    for name in names:
        if name not in row:
            continue

        raw = str(row.get(name, "")).strip()
        if not raw:
            continue

        try:
            return float(raw)
        except ValueError:
            continue

    return None


def _run_quota_probe(
    optimization_root: Path,
    probe: Any,
) -> dict[str, Any]:
    if not isinstance(probe, dict):
        raise GuardError("quota_probe must be an object")

    kind = str(probe.get("kind", "")).strip()

    if kind == "lfs_quota_user":
        return _run_lfs_quota_user_probe(optimization_root, probe)

    raise GuardError(f"unsupported quota_probe kind: {kind}")


def _run_lfs_quota_user_probe(
    optimization_root: Path,
    probe: dict[str, Any],
) -> dict[str, Any]:
    path_raw = str(probe.get("path", "/HOME"))
    path = _render_root_path(optimization_root, path_raw)
    parse_filesystem = path_raw.replace("{optimization_root}", str(optimization_root))

    user_raw = str(probe.get("user", "{USER}"))
    user = user_raw.replace("{USER}", os.environ.get("USER", ""))

    if not user:
        raise GuardError("lfs_quota_user probe could not resolve user")

    command = ["lfs", "quota", "-h", "-u", user, str(path)]

    result = subprocess.run(
        command,
        capture_output=True,
        text=True,
        check=False,
    )

    if result.returncode != 0:
        raise GuardError(
            "lfs quota command failed: "
            f"return_code={result.returncode}, stderr={result.stderr.strip()}"
        )

    parsed = _parse_lfs_quota_output(result.stdout, parse_filesystem)

    return {
        "quota_probe_kind": "lfs_quota_user",
        "quota_command": command,
        "quota_probe_path": str(path),
        "quota_user": user,
        "quota_used_bytes": parsed["used_bytes"],
        "quota_soft_bytes": parsed["soft_bytes"],
        "quota_limit_bytes": parsed["limit_bytes"],
        "quota_files_used": parsed.get("files_used"),
        "quota_raw_stdout": result.stdout,
    }


def _normalize_quota_filesystem(value: str) -> str:
    text = str(value).strip().replace("\\", "/")
    while "//" in text:
        text = text.replace("//", "/")
    return text.rstrip("/") or "/"


def _parse_lfs_quota_output(stdout: str, filesystem: str) -> dict[str, Any]:
    for line in stdout.splitlines():
        parts = line.split()
        if len(parts) < 4:
            continue

        # Expected Lustre row:
        # /HOME  422.3G  0k  1T  -  171177  0  0  -
        if _normalize_quota_filesystem(parts[0]) != _normalize_quota_filesystem(
            filesystem
        ):
            continue

        used = _parse_human_bytes(parts[1])
        soft = _parse_human_bytes(parts[2])
        hard = _parse_human_bytes(parts[3])

        limit = hard if hard > 0 else soft

        files_used = None
        if len(parts) >= 6:
            try:
                files_used = int(parts[5])
            except ValueError:
                files_used = None

        return {
            "used_bytes": used,
            "soft_bytes": soft,
            "limit_bytes": limit,
            "files_used": files_used,
        }

    raise GuardError(f"could not parse lfs quota output for filesystem {filesystem!r}")


def _parse_human_bytes(text: str) -> int:
    raw = str(text).strip()
    if not raw:
        raise GuardError("empty byte quantity")

    if raw[-1].isalpha():
        number = raw[:-1]
        suffix = raw[-1].lower()
    else:
        number = raw
        suffix = ""

    try:
        value = float(number)
    except ValueError as exc:
        raise GuardError(f"invalid byte quantity: {text!r}") from exc

    factors = {
        "": 1,
        "b": 1,
        "k": 1024,
        "m": 1024**2,
        "g": 1024**3,
        "t": 1024**4,
        "p": 1024**5,
    }

    if suffix not in factors:
        raise GuardError(f"unsupported byte suffix in quantity: {text!r}")

    return int(value * factors[suffix])
