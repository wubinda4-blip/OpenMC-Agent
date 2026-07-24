"""Canonical control-state labels for source-derived insert contracts."""

from __future__ import annotations

from typing import Any


DEFAULT_CONTROL_STATE_ID = "base"


def canonicalize_control_state_id(value: Any) -> str | None:
    """Normalize a source control-state identifier without inventing variants.

    ``None`` means the source/LLM did not declare a control-state field.
    A blank string is a real, observed campaign state in some inputs; convert
    it to a stable label so downstream truthiness checks and equality matches
    do not treat it as missing.
    """
    if value is None:
        return None
    if isinstance(value, str):
        stripped = value.strip()
        return stripped or DEFAULT_CONTROL_STATE_ID
    return str(value)


def normalize_localized_insert_control_states(facts_patch: dict[str, Any]) -> dict[str, Any]:
    """Return a shallow-normalized FactsPatch dict for localized insert states."""
    requirements = facts_patch.get("localized_insert_requirements")
    if not isinstance(requirements, list):
        return facts_patch
    normalized_requirements: list[Any] = []
    changed = False
    for item in requirements:
        if not isinstance(item, dict):
            normalized_requirements.append(item)
            continue
        normalized_item = dict(item)
        if "control_state_id" in normalized_item:
            normalized_value = canonicalize_control_state_id(
                normalized_item.get("control_state_id")
            )
            if normalized_item.get("control_state_id") != normalized_value:
                normalized_item["control_state_id"] = normalized_value
                changed = True
        normalized_requirements.append(normalized_item)
    if not changed:
        return facts_patch
    normalized_patch = dict(facts_patch)
    normalized_patch["localized_insert_requirements"] = normalized_requirements
    return normalized_patch


__all__ = [
    "DEFAULT_CONTROL_STATE_ID",
    "canonicalize_control_state_id",
    "normalize_localized_insert_control_states",
]
