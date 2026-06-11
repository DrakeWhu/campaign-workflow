from __future__ import annotations

import csv
import io
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class CaseRecord:
    case_id: int
    case_name: str
    row: dict[str, str]


def load_campaign_config(campaign_root: Path) -> dict[str, Any]:
    """Load campaign.json from the campaign root."""
    config_path = campaign_root / "campaign.json"

    if not config_path.exists():
        raise FileNotFoundError(f"Missing campaign.json at campaign root: {config_path}")

    with config_path.open("r", encoding="utf-8-sig") as f:
        data = json.load(f)

    if not isinstance(data, dict):
        raise ValueError(f"campaign.json must contain a JSON object: {config_path}")

    required = [
        "schema_version",
        "campaign_name",
        "case_manifest",
        "case_id_column",
        "case_name_column",
    ]

    missing = [key for key in required if key not in data]
    if missing:
        raise ValueError(f"campaign.json is missing required keys: {missing}")

    return data


def _parse_tabular_text(text: str) -> list[dict[str, str]]:
    """Parse a TSV-like file.

    Primary format is real TSV. A whitespace fallback exists only for simple
    manually-created test manifests where fields do not contain spaces and no
    empty columns are required.
    """
    nonempty_lines = [line.rstrip("\n") for line in text.splitlines() if line.strip()]

    if not nonempty_lines:
        return []

    header = nonempty_lines[0]

    if "\t" in header:
        reader = csv.DictReader(io.StringIO(text), delimiter="\t")
        rows = []
        for row in reader:
            if row is None:
                continue
            clean = {str(k).strip(): ("" if v is None else str(v).strip()) for k, v in row.items() if k is not None}
            if any(value != "" for value in clean.values()):
                rows.append(clean)
        return rows

    # Fallback for simple whitespace-separated fake manifests.
    headers = header.split()
    rows = []

    for line in nonempty_lines[1:]:
        values = line.split()
        if len(values) != len(headers):
            raise ValueError(
                "Manifest is not tab-separated and whitespace fallback failed: "
                f"expected {len(headers)} fields, got {len(values)} in line: {line!r}"
            )
        rows.append(dict(zip(headers, values)))

    return rows


def load_cases(campaign_root: Path, config: dict[str, Any]) -> list[CaseRecord]:
    """Load cases from the configured case manifest."""
    manifest_rel = config["case_manifest"]
    case_id_column = config["case_id_column"]
    case_name_column = config["case_name_column"]

    manifest_path = campaign_root / manifest_rel

    if not manifest_path.exists():
        raise FileNotFoundError(f"Missing case manifest: {manifest_path}")

    text = manifest_path.read_text(encoding="utf-8-sig")
    rows = _parse_tabular_text(text)

    cases: list[CaseRecord] = []
    seen_ids: set[int] = set()
    seen_names: set[str] = set()

    for row_number, row in enumerate(rows, start=2):
        if case_id_column not in row:
            raise ValueError(f"Missing column {case_id_column!r} in manifest row {row_number}")
        if case_name_column not in row:
            raise ValueError(f"Missing column {case_name_column!r} in manifest row {row_number}")

        raw_case_id = row[case_id_column].strip()
        case_name = row[case_name_column].strip()

        if raw_case_id == "":
            raise ValueError(f"Empty case id in manifest row {row_number}")
        if case_name == "":
            raise ValueError(f"Empty case name in manifest row {row_number}")

        try:
            case_id = int(raw_case_id)
        except ValueError as exc:
            raise ValueError(f"Invalid integer case id {raw_case_id!r} in row {row_number}") from exc

        if case_id in seen_ids:
            raise ValueError(f"Duplicate case id in manifest: {case_id}")
        if case_name in seen_names:
            raise ValueError(f"Duplicate case name in manifest: {case_name}")

        seen_ids.add(case_id)
        seen_names.add(case_name)
        cases.append(CaseRecord(case_id=case_id, case_name=case_name, row=dict(row)))

    return cases
