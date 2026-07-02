from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence

from campaign_workflow.core.state import now_utc
from campaign_workflow.core.tsv_cases import load_campaign_config, load_cases
from campaign_workflow.slurm_submit_guard import (
    assert_not_inside_slurm_job_for_sbatch,
)

_ARRAY_SPEC_RE = re.compile(
    r"^(?P<body>\d+(?:-\d+)?(?:,\d+(?:-\d+)?)*)(?:%(?P<limit>\d+))?$"
)


class SubmitIterationError(RuntimeError):
    """Raised when a materialized optimization iteration cannot be submitted safely."""


@dataclass(frozen=True)
class SubmitIterationPlan:
    optimization_root: Path
    iteration: int
    campaign_root: Path
    n_cases: int
    array_spec: str
    submitted_case_ids: list[int]
    submit_script: Path
    workflow_root: Path
    workflow_env: Path
    working_directory: Path
    submit_command: list[str]
    submitted_case_count: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "optimization_root": str(self.optimization_root),
            "iteration": self.iteration,
            "campaign_root": str(self.campaign_root),
            "n_cases": self.n_cases,
            "array_spec": self.array_spec,
            "submitted_case_ids": self.submitted_case_ids,
            "submitted_case_count": self.submitted_case_count,
            "submit_script": str(self.submit_script),
            "workflow_root": str(self.workflow_root),
            "workflow_env": str(self.workflow_env),
            "working_directory": str(self.working_directory),
            "submit_command": self.submit_command,
            "submit_log": None,
        }


@dataclass(frozen=True)
class SubmitIterationResult:
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


def build_submit_iteration_plan(
    *,
    tick_summary: dict[str, Any],
    optimization_root: Path,
    iteration: int,
    array_spec: str | None = None,
    max_cases: int | None = None,
    submit_script: Path | None = None,
    workflow_root: Path | None = None,
    workflow_env: Path | None = None,
    job_name: str | None = None,
) -> SubmitIterationPlan:
    optimization_root = optimization_root.resolve()

    if tick_summary.get("pause_file_exists"):
        raise SubmitIterationError("optimization is paused: PAUSE_OPTIMIZATION exists")

    iteration_summary = _find_iteration(tick_summary, iteration)
    if iteration_summary is None:
        raise SubmitIterationError(f"iteration not found: iter_{iteration:03d}")

    errors = iteration_summary.get("errors", [])
    if errors:
        raise SubmitIterationError(f"iteration audit has errors: {errors}")

    if bool(iteration_summary.get("submitted")):
        raise SubmitIterationError(f"iteration already submitted: iter_{iteration:03d}")

    status = str(iteration_summary.get("status", ""))
    if status in {
        "submitted",
        "running",
        "postprocessing",
        "reduced_ready",
        "closed",
        "paused",
    }:
        raise SubmitIterationError(
            f"iteration is not eligible for submit: status={status!r}"
        )

    recommended_action = str(iteration_summary.get("recommended_action", ""))
    if recommended_action != "submit_iteration":
        raise SubmitIterationError(
            f"iteration is not ready for submit: recommended_action={recommended_action!r}"
        )

    n_cases = int(iteration_summary.get("n_cases", 0))
    n_case_dirs = int(iteration_summary.get("n_case_dirs", 0))
    n_case_states = int(iteration_summary.get("n_case_states", 0))
    if n_cases <= 0:
        raise SubmitIterationError("iteration has no cases")
    if n_case_dirs != n_cases:
        raise SubmitIterationError(
            f"case directory count mismatch: n_cases={n_cases}, n_case_dirs={n_case_dirs}"
        )
    if n_case_states != n_cases:
        raise SubmitIterationError(
            f"case state count mismatch: n_cases={n_cases}, n_case_states={n_case_states}"
        )

    campaign_root = optimization_root / str(iteration_summary["campaign_root"])
    campaign_root = campaign_root.resolve()
    if not campaign_root.is_dir():
        raise SubmitIterationError(f"campaign root does not exist: {campaign_root}")

    try:
        campaign_config = load_campaign_config(campaign_root)
        cases = load_cases(campaign_root, campaign_config)
    except Exception as exc:
        raise SubmitIterationError(
            f"failed to load cases for submit planning: {exc}"
        ) from exc

    case_ids = sorted(case.case_id for case in cases)
    if len(case_ids) != n_cases:
        raise SubmitIterationError(
            f"case manifest/audit count mismatch: audit={n_cases}, manifest={len(case_ids)}"
        )

    chosen_array_spec = choose_array_spec(
        case_ids=case_ids, array_spec=array_spec, max_cases=max_cases
    )
    submitted_case_ids = expand_array_spec(chosen_array_spec)
    missing_ids = sorted(set(submitted_case_ids) - set(case_ids))
    if missing_ids:
        raise SubmitIterationError(
            f"array spec references unknown case IDs: {missing_ids}"
        )

    if submit_script is None:
        resolved_workflow_root = (
            workflow_root.resolve()
            if workflow_root is not None
            else default_workflow_root()
        )
        resolved_submit_script = (
            resolved_workflow_root
            / "examples"
            / "sunrise"
            / "submit_case_cycle_array.sh"
        )
    else:
        resolved_submit_script = submit_script.resolve()
        resolved_workflow_root = (
            workflow_root.resolve()
            if workflow_root is not None
            else infer_workflow_root_from_script(resolved_submit_script)
        )

    if workflow_env is None:
        resolved_workflow_env = Path.home() / "apps" / "env" / "campaign-workflow.sh"
    else:
        resolved_workflow_env = workflow_env.expanduser().resolve()

    if not resolved_submit_script.is_file():
        raise SubmitIterationError(
            f"submit script does not exist: {resolved_submit_script}"
        )

    resolved_job_name = job_name or f"cw_iter_{iteration:03d}_cycle"
    command = [
        "sbatch",
        "--parsable",
        f"--array={chosen_array_spec}",
        f"--job-name={resolved_job_name}",
        (
            "--export=ALL,"
            f"CAMPAIGN_ROOT={campaign_root},"
            f"WORKFLOW_ROOT={resolved_workflow_root},"
            f"WORKFLOW_ENV={resolved_workflow_env}"
        ),
        str(resolved_submit_script),
    ]

    return SubmitIterationPlan(
        optimization_root=optimization_root,
        iteration=iteration,
        campaign_root=campaign_root,
        n_cases=n_cases,
        array_spec=chosen_array_spec,
        submitted_case_ids=submitted_case_ids,
        submit_script=resolved_submit_script,
        workflow_root=resolved_workflow_root,
        workflow_env=resolved_workflow_env,
        working_directory=campaign_root,
        submit_command=command,
        submitted_case_count=len(submitted_case_ids),
    )


def execute_submit_iteration(plan: SubmitIterationPlan) -> SubmitIterationResult:
    try:
        assert_not_inside_slurm_job_for_sbatch()
    except RuntimeError as exc:
        raise SubmitIterationError(str(exc)) from exc

    completed = subprocess.run(
        plan.submit_command,
        cwd=str(plan.working_directory),
        text=True,
        capture_output=True,
        check=False,
    )

    if completed.returncode != 0:
        raise SubmitIterationError(
            "sbatch failed with return code "
            f"{completed.returncode}: stdout={completed.stdout!r} stderr={completed.stderr!r}"
        )

    job_id = parse_sbatch_job_id(completed.stdout)
    if not job_id:
        raise SubmitIterationError(
            f"could not parse sbatch job id from stdout: {completed.stdout!r}"
        )

    return SubmitIterationResult(
        job_id=job_id,
        return_code=completed.returncode,
        stdout=completed.stdout,
        stderr=completed.stderr,
        submitted_at=now_utc(),
    )


def update_state_after_submit(
    *,
    state_doc: dict[str, Any],
    plan: SubmitIterationPlan,
    result: SubmitIterationResult,
) -> dict[str, Any]:
    updated = dict(state_doc)
    iterations = []
    found = False

    for raw_item in state_doc.get("iterations", []):
        if not isinstance(raw_item, dict):
            continue
        item = dict(raw_item)
        if int(item.get("iteration", -1)) == plan.iteration:
            found = True
            job_ids = list(item.get("slurm_job_ids", []))
            job_ids.append(result.job_id)
            item.update(
                {
                    "status": "submitted",
                    "submitted": True,
                    "slurm_job_ids": job_ids,
                    "submitted_at": result.submitted_at,
                    "submit_command": plan.submit_command,
                    "array_spec": plan.array_spec,
                    "submitted_case_ids": plan.submitted_case_ids,
                    "submitted_case_count": plan.submitted_case_count,
                    "submit_script": str(plan.submit_script),
                    "working_directory": str(plan.working_directory),
                    "submit_log": None,
                }
            )
        iterations.append(item)

    if not found:
        raise SubmitIterationError(
            f"cannot update state: iteration not found: {plan.iteration}"
        )

    updated["iterations"] = iterations
    updated["status"] = "running"
    updated["latest_iteration"] = plan.iteration
    updated["updated_at"] = result.submitted_at
    return updated


def state_updates_preview(plan: SubmitIterationPlan) -> dict[str, Any]:
    return {
        "iteration": plan.iteration,
        "status": "submitted",
        "submitted": True,
        "slurm_job_ids": ["<sbatch-job-id>"],
        "submitted_at": "<set after successful sbatch>",
        "submit_command": plan.submit_command,
        "array_spec": plan.array_spec,
        "submitted_case_ids": plan.submitted_case_ids,
        "submitted_case_count": plan.submitted_case_count,
        "submit_script": str(plan.submit_script),
        "working_directory": str(plan.working_directory),
        "submit_log": None,
    }


def choose_array_spec(
    *, case_ids: Sequence[int], array_spec: str | None, max_cases: int | None
) -> str:
    if array_spec is not None and max_cases is not None:
        raise SubmitIterationError(
            "--array-spec and --max-cases are mutually exclusive"
        )

    if array_spec is not None:
        validate_array_spec(array_spec)
        return array_spec

    sorted_ids = sorted(case_ids)
    if not sorted_ids:
        raise SubmitIterationError("cannot build array spec for empty case list")

    if max_cases is not None:
        if max_cases <= 0:
            raise SubmitIterationError("--max-cases must be positive")
        if max_cases > len(sorted_ids):
            raise SubmitIterationError(
                f"--max-cases exceeds case count: {max_cases} > {len(sorted_ids)}"
            )
        sorted_ids = sorted_ids[:max_cases]

    return compact_case_ids_as_array_spec(sorted_ids)


def validate_array_spec(array_spec: str) -> None:
    if not array_spec or array_spec.strip() != array_spec:
        raise SubmitIterationError(f"invalid SLURM array spec: {array_spec!r}")

    match = _ARRAY_SPEC_RE.fullmatch(array_spec)
    if match is None:
        raise SubmitIterationError(f"invalid SLURM array spec: {array_spec!r}")

    limit = match.group("limit")
    if limit is not None and int(limit) <= 0:
        raise SubmitIterationError(
            f"invalid SLURM array concurrency limit: {array_spec!r}"
        )

    for part in match.group("body").split(","):
        if "-" in part:
            start_s, end_s = part.split("-", 1)
            start = int(start_s)
            end = int(end_s)
            if end < start:
                raise SubmitIterationError(
                    f"invalid descending SLURM array range: {part!r}"
                )


def expand_array_spec(array_spec: str) -> list[int]:
    validate_array_spec(array_spec)
    body = array_spec.split("%", 1)[0]
    ids: set[int] = set()
    for part in body.split(","):
        if "-" in part:
            start_s, end_s = part.split("-", 1)
            ids.update(range(int(start_s), int(end_s) + 1))
        else:
            ids.add(int(part))
    return sorted(ids)


def compact_case_ids_as_array_spec(case_ids: Sequence[int]) -> str:
    ids = sorted(case_ids)
    if not ids:
        raise SubmitIterationError("cannot compact empty case ID list")
    if ids == list(range(ids[0], ids[-1] + 1)):
        return f"{ids[0]}-{ids[-1]}" if ids[0] != ids[-1] else str(ids[0])
    return ",".join(str(item) for item in ids)


def parse_sbatch_job_id(stdout: str) -> str:
    lines = [line.strip() for line in stdout.splitlines() if line.strip()]
    if not lines:
        return ""
    return lines[-1].split(";", 1)[0].strip()


def default_workflow_root() -> Path:
    return Path(__file__).resolve().parents[1]


def infer_workflow_root_from_script(submit_script: Path) -> Path:
    parts = submit_script.resolve().parts
    if len(parts) >= 3 and parts[-3:] == (
        "examples",
        "sunrise",
        "submit_case_cycle_array.sh",
    ):
        return submit_script.resolve().parents[2]
    return default_workflow_root()


def _find_iteration(
    tick_summary: dict[str, Any], iteration: int
) -> dict[str, Any] | None:
    for item in tick_summary.get("iterations", []):
        if isinstance(item, dict) and int(item.get("iteration", -1)) == iteration:
            return item
    return None
