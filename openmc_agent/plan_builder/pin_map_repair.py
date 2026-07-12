"""Deterministic semantic diagnosis for base-lattice pin-map count errors.

This module deliberately reasons from patch relationships and expanded counts,
not universe names or catalog order.  It can therefore repair only the narrow
case where an axial replacement was used as the base lattice default.
"""

from __future__ import annotations

from collections import Counter
from typing import Any

from pydantic import Field

from openmc_agent.schemas import AgentBaseModel, SimulationPlan, ValidationReport

from .assembler import _build_kind_to_universe_map, expand_pin_map
from .patches import AxialLayersPatch, PinMapPatch, UniversesPatch, parse_patch_content
from .state import PlanBuildState
from .validation_repair import PatchRepairOperation


class PinCountDelta(AgentBaseModel):
    universe_id: str
    actual: int
    expected: int
    delta: int


class UniverseUsageClassification(AgentBaseModel):
    base_lattice_universe_ids: list[str] = Field(default_factory=list)
    axial_profile_source_ids: list[str] = Field(default_factory=list)
    axial_profile_replacement_ids: list[str] = Field(default_factory=list)
    ambiguous_ids: list[str] = Field(default_factory=list)


class PinMapRepairDiagnosis(AgentBaseModel):
    lattice_id: str
    current_default_universe_id: str
    expanded_default_universe_id: str | None = None
    total_cells: int
    default_position_count: int
    coordinate_group_counts: dict[str, int]
    expected_counts: dict[str, int]
    actual_counts: dict[str, int]
    deltas: list[PinCountDelta]
    universe_catalog: list[dict[str, Any]]
    axial_profile_source_ids: list[str]
    axial_profile_replacement_ids: list[str]
    deterministic_repair_available: bool
    deterministic_operations: list[PatchRepairOperation]
    reasons: list[str] = Field(default_factory=list)


def _valid_patch(state: PlanBuildState, patch_type: str) -> Any | None:
    envelope = next(
        (item for item in state.patches.values() if item.patch_type == patch_type and item.status == "valid"),
        None,
    )
    if envelope is None:
        return None
    return parse_patch_content(patch_type, envelope.content)


def classify_universe_usage(
    *, pin_map: PinMapPatch, universes: UniversesPatch | None, axial_layers: AxialLayersPatch | None,
) -> UniverseUsageClassification:
    """Classify usage by explicit patch references, never by ID spelling."""
    sources: set[str] = set()
    replacements: set[str] = set()
    if axial_layers is not None:
        for loading in axial_layers.lattice_loadings:
            for transform in loading.transformations:
                if transform.operation_kind != "replace_universe_family":
                    continue
                if transform.source_universe_id:
                    sources.add(transform.source_universe_id)
                sources.update(transform.source_universe_ids)
                replacements.add(transform.replacement_universe_id)
    kind_map = _build_kind_to_universe_map(universes, pin_map)
    special_ids = {
        kind_map.get(kind)
        for kind in ("guide_tube", "instrument_tube", "pyrex_rod", "thimble_plug", "water_cell")
    }
    base = {pin_map.default_universe_id, *sources, *(item for item in special_ids if item)}
    # A universe used as both a base/source and an axial replacement is not
    # sufficient evidence to select it as a default automatically.
    ambiguous = (base & replacements) - {pin_map.default_universe_id}
    return UniverseUsageClassification(
        base_lattice_universe_ids=sorted(base),
        axial_profile_source_ids=sorted(sources),
        axial_profile_replacement_ids=sorted(replacements),
        ambiguous_ids=sorted(ambiguous),
    )


def _coordinate_group_counts(pin_map: PinMapPatch) -> tuple[dict[str, int], int]:
    kind_map = _build_kind_to_universe_map(None, pin_map)
    # Coordinate normalization/overlap validation belongs to the assembler;
    # expanding here provides the exact count in its canonical convention.
    grid = expand_pin_map(pin_map, universe_ids=kind_map)
    total = sum(len(row) for row in grid)
    counts = {
        "guide_tube": len(pin_map.guide_tube_coords),
        "instrument_tube": len(pin_map.instrument_tube_coords),
        "pyrex_rod": len(pin_map.pyrex_rod_coords),
        "thimble_plug": len(pin_map.thimble_plug_coords),
        "water_cell": len(pin_map.water_cell_coords),
    }
    # A coordinate can be invalid/overlapping only in invalid patch input. The
    # local patch validator has already ruled that out before diagnosis.
    return counts, total - sum(counts.values())


def preview_pin_map_candidate_counts(
    *, state: PlanBuildState, candidate_patch: PinMapPatch,
) -> dict[str, Any]:
    """Return the deterministic base-lattice universe counts for a candidate."""
    universes = _valid_patch(state, "universes")
    if not isinstance(universes, UniversesPatch):
        return {"ok": False, "reason": "valid universes patch is unavailable", "actual_counts": {}}
    ids = {item.universe_id for item in universes.universes}
    if candidate_patch.default_universe_id not in ids:
        return {
            "ok": False,
            "reason": "candidate default_universe_id is missing from universes",
            "actual_counts": {},
        }
    pattern = expand_pin_map(candidate_patch, universe_ids=_build_kind_to_universe_map(universes, candidate_patch))
    return {
        "ok": True,
        "expanded_default_universe_id": candidate_patch.default_universe_id,
        "actual_counts": dict(sorted(Counter(item for row in pattern for item in row).items())),
        "total_cells": sum(len(row) for row in pattern),
    }


def diagnose_pin_map_count_mismatch(
    *, state: PlanBuildState, plan: SimulationPlan, report: ValidationReport, target_patch: PinMapPatch,
) -> PinMapRepairDiagnosis:
    """Prove the one safe default-universe correction, if one exists."""
    lattice = next((item for item in plan.complex_model.lattices if item.id == "assembly_lattice"), None)
    if lattice is None:
        lattice = plan.complex_model.lattices[0] if plan.complex_model.lattices else None
    if lattice is None:
        return PinMapRepairDiagnosis(
            lattice_id="", current_default_universe_id=target_patch.default_universe_id,
            total_cells=0, default_position_count=0, coordinate_group_counts={}, expected_counts={},
            actual_counts={}, deltas=[], universe_catalog=[], axial_profile_source_ids=[],
            axial_profile_replacement_ids=[], deterministic_repair_available=False,
            deterministic_operations=[], reasons=["assembled plan has no lattice"],
        )
    universes = _valid_patch(state, "universes")
    axial = _valid_patch(state, "axial_layers")
    universes = universes if isinstance(universes, UniversesPatch) else None
    axial = axial if isinstance(axial, AxialLayersPatch) else None
    classification = classify_universe_usage(pin_map=target_patch, universes=universes, axial_layers=axial)
    expected = dict(lattice.expected_counts or {})
    actual = dict(Counter(item for row in lattice.universe_pattern for item in row))
    deltas = [
        PinCountDelta(universe_id=item, actual=actual.get(item, 0), expected=expected.get(item, 0), delta=actual.get(item, 0) - expected.get(item, 0))
        for item in sorted(set(actual) | set(expected)) if actual.get(item, 0) != expected.get(item, 0)
    ]
    try:
        preview = preview_pin_map_candidate_counts(state=state, candidate_patch=target_patch)
        # Read the current assembled pattern at positions that the pin map
        # designates as defaults.  This exposes a stale/incorrect assembler
        # expansion without trusting the corrected expansion implementation.
        expected_pattern = expand_pin_map(
            target_patch, universe_ids=_build_kind_to_universe_map(universes, target_patch),
        )
        observed_defaults = {
            lattice.universe_pattern[row][col]
            for row, line in enumerate(expected_pattern)
            for col, value in enumerate(line)
            if value == target_patch.default_universe_id
        }
        expanded_default = next(iter(observed_defaults)) if len(observed_defaults) == 1 else None
        group_counts, default_count = _coordinate_group_counts(target_patch)
    except ValueError as exc:
        preview, expanded_default, group_counts, default_count = {}, None, {}, 0
        diagnostic_reason = f"pin-map expansion failed: {exc}"
    else:
        diagnostic_reason = ""
    catalog = []
    for item in (universes.universes if universes else []):
        catalog.append({
            "universe_id": item.universe_id,
            "kind": item.kind,
            "base_lattice_candidate": item.universe_id in classification.base_lattice_universe_ids,
            "axial_profile_source": item.universe_id in classification.axial_profile_source_ids,
            "axial_profile_replacement": item.universe_id in classification.axial_profile_replacement_ids,
        })
    deficits = [item for item in deltas if item.delta < 0]
    surpluses = [item for item in deltas if item.delta > 0]
    reasons: list[str] = [diagnostic_reason] if diagnostic_reason else []
    operations: list[PatchRepairOperation] = []
    if len(deficits) != 1 or len(surpluses) != 1:
        reasons.append("count deltas do not have one deficit and one surplus universe")
    else:
        deficit, surplus = deficits[0], surpluses[0]
        if -deficit.delta != surplus.delta:
            reasons.append("deficit and surplus counts are not equal")
        elif surplus.delta != default_count:
            reasons.append("count delta does not match all default lattice positions")
        elif deficit.universe_id not in classification.axial_profile_source_ids and deficit.universe_id != target_patch.default_universe_id:
            reasons.append("deficit universe is not an explicit base or axial source")
        elif surplus.universe_id not in classification.axial_profile_replacement_ids:
            reasons.append("surplus universe is not an axial replacement")
        elif target_patch.default_universe_id != surplus.universe_id and expanded_default != surplus.universe_id:
            reasons.append("surplus universe is not the current expanded default")
        elif deficit.universe_id in classification.ambiguous_ids:
            reasons.append("deficit universe usage is ambiguous")
        else:
            operations = [PatchRepairOperation(op="replace", path="/default_universe_id", value=deficit.universe_id)]
            reasons.append("equal and opposite default-position deltas prove the base universe replacement")
    return PinMapRepairDiagnosis(
        lattice_id=lattice.id,
        current_default_universe_id=target_patch.default_universe_id,
        expanded_default_universe_id=expanded_default,
        total_cells=sum(len(row) for row in lattice.universe_pattern),
        default_position_count=default_count,
        coordinate_group_counts=group_counts,
        expected_counts=expected,
        actual_counts=actual,
        deltas=deltas,
        universe_catalog=catalog,
        axial_profile_source_ids=classification.axial_profile_source_ids,
        axial_profile_replacement_ids=classification.axial_profile_replacement_ids,
        deterministic_repair_available=bool(operations),
        deterministic_operations=operations,
        reasons=reasons,
    )
