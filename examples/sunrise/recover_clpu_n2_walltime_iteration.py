#!/usr/bin/env python3
"""Retry one partially timed-out CLPU-N2 iteration without rebuilding its chain."""
from __future__ import annotations

import argparse
import importlib.util
import json
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from types import ModuleType
from typing import Any, Sequence

_SCRIPT = Path(__file__).resolve()
_REPO = _SCRIPT.parents[2]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from campaign_workflow.cli.mark_sim_failed import main as mark_sim_failed
from campaign_workflow.cli.mark_sim_retryable import main as mark_sim_retryable
from campaign_workflow.core.atomic_io import read_json, write_json_atomic
from campaign_workflow.core.transitions import state_name
from campaign_workflow.core.tsv_cases import load_campaign_config, load_cases
from campaign_workflow.optimization_state import (
    read_optimization_state,
    write_optimization_state,
)
from campaign_workflow.submit_iteration import compact_case_ids_as_array_spec


class RecoveryError(RuntimeError):
    pass


def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def run(command: Sequence[str], *, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    completed = subprocess.run(
        list(command), cwd=None if cwd is None else str(cwd), text=True,
        capture_output=True, check=False,
    )
    if completed.returncode != 0:
        raise RecoveryError(
            f"command failed ({completed.returncode}): {list(command)!r}\n"
            f"stdout={completed.stdout!r}\nstderr={completed.stderr!r}"
        )
    return completed


def load_script(path: Path, name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RecoveryError(f"could not import {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description=(
            "Recover a CLPU-N2 iteration whose array partly timed out: preserve "
            "completed cases, quarantine partial raw output, submit only timeout "
            "cases on hold, rewire the existing tick, update future arrays, release."
        )
    )
    p.add_argument("--optimization-root", type=Path, required=True)
    p.add_argument("--iteration", type=int, required=True)
    p.add_argument("--failed-array-job-id", required=True)
    p.add_argument("--blocked-tick-job-id", required=True)
    p.add_argument("--chain-manifest", type=Path, required=True)
    p.add_argument("--retry-array-spec", required=True)
    p.add_argument("--completed-case-id", type=int, action="append", required=True)
    p.add_argument("--workflow-root", type=Path, required=True)
    p.add_argument("--workflow-env", type=Path, required=True)
    p.add_argument("--case-runner", type=Path, required=True)
    p.add_argument("--job-name-prefix", default="clpu_n2_retry")
    p.add_argument("--partition", default="T12H")
    p.add_argument("--time", default="12:00:00")
    p.add_argument("--nodes", type=int, default=1)
    p.add_argument("--ntasks", type=int, default=24)
    p.add_argument("--execute", action="store_true")
    return p


def expand_array_spec(raw: str) -> list[int]:
    body = raw.split("%", 1)[0]
    result: set[int] = set()
    for item in body.split(","):
        if "-" in item:
            lo, hi = (int(x) for x in item.split("-", 1))
            if lo > hi:
                raise RecoveryError(f"invalid array range: {item}")
            result.update(range(lo, hi + 1))
        else:
            result.add(int(item))
    return sorted(result)


def manifest_jobs(manifest: dict[str, Any]) -> list[dict[str, Any]]:
    jobs = manifest.get("jobs")
    if not isinstance(jobs, list) or not jobs:
        raise RecoveryError("chain manifest has no jobs")
    if not all(isinstance(item, dict) for item in jobs):
        raise RecoveryError("chain manifest contains invalid jobs")
    return jobs


def one_job(
    jobs: list[dict[str, Any]], *, kind: str, iteration: int, job_id: str
) -> dict[str, Any]:
    matches = [
        item for item in jobs
        if item.get("kind") == kind
        and int(item.get("iteration", -1)) == iteration
        and str(item.get("job_id", "")) == job_id
    ]
    if len(matches) != 1:
        raise RecoveryError(
            f"expected exactly one {kind} iter_{iteration:03d} job_id={job_id}; "
            f"found {len(matches)}"
        )
    return matches[0]


def job_fields(job_id: str) -> dict[str, str]:
    text = run(["scontrol", "show", "job", "-o", job_id]).stdout.strip()
    if not text:
        raise RecoveryError(f"scontrol returned no job data for {job_id}")
    fields: dict[str, str] = {}
    for token in text.split():
        if "=" in token:
            key, value = token.split("=", 1)
            fields[key] = value
    return fields


def array_accounting(job_id: str) -> dict[int, str]:
    completed = run([
        "sacct", "-X", "-n", "-P", "-j", job_id,
        "-o", "ArrayJobID,ArrayTaskID,State",
    ])
    states: dict[int, str] = {}
    for line in completed.stdout.splitlines():
        fields = line.split("|")
        if len(fields) < 3 or fields[0] != job_id or not fields[1].isdigit():
            continue
        states[int(fields[1])] = fields[2].split("+", 1)[0]
    return states


def build_retry_command(args: argparse.Namespace) -> list[str]:
    base = load_script(
        args.workflow_root / "examples" / "sunrise" / "submit_morbo_chain.py",
        "clpu_n2_recovery_base_chain",
    )
    wrapper = load_script(
        args.workflow_root / "examples" / "sunrise" / "submit_clpu_n2_morbo_chain.py",
        "clpu_n2_recovery_wrapper",
    )
    chain_args = base.ChainArgs(
        optimization_root=args.optimization_root,
        start_iteration=args.iteration,
        num_additional_iterations=1,
        array_spec=args.retry_array_spec,
        workflow_root=args.workflow_root,
        workflow_env=args.workflow_env,
        job_name_prefix=args.job_name_prefix,
        partition=args.partition,
        time=args.time,
        nodes=args.nodes,
        ntasks=args.ntasks,
        tick_partition="T6H",
        tick_time="06:00:00",
        tick_nodes=1,
        tick_ntasks=24,
        execute=True,
        optimization_config=args.optimization_root / "optimizer.json",
        array_script=args.workflow_root / "examples" / "sunrise" / "run_iteration_array.sh",
        tick_script=args.workflow_root / "examples" / "sunrise" / "run_optimizer_tick_materialize_only.sh",
        case_runner=args.case_runner,
        initial_dependency_job_id=None,
    )
    base.validate_chain_args(chain_args)
    command = base.build_array_sbatch_command(
        args=chain_args, iteration=args.iteration, dependency=None
    )
    command = wrapper._force_validated_cleanup_export(
        command, iteration=args.iteration, iteration_root=args.optimization_root
    )
    command.insert(2, "--hold")
    return command


def case_attempt_dir(case_dir: Path, failed_job_id: str, case_id: int) -> Path:
    return case_dir / "attempts" / f"slurm_{failed_job_id}_task_{case_id}"


def validate_cases(
    *, args: argparse.Namespace, retry_ids: list[int], completed_ids: list[int]
) -> tuple[Path, dict[int, Path], list[dict[str, Any]]]:
    campaign_root = args.optimization_root / "iterations" / f"iter_{args.iteration:03d}"
    config = load_campaign_config(campaign_root)
    cases = load_cases(campaign_root, config)
    by_id = {case.case_id: campaign_root / case.case_name for case in cases}
    expected = sorted(by_id)
    if sorted(set(retry_ids) | set(completed_ids)) != expected:
        raise RecoveryError(
            f"retry+completed IDs must exactly cover materialized cases: "
            f"expected={expected} retry={retry_ids} completed={completed_ids}"
        )
    plans: list[dict[str, Any]] = []
    for case_id in completed_ids:
        case_dir = by_id[case_id]
        state = state_name(read_json(case_dir / "state.json"))
        required = [
            "post/sim_done.json", "post/analysis_done.json",
            "post/raw_delete_eligible.json", "post/raw_deleted.json",
        ]
        missing = [rel for rel in required if not (case_dir / rel).is_file()]
        if state != "Raw_deleted" or missing:
            raise RecoveryError(
                f"completed case {case_id} is not safely complete: "
                f"state={state} missing={missing}"
            )
    for case_id in retry_ids:
        case_dir = by_id[case_id]
        state = state_name(read_json(case_dir / "state.json"))
        forbidden = [
            "post/sim_done.json", "post/analysis_done.json",
            "post/raw_delete_eligible.json", "post/raw_deleted.json",
        ]
        present = [rel for rel in forbidden if (case_dir / rel).exists()]
        attempt = case_attempt_dir(case_dir, args.failed_array_job_id, case_id)
        if state not in {"Running", "Failed", "Retryable"}:
            raise RecoveryError(f"retry case {case_id} has incompatible state {state}")
        if present:
            raise RecoveryError(
                f"retry case {case_id} has success/cleanup evidence: {present}"
            )
        if attempt.exists() and (case_dir / "diags").exists():
            raise RecoveryError(
                f"retry case {case_id} has both live diags and existing quarantine {attempt}"
            )
        plans.append({
            "case_id": case_id,
            "case_dir": str(case_dir),
            "state": state,
            "quarantine": str(attempt),
            "live_diags": (case_dir / "diags").exists(),
            "quarantine_exists": attempt.exists(),
        })
    return campaign_root, by_id, plans


def quarantine_case(case_dir: Path, attempt: Path) -> None:
    if attempt.exists():
        return
    attempt.mkdir(parents=True, exist_ok=False)
    for name in ("diags", "checkpoints"):
        source = case_dir / name
        if source.exists():
            shutil.move(str(source), str(attempt / name))
    post_archive = attempt / "post"
    marker_names = ("sim_submitted.json", "sim_running.json", "sim_failed.json")
    for name in marker_names:
        source = case_dir / "post" / name
        if source.exists():
            post_archive.mkdir(parents=True, exist_ok=True)
            shutil.move(str(source), str(post_archive / name))
    run_info = case_dir / "run_info.txt"
    if run_info.exists():
        shutil.move(str(run_info), str(attempt / "run_info.txt"))


def prepare_retry_cases(
    *, args: argparse.Namespace, by_id: dict[int, Path], retry_ids: list[int]
) -> None:
    for case_id in retry_ids:
        case_dir = by_id[case_id]
        current = state_name(read_json(case_dir / "state.json"))
        common = ["--campaign-root", str(case_dir.parent), "--case-id", str(case_id)]
        if current == "Running":
            rc = mark_sim_failed(common + [
                "--scheduler", "slurm",
                "--scheduler-job-id", args.failed_array_job_id,
                "--scheduler-array-task-id", str(case_id),
                "--return-code", "124",
                "--error", "SLURM walltime exceeded",
            ])
            if rc:
                raise RecoveryError(f"mark_sim_failed failed for case {case_id}")
            current = "Failed"
        if current == "Failed":
            rc = mark_sim_retryable(common + [
                "--scheduler", "slurm",
                "--failed-job-id", args.failed_array_job_id,
                "--failed-array-task-id", str(case_id),
                "--reason", "retry after verified SLURM TIMEOUT with partial raw quarantined",
            ])
            if rc:
                raise RecoveryError(f"mark_sim_retryable failed for case {case_id}")
        quarantine_case(
            case_dir,
            case_attempt_dir(case_dir, args.failed_array_job_id, case_id),
        )


def update_optimizer_state(
    *, args: argparse.Namespace, retry_job_id: str, command: list[str], retry_ids: list[int]
) -> None:
    info = read_optimization_state(args.optimization_root)
    state = info.data
    if not isinstance(state, dict):
        raise RecoveryError("optimization_state.json is missing")
    rows: list[dict[str, Any]] = []
    found = False
    stamp = now_utc()
    for raw in state.get("iterations", []):
        if not isinstance(raw, dict):
            continue
        row = dict(raw)
        if int(row.get("iteration", -1)) == args.iteration:
            found = True
            job_ids = list(dict.fromkeys([*(str(x) for x in row.get("slurm_job_ids", [])), retry_job_id]))
            history = list(row.get("submission_history", []))
            if not any(str(item.get("job_id")) == retry_job_id for item in history if isinstance(item, dict)):
                history.append({
                    "job_id": retry_job_id,
                    "submitted_at": stamp,
                    "array_spec": args.retry_array_spec,
                    "submitted_case_ids": retry_ids,
                    "submit_command": command,
                    "case_runner": str(args.case_runner),
                    "confirm_cleanup_execute": True,
                    "retry": True,
                    "retry_of_job_id": args.failed_array_job_id,
                    "retry_reason": "verified SLURM walltime timeout",
                })
            array_specs = list(row.get("array_specs", []))
            if args.retry_array_spec not in array_specs:
                array_specs.append(args.retry_array_spec)
            submit_commands = list(row.get("submit_commands", []))
            if command not in submit_commands:
                submit_commands.append(command)
            cumulative = sorted(set(int(x) for x in row.get("submitted_case_ids", [])) | set(retry_ids))
            row.update({
                "status": "submitted", "submitted": True,
                "recommended_action": "wait_for_jobs",
                "slurm_job_ids": job_ids,
                "submitted_at": stamp,
                "submit_command": command,
                "array_spec": compact_case_ids_as_array_spec(cumulative),
                "array_specs": array_specs,
                "submitted_case_ids": cumulative,
                "submitted_case_count": len(cumulative),
                "submit_commands": submit_commands,
                "submission_history": history,
                "case_runner": str(args.case_runner),
                "confirm_cleanup_execute": True,
            })
        rows.append(row)
    if not found:
        raise RecoveryError(f"iter_{args.iteration:03d} missing from optimization state")
    state["iterations"] = rows
    state["status"] = "running"
    state["updated_at"] = stamp
    write_optimization_state(args.optimization_root, state)


def receipt_path(args: argparse.Namespace) -> Path:
    return (
        args.optimization_root / "provenance" / "recoveries" /
        f"clpu_n2_walltime_iter_{args.iteration:03d}_job_{args.failed_array_job_id}.json"
    )


def main(argv: Sequence[str] | None = None) -> int:
    args = parser().parse_args(argv)
    args.optimization_root = args.optimization_root.expanduser().resolve(strict=True)
    args.workflow_root = args.workflow_root.expanduser().resolve(strict=True)
    args.workflow_env = args.workflow_env.expanduser().resolve(strict=True)
    args.case_runner = args.case_runner.expanduser().resolve(strict=True)
    args.chain_manifest = args.chain_manifest.expanduser().resolve(strict=True)
    retry_ids = expand_array_spec(args.retry_array_spec)
    completed_ids = sorted(set(args.completed_case_id))
    if set(retry_ids) & set(completed_ids):
        raise RecoveryError("retry and completed case IDs overlap")
    if args.partition != "T12H" or args.time != "12:00:00":
        raise RecoveryError("this recovery requires static T12H / 12:00:00")

    manifest = read_json(args.chain_manifest)
    jobs = manifest_jobs(manifest)
    one_job(jobs, kind="array", iteration=args.iteration, job_id=args.failed_array_job_id)
    one_job(jobs, kind="tick", iteration=args.iteration, job_id=args.blocked_tick_job_id)
    future_arrays = [
        item for item in jobs
        if item.get("kind") == "array" and int(item.get("iteration", -1)) > args.iteration
    ]
    if not future_arrays:
        raise RecoveryError("manifest has no future arrays to update")
    future_array_ids = [str(item.get("job_id", "")) for item in future_arrays]
    if any(not item for item in future_array_ids):
        raise RecoveryError("future array manifest entries have missing job IDs")

    accounting = array_accounting(args.failed_array_job_id)
    expected_states = {case_id: "TIMEOUT" for case_id in retry_ids}
    expected_states.update({case_id: "COMPLETED" for case_id in completed_ids})
    if accounting != expected_states:
        raise RecoveryError(
            f"array accounting mismatch: expected={expected_states} actual={accounting}"
        )
    tick_before = job_fields(args.blocked_tick_job_id)
    if tick_before.get("JobState") != "PENDING":
        raise RecoveryError(
            f"blocked tick is not pending: {tick_before.get('JobState')}"
        )
    for job_id in future_array_ids:
        fields = job_fields(job_id)
        if fields.get("JobState") != "PENDING":
            raise RecoveryError(f"future array {job_id} is not pending")

    campaign_root, by_id, case_plans = validate_cases(
        args=args, retry_ids=retry_ids, completed_ids=completed_ids
    )
    gate_receipt = (
        args.optimization_root / "provenance" /
        f"clpu_n2_gate_b_iter_{args.iteration:03d}.json"
    )
    if not gate_receipt.is_file():
        raise RecoveryError(f"missing existing Gate B receipt: {gate_receipt}")
    command = build_retry_command(args)
    plan = {
        "dry_run": not args.execute,
        "iteration": args.iteration,
        "campaign_root": str(campaign_root),
        "failed_array_job_id": args.failed_array_job_id,
        "blocked_tick_job_id": args.blocked_tick_job_id,
        "retry_case_ids": retry_ids,
        "preserved_completed_case_ids": completed_ids,
        "case_plans": case_plans,
        "retry_submit_command": command,
        "tick_dependency_update": [
            "scontrol", "update", f"JobId={args.blocked_tick_job_id}",
            "Dependency=afterok:<retry-array-job-id>",
        ],
        "future_array_resource_updates": [
            ["scontrol", "update", f"JobId={job_id}", "Partition=T12H", "TimeLimit=12:00:00"]
            for job_id in future_array_ids
        ],
        "chain_manifest": str(args.chain_manifest),
        "receipt": str(receipt_path(args)),
    }
    if not args.execute:
        print(json.dumps(plan, indent=2, sort_keys=True))
        return 0

    recovery_path = receipt_path(args)
    if recovery_path.exists():
        receipt = read_json(recovery_path)
    else:
        receipt = {"schema_version": 1, "created_at": now_utc(), **plan}
        receipt["dry_run"] = False
        receipt["status"] = "preparing_cases"
        write_json_atomic(recovery_path, receipt)

    retry_job_id = str(receipt.get("retry_array_job_id", ""))
    if not retry_job_id:
        prepare_retry_cases(args=args, by_id=by_id, retry_ids=retry_ids)
        receipt["status"] = "submitting_held_retry"
        write_json_atomic(recovery_path, receipt)
        submitted = run(command, cwd=args.optimization_root)
        retry_job_id = submitted.stdout.strip().split(";", 1)[0]
        if not retry_job_id.isdigit():
            raise RecoveryError(f"could not parse retry job id: {submitted.stdout!r}")
        receipt["retry_array_job_id"] = retry_job_id
        receipt["status"] = "retry_held"
        write_json_atomic(recovery_path, receipt)

    update_optimizer_state(
        args=args, retry_job_id=retry_job_id, command=command, retry_ids=retry_ids
    )
    run([
        "scontrol", "update", f"JobId={args.blocked_tick_job_id}",
        f"Dependency=afterok:{retry_job_id}",
    ])
    for job_id in future_array_ids:
        run([
            "scontrol", "update", f"JobId={job_id}",
            "Partition=T12H", "TimeLimit=12:00:00",
        ])
    tick_after = job_fields(args.blocked_tick_job_id)
    if tick_after.get("Dependency") != f"afterok:{retry_job_id}(unfulfilled)":
        # Slurm versions differ in the optional parenthesized status suffix.
        if not tick_after.get("Dependency", "").startswith(f"afterok:{retry_job_id}"):
            raise RecoveryError(
                f"tick dependency verification failed: {tick_after.get('Dependency')}"
            )
    for job_id in future_array_ids:
        fields = job_fields(job_id)
        if fields.get("Partition") != "T12H" or fields.get("TimeLimit") != "12:00:00":
            raise RecoveryError(
                f"future array resource verification failed for {job_id}: "
                f"Partition={fields.get('Partition')} TimeLimit={fields.get('TimeLimit')}"
            )
    run(["scontrol", "release", retry_job_id])
    receipt.update({
        "status": "released",
        "completed_at": now_utc(),
        "retry_array_job_id": retry_job_id,
        "tick_dependency_after": tick_after.get("Dependency"),
        "future_array_job_ids_updated": future_array_ids,
    })
    write_json_atomic(recovery_path, receipt)
    print(json.dumps(receipt, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (RecoveryError, RuntimeError, ValueError) as exc:
        print(f"[CLPU-N2-WALLTIME-RECOVERY] ERROR: {exc}", file=sys.stderr)
        raise SystemExit(2)
