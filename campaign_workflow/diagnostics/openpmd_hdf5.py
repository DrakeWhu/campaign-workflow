from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from campaign_workflow.core.manifests import build_raw_manifest
from campaign_workflow.core.path_safety import PathSafetyError, glob_existing_files_inside_case, safe_relative_posix


DEFAULT_ALLOWED_SUFFIXES = {
    "fake": [".fake"],
    "openpmd_hdf5": [".h5", ".hdf5"],
}

SUPPORTED_RAW_DIAGNOSTIC_KINDS = set(DEFAULT_ALLOWED_SUFFIXES)


def validate_raw_diagnostic(
    *,
    case_dir: Path,
    case_id: int,
    case_name: str,
    diagnostic: dict[str, Any],
) -> dict[str, Any]:
    """Validate one configured raw diagnostic for one case.

    The adapter is intentionally generic: it checks file discovery, path safety,
    file age/size/suffix, and optionally HDF5 readability for openPMD/HDF5 raw
    diagnostics. It does not interpret capillary/guiding physics.
    """
    name = _required_string(diagnostic, "name")
    kind = _required_string(diagnostic, "kind")

    if kind not in SUPPORTED_RAW_DIAGNOSTIC_KINDS:
        return _failure(
            diagnostic_name=name,
            diagnostic_kind=kind,
            errors=[f"unsupported raw diagnostic kind: {kind!r}"],
        )

    raw_glob = _required_string(diagnostic, "glob")
    min_files = _nonnegative_int(diagnostic.get("min_files", 1), "min_files")
    min_age_seconds = _nonnegative_float(diagnostic.get("min_age_seconds", 0), "min_age_seconds")
    allowed_suffixes = _allowed_suffixes(diagnostic, kind)

    errors: list[str] = []
    warnings: list[str] = []
    files: list[Path] = []

    try:
        files = glob_existing_files_inside_case(case_dir, raw_glob)
    except PathSafetyError as exc:
        errors.append(str(exc))
    except Exception as exc:
        errors.append(f"failed to resolve diagnostic glob {raw_glob!r}: {exc}")

    if len(files) < min_files:
        errors.append(f"diagnostic {name!r} requires at least {min_files} file(s), found {len(files)}")

    now = time.time()
    file_entries: list[dict[str, Any]] = []

    for path in files:
        try:
            entry_errors, entry_warnings, entry = _validate_one_file(
                case_dir=case_dir,
                path=path,
                allowed_suffixes=allowed_suffixes,
                min_age_seconds=min_age_seconds,
                now=now,
            )
            errors.extend(entry_errors)
            warnings.extend(entry_warnings)
            file_entries.append(entry)
        except Exception as exc:
            errors.append(f"failed to validate file {path}: {exc}")

    if kind == "openpmd_hdf5" and not errors:
        hdf5_errors, hdf5_warnings = _validate_hdf5_readability(files)
        errors.extend(hdf5_errors)
        warnings.extend(hdf5_warnings)

    ok = not errors
    total_size_bytes = int(sum(entry.get("size_bytes", 0) for entry in file_entries))

    summary: dict[str, Any] = {
        "schema_version": 1,
        "diagnostic_name": name,
        "diagnostic_kind": kind,
        "ok": ok,
        "validated_at": _now_utc(),
        "glob": raw_glob,
        "min_files": min_files,
        "min_age_seconds": min_age_seconds,
        "allowed_suffixes": allowed_suffixes,
        "file_count": len(files),
        "total_size_bytes": total_size_bytes,
        "files": file_entries,
        "errors": errors,
        "warnings": warnings,
    }

    manifest = None
    if ok:
        manifest = build_raw_manifest(
            case_dir=case_dir,
            case_id=case_id,
            case_name=case_name,
            diagnostic_name=name,
            diagnostic_kind=kind,
            files=files,
            validation_summary={
                "ok": ok,
                "file_count": len(files),
                "total_size_bytes": total_size_bytes,
                "errors": [],
                "warnings": warnings,
            },
        )

    return {
        "ok": ok,
        "summary": summary,
        "manifest": manifest,
        "files": files,
        "errors": errors,
        "warnings": warnings,
    }


def _validate_one_file(
    *,
    case_dir: Path,
    path: Path,
    allowed_suffixes: list[str],
    min_age_seconds: float,
    now: float,
) -> tuple[list[str], list[str], dict[str, Any]]:
    errors: list[str] = []
    warnings: list[str] = []

    relative_path = safe_relative_posix(case_dir, path)
    stat = path.stat()
    suffix = path.suffix
    age_seconds = max(0.0, now - stat.st_mtime)

    if not path.is_file():
        errors.append(f"not a regular file: {relative_path}")

    if suffix not in allowed_suffixes:
        errors.append(f"invalid suffix for {relative_path}: {suffix!r}; allowed={allowed_suffixes}")

    if stat.st_size <= 0:
        errors.append(f"empty file: {relative_path}")

    if age_seconds < min_age_seconds:
        errors.append(
            f"file is too recent: {relative_path}; age_seconds={age_seconds:.3f}, "
            f"min_age_seconds={min_age_seconds:.3f}"
        )

    entry = {
        "relative_path": relative_path,
        "size_bytes": int(stat.st_size),
        "mtime_ns": int(stat.st_mtime_ns),
        "suffix": suffix,
        "age_seconds": age_seconds,
        "is_symlink": path.is_symlink(),
    }

    return errors, warnings, entry


def _validate_hdf5_readability(files: list[Path]) -> tuple[list[str], list[str]]:
    errors: list[str] = []
    warnings: list[str] = []

    try:
        import h5py  # type: ignore[import-not-found]
    except Exception as exc:
        return [f"h5py is required to validate openpmd_hdf5 files: {exc}"], warnings

    for path in files:
        try:
            with h5py.File(path, "r") as h5:
                # This intentionally performs only a minimal read/open check.
                # Strict openPMD semantic validation can be added later without
                # changing the core workflow contract.
                _ = list(h5.keys())
        except Exception as exc:
            errors.append(f"failed to open HDF5 file {path.name!r}: {exc}")

    return errors, warnings


def _allowed_suffixes(diagnostic: dict[str, Any], kind: str) -> list[str]:
    raw = diagnostic.get("allowed_suffixes", DEFAULT_ALLOWED_SUFFIXES[kind])
    if not isinstance(raw, list) or not raw:
        raise ValueError("allowed_suffixes must be a non-empty list")

    suffixes: list[str] = []
    for item in raw:
        if not isinstance(item, str) or not item.startswith("."):
            raise ValueError(f"invalid suffix in allowed_suffixes: {item!r}")
        suffixes.append(item)

    return suffixes


def _required_string(data: dict[str, Any], key: str) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"diagnostic field {key!r} must be a non-empty string")
    return value.strip()


def _nonnegative_int(value: Any, label: str) -> int:
    if isinstance(value, bool):
        raise ValueError(f"{label} must be an integer, got boolean")
    try:
        parsed = int(value)
    except Exception as exc:
        raise ValueError(f"{label} must be an integer: {value!r}") from exc
    if parsed < 0:
        raise ValueError(f"{label} must be non-negative: {parsed}")
    return parsed


def _nonnegative_float(value: Any, label: str) -> float:
    if isinstance(value, bool):
        raise ValueError(f"{label} must be a number, got boolean")
    try:
        parsed = float(value)
    except Exception as exc:
        raise ValueError(f"{label} must be a number: {value!r}") from exc
    if parsed < 0:
        raise ValueError(f"{label} must be non-negative: {parsed}")
    return parsed


def _failure(*, diagnostic_name: str, diagnostic_kind: str, errors: list[str]) -> dict[str, Any]:
    summary = {
        "schema_version": 1,
        "diagnostic_name": diagnostic_name,
        "diagnostic_kind": diagnostic_kind,
        "ok": False,
        "validated_at": _now_utc(),
        "file_count": 0,
        "total_size_bytes": 0,
        "files": [],
        "errors": errors,
        "warnings": [],
    }
    return {
        "ok": False,
        "summary": summary,
        "manifest": None,
        "files": [],
        "errors": errors,
        "warnings": [],
    }


def _now_utc() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")