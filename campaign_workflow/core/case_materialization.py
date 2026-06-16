from __future__ import annotations

import os
import shutil
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from campaign_workflow.core.case_dirs import build_case_dir_plan, materialize_one_case_dir
from campaign_workflow.core.path_safety import validate_relative_path
from campaign_workflow.core.tsv_cases import CaseRecord


@dataclass(frozen=True)
class EnvColumnRule:
    column: str
    env: str
    required: bool = False
    scale: Decimal | None = None


@dataclass(frozen=True)
class CaseMaterializationConfig:
    input_template: Path
    input_name: Path
    env_name: Path
    env_column_rules: tuple[EnvColumnRule, ...]
    env_constants: tuple[tuple[str, str], ...]


DEFAULT_ENV_COLUMN_RULES = (
    EnvColumnRule("CASE_ID", "CAP_CASE_ID", required=True),
    EnvColumnRule("CASE_NAME", "CAP_CASE_NAME", required=True),
    EnvColumnRule("LASER_CASE", "CAP_LASER_CASE", required=True),
    EnvColumnRule("PLASMA_KIND", "CAP_PLASMA_KIND", required=True),
    EnvColumnRule("N0_CM3", "CAP_N0_CM3", required=True),
    EnvColumnRule("PLATEAU_LENGTH_MM", "CAP_PLATEAU_LENGTH_M", required=True, scale=Decimal("1e-3")),
    EnvColumnRule("RADIUS_UM", "CAP_RADIUS_M", required=False, scale=Decimal("1e-6")),
    EnvColumnRule(
        "FOCUS_OFFSET_FROM_PLATEAU_START_MM",
        "CAP_FOCUS_OFFSET_FROM_PLATEAU_START_MM",
        required=True,
    ),
    EnvColumnRule("CAP_RMAX_UM", "CAP_RMAX_M", required=True, scale=Decimal("1e-6")),
    EnvColumnRule("CAP_NR", "CAP_NR", required=True),
)

DEFAULT_ENV_CONSTANTS = (
    ("CAP_DIAG_PRESET", "guiding_rhoe"),
    ("CAP_LONG_PROFILE", "both"),
)


def _require_env_name(name: str) -> str:
    text = str(name).strip()
    if text == "":
        raise ValueError("environment variable name is empty")
    if not text.replace("_", "A").isalnum() or text[0].isdigit():
        raise ValueError(f"invalid environment variable name: {name!r}")
    return text


def _parse_scale(value: Any) -> Decimal | None:
    if value is None or value == "":
        return None
    try:
        return Decimal(str(value))
    except InvalidOperation as exc:
        raise ValueError(f"invalid env column scale: {value!r}") from exc


def _parse_env_column_rule(raw_rule: dict[str, Any]) -> EnvColumnRule:
    if not isinstance(raw_rule, dict):
        raise ValueError(f"env_columns entries must be objects, got {type(raw_rule).__name__}")

    column = str(raw_rule.get("column", "")).strip()
    env = _require_env_name(str(raw_rule.get("env", "")).strip())

    if column == "":
        raise ValueError("env column rule has empty column")

    return EnvColumnRule(
        column=column,
        env=env,
        required=bool(raw_rule.get("required", False)),
        scale=_parse_scale(raw_rule.get("scale")),
    )


def _parse_env_constants(raw_constants: Any) -> tuple[tuple[str, str], ...]:
    if raw_constants is None:
        return tuple(DEFAULT_ENV_CONSTANTS)

    items: list[tuple[str, str]] = []

    if isinstance(raw_constants, dict):
        iterator = raw_constants.items()
    elif isinstance(raw_constants, list):
        iterator = []
        for item in raw_constants:
            if not isinstance(item, dict):
                raise ValueError("env_constants list entries must be objects")
            iterator.append((item.get("env", ""), item.get("value", "")))
    else:
        raise ValueError("env_constants must be an object or a list")

    for raw_name, raw_value in iterator:
        name = _require_env_name(str(raw_name).strip())
        value = str(raw_value)
        items.append((name, value))

    return tuple(items)


def get_case_materialization_config(config: dict[str, Any]) -> CaseMaterializationConfig:
    materialization = config.get("case_materialization", {})
    if materialization is None:
        materialization = {}
    if not isinstance(materialization, dict):
        raise ValueError("campaign.json case_materialization must be an object")

    simulation = config.get("simulation", {})
    if not isinstance(simulation, dict):
        simulation = {}

    input_template = validate_relative_path(
        materialization.get("input_template", "input_template.py"),
        label="case_materialization.input_template",
    )
    input_name = validate_relative_path(
        materialization.get("input_name", simulation.get("input_script", "input.py")),
        label="case_materialization.input_name",
    )
    env_name = validate_relative_path(
        materialization.get("env_name", "case.env"),
        label="case_materialization.env_name",
    )

    raw_rules = materialization.get("env_columns")
    if raw_rules is None:
        env_column_rules = DEFAULT_ENV_COLUMN_RULES
    else:
        if not isinstance(raw_rules, list):
            raise ValueError("case_materialization.env_columns must be a list")
        env_column_rules = tuple(_parse_env_column_rule(rule) for rule in raw_rules)

    env_constants = _parse_env_constants(materialization.get("env_constants"))

    return CaseMaterializationConfig(
        input_template=input_template,
        input_name=input_name,
        env_name=env_name,
        env_column_rules=tuple(env_column_rules),
        env_constants=env_constants,
    )


def _decimal_to_env_text(value: Decimal) -> str:
    if value == 0:
        return "0"
    return format(value.normalize(), "e")


def _convert_value(raw_value: str, rule: EnvColumnRule) -> str:
    text = str(raw_value).strip()
    if rule.scale is None:
        return text

    try:
        return _decimal_to_env_text(Decimal(text) * rule.scale)
    except InvalidOperation as exc:
        raise ValueError(
            f"column {rule.column!r} value {raw_value!r} cannot be converted for {rule.env}"
        ) from exc


def build_env_values(case: CaseRecord, mat_config: CaseMaterializationConfig) -> tuple[list[tuple[str, str]], list[str]]:
    values: list[tuple[str, str]] = []
    errors: list[str] = []
    seen_env: set[str] = set()

    for rule in mat_config.env_column_rules:
        if rule.column not in case.row:
            if rule.required:
                errors.append(f"missing required column {rule.column!r} for {rule.env}")
            continue

        raw_value = str(case.row.get(rule.column, "")).strip()
        if raw_value == "":
            if rule.required:
                errors.append(f"empty required column {rule.column!r} for {rule.env}")
            continue

        try:
            value = _convert_value(raw_value, rule)
        except ValueError as exc:
            errors.append(str(exc))
            continue

        if rule.env in seen_env:
            errors.append(f"duplicate environment variable in materialization rules: {rule.env}")
            continue

        seen_env.add(rule.env)
        values.append((rule.env, value))

    for name, value in mat_config.env_constants:
        if name in seen_env:
            errors.append(f"duplicate environment variable from constants: {name}")
            continue
        seen_env.add(name)
        values.append((name, value))

    return values, errors


def _shell_double_quote(value: str) -> str:
    text = str(value)
    text = text.replace("\\", "\\\\")
    text = text.replace('"', '\\"')
    text = text.replace("$", "\\$")
    text = text.replace("`", "\\`")
    return f'"{text}"'


def render_case_env(case: CaseRecord, env_values: list[tuple[str, str]]) -> str:
    lines = [
        "# Generated by campaign_workflow.cli.materialize_cases",
        "# Source: cases.tsv + input_template.py",
        f"# CASE_ID={case.case_id}",
        f"# CASE_NAME={case.case_name}",
        "",
    ]

    for name, value in env_values:
        lines.append(f"export {name}={_shell_double_quote(value)}")

    lines.append("")
    return "\n".join(lines)


def _write_text_atomic(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_name(f".{path.name}.tmp.{os.getpid()}")
    try:
        with tmp_path.open("w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_path, path)
    finally:
        if tmp_path.exists():
            try:
                tmp_path.unlink()
            except OSError:
                pass


def _copy_file_atomic(src: Path, dst: Path) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = dst.with_name(f".{dst.name}.tmp.{os.getpid()}")
    try:
        shutil.copyfile(src, tmp_path)

        # On Windows, os.fsync() may fail with EBADF on a read-only fd.
        # Open read/write so the flush is portable across local Windows tests
        # and POSIX/HPC filesystems.
        with tmp_path.open("r+b") as handle:
            os.fsync(handle.fileno())

        os.replace(tmp_path, dst)
    finally:
        if tmp_path.exists():
            try:
                tmp_path.unlink()
            except OSError:
                pass


def build_case_materialization_plan(
    *,
    campaign_root: Path,
    cases: list[CaseRecord],
    config: dict[str, Any],
    mat_config: CaseMaterializationConfig,
) -> list[dict[str, Any]]:
    template_path = (campaign_root / mat_config.input_template).resolve(strict=False)
    if not template_path.exists():
        raise FileNotFoundError(f"missing input template: {template_path}")
    if not template_path.is_file():
        raise ValueError(f"input template is not a regular file: {template_path}")

    case_dir_plans = build_case_dir_plan(campaign_root=campaign_root, cases=cases, config=config)

    plans: list[dict[str, Any]] = []
    for case, case_dir_plan in zip(cases, case_dir_plans):
        case_dir = Path(case_dir_plan["case_dir"])
        input_path = case_dir / mat_config.input_name
        env_path = case_dir / mat_config.env_name
        env_values, env_errors = build_env_values(case, mat_config)
        plans.append(
            {
                "case": case,
                "case_dir_plan": case_dir_plan,
                "case_dir": case_dir,
                "input_path": input_path,
                "env_path": env_path,
                "template_path": template_path,
                "env_text": render_case_env(case, env_values),
                "env_errors": env_errors,
            }
        )

    return plans


def materialize_one_case(
    *,
    plan: dict[str, Any],
    dry_run: bool,
    overwrite: bool,
) -> dict[str, Any]:
    case: CaseRecord = plan["case"]
    input_path: Path = plan["input_path"]
    env_path: Path = plan["env_path"]
    template_path: Path = plan["template_path"]

    result: dict[str, Any] = {
        "case_id": case.case_id,
        "case_name": case.case_name,
        "case_dir": str(plan["case_dir"]),
        "input_path": str(input_path),
        "env_path": str(env_path),
        "actions": [],
        "errors": [],
        "case_dir_actions": 0,
        "input_written": 0,
        "env_written": 0,
    }

    result["errors"].extend(plan.get("env_errors", []))

    if result["errors"]:
        return result

    dir_result = materialize_one_case_dir(plan=plan["case_dir_plan"], dry_run=dry_run)
    result["actions"].extend(dir_result["actions"])
    result["errors"].extend(dir_result["errors"])
    result["case_dir_actions"] = len(dir_result["actions"])

    input_action_planned = False
    env_action_planned = False

    if input_path.exists() and not overwrite:
        result["errors"].append(f"refusing to overwrite existing input file: {input_path}")
    else:
        action = "overwrite input file" if input_path.exists() else "copy input template"
        result["actions"].append(f"{action}: {template_path} -> {input_path}")
        input_action_planned = True

    if env_path.exists() and not overwrite:
        result["errors"].append(f"refusing to overwrite existing env file: {env_path}")
    else:
        action = "overwrite env file" if env_path.exists() else "write env file"
        result["actions"].append(f"{action}: {env_path}")
        env_action_planned = True

    if result["errors"]:
        return result

    result["input_written"] = 1 if input_action_planned else 0
    result["env_written"] = 1 if env_action_planned else 0

    if dry_run:
        return result

    _copy_file_atomic(template_path, input_path)
    _write_text_atomic(env_path, plan["env_text"])

    return result