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
  model_scope (single_pin|single_assembly|multi_assembly_core|full_core|unknown),
  assembly_count, core_lattice_size [int,int], assembly_type_counts {{type_id: count}},
  scoped_expected_counts [list of {{role, value, scope, assembly_type_id}}],
  boundary_scope, symmetry_description,
  material_roles [list[str]], missing_facts [list[str]], assumptions [list[str]],
  source_notes [list[str]].

Rules:
- Extract benchmark facts from the requirement; do NOT invent numbers.
- If variant is 3A/3B, set selected_variant.
- If requirement mentions spacer grids, set has_spacer_grids=true.
- If requirement mentions Pyrex/thimble plug/guide tube/instrument tube, set has_special_pin_map=true.
- Determine model_scope: single_assembly for one assembly, multi_assembly_core for N×N cores.
- For multi-assembly cores: set assembly_count, core_lattice_size, assembly_type_counts.
- For multi-assembly cores: use scoped_expected_counts with explicit scope, NOT legacy fields.
- Do NOT divide core totals by assembly count to guess per-assembly counts.
- Legacy fields (expected_pin_count etc.) are for single_assembly only.
- Unknown facts go into missing_facts, NOT fabricated values.

Minimal example (single assembly):
{{"patch_type": "facts", "benchmark_id": "EXAMPLE", "selected_variant": "3B",
  "model_scope": "single_assembly", "lattice_size": [17, 17], "has_axial_geometry": true,
  "has_spacer_grids": true, "has_special_pin_map": true, "missing_facts": []}}

Reactor-neutral multi-assembly example:
{{"patch_type": "facts", "model_scope": "multi_assembly_core",
  "core_lattice_size": [2, 2], "assembly_count": 4,
  "assembly_type_counts": {{"type_a": 2, "type_b": 2}},
  "scoped_expected_counts": [
    {{"role": "fuel_pin", "value": 1000, "scope": "core_total"}},
    {{"role": "fuel_pin", "value": 250, "scope": "assembly_type", "assembly_type_id": "type_a"}}
  ]}}""",

    "materials": """\
Requested patch type: materials
Schema: {{"patch_type": "materials", "materials": [{{"material_id", "name", "role",
  "density_g_cm3", "temperature_K", "composition": {{"element": fraction}},
  "composition_basis", "composition_status", "source_note", "warnings": [],
  "mixture_components": [{{"material_id", "volume_fraction"}}]}}], "assumptions": []}}

Rules:
- Do NOT generate universes, cells, lattices, or axial structures.
- Zircaloy-4 is NOT pure Zr; SS-304 is NOT pure Fe; Inconel-718 is NOT pure Ni.
- If composition is incomplete, set composition_status to "needs_library" or "needs_confirmation".
- approximate alloy is allowed ONLY with composition_status="approximate" AND an explicit warning.
- Never set composition_status="confirmed" for a known multi-element alloy with a single-element composition.
- When the input specifies a homogenized structural slab (for example a nozzle,
  support plate, baffle, or other steel/coolant structure), define a distinct
  material with role="structural" and mixture_components referencing the existing
  structural and coolant material IDs with the input-provided volume fractions.
  Do not represent that mixture only in an assumption and do not substitute pure
  coolant for the structural slab.
- Cross section paths do NOT belong here.

composition_basis semantics (MUST declare for every material with a composition):
- "atom_frac": each value is an atom fraction (e.g., U235=3.1 means 3.1 at% U-235).
- "weight_frac": each value is a weight fraction (e.g., Zr=98.2 means 98.2 wt% Zr).
- "atom_density_barn_cm": each value is an absolute atom density in atoms/barn-cm
  (use this when the source gives values like 2.233e-2).
- "stoichiometric_ratio": the fuel nuclides sum to ~100 (enrichment vector,
  e.g., U235=3.1 means 3.1 at% of the uranium) and the oxygen entry gives the
  O/U ratio (e.g., O16=2.0 means O/U=2 for UO2).
  Use this when the source specifies enrichment per-100-uranium and oxygen as
  a molecular ratio. If U isotopes sum to ~100 and O16 is a small number
  like 2.0, this is ALWAYS stoichiometric_ratio, NEVER weight_frac.
- "ppm_by_weight": a coolant entry like B10=1066 means 1066 ppm total boron
  by weight (NOT an atom fraction).  Use this when the source gives boron
  concentration in ppm.
- "unknown": only if truly unclear; this will BLOCK rendering and require retry.

Minimal example:
{{"patch_type": "materials", "materials": [
 {{"material_id": "fuel", "name": "UO2", "role": "fuel", "density_g_cm3": 10.257,
   "composition": {{"U235": 3.1, "U238": 96.9, "O16": 2.0}},
   "composition_basis": "stoichiometric_ratio",
   "composition_status": "approximate",
   "warnings": ["enrichment approximate"]}},
 {{"material_id": "coolant", "name": "Borated water", "role": "coolant",
   "density_g_cm3": 0.743, "composition": {{"B10": 1066, "H1": 2.0, "O16": 1.0}},
   "composition_basis": "ppm_by_weight",
   "composition_status": "confirmed"}}
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
- pyrex_rod must include ALL gap layers from the input spec, not just pyrex+clad.
  A pyrex rod has concentric annuli with thin gas/water gaps between solid layers.
  Do NOT merge a gap into an adjacent solid layer. Correct cell order (inside→out):
    inner_water_or_helium (cylinder, inside inner tube)
    → inner_tube (SS304 annulus)
    → gap_1 (water or helium annulus, between inner tube and pyrex)
    → pyrex (pyrex glass annulus)
    → gap_2 (water or helium annulus, between pyrex and outer clad)
    → outer_clad (SS304 annulus)
    → outer_coolant (water background)
  Use the exact radii from the input problem description for each layer boundary.
  If the input does not specify a gap, insert a thin water annulus (0.001–0.01 cm).
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
  "water_cell_coords": [[int,int]],
  "localized_insert_intents": [{{"insert_id", "insert_kind",
  "host_kind", "insert_universe_id", "coordinates": [[int,int]],
  "z_min_cm", "z_max_cm", "application_mode", "component_role",
  "preserve_component_roles": [], "priority", "requires_human_confirmation"}}],
  "assumptions": [], "source_note"}}

CRITICAL RULES:
- Do NOT output the full 17x17 lattice. Do NOT enumerate all 289 cells.
- Do NOT output rows of fuel_pin_universe.
- ONLY output special coordinate lists and default_universe_id.
- lattice_size is [rows, cols], e.g. [17, 17].
- coordinate_convention.index_base must be 0 or 1.
- Each coordinate is [row, col] using the stated convention.
- guide_tube_coords lists ALL persistent guide tube paths (full assembly height).
  This includes positions that will host localized inserts (Pyrex, thimble plugs, etc.).
  A guide tube coordinate that hosts an insert is still a guide_tube_coord —
  the insert only affects a specific z range within that guide tube.
- localized_insert_intents declares finite-height inserts (Pyrex rods, thimble plugs,
  absorbers, control rods). Each intent specifies:
  * insert_kind: pyrex_rod | thimble_plug | absorber_insert | control_rod | custom
  * host_kind: guide_tube (the host path whose inner component is replaced)
  * insert_universe_id: the universe to use inside the host for this z range
  * coordinates: positions affected (must be a subset of guide_tube_coords)
  * z_min_cm, z_max_cm: the axial interval where the insert is active
  * application_mode: "nested_component_override" (default, preserves host wall)
  * component_role: role of the replaced inner component (e.g. "internal_coolant")
  * preserve_component_roles: roles to keep (e.g. ["tube_wall", "outer_coolant"])
- Coordinates of localized inserts MUST also appear in guide_tube_coords.
- Do NOT put insert universes as base lattice positions.
- Do NOT stretch a finite insert over the full assembly height.

Minimal example:
{{"patch_type": "pin_map", "lattice_size": [17, 17], "default_universe_id": "fuel_pin",
  "coordinate_convention": {{"index_base": 1, "row_origin": "top", "col_origin": "left", "ordering": "row_col"}},
  "guide_tube_coords": [[3,6], [3,9], [6,6], [9,3]],
  "instrument_tube_coords": [[9,9]],
  "localized_insert_intents": [
    {{"insert_id": "absorber_group_1", "insert_kind": "absorber_insert",
      "host_kind": "guide_tube", "insert_universe_id": "absorber_inner_profile",
      "coordinates": [[3,6], [6,6]], "z_min_cm": 20.0, "z_max_cm": 200.0,
      "application_mode": "nested_component_override",
      "component_role": "internal_coolant",
      "preserve_component_roles": ["tube_wall", "outer_coolant"], "priority": 0}}
  ]}}""",

    "axial_layers": """\
Requested patch type: axial_layers
Schema: {{"patch_type": "axial_layers", "axial_domain_cm": [float,float],
  "layers": [{{"layer_id", "role", "z_min_cm", "z_max_cm", "fill_type", "fill_id",
  "loading_id", "loading_ids", "requires_human_confirmation", "assumptions": [], "source_note"}}],
  "lattice_loadings": [{{"loading_id", "base_lattice_id", "derived_lattice_id",
  "transformations": [{{"operation_id", "operation_kind", "replacement_universe_id",
  "source_universe_id", "source_universe_ids", "target_coordinates",
  "component_role", "component_path_id", "preserve_component_roles",
  "preserve_path_ids", "priority", "purpose"}}],
  "overrides": {{"universe_id": [[int,int]]}}, "purpose"}}]}}

Rules:
- Do NOT generate materials/universes/lattice/full plan.
- For fill_type="material", fill_id MUST be one of Context.known_material_ids. Do not
  invent homogenized mixture IDs; use an existing ID or mark the layer for human
  confirmation when no supported material is available.
- lower_nozzle, upper_nozzle, and core_plate are whole-cross-section structural
  slabs. They MUST use an existing material whose Context material role is
  structural/steel/mixture, never a coolant/moderator material merely because the
  slab is homogenized with coolant. Do not encode a steel/coolant mixture only as
  an assumption while filling the layer with pure coolant.
- For fill_type="universe" and every transformation replacement_universe_id,
  use only Context.known_universe_ids. Do not invent end-plug or plenum universes.
- active_fuel layer must exist if the problem is a 3D fuel assembly.
- active_fuel fill_type should be "lattice" and fill_id should be "assembly_lattice".
- Do NOT use default z=-1..1 for an explicit 3D benchmark.
- If z values are unknown, set requires_human_confirmation=true; do NOT fabricate.
- Do NOT represent spacer grids as axial layer material slabs.
- Do NOT enumerate every default fuel-pin coordinate. Use replace_universe_family
  for component profiles shared by all pins of a universe family.
- Use sparse coordinate_override only for localized positions.
- Use nested_component_override when an insert occupies the inside of an existing
  tube and the tube wall must remain.
- IMPORTANT: If localized_insert_intents are declared in the pin_map, do NOT
  create separate lattice_loadings for those inserts. The assembler automatically
  derives loadings from localized_insert_intents. Only create lattice_loadings
  for fuel-component-profile transformations (replace_universe_family) and
  for inserts NOT declared as localized_insert_intents.
- Use loading_ids when an axial layer requires more than one localized loading.
- Spacer grids remain axial_overlays, not lattice transformations.
- Component-profile layers (end_plug, plenum, gas_gap, shoulder_gap) must use lattice fill
  with a replace_universe_family transformation, NOT a whole-layer material slab.
- shoulder_gap is the moderator region between the fuel stack and the nozzle/end structure.
  Guide tubes and instrument tubes continue through it. Use role "shoulder_gap"
  (or "lower_shoulder_gap" / "upper_shoulder_gap" for positional clarity).
  fill_type must be "lattice" with fill_id "assembly_lattice" and a loading_id that
  replaces only the fuel-pin family with a moderator-only universe.
  Do NOT use fill_type=material for shoulder_gap.
  Do NOT label shoulder_gap as lower_plenum/upper_plenum.

Transformation operation_kind values:
- "replace_universe_family": source_universe_id -> replacement_universe_id for all positions.
- "coordinate_override": specific target_coordinates -> replacement_universe_id.
- "nested_component_override": specific target_coordinates, component_role identifies the
  cell to replace; preserve_component_roles lists cells that must survive.

Minimal example (family replacement for a plenum layer):
{{"patch_type": "axial_layers", "axial_domain_cm": [0.0, 400.0],
  "lattice_loadings": [
    {{"loading_id": "plenum_loading", "base_lattice_id": "assembly_lattice",
      "derived_lattice_id": "assembly_lattice_plenum",
      "transformations": [
        {{"operation_id": "family_plenum", "operation_kind": "replace_universe_family",
          "replacement_universe_id": "fuel_pin_plenum", "source_universe_id": "fuel_pin"}}
      ]}}
  ],
  "layers": [
    {{"layer_id": "active_fuel", "role": "active_fuel", "z_min_cm": 10.0, "z_max_cm": 375.0,
      "fill_type": "lattice", "fill_id": "assembly_lattice"}},
    {{"layer_id": "upper_plenum", "role": "upper_plenum", "z_min_cm": 379.0, "z_max_cm": 395.0,
      "fill_type": "lattice", "fill_id": "assembly_lattice", "loading_id": "plenum_loading"}}
  ]}}""",

    "axial_overlays": """\
Requested patch type: axial_overlays
Schema: {{"patch_type": "axial_overlays", "overlays": [{{"overlay_id", "overlay_kind",
  "z_min_cm", "z_max_cm", "target_lattice_id", "material_id", "geometry_mode",
  "through_path_preserved", "requires_human_confirmation", "assumptions": []}}]}}

Rules:
- Spacer grids MUST be overlays, NOT material slabs.
- When the source provides grid mass or density, use geometry_mode="mass_conserving_outer_frame"
  with target_lattice_id="assembly_lattice", material_id, and total_mass_g set from the source.
  This preserves through-paths (fuel, clad, guide tubes) while adding the grid frame.
- Only use geometry_mode="homogenized_open_region" when mass data is truly unavailable.
- target_lattice_id should be "assembly_lattice".
- If grid z positions are unknown, use geometry_mode="skeleton" with requires_human_confirmation=true.

Minimal example (1 overlay):
{{"patch_type": "axial_overlays", "overlays": [
  {{"overlay_id": "grid_1", "overlay_kind": "spacer_grid", "z_min_cm": 50.0, "z_max_cm": 52.0,
    "target_lattice_id": "assembly_lattice", "material_id": "grid_mat",
    "geometry_mode": "mass_conserving_outer_frame", "through_path_preserved": true}}
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

    "assembly_catalog": """\
Requested patch type: assembly_catalog
Schema: {{"patch_type": "assembly_catalog",
  "assembly_types": [
    {{"assembly_type_id": "type_id", "name": "descriptive name", "role": "fuel/guide/etc",
      "multiplicity_hint": int_or_null,
      "pin_map": {{
        "lattice_size": [int, int],
        "default_universe_id": "universe_id",
        "coordinate_convention": {{"index_base": 0, "row_origin": "top", "col_origin": "left", "ordering": "row_col"}},
        "guide_tube_coords": [[row, col], ...],
        "instrument_tube_coords": [[row, col], ...],
        "water_cell_coords": [[row, col], ...],
        "localized_insert_intents": [
          {{"insert_id": "id", "insert_kind": "pyrex_rod|thimble_plug|absorber_insert|...",
            "host_kind": "guide_tube", "insert_universe_id": "universe_id",
            "coordinates": [[row, col], ...], "z_min_cm": float, "z_max_cm": float,
            "application_mode": "coordinate_override"}}
        ]
      }},
      "axial_profile_id": "id_or_null",
      "overlay_set_id": "id_or_null",
      "requires_human_confirmation": false
    }}
  ],
  "assumptions": [], "source_note": "optional"}}

Rules:
- Only output assembly type TEMPLATES — not core placement.
- Do NOT output a full expanded pin lattice (e.g. 17x17 matrix).
- Each assembly type outputs ONLY: default universe + sparse special coordinates.
- Different assembly types CAN share universe IDs.
- Different assembly types CAN have different localized inserts.
- Do NOT divide core totals evenly among assembly types.
- Each type's local structure must come from the requirement document.
- If the requirement does not provide enough detail for a type, set requires_human_confirmation=true.
- The multiplicity_hint is advisory; the core_layout patch determines actual placement.

Reactor-neutral example (2 types, 3x3 lattice):
{{"patch_type": "assembly_catalog",
  "assembly_types": [
    {{"assembly_type_id": "type_a", "pin_map": {{
      "lattice_size": [3, 3], "default_universe_id": "fuel_cell",
      "guide_tube_coords": [[1, 1]]}}}},
    {{"assembly_type_id": "type_b", "pin_map": {{
      "lattice_size": [3, 3], "default_universe_id": "fuel_cell",
      "guide_tube_coords": [[0, 0], [2, 2]],
      "localized_insert_intents": [
        {{"insert_id": "abs1", "insert_kind": "absorber_insert",
          "host_kind": "guide_tube", "insert_universe_id": "absorber",
          "coordinates": [[0, 0]], "application_mode": "coordinate_override"}}
      ]}}}}
  ]}}""",

    "localized_insert_profiles": """\
Requested patch type: localized_insert_profiles
Schema: {{"patch_type": "localized_insert_profiles",
  "profiles": [
    {{"profile_id": "unique_id",
      "anchor_kind": "bottom|top|center|absolute",
      "anchor_z_cm": float_or_null,
      "segments": [
        {{"segment_id": "unique_within_profile",
          "relative_z_min_cm": float,
          "relative_z_max_cm": float,
          "universe_id": "known_universe",
          "role": "absorber|plenum|end_structure|...",
          "source_note": "optional"}}
      ],
      "source_note": "optional",
      "assumptions": []}}
  ],
  "assumptions": [],
  "source_note": "optional"}}

Rules:
- Define ONLY reusable axial profiles — no pin coordinates, no core layout.
- Each segment uses RELATIVE coordinates (relative to anchor point).
- Each segment MUST reference a universe already defined in the universes patch.
- Segments MUST be ordered by relative_z (ascending), no overlaps.
- Mechanical gaps between segments MUST be explicitly modeled or noted in assumptions.
- Do NOT adjust profiles based on keff or criticality targets.
- For movable inserts (control rods), the ACTUAL position is provided by intent.anchor_z_cm,
  NOT the profile. The profile only defines the relative structure.
- anchor_kind determines coordinate translation: bottom=additive, top=subtractive, center=bilateral.
- Use anchor_kind="absolute" only when segments are already in global coordinates.

Reactor-neutral example (multi-segment control rod profile):
{{"patch_type": "localized_insert_profiles",
  "profiles": [
    {{"profile_id": "rod_type_a",
      "anchor_kind": "bottom",
      "segments": [
        {{"segment_id": "lower_end", "relative_z_min_cm": 0.0, "relative_z_max_cm": 10.0,
          "universe_id": "rod_end_structure", "role": "end_structure"}},
        {{"segment_id": "absorber", "relative_z_min_cm": 10.0, "relative_z_max_cm": 110.0,
          "universe_id": "rod_absorber", "role": "absorber"}},
        {{"segment_id": "plenum", "relative_z_min_cm": 110.0, "relative_z_max_cm": 140.0,
          "universe_id": "rod_plenum", "role": "plenum"}}
      ]}}
  ]}}""",

    "core_layout": """\
Requested patch type: core_layout
Schema: {{"patch_type": "core_layout",
  "core_lattice_id": "core_lattice",
  "shape": [int, int],
  "assembly_pitch_cm": float,
  "coordinate_convention": {{"index_base": 0, "row_origin": "top", "col_origin": "left", "ordering": "row_col"}},
  "assembly_pattern": [["type_id", ...], ...],
  "outer_assembly_type_id": "type_id_or_null",
  "boundary": "reflective|vacuum|periodic",
  "expected_assembly_type_counts": {{"type_id": count}},
  "symmetry_description": "optional",
  "requires_human_confirmation": false}}

Rules:
- Only output assembly type PLACEMENT — not pin coordinates.
- Do NOT re-define materials or universes.
- Each entry in assembly_pattern MUST be a known assembly_type_id from the assembly_catalog.
- The pattern shape MUST match the shape field [rows, cols].
- All rows MUST have the same length.
- Expected type multiplicities MUST match the pattern counts.
- Use the boundary from the requirement document.
- Do NOT fill missing positions with guesses.

Reactor-neutral example (2x2 heterogeneous):
{{"patch_type": "core_layout",
  "core_lattice_id": "core_lattice",
  "shape": [2, 2],
  "assembly_pitch_cm": 21.5,
  "assembly_pattern": [["type_a", "type_b"], ["type_b", "type_a"]],
  "expected_assembly_type_counts": {{"type_a": 2, "type_b": 2}},
  "boundary": "reflective"}}""",
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
        "model_scope", "assembly_count", "core_lattice_size",
        "assembly_type_counts", "known_assembly_type_ids",
        "assembly_pitch_cm", "scoped_expected_counts",
        "known_insert_profile_ids", "insert_profile_summaries",
        "movable_insert_facts",
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
