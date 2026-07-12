"""Tests that plan validator and renderer share the same assembly3d structural issues.

Validates that ``assembly3d.component_profile_as_material_slab`` is emitted at
``validate_simulation_plan`` time, not deferred to the renderer capability stage.
"""

from __future__ import annotations

import json
from pathlib import Path

from openmc_agent.assembly3d_guard import (
    assembly3d_structural_issues,
    validate_assembly3d_plan,
)
from openmc_agent.plan_builder.assembler import assemble_simulation_plan_from_patches
from openmc_agent.plan_builder.patches import parse_patch_content
from openmc_agent.plan_builder.state import PlanBuildState, PlanPatchEnvelope
from openmc_agent.renderers.assembly import _axial_assembly_modeling_errors
from openmc_agent.schemas import SimulationPlan
from openmc_agent.validator import validate_simulation_plan


def _load_fixture() -> tuple[list, dict]:
    raw = json.loads(
        (Path(__file__).parent / "fixtures/vera3_patches/vera3_3a_patches.json").read_text()
    )
    return raw["patches"], raw


def _broken_state() -> tuple[PlanBuildState, SimulationPlan]:
    """Build a state where shoulder_gap layers use material slab (the regression)."""
    patches, _ = _load_fixture()
    state = PlanBuildState(state_id="test-align", requirement_text="generic assembly")
    for payload in patches:
        content = dict(payload)
        if content["patch_type"] == "axial_layers":
            content = json.loads(json.dumps(content))
            for layer in content["layers"]:
                if "shoulder" in layer["layer_id"]:
                    layer["fill_type"] = "material"
                    layer["fill_id"] = "borated_water"
                    layer["loading_id"] = None
                    layer["loading_ids"] = []
                    layer["role"] = "shoulder_gap"
            # Remove shoulder_water_loading to force a create-bundle scenario
            content["lattice_loadings"] = [
                ll for ll in content["lattice_loadings"]
                if ll["loading_id"] != "shoulder_water_loading"
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
    return state, assembled.plan


def test_component_profile_issue_appears_in_validate_plan() -> None:
    """The shoulder-gap material slab must be caught at validate_plan time."""
    _state, plan = _broken_state()
    report = validate_simulation_plan(plan, requirement="generic assembly")
    codes = [i.code for i in report.issues]
    assert "assembly3d.component_profile_as_material_slab" in codes


def test_renderer_and_validator_return_same_component_profile_issue() -> None:
    """Both sources emit the same issue code and schema_path shape."""
    _state, plan = _broken_state()
    model = plan.complex_model
    assert model is not None

    # Validator path
    validator_issues = assembly3d_structural_issues(model)
    validator_codes = {i.code for i in validator_issues}

    # Renderer path
    renderer_issues = _axial_assembly_modeling_errors(model)
    renderer_codes = {i.code for i in renderer_issues}

    assert "assembly3d.component_profile_as_material_slab" in validator_codes
    assert "assembly3d.component_profile_as_material_slab" in renderer_codes
    assert validator_codes == renderer_codes


def test_validate_assembly3d_plan_catches_component_profile_slab() -> None:
    """validate_assembly3d_plan (called by validate_simulation_plan) catches it."""
    _state, plan = _broken_state()
    issues = validate_assembly3d_plan(plan, requirement="generic assembly")
    codes = [i.code for i in issues]
    assert "assembly3d.component_profile_as_material_slab" in codes


def test_structural_issues_deduplicated() -> None:
    """The shared function does not emit duplicate issues."""
    _state, plan = _broken_state()
    model = plan.complex_model
    assert model is not None
    issues = assembly3d_structural_issues(model)
    identities = [(i.code, i.schema_path, i.severity) for i in issues]
    assert len(identities) == len(set(identities))
