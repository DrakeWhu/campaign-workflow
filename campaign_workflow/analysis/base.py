from __future__ import annotations

import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from campaign_workflow.core.path_safety import validate_relative_path
from campaign_workflow.core.state import now_utc
from campaign_workflow.core.tsv_cases import CaseRecord


@dataclass(frozen=True)
class AnalysisAdapterResult:
    """Result returned by a case-local analysis adapter."""

    ok: bool
    adapter: str
    started_at: str
    finished_at: str
    return_code: int | None
    stdout: str
    stderr: str
    errors: list[str]
    warnings: list[str]
    metadata: dict[str, Any]


def run_analysis_adapter(
    *,
    campaign_root: Path,
    case_dir: Path,
    case: CaseRecord,
    config: dict[str, Any],
    analysis: dict[str, Any],
) -> AnalysisAdapterResult:
    """Run the configured analysis adapter for one case.

    The workflow core stays generic: it dispatches to a small adapter protocol
    and records exit status/stdout/stderr. Campaign-specific physics belongs in
    the external command/module selected by campaign.json, not in this package.
    """
    adapter = _analysis_adapter_name(analysis)

    if adapter == "fake":
        return _run_fake_adapter(case_dir=case_dir, case=case, analysis=analysis)

    if adapter in {"command", "subprocess"}:
        return _run_command_adapter(
            campaign_root=campaign_root,
            case_dir=case_dir,
            case=case,
            config=config,
            analysis=analysis,
            adapter=adapter,
        )

    if adapter in {"python_module", "module"}:
        return _run_python_module_adapter(
            campaign_root=campaign_root,
            case_dir=case_dir,
            case=case,
            config=config,
            analysis=analysis,
            adapter=adapter,
        )

    timestamp = now_utc()
    return AnalysisAdapterResult(
        ok=False,
        adapter=adapter,
        started_at=timestamp,
        finished_at=timestamp,
        return_code=None,
        stdout="",
        stderr="",
        errors=[f"unsupported analysis adapter: {adapter!r}"],
        warnings=[],
        metadata={},
    )


def _analysis_adapter_name(analysis: dict[str, Any]) -> str:
    raw = analysis.get("adapter", analysis.get("kind"))
    if not isinstance(raw, str) or not raw.strip():
        raise ValueError("campaign.json analysis must define a non-empty 'adapter' or 'kind'")
    return raw.strip()


def _run_fake_adapter(*, case_dir: Path, case: CaseRecord, analysis: dict[str, Any]) -> AnalysisAdapterResult:
    started_at = now_utc()

    if bool(analysis.get("fake_fail", False)):
        finished_at = now_utc()
        return AnalysisAdapterResult(
            ok=False,
            adapter="fake",
            started_at=started_at,
            finished_at=finished_at,
            return_code=1,
            stdout="",
            stderr="fake analysis failure requested by campaign.json\n",
            errors=["fake analysis failure requested by campaign.json"],
            warnings=[],
            metadata={"fake_fail": True},
        )

    outputs = analysis.get("outputs")
    if not isinstance(outputs, list) or not outputs:
        finished_at = now_utc()
        return AnalysisAdapterResult(
            ok=False,
            adapter="fake",
            started_at=started_at,
            finished_at=finished_at,
            return_code=1,
            stdout="",
            stderr="",
            errors=["fake analysis requires analysis.outputs to be a non-empty list"],
            warnings=[],
            metadata={},
        )

    written: list[str] = []
    warnings: list[str] = []

    try:
        for output in outputs:
            if not isinstance(output, dict):
                warnings.append(f"skipping non-object output entry: {type(output).__name__}")
                continue
            if output.get("kind") != "csv":
                warnings.append(f"fake adapter only writes csv outputs; skipped {output.get('name')!r}")
                continue

            raw_path = output.get("path")
            rel_path = validate_relative_path(raw_path, label=f"fake analysis output {output.get('name', '<unknown>')!r}")
            target = case_dir / rel_path
            target.parent.mkdir(parents=True, exist_ok=True)

            columns = _fake_output_columns(output)
            values = _fake_output_values(columns=columns, case=case)
            target.write_text(",".join(columns) + "\n" + ",".join(values) + "\n", encoding="utf-8", newline="\n")
            written.append(rel_path.as_posix())
    except Exception as exc:
        finished_at = now_utc()
        return AnalysisAdapterResult(
            ok=False,
            adapter="fake",
            started_at=started_at,
            finished_at=finished_at,
            return_code=1,
            stdout="",
            stderr=str(exc),
            errors=[f"fake analysis failed: {exc}"],
            warnings=warnings,
            metadata={"written_outputs": written},
        )

    finished_at = now_utc()
    return AnalysisAdapterResult(
        ok=True,
        adapter="fake",
        started_at=started_at,
        finished_at=finished_at,
        return_code=0,
        stdout=f"fake analysis wrote {len(written)} output(s)\n",
        stderr="",
        errors=[],
        warnings=warnings,
        metadata={"written_outputs": written},
    )


def _run_command_adapter(
    *,
    campaign_root: Path,
    case_dir: Path,
    case: CaseRecord,
    config: dict[str, Any],
    analysis: dict[str, Any],
    adapter: str,
) -> AnalysisAdapterResult:
    command = _string_list(analysis.get("command"), "analysis.command")
    argv = [_expand_template(item, campaign_root=campaign_root, case_dir=case_dir, case=case) for item in command]
    return _run_subprocess(
        argv=argv,
        campaign_root=campaign_root,
        case_dir=case_dir,
        case=case,
        config=config,
        analysis=analysis,
        adapter=adapter,
    )


def _run_python_module_adapter(
    *,
    campaign_root: Path,
    case_dir: Path,
    case: CaseRecord,
    config: dict[str, Any],
    analysis: dict[str, Any],
    adapter: str,
) -> AnalysisAdapterResult:
    module = analysis.get("module")
    if not isinstance(module, str) or not module.strip():
        raise ValueError("python_module analysis adapter requires a non-empty analysis.module string")

    args = _string_list(analysis.get("args", []), "analysis.args")
    argv = [sys.executable, "-m", module.strip()] + [
        _expand_template(item, campaign_root=campaign_root, case_dir=case_dir, case=case) for item in args
    ]
    return _run_subprocess(
        argv=argv,
        campaign_root=campaign_root,
        case_dir=case_dir,
        case=case,
        config=config,
        analysis=analysis,
        adapter=adapter,
    )


def _run_subprocess(
    *,
    argv: list[str],
    campaign_root: Path,
    case_dir: Path,
    case: CaseRecord,
    config: dict[str, Any],
    analysis: dict[str, Any],
    adapter: str,
) -> AnalysisAdapterResult:
    started_at = now_utc()
    env = os.environ.copy()
    env.update(
        {
            "CAMPAIGN_ROOT": str(campaign_root),
            "CASE_DIR": str(case_dir),
            "CASE_ID": str(case.case_id),
            "CASE_NAME": case.case_name,
            "CAMPAIGN_NAME": str(config.get("campaign_name", "")),
        }
    )
    env.update(_analysis_env(analysis))

    try:
        completed = subprocess.run(
            argv,
            cwd=case_dir,
            env=env,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
    except Exception as exc:
        finished_at = now_utc()
        return AnalysisAdapterResult(
            ok=False,
            adapter=adapter,
            started_at=started_at,
            finished_at=finished_at,
            return_code=None,
            stdout="",
            stderr=str(exc),
            errors=[f"failed to execute analysis command: {exc}"],
            warnings=[],
            metadata={"argv": argv},
        )

    finished_at = now_utc()
    ok = completed.returncode == 0
    errors = [] if ok else [f"analysis command returned non-zero exit code: {completed.returncode}"]
    return AnalysisAdapterResult(
        ok=ok,
        adapter=adapter,
        started_at=started_at,
        finished_at=finished_at,
        return_code=completed.returncode,
        stdout=completed.stdout,
        stderr=completed.stderr,
        errors=errors,
        warnings=[],
        metadata={"argv": argv},
    )


def _analysis_env(analysis: dict[str, Any]) -> dict[str, str]:
    raw = analysis.get("env", {})
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise ValueError("analysis.env must be an object of string key/value pairs")

    env: dict[str, str] = {}
    for key, value in raw.items():
        if not isinstance(key, str) or not key.strip():
            raise ValueError(f"analysis.env contains invalid key: {key!r}")
        if not isinstance(value, str):
            raise ValueError(f"analysis.env[{key!r}] must be a string")
        env[key.strip()] = value
    return env


def _string_list(value: Any, label: str) -> list[str]:
    if not isinstance(value, list) or not value:
        raise ValueError(f"{label} must be a non-empty list of strings")

    parsed: list[str] = []
    for item in value:
        if not isinstance(item, str) or not item.strip():
            raise ValueError(f"{label} contains an invalid item: {item!r}")
        parsed.append(item.strip())
    return parsed


def _expand_template(text: str, *, campaign_root: Path, case_dir: Path, case: CaseRecord) -> str:
    workflow_root = os.environ.get(
        "WORKFLOW_ROOT", str(campaign_root / "workflow")
    )
    return text.format(
        campaign_root=str(campaign_root),
        workflow_root=workflow_root,
        case_dir=str(case_dir),
        case_id=case.case_id,
        case_name=case.case_name,
    )


def _fake_output_columns(output: dict[str, Any]) -> list[str]:
    raw = output.get("required_columns", [])
    columns: list[str] = []
    if isinstance(raw, list):
        for item in raw:
            if isinstance(item, str) and item.strip() and item.strip() not in columns:
                columns.append(item.strip())

    if not columns:
        columns = ["case_id", "case_name", "fake_metric"]
    else:
        for fallback in ["case_id", "case_name", "fake_metric"]:
            if fallback not in columns:
                columns.append(fallback)

    return columns


def _fake_output_values(*, columns: list[str], case: CaseRecord) -> list[str]:
    values: list[str] = []
    for column in columns:
        if column == "case_id":
            values.append(str(case.case_id))
        elif column == "case_name":
            values.append(case.case_name)
        elif column == "iteration":
            values.append("0")
        elif column == "score":
            values.append("1.0")
        else:
            values.append("0")
    return values
