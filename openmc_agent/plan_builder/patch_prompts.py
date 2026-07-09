"""Per-patch-type prompt builders for incremental plan building (Phase 4/7B).

Each builder produces a self-contained prompt that asks the LLM for **exactly
one** patch — never a full SimulationPlan, never a full lattice pattern.

Phase 7B hardening: added CRITICAL OUTPUT CONTRACT, minimal per-patch examples,
and stronger prohibition language to prevent real LLMs from returning full
plans instead of small patches.
"""

from __future__ import annotations

import json
from typing import Any

from openmc_agent.few_shot_cases import load_patch_few_shots

from .patches import (
    get_patch_allowed_top_level_keys,
    get_patch_forbidden_top_level_keys,
)


# ---------------------------------------------------------------------------
# Critical output contract (injected into every patch prompt)
# ---------------------------------------------------------------------------

_OUTPUT_CONTRACT = """\
CRITICAL OUTPUT CONTRACT:
- You are NOT generating a SimulationPlan.
- You are NOT generating OpenMC code or XML files.
- You are NOT generating a full reactor model.
- You are generating exactly ONE JSON object for patch_type="{patch_type}".
- The top-level JSON key "patch_type" MUST equal "{patch_type}".
- Any response containing "complex_model", "simulation_plan", "core", \
"axial_layers", "axial_overlays", "universes", "lattices", "capability_report", \
"execution_check", or "plot_specs" OUTSIDE the requested patch schema will be \
REJECTED immediately.
- Return JSON only. No markdown. No prose. No code fences. No explanation."""


# ---------------------------------------------------------------------------
# Per-patch-type rules + minimal examples
# ---------------------------------------------------------------------------

_PATCH_RULES: dict[str, str] = {
    "facts": """\
Requested patch type: facts
Schema fields: benchmark_id, selected_variant, geometry_type, lattice_size [int,int],
  pin_pitch_cm, assembly_pitch_cm, has_axial_geometry, has_spacer_grids,
  has_special_pin_map, active_fuel_region_cm [float,float], axial_domain_cm [float,float],
  expected_spacer_grid_count, expected_pin_count, expected_guide_tube_count,
  expected_instrument_tube_count, expected_pyrex_count, expected_thimble_plug_count,
  material_roles [list[str]], missing_facts [list[str]], assumptions [list[str]],
  source_notes [list[str]].

Rules:
- Extract benchmark facts from the requirement; do NOT invent numbers.
- If variant is 3A/3B, set selected_variant.
- If requirement mentions spacer grids, set has_spacer_grids=true.
- If requirement mentions Pyrex/thimble plug/guide tube/instrument tube, set has_special_pin_map=true.
- Unknown facts go into missing_facts, NOT fabricated values.

Minimal example (adapt to the actual requirement):
{{"patch_type": "facts", "benchmark_id": "EXAMPLE", "selected_variant": "3B",
  "lattice_size": [17, 17], "has_axial_geometry": true,
  "has_spacer_grids": true, "has_special_pin_map": true, "missing_facts": []}}""",

    "materials": """\
Requested patch type: materials
Schema: {{"patch_type": "materials", "materials": [{{"material_id", "name", "role",
  "density_g_cm3", "temperature_K", "composition": {{"element": fraction}},
  "composition_basis", "composition_status", "source_note", "warnings": []}}], "assumptions": []}}

Rules:
- Do NOT generate universes, cells, lattices, or axial structures.
- Zircaloy-4 is NOT pure Zr; SS-304 is NOT pure Fe; Inconel-718 is NOT pure Ni.
- If composition is incomplete, set composition_status to "needs_library" or "needs_confirmation".
- approximate alloy is allowed ONLY with composition_status="approximate" AND an explicit warning.
- Never set composition_status="confirmed" for a known multi-element alloy with a single-element composition.
- Cross section paths do NOT belong here.

Minimal example:
{{"patch_type": "materials", "materials": [
  {{"material_id": "fuel", "name": "UO2", "role": "fuel", "density_g_cm3": 10.257,
    "composition": {{"U235": 3.1, "U238": 96.9}}, "composition_status": "approximate",
    "warnings": ["enrichment approximate"]}}
]}}""",

    "universes": """\
Requested patch type: universes
Schema: {{"patch_type": "universes", "universes": [{{"universe_id", "kind",
  "cells": [{{"id", "role", "material_id", "region_kind", "r_min_cm", "r_max_cm",
  "protected_through_path"}}], "source_note", "assumptions": []}}]}}

Rules:
- Do NOT generate the lattice, full pin map, or axial layers.
- Each universe must have at least one cell.
- guide_tube must include internal coolant AND tube wall material.
- thimble_plug is a guide tube with a plug inside: it MUST keep the same tube wall
  (Zircaloy-4 annulus) as a guide_tube, plus a water gap between the plug and the
  wall. Do NOT make it a solid rod of cladding material with water outside.
  Correct cell order (inside→out):
    plug (SS304 cylinder) → water_gap (water annulus) → wall (Zircaloy-4 annulus) → outer_coolant (water background)
- pyrex_rod must include a cell with pyrex material if variant requires it.
- fuel_pin should have a fuel material cell.
- Mark through-path cells with protected_through_path=true.

Minimal example:
{{"patch_type": "universes", "universes": [
  {{"universe_id": "fuel_pin", "kind": "fuel_pin", "cells": [
    {{"id": "fuel", "role": "fuel", "material_id": "fuel_mat", "region_kind": "cylinder"}}
  ]}}
]}}""",

    "pin_map": """\
Requested patch type: pin_map
Schema: {{"patch_type": "pin_map", "variant", "lattice_size": [int,int],
  "default_universe_id", "coordinate_convention": {{"index_base", "row_origin",
  "col_origin", "ordering"}},
  "guide_tube_coords": [[int,int]], "instrument_tube_coords": [[int,int]],
  "pyrex_rod_coords": [[int,int]], "thimble_plug_coords": [[int,int]],
  "water_cell_coords": [[int,int]], "assumptions": [], "source_note"}}

CRITICAL RULES:
- Do NOT output the full 17x17 lattice. Do NOT enumerate all 289 cells.
- Do NOT output rows of fuel_pin_universe.
- ONLY output special coordinate lists and default_universe_id.
- lattice_size is [rows, cols], e.g. [17, 17].
- coordinate_convention.index_base must be 0 or 1.
- Each coordinate is [row, col] using the stated convention.

Minimal example (only special positions, NOT 289 entries):
{{"patch_type": "pin_map", "lattice_size": [17, 17], "default_universe_id": "fuel_pin",
  "coordinate_convention": {{"index_base": 1, "row_origin": "top", "col_origin": "left", "ordering": "row_col"}},
  "guide_tube_coords": [[3,6], [3,9]], "instrument_tube_coords": [[9,9]]}}""",

    "axial_layers": """\
Requested patch type: axial_layers
Schema: {{"patch_type": "axial_layers", "axial_domain_cm": [float,float],
  "layers": [{{"layer_id", "role", "z_min_cm", "z_max_cm", "fill_type", "fill_id",
  "requires_human_confirmation", "assumptions": [], "source_note"}}]}}

Rules:
- Do NOT generate materials/universes/lattice/full plan.
- active_fuel layer must exist if the problem is a 3D fuel assembly.
- active_fuel fill_type should be "lattice" and fill_id should be "assembly_lattice".
- Do NOT use default z=-1..1 for an explicit 3D benchmark.
- If z values are unknown, set requires_human_confirmation=true; do NOT fabricate.
- Do NOT represent spacer grids as axial layer material slabs.

Minimal example (2 layers only, adapt to requirement):
{{"patch_type": "axial_layers", "axial_domain_cm": [0.0, 400.0], "layers": [
  {{"layer_id": "active_fuel", "role": "active_fuel", "z_min_cm": 10.0, "z_max_cm": 375.0,
    "fill_type": "lattice", "fill_id": "assembly_lattice"}}
]}}""",

    "axial_overlays": """\
Requested patch type: axial_overlays
Schema: {{"patch_type": "axial_overlays", "overlays": [{{"overlay_id", "overlay_kind",
  "z_min_cm", "z_max_cm", "target_lattice_id", "material_id", "geometry_mode",
  "through_path_preserved", "requires_human_confirmation", "assumptions": []}}]}}

Rules:
- Spacer grids MUST be overlays, NOT material slabs.
- Default Level 1: geometry_mode="homogenized_open_region", through_path_preserved=true.
- target_lattice_id should be "assembly_lattice".
- If grid z positions are unknown, use geometry_mode="skeleton" with requires_human_confirmation=true.

Minimal example (1 overlay):
{{"patch_type": "axial_overlays", "overlays": [
  {{"overlay_id": "grid_1", "overlay_kind": "spacer_grid", "z_min_cm": 50.0, "z_max_cm": 52.0,
    "target_lattice_id": "assembly_lattice", "material_id": "grid_mat",
    "geometry_mode": "homogenized_open_region", "through_path_preserved": true}}
]}}""",

    "settings": """\
Requested patch type: settings
Schema: {{"patch_type": "settings", "source_strategy",
  "source_requires_fissionable_constraint", "plot_strategy",
  "cross_sections_runtime_required", "tallies_required_for_smoke_test", "assumptions": []}}

Rules:
- source_strategy defaults to "active_fuel_box".
- plot_strategy defaults to "full_assembly".
- cross_sections_runtime_required=true (runtime concern, not plan-generation blocker).
- tallies_required_for_smoke_test=false.

Minimal example:
{{"patch_type": "settings", "source_strategy": "active_fuel_box",
  "plot_strategy": "full_assembly", "cross_sections_runtime_required": true,
  "tallies_required_for_smoke_test": false}}""",
}


# Total/per-patch character budgets for the few-shot reference block in a patch
# prompt. Each incremental layer is generated independently, so this budget
# applies per layer (not accumulated across the 7-patch pipeline).
FEW_SHOT_PATCH_BUDGET: int = 2400
_FEWSHOT_PER_PATCH_MAX: int = 1200


def _few_shot_block(patch_type: str, context: Any | None) -> str:
    """Render a reference-patch block from gold few-shot cases, if any.

    Returns ``""`` when there is no context, no case ids, or no patch of the
    requested type is available — leaving the prompt unchanged for backward
    compatibility.
    """
    case_ids = list(getattr(context, "few_shot_case_ids", []) or []) if context else []
    if not case_ids:
        return ""
    patches = load_patch_few_shots(patch_type, case_ids, limit=2)
    if not patches:
        return ""
    header = (
        f"Reference {patch_type} patch(es) from similar successful cases "
        f"— illustrative, not authoritative (adapt structure & values, do NOT "
        f"copy verbatim):"
    )
    lines = [header]
    remaining = FEW_SHOT_PATCH_BUDGET - len(header)
    for patch in patches:
        blob = json.dumps(patch, ensure_ascii=False)
        if len(blob) > _FEWSHOT_PER_PATCH_MAX:
            blob = blob[: _FEWSHOT_PER_PATCH_MAX] + "...(truncated)"
        if len(blob) + 1 > remaining:
            break
        lines.append(blob)
        remaining -= len(blob) + 1
    if len(lines) == 1:
        return ""
    return "\n".join(lines) + "\n\n"


def build_patch_prompt(
    patch_type: str,
    requirement: str,
    context: Any | None = None,
) -> str:
    """Build a prompt for generating exactly one patch of the given type."""
    patch_rules = _PATCH_RULES.get(patch_type, "")
    if not patch_rules:
        patch_rules = f"Requested patch type: {patch_type}\n(No specific rules defined.)"

    contract = _OUTPUT_CONTRACT.format(patch_type=patch_type)
    context_block = _context_block(context)
    few_shot_block = _few_shot_block(patch_type, context)

    # Phase 7C: add allowed/forbidden keys.
    allowed = sorted(get_patch_allowed_top_level_keys(patch_type))
    forbidden = sorted(get_patch_forbidden_top_level_keys(patch_type))
    keys_block = (
        f"Allowed top-level keys for patch_type=\"{patch_type}\":\n"
        f"  {', '.join(allowed)}\n\n"
        f"FORBIDDEN top-level keys (will cause immediate rejection):\n"
        f"  {', '.join(forbidden)}\n\n"
    )

    return (
        f"{contract}\n\n"
        f"{keys_block}"
        f"{patch_rules}\n\n"
        f"{few_shot_block}"
        f"{context_block}"
        f"Requirement:\n{requirement}\n\n"
        f'Return ONLY the JSON object with patch_type="{patch_type}". No other text.'
    )


def build_retry_prompt(
    patch_type: str,
    requirement: str,
    context: Any | None,
    issues: list[dict[str, Any]],
    attempt_index: int,
) -> str:
    """Build a retry prompt for a patch that failed validation."""
    base_prompt = build_patch_prompt(patch_type, requirement, context)

    # Check failure type for targeted retry message.
    issue_codes = [i.get("code", "") for i in issues]
    is_full_plan = any(
        "full_plan" in code or "full_lattice" in code
        for code in issue_codes
    )
    is_patch_type_issue = any(
        "patch_type" in code for code in issue_codes
    )
    is_parse_error = any(
        "json_parse" in code for code in issue_codes
    )

    issue_lines: list[str] = []
    for issue in issues:
        sev = issue.get("severity", "error")
        code = issue.get("code", "unknown")
        msg = issue.get("message", "")
        issue_lines.append(f"  - [{sev}] {code}: {msg}")

    if is_full_plan:
        forbidden_block = (
            f"\n\nYour previous response was REJECTED because it looked like a "
            f"full SimulationPlan, not a {patch_type} patch.\n"
            f"Do not include complex_model/core/materials/universes/lattices "
            f"unless they are part of the {patch_type} schema.\n"
            f'The first character must be "{{". The last character must be "}}".\n'
            f'Regenerate ONLY the {patch_type} JSON with patch_type="{patch_type}".'
        )
    elif is_patch_type_issue:
        forbidden_block = (
            f'\n\nYour response must include patch_type="{patch_type}" as a '
            f'top-level key. Add it exactly. Do not omit or rename it.'
        )
    elif is_parse_error:
        forbidden_block = (
            f"\n\nYour previous response was not valid JSON.\n"
            f"Return JSON only. No prose. No markdown."
        )
    else:
        forbidden_block = ""

    return (
        f"{base_prompt}"
        f"\n\nThe previous {patch_type} (attempt {attempt_index}) failed:\n"
        + "\n".join(issue_lines)
        + forbidden_block
        + f"\n\nFix these issues and regenerate ONLY the {patch_type} JSON."
    )


# ---------------------------------------------------------------------------
# Context serialization
# ---------------------------------------------------------------------------


def _context_block(context: Any | None) -> str:
    if context is None:
        return ""

    parts: list[str] = ["Context:"]
    for attr in (
        "benchmark_id", "selected_variant",
        "confirmed_facts", "extracted_facts",
        "validated_patch_summaries",
        "expected_counts",
        "known_material_ids", "known_universe_ids", "known_lattice_ids",
        "active_fuel_region_cm", "axial_domain_cm",
        "strict_benchmark",
    ):
        val = getattr(context, attr, None)
        if val is None:
            continue
        if isinstance(val, dict) and not val:
            continue
        if isinstance(val, list) and not val:
            continue
        if isinstance(val, tuple):
            val = list(val)
        parts.append(f"  {attr}: {val}")

    if len(parts) == 1:
        return ""
    return "\n".join(parts) + "\n\n"


__all__ = [
    "build_patch_prompt",
    "build_retry_prompt",
]
