from __future__ import annotations

import csv
from pathlib import Path
from typing import Any

from campaign_workflow.core.manifests import file_manifest_entry
from campaign_workflow.core.path_safety import PathSafetyError, validate_existing_file_inside_case
from campaign_workflow.core.state import now_utc


SUPPORTED_REDUCED_OUTPUT_KINDS = {"csv"}


def validate_reduced_output(
    *,
    case_dir: Path,
    output: dict[str, Any],
) -> dict[str, Any]:
    """Validate one configured reduced output for one case.

    This validator is intentionally generic. For CSV outputs it checks path
    safety, file existence, suffix, non-empty payload, CSV readability, row
    count, and configured columns. It does not interpret guiding/capillary
    physics or any campaign-specific metric semantics.
    """
    name = _required_string(output, "name")
    kind = _required_string(output, "kind")

    if kind not in SUPPORTED_REDUCED_OUTPUT_KINDS:
        return _failure(
            output_name=name,
            output_kind=kind,
            path=str(output.get("path", "")),
            errors=[f"unsupported reduced output kind: {kind!r}"],
        )

    raw_path = _required_string(output, "path")
    min_rows = _nonnegative_int(output.get("min_rows", 1), "min_rows")
    required_columns = _string_list(output.get("required_columns", []), "required_columns")
    allowed_suffixes = _allowed_suffixes(output, kind)

    errors: list[str] = []
    warnings: list[str] = []
    file_entry: dict[str, Any] | None = None
    row_count = 0
    columns: list[str] = []
    resolved_path: Path | None = None

    try:
        resolved_path = validate_existing_file_inside_case(case_dir, raw_path, label=f"reduced output {name!r}")
    except PathSafetyError as exc:
        errors.append(str(exc))
    except FileNotFoundError:
        errors.append(f"reduced output {name!r} does not exist: {raw_path}")
    except Exception as exc:
        errors.append(f"failed to resolve reduced output {name!r}: {exc}")

    if resolved_path is not None:
        try:
            file_entry = file_manifest_entry(case_dir, resolved_path)
            suffix = resolved_path.suffix
            if suffix not in allowed_suffixes:
                errors.append(f"invalid suffix for reduced output {name!r}: {suffix!r}; allowed={allowed_suffixes}")
            if file_entry["size_bytes"] <= 0:
                errors.append(f"empty reduced output {name!r}: {raw_path}")
        except Exception as exc:
            errors.append(f"failed to inspect reduced output {name!r}: {exc}")

    if resolved_path is not None and not errors:
        csv_errors, csv_warnings, columns, row_count = _validate_csv_file(
            resolved_path,
            required_columns=required_columns,
            min_rows=min_rows,
        )
        errors.extend(csv_errors)
        warnings.extend(csv_warnings)

    ok = not errors

    return {
        "schema_version": 1,
        "output_name": name,
        "output_kind": kind,
        "ok": ok,
        "validated_at": now_utc(),
        "path": raw_path,
        "allowed_suffixes": allowed_suffixes,
        "min_rows": min_rows,
        "required_columns": required_columns,
        "row_count": row_count,
        "columns": columns,
        "file": file_entry,
        "errors": errors,
        "warnings": warnings,
    }


def _validate_csv_file(
    path: Path,
    *,
    required_columns: list[str],
    min_rows: int,
) -> tuple[list[str], list[str], list[str], int]:
    errors: list[str] = []
    warnings: list[str] = []
    columns: list[str] = []
    row_count = 0

    try:
        with path.open("r", encoding="utf-8-sig", newline="") as f:
            reader = csv.DictReader(f)
            if reader.fieldnames is None:
                errors.append(f"CSV has no header: {path.name}")
                return errors, warnings, columns, row_count

            columns = [str(column) for column in reader.fieldnames]
            missing_columns = [column for column in required_columns if column not in columns]
            if missing_columns:
                errors.append(f"CSV missing required column(s): {missing_columns}; columns={columns}")

            for _row in reader:
                row_count += 1
    except UnicodeDecodeError as exc:
        errors.append(f"CSV is not valid UTF-8 text: {exc}")
    except csv.Error as exc:
        errors.append(f"CSV parser error: {exc}")
    except Exception as exc:
        errors.append(f"failed to read CSV: {exc}")

    if row_count < min_rows:
        errors.append(f"CSV requires at least {min_rows} data row(s), found {row_count}")

    return errors, warnings, columns, row_count


def _allowed_suffixes(output: dict[str, Any], kind: str) -> list[str]:
    default_suffixes = {"csv": [".csv"]}
    raw = output.get("allowed_suffixes", default_suffixes[kind])
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
        raise ValueError(f"reduced output field {key!r} must be a non-empty string")
    return value.strip()


def _string_list(value: Any, label: str) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise ValueError(f"{label} must be a list of strings")

    parsed: list[str] = []
    for item in value:
        if not isinstance(item, str) or not item.strip():
            raise ValueError(f"{label} contains a non-empty non-string item: {item!r}")
        parsed.append(item.strip())
    return parsed


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


def _failure(*, output_name: str, output_kind: str, path: str, errors: list[str]) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "output_name": output_name,
        "output_kind": output_kind,
        "ok": False,
        "validated_at": now_utc(),
        "path": path,
        "allowed_suffixes": [],
        "min_rows": 0,
        "required_columns": [],
        "row_count": 0,
        "columns": [],
        "file": None,
        "errors": errors,
        "warnings": [],
    }