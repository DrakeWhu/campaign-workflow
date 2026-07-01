from __future__ import annotations

import shlex
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from campaign_workflow.core.atomic_io import write_json_atomic
from campaign_workflow.core.state import now_utc
from campaign_workflow.guards import (
    evaluate_guards,
    guard_report_blocks,
    write_guard_report,
)
from campaign_workflow.optimization_state import (
    read_optimization_state,
    write_optimization_state,
)
from campaign_workflow.optimizer_tick import run_optimizer_tick
from campaign_workflow.propose_next_iteration import (
    ProposeNextIterationError,
    build_propose_next_iteration_plan,
    init_next_case_states,
    materialize_next_campaign,
    prepare_next_campaign_from_optimizer_outputs,
    run_external_optimizer_command,
    update_state_after_next_iteration,
    verify_optimizer_outputs,
)
from campaign_workflow.reconcile_iteration import (
    reconcile_iteration,
    update_state_after_reconcile,
)
from campaign_workflow.stopping import (
    evaluate_stopping,
    stopping_blocks,
    update_state_after_stopping,
    write_stopping_report,
)
from campaign_workflow.submit_iteration import (
    build_submit_iteration_plan,
    execute_submit_iteration,
    parse_sbatch_job_id,
    state_updates_preview,
    update_state_after_submit,
)


class OptimizerLoopError(RuntimeError):
    """Raised when a recursive optimizer loop tick cannot be performed safely."""


@dataclass(frozen=True)
class DependentOptimizerTickPlan:
    optimization_root: Path
    current_iteration: int
    next_iteration: int
    dependency_job_id: str
    workflow_root: Path
    workflow_env: Path
    job_name: str
    script_path: Path
    stdout_path: Path
    stderr_path: Path
    submit_command: list[str]
    tick_command: list[str]
    working_directory: Path

    def to_dict(self) -> dict[str, Any]:
        return {
            "optimization_root": str(self.optimization_root),
            "current_iteration": self.current_iteration,
            "next_iteration": self.next_iteration,
            "dependency": f"afterany:{self.dependency_job_id}",
            "dependency_job_id": self.dependency_job_id,
            "workflow_root": str(self.workflow_root),
            "workflow_env": str(self.workflow_env),
            "job_name": self.job_name,
            "script_path": str(self.script_path),
            "stdout_path": str(self.stdout_path),
            "stderr_path": str(self.stderr_path),
            "submit_command": self.submit_command,
            "tick_command": self.tick_command,
            "working_directory": str(self.working_directory),
        }


@dataclass(frozen=True)
class DependentOptimizerTickResult:
    job_id: str
    return_code: int
    stdout: str
    stderr: str
    submitted_at: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "job_id": self.job_id,
            "return_code": self.return_code,
            "stdout": self.stdout,
            "stderr": self.stderr,
            "submitted_at": self.submitted_at,
        }


def run_optimizer_loop_once(
    *,
    optimization_root: Path,
    iteration: int,
    next_iteration: int | None,
    array_spec: str | None = None,
    max_cases: int | None = None,
    submit_script: Path | None = None,
    workflow_root: Path | None = None,
    workflow_env: Path | None = None,
    job_name_prefix: str | None = None,
    optimization_config_path: Path | None = None,
    execute: bool,
) -> dict[str, Any]:
    """Run one non-resident recursive BO/MOBO loop step.

    The operation is intentionally finite. In execute mode it may submit at most two
    SLURM jobs: the next iteration case-cycle array and the following dependent
    optimizer tick.
    """
    optimization_root = optimization_root.resolve()
    current_iteration = int(iteration)
    target_next_iteration = (
        int(next_iteration) if next_iteration is not None else current_iteration + 1
    )
    if target_next_iteration <= current_iteration:
        raise OptimizerLoopError(
            "next_iteration must be greater than iteration: "
            f"iteration={current_iteration}, next_iteration={target_next_iteration}"
        )

    mode = "execute" if execute else "dry-run"
    summary: dict[str, Any] = {
        "schema_version": 1,
        "action": "run_loop_once",
        "mode": mode,
        "optimization_root": str(optimization_root),
        "iteration": current_iteration,
        "next_iteration": target_next_iteration,
        "state_written": False,
        "destructive_operations": 0,
        "max_sbatch_calls_if_executed": 2,
        "will_not": [
            "modify physics inputs",
            "modify input_template.py physics content",
            "read raw HDF5/openPMD diagnostics",
            "delete files",
            "cleanup raw diagnostics",
            "change optimizer backend",
            "change objective/scoring physics",
        ],
        "loop_log_dir": str(
            loop_log_dir(
                optimization_root,
                current_iteration=current_iteration,
                next_iteration=target_next_iteration,
            )
        ),
    }

    state_info = read_optimization_state(optimization_root)
    if not state_info.exists or state_info.data is None:
        raise OptimizerLoopError(
            "optimization_state.json is required for run_loop_once"
        )

    pre_reconcile_audit = run_optimizer_tick(
        optimization_root=optimization_root,
        iteration=current_iteration,
    )
    state_doc = state_info.data

    reconciliation = reconcile_iteration(
        tick_summary=pre_reconcile_audit,
        state_doc=state_doc,
        optimization_root=optimization_root,
        iteration=current_iteration,
        query_slurm=True,
    )
    state_after_reconcile = update_state_after_reconcile(
        state_doc=state_doc,
        reconciliation=reconciliation,
    )

    summary["reconciliation"] = reconciliation
    summary["state_updates_after_reconcile"] = reconciliation["state_update"]

    if execute:
        write_optimization_state(optimization_root, state_after_reconcile)
        summary["state_written"] = True
        working_state_doc = state_after_reconcile
        tick_after_reconcile = run_optimizer_tick(
            optimization_root=optimization_root,
            iteration=current_iteration,
        )
    else:
        working_state_doc = state_after_reconcile
        tick_after_reconcile = pre_reconcile_audit

    if str(reconciliation.get("status")) not in {"reduced_ready", "closed"}:
        summary["loop_stopped_before_propose"] = True
        summary["recommended_action"] = reconciliation.get("recommended_action")
        summary["stop_reason"] = (
            "current iteration is not ready to propose the next iteration"
        )
        _write_loop_report_if_execute(optimization_root, summary, execute=execute)
        return summary

    stopping_report = evaluate_stopping(
        optimization_root=optimization_root,
        tick_summary=tick_after_reconcile,
        state_doc=working_state_doc,
        action="run_loop_once",
        iteration=current_iteration,
        next_iteration=target_next_iteration,
        optimization_config_path=optimization_config_path,
    )
    summary["stopping_report"] = stopping_report

    if stopping_blocks(stopping_report):
        summary["loop_stopped_before_propose"] = True
        summary["propose_blocked_by_stopping"] = True
        summary["recommended_action"] = stopping_report["recommended_action"]
        summary["state_written"] = bool(summary.get("state_written"))
        if execute:
            report_path = write_stopping_report(optimization_root, stopping_report)
            stopped_state = update_state_after_stopping(
                state_doc=working_state_doc,
                report=stopping_report,
                report_path=report_path,
            )
            write_optimization_state(optimization_root, stopped_state)
            summary["state_written"] = True
            summary["stopping_report_written"] = str(report_path)
            summary["optimization_state_after_stopping"] = stopped_state
        _write_loop_report_if_execute(optimization_root, summary, execute=execute)
        return summary

    try:
        propose_plan = build_propose_next_iteration_plan(
            tick_summary=tick_after_reconcile,
            state_doc=working_state_doc,
            optimization_root=optimization_root,
            from_iteration=current_iteration,
            next_iteration=target_next_iteration,
            optimization_config_path=optimization_config_path,
        )
    except ProposeNextIterationError as exc:
        message = str(exc)
        if not _propose_error_is_safe_loop_stop(message):
            raise
        summary["loop_stopped_before_propose"] = True
        summary["propose_blocked_by_policy_or_idempotence"] = True
        summary["propose_error"] = message
        summary["recommended_action"] = "no_action"
        _write_loop_report_if_execute(optimization_root, summary, execute=execute)
        return summary

    summary["propose_next_iteration_plan"] = propose_plan.to_dict()

    propose_guard_report = evaluate_guards(
        optimization_root=optimization_root,
        tick_summary=tick_after_reconcile,
        action="propose_next_iteration",
        from_iteration=current_iteration,
        next_iteration=target_next_iteration,
        optimization_config_path=optimization_config_path,
    )
    summary["propose_guard_report"] = propose_guard_report

    if guard_report_blocks(propose_guard_report):
        summary["loop_stopped_before_propose"] = True
        summary["propose_blocked_by_guards"] = True
        summary["recommended_action"] = propose_guard_report["recommended_action"]
        if execute:
            report_path = write_guard_report(optimization_root, propose_guard_report)
            summary["guard_report_written"] = str(report_path)
        _write_loop_report_if_execute(optimization_root, summary, execute=execute)
        return summary

    if not execute:
        dependent_preview = build_dependent_optimizer_tick_plan(
            optimization_root=optimization_root,
            current_iteration=target_next_iteration,
            next_iteration=target_next_iteration + 1,
            dependency_job_id="<next-array-job-id>",
            array_spec=array_spec,
            max_cases=max_cases,
            submit_script=submit_script,
            workflow_root=workflow_root,
            workflow_env=workflow_env,
            job_name_prefix=job_name_prefix,
            optimization_config_path=optimization_config_path,
        )
        summary["submit_plan_after_materialization"] = {
            "iteration": target_next_iteration,
            "array_spec": array_spec,
            "max_cases": max_cases,
            "job_name": iteration_array_job_name(
                job_name_prefix=job_name_prefix,
                iteration=target_next_iteration,
            ),
            "note": (
                "Exact submit command requires executing optimizer proposal and "
                "materializing the next iteration first. Dry-run does not call the "
                "external optimizer and does not call sbatch."
            ),
        }
        summary["dependent_optimizer_tick_plan"] = dependent_preview.to_dict()
        summary["dependent_optimizer_tick_script_preview"] = (
            build_dependent_tick_script(dependent_preview)
        )
        summary["recommended_action"] = "run_loop_once_execute_after_review"
        _write_loop_report_if_execute(optimization_root, summary, execute=execute)
        return summary

    optimizer_result = run_external_optimizer_command(propose_plan)
    optimizer_outputs = verify_optimizer_outputs(propose_plan)

    post_optimizer_stopping_report = evaluate_stopping(
        optimization_root=optimization_root,
        tick_summary=tick_after_reconcile,
        state_doc=working_state_doc,
        action="run_loop_once",
        iteration=current_iteration,
        next_iteration=target_next_iteration,
        optimization_config_path=optimization_config_path,
        signals_iteration=target_next_iteration,
    )
    summary["external_optimizer_result"] = optimizer_result.to_dict()
    summary["optimizer_outputs"] = optimizer_outputs
    summary["post_optimizer_stopping_report"] = post_optimizer_stopping_report

    if stopping_blocks(post_optimizer_stopping_report):
        report_path = write_stopping_report(
            optimization_root,
            post_optimizer_stopping_report,
        )
        stopped_state = update_state_after_stopping(
            state_doc=working_state_doc,
            report=post_optimizer_stopping_report,
            report_path=report_path,
        )
        write_optimization_state(optimization_root, stopped_state)
        summary["state_written"] = True
        summary["loop_stopped_after_optimizer"] = True
        summary["propose_blocked_by_stopping"] = True
        summary["recommended_action"] = post_optimizer_stopping_report[
            "recommended_action"
        ]
        summary["stopping_report_written"] = str(report_path)
        summary["optimization_state_after_stopping"] = stopped_state
        _write_loop_report_if_execute(optimization_root, summary, execute=execute)
        return summary

    preparation_result = prepare_next_campaign_from_optimizer_outputs(propose_plan)

    materialization_result = None
    if propose_plan.materialize_after_prepare:
        materialization_result = materialize_next_campaign(propose_plan)

    init_states_result = None
    if propose_plan.init_case_states_after_materialize:
        init_states_result = init_next_case_states(propose_plan)

    next_audit = run_optimizer_tick(
        optimization_root=optimization_root,
        iteration=propose_plan.next_iteration,
    )
    next_iterations = next_audit.get("iterations", [])
    if len(next_iterations) != 1:
        raise OptimizerLoopError(
            "failed to audit prepared next iteration: "
            f"expected 1 iteration summary, got {len(next_iterations)}"
        )

    state_after_propose = update_state_after_next_iteration(
        state_doc=working_state_doc,
        plan=propose_plan,
        optimizer_result=optimizer_result,
        optimizer_outputs=optimizer_outputs,
        preparation_result=preparation_result,
        materialization_result=materialization_result,
        init_states_result=init_states_result,
        next_iteration_summary=next_iterations[0],
    )
    write_optimization_state(optimization_root, state_after_propose)
    summary["state_written"] = True
    summary["campaign_preparation_result"] = preparation_result
    summary["materialization_result"] = materialization_result
    summary["init_case_states_result"] = init_states_result
    summary["next_iteration_audit"] = next_iterations[0]
    summary["optimization_state_after_propose"] = state_after_propose

    submit_audit = run_optimizer_tick(
        optimization_root=optimization_root,
        iteration=target_next_iteration,
    )
    submit_guard_report = evaluate_guards(
        optimization_root=optimization_root,
        tick_summary=submit_audit,
        action="submit_iteration",
        iteration=target_next_iteration,
        array_spec=array_spec,
        max_cases=max_cases,
        optimization_config_path=optimization_config_path,
    )
    summary["submit_guard_report"] = submit_guard_report

    if guard_report_blocks(submit_guard_report):
        summary["loop_stopped_before_submit"] = True
        summary["submit_blocked_by_guards"] = True
        summary["recommended_action"] = submit_guard_report["recommended_action"]
        report_path = write_guard_report(optimization_root, submit_guard_report)
        summary["submit_guard_report_written"] = str(report_path)
        _write_loop_report_if_execute(optimization_root, summary, execute=execute)
        return summary

    submit_plan = build_submit_iteration_plan(
        tick_summary=submit_audit,
        optimization_root=optimization_root,
        iteration=target_next_iteration,
        array_spec=array_spec,
        max_cases=max_cases,
        submit_script=submit_script,
        workflow_root=workflow_root,
        workflow_env=workflow_env,
        job_name=iteration_array_job_name(
            job_name_prefix=job_name_prefix,
            iteration=target_next_iteration,
        ),
    )
    summary["submit_plan"] = submit_plan.to_dict()
    summary["state_updates_after_submit"] = state_updates_preview(submit_plan)

    submit_result = execute_submit_iteration(submit_plan)
    state_after_submit = update_state_after_submit(
        state_doc=state_after_propose,
        plan=submit_plan,
        result=submit_result,
    )
    write_optimization_state(optimization_root, state_after_submit)
    summary["submission_result"] = submit_result.to_dict()
    summary["optimization_state_after_submit"] = state_after_submit

    dependent_tick_plan = build_dependent_optimizer_tick_plan(
        optimization_root=optimization_root,
        current_iteration=target_next_iteration,
        next_iteration=target_next_iteration + 1,
        dependency_job_id=submit_result.job_id,
        array_spec=array_spec,
        max_cases=max_cases,
        submit_script=submit_script,
        workflow_root=workflow_root,
        workflow_env=workflow_env,
        job_name_prefix=job_name_prefix,
        optimization_config_path=optimization_config_path,
    )
    write_dependent_tick_script(dependent_tick_plan)
    dependent_tick_result = execute_dependent_optimizer_tick_submit(dependent_tick_plan)
    state_after_tick_submit = update_state_after_dependent_tick_submit(
        state_doc=state_after_submit,
        iteration=target_next_iteration,
        plan=dependent_tick_plan,
        result=dependent_tick_result,
    )
    write_optimization_state(optimization_root, state_after_tick_submit)

    summary["dependent_optimizer_tick_plan"] = dependent_tick_plan.to_dict()
    summary["dependent_optimizer_tick_script_written"] = str(
        dependent_tick_plan.script_path
    )
    summary["dependent_optimizer_tick_result"] = dependent_tick_result.to_dict()
    summary["optimization_state_after_dependent_tick_submit"] = state_after_tick_submit
    summary["recommended_action"] = "wait_for_jobs"

    report_path = _write_loop_report_if_execute(
        optimization_root,
        summary,
        execute=execute,
    )
    if report_path is not None:
        summary["loop_report_written"] = str(report_path)
        state_with_report = attach_loop_report_to_iteration(
            state_doc=state_after_tick_submit,
            iteration=target_next_iteration,
            report_path=report_path,
        )
        write_optimization_state(optimization_root, state_with_report)
        summary["optimization_state_after_loop_report"] = state_with_report

    return summary


def _propose_error_is_safe_loop_stop(message: str) -> bool:
    safe_prefixes = (
        "max_iterations reached",
        "max_total_submitted_cases reached",
        "next_iteration already exists in optimization_state.json",
        "next iteration campaign root already exists",
    )
    return any(message.startswith(prefix) for prefix in safe_prefixes)


def build_dependent_optimizer_tick_plan(
    *,
    optimization_root: Path,
    current_iteration: int,
    next_iteration: int,
    dependency_job_id: str,
    array_spec: str | None,
    max_cases: int | None,
    submit_script: Path | None,
    workflow_root: Path | None,
    workflow_env: Path | None,
    job_name_prefix: str | None,
    optimization_config_path: Path | None,
) -> DependentOptimizerTickPlan:
    optimization_root = optimization_root.resolve()
    resolved_workflow_root = (
        workflow_root.expanduser().resolve(strict=False)
        if workflow_root is not None
        else Path(__file__).resolve().parents[1]
    )
    resolved_workflow_env = (
        workflow_env.expanduser().resolve(strict=False)
        if workflow_env is not None
        else Path.home() / "apps" / "env" / "campaign-workflow.sh"
    )

    log_dir = loop_log_dir(
        optimization_root,
        current_iteration=current_iteration,
        next_iteration=next_iteration,
    )
    script_path = log_dir / f"optimizer_tick_iter_{current_iteration:03d}.sh"
    job_name = optimizer_tick_job_name(
        job_name_prefix=job_name_prefix,
        iteration=current_iteration,
    )
    stdout_path = log_dir / f"{job_name}_%j.out"
    stderr_path = log_dir / f"{job_name}_%j.err"

    tick_command = [
        "python",
        "-m",
        "campaign_workflow.cli.optimizer_tick",
        "--optimization-root",
        str(optimization_root),
        "--action",
        "run_loop_once",
        "--iteration",
        str(current_iteration),
        "--next-iteration",
        str(next_iteration),
    ]
    if array_spec is not None:
        tick_command.extend(["--array-spec", str(array_spec)])
    if max_cases is not None:
        tick_command.extend(["--max-cases", str(max_cases)])
    if submit_script is not None:
        tick_command.extend(
            ["--submit-script", str(submit_script.expanduser().resolve(strict=False))]
        )
    tick_command.extend(["--workflow-root", str(resolved_workflow_root)])
    tick_command.extend(["--workflow-env", str(resolved_workflow_env)])
    if job_name_prefix:
        tick_command.extend(["--job-name-prefix", job_name_prefix])
    if optimization_config_path is not None:
        config_path = optimization_config_path.expanduser().resolve(strict=False)
        tick_command.extend(["--optimization-config", str(config_path)])
    tick_command.append("--execute")

    submit_command = [
        "sbatch",
        "--parsable",
        f"--dependency=afterany:{dependency_job_id}",
        f"--job-name={job_name}",
        f"--output={stdout_path}",
        f"--error={stderr_path}",
        str(script_path),
    ]

    return DependentOptimizerTickPlan(
        optimization_root=optimization_root,
        current_iteration=int(current_iteration),
        next_iteration=int(next_iteration),
        dependency_job_id=str(dependency_job_id),
        workflow_root=resolved_workflow_root,
        workflow_env=resolved_workflow_env,
        job_name=job_name,
        script_path=script_path,
        stdout_path=stdout_path,
        stderr_path=stderr_path,
        submit_command=submit_command,
        tick_command=tick_command,
        working_directory=optimization_root,
    )


def build_dependent_tick_script(plan: DependentOptimizerTickPlan) -> str:
    command = " ".join(shlex.quote(part) for part in plan.tick_command)
    return (
        "#!/usr/bin/env bash\n"
        "set -Eeuo pipefail\n"
        "trap 'echo \"[OPTIMIZER-LOOP] ERROR at line ${LINENO}: ${BASH_COMMAND}\" >&2' ERR\n"
        "set -x\n"
        f"OPTIMIZATION_ROOT={shlex.quote(str(plan.optimization_root))}\n"
        f"WORKFLOW_ROOT={shlex.quote(str(plan.workflow_root))}\n"
        f"WORKFLOW_ENV={shlex.quote(str(plan.workflow_env))}\n"
        'cd "${OPTIMIZATION_ROOT}"\n'
        'if [[ ! -f "${WORKFLOW_ENV}" ]]; then\n'
        '    echo "[OPTIMIZER-LOOP] ERROR: missing workflow env: ${WORKFLOW_ENV}" >&2\n'
        "    exit 1\n"
        "fi\n"
        'source "${WORKFLOW_ENV}"\n'
        f"{command}\n"
    )


def write_dependent_tick_script(plan: DependentOptimizerTickPlan) -> None:
    plan.script_path.parent.mkdir(parents=True, exist_ok=True)
    plan.script_path.write_text(build_dependent_tick_script(plan), encoding="utf-8")
    plan.script_path.chmod(0o750)


def execute_dependent_optimizer_tick_submit(
    plan: DependentOptimizerTickPlan,
) -> DependentOptimizerTickResult:
    completed = subprocess.run(
        plan.submit_command,
        cwd=str(plan.working_directory),
        text=True,
        capture_output=True,
        check=False,
    )
    if completed.returncode != 0:
        raise OptimizerLoopError(
            "dependent optimizer tick sbatch failed with return code "
            f"{completed.returncode}: stdout={completed.stdout!r} stderr={completed.stderr!r}"
        )

    job_id = parse_sbatch_job_id(completed.stdout)
    if not job_id:
        raise OptimizerLoopError(
            "could not parse dependent optimizer tick job id from stdout: "
            f"{completed.stdout!r}"
        )

    return DependentOptimizerTickResult(
        job_id=job_id,
        return_code=completed.returncode,
        stdout=completed.stdout,
        stderr=completed.stderr,
        submitted_at=now_utc(),
    )


def update_state_after_dependent_tick_submit(
    *,
    state_doc: dict[str, Any],
    iteration: int,
    plan: DependentOptimizerTickPlan,
    result: DependentOptimizerTickResult,
) -> dict[str, Any]:
    updated = dict(state_doc)
    iterations: list[dict[str, Any]] = []
    found = False

    for raw_item in state_doc.get("iterations", []):
        if not isinstance(raw_item, dict):
            continue
        item = dict(raw_item)
        if int(item.get("iteration", -1)) == int(iteration):
            found = True
            tick_job_ids = list(item.get("optimizer_tick_job_ids", []))
            if result.job_id not in tick_job_ids:
                tick_job_ids.append(result.job_id)
            item["optimizer_tick_job_ids"] = tick_job_ids
            item["dependent_optimizer_tick"] = {
                "job_id": result.job_id,
                "submitted_at": result.submitted_at,
                "dependency": f"afterany:{plan.dependency_job_id}",
                "dependency_job_id": plan.dependency_job_id,
                "job_name": plan.job_name,
                "script_path": str(plan.script_path),
                "stdout_path": str(plan.stdout_path),
                "stderr_path": str(plan.stderr_path),
                "submit_command": plan.submit_command,
                "tick_command": plan.tick_command,
                "next_iteration": plan.next_iteration,
            }
        iterations.append(item)

    if not found:
        raise OptimizerLoopError(
            f"cannot update state: iteration not found for dependent tick: {iteration}"
        )

    updated["iterations"] = iterations
    updated["status"] = "running"
    updated["recommended_action"] = "wait_for_jobs"
    updated["updated_at"] = result.submitted_at
    return updated


def attach_loop_report_to_iteration(
    *,
    state_doc: dict[str, Any],
    iteration: int,
    report_path: Path,
) -> dict[str, Any]:
    updated = dict(state_doc)
    iterations: list[dict[str, Any]] = []
    for raw_item in state_doc.get("iterations", []):
        if not isinstance(raw_item, dict):
            continue
        item = dict(raw_item)
        if int(item.get("iteration", -1)) == int(iteration):
            item["latest_loop_report"] = str(report_path)
        iterations.append(item)
    updated["iterations"] = iterations
    updated["latest_loop_report"] = str(report_path)
    updated["updated_at"] = now_utc()
    return updated


def iteration_array_job_name(*, job_name_prefix: str | None, iteration: int) -> str:
    if job_name_prefix:
        return f"{job_name_prefix}_iter{int(iteration):03d}_cycle"
    return f"cw_iter_{int(iteration):03d}_cycle"


def optimizer_tick_job_name(*, job_name_prefix: str | None, iteration: int) -> str:
    if job_name_prefix:
        return f"{job_name_prefix}_tick{int(iteration):03d}"
    return f"cw_tick_{int(iteration):03d}"


def loop_log_dir(
    optimization_root: Path,
    *,
    current_iteration: int,
    next_iteration: int,
) -> Path:
    return (
        optimization_root
        / "loop_logs"
        / f"iter_{int(current_iteration):03d}_to_iter_{int(next_iteration):03d}"
    )


def loop_report_path(
    optimization_root: Path,
    *,
    current_iteration: int,
    next_iteration: int,
) -> Path:
    return (
        loop_log_dir(
            optimization_root,
            current_iteration=current_iteration,
            next_iteration=next_iteration,
        )
        / "run_loop_once_report.json"
    )


def _write_loop_report_if_execute(
    optimization_root: Path,
    summary: dict[str, Any],
    *,
    execute: bool,
) -> Path | None:
    if not execute:
        return None
    path = loop_report_path(
        optimization_root,
        current_iteration=int(summary["iteration"]),
        next_iteration=int(summary["next_iteration"]),
    )
    write_json_atomic(path, summary, dry_run=False)
    return path
