"""Lattice-loading early validation: structural issues surface at plan time.

Tests that ``lattice_loading_structural_issues`` (the shared source of truth
called by both ``validate_simulation_plan`` and the renderer ``can_render``
path) surfaces ``lattice_transform.replacement_universe_missing`` and the
``renderer.axial_loading_materialization_failed`` wrapper at plan-validation
time, with precise schema paths and no duplicates.

The broken scenario mirrors the documented VERA3B regression in
``tests/fixtures/regressions/vera3b_missing_grid_replacement_universe.json``:
two ``replace_universe_family`` transformations referencing an undefined
``grid_cell`` are injected onto the clean 3B fixture so the existing spacer
grids (8 overlays) make the grid transformations redundant.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path

from openmc_agent.lattice_loading_validation import (
    lattice_loading_structural_issues,
)
from openmc_agent.plan_builder.assembler import assemble_simulation_plan_from_patches
from openmc_agent.plan_builder.patches import parse_patch_content
from openmc_agent.plan_builder.state import PlanBuildState, PlanPatchEnvelope
from openmc_agent.renderers.assembly import _axial_assembly_modeling_errors
from openmc_agent.schemas import SimulationPlan
from openmc_agent.validator import validate_simulation_plan

_FIXTURE_DIR = Path(__file__).parent / "fixtures" / "vera3_patches"


def _load_fixture(variant: str = "3a") -> list[dict]:
    path = _FIXTURE_DIR / f"vera3_{variant}_patches.json"
    with open(path) as f:
        data = json.load(f)
    if isinstance(data, dict):
        return data["patches"]
    return data


def _inject_grid_cell_regression(patches: list[dict]) -> list[dict]:
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


def _build_state(variant: str = "3a") -> tuple[PlanBuildState, SimulationPlan]:
    patches = _load_fixture(variant)
    if variant == "3b":
        patches = _inject_grid_cell_regression(patches)
    state = PlanBuildState(state_id="test", requirement_text="generic assembly")
    for payload in patches:
        content = copy.deepcopy(payload)
        state.add_patch(PlanPatchEnvelope(
            patch_id=content["patch_type"],
            patch_type=content["patch_type"],
            content=content, status="valid",
        ))
    assembled = assemble_simulation_plan_from_patches(
        [parse_patch_content(p.patch_type, p.content) for p in state.patches.values()],
        strict=True,
    )
    assert assembled.ok and assembled.plan is not None, (
        f"fixture {variant!r} failed to assemble: {assembled.summary}"
    )
    state.assembled_plan = assembled.plan.model_dump(mode="json")
    return state, assembled.plan


def test_replacement_universe_missing_detected_at_validate_plan() -> None:
    _state, plan = _build_state("3b")
    report = validate_simulation_plan(plan, requirement="generic assembly")
    codes = [i.code for i in report.issues]
    assert "lattice_transform.replacement_universe_missing" in codes, (
        f"expected replacement_universe_missing in {codes}"
    )
    assert "renderer.axial_loading_materialization_failed" in codes, (
        f"expected axial_loading_materialization_failed in {codes}"
    )


def test_renderer_and_validator_return_same_lattice_codes() -> None:
    _state, plan = _build_state("3b")
    model = plan.complex_model
    assert model is not None

    validator_issues = lattice_loading_structural_issues(model)
    validator_codes = {i.code for i in validator_issues}

    renderer_issues = _axial_assembly_modeling_errors(model)
    renderer_codes = {i.code for i in renderer_issues}

    assert "lattice_transform.replacement_universe_missing" in validator_codes, (
        f"validator missing replacement_universe_missing: {validator_codes}"
    )
    assert "lattice_transform.replacement_universe_missing" in renderer_codes, (
        f"renderer missing replacement_universe_missing: {renderer_codes}"
    )
    assert validator_codes == renderer_codes, (
        f"validator and renderer lattice codes diverge: "
        f"{validator_codes} vs {renderer_codes}"
    )


def test_lattice_loading_issues_have_precise_schema_path() -> None:
    _state, plan = _build_state("3b")
    model = plan.complex_model
    assert model is not None
    issues = lattice_loading_structural_issues(model)
    assert issues, "expected lattice-loading structural issues for 3b regression"
    for issue in issues:
        path = issue.schema_path or ""
        assert "lattice_loadings[" in path or "axial_layers[" in path, (
            f"issue {issue.code!r} lacks layer/loading context in schema_path={path!r}"
        )


def test_no_duplicate_lattice_loading_issues() -> None:
    _state, plan = _build_state("3b")
    model = plan.complex_model
    assert model is not None
    issues = lattice_loading_structural_issues(model)

    def _normalize(path: str) -> str:
        out: list[str] = []
        for part in path.replace(".", "/").split("/"):
            if part and not part.lstrip("-").isdigit():
                out.append(part)
        return ".".join(out)

    identities = [
        (i.code, _normalize(i.schema_path or ""), i.severity)
        for i in issues
    ]
    assert len(identities) == len(set(identities)), (
        f"duplicate lattice-loading issues: {identities}"
    )


def test_clean_plan_has_no_lattice_loading_issues() -> None:
    _state, plan = _build_state("3a")
    model = plan.complex_model
    assert model is not None
    issues = lattice_loading_structural_issues(model)
    lattice_codes = {i.code for i in issues}
    assert not any(c.startswith("lattice_transform.") for c in lattice_codes), (
        f"clean 3a plan has lattice_transform issues: {lattice_codes}"
    )


def test_probe_does_not_duplicate_shared_validator_issues() -> None:
    from openmc_agent.graph import _probe_axial_materialization_blockers

    _state, plan = _build_state("3b")
    model = plan.complex_model
    assert model is not None
    shared = lattice_loading_structural_issues(model)
    shared_codes = {i.code for i in shared}

    probe = _probe_axial_materialization_blockers(plan)
    novel = {i.code for i in probe} - shared_codes
    assert not novel, (
        f"probe surfaced novel codes not already caught by the shared "
        f"validator: {novel}"
    )
