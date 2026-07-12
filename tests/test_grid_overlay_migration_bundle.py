"""Tests for the P0-D5B deterministic grid-overlay migration repair bundle.

Covers the full diagnose -> propose -> evaluate -> commit pipeline for the
VERA3B regression where an LLM emitted ``replace_universe_family`` lattice
transformations referencing an undefined ``grid_cell`` while the plan already
carries 8 correct ``spacer_grid`` axial overlays.  The deterministic repair
must remove the redundant grid transformations and clean up layer references
without touching non-grid loadings, pin counts, or overlays.
"""

from __future__ import annotations

import copy
import json
from collections import Counter
from pathlib import Path

from openmc_agent.plan_builder.assembler import assemble_simulation_plan_from_patches
from openmc_agent.plan_builder.component_profile_repair import commit_accepted_repair_bundle
from openmc_agent.plan_builder.grid_loading_repair import (
    diagnose_grid_loading_failure,
    evaluate_grid_migration_repair_bundle,
    propose_grid_migration_repair_bundle,
)
from openmc_agent.plan_builder.patches import parse_patch_content
from openmc_agent.plan_builder.state import PlanBuildState, PlanPatchEnvelope
from openmc_agent.schemas import SimulationPlan
from openmc_agent.validator import validate_simulation_plan

_FIXTURE_DIR = Path(__file__).parent / "fixtures" / "vera3_patches"

_GRID_LOADING_IDS = ("spacer_grid_loadings", "top_grid_loading")
_NON_GRID_LOADING_IDS = (
    "end_plug_loading",
    "plenum_loading",
    "pyrex_active_loading",
    "thimble_plug_loading",
    "shoulder_water_loading",
)


def _load_fixture(variant: str = "3b") -> list[dict]:
    path = _FIXTURE_DIR / f"vera3_{variant}_patches.json"
    with open(path) as f:
        return json.load(f)["patches"]


def _inject_grid_loadings(patches: list[dict]) -> list[dict]:
    out = [copy.deepcopy(p) for p in patches]
    for p in out:
        if p.get("patch_type") != "axial_layers":
            continue
        p.setdefault("lattice_loadings", []).append({
            "loading_id": "spacer_grid_loadings",
            "base_lattice_id": "assembly_lattice",
            "derived_lattice_id": "assembly_lattice_grid",
            "transformations": [
                {
                    "operation_id": "replace_water_with_grid",
                    "operation_kind": "replace_universe_family",
                    "replacement_universe_id": "grid_cell",
                    "source_universe_id": "guide_tube",
                    "purpose": "Replace water with spacer grid",
                }
            ],
            "purpose": "Redundant spacer grid loading",
        })
        p["lattice_loadings"].append({
            "loading_id": "top_grid_loading",
            "base_lattice_id": "assembly_lattice",
            "derived_lattice_id": "assembly_lattice_top_grid",
            "transformations": [
                {
                    "operation_id": "replace_water_with_top_grid",
                    "operation_kind": "replace_universe_family",
                    "replacement_universe_id": "grid_cell",
                    "source_universe_id": "guide_tube",
                    "purpose": "Replace water with top spacer grid",
                }
            ],
            "purpose": "Redundant top spacer grid loading",
        })
        for layer in p.get("layers", []):
            if layer["layer_id"] == "active_fuel_pyrex_span":
                layer["loading_id"] = None
                layer["loading_ids"] = ["pyrex_active_loading", "spacer_grid_loadings"]
            elif layer["layer_id"] == "upper_plenum_middle_thimble":
                layer["loading_ids"] = [
                    "plenum_loading", "thimble_plug_loading", "top_grid_loading",
                ]
    return out


def _build_state_with_grid_issue(
    variant: str = "3b",
) -> tuple[PlanBuildState, SimulationPlan, "ValidationReport"]:  # type: name-error
    patches = _inject_grid_loadings(_load_fixture(variant))
    state = PlanBuildState(state_id="test-grid", requirement_text="generic assembly")
    for payload in patches:
        content = copy.deepcopy(payload)
        state.add_patch(PlanPatchEnvelope(
            patch_id=content["patch_type"],
            patch_type=content["patch_type"],
            content=content,
            status="valid",
        ))
    assembled = assemble_simulation_plan_from_patches(
        [parse_patch_content(p.patch_type, p.content) for p in state.patches.values()],
        strict=True,
    )
    assert assembled.ok and assembled.plan is not None, (
        f"fixture {variant!r} failed to assemble: {assembled.summary}"
    )
    state.assembled_plan = assembled.plan.model_dump(mode="json")
    plan = assembled.plan
    report = validate_simulation_plan(plan, requirement="generic assembly")
    return state, plan, report


def _run_full_bundle() -> tuple[PlanBuildState, SimulationPlan, "ValidationReport", object, object, object]:
    state, plan, report = _build_state_with_grid_issue()
    diagnosis = diagnose_grid_loading_failure(
        state=state, plan=plan, issues=report.issues,
    )
    assert diagnosis is not None, "expected a grid-loading diagnosis"
    proposal = propose_grid_migration_repair_bundle(state=state, diagnosis=diagnosis)
    assert proposal is not None, "expected a deterministic bundle proposal"
    evaluation = evaluate_grid_migration_repair_bundle(
        state=state, proposal=proposal, diagnosis=diagnosis,
        report_before=report, requirement="generic assembly",
    )
    assert evaluation.accepted, f"bundle should be accepted: {evaluation.reasons}"
    return state, plan, report, diagnosis, proposal, evaluation


def test_bundle_removes_grid_transformations() -> None:
    state, _plan, _report, _diag, proposal, evaluation = _run_full_bundle()
    repaired_plan = commit_accepted_repair_bundle(state, proposal, evaluation)
    assert repaired_plan is not None

    loading_ids = {l.id for l in repaired_plan.complex_model.lattice_loadings}
    assert "spacer_grid_loadings" not in loading_ids, (
        f"spacer_grid_loadings should be removed; got {sorted(loading_ids)}"
    )
    assert "top_grid_loading" not in loading_ids, (
        f"top_grid_loading should be removed; got {sorted(loading_ids)}"
    )


def test_bundle_preserves_non_grid_transformations() -> None:
    state, _plan, _report, _diag, proposal, evaluation = _run_full_bundle()
    repaired_plan = commit_accepted_repair_bundle(state, proposal, evaluation)
    assert repaired_plan is not None

    loadings = {l.id: l for l in repaired_plan.complex_model.lattice_loadings}
    for non_grid_id in _NON_GRID_LOADING_IDS:
        assert non_grid_id in loadings, (
            f"{non_grid_id!r} should still be present after repair"
        )
        assert loadings[non_grid_id].transformations, (
            f"{non_grid_id!r} should keep its transformations"
        )


def test_bundle_preserves_pin_counts() -> None:
    state, plan, _report, _diag, proposal, evaluation = _run_full_bundle()

    before_counts: dict[str, Counter] = {}
    for lat in plan.complex_model.lattices:
        before_counts[lat.id] = Counter(
            item for row in lat.universe_pattern for item in row
        )

    repaired_plan = commit_accepted_repair_bundle(state, proposal, evaluation)
    assert repaired_plan is not None

    for lat in repaired_plan.complex_model.lattices:
        after_counts = Counter(item for row in lat.universe_pattern for item in row)
        assert before_counts.get(lat.id, after_counts) == after_counts, (
            f"pin counts changed for lattice {lat.id!r}"
        )


def test_bundle_does_not_create_solid_grid_universe() -> None:
    state, _plan, _report, _diag, proposal, evaluation = _run_full_bundle()
    repaired_plan = commit_accepted_repair_bundle(state, proposal, evaluation)
    assert repaired_plan is not None

    universe_ids = {u.id for u in repaired_plan.complex_model.universes}
    assert "grid_cell" not in universe_ids, (
        "repair must not synthesize a solid grid_cell universe"
    )


def test_bundle_does_not_mutate_state_before_acceptance() -> None:
    state, _plan, report = _build_state_with_grid_issue()
    original_axial = copy.deepcopy(state.patches["axial_layers"].content)

    diagnosis = diagnose_grid_loading_failure(
        state=state, plan=_plan, issues=report.issues,
    )
    proposal = propose_grid_migration_repair_bundle(state=state, diagnosis=diagnosis)
    evaluation = evaluate_grid_migration_repair_bundle(
        state=state, proposal=proposal, diagnosis=diagnosis,
        report_before=report, requirement="generic assembly",
    )
    assert evaluation.accepted

    assert state.patches["axial_layers"].content == original_axial, (
        "real state must not be mutated before commit"
    )


def test_bundle_overlay_count_unchanged() -> None:
    state, _plan, _report, _diag, proposal, evaluation = _run_full_bundle()

    overlay_patch = next(
        (p for p in state.patches.values()
         if p.patch_type == "axial_overlays" and p.status == "valid"),
        None,
    )
    before = sum(
        1 for o in overlay_patch.content.get("overlays", [])
        if o.get("overlay_kind") == "spacer_grid"
    ) if overlay_patch else 0

    repaired_plan = commit_accepted_repair_bundle(state, proposal, evaluation)
    assert repaired_plan is not None

    overlay_patch_after = next(
        (p for p in state.patches.values()
         if p.patch_type == "axial_overlays" and p.status == "valid"),
        None,
    )
    after = sum(
        1 for o in overlay_patch_after.content.get("overlays", [])
        if o.get("overlay_kind") == "spacer_grid"
    ) if overlay_patch_after else 0

    assert before == after, (
        f"spacer_grid overlay count changed: {before} -> {after}"
    )
    assert before == 8, f"expected 8 spacer_grid overlays, got {before}"


def test_bundle_layer_loading_ids_cleaned() -> None:
    state, _plan, _report, _diag, proposal, evaluation = _run_full_bundle()
    repaired_plan = commit_accepted_repair_bundle(state, proposal, evaluation)
    assert repaired_plan is not None

    layers = {l.id: l for l in repaired_plan.complex_model.core.axial_layers}

    active = layers.get("active_fuel_pyrex_span")
    assert active is not None
    active_lids = active.loading_ids or ([active.loading_id] if active.loading_id else [])
    assert "spacer_grid_loadings" not in active_lids, (
        f"active_fuel_pyrex_span should not reference spacer_grid_loadings: {active_lids}"
    )

    plenum = layers.get("upper_plenum_middle_thimble")
    assert plenum is not None
    plenum_lids = plenum.loading_ids or ([plenum.loading_id] if plenum.loading_id else [])
    assert "top_grid_loading" not in plenum_lids, (
        f"upper_plenum_middle_thimble should not reference top_grid_loading: {plenum_lids}"
    )


def test_deterministic_repair_does_not_call_llm() -> None:
    state, _plan, report = _build_state_with_grid_issue()
    diagnosis = diagnose_grid_loading_failure(
        state=state, plan=_plan, issues=report.issues,
    )
    proposal = propose_grid_migration_repair_bundle(state=state, diagnosis=diagnosis)
    assert proposal is not None
    assert proposal.strategy == "deterministic_grid_migration", (
        f"expected deterministic_grid_migration strategy, got {proposal.strategy!r}"
    )
