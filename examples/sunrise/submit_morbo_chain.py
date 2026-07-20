#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

_SCRIPT_PATH = Path(__file__).resolve()
_REPO_ROOT = _SCRIPT_PATH.parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from campaign_workflow.slurm_submit_guard import assert_not_inside_slurm_job_for_sbatch
from campaign_workflow.submit_iteration import parse_sbatch_job_id
from campaign_workflow.optimization_state import (
    read_optimization_state,
    write_optimization_state,
)
from campaign_workflow.presubmitted_chain import (
    update_state_after_presubmitted_array,
)


class MorboChainSubmitError(RuntimeError):
    """Raised when a finite MORBO chain cannot be planned or submitted safely."""


@dataclass(frozen=True)
class ChainArgs:
    optimization_root: Path
    start_iteration: int
    num_additional_iterations: int
    array_spec: str
    workflow_root: Path
    workflow_env: Path
    job_name_prefix: str
    partition: str
    time: str
    nodes: int
    ntasks: int
    mem: str
    tick_partition: str
    tick_time: str
    tick_nodes: int
    tick_ntasks: int
    tick_mem: str
    execute: bool
    optimization_config: Path | None
    array_script: Path
    tick_script: Path
    case_runner: Path | None
    initial_dependency_job_id: str | None

    @property
    def final_iteration(self) -> int:
        return self.start_iteration + self.num_additional_iterations - 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Submit a finite SUNRISE-compatible MORBO chain from login/control. "
            "All sbatch calls happen now; optimizer ticks inside SLURM only materialize."
        )
    )
    parser.add_argument("--optimization-root", type=Path, required=True)
    parser.add_argument("--start-iteration", type=int, required=True)
    parser.add_argument("--num-additional-iterations", type=int, required=True)
    parser.add_argument("--array-spec", required=True)
    parser.add_argument("--workflow-root", type=Path, required=True)
    parser.add_argument("--workflow-env", type=Path, required=True)
    parser.add_argument("--job-name-prefix", default="cw_morbo")
    parser.add_argument("--partition", default="T6H")
    parser.add_argument("--time", default="06:00:00")
    parser.add_argument("--nodes", type=int, default=1)
    parser.add_argument("--ntasks", type=int, default=24)
    parser.add_argument("--mem", default="64G")
    parser.add_argument("--tick-partition", default=None)
    parser.add_argument("--tick-time", default=None)
    parser.add_argument("--tick-nodes", type=int, default=None)
    parser.add_argument("--tick-ntasks", type=int, default=None)
    parser.add_argument("--tick-mem", default=None)
    parser.add_argument("--optimization-config", type=Path, default=None)
    parser.add_argument(
        "--array-script",
        type=Path,
        default=None,
        help=(
            "Override static iteration-array sbatch script. Defaults to "
            "examples/sunrise/run_iteration_array.sh under workflow root."
        ),
    )
    parser.add_argument(
        "--tick-script",
        type=Path,
        default=None,
        help=(
            "Override static optimizer tick sbatch script. Defaults to "
            "examples/sunrise/run_optimizer_tick_materialize_only.sh under workflow root."
        ),
    )
    parser.add_argument(
        "--case-runner",
        type=Path,
        default=None,
        help=(
            "Case-local simulation runner exported to every iteration array. "
            "When omitted, the case-cycle default is used."
        ),
    )
    parser.add_argument(
        "--initial-dependency-job-id",
        default=None,
        help=(
            "Optional existing SLURM job ID. The first iteration array is "
            "submitted with afterok:<job-id>; later dependencies remain internal "
            "to this finite chain."
        ),
    )
    parser.add_argument(
        "--execute",
        action="store_true",
        help="Actually call sbatch. Without this flag, only print the planned chain.",
    )
    return parser


def resolve_args(raw: argparse.Namespace) -> ChainArgs:
    optimization_root = raw.optimization_root.expanduser().resolve(strict=False)
    workflow_root = raw.workflow_root.expanduser().resolve(strict=False)
    workflow_env = raw.workflow_env.expanduser().resolve(strict=False)
    optimization_config = (
        raw.optimization_config.expanduser().resolve(strict=False)
        if raw.optimization_config is not None
        else None
    )
    array_script = (
        raw.array_script.expanduser().resolve(strict=False)
        if raw.array_script is not None
        else workflow_root / "examples" / "sunrise" / "run_iteration_array.sh"
    )
    tick_script = (
        raw.tick_script.expanduser().resolve(strict=False)
        if raw.tick_script is not None
        else workflow_root
        / "examples"
        / "sunrise"
        / "run_optimizer_tick_materialize_only.sh"
    )
    case_runner = (
        raw.case_runner.expanduser().resolve(strict=False)
        if raw.case_runner is not None
        else None
    )

    args = ChainArgs(
        optimization_root=optimization_root,
        start_iteration=int(raw.start_iteration),
        num_additional_iterations=int(raw.num_additional_iterations),
        array_spec=str(raw.array_spec),
        workflow_root=workflow_root,
        workflow_env=workflow_env,
        job_name_prefix=str(raw.job_name_prefix),
        partition=str(raw.partition),
        time=str(raw.time),
        nodes=int(raw.nodes),
        ntasks=int(raw.ntasks),
        mem=str(raw.mem),
        tick_partition=str(raw.tick_partition or raw.partition),
        tick_time=str(raw.tick_time or raw.time),
        tick_nodes=int(raw.tick_nodes or raw.nodes),
        tick_ntasks=int(raw.tick_ntasks or raw.ntasks),
        tick_mem=str(raw.tick_mem or raw.mem),
        execute=bool(raw.execute),
        optimization_config=optimization_config,
        array_script=array_script,
        tick_script=tick_script,
        case_runner=case_runner,
        initial_dependency_job_id=(
            str(raw.initial_dependency_job_id).strip()
            if raw.initial_dependency_job_id is not None
            else None
        ),
    )
    validate_chain_args(args)
    return args


def validate_chain_args(args: ChainArgs) -> None:
    assert_not_inside_slurm_job_for_sbatch()

    if args.start_iteration < 0:
        raise MorboChainSubmitError("--start-iteration must be >= 0")
    if args.num_additional_iterations < 1:
        raise MorboChainSubmitError("--num-additional-iterations must be >= 1")
    if args.nodes < 1:
        raise MorboChainSubmitError("--nodes must be >= 1")
    if args.ntasks < 1:
        raise MorboChainSubmitError("--ntasks must be >= 1")
    if args.tick_nodes < 1:
        raise MorboChainSubmitError("--tick-nodes must be >= 1")
    if args.tick_ntasks < 1:
        raise MorboChainSubmitError("--tick-ntasks must be >= 1")
    if not args.optimization_root.is_dir():
        raise MorboChainSubmitError(
            f"optimization root does not exist: {args.optimization_root}"
        )
    if not (args.optimization_root / "optimization.json").is_file():
        raise MorboChainSubmitError(
            f"missing optimization.json under optimization root: {args.optimization_root}"
        )
    if not args.workflow_root.is_dir():
        raise MorboChainSubmitError(
            f"workflow root does not exist: {args.workflow_root}"
        )
    if not args.workflow_env.is_file():
        raise MorboChainSubmitError(f"workflow env does not exist: {args.workflow_env}")
    if not args.array_script.is_file():
        raise MorboChainSubmitError(
            f"array sbatch script does not exist: {args.array_script}"
        )
    if not args.tick_script.is_file():
        raise MorboChainSubmitError(
            f"tick sbatch script does not exist: {args.tick_script}"
        )
    if args.case_runner is not None and not args.case_runner.is_file():
        raise MorboChainSubmitError(
            f"case runner does not exist: {args.case_runner}"
        )
    if args.optimization_config is not None and not args.optimization_config.is_file():
        raise MorboChainSubmitError(
            f"optimization config does not exist: {args.optimization_config}"
        )
    if args.initial_dependency_job_id is not None and re.fullmatch(
        r"[1-9][0-9]*", args.initial_dependency_job_id
    ) is None:
        raise MorboChainSubmitError(
            "--initial-dependency-job-id must be a positive numeric SLURM job ID"
        )


def build_symbolic_chain(args: ChainArgs) -> list[dict[str, Any]]:
    jobs: list[dict[str, Any]] = []
    for iteration in range(args.start_iteration, args.final_iteration + 1):
        next_iteration = iteration + 1
        array_symbol = f"A_{iteration:03d}"
        tick_symbol = f"T_{iteration:03d}"
        previous_tick_symbol = f"T_{iteration - 1:03d}"
        if iteration == args.start_iteration:
            array_dependency = (
                f"afterok:{args.initial_dependency_job_id}"
                if args.initial_dependency_job_id is not None
                else None
            )
        else:
            array_dependency = f"afterok:{previous_tick_symbol}"
        jobs.append(
            {
                "kind": "array",
                "symbol": array_symbol,
                "iteration": iteration,
                "dependency": array_dependency,
                "job_id": None,
                "submit_command": build_array_sbatch_command(
                    args=args,
                    iteration=iteration,
                    dependency=array_dependency,
                ),
            }
        )
        jobs.append(
            {
                "kind": "tick",
                "symbol": tick_symbol,
                "iteration": iteration,
                "next_iteration": next_iteration,
                "dependency": f"afterok:{array_symbol}",
                "job_id": None,
                "submit_command": build_tick_sbatch_command(
                    args=args,
                    iteration=iteration,
                    next_iteration=next_iteration,
                    dependency=f"afterok:{array_symbol}",
                ),
            }
        )
    return jobs


def build_array_sbatch_command(
    *, args: ChainArgs, iteration: int, dependency: str | None
) -> list[str]:
    job_name = f"{args.job_name_prefix}_A{iteration:03d}"
    loop_logs = args.optimization_root / "loop_logs"
    command = common_sbatch_prefix(args=args, job_name=job_name, job_kind="array")
    command.extend(
        [
            f"--array={args.array_spec}",
            f"--output={loop_logs / (job_name + '_%A_%a.out')}",
            f"--error={loop_logs / (job_name + '_%A_%a.err')}",
        ]
    )
    if dependency:
        command.append(f"--dependency={dependency}")
    export_values: dict[str, object] = {
        "CW_OPTIMIZATION_ROOT": args.optimization_root,
        "CW_ITERATION": iteration,
        "CW_WORKFLOW_ROOT": args.workflow_root,
        "CW_WORKFLOW_ENV": args.workflow_env,
        "CW_JOB_NAME_PREFIX": args.job_name_prefix,
        "CONFIRM_CLEANUP_EXECUTE": "1",
    }
    if args.case_runner is not None:
        export_values["CW_CASE_RUNNER"] = args.case_runner

    command.extend(
        [
            export_arg(export_values),
            str(args.array_script),
        ]
    )
    return command


def build_tick_sbatch_command(
    *, args: ChainArgs, iteration: int, next_iteration: int, dependency: str
) -> list[str]:
    job_name = f"{args.job_name_prefix}_T{iteration:03d}"
    loop_logs = args.optimization_root / "loop_logs"
    export_values: dict[str, object] = {
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

    command = common_sbatch_prefix(args=args, job_name=job_name, job_kind="tick")
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


def common_sbatch_prefix(
    *, args: ChainArgs, job_name: str, job_kind: str = "array"
) -> list[str]:
    if job_kind == "array":
        partition = args.partition
        time = args.time
        nodes = args.nodes
        ntasks = args.ntasks
        mem = args.mem
    elif job_kind == "tick":
        partition = args.tick_partition
        time = args.tick_time
        nodes = args.tick_nodes
        ntasks = args.tick_ntasks
        mem = args.tick_mem
    else:
        raise ValueError(f"unsupported sbatch job kind: {job_kind!r}")

    return [
        "sbatch",
        "--parsable",
        f"--partition={partition}",
        f"--time={time}",
        f"--nodes={nodes}",
        f"--ntasks={ntasks}",
        f"--mem={mem}",
        f"--job-name={job_name}",
    ]


def export_arg(values: dict[str, object]) -> str:
    encoded = ",".join(f"{key}={value}" for key, value in values.items())
    return f"--export=ALL,{encoded}"


def submit_finite_chain(args: ChainArgs) -> dict[str, Any]:
    args.optimization_root.joinpath("loop_logs").mkdir(parents=True, exist_ok=True)
    jobs: list[dict[str, Any]] = []
    previous_tick_job_id: str | None = args.initial_dependency_job_id

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
    args: ChainArgs,
    jobs: list[dict[str, Any]],
    iteration: int,
    array_job: dict[str, Any],
) -> None:
    """Record array submit metadata for already-materialized iterations.

    Finite chains pre-submit arrays for future iterations before those iterations
    exist. Those future iterations are registered by optimizer ticks immediately
    after materialization. For the initial iteration, however, the state already
    exists and must be updated now; otherwise the first tick sees zero submitted
    reduced-valid cases.
    """

    state_info = read_optimization_state(args.optimization_root)
    state_doc = state_info.data
    if state_doc is None:
        return

    if not any(
        isinstance(item, dict) and int(item.get("iteration", -1)) == int(iteration)
        for item in state_doc.get("iterations", [])
    ):
        return

    partial_manifest = build_manifest(args=args, jobs=jobs, dry_run=False)

    state_after_submit = update_state_after_presubmitted_array(
        state_doc=state_doc,
        optimization_root=args.optimization_root,
        iteration=iteration,
        manifest_path=args.optimization_root
        / "loop_logs"
        / "<pending-morbo-chain-manifest>",
        manifest=partial_manifest,
        array_job=array_job,
    )

    write_optimization_state(args.optimization_root, state_after_submit)


def execute_sbatch(command: Sequence[str], *, cwd: Path) -> str:
    completed = subprocess.run(
        list(command),
        cwd=str(cwd),
        text=True,
        capture_output=True,
        check=False,
    )
    if completed.returncode != 0:
        raise MorboChainSubmitError(
            "sbatch failed with return code "
            f"{completed.returncode}: stdout={completed.stdout!r} stderr={completed.stderr!r}"
        )
    job_id = parse_sbatch_job_id(completed.stdout)
    if not job_id:
        raise MorboChainSubmitError(
            f"could not parse sbatch job id from stdout: {completed.stdout!r}"
        )
    return job_id


def build_manifest(
    *, args: ChainArgs, jobs: list[dict[str, Any]], dry_run: bool
) -> dict[str, Any]:
    return {
        "schema_version": 1,
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
        "time": args.time,
        "nodes": args.nodes,
        "ntasks": args.ntasks,
        "mem": args.mem,
        "tick_resources": {
            "partition": args.tick_partition,
            "time": args.tick_time,
            "nodes": args.tick_nodes,
            "ntasks": args.tick_ntasks,
            "mem": args.tick_mem,
        },
        "array_script": str(args.array_script),
        "tick_script": str(args.tick_script),
        "case_runner": (
            str(args.case_runner) if args.case_runner is not None else None
        ),
        "initial_dependency_job_id": args.initial_dependency_job_id,
        "optimization_config": (
            str(args.optimization_config)
            if args.optimization_config is not None
            else None
        ),
        "chain_text": chain_text(args),
        "jobs": jobs,
    }


def chain_text(args: ChainArgs) -> str:
    parts: list[str] = []
    if args.initial_dependency_job_id is not None:
        parts.append(f"J_{args.initial_dependency_job_id}")
    for iteration in range(args.start_iteration, args.final_iteration + 1):
        parts.append(f"A_{iteration}")
        parts.append(f"T_{iteration}")
    return " -> ".join(parts)


def write_manifest(optimization_root: Path, manifest: dict[str, Any]) -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    path = optimization_root / "loop_logs" / f"morbo_chain_{stamp}.json"
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
    except (MorboChainSubmitError, RuntimeError) as exc:
        print(f"[MORBO-CHAIN] ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
