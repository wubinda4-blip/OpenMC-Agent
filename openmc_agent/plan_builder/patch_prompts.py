"""Per-patch-type prompt builders for incremental plan building (Phase 4).

Each builder produces a self-contained prompt that asks the LLM for **exactly
one** patch — never a full SimulationPlan, never a full lattice pattern.

Design constraints
------------------
* **No full plan.**  Every prompt includes the global rule that forbids
  outputting a complete SimulationPlan or OpenMC Python code.
* **No benchmark facts hardcoded.**  Only generic engineering vocabulary
  appears; benchmark numbers come from the requirement text and context.
* **Chinese-friendly.**  The requirement may be in Chinese, but the patch JSON
  keys must be English schema field names.
"""

from __future__ import annotations

from typing import Any

from .patches import PatchType


# ---------------------------------------------------------------------------
# Global rules (injected into every patch prompt)
# ---------------------------------------------------------------------------

_GLOBAL_RULES = """\
You are generating exactly ONE incremental planning patch for a reactor model.
Global rules (MUST follow):
- Output ONLY valid JSON matching the requested patch schema.
- Do NOT output a full SimulationPlan.
- Do NOT output OpenMC Python code.
- Do NOT output markdown fences or comments.
- Do NOT output the full lattice universe_pattern (e.g. all 289 cells of a 17x17).
- JSON keys MUST be the English schema field names shown below.
- Requirement may be in Chinese; JSON field values follow the schema types.
- If a physical fact is unknown, put it in missing_facts or assumptions; do NOT fabricate."""


# ---------------------------------------------------------------------------
# Per-patch-type prompt builders
# ---------------------------------------------------------------------------

_PATCH_RULES: dict[str, str] = {
    "facts": """\
Requested patch type: FactsPatch
Schema fields: benchmark_id, selected_variant, geometry_type, lattice_size [int,int],
  pin_pitch_cm, assembly_pitch_cm, has_axial_geometry, has_spacer_grids,
  has_special_pin_map, active_fuel_region_cm [float,float], axial_domain_cm [float,float],
  expected_spacer_grid_count, expected_pin_count, expected_guide_tube_count,
  expected_instrument_tube_count, expected_pyrex_count, expected_thimble_plug_count,
  material_roles [list[str]], missing_facts [list[str]], assumptions [list[str]].

Rules:
- Extract benchmark facts from the requirement; do NOT invent numbers.
- If variant is 3A/3B, set selected_variant.
- If requirement mentions spacer grids, set has_spacer_grids=true.
- If requirement mentions Pyrex/thimble plug/guide tube/instrument tube, set has_special_pin_map=true.
- Unknown facts go into missing_facts, NOT fabricated values.""",

    "materials": """\
Requested patch type: MaterialsPatch
Schema: { patch_type: "materials", materials: [ { material_id, name, role,
  density_g_cm3, temperature_K, composition {element: fraction}, composition_basis,
  composition_status, source_note, warnings [list[str]] } ], assumptions }

Rules:
- Do NOT generate universes, cells, or lattices.
- Zircaloy-4 is NOT pure Zr; SS-304 is NOT pure Fe; Inconel-718 is NOT pure Ni.
- If composition is incomplete, set composition_status to "needs_library" or "needs_confirmation".
- approximate alloy is allowed ONLY with composition_status="approximate" AND an explicit warning.
- Never set composition_status="confirmed" for a known multi-element alloy with a single-element composition.
- Cross section paths do NOT belong here.""",

    "universes": """\
Requested patch type: UniversesPatch
Schema: { patch_type: "universes", universes: [ { universe_id, kind, cells: [ {
  id, role, material_id, region_kind, r_min_cm, r_max_cm, fill_universe_id,
  protected_through_path } ], source_note, assumptions } ] }

Rules:
- Do NOT generate the lattice or full pin map.
- Each universe must have at least one cell.
- guide_tube must include internal coolant AND tube wall material.
- pyrex_rod must include a cell with pyrex material if variant requires it.
- fuel_pin should have a fuel material cell.
- Mark through-path cells with protected_through_path=true.
- r_min_cm < r_max_cm for annulus regions.""",

    "pin_map": """\
Requested patch type: PinMapPatch
Schema: { patch_type: "pin_map", variant, lattice_size [int,int], default_universe_id,
  coordinate_convention: { index_base, row_origin, col_origin, ordering },
  guide_tube_coords [[int,int]], instrument_tube_coords [[int,int]],
  pyrex_rod_coords [[int,int]], thimble_plug_coords [[int,int]],
  water_cell_coords [[int,int]], assumptions, source_note }

CRITICAL RULES:
- Do NOT output the full 17x17 lattice. Do NOT enumerate all 289 cells.
- ONLY output special coordinates and default_universe_id.
- lattice_size is [rows, cols], e.g. [17, 17].
- coordinate_convention.index_base must be 0 or 1.
- Each coordinate is [row, col] using the stated convention.
- If coordinates are uncertain, put them in assumptions, do NOT fabricate.""",

    "axial_layers": """\
Requested patch type: AxialLayersPatch
Schema: { patch_type: "axial_layers", axial_domain_cm [float,float],
  layers: [ { layer_id, role, z_min_cm, z_max_cm, fill_type, fill_id,
  requires_human_confirmation, assumptions, source_note } ] }

Rules:
- Do NOT generate materials/universes/lattice.
- active_fuel layer must exist if the problem is a 3D fuel assembly.
- active_fuel fill_type should be "lattice" and fill_id should be "assembly_lattice".
- Do NOT use default z=-1..1 for an explicit 3D benchmark.
- If z values are unknown, set requires_human_confirmation=true; do NOT fabricate.
- Do NOT represent spacer grids as axial layer material slabs.""",

    "axial_overlays": """\
Requested patch type: AxialOverlaysPatch
Schema: { patch_type: "axial_overlays", overlays: [ { overlay_id, overlay_kind,
  z_min_cm, z_max_cm, target_lattice_id, material_id, geometry_mode,
  through_path_preserved, volume_fraction, effective_density_g_cm3,
  requires_human_confirmation, assumptions, source_note } ] }

Rules:
- Spacer grids MUST be overlays, NOT material slabs.
- Default Level 1: geometry_mode="homogenized_open_region", through_path_preserved=true.
- target_lattice_id should be "assembly_lattice".
- If grid z positions are unknown, use geometry_mode="skeleton" with requires_human_confirmation=true.
- Do NOT claim volume-fraction calibration is complete unless context explicitly supports it.
- No explicit bars or mixing vanes unless the renderer supports them.""",

    "settings": """\
Requested patch type: SettingsPatch
Schema: { patch_type: "settings", source_strategy, source_requires_fissionable_constraint,
  plot_strategy, cross_sections_runtime_required, tallies_required_for_smoke_test, assumptions }

Rules:
- source_strategy defaults to "active_fuel_box".
- plot_strategy defaults to "full_assembly".
- cross_sections_runtime_required=true (runtime concern, not plan-generation blocker).
- tallies_required_for_smoke_test=false.
- Missing cross sections path does NOT block plan generation.""",
}


def build_patch_prompt(
    patch_type: str,
    requirement: str,
    context: Any | None = None,
) -> str:
    """Build a prompt for generating exactly one patch of the given type.

    Parameters
    ----------
    patch_type
        One of the :data:`PatchType` values.
    requirement
        The user/benchmark requirement text (may be Chinese).
    context
        Optional :class:`PatchGenerationContext` with confirmed facts,
        validated patch summaries, expected counts, etc.

    Returns
    -------
    str
        The complete prompt string.
    """
    patch_rules = _PATCH_RULES.get(patch_type, "")
    if not patch_rules:
        patch_rules = f"Requested patch type: {patch_type}\n(No specific rules defined.)"

    context_block = _context_block(context)
    retry_block = ""

    return (
        f"{_GLOBAL_RULES}\n\n"
        f"{patch_rules}\n\n"
        f"{context_block}"
        f"Requirement:\n{requirement}\n\n"
        f"{retry_block}"
        "Return ONLY the JSON object for this patch. No other text."
    )


def build_retry_prompt(
    patch_type: str,
    requirement: str,
    context: Any | None,
    issues: list[dict[str, Any]],
    attempt_index: int,
) -> str:
    """Build a retry prompt for a patch that failed validation.

    Includes the specific validation errors so the LLM can fix them without
    regenerating unrelated patches.
    """
    base_prompt = build_patch_prompt(patch_type, requirement, context)
    issue_lines: list[str] = []
    for issue in issues:
        sev = issue.get("severity", "error")
        code = issue.get("code", "unknown")
        msg = issue.get("message", "")
        issue_lines.append(f"  - [{sev}] {code}: {msg}")

    return (
        f"{base_prompt}\n\n"
        f"The previous {patch_type} (attempt {attempt_index}) failed validation:\n"
        + "\n".join(issue_lines)
        + "\n\nFix these issues and regenerate ONLY the "
        f"{patch_type} JSON. Do NOT output a full SimulationPlan."
    )


# ---------------------------------------------------------------------------
# Context serialization
# ---------------------------------------------------------------------------


def _context_block(context: Any | None) -> str:
    """Serialize the context into a prompt block. Returns empty string if None."""
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
