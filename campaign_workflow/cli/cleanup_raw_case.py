from __future__ import annotations

import argparse
import copy
import sys
from pathlib import Path
from typing import Any

from campaign_workflow.core.atomic_io import read_json, write_json_atomic
from campaign_workflow.core.manifests import file_manifest_entry
from campaign_workflow.core.path_safety import glob_existing_files_inside_case, safe_relative_posix
from campaign_workflow.core.state import (
    actor,
    get_state_layout,
    now_utc,
    validate_state_document,
    validate_validation_document,
)
from campaign_workflow.core.storage import (
    bytes_to_gb,
    cleanup_raw_delete_globs,
    required_raw_evidence_ok,
    required_reduced_evidence_ok,
)
from campaign_workflow.core.transitions import state_name
from campaign_workflow.core.tsv_cases import CaseRecord, load_campaign_config, load_cases


OPERATION = "cleanup_raw_case"
MANIFEST_FILENAME = "raw_delete_manifest.json"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Create raw cleanup dry-run manifests without deleting anything."
    )

    parser.add_argument(
        "--campaign-root",
        type=Path,
        default=Path("."),
        help="Campaign root containing campaign.json and the case manifest.",
    )

    parser.add_argument(
        "--case-id",
        type=int,
        action="append",
        default=None,
        help="Restrict operation to one case ID. Can be passed multiple times.",
    )

    parser.add_argument(
        "--dry-run",
        action="store_true",
        help=(
            "Write a dry-run delete manifest and update cleanup evidence. "
            "No files are deleted."
        ),
    )

    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Print per-case manifest details.",
    )

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    if not args.dry_run:
        print(
            "ERROR: Fase 6 only supports --dry-run. "
            "Deletion will be implemented later as a separate --execute phase.",
            file=sys.stderr,
        )
        return 2

    campaign_root = args.campaign_root.resolve()

    try:
        config = load_campaign_config(campaign_root)
        cases = load_cases(campaign_root, config)
    except Exception as exc:
        print(f"ERROR: failed to load campaign configuration/cases: {exc}", file=sys.stderr)
        return 1

    selected_ids = set(args.case_id or [])
    if selected_ids:
        known_ids = {case.case_id for case in cases}
        unknown = sorted(selected_ids - known_ids)
        if unknown:
            print(f"ERROR: requested unknown case IDs: {unknown}", file=sys.stderr)
            return 1
        cases = [case for case in cases if case.case_id in selected_ids]

    cases_ok = 0
    cases_with_errors = 0
    total_errors = 0
    total_manifest_bytes = 0
    total_manifest_files = 0

    print(f"campaign_root={campaign_root}")
    print(f"campaign_name={config.get('campaign_name')}")
    print(f"selected_cases={len(cases)}")

    for case in cases:
        result = cleanup_one_case_dry_run(
            campaign_root=campaign_root,
            config=config,
            case=case,
        )

        errors = result["errors"]
        total_errors += len(errors)

        if errors:
            cases_with_errors += 1
        if result["case_ok"]:
            cases_ok += 1
            total_manifest_bytes += int(result["manifest_total_size_bytes"])
            total_manifest_files += int(result["manifest_file_count"])

        if args.verbose or errors:
            print()
            print(f"[case {case.case_id}] {case.case_name}")
            print(f"  case_ok={result['case_ok']}")
            print(f"  state={result.get('state')}")
            print(f"  manifest_path={result.get('manifest_path')}")
            print(f"  manifest_files={result.get('manifest_file_count')}")
            print(f"  manifest_bytes={result.get('manifest_total_size_bytes')}")
            print(f"  manifest_GB={result.get('manifest_total_size_GB')}")
            print(f"  raw_validation_ok={result.get('raw_validation_ok')}")
            print(f"  reduced_validation_ok={result.get('reduced_validation_ok')}")
            print(f"  cleanup_allowed={result.get('cleanup_allowed')}")

            for action in result.get("actions", []):
                print(f"  OK: {action}")

            for warning in result.get("warnings", []):
                print(f"  WARNING: {warning}")

            for error in errors:
                print(f"  ERROR: {error}")

    print()
    print("=== SUMMARY ===")
    print(f"cases_processed={len(cases)}")
    print(f"cases_ok={cases_ok}")
    print(f"cases_with_errors={cases_with_errors}")
    print(f"errors={total_errors}")
    print(f"manifest_files={total_manifest_files}")
    print(f"manifest_bytes={total_manifest_bytes}")
    print(f"manifest_GB={bytes_to_gb(total_manifest_bytes)}")
    print("mode=dry-run-manifest")
    print("destructive_operations=0")

    return 1 if total_errors else 0


def cleanup_one_case_dry_run(
    *,
    campaign_root: Path,
    config: dict[str, Any],
    case: CaseRecord,
) -> dict[str, Any]:
    layout = get_state_layout(config)
    case_dir = campaign_root / case.case_name
    state_path = case_dir / layout["state_file"]
    validation_path = case_dir / layout["validation_file"]
    manifest_path = case_dir / layout["manifests_dir"] / MANIFEST_FILENAME
    eligibility_marker_path = case_dir / layout["post_dir"] / "raw_delete_eligible.json"

    result: dict[str, Any] = {
        "case_id": case.case_id,
        "case_name": case.case_name,
        "case_ok": False,
        "state": None,
        "manifest_path": str(manifest_path),
        "manifest_file_count": 0,
        "manifest_total_size_bytes": 0,
        "manifest_total_size_GB": 0.0,
        "raw_validation_ok": False,
        "reduced_validation_ok": False,
        "cleanup_allowed": False,
        "actions": [],
        "warnings": [],
        "errors": [],
    }

    if not case_dir.exists() or not case_dir.is_dir():
        result["errors"].append(f"missing case directory: {case_dir}")
        return result

    try:
        state_doc = read_json(state_path)
        validation_doc = read_json(validation_path)
    except Exception as exc:
        result["errors"].append(f"failed to read state/validation files: {exc}")
        return result

    state_errors = validate_state_document(state_doc, case)
    validation_errors = validate_validation_document(validation_doc, case)

    if state_errors or validation_errors:
        result["errors"].extend(state_errors)
        result["errors"].extend(validation_errors)
        return result

    try:
        current_state = state_name(state_doc)
    except Exception as exc:
        result["errors"].append(str(exc))
        return result

    result["state"] = current_state

    if current_state != "Raw_delete_eligible":
        result["errors"].append(
            f"cleanup dry-run requires state 'Raw_delete_eligible', got {current_state!r}"
        )
        return result

    raw_ok = required_raw_evidence_ok(config, validation_doc)
    reduced_ok = required_reduced_evidence_ok(config, validation_doc)

    result["raw_validation_ok"] = raw_ok
    result["reduced_validation_ok"] = reduced_ok

    if not raw_ok:
        result["errors"].append("required raw validation evidence is missing or not ok")
    if not reduced_ok:
        result["errors"].append("required reduced validation evidence is missing or not ok")

    legacy = validation_doc.get("legacy")
    if isinstance(legacy, dict) and legacy.get("legacy_reduced_only") is True:
        result["errors"].append("legacy reduced-only cases cannot produce raw cleanup manifests")

    cleanup = validation_doc.get("cleanup")
    if not isinstance(cleanup, dict):
        result["errors"].append("validation.json cleanup must be an object")
        return result

    cleanup_allowed = bool(cleanup.get("cleanup_allowed", False))
    result["cleanup_allowed"] = cleanup_allowed

    if not cleanup_allowed:
        result["errors"].append("cleanup.cleanup_allowed is false; run mark_raw_delete_eligible first")

    if cleanup.get("operation") != "mark_raw_delete_eligible":
        result["errors"].append("cleanup evidence was not produced by mark_raw_delete_eligible")

    if not eligibility_marker_path.exists():
        result["errors"].append(f"missing eligibility marker: {eligibility_marker_path}")

    eligibility_marker: dict[str, Any] | None = None
    if eligibility_marker_path.exists():
        try:
            eligibility_marker = read_json(eligibility_marker_path)
        except Exception as exc:
            result["errors"].append(f"failed to read eligibility marker: {exc}")

    paths = resolve_cleanup_paths(case_dir=case_dir, config=config)
    manifest_entries = [file_manifest_entry(case_dir, path) for path in paths]

    total_size_bytes = int(sum(int(entry["size_bytes"]) for entry in manifest_entries))
    file_count = len(manifest_entries)

    result["manifest_file_count"] = file_count
    result["manifest_total_size_bytes"] = total_size_bytes
    result["manifest_total_size_GB"] = bytes_to_gb(total_size_bytes)

    if file_count == 0:
        result["errors"].append("cleanup dry-run found no files to include in manifest")

    expected_count = cleanup.get("candidate_file_count")
    expected_bytes = cleanup.get("candidate_total_size_bytes")

    if not isinstance(expected_count, int):
        result["errors"].append("cleanup.candidate_file_count must be present and integer")
    elif expected_count != file_count:
        result["errors"].append(
            f"cleanup candidate file count mismatch: eligibility={expected_count}, current={file_count}"
        )

    if not isinstance(expected_bytes, int):
        result["errors"].append("cleanup.candidate_total_size_bytes must be present and integer")
    elif expected_bytes != total_size_bytes:
        result["errors"].append(
            f"cleanup candidate byte mismatch: eligibility={expected_bytes}, current={total_size_bytes}"
        )

    if eligibility_marker is not None:
        marker_count = eligibility_marker.get("candidate_file_count")
        marker_bytes = eligibility_marker.get("candidate_total_size_bytes")

        if marker_count != file_count:
            result["errors"].append(
                f"eligibility marker file count mismatch: marker={marker_count}, current={file_count}"
            )

        if marker_bytes != total_size_bytes:
            result["errors"].append(
                f"eligibility marker byte mismatch: marker={marker_bytes}, current={total_size_bytes}"
            )

        if eligibility_marker.get("cleanup_requires_dry_run_manifest") is not True:
            result["errors"].append("eligibility marker does not require dry-run manifest")

        if eligibility_marker.get("cleanup_requires_explicit_execute") is not True:
            result["errors"].append("eligibility marker does not require explicit execute")

    if result["errors"]:
        return result

    manifest = build_raw_delete_manifest(
        case=case,
        config=config,
        state_doc=state_doc,
        validation_doc=validation_doc,
        manifest_entries=manifest_entries,
        total_size_bytes=total_size_bytes,
    )

    updated_validation = copy.deepcopy(validation_doc)
    updated_cleanup = updated_validation.setdefault("cleanup", {})
    if not isinstance(updated_cleanup, dict):
        updated_validation["cleanup"] = {}
        updated_cleanup = updated_validation["cleanup"]

    updated_cleanup.update(
        {
            "cleanup_allowed": True,
            "delete_manifest_ready": True,
            "delete_manifest_created_at": manifest["created_at"],
            "delete_manifest_path": f"{layout['manifests_dir']}/{MANIFEST_FILENAME}",
            "delete_manifest_file_count": file_count,
            "delete_manifest_total_size_bytes": total_size_bytes,
            "delete_manifest_total_size_GB": bytes_to_gb(total_size_bytes),
            "delete_manifest_mode": "dry-run",
            "execute_required": True,
            "destructive_operations": 0,
            "reason": (
                "Dry-run cleanup manifest was created. "
                "Raw files may only be deleted by the later explicit execute phase."
            ),
        }
    )
    updated_validation["updated_at"] = now_utc()

    write_json_atomic(manifest_path, manifest)
    write_json_atomic(validation_path, updated_validation)

    result["case_ok"] = True
    result["actions"].append(f"write dry-run delete manifest: {manifest_path}")
    result["actions"].append(f"update cleanup manifest evidence: {validation_path}")
    result["actions"].append("delete no files")

    return result


def resolve_cleanup_paths(*, case_dir: Path, config: dict[str, Any]) -> list[Path]:
    paths_by_rel: dict[str, Path] = {}

    for raw_glob in cleanup_raw_delete_globs(config):
        for path in glob_existing_files_inside_case(case_dir, raw_glob):
            rel = safe_relative_posix(case_dir, path)
            paths_by_rel[rel] = path

    return [paths_by_rel[key] for key in sorted(paths_by_rel)]


def build_raw_delete_manifest(
    *,
    case: CaseRecord,
    config: dict[str, Any],
    state_doc: dict[str, Any],
    validation_doc: dict[str, Any],
    manifest_entries: list[dict[str, Any]],
    total_size_bytes: int,
) -> dict[str, Any]:
    cleanup_globs = cleanup_raw_delete_globs(config)

    return {
        "schema_version": 1,
        "manifest_type": "raw_cleanup_dry_run",
        "created_at": now_utc(),
        "actor": actor(),
        "operation": OPERATION,
        "case_id": case.case_id,
        "case_name": case.case_name,
        "source_state": state_doc.get("state"),
        "delete_globs": cleanup_globs,
        "file_count": len(manifest_entries),
        "total_size_bytes": total_size_bytes,
        "total_size_GB": bytes_to_gb(total_size_bytes),
        "files": manifest_entries,
        "eligibility_evidence": {
            "raw_validation_ok": required_raw_evidence_ok(config, validation_doc),
            "reduced_validation_ok": required_reduced_evidence_ok(config, validation_doc),
            "cleanup_allowed": bool(validation_doc.get("cleanup", {}).get("cleanup_allowed", False)),
            "cleanup_operation": validation_doc.get("cleanup", {}).get("operation"),
            "eligibility_marker_path": validation_doc.get("cleanup", {}).get("eligibility_marker_path"),
        },
        "execute_allowed": True,
        "execute_requires_revalidation": True,
        "execute_must_delete_only_manifest_files": True,
        "directory_delete_allowed": False,
        "destructive_operations": 0,
    }


if __name__ == "__main__":
    raise SystemExit(main())