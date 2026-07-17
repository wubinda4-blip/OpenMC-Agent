"""Phase 4: Material-Universe binding view."""

from __future__ import annotations

from openmc_agent.plan_builder.closed_loop.material_universe_binding import build_material_universe_binding_view
from openmc_agent.plan_builder.state import PlanBuildState, PlanPatchEnvelope


def _state(*, materials=None, universes=None, facts=None) -> PlanBuildState:
    state = PlanBuildState(state_id="mu-binding", requirement_text="reactor-neutral")
    facts = facts or {"patch_type": "facts", "model_scope": "single_assembly", "fuel_variant_requirements": [{"variant_id": "fuel_21", "enrichment_wt_percent": 2.1, "assembly_type_ids": []}]}
    materials = materials or {"patch_type": "materials", "materials": [
        {"material_id": "fuel_21", "name": "fuel 2.1", "role": "fuel", "density_g_cm3": 10.0, "composition": {"U235": 2.1, "U238": 97.9}, "source_variant_id": "fuel_21"},
        {"material_id": "clad", "name": "clad", "role": "cladding", "density_g_cm3": 6.5},
        {"material_id": "coolant", "name": "water", "role": "coolant", "density_g_cm3": 0.99},
    ]}
    universes = universes or {"patch_type": "universes", "universes": [
        {"universe_id": "fuel", "kind": "fuel_pin", "cells": [
            {"id": "fuel_pellet", "role": "fuel", "material_id": "fuel_21", "region_kind": "cylinder", "r_min_cm": 0, "r_max_cm": 0.4},
            {"id": "gap", "role": "gap", "material_id": "coolant", "region_kind": "annulus", "r_min_cm": 0.4, "r_max_cm": 0.41},
            {"id": "clad", "role": "clad", "material_id": "clad", "region_kind": "annulus", "r_min_cm": 0.41, "r_max_cm": 0.45},
            {"id": "moderator", "role": "background", "material_id": "coolant", "region_kind": "background"},
        ]},
    ]}
    for patch in (facts, materials, universes):
        state.add_patch(PlanPatchEnvelope(patch_id=patch["patch_type"], patch_type=patch["patch_type"], content=patch, status="valid"))
    return state


def test_binding_view_has_stable_material_and_universe_records() -> None:
    state = _state()
    view = build_material_universe_binding_view(state=state)
    assert len(view.material_records) == 3
    assert len(view.universe_records) == 1
    assert view.universe_records[0].universe_id == "fuel"
    assert view.universe_records[0].kind == "fuel_pin"


def test_cell_bindings_record_material_role_compatibility() -> None:
    state = _state()
    view = build_material_universe_binding_view(state=state)
    bindings = {b.cell_id: b for b in view.cell_material_bindings}
    assert bindings["fuel_pellet"].status == "pass"
    assert bindings["clad"].status == "pass"


def test_unknown_material_reference_recorded_as_unresolved() -> None:
    state = _state(universes={"patch_type": "universes", "universes": [
        {"universe_id": "fuel", "kind": "fuel_pin", "cells": [{"id": "c", "role": "fuel", "material_id": "nonexistent", "region_kind": "cylinder"}]},
    ]})
    view = build_material_universe_binding_view(state=state)
    assert any(item["kind"] == "unknown_material_reference" for item in view.unresolved_references)
    assert any(b.status == "fail" for b in view.cell_material_bindings)


def test_optional_unused_material_not_flagged() -> None:
    """A library/helper material not referenced by any universe is allowed."""
    state = _state(materials={"patch_type": "materials", "materials": [
        {"material_id": "fuel_21", "name": "f", "role": "fuel", "density_g_cm3": 10.0, "source_variant_id": "fuel_21"},
        {"material_id": "library_helper", "name": "lib", "role": "structural", "density_g_cm3": 7.0, "composition_status": "needs_library"},
    ]}, universes={"patch_type": "universes", "universes": [
        {"universe_id": "fuel", "kind": "fuel_pin", "cells": [{"id": "c", "role": "fuel", "material_id": "fuel_21", "region_kind": "cylinder"}]},
    ]})
    view = build_material_universe_binding_view(state=state)
    # library_helper exists but is unreferenced; binding view should not flag it.
    assert any(m.material_id == "library_helper" for m in view.material_records)


def test_fuel_variant_binding_links_material_to_active_fuel_universe() -> None:
    state = _state()
    view = build_material_universe_binding_view(state=state)
    assert len(view.fuel_variant_bindings) == 1
    variant = view.fuel_variant_bindings[0]
    assert variant.variant_id == "fuel_21"
    assert variant.material_id == "fuel_21"
    assert "fuel" in variant.active_fuel_universe_ids
    assert variant.status == "pass"


def test_hash_ordering_is_stable() -> None:
    state = _state()
    view1 = build_material_universe_binding_view(state=state)
    view2 = build_material_universe_binding_view(state=state)
    assert view1.materials_patch_hash == view2.materials_patch_hash
    assert view1.universes_patch_hash == view2.universes_patch_hash
