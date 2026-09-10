#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from campaign_workflow.clpu_n2_gate_b import (
    GateBError,
    build_gate_b_receipt,
    verify_runtime_assets,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Validate the materialized CLPU N2 PICMI Gate B contract."
    )
    sub = parser.add_subparsers(dest="command", required=True)

    validate = sub.add_parser("validate")
    validate.add_argument("--optimization-root", type=Path, required=True)
    validate.add_argument("--campaign-root", type=Path, required=True)
    validate.add_argument("--workflow-root", type=Path, required=True)
    validate.add_argument("--guiding-analysis-root", type=Path, required=True)
    validate.add_argument("--optimizer-root", type=Path, required=True)
    validate.add_argument("--output", type=Path, required=True)

    runtime = sub.add_parser("verify-runtime")
    runtime.add_argument("--receipt", type=Path, required=True)
    runtime.add_argument("--optimization-root", type=Path, required=True)
    runtime.add_argument("--workflow-root", type=Path, required=True)
    runtime.add_argument("--guiding-analysis-root", type=Path, required=True)
    runtime.add_argument("--optimizer-root", type=Path, required=True)

    return parser


def main(argv: list[str] | None = None) -> int:
    try:
        args = build_parser().parse_args(argv)
        if args.command == "validate":
            result = build_gate_b_receipt(
                optimization_root=args.optimization_root,
                campaign_root=args.campaign_root,
                workflow_root=args.workflow_root,
                guiding_analysis_root=args.guiding_analysis_root,
                optimizer_root=args.optimizer_root,
                output_path=args.output,
            )
        else:
            result = verify_runtime_assets(
                receipt_path=args.receipt,
                optimization_root=args.optimization_root,
                workflow_root=args.workflow_root,
                guiding_analysis_root=args.guiding_analysis_root,
                optimizer_root=args.optimizer_root,
            )
    except (GateBError, OSError, ValueError) as exc:
        print(f"[CLPU-N2-GATE-B] ERROR: {exc}", file=sys.stderr)
        return 2

    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
