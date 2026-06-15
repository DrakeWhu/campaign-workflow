from __future__ import annotations

import copy
from typing import Any

from campaign_workflow.core.state import VALID_STATES, actor, now_utc


RAW_VALIDATION_COMPATIBLE_STATES = {
    "Sim_done",
    "Validation_failed",
    "Raw_validated",
}


REDUCED_VALIDATION_COMPATIBLE_STATES = {
    "Raw_validated",
    "Analyzing",
    "Analysis_failed",
    "Reduced_validated",
    "Validation_failed",
}


LEGACY_REDUCED_VALIDATION_COMPATIBLE_STATES = {
    "Created",
    "Validation_failed",
    "Reduced_validated",
}

ANALYSIS_START_COMPATIBLE_STATES = {
    "Raw_validated",
    "Analysis_failed",
}

ANALYSIS_FINAL_COMPATIBLE_STATES = {
    "Analyzing",
}

RAW_DELETE_ELIGIBILITY_COMPATIBLE_STATES = {
    "Reduced_validated",
    "Raw_delete_eligible",
}

RAW_DELETE_ELIGIBILITY_FINAL_STATES = {
    "Raw_delete_eligible",
}

RAW_DELETE_EXECUTE_COMPATIBLE_STATES = {
    "Raw_delete_eligible",
}

def state_name(state_doc: dict[str, Any]) -> str:
    state = state_doc.get("state")
    if not isinstance(state, str):
        raise ValueError("state.json missing string field 'state'")
    if state not in VALID_STATES:
        raise ValueError(f"invalid state: {state!r}")
    return state


def ensure_raw_validation_compatible(state_doc: dict[str, Any]) -> None:
    current = state_name(state_doc)
    if current not in RAW_VALIDATION_COMPATIBLE_STATES:
        allowed = ", ".join(sorted(RAW_VALIDATION_COMPATIBLE_STATES))
        raise ValueError(f"raw validation is not allowed from state {current!r}; allowed states: {allowed}")


def ensure_reduced_validation_compatible(state_doc: dict[str, Any]) -> None:
    current = state_name(state_doc)
    if current not in REDUCED_VALIDATION_COMPATIBLE_STATES:
        allowed = ", ".join(sorted(REDUCED_VALIDATION_COMPATIBLE_STATES))
        raise ValueError(f"reduced validation is not allowed from state {current!r}; allowed states: {allowed}")


def ensure_legacy_reduced_validation_compatible(state_doc: dict[str, Any]) -> None:
    current = state_name(state_doc)
    if current not in LEGACY_REDUCED_VALIDATION_COMPATIBLE_STATES:
        allowed = ", ".join(sorted(LEGACY_REDUCED_VALIDATION_COMPATIBLE_STATES))
        raise ValueError(
            f"legacy reduced-only validation is not allowed from state {current!r}; allowed states: {allowed}"
        )


def ensure_analysis_start_compatible(
    state_doc: dict[str, Any],
    *,
    allow_raw_delete_eligible: bool = False,
) -> None:
    current = state_name(state_doc)
    allowed_states = set(ANALYSIS_START_COMPATIBLE_STATES)
    if allow_raw_delete_eligible:
        allowed_states.add("Raw_delete_eligible")

    if current not in allowed_states:
        allowed = ", ".join(sorted(allowed_states))
        raise ValueError(f"analysis is not allowed from state {current!r}; allowed states: {allowed}")

def ensure_analysis_final_compatible(state_doc: dict[str, Any]) -> None:
    current = state_name(state_doc)
    if current not in ANALYSIS_FINAL_COMPATIBLE_STATES:
        allowed = ", ".join(sorted(ANALYSIS_FINAL_COMPATIBLE_STATES))
        raise ValueError(f"analysis finalization is not allowed from state {current!r}; allowed states: {allowed}")


def transition_state_document(
    state_doc: dict[str, Any],
    *,
    to_state: str,
    operation: str,
    reason: str,
) -> dict[str, Any]:
    """Return a copied state document with one appended transition."""
    if to_state not in VALID_STATES:
        raise ValueError(f"invalid target state: {to_state!r}")

    previous = state_name(state_doc)
    timestamp = now_utc()
    updated = copy.deepcopy(state_doc)

    history = updated.setdefault("history", [])
    if not isinstance(history, list):
        raise ValueError("state.json history must be a list")

    updated["state"] = to_state
    updated["updated_at"] = timestamp
    history.append(
        {
            "timestamp": timestamp,
            "from": previous,
            "to": to_state,
            "operation": operation,
            "reason": reason,
            "actor": actor(),
        }
    )

    return updated


def raw_validation_success_transition(state_doc: dict[str, Any]) -> dict[str, Any]:
    ensure_raw_validation_compatible(state_doc)
    return transition_state_document(
        state_doc,
        to_state="Raw_validated",
        operation="validate_raw_case",
        reason="all required raw diagnostics validated",
    )


def raw_validation_failure_transition(state_doc: dict[str, Any]) -> dict[str, Any]:
    ensure_raw_validation_compatible(state_doc)
    return transition_state_document(
        state_doc,
        to_state="Validation_failed",
        operation="validate_raw_case",
        reason="raw diagnostic validation failed",
    )


def reduced_validation_success_transition(state_doc: dict[str, Any]) -> dict[str, Any]:
    ensure_reduced_validation_compatible(state_doc)
    return transition_state_document(
        state_doc,
        to_state="Reduced_validated",
        operation="validate_reduced_case",
        reason="all required reduced outputs validated",
    )


def reduced_validation_failure_transition(state_doc: dict[str, Any]) -> dict[str, Any]:
    ensure_reduced_validation_compatible(state_doc)
    return transition_state_document(
        state_doc,
        to_state="Validation_failed",
        operation="validate_reduced_case",
        reason="reduced output validation failed",
    )


def legacy_reduced_validation_success_transition(state_doc: dict[str, Any]) -> dict[str, Any]:
    ensure_legacy_reduced_validation_compatible(state_doc)
    return transition_state_document(
        state_doc,
        to_state="Reduced_validated",
        operation="validate_reduced_case",
        reason="legacy reduced-only outputs validated without raw diagnostic evidence",
    )


def legacy_reduced_validation_failure_transition(state_doc: dict[str, Any]) -> dict[str, Any]:
    ensure_legacy_reduced_validation_compatible(state_doc)
    return transition_state_document(
        state_doc,
        to_state="Validation_failed",
        operation="validate_reduced_case",
        reason="legacy reduced-only output validation failed",
    )


def mark_sim_done_transition(state_doc: dict[str, Any], *, reason: str) -> dict[str, Any]:
    current = state_name(state_doc)
    if current != "Created":
        raise ValueError(f"mark_sim_done is not allowed from state {current!r}; allowed state: Created")
    return transition_state_document(
        state_doc,
        to_state="Sim_done",
        operation="mark_sim_done",
        reason=reason,
    )


def analysis_start_transition(
    state_doc: dict[str, Any],
    *,
    allow_raw_delete_eligible: bool = False,
) -> dict[str, Any]:
    ensure_analysis_start_compatible(
        state_doc,
        allow_raw_delete_eligible=allow_raw_delete_eligible,
    )
    return transition_state_document(
        state_doc,
        to_state="Analyzing",
        operation="analyze_case",
        reason="case-local analysis started",
    )


def analysis_success_transition(state_doc: dict[str, Any]) -> dict[str, Any]:
    ensure_analysis_final_compatible(state_doc)
    return transition_state_document(
        state_doc,
        to_state="Reduced_validated",
        operation="analyze_case",
        reason="analysis completed and configured reduced outputs validated",
    )


def analysis_failure_transition(state_doc: dict[str, Any], *, reason: str) -> dict[str, Any]:
    ensure_analysis_final_compatible(state_doc)
    return transition_state_document(
        state_doc,
        to_state="Analysis_failed",
        operation="analyze_case",
        reason=reason,
    )

def ensure_raw_delete_eligibility_compatible(state_doc: dict[str, Any]) -> None:
    current = state_name(state_doc)
    if current not in RAW_DELETE_ELIGIBILITY_COMPATIBLE_STATES:
        allowed = ", ".join(sorted(RAW_DELETE_ELIGIBILITY_COMPATIBLE_STATES))
        raise ValueError(
            f"raw delete eligibility is not allowed from state {current!r}; "
            f"allowed states: {allowed}"
        )


def raw_delete_eligible_transition(state_doc: dict[str, Any]) -> dict[str, Any]:
    ensure_raw_delete_eligibility_compatible(state_doc)

    current = state_name(state_doc)
    if current == "Raw_delete_eligible":
        return transition_state_document(
            state_doc,
            to_state="Raw_delete_eligible",
            operation="mark_raw_delete_eligible",
            reason="raw delete eligibility revalidated",
        )

    return transition_state_document(
        state_doc,
        to_state="Raw_delete_eligible",
        operation="mark_raw_delete_eligible",
        reason="raw and reduced evidence allow entering cleanup dry-run phase",
    )


def ensure_raw_delete_execute_compatible(state_doc: dict[str, Any]) -> None:
    current = state_name(state_doc)
    if current not in RAW_DELETE_EXECUTE_COMPATIBLE_STATES:
        allowed = ", ".join(sorted(RAW_DELETE_EXECUTE_COMPATIBLE_STATES))
        raise ValueError(
            f"raw delete execute is not allowed from state {current!r}; "
            f"allowed states: {allowed}"
        )


def raw_deleted_transition(state_doc: dict[str, Any]) -> dict[str, Any]:
    ensure_raw_delete_execute_compatible(state_doc)
    return transition_state_document(
        state_doc,
        to_state="Raw_deleted",
        operation="cleanup_raw_case",
        reason="raw cleanup executed from validated dry-run manifest",
    )