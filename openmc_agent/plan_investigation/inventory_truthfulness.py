"""Phase 8A Step 5 truthfulness violation codes for the inventory layer.

Extends the Step 4 truthfulness auditor with inventory-specific checks.
Mode-aware: when ``plan_investigation_mode == "off"``, inventory-related
checks are skipped entirely.
"""

from __future__ import annotations

from typing import Any, Mapping

__all__ = [
    "INVENTORY_TRUTH_VIOLATIONS",
    "inventory_truth_violations_for_run",
    "TV_MANDATORY_INVESTIGATION_ACTION_MISSING",
    "TV_MANDATORY_ACTION_REPORTED_WITHOUT_TOOL_CALL",
    "TV_COMPONENT_EVIDENCE_WITHOUT_VALID_SOURCE_SPAN",
    "TV_COMPONENT_EVIDENCE_VALUE_NOT_SOURCE_BACKED",
    "TV_COMPONENT_EVIDENCE_SYNTHESIS_MISSING",
    "TV_INVENTORY_COMPILED_BEFORE_FACTS_ACCEPTED",
    "TV_INVENTORY_HASH_MISMATCH",
    "TV_INVENTORY_CONTAINS_UNSUPPORTED_IMPLICIT_COMPONENT",
    "TV_INVENTORY_UNRESOLVED_COMPONENT_HIDDEN",
    "TV_MATERIALS_GENERATED_WITHOUT_INVENTORY",
    "TV_UNIVERSES_GENERATED_WITHOUT_INVENTORY",
    "TV_MATERIAL_REQUIREMENT_NOT_COVERED",
    "TV_UNIVERSE_REQUIREMENT_NOT_COVERED",
    "TV_LEGACY_AUXILIARY_FALLBACK_USED_IN_CONTROLLED_MODE",
    "TV_FABRICATED_RADIAL_GAP",
    "TV_FABRICATED_GEOMETRY_VALUE",
    "TV_INVENTORY_ARTIFACT_MISSING",
    "TV_INVENTORY_REASONING_CONTENT_PERSISTED",
    "TV_MU_GATE_ACCEPTED_WITH_INVENTORY_PREFLIGHT_FAILURE",
]


TV_MANDATORY_INVESTIGATION_ACTION_MISSING = (
    "mandatory_investigation_action_missing"
)
TV_MANDATORY_ACTION_REPORTED_WITHOUT_TOOL_CALL = (
    "mandatory_action_reported_without_tool_call"
)
TV_COMPONENT_EVIDENCE_WITHOUT_VALID_SOURCE_SPAN = (
    "component_evidence_without_valid_source_span"
)
TV_COMPONENT_EVIDENCE_VALUE_NOT_SOURCE_BACKED = (
    "component_evidence_value_not_source_backed"
)
TV_COMPONENT_EVIDENCE_SYNTHESIS_MISSING = (
    "component_evidence_synthesis_missing"
)
TV_INVENTORY_COMPILED_BEFORE_FACTS_ACCEPTED = (
    "inventory_compiled_before_facts_accepted"
)
TV_INVENTORY_HASH_MISMATCH = "inventory_hash_mismatch"
TV_INVENTORY_CONTAINS_UNSUPPORTED_IMPLICIT_COMPONENT = (
    "inventory_contains_unsupported_implicit_component"
)
TV_INVENTORY_UNRESOLVED_COMPONENT_HIDDEN = (
    "inventory_unresolved_component_hidden"
)
TV_MATERIALS_GENERATED_WITHOUT_INVENTORY = (
    "materials_generated_without_inventory"
)
TV_UNIVERSES_GENERATED_WITHOUT_INVENTORY = (
    "universes_generated_without_inventory"
)
TV_MATERIAL_REQUIREMENT_NOT_COVERED = "material_requirement_not_covered"
TV_UNIVERSE_REQUIREMENT_NOT_COVERED = "universe_requirement_not_covered"
TV_LEGACY_AUXILIARY_FALLBACK_USED_IN_CONTROLLED_MODE = (
    "legacy_auxiliary_fallback_used_in_controlled_mode"
)
TV_FABRICATED_RADIAL_GAP = "fabricated_radial_gap"
TV_FABRICATED_GEOMETRY_VALUE = "fabricated_geometry_value"
TV_INVENTORY_ARTIFACT_MISSING = "inventory_artifact_missing"
TV_INVENTORY_REASONING_CONTENT_PERSISTED = (
    "inventory_reasoning_content_persisted"
)
TV_MU_GATE_ACCEPTED_WITH_INVENTORY_PREFLIGHT_FAILURE = (
    "material_universe_gate_accepted_with_inventory_preflight_failure"
)


INVENTORY_TRUTH_VIOLATIONS: tuple[str, ...] = (
    TV_MANDATORY_INVESTIGATION_ACTION_MISSING,
    TV_MANDATORY_ACTION_REPORTED_WITHOUT_TOOL_CALL,
    TV_COMPONENT_EVIDENCE_WITHOUT_VALID_SOURCE_SPAN,
    TV_COMPONENT_EVIDENCE_VALUE_NOT_SOURCE_BACKED,
    TV_COMPONENT_EVIDENCE_SYNTHESIS_MISSING,
    TV_INVENTORY_COMPILED_BEFORE_FACTS_ACCEPTED,
    TV_INVENTORY_HASH_MISMATCH,
    TV_INVENTORY_CONTAINS_UNSUPPORTED_IMPLICIT_COMPONENT,
    TV_INVENTORY_UNRESOLVED_COMPONENT_HIDDEN,
    TV_MATERIALS_GENERATED_WITHOUT_INVENTORY,
    TV_UNIVERSES_GENERATED_WITHOUT_INVENTORY,
    TV_MATERIAL_REQUIREMENT_NOT_COVERED,
    TV_UNIVERSE_REQUIREMENT_NOT_COVERED,
    TV_LEGACY_AUXILIARY_FALLBACK_USED_IN_CONTROLLED_MODE,
    TV_FABRICATED_RADIAL_GAP,
    TV_FABRICATED_GEOMETRY_VALUE,
    TV_INVENTORY_ARTIFACT_MISSING,
    TV_INVENTORY_REASONING_CONTENT_PERSISTED,
    TV_MU_GATE_ACCEPTED_WITH_INVENTORY_PREFLIGHT_FAILURE,
)


def inventory_truth_violations_for_run(
    *,
    run_summary: Mapping[str, Any],
    inventory_summary: Mapping[str, Any] | None = None,
    artifact_text_snapshot: str | None = None,
) -> list[str]:
    """Return the list of inventory truth-violation codes for one run.

    Mode-aware: off mode skips all inventory checks.
    """

    mode = str(run_summary.get("plan_investigation_mode", "off")).lower()
    if mode == "off":
        return []

    violations: list[str] = []
    inv = inventory_summary or {}

    # 1. Controlled mode requires inventory compilation.
    if mode == "controlled":
        if not inv.get("inventory_compiled"):
            # Materials or Universes generated without an inventory.
            if run_summary.get("materials_patch_generated"):
                violations.append(TV_MATERIALS_GENERATED_WITHOUT_INVENTORY)
            if run_summary.get("universes_patch_generated"):
                violations.append(TV_UNIVERSES_GENERATED_WITHOUT_INVENTORY)

    # 2. Inventory hash mismatch.
    if inv.get("inventory_hash_mismatch"):
        violations.append(TV_INVENTORY_HASH_MISMATCH)

    # 3. Unsupported implicit component in controlled mode.
    if mode == "controlled" and inv.get("unsupported_implicit_component_count", 0) > 0:
        violations.append(TV_INVENTORY_CONTAINS_UNSUPPORTED_IMPLICIT_COMPONENT)

    # 4. Unresolved component hidden (not reported).
    if inv.get("unresolved_component_count", 0) > 0 and not inv.get(
        "unresolved_components_reported", True
    ):
        violations.append(TV_INVENTORY_UNRESOLVED_COMPONENT_HIDDEN)

    # 5. Material requirement not covered.
    if inv.get("material_requirement_uncovered_count", 0) > 0:
        violations.append(TV_MATERIAL_REQUIREMENT_NOT_COVERED)

    # 6. Universe requirement not covered.
    if inv.get("universe_requirement_uncovered_count", 0) > 0:
        violations.append(TV_UNIVERSE_REQUIREMENT_NOT_COVERED)

    # 7. Legacy auxiliary fallback in controlled mode.
    if mode == "controlled" and inv.get("legacy_auxiliary_fallback_used"):
        violations.append(TV_LEGACY_AUXILIARY_FALLBACK_USED_IN_CONTROLLED_MODE)

    # 8. Fabricated geometry values.
    if inv.get("fabricated_geometry_value_count", 0) > 0:
        violations.append(TV_FABRICATED_GEOMETRY_VALUE)

    # 9. Inventory artifact missing in controlled mode.
    if (
        mode == "controlled"
        and inv.get("inventory_compiled")
        and not inv.get("inventory_artifact_written")
    ):
        violations.append(TV_INVENTORY_ARTIFACT_MISSING)

    # 10. reasoning_content leaked into inventory artifact.
    if artifact_text_snapshot and "reasoning_content" in artifact_text_snapshot:
        violations.append(TV_INVENTORY_REASONING_CONTENT_PERSISTED)

    # 11. MU Gate accepted despite inventory preflight failure.
    if (
        run_summary.get("material_universe_gate_accepted")
        and inv.get("inventory_preflight_passed") is False
    ):
        violations.append(TV_MU_GATE_ACCEPTED_WITH_INVENTORY_PREFLIGHT_FAILURE)

    return violations
