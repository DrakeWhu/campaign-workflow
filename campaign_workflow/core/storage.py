from __future__ import annotations

from pathlib import Path
from typing import Any

from campaign_workflow.core.atomic_io import read_json
from campaign_workflow.core.path_safety import (
    PathSafetyError,
    glob_existing_files_inside_case,
    safe_relative_posix,
)
from campaign_workflow.core.state import (
    get_state_layout,
    now_utc,
    validate_state_document,
    validate_validation_document,
)
from campaign_workflow.core.tsv_cases import CaseRecord


BYTES_PER_GB = 1024**3


def bytes_to_gb(value: int) -> float:
    return round(float(value) / BYTES_PER_GB, 6)


def build_storage_snapshot(
    *,
    campaign_root: Path,
    config: dict[str, Any],
    cases: list[CaseRecord],
) -> dict[str, Any]:
    """Build a campaign-level storage snapshot without modifying case data."""
    layout = get_state_layout(config)

    case_summaries: list[dict[str, Any]] = []
    cases_by_state: dict[str, int] = {}
    errors: list[dict[str, Any]] = []

    totals = {
        "case_total_bytes": 0,
        "raw_live_bytes": 0,
        "raw_delete_eligible_bytes": 0,
        "raw_deleted_bytes": 0,
        "safe_cleanup_candidate_bytes": 0,
    }

    for case in cases:
        summary = build_case_storage_summary(
            campaign_root=campaign_root,
            config=config,
            case=case,
            state_file=layout["state_file"],
            validation_file=layout["validation_file"],
        )
        case_summaries.append(summary)

        state = str(summary.get("state", "<unknown>"))
        cases_by_state[state] = cases_by_state.get(state, 0) + 1

        for key in totals:
            totals[key] += int(summary.get(key, 0))

        for error in summary.get("errors", []):
            errors.append(
                {
                    "case_id": case.case_id,
                    "case_name": case.case_name,
                    "error": error,
                }
            )

    validated_cases = sum(
        1
        for item in case_summaries
        if item.get("raw_validation_ok") and item.get("reduced_validation_ok")
    )
    submitted_cases = sum(
        1
        for item in case_summaries
        if item.get("state") not in {"Created", "<missing>", "<invalid>"}
    )

    avg_raw_per_case_bytes = int(totals["raw_live_bytes"] / len(cases)) if cases else 0

    return {
        "schema_version": 1,
        "snapshot_type": "campaign_storage",
        "created_at": now_utc(),
        "campaign_name": config.get("campaign_name"),
        "campaign_root": str(campaign_root),
        "case_count": len(cases),
        "validated_cases": validated_cases,
        "submitted_cases": submitted_cases,
        "cases_by_state": dict(sorted(cases_by_state.items())),
        "case_total_bytes": totals["case_total_bytes"],
        "case_total_GB": bytes_to_gb(totals["case_total_bytes"]),
        "raw_live_bytes": totals["raw_live_bytes"],
        "raw_live_GB": bytes_to_gb(totals["raw_live_bytes"]),
        "raw_delete_eligible_bytes": totals["raw_delete_eligible_bytes"],
        "raw_delete_eligible_GB": bytes_to_gb(totals["raw_delete_eligible_bytes"]),
        "raw_deleted_bytes": totals["raw_deleted_bytes"],
        "raw_deleted_GB": bytes_to_gb(totals["raw_deleted_bytes"]),
        "safe_cleanup_candidate_bytes": totals["safe_cleanup_candidate_bytes"],
        "safe_cleanup_candidate_GB": bytes_to_gb(totals["safe_cleanup_candidate_bytes"]),
        "avg_raw_per_case_bytes": avg_raw_per_case_bytes,
        "avg_raw_per_case_GB": bytes_to_gb(avg_raw_per_case_bytes),
        "cleanup_globs": cleanup_raw_delete_globs(config),
        "safe_quota_GB": storage_policy_number(config, "safe_quota_GB"),
        "reserved_quota_GB": storage_policy_number(config, "reserved_quota_GB"),
        "errors": errors,
        "cases": case_summaries,
        "destructive_operations": 0,
    }


def build_case_storage_summary(
    *,
    campaign_root: Path,
    config: dict[str, Any],
    case: CaseRecord,
    state_file: str,
    validation_file: str,
) -> dict[str, Any]:
    case_dir = campaign_root / case.case_name

    summary: dict[str, Any] = {
        "case_id": case.case_id,
        "case_name": case.case_name,
        "case_dir": str(case_dir),
        "state": "<missing>",
        "case_total_bytes": 0,
        "raw_live_bytes": 0,
        "raw_delete_eligible_bytes": 0,
        "raw_deleted_bytes": 0,
        "safe_cleanup_candidate_bytes": 0,
        "raw_validation_ok": False,
        "reduced_validation_ok": False,
        "cleanup_allowed": False,
        "cleanup_globs": cleanup_raw_delete_globs(config),
        "cleanup_files": [],
        "errors": [],
        "warnings": [],
    }

    if not case_dir.exists():
        summary["errors"].append(f"missing case directory: {case_dir}")
        return summary
    if not case_dir.is_dir():
        summary["state"] = "<invalid>"
        summary["errors"].append(f"case path is not a directory: {case_dir}")
        return summary

    try:
        summary["case_total_bytes"] = directory_size_bytes(case_dir)
    except Exception as exc:
        summary["errors"].append(f"failed to compute case directory size: {exc}")

    validation_doc: dict[str, Any] | None = None

    try:
        state_doc = read_json(case_dir / state_file)
        state_errors = validate_state_document(state_doc, case)
        if state_errors:
            summary["state"] = "<invalid>"
            summary["errors"].extend(state_errors)
        else:
            summary["state"] = state_doc.get("state", "<invalid>")
    except Exception as exc:
        summary["errors"].append(f"failed to read/validate state file: {exc}")

    try:
        validation_doc = read_json(case_dir / validation_file)
        validation_errors = validate_validation_document(validation_doc, case)
        if validation_errors:
            summary["errors"].extend(validation_errors)
        else:
            summary["raw_validation_ok"] = required_raw_evidence_ok(config, validation_doc)
            summary["reduced_validation_ok"] = required_reduced_evidence_ok(config, validation_doc)

            cleanup = validation_doc.get("cleanup", {})
            if isinstance(cleanup, dict):
                summary["cleanup_allowed"] = bool(cleanup.get("cleanup_allowed", False))
    except Exception as exc:
        summary["errors"].append(f"failed to read/validate validation file: {exc}")

    try:
        cleanup_files = resolve_cleanup_files(case_dir=case_dir, config=config)
        summary["cleanup_files"] = cleanup_files
        summary["raw_live_bytes"] = int(sum(int(item["size_bytes"]) for item in cleanup_files))
    except Exception as exc:
        summary["errors"].append(f"failed to resolve cleanup raw files: {exc}")

    if summary["state"] == "Raw_delete_eligible":
        summary["raw_delete_eligible_bytes"] = summary["raw_live_bytes"]

    if summary["state"] == "Raw_deleted":
        summary["raw_deleted_bytes"] = deleted_raw_bytes_from_post(case_dir)

    if (
        summary["raw_validation_ok"]
        and summary["reduced_validation_ok"]
        and summary["state"] != "Raw_deleted"
    ):
        summary["safe_cleanup_candidate_bytes"] = summary["raw_live_bytes"]

    return summary


def directory_size_bytes(root: Path) -> int:
    total = 0
    for path in root.rglob("*"):
        try:
            if path.is_file():
                safe_relative_posix(root, path)
                total += int(path.stat().st_size)
        except FileNotFoundError:
            continue
    return total


def cleanup_raw_delete_globs(config: dict[str, Any]) -> list[str]:
    cleanup = config.get("cleanup", {})
    if not isinstance(cleanup, dict):
        return []

    raw = cleanup.get("raw_delete_globs", [])
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise ValueError("cleanup.raw_delete_globs must be a list")

    globs: list[str] = []
    for item in raw:
        if not isinstance(item, str) or not item.strip():
            raise ValueError(f"cleanup.raw_delete_globs contains invalid item: {item!r}")
        globs.append(item.strip())

    return globs


def resolve_cleanup_files(*, case_dir: Path, config: dict[str, Any]) -> list[dict[str, Any]]:
    entries_by_path: dict[str, dict[str, Any]] = {}

    for raw_glob in cleanup_raw_delete_globs(config):
        matches = glob_existing_files_inside_case(case_dir, raw_glob)

        for path in matches:
            try:
                rel = safe_relative_posix(case_dir, path)
                stat = path.stat()
            except (FileNotFoundError, PathSafetyError):
                continue

            entries_by_path[rel] = {
                "relative_path": rel,
                "size_bytes": int(stat.st_size),
                "suffix": path.suffix,
                "matched_globs": sorted(
                    set(entries_by_path.get(rel, {}).get("matched_globs", []) + [raw_glob])
                ),
            }

    return [entries_by_path[key] for key in sorted(entries_by_path)]


def required_raw_evidence_ok(config: dict[str, Any], validation_doc: dict[str, Any]) -> bool:
    raw_diagnostics = config.get("raw_diagnostics")
    if not isinstance(raw_diagnostics, list) or not raw_diagnostics:
        return False

    raw_section = validation_doc.get("raw")
    if not isinstance(raw_section, dict):
        return False

    required_names: list[str] = []
    for diagnostic in raw_diagnostics:
        if not isinstance(diagnostic, dict):
            return False
        if not bool(diagnostic.get("required", True)):
            continue

        name = diagnostic.get("name")
        if not isinstance(name, str) or not name.strip():
            return False

        required_names.append(name.strip())

    if not required_names:
        return False

    for name in required_names:
        summary = raw_section.get(name)
        if not isinstance(summary, dict):
            return False
        if summary.get("ok") is not True:
            return False

        manifest_path = summary.get("manifest_path")
        if not isinstance(manifest_path, str) or not manifest_path.strip():
            return False

    return True


def required_reduced_evidence_ok(config: dict[str, Any], validation_doc: dict[str, Any]) -> bool:
    analysis = config.get("analysis")
    if not isinstance(analysis, dict):
        return False

    outputs = analysis.get("outputs")
    if not isinstance(outputs, list) or not outputs:
        return False

    reduced_section = validation_doc.get("reduced")
    if not isinstance(reduced_section, dict):
        return False

    required_names: list[str] = []
    for output in outputs:
        if not isinstance(output, dict):
            return False
        if not bool(output.get("required", True)):
            continue

        name = output.get("name")
        if not isinstance(name, str) or not name.strip():
            return False

        required_names.append(name.strip())

    if not required_names:
        return False

    for name in required_names:
        summary = reduced_section.get(name)
        if not isinstance(summary, dict):
            return False
        if summary.get("ok") is not True:
            return False

    return True


def deleted_raw_bytes_from_post(case_dir: Path) -> int:
    post_dir = case_dir / "post"
    candidates = [
        post_dir / "raw_deleted.json",
        post_dir / "cleanup_raw_deleted.json",
    ]

    for candidate in candidates:
        if not candidate.exists():
            continue

        try:
            doc = read_json(candidate)
        except Exception:
            continue

        for key in ["deleted_bytes", "total_size_bytes", "raw_deleted_bytes"]:
            value = doc.get(key)
            if isinstance(value, int):
                return int(value)

    return 0


def storage_policy_number(config: dict[str, Any], key: str) -> float | None:
    storage = config.get("storage", {})
    if not isinstance(storage, dict):
        return None

    value = storage.get(key)
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)

    raise ValueError(f"storage.{key} must be numeric")