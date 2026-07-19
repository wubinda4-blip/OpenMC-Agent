"""Phase 8A Step 7 — research compilation diff + invalidation tests."""

from __future__ import annotations

import pytest

from openmc_agent.plan_investigation.research_recompilation import (
    ResearchCompilationDiff,
    ResearchInvalidationPlan,
    build_research_invalidation_plan,
    compute_compilation_diff,
)


class _FakeReqSet:
    """Minimal stand-in for a requirement set."""

    def __init__(self, *, req_hash: str = "", req_ids: list[str] | None = None,
                 unresolved: list[str] | None = None):
        self.requirement_set_hash = req_hash
        self.requirements = [type("R", (), {"requirement_id": rid})() for rid in (req_ids or [])]
        self.unresolved_requirements = unresolved or []


# ---------------------------------------------------------------------------
# Compilation diff
# ---------------------------------------------------------------------------


def test_diff_detects_material_requirement_change() -> None:
    before = _FakeReqSet(req_hash="h1", req_ids=["m1"])
    after = _FakeReqSet(req_hash="h2", req_ids=["m1", "m2"])
    diff = compute_compilation_diff(
        request_id="r1",
        inventory_before=None, inventory_after=None,
        material_req_before=before, material_req_after=after,
        universe_req_before=None, universe_req_after=None,
    )
    assert diff.has_changes
    assert diff.material_requirement_hash_before == "h1"
    assert diff.material_requirement_hash_after == "h2"
    assert "m2" in diff.added_material_requirement_ids


def test_diff_no_changes_when_hashes_match() -> None:
    before = _FakeReqSet(req_hash="h1")
    after = _FakeReqSet(req_hash="h1")
    diff = compute_compilation_diff(
        request_id="r1",
        inventory_before=None, inventory_after=None,
        material_req_before=before, material_req_after=after,
        universe_req_before=None, universe_req_after=None,
    )
    assert not diff.has_changes


def test_diff_hash_is_stable() -> None:
    before = _FakeReqSet(req_hash="h1")
    after = _FakeReqSet(req_hash="h2")
    diff1 = compute_compilation_diff(
        request_id="r1", inventory_before=None, inventory_after=None,
        material_req_before=before, material_req_after=after,
        universe_req_before=None, universe_req_after=None,
    )
    diff2 = compute_compilation_diff(
        request_id="r1", inventory_before=None, inventory_after=None,
        material_req_before=before, material_req_after=after,
        universe_req_before=None, universe_req_after=None,
    )
    assert diff1.diff_hash == diff2.diff_hash


# ---------------------------------------------------------------------------
# Invalidation plan
# ---------------------------------------------------------------------------


def test_invalidation_plan_invalidates_materials_on_mreq_change() -> None:
    before = _FakeReqSet(req_hash="h1")
    after = _FakeReqSet(req_hash="h2", req_ids=["m_new"])
    diff = compute_compilation_diff(
        request_id="r1", inventory_before=None, inventory_after=None,
        material_req_before=before, material_req_after=after,
        universe_req_before=None, universe_req_after=None,
    )
    plan = build_research_invalidation_plan(
        request_id="r1", diff=diff, gate_id="material_universe",
    )
    assert "materials" in plan.invalidated_patch_types
    assert "universes" in plan.invalidated_patch_types
    assert "facts" in plan.preserved_patch_types
    assert plan.gate_replay_required


def test_invalidation_plan_invalidates_universes_on_ureq_change() -> None:
    before = _FakeReqSet(req_hash="h1")
    after = _FakeReqSet(req_hash="h2")
    diff = compute_compilation_diff(
        request_id="r1", inventory_before=None, inventory_after=None,
        material_req_before=None, material_req_after=None,
        universe_req_before=before, universe_req_after=after,
    )
    plan = build_research_invalidation_plan(
        request_id="r1", diff=diff, gate_id="material_universe",
    )
    assert "universes" in plan.invalidated_patch_types
    assert "materials" not in plan.invalidated_patch_types


def test_invalidation_plan_preserves_facts_always() -> None:
    """Facts are never invalidated by research."""

    before = _FakeReqSet(req_hash="h1")
    after = _FakeReqSet(req_hash="h2")
    diff = compute_compilation_diff(
        request_id="r1", inventory_before=None, inventory_after=None,
        material_req_before=before, material_req_after=after,
        universe_req_before=None, universe_req_after=None,
    )
    plan = build_research_invalidation_plan(
        request_id="r1", diff=diff, gate_id="material_universe",
    )
    assert "facts" not in plan.invalidated_patch_types
    assert "facts" in plan.preserved_patch_types


def test_invalidation_plan_respects_blocking_finding_owners() -> None:
    """Blocking finding owners are honoured even when hashes match."""

    before = _FakeReqSet(req_hash="h1")
    after = _FakeReqSet(req_hash="h1")
    diff = compute_compilation_diff(
        request_id="r1", inventory_before=None, inventory_after=None,
        material_req_before=before, material_req_after=after,
        universe_req_before=None, universe_req_after=None,
    )
    plan = build_research_invalidation_plan(
        request_id="r1", diff=diff, gate_id="material_universe",
        blocking_finding_owners=("materials", "universes"),
    )
    assert "materials" in plan.invalidated_patch_types
    assert "universes" in plan.invalidated_patch_types


def test_invalidation_plan_does_not_touch_placement_or_axial() -> None:
    """Step 7 must NOT invalidate Placement / Axial patches."""

    before = _FakeReqSet(req_hash="h1")
    after = _FakeReqSet(req_hash="h2")
    diff = compute_compilation_diff(
        request_id="r1", inventory_before=None, inventory_after=None,
        material_req_before=before, material_req_after=after,
        universe_req_before=None, universe_req_after=None,
    )
    plan = build_research_invalidation_plan(
        request_id="r1", diff=diff, gate_id="material_universe",
    )
    for ptype in ("pin_map", "assembly_catalog", "core_layout",
                  "localized_insert_profiles", "axial_layers",
                  "axial_overlays", "base_path_axial_profiles"):
        assert ptype not in plan.invalidated_patch_types


def test_invalidation_plan_hash_is_stable() -> None:
    before = _FakeReqSet(req_hash="h1")
    after = _FakeReqSet(req_hash="h2")
    diff = compute_compilation_diff(
        request_id="r1", inventory_before=None, inventory_after=None,
        material_req_before=before, material_req_after=after,
        universe_req_before=None, universe_req_after=None,
    )
    plan1 = build_research_invalidation_plan(request_id="r1", diff=diff)
    plan2 = build_research_invalidation_plan(request_id="r1", diff=diff)
    assert plan1.invalidation_hash == plan2.invalidation_hash
