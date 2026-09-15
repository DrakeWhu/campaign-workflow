#!/usr/bin/env python3
"""Recover a never-started CLPU N2 finite chain after all its jobs are terminal."""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path


def now() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def read(path: Path) -> dict:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise RuntimeError(f"JSON root is not an object: {path}")
    return data


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--optimization-root", type=Path, required=True)
    p.add_argument("--start-iteration", type=int, required=True)
    p.add_argument("--execute", action="store_true")
    args = p.parse_args()
    root = args.optimization_root.expanduser().resolve(strict=True)
    manifests = sorted((root / "loop_logs").glob("morbo_chain_n2_*.json"))
    candidates: list[tuple[Path, dict]] = []
    for path in manifests:
        try:
            data = read(path)
        except Exception:
            continue
        if int(data.get("start_iteration", -1)) == args.start_iteration:
            candidates.append((path, data))
    if len(candidates) != 1:
        raise RuntimeError(f"expected exactly one N2 manifest for iter {args.start_iteration}; found {len(candidates)}")
    manifest_path, manifest = candidates[0]
    jobs = manifest.get("jobs")
    if not isinstance(jobs, list) or not jobs:
        raise RuntimeError("manifest has no jobs")
    ids = [str(x.get("job_id", "")).strip() for x in jobs if isinstance(x, dict)]
    if len(ids) != len(jobs) or any(not x for x in ids):
        raise RuntimeError("manifest has incomplete accepted job IDs")
    if any(str(x.get("kind")) == "array" and int(x.get("iteration", -1)) == args.start_iteration
           for x in jobs if isinstance(x, dict)) is False:
        raise RuntimeError("manifest has no start-iteration array")
    result = subprocess.run(
        ["squeue", "-h", "-j", ",".join(ids), "-o", "%i %T"],
        text=True, capture_output=True, check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(f"squeue failed: {result.stderr.strip()}")
    if result.stdout.strip():
        raise RuntimeError("refusing recovery while chain jobs remain active:\n" + result.stdout.strip())
    acct = subprocess.run(
        ["sacct", "-X", "-n", "-P", "-j", ",".join(ids), "-o", "JobIDRaw,State"],
        text=True, capture_output=True, check=False,
    )
    if acct.returncode != 0:
        raise RuntimeError(f"sacct failed: {acct.stderr.strip()}")
    forbidden = ("COMPLETED", "RUNNING", "PENDING", "CONFIGURING", "SUSPENDED")
    if any(token in acct.stdout for token in forbidden):
        raise RuntimeError("refusing recovery: chain has a non-terminal or completed job:\n" + acct.stdout.strip())

    state_path = root / "optimization_state.json"
    state = read(state_path)
    rows = state.get("iterations")
    if not isinstance(rows, list):
        raise RuntimeError("optimization_state has no iterations list")
    target = next((x for x in rows if isinstance(x, dict) and int(x.get("iteration", -1)) == args.start_iteration), None)
    if not isinstance(target, dict):
        raise RuntimeError("start iteration missing from optimization_state")
    if not bool(target.get("submitted")):
        raise RuntimeError("start iteration is not marked submitted; recovery is not applicable")

    summary = {"manifest": str(manifest_path), "job_ids": ids, "sacct": acct.stdout.strip()}
    if not args.execute:
        print(json.dumps({"dry_run": True, **summary}, indent=2))
        return 0

    stamp = now()
    archive = root / "provenance" / "aborted_chains" / f"{manifest_path.stem}_{stamp}.json"
    archive.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(manifest_path), str(archive))
    backup = root / "provenance" / "aborted_chains" / f"optimization_state_before_recovery_{stamp}.json"
    backup.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    reset_keys = {
        "slurm_job_ids", "submitted_at", "submit_command", "array_spec",
        "submitted_case_ids", "submitted_case_count", "array_noop_task_ids",
        "submit_script", "working_directory", "submit_log",
        "pre_submitted_chain_manifest", "pre_submitted_chain_registered_at",
    }
    for item in rows:
        if isinstance(item, dict) and int(item.get("iteration", -1)) == args.start_iteration:
            for key in reset_keys:
                item.pop(key, None)
            item["submitted"] = False
            item["status"] = "Created"
            item["recommended_action"] = "submit_iteration"
    state["status"] = "running"
    state["updated_at"] = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    tmp = state_path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    tmp.replace(state_path)
    receipt = root / "provenance" / "aborted_chains" / f"recovery_{stamp}.json"
    receipt.write_text(json.dumps({
        "schema_version": 1, "status": "pass", "start_iteration": args.start_iteration,
        "archived_manifest": str(archive), "state_backup": str(backup), **summary,
    }, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(receipt)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
