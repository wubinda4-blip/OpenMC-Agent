"""Tests for the deterministic shoulder-gap multi-patch repair bundle."""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from openmc_agent.plan_builder.assembler import assemble_simulation_plan_from_patches
from openmc_agent.plan_builder.component_profile_repair import (
    diagnose_component_profile_slab,
    evaluate_shoulder_gap_repair_bundle,
    propose_shoulder_gap_repair_bundle,
    commit_accepted_repair_bundle,
)
from openmc_agent.plan_builder.patches import parse_patch_content
from openmc_agent.plan_builder.state import PlanBuildState, PlanPatchEnvelope
from openmc_agent.schemas import ValidationIssue, ValidationReport
from openmc_agent.validator import validate_simulation_plan


def _load_fixture_patches() -> list[dict]:
    raw = json.loads(
        (Path(__file__).parent / "fixtures/vera3_patches/vera3_3a_patches.json").read_text()
    )
    return raw["patches"]


def _broken_state(
    *, keep_moderator_only_universe: bool = True,
) -> tuple[PlanBuildState, "SimulationPlan", ValidationReport]:  # type: name-error
    patches = _load_fixture_patches()
    state = PlanBuildState(state_id="test-bundle", requirement_text="generic assembly")
    for payload in patches:
        content = copy.deepcopy(payload)
        if content["patch_type"] == "axial_layers":
            for layer in content["layers"]:
                if "shoulder" in layer["layer_id"]:
                    layer["fill_type"] = "material"
                    layer["fill_id"] = "borated_water"
                    layer["loading_id"] = None
                    layer["loading_ids"] = []
                    layer["role"] = "shoulder_gap"
            content["lattice_loadings"] = [
                ll for ll in content["lattice_loadings"]
                if ll["loading_id"] != "shoulder_water_loading"
            ]
        if content["patch_type"] == "universes":
            if not keep_moderator_only_universe:
                content["universes"] = [
                    u for u in content["universes"]
                    if u["universe_id"] != "moderator_only_pin"
                ]
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
    assert assembled.ok and assembled.plan is not None
    state.assembled_plan = assembled.plan.model_dump(mode="json")
    plan = assembled.plan
    report = validate_simulation_plan(plan, requirement="generic assembly")
    return state, plan, report


def test_bundle_repair_with_existing_moderator_universe(tmp_path) -> None:
    """Bundle repair when a moderator_only_pin universe already exists."""
    state, _plan, report = _broken_state(keep_moderator_only_universe=True)
    diagnosis = diagnose_component_profile_slab(
        state=state, plan=_plan, layer_id="lower_shoulder_gap",
    )
    assert diagnosis is not None and diagnosis.deterministic_repair_available

    proposal = propose_shoulder_gap_repair_bundle(state=state, diagnosis=diagnosis)
    assert proposal is not None
    assert proposal.strategy == "deterministic_shoulder_gap_repair"

    evaluation = evaluate_shoulder_gap_repair_bundle(
        state=state, proposal=proposal, diagnosis=diagnosis,
        report_before=report, requirement="generic assembly",
    )
    assert evaluation.accepted, f"Bundle should be accepted: {evaluation.reasons}"
    assert evaluation.repaired_plan is not None


def test_bundle_repair_derives_moderator_universe(tmp_path) -> None:
    """Bundle repair derives a moderator-only universe when none exists."""
    state, _plan, report = _broken_state(keep_moderator_only_universe=False)
    diagnosis = diagnose_component_profile_slab(
        state=state, plan=_plan, layer_id="lower_shoulder_gap",
    )
    assert diagnosis is not None and diagnosis.deterministic_repair_available

    proposal = propose_shoulder_gap_repair_bundle(state=state, diagnosis=diagnosis)
    assert proposal is not None
    # Should include a universes patch operation
    patch_types = {op.patch_type for op in proposal.operations}
    assert "universes" in patch_types

    evaluation = evaluate_shoulder_gap_repair_bundle(
        state=state, proposal=proposal, diagnosis=diagnosis,
        report_before=report, requirement="generic assembly",
    )
    assert evaluation.accepted, f"Bundle should be accepted: {evaluation.reasons}"


def test_bundle_does_not_mutate_state_before_acceptance() -> None:
    """The real state is unchanged until commit."""
    state, _plan, report = _broken_state(keep_moderator_only_universe=True)
    original_axial = copy.deepcopy(state.patches["axial_layers"].content)

    diagnosis = diagnose_component_profile_slab(
        state=state, plan=_plan, layer_id="lower_shoulder_gap",
    )
    proposal = propose_shoulder_gap_repair_bundle(state=state, diagnosis=diagnosis)
    evaluation = evaluate_shoulder_gap_repair_bundle(
        state=state, proposal=proposal, diagnosis=diagnosis,
        report_before=report, requirement="generic assembly",
    )
    assert evaluation.accepted

    # State must still have the original broken axial layers
    assert state.patches["axial_layers"].content == original_axial


def test_bundle_commit_updates_state() -> None:
    """After commit, the real state is updated."""
    state, _plan, report = _broken_state(keep_moderator_only_universe=True)
    diagnosis = diagnose_component_profile_slab(
        state=state, plan=_plan, layer_id="lower_shoulder_gap",
    )
    proposal = propose_shoulder_gap_repair_bundle(state=state, diagnosis=diagnosis)
    evaluation = evaluate_shoulder_gap_repair_bundle(
        state=state, proposal=proposal, diagnosis=diagnosis,
        report_before=report, requirement="generic assembly",
    )
    assert evaluation.accepted

    repaired_plan = commit_accepted_repair_bundle(state, proposal, evaluation)
    assert repaired_plan is not None

    # The layer should now use lattice fill
    model = repaired_plan.complex_model
    layer = next(l for l in model.core.axial_layers if l.id == "lower_shoulder_gap")
    assert layer.fill.type == "lattice"
    assert layer.fill.id == "assembly_lattice"
    assert layer.loading_id is not None


def test_bundle_resolves_component_profile_issue() -> None:
    """After repair, the target layer's component_profile_as_material_slab issue is gone."""
    state, _plan, report = _broken_state(keep_moderator_only_universe=True)
    before_codes = {i.code for i in report.issues}
    assert "assembly3d.component_profile_as_material_slab" in before_codes

    diagnosis = diagnose_component_profile_slab(
        state=state, plan=_plan, layer_id="lower_shoulder_gap",
    )
    proposal = propose_shoulder_gap_repair_bundle(state=state, diagnosis=diagnosis)
    evaluation = evaluate_shoulder_gap_repair_bundle(
        state=state, proposal=proposal, diagnosis=diagnosis,
        report_before=report, requirement="generic assembly",
    )
    assert evaluation.accepted

    after_report = ValidationReport.model_validate(evaluation.validation_report_after)
    # The specific layer's issue must be gone
    target_issues = [
        i for i in after_report.issues
        if i.code == "assembly3d.component_profile_as_material_slab"
        and "lower_shoulder_gap" in (i.schema_path or "")
    ]
    assert target_issues == [], "lower_shoulder_gap issue should be resolved"


def test_bundle_preserves_guide_instrument_through_path() -> None:
    """Guide/instrument tube counts unchanged after repair."""
    state, plan, report = _broken_state(keep_moderator_only_universe=True)
    diagnosis = diagnose_component_profile_slab(
        state=state, plan=plan, layer_id="lower_shoulder_gap",
    )
    proposal = propose_shoulder_gap_repair_bundle(state=state, diagnosis=diagnosis)
    evaluation = evaluate_shoulder_gap_repair_bundle(
        state=state, proposal=proposal, diagnosis=diagnosis,
        report_before=report, requirement="generic assembly",
    )
    assert evaluation.accepted

    repaired_plan = commit_accepted_repair_bundle(state, proposal, evaluation)
    assert repaired_plan is not None

    # Check that guide_tube / instrument_tube universes are still in the lattice
    lattice = next(l for l in repaired_plan.complex_model.lattices if l.id == "assembly_lattice")
    all_universes = {item for row in lattice.universe_pattern for item in row}
    assert "guide_tube" in all_universes
    assert "instrument_tube" in all_universes

    from collections import Counter
    counts = Counter(item for row in lattice.universe_pattern for item in row)
    assert counts["guide_tube"] == 24
    assert counts["instrument_tube"] == 1


def test_bundle_does_not_modify_materials_or_pin_map() -> None:
    """Materials and pin_map patches are untouched."""
    state, _plan, report = _broken_state(keep_moderator_only_universe=True)
    original_materials = copy.deepcopy(state.patches["materials"].content)
    original_pin_map = copy.deepcopy(state.patches["pin_map"].content)

    diagnosis = diagnose_component_profile_slab(
        state=state, plan=_plan, layer_id="lower_shoulder_gap",
    )
    proposal = propose_shoulder_gap_repair_bundle(state=state, diagnosis=diagnosis)
    evaluation = evaluate_shoulder_gap_repair_bundle(
        state=state, proposal=proposal, diagnosis=diagnosis,
        report_before=report, requirement="generic assembly",
    )
    assert evaluation.accepted
    commit_accepted_repair_bundle(state, proposal, evaluation)

    assert state.patches["materials"].content == original_materials
    assert state.patches["pin_map"].content == original_pin_map


def test_bundle_only_modifies_target_layer() -> None:
    """Only the target shoulder_gap layer fill is changed; other layers untouched."""
    state, _plan, report = _broken_state(keep_moderator_only_universe=True)
    original_layers = copy.deepcopy(state.patches["axial_layers"].content["layers"])

    diagnosis = diagnose_component_profile_slab(
        state=state, plan=_plan, layer_id="lower_shoulder_gap",
    )
    proposal = propose_shoulder_gap_repair_bundle(state=state, diagnosis=diagnosis)
    evaluation = evaluate_shoulder_gap_repair_bundle(
        state=state, proposal=proposal, diagnosis=diagnosis,
        report_before=report, requirement="generic assembly",
    )
    assert evaluation.accepted
    commit_accepted_repair_bundle(state, proposal, evaluation)

    new_layers = state.patches["axial_layers"].content["layers"]
    for i, (old, new) in enumerate(zip(original_layers, new_layers)):
        if old["layer_id"] == "lower_shoulder_gap":
            assert new["fill_type"] == "lattice"
            assert new["fill_id"] == "assembly_lattice"
            assert new["loading_id"] is not None
        elif "shoulder" not in old["layer_id"]:
            # Non-shoulder layers must be unchanged
            assert new == old, f"Layer {old['layer_id']} was unexpectedly modified"
