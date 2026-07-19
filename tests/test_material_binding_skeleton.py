"""Phase 8B Step 1: Material binding skeleton tests.

Tests:
1. Each MaterialRequirement generates exactly one slot.
2. Immutable fields cannot be modified.
3. Unique candidate → uniquely_resolved.
4. Multiple candidates → ambiguous.
5. No candidates → requires_generation.
6. Slot hash is deterministic.
"""

from __future__ import annotations

from openmc_agent.plan_builder.material_binding_skeleton import (
    MaterialBindingResolution,
    MaterialBindingSkeleton,
    MaterialBindingSlot,
    compile_material_binding_skeleton,
)


def test_each_requirement_generates_slot() -> None:
    """Every requirement must produce exactly one slot."""
    class _StubReq:
        requirement_id = "req_1"
        role = "fuel"
        source_variant_id = "var_1"
        localized_insert_requirement_id = None
        required_composition_status = "complete"

    class _StubReqSet:
        requirements = [_StubReq()]
        requirement_set_hash = "abc123"

    class _StubInventory:
        inventory_hash = "def456"

    skeleton = compile_material_binding_skeleton(
        inventory=_StubInventory(),
        material_requirement_set=_StubReqSet(),
        accepted_facts=None,
    )
    assert len(skeleton.slots) == 1
    assert skeleton.slots[0].requirement_id == "req_1"


def test_unique_candidate_uniquely_resolved() -> None:
    """Single matching candidate → uniquely_resolved."""
    class _StubReq:
        requirement_id = "req_1"
        role = "fuel"
        source_variant_id = "var_1"
        localized_insert_requirement_id = None
        required_composition_status = "complete"

    class _StubReqSet:
        requirements = [_StubReq()]
        requirement_set_hash = "abc123"

    class _StubInventory:
        inventory_hash = "def456"

    existing_patch = {
        "materials": [
            {"material_id": "mat_1", "role": "fuel", "source_variant_id": "var_1"},
        ]
    }
    skeleton = compile_material_binding_skeleton(
        inventory=_StubInventory(),
        material_requirement_set=_StubReqSet(),
        accepted_facts=None,
        existing_materials_patch=existing_patch,
    )
    assert skeleton.slots[0].resolution_status == MaterialBindingResolution.uniquely_resolved
    assert skeleton.slots[0].assigned_material_id == "mat_1"


def test_no_candidate_requires_generation() -> None:
    """No candidate → requires_generation."""
    class _StubReq:
        requirement_id = "req_1"
        role = "fuel"
        source_variant_id = "var_1"
        localized_insert_requirement_id = None
        required_composition_status = "complete"

    class _StubReqSet:
        requirements = [_StubReq()]
        requirement_set_hash = "abc123"

    class _StubInventory:
        inventory_hash = "def456"

    skeleton = compile_material_binding_skeleton(
        inventory=_StubInventory(),
        material_requirement_set=_StubReqSet(),
        accepted_facts=None,
        existing_materials_patch={"materials": []},
    )
    assert skeleton.slots[0].resolution_status == MaterialBindingResolution.requires_generation
    assert skeleton.slots[0].assigned_material_id is None


def test_ambiguous_with_multiple_candidates() -> None:
    """Multiple candidates → ambiguous."""
    class _StubReq:
        requirement_id = "req_1"
        role = "fuel"
        source_variant_id = "var_1"
        localized_insert_requirement_id = None
        required_composition_status = "complete"

    class _StubReqSet:
        requirements = [_StubReq()]
        requirement_set_hash = "abc123"

    class _StubInventory:
        inventory_hash = "def456"

    existing_patch = {
        "materials": [
            {"material_id": "mat_1", "role": "fuel", "source_variant_id": "var_1"},
            {"material_id": "mat_2", "role": "fuel", "source_variant_id": "var_1"},
        ]
    }
    skeleton = compile_material_binding_skeleton(
        inventory=_StubInventory(),
        material_requirement_set=_StubReqSet(),
        accepted_facts=None,
        existing_materials_patch=existing_patch,
    )
    assert skeleton.slots[0].resolution_status == MaterialBindingResolution.ambiguous
    assert skeleton.slots[0].assigned_material_id is None
    assert len(skeleton.slots[0].candidate_material_ids) == 2


def test_slot_immutable_fields() -> None:
    """Immutable fields must be preserved."""
    class _StubReq:
        requirement_id = "req_1"
        role = "fuel"
        source_variant_id = "var_1"
        localized_insert_requirement_id = None
        required_composition_status = "complete"

    class _StubReqSet:
        requirements = [_StubReq()]
        requirement_set_hash = "abc123"

    class _StubInventory:
        inventory_hash = "def456"

    skeleton = compile_material_binding_skeleton(
        inventory=_StubInventory(),
        material_requirement_set=_StubReqSet(),
        accepted_facts=None,
    )
    assert "material_id" in skeleton.slots[0].immutable_fields
    assert "role" in skeleton.slots[0].immutable_fields
    assert "requirement_id" in skeleton.slots[0].immutable_fields
    assert "source_variant_id" in skeleton.slots[0].immutable_fields


def test_slot_hash_deterministic() -> None:
    """Slot hash must be deterministic for same input."""
    class _StubReq:
        requirement_id = "req_1"
        role = "fuel"
        source_variant_id = "var_1"
        localized_insert_requirement_id = None
        required_composition_status = "complete"

    class _StubReqSet:
        requirements = [_StubReq()]
        requirement_set_hash = "abc123"

    class _StubInventory:
        inventory_hash = "def456"

    skeleton1 = compile_material_binding_skeleton(
        inventory=_StubInventory(),
        material_requirement_set=_StubReqSet(),
        accepted_facts=None,
    )
    skeleton2 = compile_material_binding_skeleton(
        inventory=_StubInventory(),
        material_requirement_set=_StubReqSet(),
        accepted_facts=None,
    )
    assert skeleton1.slots[0].slot_hash == skeleton2.slots[0].slot_hash


def test_skeleton_properties() -> None:
    """Skeleton aggregated properties must be correct."""
    class _StubReq:
        requirement_id = "req_1"
        role = "fuel"
        source_variant_id = "var_1"
        localized_insert_requirement_id = None
        required_composition_status = "complete"

    class _StubReqSet:
        requirements = [_StubReq()]
        requirement_set_hash = "abc123"

    class _StubInventory:
        inventory_hash = "def456"

    existing_patch = {
        "materials": [
            {"material_id": "mat_1", "role": "fuel", "source_variant_id": "var_1"},
        ]
    }
    skeleton = compile_material_binding_skeleton(
        inventory=_StubInventory(),
        material_requirement_set=_StubReqSet(),
        accepted_facts=None,
        existing_materials_patch=existing_patch,
    )
    assert skeleton.resolved_count == 1
    assert skeleton.requires_generation_count == 0
    assert skeleton.ambiguous_count == 0
