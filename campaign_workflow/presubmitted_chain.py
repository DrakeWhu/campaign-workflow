from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from campaign_workflow.submit_iteration import expand_array_spec


class PreSubmittedChainError(RuntimeError):
    """Raised when pre-submitted MORBO chain metadata cannot be applied safely."""


def find_latest_chain_manifest_for_array(
    *,
    optimization_root: Path,
    iteration: int,
    job_name_prefix: str | None = None,
) -> tuple[Path, dict[str, Any], dict[str, Any]] | None:
    """Return the latest morbo_chain manifest entry for a pre-submitted array."""

    loop_logs = optimization_root / "loop_logs"
    manifests = sorted(loop_logs.glob("morbo_chain_*.json"))

    for path in reversed(manifests):
        try:
            manifest = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue

        if bool(manifest.get("dry_run")):
            continue

        if job_name_prefix is not None and str(manifest.get("job_name_prefix")) != str(
            job_name_prefix
        ):
            continue

        for job in manifest.get("jobs", []):
            if not isinstance(job, dict):
                continue
            if job.get("kind") != "array":
                continue

            try:
                job_iteration = int(job.get("iteration"))
            except Exception:
                continue

            if job_iteration == int(iteration) and str(job.get("job_id", "")):
                return path, manifest, job

    return None


def update_state_after_presubmitted_array(
    *,
    state_doc: dict[str, Any],
    optimization_root: Path,
    iteration: int,
    manifest_path: Path,
    manifest: dict[str, Any],
    array_job: dict[str, Any],
) -> dict[str, Any]:
    """Attach finite-chain array submit metadata to an existing iteration state."""

    array_spec = str(manifest.get("array_spec", ""))
    submitted_case_ids = expand_array_spec(array_spec)

    job_id = str(array_job.get("job_id", "")).strip()
    if not job_id:
        raise PreSubmittedChainError("array job metadata has no job_id")

    updated = dict(state_doc)
    iterations: list[dict[str, Any]] = []
    found = False

    for raw_item in state_doc.get("iterations", []):
        if not isinstance(raw_item, dict):
            continue

        item = dict(raw_item)

        try:
            item_iteration = int(item.get("iteration", -1))
        except Exception:
            iterations.append(item)
            continue

        if item_iteration == int(iteration):
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
                    "submitted_at": str(manifest.get("created_at", "")),
                    "submit_command": array_job.get("submit_command"),
                    "array_spec": array_spec,
                    "submitted_case_ids": submitted_case_ids,
                    "submitted_case_count": len(submitted_case_ids),
                    "submit_script": str(manifest.get("array_script", "")),
                    "working_directory": str(optimization_root),
                    "submit_log": None,
                    "pre_submitted_chain_manifest": str(manifest_path),
                    "pre_submitted_chain_registered_at": str(
                        manifest.get("created_at", "")
                    ),
                }
            )

        iterations.append(item)

    if not found:
        raise PreSubmittedChainError(
            f"cannot attach pre-submitted array metadata: iteration not found: {iteration}"
        )

    updated["iterations"] = iterations
    updated["status"] = "running"
    updated["latest_iteration"] = max(
        int(item.get("iteration", -1))
        for item in iterations
        if isinstance(item, dict) and "iteration" in item
    )
    updated["updated_at"] = str(manifest.get("created_at", ""))
    return updated
