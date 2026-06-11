from __future__ import annotations

from pathlib import Path
from typing import Iterable


class PathSafetyError(ValueError):
    """Raised when a path violates the campaign safety boundary."""


def _is_relative_to(path: Path, base: Path) -> bool:
    try:
        path.relative_to(base)
        return True
    except ValueError:
        return False


def _contains_parent_reference(path: Path) -> bool:
    return any(part == ".." for part in path.parts)


def validate_relative_path(raw_path: str | Path, *, label: str = "path") -> Path:
    """Validate a user/config path that must be relative and must not climb upward.

    This intentionally accepts paths that do not exist yet. It rejects absolute
    paths and any explicit '..' component. It is used for config paths and glob
    patterns before resolving against a case directory.
    """
    if raw_path is None:
        raise PathSafetyError(f"{label} is missing")

    text = str(raw_path).strip()
    if text == "":
        raise PathSafetyError(f"{label} is empty")

    path = Path(text)

    if path.is_absolute():
        raise PathSafetyError(f"{label} must be relative, got absolute path: {text!r}")

    if _contains_parent_reference(path):
        raise PathSafetyError(f"{label} must not contain '..': {text!r}")

    return path


def validate_relative_glob(raw_glob: str | Path, *, label: str = "glob") -> str:
    """Validate a relative glob pattern before using Path.glob."""
    path = validate_relative_path(raw_glob, label=label)
    return path.as_posix()


def resolve_case_dir(case_dir: Path) -> Path:
    """Resolve an existing case directory."""
    if not case_dir.exists():
        raise PathSafetyError(f"case directory does not exist: {case_dir}")
    if not case_dir.is_dir():
        raise PathSafetyError(f"case path is not a directory: {case_dir}")
    return case_dir.resolve(strict=True)


def resolve_existing_path_inside_case(case_dir: Path, raw_path: str | Path, *, label: str = "path") -> Path:
    """Resolve an existing path and require it to stay inside CASE_DIR.

    Symlinks are followed. A symlink is accepted only if its resolved target is
    still inside the case directory. This rejects symlink escapes.
    """
    rel_path = validate_relative_path(raw_path, label=label)
    case_real = resolve_case_dir(case_dir)
    candidate = (case_real / rel_path).resolve(strict=True)

    if not _is_relative_to(candidate, case_real):
        raise PathSafetyError(
            f"{label} escapes case directory: raw={str(raw_path)!r}, resolved={candidate}, case_dir={case_real}"
        )

    return candidate


def validate_existing_file_inside_case(case_dir: Path, raw_path: str | Path, *, label: str = "file") -> Path:
    """Resolve and validate an existing regular file inside CASE_DIR."""
    candidate = resolve_existing_path_inside_case(case_dir, raw_path, label=label)

    if not candidate.is_file():
        raise PathSafetyError(f"{label} is not a regular file: {candidate}")

    return candidate


def safe_relative_posix(case_dir: Path, path: Path) -> str:
    """Return a POSIX relative path after verifying the resolved path is in CASE_DIR."""
    case_real = resolve_case_dir(case_dir)
    resolved = path.resolve(strict=True)

    if not _is_relative_to(resolved, case_real):
        raise PathSafetyError(f"path escapes case directory: {resolved} not under {case_real}")

    return resolved.relative_to(case_real).as_posix()


def glob_existing_files_inside_case(case_dir: Path, raw_glob: str | Path) -> list[Path]:
    """Resolve a safe relative glob and return matching files inside CASE_DIR.

    Returned paths are resolved absolute paths. Every match is checked against
    the case boundary, so symlink escapes are rejected by the caller when it
    validates each file.
    """
    safe_glob = validate_relative_glob(raw_glob)
    case_real = resolve_case_dir(case_dir)

    matches: list[Path] = []
    for match in case_real.glob(safe_glob):
        try:
            resolved = match.resolve(strict=True)
        except FileNotFoundError:
            continue

        if not _is_relative_to(resolved, case_real):
            raise PathSafetyError(
                f"glob match escapes case directory: glob={safe_glob!r}, match={match}, resolved={resolved}"
            )

        if resolved.is_file():
            matches.append(resolved)

    return sorted(set(matches), key=lambda p: p.as_posix())


def require_all_inside_case(case_dir: Path, paths: Iterable[Path]) -> list[Path]:
    """Validate a collection of existing paths and return resolved paths."""
    case_real = resolve_case_dir(case_dir)
    resolved_paths: list[Path] = []

    for path in paths:
        resolved = path.resolve(strict=True)
        if not _is_relative_to(resolved, case_real):
            raise PathSafetyError(f"path escapes case directory: {resolved} not under {case_real}")
        resolved_paths.append(resolved)

    return resolved_paths