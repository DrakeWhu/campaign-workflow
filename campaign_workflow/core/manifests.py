from __future__ import annotations

from pathlib import Path
from typing import Any

from campaign_workflow.core.path_safety import safe_relative_posix
from campaign_workflow.core.state import actor, now_utc


def now_utc_from_timestamp(timestamp: float) -> str:
    from datetime import datetime, timezone

    return datetime.fromtimestamp(timestamp, timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def file_manifest_entry(case_dir: Path, path: Path) -> dict[str, Any]:
    """Create a lightweight manifest entry for one raw file.

    No content hash is computed in V1 because raw WarpX/openPMD HDF5 files can be
    very large. Size, mtime and inode/device are enough for validation evidence
    and later manifest revalidation without extra heavy I/O.
    """
    stat = path.stat()

    entry: dict[str, Any] = {
        "relative_path": safe_relative_posix(case_dir, path),
        "size_bytes": int(stat.st_size),
        "mtime_ns": int(stat.st_mtime_ns),
        "mtime_utc": now_utc_from_timestamp(stat.st_mtime),
        "suffix": path.suffix,
    }

    if hasattr(stat, "st_dev"):
        entry["device"] = int(stat.st_dev)
    if hasattr(stat, "st_ino"):
        entry["inode"] = int(stat.st_ino)

    return entry


def build_raw_manifest(
    *,
    case_dir: Path,
    case_id: int,
    case_name: str,
    diagnostic_name: str,
    diagnostic_kind: str,
    files: list[Path],
    validation_summary: dict[str, Any],
) -> dict[str, Any]:
    """Build the raw manifest document for one diagnostic in one case."""
    entries = [file_manifest_entry(case_dir, path) for path in files]

    return {
        "schema_version": 1,
        "manifest_type": "raw_diagnostic",
        "created_at": now_utc(),
        "actor": actor(),
        "case_id": case_id,
        "case_name": case_name,
        "diagnostic_name": diagnostic_name,
        "diagnostic_kind": diagnostic_kind,
        "file_count": len(entries),
        "total_size_bytes": int(sum(entry["size_bytes"] for entry in entries)),
        "files": entries,
        "validation_summary": validation_summary,
        "destructive_operations": 0,
    }


def raw_manifest_filename(diagnostic_name: str) -> str:
    safe = diagnostic_name.replace("/", "_").replace("\\", "_").strip()
    if not safe:
        safe = "raw"
    return f"raw_{safe}.json"


def manifest_path(case_dir: Path, manifests_dir: str, diagnostic_name: str) -> Path:
    return case_dir / manifests_dir / raw_manifest_filename(diagnostic_name)