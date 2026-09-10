#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import sys
from types import ModuleType
from typing import Any, Sequence


RETENTION_CONTRACT_ID = "clpu_n2_retain_raw_v1"
RETENTION_POLICY_KEY = "raw_retention_required"


def _load_base_chain() -> ModuleType:
    path = Path(__file__).with_name("submit_morbo_chain.py").resolve()
    spec = importlib.util.spec_from_file_location("campaign_workflow_submit_morbo_chain_base", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"could not import base MORBO chain launcher: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _require_retention_policy(base: ModuleType, argv: Sequence[str]) -> Path:
    parser = base.build_parser()
    raw = parser.parse_args(list(argv))
    optimization_root = raw.optimization_root.expanduser().resolve(strict=False)
    path = optimization_root / "optimization.json"
    if not path.is_file():
        raise RuntimeError(f"missing optimization.json under optimization root: {optimization_root}")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise RuntimeError(f"could not read optimization policy: {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise RuntimeError(f"optimization.json root must be an object: {path}")
    policy = payload.get("policy", {}) or {}
    if not isinstance(policy, dict):
        raise RuntimeError("optimization.json policy must be an object")
    value = policy.get(RETENTION_POLICY_KEY)
    if value is not True:
        raise RuntimeError(
            "CLPU N2 launch requires explicit policy.raw_retention_required=true; "
            f"got {value!r} in {path}"
        )
    return optimization_root


def _force_retained_raw_export(command: Sequence[str]) -> list[str]:
    out = list(command)
    found = False
    for index, item in enumerate(out):
        if not str(item).startswith("--export="):
            continue
        found = True
        values = str(item)[len("--export=") :].split(",")
        values = [
            value
            for value in values
            if not value.startswith("CONFIRM_CLEANUP_EXECUTE=")
            and not value.startswith("CW_RAW_RETENTION_REQUIRED=")
        ]
        values.extend(
            [
                "CW_RAW_RETENTION_REQUIRED=1",
                "CONFIRM_CLEANUP_EXECUTE=0",
            ]
        )
        out[index] = "--export=" + ",".join(values)
        break
    if not found:
        raise RuntimeError("base MORBO array command does not contain --export")
    return out


def _install_retention_contract(base: ModuleType) -> None:
    original_build_array = base.build_array_sbatch_command
    original_build_manifest = base.build_manifest

    def build_array_sbatch_command(*, args: Any, iteration: int, dependency: str | None) -> list[str]:
        command = original_build_array(
            args=args,
            iteration=iteration,
            dependency=dependency,
        )
        return _force_retained_raw_export(command)

    def build_manifest(*, args: Any, jobs: list[dict[str, Any]], dry_run: bool) -> dict[str, Any]:
        manifest = original_build_manifest(args=args, jobs=jobs, dry_run=dry_run)
        manifest["raw_retention_policy"] = {
            "contract_id": RETENTION_CONTRACT_ID,
            "required": True,
            "cleanup_execute": False,
        }
        return manifest

    base.build_array_sbatch_command = build_array_sbatch_command
    base.build_manifest = build_manifest


def main(argv: Sequence[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    try:
        base = _load_base_chain()
        _require_retention_policy(base, args)
        _install_retention_contract(base)
        return int(base.main(args))
    except (RuntimeError, ValueError) as exc:
        print(f"[CLPU-N2-CHAIN] ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
