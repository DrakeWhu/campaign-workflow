#!/usr/bin/env python3
"""Build a CLPU N2 launch receipt from immutable Gate A/B/D and case evidence."""
from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path


def read_json(path: Path) -> dict:
    if not path.is_file():
        raise RuntimeError(f"missing evidence: {path}")
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise RuntimeError(f"evidence is not a JSON object: {path}")
    return data


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--optimization-root", type=Path, required=True)
    parser.add_argument("--start-iteration", type=int, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    root = args.optimization_root.expanduser().resolve()
    config = read_json(root / "optimization.json")
    policy = config.get("policy")
    if not isinstance(policy, dict) or policy.get("cleanup_after_validation_required") is not True:
        raise RuntimeError("requires policy.cleanup_after_validation_required=true")

    sources = {
        "gate_a": root / "optimizer_runs" / "iter_000" / "reports" / "clpu_n2_gate_a.json",
        "gate_b": root / "provenance" / "clpu_n2_gate_b.json",
        "gate_d": root / "provenance" / "clpu_n2_gate_d.json",
    }
    evidence: dict[str, dict[str, str]] = {}
    for name, path in sources.items():
        payload = read_json(path)
        if payload.get("status") != "pass":
            raise RuntimeError(f"{name} is not pass: {path}")
        evidence[name] = {"path": str(path), "sha256": digest(path)}

    state = read_json(root / "optimization_state.json")
    rows = state.get("iterations")
    if not isinstance(rows, list):
        raise RuntimeError("optimization_state has no iterations list")
    row = next((x for x in rows if isinstance(x, dict) and x.get("iteration") == 0), None)
    if not isinstance(row, dict):
        raise RuntimeError("iteration 0 missing from optimization_state")
    expected = list(range(8))
    if row.get("submitted_sim_done_case_ids") != expected or row.get("submitted_reduced_valid_case_ids") != expected:
        raise RuntimeError("iteration 0 is not fully simulated and reduced-valid")

    campaign = root / "iterations" / "iter_000"
    for case_id in expected:
        case = campaign / f"{case_id:03d}"
        # Case directories are named with a numeric prefix; resolve exact materialized name.
        matches = sorted(campaign.glob(f"{case_id:03d}_*"))
        if len(matches) != 1:
            raise RuntimeError(f"could not resolve materialized case {case_id:03d}")
        case = matches[0]
        case_state = read_json(case / "state.json")
        if case_state.get("state") != "Raw_delete_eligible":
            raise RuntimeError(f"case {case_id:03d} is not Raw_delete_eligible")
        read_json(case / "post" / "analysis_done.json")

    output = args.output.expanduser().resolve(strict=False)
    if output.exists():
        raise RuntimeError(f"refusing to overwrite launch receipt: {output}")
    receipt = {
        "schema_version": 1,
        "contract_id": "clpu_n2_launch_gate_v1",
        "status": "pass",
        "allow_sbatch": True,
        "gate_a_status": "pass",
        "gate_b_status": "pass",
        "f01_f03_status": "pass",
        "raw_retention_status": "not_required",
        "raw_retention_capacity_status": "not_required",
        "cleanup_after_validation_status": "pass",
        "cleanup_execute": True,
        "iteration": args.start_iteration,
        "optimization_root": str(root),
        "created_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "evidence": evidence,
        "case_evidence": {
            "source_iteration": 0,
            "submitted_sim_done_case_ids": expected,
            "submitted_reduced_valid_case_ids": expected,
            "required_state": "Raw_delete_eligible",
            "required_receipt": "post/analysis_done.json",
        },
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
