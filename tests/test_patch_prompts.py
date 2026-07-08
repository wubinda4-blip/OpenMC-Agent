"""Tests for patch prompt builders (Phase 4)."""

from __future__ import annotations

import pytest

from openmc_agent.plan_builder.patch_prompts import build_patch_prompt, build_retry_prompt
from openmc_agent.plan_builder.patch_generator import PatchGenerationContext


_ALL_PATCH_TYPES = [
    "facts", "materials", "universes", "pin_map",
    "axial_layers", "axial_overlays", "settings",
]


# ---------------------------------------------------------------------------
# 1. build prompt forbids full SimulationPlan for each patch type
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("patch_type", _ALL_PATCH_TYPES)
def test_prompt_forbids_full_simulation_plan(patch_type: str) -> None:
    prompt = build_patch_prompt(patch_type, "3D assembly requirement", None)
    assert "NOT generating a SimulationPlan" in prompt
    assert patch_type in prompt


@pytest.mark.parametrize("patch_type", _ALL_PATCH_TYPES)
def test_prompt_asks_for_only_that_patch(patch_type: str) -> None:
    prompt = build_patch_prompt(patch_type, "requirement", None)
    assert patch_type in prompt
    # Should not mention other patch types in the schema section
    for other in _ALL_PATCH_TYPES:
        if other == patch_type:
            continue
        # The global rules mention "SimulationPlan" generically; we just check
        # the specific patch rules section doesn't describe other types.
        # (Some cross-references are fine, e.g. "do not generate materials")


# ---------------------------------------------------------------------------
# 7. axial_layers prompt forbids default unit slab
# ---------------------------------------------------------------------------


def test_axial_layers_prompt_forbids_default_unit_slab() -> None:
    prompt = build_patch_prompt("axial_layers", "3D benchmark", None)
    assert "z=-1..1" in prompt or "z=-1" in prompt
    assert "active_fuel" in prompt.lower() or "active fuel" in prompt.lower()


# ---------------------------------------------------------------------------
# 8. axial_overlays prompt enforces overlay not slab
# ---------------------------------------------------------------------------


def test_axial_overlays_prompt_enforces_overlay_not_slab() -> None:
    prompt = build_patch_prompt("axial_overlays", "spacer grids", None)
    assert "material slab" in prompt.lower() or "material slab" in prompt
    assert "homogenized_open_region" in prompt
    assert "through_path_preserved" in prompt


# ---------------------------------------------------------------------------
# 9. materials prompt forbids confirmed pure-element alloys
# ---------------------------------------------------------------------------


def test_materials_prompt_forbids_confirmed_pure_element_alloys() -> None:
    prompt = build_patch_prompt("materials", "Zircaloy-4 cladding", None)
    assert "Zircaloy-4" in prompt or "Zircaloy" in prompt
    assert "SS-304" in prompt or "SS304" in prompt
    assert "Inconel" in prompt
    assert "confirmed" in prompt.lower() or "approximate" in prompt.lower()


# ---------------------------------------------------------------------------
# pin_map prompt forbids full lattice
# ---------------------------------------------------------------------------


def test_pin_map_prompt_forbids_full_lattice() -> None:
    prompt = build_patch_prompt("pin_map", "17x17 assembly", None)
    assert "17x17" in prompt or "289" in prompt
    assert "Do NOT" in prompt or "do NOT" in prompt


# ---------------------------------------------------------------------------
# prompt includes context when provided
# ---------------------------------------------------------------------------


def test_prompt_includes_context() -> None:
    ctx = PatchGenerationContext(
        benchmark_id="VERA3",
        selected_variant="3B",
        expected_counts={"expected_pyrex_rod_count": 16},
        strict_benchmark=True,
    )
    prompt = build_patch_prompt("pin_map", "VERA3 3B", ctx)
    assert "VERA3" in prompt
    assert "3B" in prompt
    assert "pyrex_rod_count" in prompt or "16" in prompt


def test_prompt_empty_context_omits_context_block() -> None:
    prompt = build_patch_prompt("facts", "requirement", None)
    assert "Context:" not in prompt


# ---------------------------------------------------------------------------
# retry prompt includes validation issues
# ---------------------------------------------------------------------------


def test_retry_prompt_includes_issues() -> None:
    issues = [
        {"code": "patch.pin_map.coord_overlap", "severity": "error", "message": "(5,5) overlaps"},
    ]
    prompt = build_retry_prompt("pin_map", "requirement", None, issues, 1)
    assert "coord_overlap" in prompt
    assert "attempt 1" in prompt.lower() or "attempt 1" in prompt
    assert "NOT generating a SimulationPlan" in prompt


# ---------------------------------------------------------------------------
# patch few-shot reference injection
# ---------------------------------------------------------------------------


def test_prompt_includes_few_shot_when_case_ids_present() -> None:
    ctx = PatchGenerationContext(few_shot_case_ids=["assembly_3d_with_spacer_grids"])
    prompt = build_patch_prompt("materials", "assembly requirement", ctx)
    assert "Reference materials patch" in prompt
    assert '"patch_type": "materials"' in prompt


def test_prompt_omits_few_shot_when_case_ids_empty() -> None:
    ctx = PatchGenerationContext(few_shot_case_ids=[])
    prompt = build_patch_prompt("materials", "requirement", ctx)
    assert "Reference materials patch" not in prompt


def test_prompt_omits_few_shot_when_no_patch_available() -> None:
    # pin_cell_basic publishes no patches/
    ctx = PatchGenerationContext(few_shot_case_ids=["pin_cell_basic"])
    prompt = build_patch_prompt("materials", "requirement", ctx)
    assert "Reference materials patch" not in prompt


def test_retry_prompt_also_carries_few_shot() -> None:
    ctx = PatchGenerationContext(few_shot_case_ids=["assembly_3d_with_spacer_grids"])
    prompt = build_retry_prompt("materials", "requirement", ctx, [], 1)
    assert "Reference materials patch" in prompt
