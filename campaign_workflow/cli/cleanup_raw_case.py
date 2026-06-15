from __future__ import annotations

import argparse
import copy
import sys
from pathlib import Path
from typing import Any

from campaign_workflow.core.atomic_io import read_json, write_json_atomic
from campaign_workflow.core.manifests import file_manifest_entry
from campaign_workflow.core.path_safety import (
    glob_existing_files_inside_case,
    safe_relative_posix,
    validate_relative_path,
)
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
from campaign_workflow.core.transitions import raw_deleted_transition, state_name
from campaign_workflow.core.tsv_cases import CaseRecord, load_campaign_config, load_cases


OPERATION = "cleanup_raw_case"
MANIFEST_FILENAME = "raw_delete_manifest.json"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Create raw cleanup dry-run manifests or execute validated raw cleanup."
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

    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument(
        "--dry-run",
        action="store_true",
        help=(
            "Write a dry-run delete manifest and update cleanup evidence. "
            "No files are deleted."
        ),
    )
    mode.add_argument(
        "--execute",
        action="store_true",
        help=(
            "Delete only files listed in an existing validated dry-run manifest. "
            "No directories are deleted."
        ),
    )

    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Print per-case cleanup details.",
    )

    return parser


def main(argv: list[str] | None = None) -> int:
    try:
        args = build_parser().parse_args(argv)
    except SystemExit as exc:
        if isinstance(exc.code, int):
            return exc.code
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
    total_files = 0
    total_bytes = 0

    mode_name = "dry-run-manifest" if args.dry_run else "execute"

    print(f"campaign_root={campaign_root}")
    print(f"campaign_name={config.get('campaign_name')}")
    print(f"selected_cases={len(cases)}")

    for case in cases:
        if args.dry_run:
            result = cleanup_one_case_dry_run(
                campaign_root=campaign_root,
                config=config,
                case=case,
            )
        else:
            result = cleanup_one_case_execute(
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
            total_files += int(result.get("file_count", result.get("manifest_file_count", 0)))
            total_bytes += int(result.get("total_size_bytes", result.get("manifest_total_size_bytes", 0)))

        if args.verbose or errors:
            print()
            print(f"[case {case.case_id}] {case.case_name}")
            print(f"  case_ok={result['case_ok']}")
            print(f"  state={result.get('state')}")
            print(f"  target_state={result.get('target_state')}")
            print(f"  manifest_path={result.get('manifest_path')}")
            print(f"  files={result.get('file_count', result.get('manifest_file_count'))}")
            print(f"  bytes={result.get('total_size_bytes', result.get('manifest_total_size_bytes'))}")
            print(f"  GB={result.get('total_size_GB', result.get('manifest_total_size_GB'))}")
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
    print(f"files={total_files}")
    print(f"bytes={total_bytes}")
    print(f"GB={bytes_to_gb(total_bytes)}")
    print(f"mode={mode_name}")
    print(f"destructive_operations={total_files if args.execute and total_errors == 0 else 0}")

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
        "target_state": "Raw_delete_eligible",
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

    loaded = load_and_validate_cleanup_context(
        campaign_root=campaign_root,
        config=config,
        case=case,
        require_manifest=False,
    )
    result.update(loaded["result_fields"])

    if loaded["errors"]:
        result["errors"].extend(loaded["errors"])
        return result

    case_dir = loaded["case_dir"]
    state_doc = loaded["state_doc"]
    validation_doc = loaded["validation_doc"]

    current_state = state_name(state_doc)
    result["state"] = current_state

    if current_state != "Raw_delete_eligible":
        result["errors"].append(
            f"cleanup dry-run requires state 'Raw_delete_eligible', got {current_state!r}"
        )
        return result

    eligibility_errors = validate_cleanup_eligibility_common(
        config=config,
        case_dir=case_dir,
        validation_doc=validation_doc,
        eligibility_marker_path=eligibility_marker_path,
    )
    result["errors"].extend(eligibility_errors)

    paths = resolve_cleanup_paths(case_dir=case_dir, config=config)
    manifest_entries = [file_manifest_entry(case_dir, path) for path in paths]

    total_size_bytes = int(sum(int(entry["size_bytes"]) for entry in manifest_entries))
    file_count = len(manifest_entries)

    result["manifest_file_count"] = file_count
    result["manifest_total_size_bytes"] = total_size_bytes
    result["manifest_total_size_GB"] = bytes_to_gb(total_size_bytes)

    if file_count == 0:
        result["errors"].append("cleanup dry-run found no files to include in manifest")

    result["errors"].extend(
        validate_candidate_count_and_bytes(
            validation_doc=validation_doc,
            file_count=file_count,
            total_size_bytes=total_size_bytes,
            eligibility_marker_path=eligibility_marker_path,
        )
    )

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


def cleanup_one_case_execute(
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
    deleted_marker_path = case_dir / layout["post_dir"] / "raw_deleted.json"
    eligibility_marker_path = case_dir / layout["post_dir"] / "raw_delete_eligible.json"

    result: dict[str, Any] = {
        "case_id": case.case_id,
        "case_name": case.case_name,
        "case_ok": False,
        "state": None,
        "target_state": "Raw_deleted",
        "manifest_path": str(manifest_path),
        "file_count": 0,
        "total_size_bytes": 0,
        "total_size_GB": 0.0,
        "raw_validation_ok": False,
        "reduced_validation_ok": False,
        "cleanup_allowed": False,
        "actions": [],
        "warnings": [],
        "errors": [],
    }

    loaded = load_and_validate_cleanup_context(
        campaign_root=campaign_root,
        config=config,
        case=case,
        require_manifest=True,
    )
    result.update(loaded["result_fields"])

    if loaded["errors"]:
        result["errors"].extend(loaded["errors"])
        return result

    case_dir = loaded["case_dir"]
    state_doc = loaded["state_doc"]
    validation_doc = loaded["validation_doc"]
    manifest = loaded["manifest"]

    current_state = state_name(state_doc)
    result["state"] = current_state

    if current_state != "Raw_delete_eligible":
        result["errors"].append(
            f"cleanup execute requires state 'Raw_delete_eligible', got {current_state!r}"
        )
        return result

    result["errors"].extend(
        validate_cleanup_eligibility_common(
            config=config,
            case_dir=case_dir,
            validation_doc=validation_doc,
            eligibility_marker_path=eligibility_marker_path,
        )
    )

    manifest_validation = validate_execute_manifest(
        case=case,
        case_dir=case_dir,
        manifest=manifest,
        validation_doc=validation_doc,
    )
    result["errors"].extend(manifest_validation["errors"])

    file_paths = manifest_validation["file_paths"]
    file_count = len(file_paths)
    total_size_bytes = int(manifest_validation["total_size_bytes"])

    result["file_count"] = file_count
    result["total_size_bytes"] = total_size_bytes
    result["total_size_GB"] = bytes_to_gb(total_size_bytes)

    result["errors"].extend(
        validate_candidate_count_and_bytes(
            validation_doc=validation_doc,
            file_count=file_count,
            total_size_bytes=total_size_bytes,
            eligibility_marker_path=eligibility_marker_path,
        )
    )

    if result["errors"]:
        return result

    deleted_entries: list[dict[str, Any]] = []

    try:
        for path in file_paths:
            rel = safe_relative_posix(case_dir, path)
            size = int(path.stat().st_size)

            if not path.is_file():
                raise ValueError(f"manifest path is not a regular file before deletion: {rel}")

            path.unlink()

            deleted_entries.append(
                {
                    "relative_path": rel,
                    "size_bytes": size,
                    "deleted_at": now_utc(),
                }
            )
    except Exception as exc:
        result["errors"].append(f"deletion failed before completion: {exc}")
        result["warnings"].append(
            "Some files may already have been deleted. Inspect the manifest and case directory before retrying."
        )
        return result

    deleted_total_bytes = int(sum(int(item["size_bytes"]) for item in deleted_entries))
    deleted_file_count = len(deleted_entries)

    deleted_marker = build_raw_deleted_marker(
        case=case,
        manifest=manifest,
        deleted_entries=deleted_entries,
        total_size_bytes=deleted_total_bytes,
    )

    updated_validation = copy.deepcopy(validation_doc)
    cleanup = updated_validation.setdefault("cleanup", {})
    if not isinstance(cleanup, dict):
        updated_validation["cleanup"] = {}
        cleanup = updated_validation["cleanup"]

    cleanup.update(
        {
            "cleanup_allowed": False,
            "raw_deleted": True,
            "raw_deleted_at": deleted_marker["created_at"],
            "raw_deleted_marker_path": f"{layout['post_dir']}/raw_deleted.json",
            "deleted_file_count": deleted_file_count,
            "deleted_total_size_bytes": deleted_total_bytes,
            "deleted_total_size_GB": bytes_to_gb(deleted_total_bytes),
            "delete_manifest_path": f"{layout['manifests_dir']}/{MANIFEST_FILENAME}",
            "delete_manifest_mode": "executed",
            "execute_required": False,
            "destructive_operations": deleted_file_count,
            "reason": "Raw cleanup executed from validated dry-run manifest.",
        }
    )
    updated_validation["updated_at"] = now_utc()

    next_state = raw_deleted_transition(state_doc)

    write_json_atomic(deleted_marker_path, deleted_marker)
    write_json_atomic(validation_path, updated_validation)
    write_json_atomic(state_path, next_state)

    result["case_ok"] = True
    result["actions"].append(f"deleted manifest-listed files: {deleted_file_count}")
    result["actions"].append(f"write raw deleted marker: {deleted_marker_path}")
    result["actions"].append(f"update cleanup execution evidence: {validation_path}")
    result["actions"].append(f"transition state to Raw_deleted: {state_path}")

    return result


def load_and_validate_cleanup_context(
    *,
    campaign_root: Path,
    config: dict[str, Any],
    case: CaseRecord,
    require_manifest: bool,
) -> dict[str, Any]:
    layout = get_state_layout(config)
    case_dir = campaign_root / case.case_name
    state_path = case_dir / layout["state_file"]
    validation_path = case_dir / layout["validation_file"]
    manifest_path = case_dir / layout["manifests_dir"] / MANIFEST_FILENAME

    result_fields = {
        "raw_validation_ok": False,
        "reduced_validation_ok": False,
        "cleanup_allowed": False,
    }

    result: dict[str, Any] = {
        "case_dir": case_dir,
        "state_doc": None,
        "validation_doc": None,
        "manifest": None,
        "result_fields": result_fields,
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

    raw_ok = required_raw_evidence_ok(config, validation_doc)
    reduced_ok = required_reduced_evidence_ok(config, validation_doc)

    cleanup = validation_doc.get("cleanup", {})
    cleanup_allowed = bool(cleanup.get("cleanup_allowed", False)) if isinstance(cleanup, dict) else False

    result_fields["raw_validation_ok"] = raw_ok
    result_fields["reduced_validation_ok"] = reduced_ok
    result_fields["cleanup_allowed"] = cleanup_allowed

    result["state_doc"] = state_doc
    result["validation_doc"] = validation_doc

    if require_manifest:
        if not manifest_path.exists():
            result["errors"].append(f"missing dry-run delete manifest: {manifest_path}")
        else:
            try:
                result["manifest"] = read_json(manifest_path)
            except Exception as exc:
                result["errors"].append(f"failed to read dry-run delete manifest: {exc}")

    return result


def validate_cleanup_eligibility_common(
    *,
    config: dict[str, Any],
    case_dir: Path,
    validation_doc: dict[str, Any],
    eligibility_marker_path: Path,
) -> list[str]:
    errors: list[str] = []

    raw_ok = required_raw_evidence_ok(config, validation_doc)
    reduced_ok = required_reduced_evidence_ok(config, validation_doc)

    if not raw_ok:
        errors.append("required raw validation evidence is missing or not ok")
    if not reduced_ok:
        errors.append("required reduced validation evidence is missing or not ok")

    legacy = validation_doc.get("legacy")
    if isinstance(legacy, dict) and legacy.get("legacy_reduced_only") is True:
        errors.append("legacy reduced-only cases cannot produce or execute raw cleanup manifests")

    cleanup = validation_doc.get("cleanup")
    if not isinstance(cleanup, dict):
        errors.append("validation.json cleanup must be an object")
        return errors

    if not bool(cleanup.get("cleanup_allowed", False)):
        errors.append("cleanup.cleanup_allowed is false; run mark_raw_delete_eligible first")

    if cleanup.get("operation") != "mark_raw_delete_eligible":
        errors.append("cleanup evidence was not produced by mark_raw_delete_eligible")

    if not eligibility_marker_path.exists():
        errors.append(f"missing eligibility marker: {eligibility_marker_path}")
        return errors

    try:
        marker = read_json(eligibility_marker_path)
    except Exception as exc:
        errors.append(f"failed to read eligibility marker: {exc}")
        return errors

    if marker.get("cleanup_requires_dry_run_manifest") is not True:
        errors.append("eligibility marker does not require dry-run manifest")

    if marker.get("cleanup_requires_explicit_execute") is not True:
        errors.append("eligibility marker does not require explicit execute")

    if marker.get("cleanup_allowed") is not True:
        errors.append("eligibility marker cleanup_allowed is not true")

    if marker.get("state") != "Raw_delete_eligible":
        errors.append("eligibility marker state is not Raw_delete_eligible")

    return errors


def validate_candidate_count_and_bytes(
    *,
    validation_doc: dict[str, Any],
    file_count: int,
    total_size_bytes: int,
    eligibility_marker_path: Path,
) -> list[str]:
    errors: list[str] = []

    cleanup = validation_doc.get("cleanup", {})
    if not isinstance(cleanup, dict):
        return ["validation.json cleanup must be an object"]

    expected_count = cleanup.get("candidate_file_count")
    expected_bytes = cleanup.get("candidate_total_size_bytes")

    if not isinstance(expected_count, int):
        errors.append("cleanup.candidate_file_count must be present and integer")
    elif expected_count != file_count:
        errors.append(
            f"cleanup candidate file count mismatch: eligibility={expected_count}, current={file_count}"
        )

    if not isinstance(expected_bytes, int):
        errors.append("cleanup.candidate_total_size_bytes must be present and integer")
    elif expected_bytes != total_size_bytes:
        errors.append(
            f"cleanup candidate byte mismatch: eligibility={expected_bytes}, current={total_size_bytes}"
        )

    if eligibility_marker_path.exists():
        try:
            marker = read_json(eligibility_marker_path)
        except Exception as exc:
            errors.append(f"failed to read eligibility marker: {exc}")
            return errors

        marker_count = marker.get("candidate_file_count")
        marker_bytes = marker.get("candidate_total_size_bytes")

        if marker_count != file_count:
            errors.append(
                f"eligibility marker file count mismatch: marker={marker_count}, current={file_count}"
            )

        if marker_bytes != total_size_bytes:
            errors.append(
                f"eligibility marker byte mismatch: marker={marker_bytes}, current={total_size_bytes}"
            )

    return errors


def validate_execute_manifest(
    *,
    case: CaseRecord,
    case_dir: Path,
    manifest: dict[str, Any],
    validation_doc: dict[str, Any],
) -> dict[str, Any]:
    errors: list[str] = []
    file_paths: list[Path] = []
    seen: set[str] = set()
    total_size_bytes = 0

    if manifest.get("manifest_type") != "raw_cleanup_dry_run":
        errors.append("manifest_type must be raw_cleanup_dry_run")

    if manifest.get("operation") != OPERATION:
        errors.append(f"manifest operation must be {OPERATION!r}")

    if manifest.get("case_id") != case.case_id:
        errors.append(f"manifest case_id mismatch: {manifest.get('case_id')} != {case.case_id}")

    if manifest.get("case_name") != case.case_name:
        errors.append(f"manifest case_name mismatch: {manifest.get('case_name')} != {case.case_name}")

    if manifest.get("source_state") != "Raw_delete_eligible":
        errors.append("manifest source_state must be Raw_delete_eligible")

    if manifest.get("execute_allowed") is not True:
        errors.append("manifest execute_allowed must be true")

    if manifest.get("execute_requires_revalidation") is not True:
        errors.append("manifest execute_requires_revalidation must be true")

    if manifest.get("execute_must_delete_only_manifest_files") is not True:
        errors.append("manifest execute_must_delete_only_manifest_files must be true")

    if manifest.get("directory_delete_allowed") is not False:
        errors.append("manifest directory_delete_allowed must be false")

    if manifest.get("destructive_operations") != 0:
        errors.append("dry-run manifest destructive_operations must be 0")

    evidence = manifest.get("eligibility_evidence")
    if not isinstance(evidence, dict):
        errors.append("manifest eligibility_evidence must be an object")
    else:
        if evidence.get("raw_validation_ok") is not True:
            errors.append("manifest raw_validation_ok evidence is not true")
        if evidence.get("reduced_validation_ok") is not True:
            errors.append("manifest reduced_validation_ok evidence is not true")
        if evidence.get("cleanup_allowed") is not True:
            errors.append("manifest cleanup_allowed evidence is not true")
        if evidence.get("cleanup_operation") != "mark_raw_delete_eligible":
            errors.append("manifest cleanup_operation evidence is not mark_raw_delete_eligible")

    files = manifest.get("files")
    if not isinstance(files, list) or not files:
        errors.append("manifest files must be a non-empty list")
        files = []

    for index, entry in enumerate(files):
        if not isinstance(entry, dict):
            errors.append(f"manifest file entry {index} must be an object")
            continue

        rel_raw = entry.get("relative_path")
        try:
            rel_path = validate_relative_path(rel_raw, label=f"manifest file entry {index}")
        except Exception as exc:
            errors.append(str(exc))
            continue

        rel = rel_path.as_posix()
        if rel in seen:
            errors.append(f"duplicate manifest relative_path: {rel}")
            continue
        seen.add(rel)

        path = case_dir / rel_path

        try:
            resolved_rel = safe_relative_posix(case_dir, path)
        except Exception as exc:
            errors.append(f"manifest path is not safely inside case dir: {rel}: {exc}")
            continue

        if resolved_rel != rel:
            errors.append(f"manifest relative path normalization mismatch: {rel} != {resolved_rel}")
            continue

        if not path.exists():
            errors.append(f"manifest file does not exist: {rel}")
            continue

        if not path.is_file():
            errors.append(f"manifest path is not a regular file: {rel}")
            continue

        size = int(path.stat().st_size)
        expected_size = entry.get("size_bytes")
        if not isinstance(expected_size, int):
            errors.append(f"manifest file {rel} has non-integer size_bytes")
            continue

        if expected_size != size:
            errors.append(f"manifest file size mismatch for {rel}: manifest={expected_size}, current={size}")
            continue

        file_paths.append(path)
        total_size_bytes += size

    manifest_count = manifest.get("file_count")
    manifest_bytes = manifest.get("total_size_bytes")

    if manifest_count != len(file_paths):
        errors.append(f"manifest file_count mismatch: manifest={manifest_count}, validated={len(file_paths)}")

    if manifest_bytes != total_size_bytes:
        errors.append(f"manifest total_size_bytes mismatch: manifest={manifest_bytes}, validated={total_size_bytes}")

    cleanup = validation_doc.get("cleanup", {})
    if isinstance(cleanup, dict):
        ready = cleanup.get("delete_manifest_ready")
        mode = cleanup.get("delete_manifest_mode")
        manifest_path = cleanup.get("delete_manifest_path")

        if ready is not True:
            errors.append("validation cleanup.delete_manifest_ready is not true")
        if mode != "dry-run":
            errors.append("validation cleanup.delete_manifest_mode is not dry-run")
        if manifest_path != f"manifests/{MANIFEST_FILENAME}":
            errors.append("validation cleanup.delete_manifest_path does not match expected manifest path")
    else:
        errors.append("validation cleanup must be an object")

    return {
        "errors": errors,
        "file_paths": file_paths,
        "total_size_bytes": total_size_bytes,
    }


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


def build_raw_deleted_marker(
    *,
    case: CaseRecord,
    manifest: dict[str, Any],
    deleted_entries: list[dict[str, Any]],
    total_size_bytes: int,
) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "created_at": now_utc(),
        "operation": OPERATION,
        "case_id": case.case_id,
        "case_name": case.case_name,
        "source_manifest_type": manifest.get("manifest_type"),
        "source_manifest_created_at": manifest.get("created_at"),
        "deleted_file_count": len(deleted_entries),
        "deleted_total_size_bytes": total_size_bytes,
        "deleted_total_size_GB": bytes_to_gb(total_size_bytes),
        "deleted_files": deleted_entries,
        "directory_delete_allowed": False,
        "destructive_operations": len(deleted_entries),
    }


if __name__ == "__main__":
    raise SystemExit(main())