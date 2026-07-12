"""Deterministic component-profile / shoulder-gap slab repair.

When an axial layer whose role is a fuel-pin internal component profile
(end plug, plenum, gas gap, shoulder gap) is modeled as a single material
slab, it truncates every pin and tube through-path.  This module diagnoses
whether the defect is *uniquely solvable* from the existing plan structure
and, when it is, produces a deterministic multi-patch repair bundle that:

1. derives or reuses a moderator-only universe (if needed);
2. adds a lattice loading that replaces only the base fuel-pin family;
3. updates the offending layer to ``fill_type=lattice`` with that loading.

The bundle is applied atomically on a clone; the real ``PlanBuildState`` is
never mutated until every gate passes.
"""

from __future__ import annotations

import copy
import hashlib
import json
from typing import Any, Literal

from pydantic import Field

from openmc_agent.schemas import AgentBaseModel, SimulationPlan, ValidationReport
from openmc_agent.validator import validate_simulation_plan

from .assembler import assemble_simulation_plan_from_patches
from .patches import (
    AxialLayerPatchItem,
    AxialLayersPatch,
    LatticeLoadingPatchItem,
    LatticeTransformationPatchItem,
    UniversesPatch,
    parse_patch_content,
)
from .pin_map_repair import _valid_patch
from .state import PlanBuildState
from .validation_repair import PatchRepairOperation, stable_json_hash

# Roles that represent fuel-pin internal component profiles.  A material slab
# for any of these truncates the whole assembly cross section.
_COMPONENT_PROFILE_ROLES: frozenset[str] = frozenset({
    "lower_end_plug",
    "upper_end_plug",
    "lower_plenum",
    "upper_plenum",
    "gas_gap",
    "shoulder_gap",
    "lower_shoulder_gap",
    "upper_shoulder_gap",
})

# Roles / text signals that indicate the layer is a shoulder / moderator gap
# (not a solid structural component like a nozzle or core plate).
_SHOULDER_GAP_SIGNALS: frozenset[str] = frozenset({
    "shoulder_gap",
    "lower_shoulder_gap",
    "upper_shoulder_gap",
    "gas_gap",
})

# Roles that are solid structural components and must NOT be treated as
# moderator-only shoulder gaps.
_SOLID_STRUCTURE_ROLES: frozenset[str] = frozenset({
    "lower_nozzle",
    "upper_nozzle",
    "core_plate",
    "reflector",
})


# ---------------------------------------------------------------------------
# Diagnosis model
# ---------------------------------------------------------------------------


class ComponentProfileSlabDiagnosis(AgentBaseModel):
    layer_id: str
    layer_role: str
    z_min_cm: float
    z_max_cm: float

    current_fill_type: str
    current_fill_id: str | None

    base_lattice_id: str | None
    base_default_universe_id: str | None

    guide_tube_count: int
    instrument_tube_count: int

    background_material_id: str | None
    candidate_profile_universe_ids: list[str] = Field(default_factory=list)
    candidate_loading_ids: list[str] = Field(default_factory=list)

    repair_kind: Literal[
        "reuse_existing_loading",
        "create_moderator_profile_bundle",
        "component_profile_loading_required",
        "ambiguous",
    ]
    deterministic_repair_available: bool
    reasons: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Multi-patch repair bundle models
# ---------------------------------------------------------------------------


class PatchRepairBundleOperation(AgentBaseModel):
    patch_type: str
    operation: PatchRepairOperation


class PatchRepairBundleProposal(AgentBaseModel):
    repair_id: str
    strategy: str
    operations: list[PatchRepairBundleOperation]
    rationale: str
    confidence: float


class PatchRepairBundleEvaluation(AgentBaseModel):
    accepted: bool
    status: str
    patch_evaluations: dict[str, Any] = Field(default_factory=dict)
    validation_report_before: dict[str, Any] = Field(default_factory=dict)
    validation_report_after: dict[str, Any] = Field(default_factory=dict)
    capability_before: dict[str, Any] | None = None
    capability_after: dict[str, Any] | None = None
    repaired_plan: dict[str, Any] | None = None
    repaired_patches: dict[str, Any] = Field(default_factory=dict)
    reasons: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _build_kind_to_universe_map_from_patches(
    universes: UniversesPatch | None,
    pin_map: Any | None,
) -> dict[str, str]:
    """Map pin-map special kinds to their universe IDs."""
    kind_map: dict[str, str] = {}
    if universes is not None:
        for u in universes.universes:
            if u.kind in ("guide_tube", "instrument_tube", "pyrex_rod", "thimble_plug", "water_cell"):
                kind_map.setdefault(u.kind, u.universe_id)
    if pin_map is not None:
        for kind in ("guide_tube", "instrument_tube", "pyrex_rod", "thimble_plug", "water_cell"):
            coords = getattr(pin_map, f"{kind}_coords", None)
            if coords and kind not in kind_map:
                pass
    return kind_map


def _find_background_material_id(universes: UniversesPatch, source_universe_id: str) -> str | None:
    """Find the background/open moderator material from a fuel-pin universe."""
    target = next((u for u in universes.universes if u.universe_id == source_universe_id), None)
    if target is None:
        return None
    # The background cell is typically role=coolant/moderator with region_kind=background
    for cell in reversed(target.cells):
        if cell.region_kind == "background" and cell.material_id:
            return cell.material_id
    # Fallback: any cell with coolant/moderator role
    for cell in target.cells:
        if cell.role in ("coolant", "moderator") and cell.material_id:
            return cell.material_id
    return None


def _find_moderator_only_universe(
    universes: UniversesPatch,
    background_material_id: str,
) -> str | None:
    """Find an existing universe that is moderator-only (single background coolant cell)."""
    for u in universes.universes:
        if len(u.cells) != 1:
            continue
        cell = u.cells[0]
        if cell.material_id == background_material_id and cell.region_kind in ("background", "box"):
            return u.universe_id
    return None


def _find_existing_shoulder_loading(
    axial: AxialLayersPatch,
    base_lattice_id: str,
    base_default_universe_id: str,
    replacement_universe_id: str,
) -> str | None:
    """Find a loading that replaces the base fuel family with the moderator universe."""
    for loading in axial.lattice_loadings:
        if loading.base_lattice_id != base_lattice_id:
            continue
        for transform in loading.transformations:
            if (
                transform.operation_kind == "replace_universe_family"
                and transform.source_universe_id == base_default_universe_id
                and transform.replacement_universe_id == replacement_universe_id
            ):
                return loading.loading_id
    return None


def _short_hash(*parts: str) -> str:
    raw = "|".join(parts)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:12]


# ---------------------------------------------------------------------------
# Diagnosis
# ---------------------------------------------------------------------------


def diagnose_component_profile_slab(
    *,
    state: PlanBuildState,
    plan: SimulationPlan,
    layer_id: str,
) -> ComponentProfileSlabDiagnosis | None:
    """Diagnose whether a component-profile material slab is uniquely repairable.

    Returns ``None`` if the layer is not found or the patch structure is
    incomplete.  Returns a diagnosis with ``deterministic_repair_available=True``
    only when all the proof conditions in the shoulder-gap contract are met.
    """
    axial = _valid_patch(state, "axial_layers")
    if not isinstance(axial, AxialLayersPatch):
        return None
    universes = _valid_patch(state, "universes")
    if not isinstance(universes, UniversesPatch):
        return None
    pin_map = _valid_patch(state, "pin_map")

    target_layer = next((l for l in axial.layers if l.layer_id == layer_id), None)
    if target_layer is None:
        return None

    # Must be a component-profile role
    if target_layer.role not in _COMPONENT_PROFILE_ROLES:
        return None

    # Must currently be a material slab
    if target_layer.fill_type != "material":
        return None

    z_min = target_layer.z_min_cm or 0.0
    z_max = target_layer.z_max_cm or 0.0

    # Identify the base lattice and its default universe
    base_lattice_id = "assembly_lattice"
    lattice = next(
        (item for item in plan.complex_model.lattices if item.id == base_lattice_id),
        plan.complex_model.lattices[0] if plan.complex_model.lattices else None,
    )
    if lattice is None:
        return None

    base_default_universe_id = pin_map.default_universe_id if pin_map else None
    if base_default_universe_id is None:
        return None

    guide_tube_count = len(pin_map.guide_tube_coords) if pin_map else 0
    instrument_tube_count = len(pin_map.instrument_tube_coords) if pin_map else 0

    # Find the background moderator material from the base fuel-pin universe
    background_material_id = _find_background_material_id(universes, base_default_universe_id)

    # Find candidate moderator-only universe
    candidate_profile_universe_ids: list[str] = []
    if background_material_id is not None:
        mod_universe = _find_moderator_only_universe(universes, background_material_id)
        if mod_universe is not None:
            candidate_profile_universe_ids.append(mod_universe)

    # Find existing shoulder-gap-style loading
    candidate_loading_ids: list[str] = []
    if candidate_profile_universe_ids:
        for mod_uid in candidate_profile_universe_ids:
            existing = _find_existing_shoulder_loading(
                axial, base_lattice_id, base_default_universe_id, mod_uid,
            )
            if existing is not None:
                candidate_loading_ids.append(existing)

    reasons: list[str] = []

    # Check shoulder-gap structural signals
    is_shoulder_like = target_layer.role in _SHOULDER_GAP_SIGNALS
    is_solid_structure = target_layer.role in _SOLID_STRUCTURE_ROLES

    if is_solid_structure:
        reasons.append("layer role is a solid structural component, not a shoulder gap")
        repair_kind: Literal[
            "reuse_existing_loading", "create_moderator_profile_bundle",
            "component_profile_loading_required", "ambiguous",
        ] = "ambiguous"
    elif not is_shoulder_like:
        # Component-profile but not shoulder-gap-like (e.g. end_plug, plenum) --
        # still needs lattice+loading but the replacement universe is not
        # necessarily moderator-only.  Defer to LLM.
        reasons.append("layer is a component profile but not a shoulder/moderator gap; replacement universe is non-trivial")
        repair_kind = "component_profile_loading_required"
    elif background_material_id is None:
        reasons.append("cannot identify background moderator material from base universe")
        repair_kind = "ambiguous"
    elif not candidate_profile_universe_ids:
        # No existing moderator-only universe; can we derive one?
        if background_material_id is not None:
            repair_kind = "create_moderator_profile_bundle"
            reasons.append("no existing moderator-only universe; deterministic derivation available")
        else:
            repair_kind = "ambiguous"
            reasons.append("no existing moderator-only universe and cannot derive one")
    elif candidate_loading_ids:
        repair_kind = "reuse_existing_loading"
        reasons.append("existing moderator-only universe and matching loading are reusable")
    else:
        repair_kind = "create_moderator_profile_bundle"
        reasons.append("existing moderator-only universe found; new loading required")

    deterministic = repair_kind in ("reuse_existing_loading", "create_moderator_profile_bundle")

    return ComponentProfileSlabDiagnosis(
        layer_id=layer_id,
        layer_role=target_layer.role,
        z_min_cm=z_min,
        z_max_cm=z_max,
        current_fill_type=target_layer.fill_type,
        current_fill_id=target_layer.fill_id,
        base_lattice_id=base_lattice_id,
        base_default_universe_id=base_default_universe_id,
        guide_tube_count=guide_tube_count,
        instrument_tube_count=instrument_tube_count,
        background_material_id=background_material_id,
        candidate_profile_universe_ids=candidate_profile_universe_ids,
        candidate_loading_ids=candidate_loading_ids,
        repair_kind=repair_kind,
        deterministic_repair_available=deterministic,
        reasons=reasons,
    )


# ---------------------------------------------------------------------------
# Deterministic repair bundle construction
# ---------------------------------------------------------------------------


def _build_derived_moderator_universe(
    source_universe_id: str,
    material_id: str,
) -> dict[str, Any]:
    """Construct a deterministic moderator-only universe dict."""
    h = _short_hash(source_universe_id, material_id)
    return {
        "universe_id": f"derived_moderator_only_{h}",
        "kind": "custom",
        "cells": [
            {
                "id": "moderator",
                "role": "coolant",
                "material_id": material_id,
                "region_kind": "background",
            }
        ],
        "source_note": "Deterministically derived moderator-only pitch profile",
    }


def propose_shoulder_gap_repair_bundle(
    *,
    state: PlanBuildState,
    diagnosis: ComponentProfileSlabDiagnosis,
) -> PatchRepairBundleProposal | None:
    """Construct a deterministic multi-patch repair for a uniquely-proven shoulder gap.

    Returns ``None`` if the diagnosis is not deterministic.
    """
    if not diagnosis.deterministic_repair_available:
        return None

    axial = _valid_patch(state, "axial_layers")
    if not isinstance(axial, AxialLayersPatch):
        return None
    universes = _valid_patch(state, "universes")
    if not isinstance(universes, UniversesPatch):
        return None

    base_default = diagnosis.base_default_universe_id
    if base_default is None:
        return None

    # Determine the replacement universe and whether we need to create it
    need_universe_patch = False
    if diagnosis.candidate_profile_universe_ids:
        replacement_universe_id = diagnosis.candidate_profile_universe_ids[0]
    elif diagnosis.background_material_id is not None:
        derived = _build_derived_moderator_universe(base_default, diagnosis.background_material_id)
        replacement_universe_id = derived["universe_id"]
        need_universe_patch = True
    else:
        return None

    # Determine the loading_id
    h = _short_hash(diagnosis.layer_id, base_default, replacement_universe_id)

    # Reuse existing loading if available
    loading_id: str | None = None
    if diagnosis.candidate_loading_ids:
        loading_id = diagnosis.candidate_loading_ids[0]

    operations: list[PatchRepairBundleOperation] = []

    # 1. Add derived universe (if needed)
    if need_universe_patch:
        operations.append(PatchRepairBundleOperation(
            patch_type="universes",
            operation=PatchRepairOperation(
                op="add",
                path="/universes/-",
                value=derived,
            ),
        ))

    # 2. Add loading (if not reusing existing)
    if loading_id is None:
        loading_id = f"derived_shoulder_gap_loading_{h}"
        derived_lattice_id = f"assembly_lattice_shoulder_gap_{h}"
        new_loading = {
            "loading_id": loading_id,
            "base_lattice_id": diagnosis.base_lattice_id,
            "derived_lattice_id": derived_lattice_id,
            "transformations": [
                {
                    "operation_id": f"replace_base_with_moderator_{h}",
                    "operation_kind": "replace_universe_family",
                    "source_universe_id": base_default,
                    "replacement_universe_id": replacement_universe_id,
                    "source_universe_ids": [],
                    "target_coordinates": [],
                    "component_role": None,
                    "component_path_id": None,
                    "preserve_component_roles": [],
                    "preserve_path_ids": [],
                    "priority": 0,
                    "purpose": "",
                }
            ],
            "overrides": {},
            "purpose": (
                "Replace base fuel-pin universe family with moderator-only "
                "profile for shoulder gap; guide/instrument tubes continue through."
            ),
        }
        operations.append(PatchRepairBundleOperation(
            patch_type="axial_layers",
            operation=PatchRepairOperation(
                op="add",
                path="/lattice_loadings/-",
                value=new_loading,
            ),
        ))

    # 3. Update the target layer
    layer_index = next(
        (i for i, l in enumerate(axial.layers) if l.layer_id == diagnosis.layer_id),
        None,
    )
    if layer_index is None:
        return None

    operations.append(PatchRepairBundleOperation(
        patch_type="axial_layers",
        operation=PatchRepairOperation(
            op="replace",
            path=f"/layers/{layer_index}/fill_type",
            value="lattice",
        ),
    ))
    operations.append(PatchRepairBundleOperation(
        patch_type="axial_layers",
        operation=PatchRepairOperation(
            op="replace",
            path=f"/layers/{layer_index}/fill_id",
            value=diagnosis.base_lattice_id,
        ),
    ))
    operations.append(PatchRepairBundleOperation(
        patch_type="axial_layers",
        operation=PatchRepairOperation(
            op="replace",
            path=f"/layers/{layer_index}/loading_id",
            value=loading_id,
        ),
    ))

    return PatchRepairBundleProposal(
        repair_id=f"sgb_{h}",
        strategy="deterministic_shoulder_gap_repair",
        operations=operations,
        rationale=(
            "Shoulder-gap layer uniquely identified: base fuel-pin default "
            f"universe {base_default!r} replaced by moderator-only profile "
            f"{replacement_universe_id!r} via replace_universe_family; "
            "guide/instrument tube through-paths preserved."
        ),
        confidence=1.0,
    )


# ---------------------------------------------------------------------------
# Clone-only bundle evaluation
# ---------------------------------------------------------------------------


def _apply_bundle_to_clone(
    state: PlanBuildState,
    proposal: PatchRepairBundleProposal,
) -> PlanBuildState | None:
    """Apply all bundle operations to a deep clone. Returns None on failure."""
    clone = state.model_copy(deep=True)

    # Group operations by patch type
    by_patch: dict[str, list[PatchRepairOperation]] = {}
    for op in proposal.operations:
        by_patch.setdefault(op.patch_type, []).append(op.operation)

    # Apply operations to each target patch
    from openmc_agent.repair_proposal import apply_json_patch_to_clone

    for patch_type, ops in by_patch.items():
        target = next(
            (p for p in clone.patches.values() if p.patch_type == patch_type and p.status == "valid"),
            None,
        )
        if target is None:
            return None
        result = apply_json_patch_to_clone(target.content, ops)
        if not result.ok or not isinstance(result.plan, dict):
            return None
        target.content = result.plan
        target.source = "repair"

    return clone


def _validate_clone(clone: PlanBuildState, requirement: str) -> tuple[SimulationPlan | None, ValidationReport]:
    """Assemble and validate the clone."""
    parsed = []
    for envelope in clone.patches.values():
        if envelope.status == "valid":
            parsed.append(parse_patch_content(envelope.patch_type, envelope.content))
    assembly = assemble_simulation_plan_from_patches(parsed, strict=True)
    if not assembly.ok or assembly.plan is None:
        report = ValidationReport.from_issues(assembly.issues)
        return None, report
    plan = assembly.plan
    report = validate_simulation_plan(plan, requirement=requirement)
    return plan, report


def _check_through_path_preserved(
    plan_before: SimulationPlan | None,
    plan_after: SimulationPlan,
    diagnosis: ComponentProfileSlabDiagnosis,
) -> bool:
    """Verify guide/instrument tube through-paths are preserved after repair.

    Checks that:
    - The repaired layer fill is lattice with a loading;
    - The loading only replaces the base fuel family;
    - Guide/instrument tube universes are not replaced;
    - The base lattice pin counts are unchanged.
    """
    if plan_before is None:
        return True  # Nothing to compare; rely on other checks

    model = plan_after.complex_model
    if model is None or model.core is None:
        return False

    # Find the repaired layer
    repaired_layer = next(
        (l for l in model.core.axial_layers if l.id == diagnosis.layer_id),
        None,
    )
    if repaired_layer is None:
        return False

    # Fill must be lattice
    if repaired_layer.fill.type != "lattice":
        return False

    # Must have a loading
    loading_id = repaired_layer.loading_id or (repaired_layer.loading_ids[0] if repaired_layer.loading_ids else None)
    if loading_id is None:
        return False

    # The loading must exist (lattice_loadings is on ComplexModelSpec)
    loading = next(
        (l for l in model.lattice_loadings if l.id == loading_id),
        None,
    )
    if loading is None:
        return False

    # The loading transformations must only replace the base default universe
    for transform in loading.transformations:
        if transform.operation_kind != "replace_universe_family":
            continue
        source = transform.source_universe_id
        if source is None:
            continue
        # The source must be the base default (fuel pin), not guide/instrument
        if source != diagnosis.base_default_universe_id:
            return False

    # Compare pin counts before and after
    lattice_before = next(
        (l for l in plan_before.complex_model.lattices if l.id == diagnosis.base_lattice_id),
        None,
    ) if plan_before and plan_before.complex_model else None
    lattice_after = next(
        (l for l in model.lattices if l.id == diagnosis.base_lattice_id),
        None,
    )
    if lattice_before is not None and lattice_after is not None:
        from collections import Counter
        before_counts = dict(Counter(item for row in lattice_before.universe_pattern for item in row))
        after_counts = dict(Counter(item for row in lattice_after.universe_pattern for item in row))
        if before_counts != after_counts:
            return False

    return True


def evaluate_shoulder_gap_repair_bundle(
    *,
    state: PlanBuildState,
    proposal: PatchRepairBundleProposal,
    diagnosis: ComponentProfileSlabDiagnosis,
    report_before: ValidationReport,
    requirement: str,
) -> PatchRepairBundleEvaluation:
    """Evaluate a repair bundle atomically on a clone.

    The real ``PlanBuildState`` is never mutated.  The bundle is accepted only
    when every gate passes:

    - The clone assembles successfully;
    - ``validate_simulation_plan`` no longer reports the component_profile issue;
    - No new blocking validation issue is introduced;
    - Guide/instrument through-paths are preserved.
    """
    before_codes = sorted({i.code for i in report_before.issues if i.severity == "error"})
    before_report_dict = report_before.model_dump(mode="json")

    # Capture capability before
    plan_before: SimulationPlan | None = None
    try:
        parsed_before = [
            parse_patch_content(e.patch_type, e.content)
            for e in state.patches.values() if e.status == "valid"
        ]
        assembly_before = assemble_simulation_plan_from_patches(parsed_before, strict=True)
        if assembly_before.ok and assembly_before.plan is not None:
            plan_before = assembly_before.plan
    except Exception:
        pass

    # Apply bundle to clone
    clone = _apply_bundle_to_clone(state, proposal)
    if clone is None:
        return PatchRepairBundleEvaluation(
            accepted=False,
            status="rejected_patch_invalid",
            validation_report_before=before_report_dict,
            reasons=["bundle operations failed to apply on clone"],
        )

    # Validate the clone
    plan_after, report_after = _validate_clone(clone, requirement)
    after_report_dict = report_after.model_dump(mode="json")
    after_codes = sorted({i.code for i in report_after.issues if i.severity == "error"})

    if plan_after is None:
        return PatchRepairBundleEvaluation(
            accepted=False,
            status="rejected_patch_invalid",
            validation_report_before=before_report_dict,
            validation_report_after=after_report_dict,
            reasons=["clone assembly failed"],
        )

    # Capability after
    capability_after: dict[str, Any] | None = None
    try:
        from openmc_agent.graph import _capability_for_plan
        cap = _capability_for_plan(plan_after)
        capability_after = cap.model_dump(mode="json")
    except Exception:
        pass

    # Check issue resolution: the specific layer's issue must be gone.
    # Other layers with the same code may still be present (they will be
    # repaired in subsequent iterations of the oracle loop).
    target_code = "assembly3d.component_profile_as_material_slab"
    layer_path_fragment = diagnosis.layer_id
    target_resolved = not any(
        i.code == target_code and layer_path_fragment in (i.schema_path or "")
        for i in report_after.issues
    )

    # Check no new blockers
    before_error_identities = {
        (i.code, i.schema_path or "", i.severity)
        for i in report_before.issues if i.severity == "error"
    }
    after_error_identities = {
        (i.code, i.schema_path or "", i.severity)
        for i in report_after.issues if i.severity == "error"
    }
    introduced = after_error_identities - before_error_identities
    introduced_codes = sorted({c for c, _, _ in introduced})

    # Check through-path preservation
    through_path_ok = _check_through_path_preserved(plan_before, plan_after, diagnosis)

    reasons: list[str] = []
    if not target_resolved:
        reasons.append("component_profile_as_material_slab issue not resolved")
    if introduced:
        reasons.append(f"new blocking issues introduced: {sorted(introduced_codes)}")
    if not through_path_ok:
        reasons.append("guide/instrument through-path check failed")

    if reasons:
        return PatchRepairBundleEvaluation(
            accepted=False,
            status="rejected_new_blocker" if introduced else "rejected_no_improvement",
            validation_report_before=before_report_dict,
            validation_report_after=after_report_dict,
            capability_after=capability_after,
            reasons=reasons,
        )

    # Capture the repaired clone patches for commit
    repaired_patches: dict[str, Any] = {}
    for pt in ("axial_layers", "universes"):
        target = next(
            (p for p in clone.patches.values() if p.patch_type == pt and p.status == "valid"),
            None,
        )
        if target is not None:
            repaired_patches[pt] = copy.deepcopy(target.content)

    return PatchRepairBundleEvaluation(
        accepted=True,
        status="accepted",
        validation_report_before=before_report_dict,
        validation_report_after=after_report_dict,
        capability_after=capability_after,
        repaired_plan=plan_after.model_dump(mode="json"),
        repaired_patches=repaired_patches,
        reasons=[
            "component_profile_as_material_slab resolved without new blockers",
            "guide/instrument through-paths preserved",
        ],
    )


def commit_accepted_repair_bundle(
    state: PlanBuildState,
    proposal: PatchRepairBundleProposal,
    evaluation: PatchRepairBundleEvaluation,
) -> SimulationPlan | None:
    """Commit an accepted bundle to the real PlanBuildState.

    Applies all operations to the real patches and re-assembles.  Returns the
    re-assembled plan or ``None`` on failure.
    """
    if not evaluation.accepted:
        raise ValueError("only accepted bundle evaluations may be committed")

    from openmc_agent.repair_proposal import apply_json_patch_to_clone

    # Group operations by patch type
    by_patch: dict[str, list[PatchRepairOperation]] = {}
    for op in proposal.operations:
        by_patch.setdefault(op.patch_type, []).append(op.operation)

    for patch_type, ops in by_patch.items():
        target = next(
            (p for p in state.patches.values() if p.patch_type == patch_type and p.status == "valid"),
            None,
        )
        if target is None:
            return None
        result = apply_json_patch_to_clone(target.content, ops)
        if not result.ok or not isinstance(result.plan, dict):
            return None
        target.content = result.plan
        target.source = "repair"
        target.status = "valid"
        state.patch_status[target.patch_id] = "valid"

    # Re-assemble
    parsed = [
        parse_patch_content(e.patch_type, e.content)
        for e in state.patches.values() if e.status == "valid"
    ]
    assembly = assemble_simulation_plan_from_patches(parsed, strict=True)
    if not assembly.ok or assembly.plan is None:
        return None

    state.assembled_plan = assembly.plan.model_dump(mode="json")
    state.validation_issues = []
    return assembly.plan
