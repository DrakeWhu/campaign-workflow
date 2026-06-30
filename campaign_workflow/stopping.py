from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from campaign_workflow.core.atomic_io import read_json, write_json_atomic
from campaign_workflow.core.state import now_utc
from campaign_workflow.optimization_state import PAUSE_FILENAME, pause_file_path

OPTIMIZATION_CONFIG_FILENAME = "optimization.json"
STOPPING_REPORTS_DIRNAME = "stopping_reports"

PROPOSABLE_STATUSES = {"reduced_ready", "closed", "closeable"}
NOT_READY_STATUSES = {
    "planned",
    "optimizer_outputs_ready",
    "campaign_prepared",
    "campaign_materialized",
    "submitted",
    "running",
    "postprocessing",
}


class StoppingError(RuntimeError):
    """Raised when stopping policy evaluation itself cannot be performed safely."""


@dataclass(frozen=True)
class StoppingContext:
    optimization_root: Path
    tick_summary: dict[str, Any]
    state_doc: dict[str, Any]
    action: str
    iteration: int
    next_iteration: int | None
    optimization_config_path: Path | None


def load_stopping_config(
    optimization_root: Path,
    optimization_config_path: Path | None = None,
) -> tuple[Path | None, dict[str, Any], dict[str, Any]]:
    path = optimization_config_path
    if path is None:
        candidate = optimization_root / OPTIMIZATION_CONFIG_FILENAME
        if not candidate.is_file():
            return None, {}, {}
        path = candidate

    path = Path(path).expanduser()
    if not path.is_absolute():
        path = optimization_root / path
    path = path.resolve(strict=False)

    if not path.is_file():
        return path, {}, {}

    data = read_json(path)
    if data.get("schema_version") != 1:
        raise StoppingError(f"{path} schema_version must be 1")

    stopping = data.get("stopping", {})
    if stopping is None:
        stopping = {}
    if not isinstance(stopping, dict):
        raise StoppingError(f"{path} field 'stopping' must be an object")

    legacy_policy = data.get("policy", {})
    if legacy_policy is None:
        legacy_policy = {}
    if not isinstance(legacy_policy, dict):
        raise StoppingError(f"{path} field 'policy' must be an object when present")

    return path, stopping, legacy_policy


def evaluate_stopping(
    *,
    optimization_root: Path,
    tick_summary: dict[str, Any],
    state_doc: dict[str, Any],
    action: str,
    iteration: int,
    next_iteration: int | None = None,
    optimization_config_path: Path | None = None,
) -> dict[str, Any]:
    optimization_root = optimization_root.resolve()
    config_path, stopping_config, legacy_policy = load_stopping_config(
        optimization_root,
        optimization_config_path,
    )

    enabled = bool(stopping_config.get("enabled", bool(stopping_config)))
    mode = str(stopping_config.get("mode", "hard")).lower()
    if mode not in {"hard", "warn"}:
        raise StoppingError("stopping.mode must be 'hard' or 'warn'")

    merged_policy = _merged_policy(stopping_config, legacy_policy)
    next_iter = (
        int(next_iteration) if next_iteration is not None else int(iteration) + 1
    )

    ctx = StoppingContext(
        optimization_root=optimization_root,
        tick_summary=tick_summary,
        state_doc=state_doc,
        action=action,
        iteration=int(iteration),
        next_iteration=next_iter,
        optimization_config_path=config_path,
    )

    iteration_state = _find_iteration_state(state_doc, iteration)
    iteration_summary = _find_iteration_summary(tick_summary, iteration)
    signals_path = _stopping_signals_path(optimization_root, iteration)
    signals_doc = _read_optional_json(signals_path)

    base_details = {
        "enabled": enabled,
        "mode": mode,
        "iteration_state": _small_iteration_state(iteration_state),
        "iteration_summary": _small_iteration_summary(iteration_summary),
        "signals_path": str(signals_path),
        "signals_exists": signals_doc is not None,
    }

    if _is_paused(ctx, merged_policy):
        return _final_report(
            ctx=ctx,
            config_path=config_path,
            policy=merged_policy,
            signals=signals_doc,
            decision="paused",
            reasons=["PAUSE_OPTIMIZATION is present"],
            details=base_details,
            can_propose=False,
            overall_status="blocked",
            recommended_action="paused",
        )

    if not enabled:
        readiness = _readiness_decision(
            iteration_state=iteration_state,
            iteration_summary=iteration_summary,
        )
        if readiness["decision"] != "continue":
            return _final_report(
                ctx=ctx,
                config_path=config_path,
                policy=merged_policy,
                signals=signals_doc,
                decision=readiness["decision"],
                reasons=readiness["reasons"],
                details={**base_details, "readiness": readiness},
                can_propose=False,
                overall_status="blocked",
                recommended_action=readiness["recommended_action"],
            )

        return _final_report(
            ctx=ctx,
            config_path=config_path,
            policy=merged_policy,
            signals=signals_doc,
            decision="continue",
            reasons=["stopping policy disabled; readiness allows proposing"],
            details={**base_details, "readiness": readiness},
            can_propose=True,
            overall_status="pass",
            recommended_action="propose_next_iteration",
        )

    readiness = _readiness_decision(
        iteration_state=iteration_state,
        iteration_summary=iteration_summary,
    )
    if readiness["decision"] != "continue":
        return _final_report(
            ctx=ctx,
            config_path=config_path,
            policy=merged_policy,
            signals=signals_doc,
            decision=readiness["decision"],
            reasons=readiness["reasons"],
            details={**base_details, "readiness": readiness},
            can_propose=False,
            overall_status="blocked",
            recommended_action=readiness["recommended_action"],
        )

    budget = _budget_decision(
        state_doc=state_doc,
        policy=merged_policy,
        next_iteration=next_iter,
    )
    if budget is not None:
        return _policy_report(
            ctx=ctx,
            config_path=config_path,
            policy=merged_policy,
            signals=signals_doc,
            decision="stop_budget",
            reasons=[budget["reason"]],
            details={**base_details, "budget": budget, "readiness": readiness},
            mode=mode,
            hard_block=True,
            recommended_action="no_action",
        )

    quality = _iteration_quality_decision(
        iteration_state=iteration_state or {},
        iteration_summary=iteration_summary or {},
        policy=merged_policy,
        signals=signals_doc,
    )
    if quality is not None:
        return _policy_report(
            ctx=ctx,
            config_path=config_path,
            policy=merged_policy,
            signals=signals_doc,
            decision=quality["decision"],
            reasons=[quality["reason"]],
            details={**base_details, "quality": quality, "readiness": readiness},
            mode=mode,
            hard_block=True,
            recommended_action="no_action",
        )

    no_improvement = _no_improvement_decision(
        policy=merged_policy,
        signals=signals_doc,
    )
    novelty = _candidate_novelty_decision(
        policy=merged_policy,
        signals=signals_doc,
    )

    if no_improvement is not None and novelty is not None:
        return _policy_report(
            ctx=ctx,
            config_path=config_path,
            policy=merged_policy,
            signals=signals_doc,
            decision="stop_converged",
            reasons=[no_improvement["reason"], novelty["reason"]],
            details={
                **base_details,
                "no_improvement": no_improvement,
                "candidate_novelty": novelty,
                "readiness": readiness,
            },
            mode=mode,
            hard_block=True,
            recommended_action="no_action",
        )

    if no_improvement is not None:
        return _policy_report(
            ctx=ctx,
            config_path=config_path,
            policy=merged_policy,
            signals=signals_doc,
            decision="stop_converged",
            reasons=[no_improvement["reason"]],
            details={
                **base_details,
                "no_improvement": no_improvement,
                "readiness": readiness,
            },
            mode=mode,
            hard_block=True,
            recommended_action="no_action",
        )

    boundary = _boundary_decision(policy=merged_policy, signals=signals_doc)
    if boundary is not None:
        block = bool(boundary.get("block", False))
        return _policy_report(
            ctx=ctx,
            config_path=config_path,
            policy=merged_policy,
            signals=signals_doc,
            decision="needs_human_review",
            reasons=[boundary["reason"]],
            details={**base_details, "boundary_saturation": boundary},
            mode=mode,
            hard_block=block,
            recommended_action="propose_next_iteration" if not block else "no_action",
        )

    surrogate = _surrogate_decision(policy=merged_policy, signals=signals_doc)
    if surrogate is not None:
        block = bool(surrogate.get("block", False))
        return _policy_report(
            ctx=ctx,
            config_path=config_path,
            policy=merged_policy,
            signals=signals_doc,
            decision="needs_human_review",
            reasons=[surrogate["reason"]],
            details={**base_details, "surrogate_reliability": surrogate},
            mode=mode,
            hard_block=block,
            recommended_action="propose_next_iteration" if not block else "no_action",
        )

    optimizer_view = _optimizer_view_decision(signals_doc)
    if optimizer_view is not None:
        decision = str(optimizer_view["decision"])
        hard_block = decision.startswith("stop_")
        return _policy_report(
            ctx=ctx,
            config_path=config_path,
            policy=merged_policy,
            signals=signals_doc,
            decision=decision,
            reasons=optimizer_view["reasons"],
            details={**base_details, "optimizer_view": optimizer_view},
            mode=mode,
            hard_block=hard_block,
            recommended_action="no_action" if hard_block else "propose_next_iteration",
        )

    return _final_report(
        ctx=ctx,
        config_path=config_path,
        policy=merged_policy,
        signals=signals_doc,
        decision="continue",
        reasons=["no stopping condition triggered"],
        details={**base_details, "readiness": readiness},
        can_propose=True,
        overall_status="pass",
        recommended_action="propose_next_iteration",
    )


def stopping_blocks(report: dict[str, Any]) -> bool:
    return not bool(report.get("can_propose_next_iteration", False))


def write_stopping_report(optimization_root: Path, report: dict[str, Any]) -> Path:
    iteration = int(report["iteration"])
    path = (
        optimization_root
        / STOPPING_REPORTS_DIRNAME
        / f"iter_{iteration:03d}_stopping_report.json"
    )
    write_json_atomic(path, report, dry_run=False)
    return path


def update_state_after_stopping(
    *,
    state_doc: dict[str, Any],
    report: dict[str, Any],
    report_path: Path | None,
) -> dict[str, Any]:
    updated = dict(state_doc)
    updated["updated_at"] = now_utc()
    updated["latest_stopping_decision"] = report.get("overall_decision")
    updated["latest_stopping_report"] = (
        None if report_path is None else str(report_path)
    )

    iteration = int(report["iteration"])
    iterations: list[dict[str, Any]] = []

    for item in state_doc.get("iterations", []):
        if not isinstance(item, dict):
            continue
        copy = dict(item)
        try:
            item_iteration = int(copy.get("iteration"))
        except Exception:
            iterations.append(copy)
            continue

        if item_iteration == iteration:
            copy["stopping_decision"] = report.get("overall_decision")
            copy["stopping_checked_at"] = report.get("created_at")
            copy["can_propose_next_iteration"] = report.get(
                "can_propose_next_iteration"
            )
            copy["latest_stopping_report"] = (
                None if report_path is None else str(report_path)
            )

        iterations.append(copy)

    updated["iterations"] = iterations

    decision = str(report.get("overall_decision"))
    if decision == "paused":
        updated["status"] = "paused"
        updated["recommended_action"] = "paused"
    elif decision == "wait_for_jobs":
        updated["recommended_action"] = "wait_for_jobs"
    elif decision == "continue":
        updated["recommended_action"] = "propose_next_iteration"
    elif decision.startswith("stop_"):
        updated["recommended_action"] = "no_action"
    elif decision == "needs_human_review":
        updated["recommended_action"] = (
            "no_action"
            if not bool(report.get("can_propose_next_iteration"))
            else "propose_next_iteration"
        )

    return updated


def _merged_policy(
    stopping_config: dict[str, Any],
    legacy_policy: dict[str, Any],
) -> dict[str, Any]:
    out: dict[str, Any] = {}

    for key in [
        "max_iterations",
        "max_total_submitted_cases",
        "max_total_materialized_cases",
        "min_new_valid_observations",
        "min_valid_fraction_to_continue",
        "max_failed_fraction_to_continue",
    ]:
        if key in legacy_policy:
            out[key] = legacy_policy[key]
        if key in stopping_config:
            out[key] = stopping_config[key]

    # Backward-compatible legacy name.
    if "min_reduced_valid_to_continue" in legacy_policy:
        out["min_new_valid_observations"] = legacy_policy[
            "min_reduced_valid_to_continue"
        ]

    for key in [
        "enabled",
        "mode",
        "no_improvement",
        "candidate_novelty",
        "surrogate_reliability",
        "boundary_saturation",
        "pause_file",
    ]:
        if key in stopping_config:
            out[key] = stopping_config[key]

    return out


def _readiness_decision(
    *,
    iteration_state: dict[str, Any] | None,
    iteration_summary: dict[str, Any] | None,
) -> dict[str, Any]:
    state = iteration_state or iteration_summary or {}
    if not state:
        return {
            "decision": "needs_human_review",
            "recommended_action": "no_action",
            "reasons": ["iteration is missing from optimization state and audit"],
        }

    status = str(state.get("status", ""))
    recommended = str(state.get("recommended_action", ""))

    slurm = state.get("slurm", {})
    if isinstance(slurm, dict) and bool(slurm.get("active")):
        return {
            "decision": "wait_for_jobs",
            "recommended_action": "wait_for_jobs",
            "reasons": ["iteration still has active scheduler jobs"],
            "status": status,
        }

    if status in {"submitted", "running", "postprocessing"}:
        return {
            "decision": "wait_for_jobs",
            "recommended_action": "wait_for_jobs",
            "reasons": [f"iteration is not finished: status={status!r}"],
            "status": status,
        }

    if status not in PROPOSABLE_STATUSES:
        if recommended == "submit_iteration":
            action = "submit_iteration"
            reason = (
                "iteration is materialized but not submitted; stopping does not "
                "replace submit_iteration"
            )
        else:
            action = "wait_for_jobs" if status in NOT_READY_STATUSES else "no_action"
            reason = (
                f"iteration is not ready to propose next iteration: status={status!r}"
            )

        return {
            "decision": "no_action"
            if action == "submit_iteration"
            else "wait_for_jobs",
            "recommended_action": action,
            "reasons": [reason],
            "status": status,
        }

    return {
        "decision": "continue",
        "recommended_action": "propose_next_iteration",
        "reasons": ["iteration is ready for stopping-policy evaluation"],
        "status": status,
    }


def _budget_decision(
    *,
    state_doc: dict[str, Any],
    policy: dict[str, Any],
    next_iteration: int,
) -> dict[str, Any] | None:
    if policy.get("max_iterations") is not None:
        limit = int(policy["max_iterations"])
        if int(next_iteration) >= limit:
            return {
                "reason": (
                    f"max_iterations reached: next_iteration={next_iteration}, "
                    f"max_iterations={limit}"
                ),
                "limit": limit,
                "next_iteration": next_iteration,
            }

    iterations = [
        item for item in state_doc.get("iterations", []) if isinstance(item, dict)
    ]

    if policy.get("max_total_submitted_cases") is not None:
        limit = int(policy["max_total_submitted_cases"])
        total = sum(
            _first_int(item, ["submitted_case_count", "n_submitted_case_dirs"])
            for item in iterations
        )
        if total >= limit:
            return {
                "reason": (
                    "max_total_submitted_cases reached: "
                    f"submitted={total}, limit={limit}"
                ),
                "limit": limit,
                "total_submitted_cases": total,
            }

    if policy.get("max_total_materialized_cases") is not None:
        limit = int(policy["max_total_materialized_cases"])
        total = sum(_first_int(item, ["n_cases"]) for item in iterations)
        if total >= limit:
            return {
                "reason": (
                    "max_total_materialized_cases reached: "
                    f"materialized={total}, limit={limit}"
                ),
                "limit": limit,
                "total_materialized_cases": total,
            }

    return None


def _iteration_quality_decision(
    *,
    iteration_state: dict[str, Any],
    iteration_summary: dict[str, Any],
    policy: dict[str, Any],
    signals: dict[str, Any] | None,
) -> dict[str, Any] | None:
    state = {**iteration_summary, **iteration_state}

    submitted = _first_int(
        state,
        ["submitted_case_count", "n_submitted_case_dirs", "n_cases"],
        default=0,
    )
    valid = _first_int(
        state,
        ["n_submitted_reduced_valid", "n_reduced_valid"],
        default=0,
    )
    failed = _first_int(
        state,
        ["n_submitted_sim_failed", "n_sim_failed"],
        default=0,
    )

    denominator = max(submitted, 1)
    valid_fraction = valid / denominator
    failed_fraction = failed / denominator

    max_failed_fraction = policy.get("max_failed_fraction_to_continue")
    if max_failed_fraction is not None:
        limit = float(max_failed_fraction)
        if failed_fraction > limit:
            return {
                "decision": "stop_failure_rate",
                "reason": (
                    "failed fraction exceeds policy: "
                    f"failed_fraction={failed_fraction:.6g}, limit={limit:.6g}"
                ),
                "submitted": submitted,
                "valid": valid,
                "failed": failed,
                "failed_fraction": failed_fraction,
            }

    min_valid_fraction = policy.get("min_valid_fraction_to_continue")
    if min_valid_fraction is not None:
        limit = float(min_valid_fraction)
        if valid_fraction < limit:
            return {
                "decision": "stop_no_new_valid_observations",
                "reason": (
                    "valid fraction below policy: "
                    f"valid_fraction={valid_fraction:.6g}, required={limit:.6g}"
                ),
                "submitted": submitted,
                "valid": valid,
                "failed": failed,
                "valid_fraction": valid_fraction,
            }

    min_new = policy.get("min_new_valid_observations")
    if min_new is not None and signals is not None:
        new_valid = _signals(signals).get("n_new_valid_observations")
        if isinstance(new_valid, int | float) and int(new_valid) < int(min_new):
            return {
                "decision": "stop_no_new_valid_observations",
                "reason": (
                    "not enough new valid observations in optimizer signals: "
                    f"new_valid={int(new_valid)}, required={int(min_new)}"
                ),
                "n_new_valid_observations": int(new_valid),
                "required": int(min_new),
            }

    return None


def _no_improvement_decision(
    *,
    policy: dict[str, Any],
    signals: dict[str, Any] | None,
) -> dict[str, Any] | None:
    cfg = policy.get("no_improvement", {})
    if not isinstance(cfg, dict) or not bool(cfg.get("enabled", False)):
        return None
    if signals is None:
        return None

    signal_root = _signals(signals)
    min_delta = float(cfg.get("min_delta", 0.0))
    objectives = [str(v) for v in cfg.get("objectives", [])]
    aggregation = str(cfg.get("aggregation", "any"))

    window = signal_root.get("best_score_improvement_window", {})
    improvements: dict[str, Any] = {}

    if isinstance(window, dict) and window.get("status") == "ok":
        raw_values = window.get("values", {})
        if isinstance(raw_values, dict):
            improvements = dict(raw_values)

    if not improvements:
        raw_values = signal_root.get("best_score_improvement", {})
        if isinstance(raw_values, dict):
            improvements = dict(raw_values)

    if objectives:
        improvements = {k: v for k, v in improvements.items() if k in objectives}

    finite_values = {
        k: float(v) for k, v in improvements.items() if isinstance(v, int | float)
    }

    if not finite_values:
        return None

    improved = {k: v >= min_delta for k, v in finite_values.items()}

    if aggregation == "all":
        triggered = not all(improved.values())
    else:
        # Default "any": keep going if at least one tracked objective improves.
        triggered = not any(improved.values())

    if not triggered:
        return None

    return {
        "reason": (
            "no best-score improvement above threshold: "
            f"min_delta={min_delta}, aggregation={aggregation}"
        ),
        "min_delta": min_delta,
        "aggregation": aggregation,
        "improvements": finite_values,
        "improved": improved,
    }


def _candidate_novelty_decision(
    *,
    policy: dict[str, Any],
    signals: dict[str, Any] | None,
) -> dict[str, Any] | None:
    cfg = policy.get("candidate_novelty", {})
    if not isinstance(cfg, dict) or not bool(cfg.get("enabled", False)):
        return None
    if signals is None:
        return None

    novelty = _signals(signals).get("candidate_novelty", {})
    if not isinstance(novelty, dict):
        return None

    median = novelty.get("median_nearest_known_scaled_dist")
    threshold = cfg.get("min_median_nearest_known_scaled_dist")
    if not isinstance(median, int | float) or threshold is None:
        return None

    threshold_f = float(threshold)
    if float(median) >= threshold_f:
        return None

    return {
        "reason": (
            "candidate novelty median nearest-known scaled distance below threshold: "
            f"median={float(median):.6g}, threshold={threshold_f:.6g}"
        ),
        "median_nearest_known_scaled_dist": float(median),
        "threshold": threshold_f,
    }


def _boundary_decision(
    *,
    policy: dict[str, Any],
    signals: dict[str, Any] | None,
) -> dict[str, Any] | None:
    cfg = policy.get("boundary_saturation", {})
    if not isinstance(cfg, dict) or not bool(cfg.get("enabled", False)):
        return None
    if signals is None:
        return None

    boundary = _signals(signals).get("boundary_saturation", {})
    if not isinstance(boundary, dict):
        return None

    value = boundary.get("fraction_candidates_near_boundary")
    threshold = float(cfg.get("review_threshold", 0.85))
    if not isinstance(value, int | float):
        return None

    if float(value) < threshold:
        return None

    return {
        "reason": (
            "candidate batch is saturated near parameter-space boundary: "
            f"fraction={float(value):.6g}, review_threshold={threshold:.6g}"
        ),
        "fraction_candidates_near_boundary": float(value),
        "review_threshold": threshold,
        "block": bool(cfg.get("block", False)),
    }


def _surrogate_decision(
    *,
    policy: dict[str, Any],
    signals: dict[str, Any] | None,
) -> dict[str, Any] | None:
    cfg = policy.get("surrogate_reliability", {})
    if not isinstance(cfg, dict) or not bool(cfg.get("enabled", False)):
        return None
    if signals is None:
        return None

    reliability = _signals(signals).get("surrogate_reliability", {})
    if not isinstance(reliability, dict):
        return None

    status = str(reliability.get("status", "unknown"))
    if status != "weak":
        return None

    return {
        "reason": "surrogate reliability is weak",
        "status": status,
        "block": bool(cfg.get("block_on_weak", False)),
    }


def _optimizer_view_decision(signals: dict[str, Any] | None) -> dict[str, Any] | None:
    if signals is None:
        return None

    recommendation = signals.get("recommendation", {})
    if not isinstance(recommendation, dict):
        return None

    view = str(recommendation.get("optimizer_view", ""))
    if view in {"", "continue", "unknown"}:
        return None

    reasons = recommendation.get("reasons", [])
    if not isinstance(reasons, list):
        reasons = [str(reasons)]

    return {
        "decision": view,
        "reasons": [str(item) for item in reasons],
    }


def _policy_report(
    *,
    ctx: StoppingContext,
    config_path: Path | None,
    policy: dict[str, Any],
    signals: dict[str, Any] | None,
    decision: str,
    reasons: list[str],
    details: dict[str, Any],
    mode: str,
    hard_block: bool,
    recommended_action: str,
) -> dict[str, Any]:
    if hard_block and mode == "hard":
        can_propose = False
        overall_status = "blocked"
    else:
        can_propose = True
        overall_status = "warn"

    return _final_report(
        ctx=ctx,
        config_path=config_path,
        policy=policy,
        signals=signals,
        decision=decision,
        reasons=reasons,
        details=details,
        can_propose=can_propose,
        overall_status=overall_status,
        recommended_action=recommended_action,
    )


def _final_report(
    *,
    ctx: StoppingContext,
    config_path: Path | None,
    policy: dict[str, Any],
    signals: dict[str, Any] | None,
    decision: str,
    reasons: list[str],
    details: dict[str, Any],
    can_propose: bool,
    overall_status: str,
    recommended_action: str,
) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "created_at": now_utc(),
        "optimization_root": str(ctx.optimization_root),
        "optimization_config": None if config_path is None else str(config_path),
        "action": ctx.action,
        "iteration": int(ctx.iteration),
        "next_iteration": ctx.next_iteration,
        "overall_decision": decision,
        "overall_status": overall_status,
        "can_propose_next_iteration": bool(can_propose),
        "recommended_action": recommended_action,
        "reasons": reasons,
        "policy": policy,
        "signals": signals,
        "details": details,
        "destructive_operations": 0,
        "will_not": [
            "submit jobs",
            "run simulations",
            "run analysis adapters",
            "read raw HDF5/openPMD diagnostics",
            "delete files",
            "cleanup raw diagnostics",
            "modify physics inputs",
            "implement recursive loop",
        ],
    }


def _is_paused(ctx: StoppingContext, policy: dict[str, Any]) -> bool:
    raw = policy.get("pause_file", PAUSE_FILENAME)
    pause_name = raw if isinstance(raw, str) and raw.strip() else PAUSE_FILENAME
    return (
        (ctx.optimization_root / pause_name).exists()
        or pause_file_path(ctx.optimization_root).exists()
        or bool(ctx.tick_summary.get("pause_file_exists"))
    )


def _stopping_signals_path(optimization_root: Path, iteration: int) -> Path:
    return (
        optimization_root
        / "optimizer_runs"
        / f"iter_{int(iteration):03d}"
        / "reports"
        / "stopping_signals.json"
    )


def _read_optional_json(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    data = read_json(path)
    if not isinstance(data, dict):
        raise StoppingError(f"expected JSON object: {path}")
    return data


def _signals(signals_doc: dict[str, Any]) -> dict[str, Any]:
    raw = signals_doc.get("signals", {})
    return raw if isinstance(raw, dict) else {}


def _find_iteration_state(
    state_doc: dict[str, Any],
    iteration: int,
) -> dict[str, Any] | None:
    for item in state_doc.get("iterations", []):
        if not isinstance(item, dict):
            continue
        try:
            if int(item.get("iteration")) == int(iteration):
                return item
        except Exception:
            continue
    return None


def _find_iteration_summary(
    tick_summary: dict[str, Any],
    iteration: int,
) -> dict[str, Any] | None:
    for item in tick_summary.get("iterations", []):
        if not isinstance(item, dict):
            continue
        try:
            if int(item.get("iteration")) == int(iteration):
                return item
        except Exception:
            continue
    return None


def _small_iteration_state(item: dict[str, Any] | None) -> dict[str, Any]:
    if not item:
        return {}
    keys = [
        "iteration",
        "status",
        "recommended_action",
        "submitted_case_count",
        "n_submitted_case_dirs",
        "n_submitted_reduced_valid",
        "n_submitted_sim_failed",
        "n_reduced_valid",
        "n_sim_failed",
        "n_cases",
    ]
    return {key: item.get(key) for key in keys if key in item}


def _small_iteration_summary(item: dict[str, Any] | None) -> dict[str, Any]:
    if not item:
        return {}
    keys = [
        "iteration",
        "status",
        "recommended_action",
        "n_cases",
        "n_reduced_valid",
        "n_sim_failed",
        "submitted",
    ]
    return {key: item.get(key) for key in keys if key in item}


def _first_int(
    item: dict[str, Any],
    keys: list[str],
    *,
    default: int = 0,
) -> int:
    for key in keys:
        try:
            value = int(item.get(key))
        except Exception:
            continue
        if value >= 0:
            return value
    return int(default)
