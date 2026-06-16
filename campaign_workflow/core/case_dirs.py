from __future__ import annotations

from pathlib import Path
from typing import Any

from campaign_workflow.core.path_safety import PathSafetyError, validate_relative_path
from campaign_workflow.core.state import get_state_layout
from campaign_workflow.core.tsv_cases import CaseRecord

DEFAULT_EXTRA_CASE_SUBDIRS = ("diags", "checkpoints")


def _is_relative_to(path: Path, base: Path) -> bool:
    try:
        path.relative_to(base)
        return True
    except ValueError:
        return False


def _unique_preserving_order(items: list[str]) -> list[str]:
    seen: set[str] = set()
    unique: list[str] = []
    for item in items:
        if item not in seen:
            seen.add(item)
            unique.append(item)
    return unique


def get_standard_case_subdirs(config: dict[str, Any]) -> list[Path]:
    """Return the standard subdirectories expected inside a case directory.

    The state/workflow subdirectories follow the existing `state` layout in
    campaign.json. `diags` and `checkpoints` are generic runtime directories
    used by the campaign layout but are intentionally not tied to any WarpX
    input, diagnostic contract, analysis adapter, or cleanup policy.
    """
    layout = get_state_layout(config)

    raw_subdirs = [
        layout["logs_dir"],
        layout["post_dir"],
        layout["manifests_dir"],
        layout["locks_dir"],
        *DEFAULT_EXTRA_CASE_SUBDIRS,
    ]

    subdirs: list[Path] = []
    for raw_subdir in _unique_preserving_order([str(item) for item in raw_subdirs]):
        subdirs.append(validate_relative_path(raw_subdir, label="case subdirectory"))

    return subdirs


def resolve_case_dir_candidate(campaign_root: Path, case_name: str) -> Path:
    """Resolve a CASE_NAME against campaign_root without allowing escapes.

    The target directory may or may not exist. Existing symlinks are resolved;
    if they point outside the campaign root, the case is rejected.
    """
    relative_case_path = validate_relative_path(case_name, label="CASE_NAME")

    if relative_case_path == Path("."):
        raise PathSafetyError("CASE_NAME must not resolve to the campaign root")

    campaign_real = campaign_root.resolve(strict=True)
    candidate = (campaign_real / relative_case_path).resolve(strict=False)

    if not _is_relative_to(candidate, campaign_real):
        raise PathSafetyError(
            f"CASE_NAME resolves outside campaign root: raw={case_name!r}, "
            f"resolved={candidate}, campaign_root={campaign_real}"
        )

    return candidate


def build_case_dir_plan(
    *,
    campaign_root: Path,
    cases: list[CaseRecord],
    config: dict[str, Any],
) -> list[dict[str, Any]]:
    """Validate and build a non-destructive creation plan for all cases."""
    if not cases:
        raise ValueError("case manifest contains no cases")

    subdirs = get_standard_case_subdirs(config)

    plans: list[dict[str, Any]] = []
    for case in cases:
        case_dir = resolve_case_dir_candidate(campaign_root, case.case_name)
        plans.append(
            {
                "case_id": case.case_id,
                "case_name": case.case_name,
                "case_dir": case_dir,
                "subdirs": [case_dir / subdir for subdir in subdirs],
            }
        )

    return plans


def materialize_one_case_dir(*, plan: dict[str, Any], dry_run: bool) -> dict[str, Any]:
    """Create one case directory and standard subdirectories if requested.

    This function is intentionally non-destructive: it only calls mkdir with
    exist_ok=True, never writes files, never overwrites files, and never deletes
    anything.
    """
    case_dir: Path = plan["case_dir"]

    result: dict[str, Any] = {
        "case_id": plan["case_id"],
        "case_name": plan["case_name"],
        "case_dir": str(case_dir),
        "actions": [],
        "errors": [],
        "case_dir_created": 0,
        "case_dir_existing": 0,
        "subdirs_created": 0,
    }

    if case_dir.exists():
        if not case_dir.is_dir():
            result["errors"].append(f"case path exists but is not a directory: {case_dir}")
            return result
        result["case_dir_existing"] = 1
    else:
        result["actions"].append(f"create case directory: {case_dir}")
        result["case_dir_created"] = 1
        if not dry_run:
            case_dir.mkdir(parents=True, exist_ok=True)

    case_dir_real = None
    if case_dir.exists() and case_dir.is_dir():
        case_dir_real = case_dir.resolve(strict=True)

    for subdir in plan["subdirs"]:
        if subdir.exists():
            if not subdir.is_dir():
                result["errors"].append(f"case subdirectory path exists but is not a directory: {subdir}")
                continue
            if case_dir_real is not None:
                subdir_real = subdir.resolve(strict=True)
                if not _is_relative_to(subdir_real, case_dir_real):
                    result["errors"].append(
                        f"case subdirectory resolves outside case directory: {subdir} -> {subdir_real}"
                    )
            continue

        result["actions"].append(f"create case subdirectory: {subdir}")
        result["subdirs_created"] += 1
        if not dry_run:
            subdir.mkdir(parents=True, exist_ok=True)

    return result