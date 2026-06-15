from __future__ import annotations

from typing import Any


def validate_required_raw_evidence(config: dict[str, Any], validation_doc: dict[str, Any]) -> list[str]:
    """Validate that required raw diagnostics have positive validation evidence.

    This is deliberately evidence-based. State alone is not sufficient: callers
    must prove that validation.json contains ok=True summaries and manifest
    references for every required raw diagnostic declared by campaign.json.
    """
    errors: list[str] = []

    raw_diagnostics = config.get("raw_diagnostics")
    if not isinstance(raw_diagnostics, list) or not raw_diagnostics:
        return ["campaign.json must define a non-empty raw_diagnostics list before reduced validation"]

    raw_section = validation_doc.get("raw")
    if not isinstance(raw_section, dict):
        return ["validation.json raw must be an object before reduced validation"]

    required_count = 0
    for diagnostic in raw_diagnostics:
        if not isinstance(diagnostic, dict):
            errors.append(f"raw diagnostic entry must be an object, got {type(diagnostic).__name__}")
            continue

        if not bool(diagnostic.get("required", True)):
            continue

        required_count += 1
        name = diagnostic.get("name")
        if not isinstance(name, str) or not name.strip():
            errors.append("required raw diagnostic is missing a non-empty name")
            continue

        summary = raw_section.get(name)
        if not isinstance(summary, dict):
            errors.append(f"missing raw validation evidence for required diagnostic {name!r}")
            continue

        if summary.get("ok") is not True:
            errors.append(f"required raw diagnostic {name!r} is not validated ok")
            continue

        manifest_path = summary.get("manifest_path")
        if not isinstance(manifest_path, str) or not manifest_path.strip():
            errors.append(f"required raw diagnostic {name!r} has no manifest_path evidence")

    if required_count == 0:
        errors.append("campaign.json must define at least one required raw diagnostic before reduced validation")

    return errors