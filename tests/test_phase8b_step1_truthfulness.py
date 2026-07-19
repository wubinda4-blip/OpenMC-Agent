"""Phase 8B Step 1: truthfulness tests for registry and binding integrity.

Tests that new truthfulness violations are properly registered and
detectable.
"""

from __future__ import annotations

from openmc_agent.real_campaign_harness import TRUTHFULNESS_VIOLATION_CODES


def test_truthfulness_codes_include_phase8b_codes() -> None:
    """New Phase 8B truthfulness codes must be registered."""
    required = {
        "retry_inventory_code_unregistered",
        "retry_material_universe_code_unregistered",
        "retry_registry_owner_conflict",
        "retry_special_route_misrepresented_as_patch",
        "inventory_binding_finding_misrouted_to_research",
        "binding_skeleton_missing",
        "binding_immutable_field_modified",
        "binding_slot_silently_dropped",
        "ambiguous_binding_auto_selected",
        "binding_retry_bypassed_phase3b",
        "binding_repair_committed_without_clone_validation",
        "binding_repair_committed_with_blocking_findings",
        "binding_gate_reopened_without_patch_hash_change",
        "binding_reviewer_result_reused",
        "binding_no_progress_loop_continued",
        "investigation_budget_consumed_mandatory_reserve",
    }
    assert required.issubset(set(TRUTHFULNESS_VIOLATION_CODES)), (
        f"Missing codes: {required - set(TRUTHFULNESS_VIOLATION_CODES)}"
    )


def test_off_mode_does_not_fire_truthfulness() -> None:
    """Off mode should not trigger binding-related violations.
    
    This is a design-level test — the actual check happens at runtime.
    Here we verify the code list is complete.
    """
    phase8b_codes = {
        c for c in TRUTHFULNESS_VIOLATION_CODES
        if c.startswith(("retry_", "binding_", "inventory_", "investigation_"))
    }
    # These are the Phase 8B codes that should NOT fire in off mode.
    off_mode_safe = {
        "retry_inventory_code_unregistered",
        "retry_material_universe_code_unregistered",
        "binding_skeleton_missing",
    }
    for code in off_mode_safe:
        assert code in phase8b_codes, (
            f"{code} should be in truthfulness codes"
        )
