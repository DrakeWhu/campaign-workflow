from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Sequence


LAUNCH_GATE_CONTRACT_ID = "clpu_n2_launch_gate_v1"
SUBMISSION_CONTRACT_ID = "clpu_n2_submission_receipts_v1"


class ClpuN2SubmissionError(RuntimeError):
    """Raised when the CLPU N2 launch/submission contract is not satisfied."""


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    tmp.replace(path)


def _job_id_text(value: Any) -> str:
    """Return a normalized accepted SLURM job id, or empty for placeholders."""
    if value is None:
        return ""
    return str(value).strip()


def require_launch_gate(
    *, optimization_root: Path, start_iteration: int
) -> tuple[Path, dict[str, Any], str]:
    root = optimization_root.expanduser().resolve(strict=False)
    path = root / "provenance" / "clpu_n2_launch_gate.json"
    if not path.is_file():
        raise ClpuN2SubmissionError(f"missing CLPU N2 launch gate receipt: {path}")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise ClpuN2SubmissionError(
            f"could not read launch gate receipt {path}: {exc}"
        ) from exc
    if not isinstance(payload, dict):
        raise ClpuN2SubmissionError("CLPU N2 launch gate receipt root must be an object")

    expected: dict[str, Any] = {
        "schema_version": 1,
        "contract_id": LAUNCH_GATE_CONTRACT_ID,
        "status": "pass",
        "allow_sbatch": True,
        "gate_a_status": "pass",
        "gate_b_status": "pass",
        "f01_f03_status": "pass",
        "raw_retention_status": "pass",
        "raw_retention_capacity_status": "pass",
        "cleanup_execute": False,
        "iteration": int(start_iteration),
    }
    for key, value in expected.items():
        if payload.get(key) != value:
            raise ClpuN2SubmissionError(
                f"CLPU N2 launch gate rejected: {key}={payload.get(key)!r}, expected {value!r}"
            )

    recorded_root = payload.get("optimization_root")
    if not isinstance(recorded_root, str) or not recorded_root.strip():
        raise ClpuN2SubmissionError("CLPU N2 launch gate has no optimization_root")
    if Path(recorded_root).expanduser().resolve(strict=False) != root:
        raise ClpuN2SubmissionError(
            "CLPU N2 launch gate optimization_root does not match requested root"
        )

    return path, payload, sha256_file(path)


def launch_identity(*, args: Any, launch_gate_sha256: str) -> dict[str, Any]:
    return {
        "contract_id": SUBMISSION_CONTRACT_ID,
        "optimization_root": str(args.optimization_root),
        "start_iteration": int(args.start_iteration),
        "final_iteration": int(args.final_iteration),
        "num_additional_iterations": int(args.num_additional_iterations),
        "array_spec": str(args.array_spec),
        "workflow_root": str(args.workflow_root),
        "workflow_env": str(args.workflow_env),
        "job_name_prefix": str(args.job_name_prefix),
        "partition": str(args.partition),
        "time": str(args.time),
        "nodes": int(args.nodes),
        "ntasks": int(args.ntasks),
        "tick_partition": str(args.tick_partition),
        "tick_time": str(args.tick_time),
        "tick_nodes": int(args.tick_nodes),
        "tick_ntasks": int(args.tick_ntasks),
        "array_script": str(args.array_script),
        "tick_script": str(args.tick_script),
        "case_runner": None if args.case_runner is None else str(args.case_runner),
        "initial_dependency_job_id": args.initial_dependency_job_id,
        "optimization_config": (
            None if args.optimization_config is None else str(args.optimization_config)
        ),
        "launch_gate_sha256": launch_gate_sha256,
    }


def launch_fingerprint(identity: dict[str, Any]) -> str:
    encoded = json.dumps(identity, sort_keys=True, separators=(",", ":")).encode(
        "utf-8"
    )
    return hashlib.sha256(encoded).hexdigest()


def submission_manifest_path(*, optimization_root: Path, fingerprint: str) -> Path:
    return optimization_root / "loop_logs" / f"morbo_chain_n2_{fingerprint[:20]}.json"


def _planned_jobs(args: Any) -> list[dict[str, Any]]:
    jobs: list[dict[str, Any]] = []
    for iteration in range(args.start_iteration, args.final_iteration + 1):
        jobs.append(
            {
                "kind": "array",
                "iteration": int(iteration),
                "job_id": None,
                "dependency": None,
                "submit_command": None,
                "receipt_path": None,
                "accepted_at": None,
            }
        )
        jobs.append(
            {
                "kind": "tick",
                "iteration": int(iteration),
                "next_iteration": int(iteration + 1),
                "job_id": None,
                "dependency": None,
                "submit_command": None,
                "receipt_path": None,
                "accepted_at": None,
            }
        )
    return jobs


def _overlaps_requested_iterations(manifest: dict[str, Any], args: Any) -> bool:
    lo = int(args.start_iteration)
    hi = int(args.final_iteration)
    for job in manifest.get("jobs", []):
        if not isinstance(job, dict) or not _job_id_text(job.get("job_id")):
            continue
        try:
            iteration = int(job.get("iteration"))
        except Exception:
            continue
        if lo <= iteration <= hi:
            return True
    return False


def _assert_no_conflicting_submission(
    *, args: Any, target_path: Path, fingerprint: str
) -> None:
    loop_logs = args.optimization_root / "loop_logs"
    if loop_logs.is_dir():
        for path in sorted(loop_logs.glob("morbo_chain_n2_*.json")):
            if path == target_path:
                continue
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                continue
            if _overlaps_requested_iterations(payload, args):
                raise ClpuN2SubmissionError(
                    "existing accepted CLPU N2 jobs overlap requested iterations under a different "
                    f"submission fingerprint: {path}"
                )


def _assert_start_iteration_not_already_submitted(*, base: Any, args: Any) -> None:
    state_info = base.read_optimization_state(args.optimization_root)
    state = state_info.data
    if not isinstance(state, dict):
        return
    for item in state.get("iterations", []):
        if not isinstance(item, dict):
            continue
        try:
            iteration = int(item.get("iteration", -1))
        except Exception:
            continue
        if iteration != int(args.start_iteration):
            continue
        job_ids = [
            _job_id_text(x)
            for x in item.get("slurm_job_ids", [])
            if _job_id_text(x)
        ]
        case_ids = item.get("submitted_case_ids", [])
        if (
            bool(item.get("submitted"))
            or job_ids
            or (isinstance(case_ids, list) and case_ids)
        ):
            raise ClpuN2SubmissionError(
                f"start iteration already submitted: iter_{args.start_iteration:03d}; refusing duplicate sbatch"
            )


def _validate_resume_manifest(
    *,
    manifest: dict[str, Any],
    identity: dict[str, Any],
    fingerprint: str,
    args: Any,
) -> None:
    if manifest.get("submission_contract_id") != SUBMISSION_CONTRACT_ID:
        raise ClpuN2SubmissionError(
            "existing N2 submission manifest has incompatible contract"
        )
    if manifest.get("launch_fingerprint") != fingerprint:
        raise ClpuN2SubmissionError("existing N2 submission manifest fingerprint mismatch")
    if manifest.get("launch_identity") != identity:
        raise ClpuN2SubmissionError("existing N2 submission manifest identity mismatch")
    jobs = manifest.get("jobs")
    planned = _planned_jobs(args)
    if not isinstance(jobs, list) or len(jobs) != len(planned):
        raise ClpuN2SubmissionError(
            "existing N2 submission manifest has incompatible job count"
        )
    seen_gap = False
    for index, (job, expected) in enumerate(zip(jobs, planned)):
        if not isinstance(job, dict):
            raise ClpuN2SubmissionError(f"invalid job entry at sequence {index}")
        if job.get("kind") != expected.get("kind") or int(
            job.get("iteration", -1)
        ) != int(expected["iteration"]):
            raise ClpuN2SubmissionError(f"job sequence mismatch at index {index}")
        accepted = bool(_job_id_text(job.get("job_id")))
        if not accepted:
            seen_gap = True
        elif seen_gap:
            raise ClpuN2SubmissionError(
                "accepted jobs are not a contiguous prefix; refusing unsafe resume"
            )
        if accepted:
            receipt_raw = job.get("receipt_path")
            if not isinstance(receipt_raw, str) or not receipt_raw:
                raise ClpuN2SubmissionError(
                    f"accepted job at sequence {index} has no receipt path"
                )
            receipt_path = Path(receipt_raw)
            if not receipt_path.is_file():
                raise ClpuN2SubmissionError(
                    f"accepted job receipt missing: {receipt_path}"
                )
            try:
                receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
            except Exception as exc:
                raise ClpuN2SubmissionError(
                    f"could not read accepted job receipt {receipt_path}: {exc}"
                ) from exc
            if (
                receipt.get("launch_fingerprint") != fingerprint
                or _job_id_text(receipt.get("job_id"))
                != _job_id_text(job.get("job_id"))
            ):
                raise ClpuN2SubmissionError(
                    f"accepted job receipt identity mismatch: {receipt_path}"
                )


def _register_array_if_materialized(
    *,
    base: Any,
    args: Any,
    manifest: dict[str, Any],
    manifest_path: Path,
    array_job: dict[str, Any],
) -> None:
    state_info = base.read_optimization_state(args.optimization_root)
    state = state_info.data
    if not isinstance(state, dict):
        return
    iteration = int(array_job["iteration"])
    if not any(
        isinstance(item, dict) and int(item.get("iteration", -1)) == iteration
        for item in state.get("iterations", [])
    ):
        return
    updated = base.update_state_after_presubmitted_array(
        state_doc=state,
        optimization_root=args.optimization_root,
        iteration=iteration,
        manifest_path=manifest_path,
        manifest=manifest,
        array_job=array_job,
    )
    base.write_optimization_state(args.optimization_root, updated)


def transactional_submit_finite_chain(
    *,
    base: Any,
    args: Any,
    launch_gate_path: Path,
    launch_gate_sha256: str,
) -> dict[str, Any]:
    identity = launch_identity(args=args, launch_gate_sha256=launch_gate_sha256)
    fingerprint = launch_fingerprint(identity)
    manifest_path = submission_manifest_path(
        optimization_root=args.optimization_root,
        fingerprint=fingerprint,
    )
    args.optimization_root.joinpath("loop_logs").mkdir(parents=True, exist_ok=True)
    receipt_dir = args.optimization_root / "loop_logs" / "clpu_n2_job_receipts"
    receipt_dir.mkdir(parents=True, exist_ok=True)

    _assert_no_conflicting_submission(
        args=args,
        target_path=manifest_path,
        fingerprint=fingerprint,
    )

    if manifest_path.is_file():
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except Exception as exc:
            raise ClpuN2SubmissionError(
                f"could not read existing submission manifest {manifest_path}: {exc}"
            ) from exc
        _validate_resume_manifest(
            manifest=manifest,
            identity=identity,
            fingerprint=fingerprint,
            args=args,
        )
        if manifest.get("submission_status") == "complete":
            raise ClpuN2SubmissionError(
                f"CLPU N2 chain already fully submitted: {manifest_path}; refusing duplicate sbatch"
            )
    else:
        _assert_start_iteration_not_already_submitted(base=base, args=args)
        manifest = base.build_manifest(
            args=args, jobs=_planned_jobs(args), dry_run=False
        )
        manifest.update(
            {
                "submission_contract_id": SUBMISSION_CONTRACT_ID,
                "submission_status": "in_progress",
                "launch_fingerprint": fingerprint,
                "launch_identity": identity,
                "launch_gate": {
                    "path": str(launch_gate_path),
                    "sha256": launch_gate_sha256,
                },
                "accepted_job_count": 0,
                "last_error": None,
                "resumed": False,
            }
        )
        write_json_atomic(manifest_path, manifest)

    jobs = manifest["jobs"]
    accepted_count = sum(
        1 for job in jobs if _job_id_text(job.get("job_id"))
    )
    if accepted_count:
        manifest["resumed"] = True
        manifest["submission_status"] = "in_progress"
        manifest["last_error"] = None
        write_json_atomic(manifest_path, manifest)

    previous_tick_job_id: str | None = args.initial_dependency_job_id
    last_array_job_id: str | None = None

    for index, job in enumerate(jobs):
        existing_job_id = _job_id_text(job.get("job_id"))
        kind = str(job["kind"])
        iteration = int(job["iteration"])

        if existing_job_id:
            if kind == "array":
                last_array_job_id = existing_job_id
            else:
                previous_tick_job_id = existing_job_id
                last_array_job_id = None
            continue

        if kind == "array":
            dependency = (
                None
                if previous_tick_job_id is None
                else f"afterok:{previous_tick_job_id}"
            )
            command = base.build_array_sbatch_command(
                args=args,
                iteration=iteration,
                dependency=dependency,
            )
        elif kind == "tick":
            if last_array_job_id is None:
                raise ClpuN2SubmissionError(
                    f"cannot submit tick at sequence {index}: preceding array receipt is missing"
                )
            dependency = f"afterok:{last_array_job_id}"
            command = base.build_tick_sbatch_command(
                args=args,
                iteration=iteration,
                next_iteration=int(job["next_iteration"]),
                dependency=dependency,
            )
        else:
            raise ClpuN2SubmissionError(
                f"unsupported job kind in N2 submission journal: {kind!r}"
            )

        try:
            job_id = base.execute_sbatch(command, cwd=args.optimization_root)
        except Exception as exc:
            manifest["submission_status"] = (
                "partial_failure" if accepted_count else "failed_before_accept"
            )
            manifest["last_error"] = str(exc)
            manifest["accepted_job_count"] = accepted_count
            write_json_atomic(manifest_path, manifest)
            raise

        accepted_at = base.now_utc()
        receipt_path = (
            receipt_dir
            / f"{fingerprint[:20]}_{index:03d}_{kind}_iter{iteration:03d}.json"
        )
        receipt = {
            "schema_version": 1,
            "contract_id": SUBMISSION_CONTRACT_ID,
            "launch_fingerprint": fingerprint,
            "sequence_index": index,
            "kind": kind,
            "iteration": iteration,
            "job_id": str(job_id),
            "dependency": dependency,
            "submit_command": command,
            "accepted_at": accepted_at,
            "launch_gate_sha256": launch_gate_sha256,
        }
        try:
            write_json_atomic(receipt_path, receipt)
        except Exception as exc:
            raise ClpuN2SubmissionError(
                "SLURM accepted a job but its durable receipt could not be written; "
                f"job_id={job_id} receipt={receipt_path}: {exc}. Do not retry automatically."
            ) from exc

        job.update(
            {
                "job_id": str(job_id),
                "dependency": dependency,
                "submit_command": command,
                "receipt_path": str(receipt_path),
                "accepted_at": accepted_at,
            }
        )
        accepted_count += 1
        manifest["accepted_job_count"] = accepted_count
        manifest["submission_status"] = "in_progress"
        manifest["last_error"] = None
        write_json_atomic(manifest_path, manifest)

        if kind == "array":
            last_array_job_id = str(job_id)
            _register_array_if_materialized(
                base=base,
                args=args,
                manifest=manifest,
                manifest_path=manifest_path,
                array_job=job,
            )
        else:
            previous_tick_job_id = str(job_id)
            last_array_job_id = None

    manifest["submission_status"] = "complete"
    manifest["accepted_job_count"] = len(jobs)
    manifest["completed_at"] = base.now_utc()
    manifest["manifest_path"] = str(manifest_path)
    write_json_atomic(manifest_path, manifest)
    return manifest
