"""Phase 8B Step 1: Universe binding skeleton tests.

Tests:
1. Each UniverseRequirement generates exactly one slot.
2. geometry_profile_id is injected from requirement.
3. source_requirement_ids are injected from requirement.
4. Unique candidate → uniquely_resolved.
5. No candidates → requires_generation.
6. Immutable fields preserved.
"""

from __future__ import annotations

from openmc_agent.plan_builder.universe_binding_skeleton import (
    UniverseBindingResolution,
    UniverseBindingSkeleton,
    compile_universe_binding_skeleton,
)


def test_each_requirement_generates_slot() -> None:
    """Every universe requirement must produce exactly one slot."""
    class _StubReq:
        requirement_id = "uni_req_1"
        geometry_profile_id = "profile_1"
        source_requirement_ids = ["mat_req_1"]
        component_kind = "fuel_pin"
        profile_kind = "radial"
        fuel_variant_id = "var_1"
        required_cell_roles = ["fuel"]
        required_material_roles = ["fuel"]
        resolved = True

    class _StubReqSet:
        requirements = [_StubReq()]
        requirement_set_hash = "abc123"

    class _StubMatBindSlot:
        requirement_id = "mat_req_1"
        required_role = "fuel"

    class _StubMatBindSkeleton:
        slots = [_StubMatBindSlot()]
        requirement_set_hash = "def456"

    class _StubInventory:
        inventory_hash = "ghi789"

    class _StubFacts:
        pass

    skeleton = compile_universe_binding_skeleton(
        inventory=_StubInventory(),
        universe_requirement_set=_StubReqSet(),
        material_requirement_set=None,
        material_binding_skeleton=_StubMatBindSkeleton(),
        accepted_facts=_StubFacts(),
    )
    assert len(skeleton.slots) == 1
    assert skeleton.slots[0].universe_requirement_id == "uni_req_1"
    assert skeleton.slots[0].geometry_profile_id == "profile_1"
    assert skeleton.slots[0].source_requirement_ids == ["mat_req_1"]


def test_unique_candidate_uniquely_resolved() -> None:
    """Single matching candidate → uniquely_resolved."""
    class _StubReq:
        requirement_id = "uni_req_1"
        geometry_profile_id = "profile_1"
        source_requirement_ids = ["mat_req_1"]
        component_kind = "fuel_pin"
        profile_kind = "radial"
        fuel_variant_id = None
        required_cell_roles = ["fuel"]
        required_material_roles = ["fuel"]
        resolved = True

    class _StubReqSet:
        requirements = [_StubReq()]
        requirement_set_hash = "abc123"

    class _StubMatBindSlot:
        requirement_id = "mat_req_1"
        required_role = "fuel"

    class _StubMatBindSkeleton:
        slots = [_StubMatBindSlot()]
        requirement_set_hash = "def456"

    class _StubInventory:
        inventory_hash = "ghi789"

    class _StubFacts:
        pass

    existing_patch = {
        "universes": [
            {"universe_id": "uni_1", "geometry_profile_id": "profile_1", "cells": []},
        ]
    }
    skeleton = compile_universe_binding_skeleton(
        inventory=_StubInventory(),
        universe_requirement_set=_StubReqSet(),
        material_requirement_set=None,
        material_binding_skeleton=_StubMatBindSkeleton(),
        accepted_facts=_StubFacts(),
        existing_universes_patch=existing_patch,
    )
    assert skeleton.slots[0].resolution_status == UniverseBindingResolution.uniquely_resolved
    assert skeleton.slots[0].assigned_universe_id == "uni_1"


def test_no_candidate_requires_generation() -> None:
    """No candidate → requires_generation."""
    class _StubReq:
        requirement_id = "uni_req_1"
        geometry_profile_id = "profile_1"
        source_requirement_ids = []
        component_kind = "fuel_pin"
        profile_kind = "radial"
        fuel_variant_id = None
        required_cell_roles = ["fuel"]
        required_material_roles = ["fuel"]
        resolved = True

    class _StubReqSet:
        requirements = [_StubReq()]
        requirement_set_hash = "abc123"

    class _StubMatBindSkeleton:
        slots = []
        requirement_set_hash = ""

    class _StubInventory:
        inventory_hash = "ghi789"

    class _StubFacts:
        pass

    skeleton = compile_universe_binding_skeleton(
        inventory=_StubInventory(),
        universe_requirement_set=_StubReqSet(),
        material_requirement_set=None,
        material_binding_skeleton=_StubMatBindSkeleton(),
        accepted_facts=_StubFacts(),
        existing_universes_patch={"universes": []},
    )
    assert skeleton.slots[0].resolution_status == UniverseBindingResolution.requires_generation
    assert skeleton.slots[0].assigned_universe_id is None


def test_immutable_fields() -> None:
    """Immutable fields must be preserved."""
    class _StubReq:
        requirement_id = "uni_req_1"
        geometry_profile_id = "profile_1"
        source_requirement_ids = ["mat_req_1"]
        component_kind = "fuel_pin"
        profile_kind = "radial"
        fuel_variant_id = None
        required_cell_roles = ["fuel"]
        required_material_roles = ["fuel"]
        resolved = True

    class _StubReqSet:
        requirements = [_StubReq()]
        requirement_set_hash = "abc123"

    class _StubMatBindSkeleton:
        slots = []
        requirement_set_hash = ""

    class _StubInventory:
        inventory_hash = "ghi789"

    class _StubFacts:
        pass

    skeleton = compile_universe_binding_skeleton(
        inventory=_StubInventory(),
        universe_requirement_set=_StubReqSet(),
        material_requirement_set=None,
        material_binding_skeleton=_StubMatBindSkeleton(),
        accepted_facts=_StubFacts(),
    )
    assert "universe_id" in skeleton.slots[0].immutable_fields
    assert "geometry_profile_id" in skeleton.slots[0].immutable_fields
    assert "source_requirement_ids" in skeleton.slots[0].immutable_fields
    assert "slot_id" in skeleton.slots[0].immutable_fields
    assert "component_kind" in skeleton.slots[0].immutable_fields
    assert "profile_kind" in skeleton.slots[0].immutable_fields


def test_material_binding_resolved() -> None:
    """Material bindings should be resolved from the material skeleton."""
    class _StubReq:
        requirement_id = "uni_req_1"
        geometry_profile_id = "profile_1"
        source_requirement_ids = ["mat_req_1"]
        component_kind = "fuel_pin"
        profile_kind = "radial"
        fuel_variant_id = None
        required_cell_roles = ["fuel"]
        required_material_roles = ["fuel"]
        resolved = True

    class _StubReqSet:
        requirements = [_StubReq()]
        requirement_set_hash = "abc123"

    class _StubMatBindSlot:
        requirement_id = "mat_req_1"
        required_role = "fuel"

    class _StubMatBindSkeleton:
        slots = [_StubMatBindSlot()]
        requirement_set_hash = "def456"

    class _StubInventory:
        inventory_hash = "ghi789"

    class _StubFacts:
        pass

    skeleton = compile_universe_binding_skeleton(
        inventory=_StubInventory(),
        universe_requirement_set=_StubReqSet(),
        material_requirement_set=None,
        material_binding_skeleton=_StubMatBindSkeleton(),
        accepted_facts=_StubFacts(),
    )
    assert "mat_req_1" in skeleton.slots[0].resolved_material_bindings
    assert skeleton.slots[0].resolved_material_bindings["mat_req_1"] == "fuel"
