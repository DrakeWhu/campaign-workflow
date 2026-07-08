#!/usr/bin/env python3
from __future__ import annotations

import argparse
from html import parser
import json
import re
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

from campaign_workflow.slurm_submit_guard import assert_not_inside_slurm_job_for_sbatch
from campaign_workflow.submit_iteration import parse_sbatch_job_id


class LynxMorboChainSubmitError(RuntimeError):
    pass


@dataclass(frozen=True)
class LynxChainArgs:
    optimization_root: Path
    start_iteration: int
    num_additional_iterations: int
    array_spec: str
    workflow_root: Path
    workflow_env: Path
    job_name_prefix: str
    partition: str
    expected_slurm_partition: str
    time: str
    nodes: int
    ntasks: int
    mem: str
    execute: bool
    optimization_config: Path | None
    array_script: Path
    tick_script: Path
    warpx_lynx_module: str

    @property
    def final_iteration(self) -> int:
        return self.start_iteration + self.num_additional_iterations - 1


def default_workflow_root() -> Path:
    return Path(__file__).resolve().parents[2]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Submit a finite Lynx-compatible MORBO chain from login/control. "
            "All sbatch calls happen now; optimizer ticks inside SLURM only materialize."
        )
    )
    parser.add_argument("--optimization-root", type=Path, required=True)
    parser.add_argument("--start-iteration", type=int, required=True)
    parser.add_argument("--num-additional-iterations", type=int, required=True)
    parser.add_argument("--array-spec", required=True)
    parser.add_argument("--workflow-root", type=Path, default=None)
    parser.add_argument(
        "--workflow-env",
        type=Path,
        default=None,
        help=(
            "Workflow environment script. Defaults to "
            "~/apps/env/campaign_workflow_lynx.sh when omitted."
        ),
    )
    parser.add_argument("--job-name-prefix", default="cw_lynx_morbo")
    parser.add_argument("--partition", default="novas")
    parser.add_argument("--expected-slurm-partition", default="novas")
    parser.add_argument("--time", default="06:00:00")
    parser.add_argument("--nodes", type=int, default=1)
    parser.add_argument("--ntasks", type=int, default=24)
    parser.add_argument("--mem", default="64G")
    parser.add_argument("--optimization-config", type=Path, default=None)
    parser.add_argument(
        "--warpx-lynx-module",
        required=True,
        help=(
            "Required Lynx WarpX module basename, for example "
            "26.03_lynx_cpu_rz_yee_openpmd_py311."
        ),
    )
    parser.add_argument(
        "--array-script",
        type=Path,
        default=None,
        help=(
            "Override static Lynx iteration-array sbatch script. Defaults to "
            "examples/lynx/run_iteration_array_lynx.sh under workflow root."
        ),
    )
    parser.add_argument(
        "--tick-script",
        type=Path,
        default=None,
        help=(
            "Override static Lynx optimizer tick sbatch script. Defaults to "
            "examples/lynx/run_optimizer_tick_materialize_only_lynx.sh under workflow root."
        ),
    )
    parser.add_argument(
        "--execute",
        action="store_true",
        help="Actually call sbatch. Without this flag, only print the planned chain.",
    )
    return parser


def resolve_args(raw: argparse.Namespace) -> LynxChainArgs:
    optimization_root = raw.optimization_root.expanduser().resolve(strict=False)
    workflow_root = (
        raw.workflow_root.expanduser().resolve(strict=False)
        if raw.workflow_root is not None
        else default_workflow_root().resolve(strict=False)
    )
    workflow_env = (
        raw.workflow_env.expanduser().resolve(strict=False)
        if raw.workflow_env is not None
        else Path("~/apps/env/campaign_workflow_lynx.sh")
        .expanduser()
        .resolve(strict=False)
    )
    optimization_config = (
        raw.optimization_config.expanduser().resolve(strict=False)
        if raw.optimization_config is not None
        else None
    )
    array_script = (
        raw.array_script.expanduser().resolve(strict=False)
        if raw.array_script is not None
        else workflow_root / "examples" / "lynx" / "run_iteration_array_lynx.sh"
    )
    tick_script = (
        raw.tick_script.expanduser().resolve(strict=False)
        if raw.tick_script is not None
        else workflow_root
        / "examples"
        / "lynx"
        / "run_optimizer_tick_materialize_only_lynx.sh"
    )

    args = LynxChainArgs(
        optimization_root=optimization_root,
        start_iteration=int(raw.start_iteration),
        num_additional_iterations=int(raw.num_additional_iterations),
        array_spec=str(raw.array_spec),
        workflow_root=workflow_root,
        workflow_env=workflow_env,
        job_name_prefix=str(raw.job_name_prefix),
        partition=str(raw.partition),
        expected_slurm_partition=str(raw.expected_slurm_partition),
        time=str(raw.time),
        nodes=int(raw.nodes),
        ntasks=int(raw.ntasks),
        mem=str(raw.mem),
        execute=bool(raw.execute),
        optimization_config=optimization_config,
        array_script=array_script,
        tick_script=tick_script,
        warpx_lynx_module=str(raw.warpx_lynx_module),
    )
    validate_chain_args(args)
    return args


def validate_chain_args(args: LynxChainArgs) -> None:
    assert_not_inside_slurm_job_for_sbatch()

    if args.start_iteration < 0:
        raise LynxMorboChainSubmitError("--start-iteration must be >= 0")

    if args.num_additional_iterations <= 0:
        raise LynxMorboChainSubmitError("--num-additional-iterations must be > 0")

    if not args.array_spec.strip():
        raise LynxMorboChainSubmitError("--array-spec must not be empty")

    if args.partition != args.expected_slurm_partition:
        raise LynxMorboChainSubmitError(
            "Lynx MORBO chain refuses partition mismatch: "
            f"--partition={args.partition!r}, "
            f"--expected-slurm-partition={args.expected_slurm_partition!r}"
        )

    if args.expected_slurm_partition != "novas":
        raise LynxMorboChainSubmitError(
            "Lynx production partition must be 'novas'. "
            f"Got {args.expected_slurm_partition!r}."
        )

    if not re.fullmatch(r"[A-Za-z0-9._+-]+", args.warpx_lynx_module):
        raise LynxMorboChainSubmitError(
            "--warpx-lynx-module must be a module basename without spaces or slashes"
        )

    if not args.optimization_root.is_dir():
        raise LynxMorboChainSubmitError(
            f"optimization root does not exist: {args.optimization_root}"
        )

    if not (args.optimization_root / "optimization.json").is_file():
        raise LynxMorboChainSubmitError(
            f"missing optimization.json in {args.optimization_root}"
        )

    if not args.workflow_root.is_dir():
        raise LynxMorboChainSubmitError(
            f"workflow root does not exist: {args.workflow_root}"
        )

    if not args.workflow_env.is_file():
        raise LynxMorboChainSubmitError(
            f"workflow env does not exist: {args.workflow_env}"
        )

    if not args.array_script.is_file():
        raise LynxMorboChainSubmitError(
            f"array sbatch script does not exist: {args.array_script}"
        )

    if not args.tick_script.is_file():
        raise LynxMorboChainSubmitError(
            f"tick sbatch script does not exist: {args.tick_script}"
        )


def build_symbolic_chain(args: LynxChainArgs) -> list[dict[str, Any]]:
    jobs: list[dict[str, Any]] = []

    for iteration in range(args.start_iteration, args.final_iteration + 1):
        next_iteration = iteration + 1
        array_symbol = f"A_{iteration:03d}"
        tick_symbol = f"T_{iteration:03d}"
        previous_tick_symbol = (
            None if iteration == args.start_iteration else f"T_{iteration - 1:03d}"
        )

        jobs.append(
            {
                "kind": "array",
                "iteration": iteration,
                "dependency": (
                    None
                    if previous_tick_symbol is None
                    else f"afterok:{previous_tick_symbol}"
                ),
                "job_id": array_symbol,
                "submit_command": build_array_sbatch_command(
                    args=args,
                    iteration=iteration,
                    dependency=(
                        None
                        if previous_tick_symbol is None
                        else f"afterok:{previous_tick_symbol}"
                    ),
                ),
            }
        )
        jobs.append(
            {
                "kind": "tick",
                "iteration": iteration,
                "next_iteration": next_iteration,
                "dependency": f"afterok:{array_symbol}",
                "job_id": tick_symbol,
                "submit_command": build_tick_sbatch_command(
                    args=args,
                    iteration=iteration,
                    next_iteration=next_iteration,
                    dependency=f"afterok:{array_symbol}",
                ),
            }
        )

    return jobs


def common_export_values(args: LynxChainArgs) -> dict[str, object]:
    return {
        "EXPECTED_SLURM_PARTITION": args.expected_slurm_partition,
        "WARPX_LYNX_MODULE": args.warpx_lynx_module,
    }


def build_array_sbatch_command(
    *, args: LynxChainArgs, iteration: int, dependency: str | None
) -> list[str]:
    job_name = f"{args.job_name_prefix}_A{iteration:03d}"
    loop_logs = args.optimization_root / "loop_logs"

    export_values: dict[str, object] = {
        **common_export_values(args),
        "CW_OPTIMIZATION_ROOT": args.optimization_root,
        "CW_ITERATION": iteration,
        "CW_WORKFLOW_ROOT": args.workflow_root,
        "CW_WORKFLOW_ENV": args.workflow_env,
        "CW_JOB_NAME_PREFIX": args.job_name_prefix,
        "CONFIRM_CLEANUP_EXECUTE": "1",
    }

    command = common_sbatch_prefix(args=args, job_name=job_name)
    command.extend(
        [
            f"--array={args.array_spec}",
            f"--output={loop_logs / (job_name + '_%A_%a.out')}",
            f"--error={loop_logs / (job_name + '_%A_%a.err')}",
        ]
    )
    if dependency:
        command.append(f"--dependency={dependency}")

    command.extend(
        [
            export_arg(export_values),
            str(args.array_script),
        ]
    )
    return command


def build_tick_sbatch_command(
    *, args: LynxChainArgs, iteration: int, next_iteration: int, dependency: str
) -> list[str]:
    job_name = f"{args.job_name_prefix}_T{iteration:03d}"
    loop_logs = args.optimization_root / "loop_logs"

    export_values: dict[str, object] = {
        **common_export_values(args),
        "CW_OPTIMIZATION_ROOT": args.optimization_root,
        "CW_ITERATION": iteration,
        "CW_NEXT_ITERATION": next_iteration,
        "CW_ARRAY_SPEC": args.array_spec,
        "CW_WORKFLOW_ROOT": args.workflow_root,
        "CW_WORKFLOW_ENV": args.workflow_env,
        "CW_JOB_NAME_PREFIX": args.job_name_prefix,
    }
    if args.optimization_config is not None:
        export_values["CW_OPTIMIZATION_CONFIG"] = args.optimization_config

    command = common_sbatch_prefix(args=args, job_name=job_name)
    command.extend(
        [
            f"--dependency={dependency}",
            f"--output={loop_logs / (job_name + '_%j.out')}",
            f"--error={loop_logs / (job_name + '_%j.err')}",
            export_arg(export_values),
            str(args.tick_script),
        ]
    )
    return command


def common_sbatch_prefix(*, args: LynxChainArgs, job_name: str) -> list[str]:
    return [
        "sbatch",
        "--parsable",
        f"--partition={args.partition}",
        f"--time={args.time}",
        f"--nodes={args.nodes}",
        f"--ntasks={args.ntasks}",
        f"--mem={args.mem}",
        f"--job-name={job_name}",
    ]


def export_arg(values: dict[str, object]) -> str:
    encoded = ",".join(f"{key}={value}" for key, value in values.items())
    return f"--export=ALL,{encoded}"


def submit_finite_chain(args: LynxChainArgs) -> dict[str, Any]:
    args.optimization_root.joinpath("loop_logs").mkdir(parents=True, exist_ok=True)
    jobs: list[dict[str, Any]] = []
    previous_tick_job_id: str | None = None

    for iteration in range(args.start_iteration, args.final_iteration + 1):
        next_iteration = iteration + 1
        array_dependency = (
            None if previous_tick_job_id is None else f"afterok:{previous_tick_job_id}"
        )

        array_command = build_array_sbatch_command(
            args=args,
            iteration=iteration,
            dependency=array_dependency,
        )
        array_job_id = execute_sbatch(array_command, cwd=args.optimization_root)
        array_job = {
            "kind": "array",
            "iteration": iteration,
            "dependency": array_dependency,
            "job_id": array_job_id,
            "submit_command": array_command,
        }
        jobs.append(array_job)

        register_existing_iteration_array_submit(
            args=args,
            jobs=jobs,
            iteration=iteration,
            array_job=array_job,
        )

        tick_dependency = f"afterok:{array_job_id}"
        tick_command = build_tick_sbatch_command(
            args=args,
            iteration=iteration,
            next_iteration=next_iteration,
            dependency=tick_dependency,
        )
        tick_job_id = execute_sbatch(tick_command, cwd=args.optimization_root)
        jobs.append(
            {
                "kind": "tick",
                "iteration": iteration,
                "next_iteration": next_iteration,
                "dependency": tick_dependency,
                "job_id": tick_job_id,
                "submit_command": tick_command,
            }
        )
        previous_tick_job_id = tick_job_id

    manifest = build_manifest(args=args, jobs=jobs, dry_run=False)
    manifest_path = write_manifest(args.optimization_root, manifest)
    manifest["manifest_path"] = str(manifest_path)
    return manifest


def register_existing_iteration_array_submit(
    *,
    args: LynxChainArgs,
    jobs: list[dict[str, Any]],
    iteration: int,
    array_job: dict[str, Any],
) -> None:
    """Record submit metadata when the starting iteration already exists.

    Future iterations are registered by optimizer ticks after materialization.
    The starting iteration may already be present in optimization_state.json and
    must be marked submitted immediately.
    """

    state_path = args.optimization_root / "optimization_state.json"
    if not state_path.is_file():
        return

    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))
    except Exception:
        return

    found = False
    submitted_case_ids = parse_array_spec(args.array_spec)
    job_id = str(array_job["job_id"])

    for item in state.get("iterations", []):
        if int(item.get("iteration", -1)) != int(iteration):
            continue

        found = True
        job_ids = [str(x) for x in item.get("slurm_job_ids", []) if str(x)]
        if job_id not in job_ids:
            job_ids.append(job_id)

        item.update(
            {
                "status": "submitted",
                "recommended_action": "wait_for_jobs",
                "submitted": True,
                "slurm_job_ids": job_ids,
                "array_spec": args.array_spec,
                "submitted_case_ids": submitted_case_ids,
                "submitted_case_count": len(submitted_case_ids),
                "submit_command": array_job.get("submit_command"),
                "submit_script": str(args.array_script),
                "working_directory": str(args.optimization_root),
                "submit_log": None,
                "submitted_at": now_utc(),
            }
        )

    if not found:
        return

    state["status"] = "running"
    state["latest_iteration"] = max(
        int(state.get("latest_iteration", iteration)),
        int(iteration),
    )
    state["updated_at"] = now_utc()

    tmp = state_path.with_suffix(state_path.suffix + ".tmp")
    tmp.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    tmp.replace(state_path)


def parse_array_spec(array_spec: str) -> list[int]:
    """Parse simple SLURM array specs such as 0-29, 0-29%5, 0,2,4-6."""

    case_ids: list[int] = []

    for raw_part in array_spec.split(","):
        part = raw_part.strip()
        if not part:
            continue

        part = part.split("%", 1)[0]

        if "-" in part:
            left, right = part.split("-", 1)
            start = int(left)
            end = int(right)
            if end < start:
                raise LynxMorboChainSubmitError(
                    f"invalid descending array range: {part}"
                )
            case_ids.extend(range(start, end + 1))
        else:
            case_ids.append(int(part))

    return sorted(set(case_ids))


def execute_sbatch(command: Sequence[str], *, cwd: Path) -> str:
    completed = subprocess.run(
        list(command),
        cwd=str(cwd),
        text=True,
        capture_output=True,
        check=False,
    )
    if completed.returncode != 0:
        raise LynxMorboChainSubmitError(
            "sbatch failed with return code "
            f"{completed.returncode}: stdout={completed.stdout!r} stderr={completed.stderr!r}"
        )

    job_id = parse_sbatch_job_id(completed.stdout)
    if not job_id:
        raise LynxMorboChainSubmitError(
            f"could not parse sbatch job id from stdout: {completed.stdout!r}"
        )

    return job_id


def build_manifest(
    *, args: LynxChainArgs, jobs: list[dict[str, Any]], dry_run: bool
) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "platform": "lynx",
        "created_at": now_utc(),
        "dry_run": bool(dry_run),
        "optimization_root": str(args.optimization_root),
        "start_iteration": args.start_iteration,
        "final_iteration": args.final_iteration,
        "num_additional_iterations": args.num_additional_iterations,
        "array_spec": args.array_spec,
        "workflow_root": str(args.workflow_root),
        "workflow_env": str(args.workflow_env),
        "job_name_prefix": args.job_name_prefix,
        "partition": args.partition,
        "expected_slurm_partition": args.expected_slurm_partition,
        "warpx_lynx_module": args.warpx_lynx_module,
        "time": args.time,
        "nodes": args.nodes,
        "ntasks": args.ntasks,
        "mem": args.mem,
        "array_script": str(args.array_script),
        "tick_script": str(args.tick_script),
        "optimization_config": (
            str(args.optimization_config)
            if args.optimization_config is not None
            else None
        ),
        "chain_text": chain_text(args),
        "jobs": jobs,
    }


def chain_text(args: LynxChainArgs) -> str:
    parts: list[str] = []
    for iteration in range(args.start_iteration, args.final_iteration + 1):
        parts.append(f"A_{iteration}")
        parts.append(f"T_{iteration}")
    return " -> ".join(parts)


def write_manifest(optimization_root: Path, manifest: dict[str, Any]) -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    path = optimization_root / "loop_logs" / f"lynx_morbo_chain_{stamp}.json"
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")
    tmp.replace(path)
    return path


def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    raw = parser.parse_args(argv)
    args = resolve_args(raw)

    if args.execute:
        manifest = submit_finite_chain(args)
    else:
        jobs = build_symbolic_chain(args)
        manifest = build_manifest(args=args, jobs=jobs, dry_run=True)

    print(json.dumps(manifest, indent=2, sort_keys=True))
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    try:
        return _main(argv)
    except SystemExit as exc:
        if isinstance(exc.code, int):
            return exc.code
        return 2
    except (LynxMorboChainSubmitError, RuntimeError) as exc:
        print(f"[LYNX-MORBO-CHAIN] ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
