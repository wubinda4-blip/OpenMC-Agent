"""Concrete localized-insert state materializer (P2-FULLCORE-2C-B).

Transforms abstract localized-insert intents + resolved profiles into
concrete per-segment pin lattices, assembly wrapper universes, core
lattices, and CoreSpec.axial_layers.

Pipeline:
    global axial segments
    → per-segment derived pin lattices (base + insert overrides)
    → per-segment wrapper universes
    → per-segment core lattices
    → CoreSpec.axial_layers with segment-specific lattice fills

All objects are *concrete* — no runtime lattice-loading derivation needed
by the renderer.  The CoreRenderer simply stacks segment-specific core
lattices at the correct z intervals.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from openmc_agent.plan_builder.hierarchical_assembler import AxialSegment
from openmc_agent.plan_builder.patches import (
    AssemblyCatalogPatch,
    AssemblyPinMapPatchItem,
    AssemblyTypePatchItem,
    CoordinateConvention,
    CoreLayoutPatch,
    LocalizedInsertIntentPatchItem,
)
from openmc_agent.plan_builder.localized_insert_profiles import (
    ResolvedLocalizedInsertProfile,
)
from openmc_agent.schemas import (
    AxialLayerSpec,
    CellSpec,
    CoreSpec,
    FillRefSpec,
    LatticeSpec,
    UniverseSpec,
)


_Z_TOL: float = 1e-6


@dataclass
class ConcreteAxialStateResult:
    """Result of concrete axial-state materialization."""

    derived_pin_lattices: list[LatticeSpec] = field(default_factory=list)
    derived_wrapper_universes: list[UniverseSpec] = field(default_factory=list)
    derived_wrapper_cells: list[CellSpec] = field(default_factory=list)
    segment_core_lattices: list[LatticeSpec] = field(default_factory=list)
    axial_layers: list[AxialLayerSpec] = field(default_factory=list)
    segment_index: list[dict[str, Any]] = field(default_factory=list)
    issues: list[dict[str, str]] = field(default_factory=list)


def _expand_base_pin_pattern(
    pm: AssemblyPinMapPatchItem,
    kind_to_universe: dict[str, str] | None = None,
) -> list[list[str]]:
    """Expand a sparse pin map to a full base universe_pattern."""
    from openmc_agent.plan_builder.hierarchical_assembler import _expand_assembly_pin_map
    return _expand_assembly_pin_map(pm, kind_to_universe=kind_to_universe)


def _apply_insert_overrides(
    base_pattern: list[list[str]],
    intent: LocalizedInsertIntentPatchItem,
) -> list[list[str]]:
    """Return a copy of base_pattern with insert overrides applied."""
    result = [row[:] for row in base_pattern]
    for r, c in intent.coordinates:
        if 0 <= r < len(result) and 0 <= c < len(result[r]):
            result[r][c] = intent.insert_universe_id
    return result


def _get_active_inserts_for_segment(
    segment: AxialSegment,
    catalog: AssemblyCatalogPatch,
    resolved_profiles: list[ResolvedLocalizedInsertProfile] | None = None,
) -> dict[str, list[tuple[str, str, list[tuple[int, int]]]]]:
    """For a given segment, return active inserts per assembly type.

    Returns
    -------
    dict[assembly_type_id, list[(insert_id, insert_universe_id, coordinates)]]
    """
    result: dict[str, list[tuple[str, str, list[tuple[int, int]]]]] = {}

    for atype in catalog.assembly_types:
        type_id = atype.assembly_type_id
        active: list[tuple[str, str, list[tuple[int, int]]]] = []

        for intent in atype.pin_map.localized_insert_intents:
            if intent.axial_profile_id is not None:
                if resolved_profiles:
                    for rp in resolved_profiles:
                        if rp.insert_id == intent.insert_id and rp.assembly_type_id == type_id:
                            for rseg in rp.resolved_segments:
                                if (
                                    segment.z_min_cm >= rseg.absolute_z_min_cm - _Z_TOL
                                    and segment.z_max_cm <= rseg.absolute_z_max_cm + _Z_TOL
                                ):
                                    active.append((
                                        f"{intent.insert_id}::{rseg.segment_id}",
                                        rseg.universe_id,
                                        list(intent.coordinates),
                                    ))
                                    break
                continue

            z_min = intent.z_min_cm or float("-inf")
            z_max = intent.z_max_cm or float("inf")
            if (
                segment.z_min_cm >= z_min - _Z_TOL
                and segment.z_max_cm <= z_max + _Z_TOL
            ):
                active.append((
                    intent.insert_id,
                    intent.insert_universe_id,
                    list(intent.coordinates),
                ))

        if active:
            result[type_id] = active

    return result


def materialize_concrete_axial_states(
    catalog: AssemblyCatalogPatch,
    layout: CoreLayoutPatch,
    segments: list[AxialSegment],
    base_pin_lattices: dict[str, LatticeSpec],
    base_assembly_universe_ids: dict[str, str],
    *,
    kind_to_universe: dict[str, str] | None = None,
    resolved_profiles: list[ResolvedLocalizedInsertProfile] | None = None,
    pitch_cm: float = 1.26,
    moderator_universe_id: str = "moderator_outer",
    outer_universe_id: str | None = None,
    core_lattice_id_base: str = "core_lattice",
) -> ConcreteAxialStateResult:
    """Materialize concrete per-segment geometry.

    For each global axial segment:
    1. Determine active inserts per assembly type.
    2. Create derived pin lattices for types with active inserts.
    3. Create derived wrapper universes/cells.
    4. Create a segment-specific core lattice.

    Segments with identical states reuse the same derived geometry.
    """
    result = ConcreteAxialStateResult()

    pitch = layout.assembly_pitch_cm or 21.50
    n_rows, n_cols = layout.shape
    core_width_x = n_cols * pitch
    core_width_y = n_rows * pitch
    lower_left = (-core_width_x / 2.0, -core_width_y / 2.0)

    boundary = layout.boundary if layout.boundary in ("reflective", "vacuum", "periodic") else "reflective"

    # Cache: (type_id, frozenset of active overrides) → (lattice_id, wrapper_universe_id)
    derived_cache: dict[tuple[str, frozenset], tuple[str, str]] = {}
    seg_counter = 0

    for seg_idx, segment in enumerate(segments):
        active_map = _get_active_inserts_for_segment(
            segment, catalog, resolved_profiles,
        )

        # Build the state signature for dedup
        sig_parts: list[str] = []
        for atype in catalog.assembly_types:
            tid = atype.assembly_type_id
            acts = active_map.get(tid, [])
            if acts:
                sig_parts.append(f"{tid}:{sorted(a[0] for a in acts)}")
        state_sig = "|".join(sig_parts)

        # Build core lattice universe_pattern for this segment
        core_pattern: list[list[str]] = []
        for row in layout.assembly_pattern:
            pattern_row: list[str] = []
            for type_id in row:
                base_uv = base_assembly_universe_ids.get(type_id, outer_universe_id or "moderator_outer")
                acts = active_map.get(type_id)
                if acts:
                    # Need a derived lattice
                    cache_key = (type_id, frozenset(a[0] for a in acts))
                    if cache_key in derived_cache:
                        seg_uv = derived_cache[cache_key][1]
                    else:
                        # Create derived pin lattice
                        pm = next(
                            (at.pin_map for at in catalog.assembly_types
                             if at.assembly_type_id == type_id),
                            None,
                        )
                        if pm is None:
                            pattern_row.append(base_uv)
                            continue

                        base_pattern = base_pin_lattices[type_id].universe_pattern
                        derived_pattern = [row[:] for row in base_pattern]
                        for insert_id, insert_uv, coords in acts:
                            for r, c in coords:
                                if 0 <= r < len(derived_pattern) and 0 <= c < len(derived_pattern[r]):
                                    derived_pattern[r][c] = insert_uv

                        derived_lat_id = f"assembly_lattice__{type_id}__seg{seg_counter}"
                        derived_lat = LatticeSpec(
                            id=derived_lat_id,
                            name=f"derived pin lattice {type_id} seg{seg_counter}",
                            kind="rect",
                            pitch_cm=(pitch_cm, pitch_cm),
                            outer_universe_id=moderator_universe_id,
                            universe_pattern=derived_pattern,
                            shape=base_pin_lattices[type_id].shape,
                            purpose=f"Segment-specific lattice for {type_id} with active inserts: {[a[0] for a in acts]}",
                        )
                        result.derived_pin_lattices.append(derived_lat)

                        # Create derived wrapper universe + cell
                        seg_cell_id = f"assembly_wrapper_cell__{type_id}__seg{seg_counter}"
                        seg_universe_id = f"assembly_universe__{type_id}__seg{seg_counter}"
                        seg_cell = CellSpec(
                            id=seg_cell_id,
                            name=f"wrapper cell {type_id} seg{seg_counter}",
                            fill_type="lattice",
                            fill_id=derived_lat_id,
                            purpose=f"Segment-specific wrapper for {type_id}",
                        )
                        seg_universe = UniverseSpec(
                            id=seg_universe_id,
                            name=f"assembly universe {type_id} seg{seg_counter}",
                            cell_ids=[seg_cell_id],
                            purpose=f"Segment-specific wrapper universe for {type_id}",
                        )
                        result.derived_wrapper_cells.append(seg_cell)
                        result.derived_wrapper_universes.append(seg_universe)

                        derived_cache[cache_key] = (derived_lat_id, seg_universe_id)
                        seg_uv = seg_universe_id
                        seg_counter += 1
                    pattern_row.append(seg_uv)
                else:
                    pattern_row.append(base_uv)
            core_pattern.append(pattern_row)

        # Create segment-specific core lattice
        seg_core_id = f"{core_lattice_id_base}__seg{seg_idx}"

        # Check if this segment's core pattern is identical to base
        base_core_pattern = None
        for lat_id_check in [core_lattice_id_base]:
            base_core = next((l for l in base_pin_lattices.values() if l.id == core_lattice_id_base), None)

        # For dedup: if no active inserts, reuse base core lattice
        if not active_map:
            seg_core_id_final = core_lattice_id_base
        else:
            seg_core_id_final = seg_core_id
            seg_core_lat = LatticeSpec(
                id=seg_core_id,
                name=f"core lattice segment {seg_idx}",
                kind="rect",
                pitch_cm=(pitch, pitch),
                lower_left_cm=lower_left,
                center_cm=(0.0, 0.0),
                outer_universe_id=moderator_universe_id,
                universe_pattern=core_pattern,
                shape=(n_rows, n_cols),
                purpose=f"Segment-specific core lattice for z=[{segment.z_min_cm}, {segment.z_max_cm}]",
            )
            result.segment_core_lattices.append(seg_core_lat)

        # Create axial layer
        layer = AxialLayerSpec(
            id=f"layer_seg{seg_idx}",
            name=f"axial layer segment {seg_idx}",
            z_min_cm=segment.z_min_cm,
            z_max_cm=segment.z_max_cm,
            fill=FillRefSpec(type="lattice", id=seg_core_id_final),
            purpose=f"Segment {seg_idx}: z=[{segment.z_min_cm:.2f}, {segment.z_max_cm:.2f}] cm",
        )
        result.axial_layers.append(layer)

        result.segment_index.append({
            "segment_id": segment.segment_id,
            "z_min_cm": segment.z_min_cm,
            "z_max_cm": segment.z_max_cm,
            "core_lattice_id": seg_core_id_final,
            "active_types": list(active_map.keys()),
            "state_signature": state_sig,
            "has_derived_lattices": bool(active_map),
        })

    return result


__all__ = [
    "ConcreteAxialStateResult",
    "materialize_concrete_axial_states",
]
