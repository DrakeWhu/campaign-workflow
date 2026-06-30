from __future__ import annotations

import os
import shlex
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence

from campaign_workflow.batch_campaign import (
    build_batch_campaign_plan,
    execute_batch_campaign_plan,
)
from campaign_workflow.core.atomic_io import read_json
from campaign_workflow.core.case_materialization import (
    build_case_materialization_plan,
    get_case_materialization_config,
    materialize_one_case,
)
from campaign_workflow.core.state import ensure_case_state, now_utc
from campaign_workflow.core.tsv_cases import load_campaign_config, load_cases
from campaign_workflow.optimization_state import pause_file_path

OPTIMIZATION_CONFIG_FILENAME = "optimization.json"
ALLOWED_FROM_STATUSES = frozenset({"reduced_ready", "closeable", "closed"})


class ProposeNextIterationError(RuntimeError):
    """Raised when the next optimizer iteration cannot be proposed safely."""


@dataclass(frozen=True)
class OptimizerExternalCommand:
    raw_command: list[str]
    effective_command: list[str]
    working_directory: Path
    env_script: Path | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "raw_command": self.raw_command,
            "effective_command": self.effective_command,
            "working_directory": str(self.working_directory),
            "env_script": None if self.env_script is None else str(self.env_script),
        }


@dataclass(frozen=True)
class ExternalOptimizerResult:
    command: list[str]
    working_directory: Path
    return_code: int
    stdout: str
    stderr: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "command": self.command,
            "working_directory": str(self.working_directory),
            "return_code": self.return_code,
            "stdout": self.stdout,
            "stderr": self.stderr,
            "ok": self.return_code == 0,
        }


@dataclass(frozen=True)
class NextIterationPlan:
    optimization_root: Path
    optimization_config_path: Path
    from_iteration: int
    next_iteration: int
    optimizer_run_dir: Path
    candidate_batch: Path
    batch_campaign_plan: Path
    template_campaign_root: Path
    output_campaign_root: Path
    campaign_name: str
    external_command: OptimizerExternalCommand
    materialize_after_prepare: bool
    init_case_states_after_materialize: bool
    policy: dict[str, Any]
    readiness: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        steps = [
            "run_external_optimizer_command",
            "verify_optimizer_outputs",
            "prepare_next_campaign_root",
        ]
        if self.materialize_after_prepare:
            steps.append("materialize_next_campaign_cases")
        if self.init_case_states_after_materialize:
            steps.append("init_next_campaign_case_states")
        steps.extend(["update_optimization_state", "stop_before_submit"])

        return {
            "optimization_root": str(self.optimization_root),
            "optimization_config_path": str(self.optimization_config_path),
            "from_iteration": self.from_iteration,
            "next_iteration": self.next_iteration,
            "optimizer_run_dir": str(self.optimizer_run_dir),
            "expected_outputs": {
                "candidate_batch": str(self.candidate_batch),
                "batch_campaign_plan": str(self.batch_campaign_plan),
            },
            "template_campaign_root": str(self.template_campaign_root),
            "output_campaign_root": str(self.output_campaign_root),
            "campaign_name": self.campaign_name,
            "external_command": self.external_command.to_dict(),
            "materialize_after_prepare": self.materialize_after_prepare,
            "init_case_states_after_materialize": self.init_case_states_after_materialize,
            "policy": self.policy,
            "readiness": self.readiness,
            "planned_steps": steps,
            "will_not": [
                "submit jobs",
                "run simulation code",
                "run analysis adapters",
                "read raw diagnostics",
                "delete files",
                "modify input_template.py physics content",
            ],
            "destructive_operations": 0,
        }


def load_optimization_config(
    optimization_root: Path,
    config_path: Path | None = None,
) -> tuple[Path, dict[str, Any]]:
    path = config_path or optimization_root / OPTIMIZATION_CONFIG_FILENAME
    path = Path(path).expanduser()
    if not path.is_absolute():
        path = optimization_root / path
    path = path.resolve(strict=False)

    if not path.exists():
        raise ProposeNextIterationError(
            f"missing optimization config: {path}. Create optimization.json first."
        )
    if not path.is_file():
        raise ProposeNextIterationError(f"optimization config is not a file: {path}")

    try:
        data = read_json(path)
    except Exception as exc:
        raise ProposeNextIterationError(
            f"failed to read optimization config {path}: {exc}"
        ) from exc

    if data.get("schema_version") != 1:
        raise ProposeNextIterationError(f"{path} schema_version must be 1")
    for key in ("optimizer", "campaign_preparation", "policy"):
        if not isinstance(data.get(key), dict):
            raise ProposeNextIterationError(f"{path} must define object field {key!r}")

    return path, data


def build_propose_next_iteration_plan(
    *,
    tick_summary: dict[str, Any],
    state_doc: dict[str, Any],
    optimization_root: Path,
    from_iteration: int,
    next_iteration: int | None,
    optimization_config_path: Path | None = None,
) -> NextIterationPlan:
    optimization_root = optimization_root.resolve()

    if (
        tick_summary.get("pause_file_exists")
        or pause_file_path(optimization_root).exists()
    ):
        raise ProposeNextIterationError(
            "optimization is paused: PAUSE_OPTIMIZATION exists"
        )

    config_path, config = load_optimization_config(
        optimization_root, optimization_config_path
    )

    from_state = _find_iteration_state(state_doc, from_iteration)
    if from_state is None:
        raise ProposeNextIterationError(
            f"from_iteration not found: iter_{from_iteration:03d}"
        )

    from_summary = _find_iteration_summary(tick_summary, from_iteration) or {}
    policy = dict(config.get("policy", {}) or {})
    readiness = evaluate_from_iteration_readiness(
        from_state=from_state,
        from_summary=from_summary,
        policy=policy,
    )
    if not readiness["ok"]:
        raise ProposeNextIterationError(str(readiness["reason"]))

    next_iter = (
        int(next_iteration) if next_iteration is not None else from_iteration + 1
    )
    if next_iter <= from_iteration:
        raise ProposeNextIterationError(
            "next_iteration must be greater than from_iteration: "
            f"from={from_iteration}, next={next_iter}"
        )
    if _find_iteration_state(state_doc, next_iter) is not None:
        raise ProposeNextIterationError(
            "next_iteration already exists in optimization_state.json: "
            f"iter_{next_iter:03d}"
        )
    if _find_iteration_summary(tick_summary, next_iter) is not None:
        raise ProposeNextIterationError(
            f"next iteration campaign root already exists: iter_{next_iter:03d}"
        )

    _check_global_policy(
        state_doc=state_doc,
        policy=policy,
        next_iteration=next_iter,
    )

    prep = dict(config.get("campaign_preparation", {}) or {})
    optimizer_runs_dir = _resolve_root_path(
        optimization_root, prep.get("optimizer_runs_dir", "optimizer_runs")
    )
    iterations_dir = _resolve_root_path(
        optimization_root, prep.get("iterations_dir", "iterations")
    )
    optimizer_run_dir = optimizer_runs_dir / f"iter_{next_iter:03d}"
    outputs_dir = optimizer_run_dir / "outputs"

    default_template_root = str(
        from_state.get("campaign_root", f"iterations/iter_{from_iteration:03d}")
    )
    template_campaign_root = _resolve_root_path(
        optimization_root,
        prep.get("template_campaign_root", default_template_root),
    )

    campaign_name_template = str(
        prep.get(
            "campaign_name_template",
            f"{state_doc.get('optimization_name', optimization_root.name)}_iter_{{next_iteration:03d}}",
        )
    )
    campaign_name = campaign_name_template.format(
        optimization_name=state_doc.get("optimization_name", optimization_root.name),
        from_iteration=from_iteration,
        next_iteration=next_iter,
    )

    command = render_external_command(
        optimization_root=optimization_root,
        config=config,
        from_iteration=from_iteration,
        next_iteration=next_iter,
        optimizer_run_dir=optimizer_run_dir,
    )

    return NextIterationPlan(
        optimization_root=optimization_root,
        optimization_config_path=config_path,
        from_iteration=from_iteration,
        next_iteration=next_iter,
        optimizer_run_dir=optimizer_run_dir,
        candidate_batch=outputs_dir / "candidate_batch.tsv",
        batch_campaign_plan=outputs_dir / "batch_campaign_plan.json",
        template_campaign_root=template_campaign_root,
        output_campaign_root=iterations_dir / f"iter_{next_iter:03d}",
        campaign_name=campaign_name,
        external_command=command,
        materialize_after_prepare=bool(prep.get("materialize_after_prepare", True)),
        init_case_states_after_materialize=bool(
            prep.get("init_case_states_after_materialize", True)
        ),
        policy=policy,
        readiness=readiness,
    )


def evaluate_from_iteration_readiness(
    *,
    from_state: dict[str, Any],
    from_summary: dict[str, Any],
    policy: dict[str, Any],
) -> dict[str, Any]:
    status = str(from_state.get("status", from_summary.get("status", "")))
    recommended = str(
        from_state.get("recommended_action", from_summary.get("recommended_action", ""))
    )
    slurm = from_state.get("slurm", {})

    if isinstance(slurm, dict) and bool(slurm.get("active")):
        return {
            "ok": False,
            "reason": "from_iteration still has active scheduler jobs; recommended_action=wait_for_jobs",
            "recommended_action": "wait_for_jobs",
            "status": status,
        }

    if status not in ALLOWED_FROM_STATUSES:
        action = (
            "wait_for_jobs"
            if recommended == "wait_for_jobs"
            or status in {"running", "submitted", "postprocessing"}
            else "inspect_failures"
        )
        return {
            "ok": False,
            "reason": (
                "from_iteration is not ready to propose next iteration: "
                f"status={status!r}, recommended_action={recommended!r}"
            ),
            "recommended_action": action,
            "status": status,
        }

    submitted = _positive_int(
        from_state.get("submitted_case_count"),
        from_state.get("n_submitted_case_dirs"),
        from_state.get("n_cases"),
        default=0,
    )
    valid = _positive_int(
        from_state.get("n_submitted_reduced_valid"),
        from_state.get("n_reduced_valid"),
        default=0,
    )
    failed = _positive_int(
        from_state.get("n_submitted_sim_failed"),
        from_state.get("n_sim_failed"),
        default=0,
    )

    denominator = max(submitted, 1)
    valid_fraction = valid / denominator
    failed_fraction = failed / denominator

    min_valid = int(policy.get("min_reduced_valid_to_continue", 1))
    min_fraction = float(policy.get("min_valid_fraction_to_continue", 0.0))
    max_failed = float(policy.get("max_failed_fraction_to_continue", 1.0))

    base = {
        "status": status,
        "submitted_case_count": submitted,
        "valid_count": valid,
        "failed_count": failed,
        "valid_fraction": valid_fraction,
        "failed_fraction": failed_fraction,
    }

    if failed_fraction > max_failed:
        return {
            **base,
            "ok": False,
            "reason": (
                "failed fraction exceeds policy: "
                f"failed_fraction={failed_fraction:.6g}, limit={max_failed:.6g}"
            ),
            "recommended_action": "inspect_failures",
        }
    if valid < min_valid:
        return {
            **base,
            "ok": False,
            "reason": (
                f"not enough reduced-valid cases: valid={valid}, required={min_valid}"
            ),
            "recommended_action": "wait_for_jobs",
        }
    if valid_fraction < min_fraction:
        return {
            **base,
            "ok": False,
            "reason": (
                "reduced-valid fraction below policy: "
                f"valid_fraction={valid_fraction:.6g}, required={min_fraction:.6g}"
            ),
            "recommended_action": "wait_for_jobs",
        }

    return {
        **base,
        "ok": True,
        "reason": "from_iteration satisfies readiness policy",
        "recommended_action": "propose_next_iteration",
    }


def render_external_command(
    *,
    optimization_root: Path,
    config: dict[str, Any],
    from_iteration: int,
    next_iteration: int,
    optimizer_run_dir: Path,
) -> OptimizerExternalCommand:
    optimizer = dict(config.get("optimizer", {}) or {})
    raw_command = optimizer.get("command")
    if not isinstance(raw_command, list) or not raw_command:
        raise ProposeNextIterationError(
            "optimization.json optimizer.command must be a non-empty list"
        )

    optimizer_config = _resolve_root_path(
        optimization_root, optimizer.get("optimizer_config", "optimizer.json")
    )
    working_directory = _resolve_root_path(
        optimization_root, optimizer.get("working_directory", ".")
    )

    context = {
        "optimization_root": str(optimization_root),
        "from_iteration": str(from_iteration),
        "next_iteration": str(next_iteration),
        "next_iteration_padded": f"{next_iteration:03d}",
        "optimizer_config": str(optimizer_config),
        "optimizer_run_dir": str(optimizer_run_dir),
    }
    rendered = [_render_token(str(item), context) for item in raw_command]
    _reject_forbidden_external_command(rendered)

    env_script = None
    effective_command = list(rendered)
    if optimizer.get("env_script") not in {None, ""}:
        env_script = _resolve_root_path(optimization_root, optimizer["env_script"])
        shell_command = (
            "source "
            + shlex.quote(str(env_script))
            + " >/dev/null 2>&1 && exec "
            + " ".join(shlex.quote(part) for part in rendered)
        )
        effective_command = ["bash", "-lc", shell_command]

    return OptimizerExternalCommand(
        raw_command=rendered,
        effective_command=effective_command,
        working_directory=working_directory,
        env_script=env_script,
    )


def run_external_optimizer_command(
    plan: NextIterationPlan,
) -> ExternalOptimizerResult:
    """Run the configured external optimizer command exactly once.

    This function intentionally does not import campaign-optimizer or any optimizer
    dependency. The integration boundary is the configured command plus files.
    """
    command = list(plan.external_command.effective_command)
    working_directory = plan.external_command.working_directory

    if not command:
        raise ProposeNextIterationError("external optimizer command is empty")
    if not working_directory.exists():
        raise ProposeNextIterationError(
            f"optimizer working_directory does not exist: {working_directory}"
        )
    if not working_directory.is_dir():
        raise ProposeNextIterationError(
            f"optimizer working_directory is not a directory: {working_directory}"
        )

    _reject_forbidden_external_command(command)

    env = os.environ.copy()
    env["CAMPAIGN_WORKFLOW_OPTIMIZATION_ROOT"] = str(plan.optimization_root)
    env["CAMPAIGN_WORKFLOW_FROM_ITERATION"] = str(plan.from_iteration)
    env["CAMPAIGN_WORKFLOW_NEXT_ITERATION"] = str(plan.next_iteration)
    env["CAMPAIGN_WORKFLOW_OPTIMIZER_RUN_DIR"] = str(plan.optimizer_run_dir)

    try:
        completed = subprocess.run(
            command,
            cwd=str(working_directory),
            env=env,
            text=True,
            capture_output=True,
            check=False,
        )
    except FileNotFoundError as exc:
        raise ProposeNextIterationError(
            f"failed to execute optimizer command; executable not found: {command[0]!r}"
        ) from exc
    except Exception as exc:
        raise ProposeNextIterationError(
            f"failed to execute optimizer command {command!r}: {exc}"
        ) from exc

    result = ExternalOptimizerResult(
        command=command,
        working_directory=working_directory,
        return_code=int(completed.returncode),
        stdout=completed.stdout or "",
        stderr=completed.stderr or "",
    )

    if result.return_code != 0:
        stderr_tail = _tail_text(result.stderr)
        stdout_tail = _tail_text(result.stdout)
        raise ProposeNextIterationError(
            "external optimizer command failed: "
            f"return_code={result.return_code}, "
            f"stderr_tail={stderr_tail!r}, stdout_tail={stdout_tail!r}"
        )

    return result


def verify_optimizer_outputs(plan: NextIterationPlan) -> dict[str, Any]:
    """Verify the minimal file contract produced by campaign-optimizer."""
    candidate_info = _verify_required_output_file(
        plan.candidate_batch,
        label="candidate_batch.tsv",
    )
    batch_plan_info = _verify_required_output_file(
        plan.batch_campaign_plan,
        label="batch_campaign_plan.json",
    )

    try:
        batch_plan_data = read_json(plan.batch_campaign_plan)
    except Exception as exc:
        raise ProposeNextIterationError(
            f"failed to parse batch_campaign_plan.json: {plan.batch_campaign_plan}: {exc}"
        ) from exc

    if not isinstance(batch_plan_data, dict):
        raise ProposeNextIterationError(
            f"batch_campaign_plan.json must contain a JSON object: {plan.batch_campaign_plan}"
        )

    return {
        "ok": True,
        "optimizer_run_dir": str(plan.optimizer_run_dir),
        "candidate_batch": candidate_info,
        "batch_campaign_plan": {
            **batch_plan_info,
            "schema_version": batch_plan_data.get("schema_version"),
            "plan_type": batch_plan_data.get("plan_type"),
            "optimizer_iteration": batch_plan_data.get("optimizer_iteration"),
        },
    }


def prepare_next_campaign_from_optimizer_outputs(
    plan: NextIterationPlan,
) -> dict[str, Any]:
    """Create iterations/iter_XXX from verified optimizer outputs.

    This reuses the Fase 3 batch-campaign preparation logic. It writes campaign
    setup files only: campaign.json, cases.tsv, input_template.py, array_logs and
    optimizer_batch_provenance.json. It does not materialize cases and does not
    submit jobs.
    """
    verify_optimizer_outputs(plan)

    try:
        batch_campaign_plan = build_batch_campaign_plan(
            candidate_batch=plan.candidate_batch,
            batch_plan=plan.batch_campaign_plan,
            template_campaign_root=plan.template_campaign_root,
            output_campaign_root=plan.output_campaign_root,
            campaign_name=plan.campaign_name,
        )
        summary = execute_batch_campaign_plan(batch_campaign_plan)
    except Exception as exc:
        raise ProposeNextIterationError(
            f"failed to prepare next campaign root {plan.output_campaign_root}: {exc}"
        ) from exc

    prepared_files = {
        "campaign_json": plan.output_campaign_root / "campaign.json",
        "cases_tsv": plan.output_campaign_root / "cases.tsv",
        "input_template": plan.output_campaign_root / "input_template.py",
        "array_logs": plan.output_campaign_root / "array_logs",
        "provenance": plan.output_campaign_root / "optimizer_batch_provenance.json",
    }
    missing = [
        name
        for name, path in prepared_files.items()
        if not (path.is_dir() if name == "array_logs" else path.is_file())
    ]
    if missing:
        raise ProposeNextIterationError(
            f"next campaign preparation did not produce required outputs: {missing}"
        )

    return {
        "ok": True,
        "campaign_root": str(plan.output_campaign_root),
        "summary": summary,
        "prepared_files": {name: str(path) for name, path in prepared_files.items()},
        "destructive_operations": 0,
    }


def materialize_next_campaign(plan: NextIterationPlan) -> dict[str, Any]:
    """Materialize case directories and case-local input files for next iteration."""
    campaign_root = plan.output_campaign_root

    if not campaign_root.is_dir():
        raise ProposeNextIterationError(
            f"cannot materialize next campaign; campaign root does not exist: {campaign_root}"
        )

    try:
        config = load_campaign_config(campaign_root)
        cases = load_cases(campaign_root, config)
        mat_config = get_case_materialization_config(config)
        materialization_plans = build_case_materialization_plan(
            campaign_root=campaign_root,
            cases=cases,
            config=config,
            mat_config=mat_config,
        )
    except Exception as exc:
        raise ProposeNextIterationError(
            f"failed to build materialization plan for {campaign_root}: {exc}"
        ) from exc

    results: list[dict[str, Any]] = []
    for item in materialization_plans:
        result = materialize_one_case(
            plan=item,
            dry_run=False,
            overwrite=False,
        )
        results.append(result)

    errors = _collect_case_errors(results)
    if errors:
        raise ProposeNextIterationError(
            "failed to materialize next campaign cases: " + "; ".join(errors[:10])
        )

    return {
        "ok": True,
        "campaign_root": str(campaign_root),
        "cases_processed": len(results),
        "inputs_written": sum(int(item.get("input_written", 0)) for item in results),
        "envs_written": sum(int(item.get("env_written", 0)) for item in results),
        "actions_performed": sum(len(item.get("actions", [])) for item in results),
        "cases_with_errors": 0,
        "errors": 0,
        "destructive_operations": 0,
    }


def init_next_case_states(plan: NextIterationPlan) -> dict[str, Any]:
    """Initialize state.json and validation.json for next iteration cases."""
    campaign_root = plan.output_campaign_root

    if not campaign_root.is_dir():
        raise ProposeNextIterationError(
            f"cannot init case states; campaign root does not exist: {campaign_root}"
        )

    try:
        config = load_campaign_config(campaign_root)
        cases = load_cases(campaign_root, config)
    except Exception as exc:
        raise ProposeNextIterationError(
            f"failed to load prepared next campaign {campaign_root}: {exc}"
        ) from exc

    results: list[dict[str, Any]] = []
    for case in cases:
        result = ensure_case_state(
            case_dir=campaign_root / case.case_name,
            case=case,
            config=config,
            dry_run=False,
            create_missing_case_dirs=False,
            check_only=False,
        )
        results.append(result)

    errors = _collect_case_errors(results)
    if errors:
        raise ProposeNextIterationError(
            "failed to initialize next campaign case states: " + "; ".join(errors[:10])
        )

    return {
        "ok": True,
        "campaign_root": str(campaign_root),
        "cases_processed": len(results),
        "actions_performed": sum(len(item.get("actions", [])) for item in results),
        "cases_with_errors": 0,
        "errors": 0,
        "destructive_operations": 0,
    }


def update_state_after_next_iteration(
    *,
    state_doc: dict[str, Any],
    plan: NextIterationPlan,
    optimizer_result: ExternalOptimizerResult,
    optimizer_outputs: dict[str, Any],
    preparation_result: dict[str, Any],
    materialization_result: dict[str, Any] | None,
    init_states_result: dict[str, Any] | None,
    next_iteration_summary: dict[str, Any],
) -> dict[str, Any]:
    """Append next iteration to optimization_state.json after full success.

    This function must only be called after:
    - external optimizer command succeeded;
    - required optimizer outputs exist;
    - next campaign root was prepared;
    - optional materialization/init steps completed successfully.

    It deliberately does not submit jobs.
    """
    completed_at = now_utc()

    updated = dict(state_doc)
    iterations: list[dict[str, Any]] = []
    found_from_iteration = False

    for raw_item in state_doc.get("iterations", []):
        if not isinstance(raw_item, dict):
            continue

        item = dict(raw_item)
        try:
            item_iteration = int(item.get("iteration", -1))
        except Exception:
            iterations.append(item)
            continue

        if item_iteration == plan.from_iteration:
            found_from_iteration = True
            previous_status = str(item.get("status", ""))
            if previous_status != "closed":
                item["previous_status_before_next_iteration"] = previous_status
                item["status"] = "closed"
                item["recommended_action"] = "no_action"
                item["closed_at"] = completed_at
                item["closed_reason"] = "next_iteration_prepared"

        iterations.append(item)

    if not found_from_iteration:
        raise ProposeNextIterationError(
            f"cannot update optimization state: from_iteration not found: {plan.from_iteration}"
        )

    if any(
        isinstance(item, dict) and int(item.get("iteration", -1)) == plan.next_iteration
        for item in iterations
    ):
        raise ProposeNextIterationError(
            "cannot update optimization state: next_iteration already exists: "
            f"{plan.next_iteration}"
        )

    next_state = dict(next_iteration_summary)
    next_state.update(
        {
            "iteration": plan.next_iteration,
            "optimizer_run_dir": _relative_posix(
                plan.optimizer_run_dir, plan.optimization_root
            ),
            "campaign_root": _relative_posix(
                plan.output_campaign_root, plan.optimization_root
            ),
            "submitted": False,
            "slurm_job_ids": [],
            "created_by_action": "propose_next_iteration",
            "created_from_iteration": plan.from_iteration,
            "created_at": completed_at,
            "optimizer_command": plan.external_command.to_dict(),
            "external_optimizer_result": optimizer_result.to_dict(),
            "optimizer_outputs": optimizer_outputs,
            "campaign_preparation_result": preparation_result,
            "materialization_result": materialization_result,
            "init_case_states_result": init_states_result,
            "next_submit_allowed": True,
            "recommended_action": next_state.get(
                "recommended_action", "submit_iteration"
            ),
        }
    )

    iterations.append(next_state)
    iterations.sort(key=lambda item: int(item.get("iteration", -1)))

    updated["iterations"] = iterations
    updated["latest_iteration"] = plan.next_iteration
    updated["status"] = str(next_state.get("status", "campaign_materialized"))
    updated["recommended_action"] = str(
        next_state.get("recommended_action", "submit_iteration")
    )
    updated["updated_at"] = completed_at

    return updated


def _relative_posix(path: Path, root: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return str(path)


def _collect_case_errors(results: list[dict[str, Any]]) -> list[str]:
    errors: list[str] = []
    for item in results:
        case_id = item.get("case_id", "?")
        case_name = item.get("case_name", "?")
        for error in item.get("errors", []):
            errors.append(f"case {case_id} {case_name}: {error}")
    return errors


def _verify_required_output_file(path: Path, *, label: str) -> dict[str, Any]:
    if not path.exists():
        raise ProposeNextIterationError(f"missing optimizer output {label}: {path}")
    if not path.is_file():
        raise ProposeNextIterationError(
            f"optimizer output {label} is not a regular file: {path}"
        )

    size_bytes = path.stat().st_size
    if size_bytes <= 0:
        raise ProposeNextIterationError(f"optimizer output {label} is empty: {path}")

    return {
        "path": str(path),
        "size_bytes": size_bytes,
    }


def _tail_text(text: str, *, max_chars: int = 1200) -> str:
    if len(text) <= max_chars:
        return text
    return text[-max_chars:]


def _check_global_policy(
    *,
    state_doc: dict[str, Any],
    policy: dict[str, Any],
    next_iteration: int,
) -> None:
    if policy.get("max_iterations") is not None:
        max_iterations = int(policy["max_iterations"])
        if next_iteration >= max_iterations:
            raise ProposeNextIterationError(
                f"max_iterations reached: next_iteration={next_iteration}, max_iterations={max_iterations}"
            )

    if policy.get("max_total_submitted_cases") is not None:
        limit = int(policy["max_total_submitted_cases"])
        total = sum(
            _positive_int(item.get("submitted_case_count"), default=0)
            for item in state_doc.get("iterations", [])
            if isinstance(item, dict)
        )
        if total >= limit:
            raise ProposeNextIterationError(
                f"max_total_submitted_cases reached: submitted={total}, limit={limit}"
            )


def _reject_forbidden_external_command(command: Sequence[str]) -> None:
    executable = Path(str(command[0])).name.lower() if command else ""
    forbidden = {
        "s" + "batch",
        "s" + "run",
        "mpi" + "exec",
        "mpi" + "run",
        "warp" + "x",
    }
    if executable in forbidden:
        raise ProposeNextIterationError(
            "unsafe optimizer command executable for propose_next_iteration: "
            f"{executable!r}"
        )

    joined = " ".join(str(part).lower() for part in command)
    if ("python " + "input.py") in joined:
        raise ProposeNextIterationError(
            "unsafe optimizer command contains direct case input execution"
        )


def _render_token(text: str, context: dict[str, str]) -> str:
    for key, value in context.items():
        text = text.replace("{" + key + "}", value)
    return text


def _find_iteration_state(
    state_doc: dict[str, Any], iteration: int
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
    tick_summary: dict[str, Any], iteration: int
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


def _resolve_root_path(root: Path, value: Any) -> Path:
    path = Path(str(value)).expanduser()
    if path.is_absolute():
        return path.resolve(strict=False)
    return (root / path).resolve(strict=False)


def _positive_int(*values: Any, default: int) -> int:
    for value in values:
        try:
            parsed = int(value)
        except Exception:
            continue
        if parsed >= 0:
            return parsed
    return int(default)
