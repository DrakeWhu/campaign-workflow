from __future__ import annotations

import csv
import os
import shutil
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from campaign_workflow.core.atomic_io import read_json, write_json_atomic
from campaign_workflow.core.path_safety import PathSafetyError, validate_relative_path


class BatchCampaignError(ValueError):
    """Raised when an optimizer candidate batch cannot become a campaign root."""


REQUIRED_CANDIDATE_BATCH_COLUMNS = (
    "CASE_ID",
    "CASE_NAME",
    "LASER_CASE",
    "PLASMA_KIND",
    "N0_CM3",
    "PLATEAU_LENGTH_MM",
    "DIAMETER_UM",
    "RADIUS_UM",
    "FOCUS_OFFSET_FROM_PLATEAU_START_MM",
    "CAP_RMAX_UM",
    "CAP_NR",
)

NUMERIC_CANDIDATE_BATCH_COLUMNS = (
    "N0_CM3",
    "PLATEAU_LENGTH_MM",
    "DIAMETER_UM",
    "RADIUS_UM",
    "FOCUS_OFFSET_FROM_PLATEAU_START_MM",
    "CAP_RMAX_UM",
    "CAP_NR",
)

ALLOWED_LASER_CASES = frozenset({"f20", "f32", "f40"})
ALLOWED_PLASMA_KINDS = frozenset({"chan", "uni", "vac"})

PROVENANCE_FILENAME = "optimizer_batch_provenance.json"


@dataclass(frozen=True)
class BatchCampaignPlan:
    candidate_batch: Path
    batch_plan: Path
    template_campaign_root: Path
    output_campaign_root: Path
    campaign_name: str
    campaign_json_source: Path
    input_template_source: Path
    campaign_json_output: Path
    input_template_output: Path
    cases_output: Path
    array_logs_output: Path
    provenance_output: Path
    rows: tuple[dict[str, str], ...]
    fieldnames: tuple[str, ...]
    template_campaign_config: dict[str, Any]
    optimizer_plan: dict[str, Any]
    campaign_config_output: dict[str, Any]


def read_candidate_batch(path: Path) -> tuple[list[dict[str, str]], list[str]]:
    """Read a reviewed optimizer candidate batch as real TSV."""
    if not path.exists():
        raise FileNotFoundError(f"candidate batch does not exist: {path}")
    if not path.is_file():
        raise BatchCampaignError(f"candidate batch is not a regular file: {path}")

    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle, delimiter="\t")
        if reader.fieldnames is None:
            raise BatchCampaignError(f"candidate batch has no header: {path}")

        fieldnames = [str(name).strip() for name in reader.fieldnames]
        if any(name == "" for name in fieldnames):
            raise BatchCampaignError(
                f"candidate batch has an empty column name: {path}"
            )
        if len(set(fieldnames)) != len(fieldnames):
            raise BatchCampaignError(
                f"candidate batch has duplicate column names: {path}"
            )

        rows: list[dict[str, str]] = []
        for row_number, raw_row in enumerate(reader, start=2):
            if raw_row is None:
                continue
            if None in raw_row:
                raise BatchCampaignError(
                    f"candidate batch row {row_number} has more fields than the header"
                )
            row = {
                str(key).strip(): ("" if value is None else str(value).strip())
                for key, value in raw_row.items()
            }
            if any(value != "" for value in row.values()):
                rows.append(row)

    if not rows:
        raise BatchCampaignError(f"candidate batch contains no candidate rows: {path}")

    return rows, fieldnames


def validate_candidate_batch(
    rows: list[dict[str, str]],
    fieldnames: list[str],
    *,
    allowed_laser_cases: frozenset[str] = ALLOWED_LASER_CASES,
    allowed_plasma_kinds: frozenset[str] = ALLOWED_PLASMA_KINDS,
) -> None:
    missing = [
        column
        for column in REQUIRED_CANDIDATE_BATCH_COLUMNS
        if column not in fieldnames
    ]
    if missing:
        raise BatchCampaignError(
            f"candidate batch is missing required columns: {missing}"
        )

    seen_case_ids: set[int] = set()
    seen_case_names: set[str] = set()

    for row_number, row in enumerate(rows, start=2):
        case_id = _parse_case_id(row.get("CASE_ID", ""), row_number=row_number)
        case_name = str(row.get("CASE_NAME", "")).strip()
        laser_case = str(row.get("LASER_CASE", "")).strip().lower()
        plasma_kind = str(row.get("PLASMA_KIND", "")).strip().lower()

        if case_id in seen_case_ids:
            raise BatchCampaignError(f"duplicate CASE_ID in candidate batch: {case_id}")
        seen_case_ids.add(case_id)

        validate_safe_case_name(case_name, row_number=row_number)
        if case_name in seen_case_names:
            raise BatchCampaignError(
                f"duplicate CASE_NAME in candidate batch: {case_name!r}"
            )
        seen_case_names.add(case_name)

        if laser_case not in allowed_laser_cases:
            raise BatchCampaignError(
                f"invalid LASER_CASE {laser_case!r} in row {row_number}; "
                f"allowed values: {sorted(allowed_laser_cases)}"
            )
        if plasma_kind not in allowed_plasma_kinds:
            raise BatchCampaignError(
                f"invalid PLASMA_KIND {plasma_kind!r} in row {row_number}; "
                f"allowed values: {sorted(allowed_plasma_kinds)}"
            )

        for column in NUMERIC_CANDIDATE_BATCH_COLUMNS:
            _parse_finite_decimal(
                row.get(column, ""), column=column, row_number=row_number
            )

        cap_nr = _parse_finite_decimal(
            row.get("CAP_NR", ""), column="CAP_NR", row_number=row_number
        )
        if cap_nr != cap_nr.to_integral_value():
            raise BatchCampaignError(
                f"CAP_NR must be integer-like in row {row_number}: {row.get('CAP_NR')!r}"
            )


def validate_safe_case_name(case_name: str, *, row_number: int | None = None) -> None:
    label = "CASE_NAME" if row_number is None else f"CASE_NAME in row {row_number}"
    text = str(case_name).strip()

    if text == "":
        raise BatchCampaignError(f"{label} is empty")
    if "/" in text or "\\" in text:
        raise BatchCampaignError(f"{label} must not contain path separators: {text!r}")

    try:
        path = validate_relative_path(text, label=label)
    except PathSafetyError as exc:
        raise BatchCampaignError(str(exc)) from exc

    if path == Path("."):
        raise BatchCampaignError(f"{label} must not resolve to '.'")
    if len(path.parts) != 1:
        raise BatchCampaignError(
            f"{label} must be a single relative directory name: {text!r}"
        )


def build_batch_campaign_plan(
    *,
    candidate_batch: Path,
    batch_plan: Path,
    template_campaign_root: Path,
    output_campaign_root: Path,
    campaign_name: str,
) -> BatchCampaignPlan:
    campaign_name = str(campaign_name).strip()
    if campaign_name == "":
        raise BatchCampaignError("campaign name is empty")

    template_root = template_campaign_root.resolve(strict=True)
    if not template_root.is_dir():
        raise BatchCampaignError(
            f"template campaign root is not a directory: {template_root}"
        )

    optimizer_plan = _read_optimizer_batch_plan(batch_plan)
    template_info = optimizer_plan.get("campaign_template", {})
    if template_info is None:
        template_info = {}
    if not isinstance(template_info, dict):
        raise BatchCampaignError(
            "batch plan field 'campaign_template' must be an object"
        )

    campaign_json_rel = validate_relative_path(
        template_info.get("campaign_json", "campaign.json"),
        label="batch_plan.campaign_template.campaign_json",
    )
    input_template_rel = validate_relative_path(
        template_info.get("input_template", "input_template.py"),
        label="batch_plan.campaign_template.input_template",
    )

    campaign_json_source = template_root / campaign_json_rel
    input_template_source = template_root / input_template_rel

    if not campaign_json_source.is_file():
        raise FileNotFoundError(
            f"template campaign.json does not exist: {campaign_json_source}"
        )
    if not input_template_source.is_file():
        raise FileNotFoundError(
            f"template input_template.py does not exist: {input_template_source}"
        )

    template_campaign_config = read_json(campaign_json_source)
    _validate_template_campaign_config(template_campaign_config, campaign_json_source)

    rows, fieldnames = read_candidate_batch(candidate_batch)
    validate_candidate_batch(rows, fieldnames)

    output_root = output_campaign_root.resolve(strict=False)
    campaign_config_output = dict(template_campaign_config)
    campaign_config_output["campaign_name"] = campaign_name
    campaign_config_output["case_manifest"] = "cases.tsv"
    campaign_config_output["case_manifest_format"] = "tsv"
    campaign_config_output["case_id_column"] = "CASE_ID"
    campaign_config_output["case_name_column"] = "CASE_NAME"

    return BatchCampaignPlan(
        candidate_batch=candidate_batch.resolve(strict=False),
        batch_plan=batch_plan.resolve(strict=False),
        template_campaign_root=template_root,
        output_campaign_root=output_root,
        campaign_name=campaign_name,
        campaign_json_source=campaign_json_source.resolve(strict=True),
        input_template_source=input_template_source.resolve(strict=True),
        campaign_json_output=output_root / "campaign.json",
        input_template_output=output_root / "input_template.py",
        cases_output=output_root / "cases.tsv",
        array_logs_output=output_root / "array_logs",
        provenance_output=output_root / PROVENANCE_FILENAME,
        rows=tuple(rows),
        fieldnames=tuple(fieldnames),
        template_campaign_config=template_campaign_config,
        optimizer_plan=optimizer_plan,
        campaign_config_output=campaign_config_output,
    )


def summarize_batch_campaign_plan(
    plan: BatchCampaignPlan, *, execute: bool
) -> dict[str, Any]:
    case_ids = [_parse_case_id(row["CASE_ID"], row_number=0) for row in plan.rows]
    plasma_counts: dict[str, int] = {}
    laser_counts: dict[str, int] = {}
    for row in plan.rows:
        plasma_kind = str(row["PLASMA_KIND"]).strip().lower()
        laser_case = str(row["LASER_CASE"]).strip().lower()
        plasma_counts[plasma_kind] = plasma_counts.get(plasma_kind, 0) + 1
        laser_counts[laser_case] = laser_counts.get(laser_case, 0) + 1

    return {
        "mode": "execute" if execute else "dry-run",
        "campaign_name": plan.campaign_name,
        "candidate_batch": str(plan.candidate_batch),
        "batch_plan": str(plan.batch_plan),
        "template_campaign_root": str(plan.template_campaign_root),
        "output_campaign_root": str(plan.output_campaign_root),
        "output_exists": plan.output_campaign_root.exists(),
        "cases": len(plan.rows),
        "case_id_min": min(case_ids),
        "case_id_max": max(case_ids),
        "laser_case_counts": dict(sorted(laser_counts.items())),
        "plasma_kind_counts": dict(sorted(plasma_counts.items())),
        "will_write": [
            str(plan.campaign_json_output),
            str(plan.cases_output),
            str(plan.input_template_output),
            str(plan.array_logs_output),
            str(plan.provenance_output),
        ],
        "will_not": [
            "materialize case directories",
            "submit SLURM jobs",
            "call sbatch/srun/mpiexec/mpirun",
            "launch WarpX",
            "run guiding-analysis",
            "read HDF5/openPMD diagnostics",
            "cleanup raw diagnostics",
            "mutate the template campaign root",
        ],
        "destructive_operations": 0,
    }


def execute_batch_campaign_plan(plan: BatchCampaignPlan) -> dict[str, Any]:
    if plan.output_campaign_root.exists():
        raise BatchCampaignError(
            f"output campaign root already exists: {plan.output_campaign_root}"
        )

    parent = plan.output_campaign_root.parent
    if not parent.exists():
        raise FileNotFoundError(
            f"parent directory for output campaign root does not exist: {parent}"
        )
    if not parent.is_dir():
        raise BatchCampaignError(
            f"parent path for output campaign root is not a directory: {parent}"
        )

    plan.output_campaign_root.mkdir()
    plan.array_logs_output.mkdir()

    shutil.copyfile(plan.input_template_source, plan.input_template_output)
    write_json_atomic(plan.campaign_json_output, plan.campaign_config_output)
    write_cases_tsv(plan.cases_output, rows=plan.rows, fieldnames=plan.fieldnames)
    write_json_atomic(plan.provenance_output, build_provenance(plan))

    return summarize_batch_campaign_plan(plan, execute=True)


def build_provenance(plan: BatchCampaignPlan) -> dict[str, Any]:
    optimizer_plan = plan.optimizer_plan
    return {
        "schema_version": 1,
        "kind": "optimizer_batch_campaign_preparation",
        "created_at": datetime.now(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z"),
        "campaign_name": plan.campaign_name,
        "candidate_batch": str(plan.candidate_batch),
        "batch_plan": str(plan.batch_plan),
        "template_campaign_root": str(plan.template_campaign_root),
        "output_campaign_root": str(plan.output_campaign_root),
        "case_count": len(plan.rows),
        "candidate_batch_columns": list(plan.fieldnames),
        "optimizer_iteration": optimizer_plan.get("optimizer_iteration"),
        "objective_config_id": optimizer_plan.get("objective_config_id"),
        "source_campaigns": optimizer_plan.get("source_campaigns", []),
        "copied_files": {
            "campaign_json": {
                "source": str(plan.campaign_json_source),
                "destination": "campaign.json",
            },
            "input_template": {
                "source": str(plan.input_template_source),
                "destination": "input_template.py",
            },
        },
        "derived_files": {
            "cases_tsv": "cases.tsv",
            "provenance": PROVENANCE_FILENAME,
        },
        "non_goals": [
            "does not submit jobs",
            "does not call sbatch/srun/mpiexec/mpirun",
            "does not launch WarpX",
            "does not run guiding-analysis",
            "does not read raw HDF5/openPMD",
            "does not delete data",
            "does not materialize case directories",
            "does not mutate previous campaigns",
            "does not import external optimizer packages",
        ],
    }


def write_cases_tsv(
    path: Path, *, rows: tuple[dict[str, str], ...], fieldnames: tuple[str, ...]
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_name(f".{path.name}.tmp.{os.getpid()}")
    try:
        with tmp_path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(
                handle,
                fieldnames=list(fieldnames),
                delimiter="\t",
                lineterminator="\n",
                extrasaction="ignore",
            )
            writer.writeheader()
            for row in rows:
                writer.writerow({name: row.get(name, "") for name in fieldnames})
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_path, path)
    finally:
        if tmp_path.exists():
            try:
                tmp_path.unlink()
            except OSError:
                pass


def _read_optimizer_batch_plan(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(f"batch campaign plan does not exist: {path}")
    if not path.is_file():
        raise BatchCampaignError(f"batch campaign plan is not a regular file: {path}")

    data = read_json(path)
    schema_version = data.get("schema_version")
    plan_type = data.get("plan_type")

    if schema_version != 1:
        raise BatchCampaignError(
            f"unsupported batch campaign plan schema_version: {schema_version!r}"
        )
    if plan_type != "optimizer_candidate_batch":
        raise BatchCampaignError(f"unsupported batch campaign plan_type: {plan_type!r}")

    return data


def _validate_template_campaign_config(config: dict[str, Any], source: Path) -> None:
    required = [
        "schema_version",
        "campaign_name",
        "case_manifest",
        "case_id_column",
        "case_name_column",
    ]
    missing = [key for key in required if key not in config]
    if missing:
        raise BatchCampaignError(
            f"template campaign.json is missing required keys {missing}: {source}"
        )


def _parse_case_id(raw_value: str, *, row_number: int) -> int:
    text = str(raw_value).strip()
    if text == "":
        raise BatchCampaignError(f"CASE_ID is empty in row {row_number}")
    try:
        return int(text)
    except ValueError as exc:
        raise BatchCampaignError(
            f"CASE_ID must be integer-like in row {row_number}: {raw_value!r}"
        ) from exc


def _parse_finite_decimal(raw_value: str, *, column: str, row_number: int) -> Decimal:
    text = str(raw_value).strip()
    if text == "":
        raise BatchCampaignError(f"{column} is empty in row {row_number}")
    try:
        value = Decimal(text)
    except InvalidOperation as exc:
        raise BatchCampaignError(
            f"{column} must be numeric in row {row_number}: {raw_value!r}"
        ) from exc
    if not value.is_finite():
        raise BatchCampaignError(
            f"{column} must be finite in row {row_number}: {raw_value!r}"
        )
    return value
