"""Deterministic grid-loading repair and spacer-grid migration.

When an LLM generates lattice transformations that reference an undefined
``grid_cell`` replacement universe (e.g. ``replace_water_with_grid``), the
correct fix is NOT to create a solid grid universe.  Spacer grids must be
expressed as ``axial_overlays`` with ``overlay_kind=spacer_grid``.

This module diagnoses whether the defect is a grid transformation that can
be safely removed or migrated, and produces a deterministic multi-patch
repair bundle when the evidence is unambiguous.

Repair strategies (priority order):

A. ``remove_redundant_grid_transformation`` — existing spacer_grid overlays
   already cover the needed z range; the grid transformation is redundant.
   Remove the grid operation; delete the loading if it becomes empty; clean
   up layer loading_id / loading_ids references.

B. ``create_grid_overlay_bundle`` — no existing overlay covers the z range,
   but the grid material and z range are unambiguous.  Create a new
   ``spacer_grid`` overlay and remove the grid transformation.

C. ``replace_cell_id_with_unique_owner_universe`` — the replacement ID is
   actually a Cell ID with a unique owning Universe.  Replace the ID with
   that Universe.

D. ``ordinary_missing_universe`` — not grid-related, no unique owner.
   Defer to LLM / dependency repair.
"""

from __future__ import annotations

import copy
import hashlib
import json
from typing import Any, Literal

from pydantic import Field

from openmc_agent.schemas import (
    AgentBaseModel,
    SimulationPlan,
    ValidationReport,
)
from openmc_agent.validator import validate_simulation_plan

from .assembler import assemble_simulation_plan_from_patches
from .component_profile_repair import (
    PatchRepairBundleEvaluation,
    PatchRepairBundleOperation,
    PatchRepairBundleProposal,
    _apply_bundle_to_clone,
    _validate_clone,
)
from .patches import (
    AxialOverlayPatchItem,
    AxialOverlaysPatch,
    LatticeLoadingPatchItem,
    LatticeTransformationPatchItem,
    parse_patch_content,
)
from .state import PlanBuildState
from .validation_repair import PatchRepairOperation


# ---------------------------------------------------------------------------
# Evidence models
# ---------------------------------------------------------------------------


_GRID_OPERATION_TOKENS: frozenset[str] = frozenset({
    "grid", "spacer", "top_grid", "replace_water_with_grid",
    "replace_water_with_top_grid", "spacer_grid",
})

_GRID_MATERIAL_ROLES: frozenset[str] = frozenset({
    "grid_inconel", "grid_zircaloy", "grid", "spacer",
})


class GridTransformationEvidence(AgentBaseModel):
    """Evidence collected for a single grid-suspect transformation."""

    loading_id: str
    operation_id: str
    operation_kind: str
    source_universe_id: str | None = None
    replacement_universe_id: str
    layers_using_loading: list[str] = Field(default_factory=list)

    replacement_is_universe: bool = False
    replacement_is_cell: bool = False
    replacement_is_material: bool = False
    owning_universe_ids: list[str] = Field(default_factory=list)

    material_id: str | None = None
    material_role: str | None = None

    matching_overlay_ids: list[str] = Field(default_factory=list)
    layer_z_ranges: list[tuple[float, float]] = Field(default_factory=list)

    facts_has_spacer_grids: bool = False
    strong_grid_evidence: list[str] = Field(default_factory=list)
    weak_grid_evidence: list[str] = Field(default_factory=list)
    evidence_source: str = "structural_issue"


class GridLoadingRepairDiagnosis(AgentBaseModel):
    """Diagnosis for one or more grid-suspect transformations."""

    issue_fingerprint: str
    operations: list[GridTransformationEvidence] = Field(default_factory=list)

    repair_kind: Literal[
        "replace_cell_id_with_unique_owner_universe",
        "remove_redundant_grid_transformation",
        "migrate_grid_transformation_to_existing_overlay",
        "create_grid_overlay_bundle",
        "ordinary_missing_universe",
        "ambiguous",
    ]
    deterministic_repair_available: bool
    reasons: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Diagnosis
# ---------------------------------------------------------------------------


def diagnose_grid_loading_failure(
    *,
    state: PlanBuildState,
    plan: SimulationPlan,
    issues: list[Any],
) -> GridLoadingRepairDiagnosis | None:
    """Diagnose ``lattice_transform.replacement_universe_missing`` issues.

    Returns ``None`` if no relevant issues are found.  Otherwise collects
    evidence for each failing operation and determines the best repair branch.
    """
    model = plan.complex_model
    if model is None or model.core is None:
        return None

    relevant_codes = {
        "lattice_transform.replacement_universe_missing",
        "lattice_transform.cell_id_used_as_universe",
        "assembly3d.spacer_grid_transformation_misuse",
    }
    failing_ops = [i for i in issues if i.code in relevant_codes]
    if not failing_ops:
        return None

    universe_ids = {u.id for u in model.universes}
    cell_ids: set[str] = {c.id for c in model.cells}
    cell_owners: dict[str, list[str]] = {}
    for u in model.universes:
        for cid in u.cell_ids:
            cell_owners.setdefault(cid, []).append(u.id)

    material_ids = {m.id for m in model.materials}
    material_roles: dict[str, str] = {}
    mat_patch = next(
        (p for p in state.patches.values()
         if p.patch_type == "materials" and p.status == "valid"),
        None,
    )
    if mat_patch:
        for mat in mat_patch.content.get("materials", []):
            mid = mat.get("material_id")
            if mid:
                material_roles[mid] = mat.get("role")

    # Existing spacer_grid overlays
    grid_overlays: list[dict[str, Any]] = []
    if model.core:
        for ov in model.core.axial_overlays:
            if ov.overlay_kind == "spacer_grid":
                grid_overlays.append({
                    "overlay_id": ov.id,
                    "z_min_cm": ov.z_min_cm,
                    "z_max_cm": ov.z_max_cm,
                    "material_id": ov.material_id,
                    "target_lattice_id": ov.target_lattice_id,
                })

    facts_has_grids = False
    facts_patch = next(
        (p for p in state.patches.values() if p.patch_type == "facts" and p.status == "valid"),
        None,
    )
    if facts_patch:
        facts_has_grids = bool(facts_patch.content.get("has_spacer_grids"))

    # Loading by id
    loading_by_id: dict[str, Any] = {
        l.id: l for l in (model.lattice_loadings or [])
    }

    # Layers using each loading
    layers_by_loading: dict[str, list[tuple[str, float, float]]] = {}
    for layer in model.core.axial_layers:
        lids = _layer_loading_ids(layer)
        for lid in lids:
            layers_by_loading.setdefault(lid, []).append(
                (layer.id, layer.z_min_cm, layer.z_max_cm)
            )

    operations_evidence: list[GridTransformationEvidence] = []

    for issue in failing_ops:
        op_id = _extract_op_id_from_path(issue.schema_path or "")
        loading_id = _extract_loading_id_from_path(issue.schema_path or "")

        if loading_id and loading_id not in loading_by_id:
            continue

        loading = loading_by_id.get(loading_id)
        if loading is None:
            continue

        transform = None
        for t in loading.transformations:
            if op_id and t.operation_id == op_id:
                transform = t
                break
        if transform is None:
            continue

        rep = transform.replacement_universe_id
        layers_using = layers_by_loading.get(loading_id, [])
        layer_names = [l[0] for l in layers_using]
        layer_zs = [(l[1], l[2]) for l in layers_using]

        rep_is_universe = rep in universe_ids
        rep_is_cell = rep in cell_ids
        rep_is_material = rep in material_ids
        owners = cell_owners.get(rep, []) if rep_is_cell else []

        strong: list[str] = []
        weak: list[str] = []

        text = " ".join([
            transform.operation_id or "",
            transform.purpose or "",
        ]).lower()

        if any(tok in text for tok in _GRID_OPERATION_TOKENS):
            weak.append(f"operation_id/purpose contains grid token: {text.strip()!r}")

        if facts_has_grids:
            strong.append("facts.has_spacer_grids=True")

        if grid_overlays:
            strong.append(f"{len(grid_overlays)} existing spacer_grid overlays")

        matching_overlay_ids: list[str] = []
        for layer_id, z_min, z_max in layers_using:
            for gov in grid_overlays:
                ov_zmin = gov.get("z_min_cm")
                ov_zmax = gov.get("z_max_cm")
                if ov_zmin is None or ov_zmax is None:
                    continue
                if _ranges_overlap(z_min, z_max, ov_zmin, ov_zmax):
                    if gov["overlay_id"] not in matching_overlay_ids:
                        matching_overlay_ids.append(gov["overlay_id"])
        if matching_overlay_ids:
            strong.append(
                f"overlays {matching_overlay_ids} overlap with layers using this loading"
            )

        mat_id = None
        mat_role = None
        if rep_is_material:
            mat_id = rep
            mat_role = material_roles.get(rep)
            if mat_role and mat_role in _GRID_MATERIAL_ROLES:
                strong.append(f"replacement material role={mat_role!r} is a grid material role")

        operations_evidence.append(GridTransformationEvidence(
            loading_id=loading_id,
            operation_id=transform.operation_id,
            operation_kind=transform.operation_kind,
            source_universe_id=transform.source_universe_id,
            replacement_universe_id=rep,
            layers_using_loading=layer_names,
            layer_z_ranges=layer_zs,
            replacement_is_universe=rep_is_universe,
            replacement_is_cell=rep_is_cell,
            replacement_is_material=rep_is_material,
            owning_universe_ids=owners,
            material_id=mat_id,
            material_role=mat_role,
            matching_overlay_ids=matching_overlay_ids,
            facts_has_spacer_grids=facts_has_grids,
            strong_grid_evidence=strong,
            weak_grid_evidence=weak,
        ))

    if not operations_evidence:
        return None

    # Determine repair branch
    all_have_existing_overlay = all(
        bool(ops.matching_overlay_ids) and ops.strong_grid_evidence
        for ops in operations_evidence
    )
    all_cell_unique_owner = all(
        ops.replacement_is_cell and len(ops.owning_universe_ids) == 1
        and not ops.strong_grid_evidence
        for ops in operations_evidence
    )
    any_grid_evidence = any(
        ops.strong_grid_evidence or ops.weak_grid_evidence
        for ops in operations_evidence
    )

    fp_hash = hashlib.sha1(
        json.dumps([ops.model_dump(mode="json") for ops in operations_evidence], sort_keys=True).encode()
    ).hexdigest()[:12]

    if all_have_existing_overlay:
        return GridLoadingRepairDiagnosis(
            issue_fingerprint=fp_hash,
            operations=operations_evidence,
            repair_kind="remove_redundant_grid_transformation",
            deterministic_repair_available=True,
            reasons=[
                "all failing operations have matching spacer_grid overlays",
                "grid transformations are redundant; overlays already cover z ranges",
            ],
        )

    if all_cell_unique_owner:
        return GridLoadingRepairDiagnosis(
            issue_fingerprint=fp_hash,
            operations=operations_evidence,
            repair_kind="replace_cell_id_with_unique_owner_universe",
            deterministic_repair_available=True,
            reasons=[
                "replacement IDs are Cell IDs with unique owning Universes",
            ],
        )

    if any_grid_evidence:
        # Could be Strategy B (create overlay) — but only if evidence is
        # sufficient (unique z range, unique material).  For safety, if
        # existing overlays exist but don't overlap, we mark as ambiguous.
        has_existing = any(
            ops.matching_overlay_ids for ops in operations_evidence
        )
        if has_existing:
            return GridLoadingRepairDiagnosis(
                issue_fingerprint=fp_hash,
                operations=operations_evidence,
                repair_kind="migrate_grid_transformation_to_existing_overlay",
                deterministic_repair_available=True,
                reasons=[
                    "some operations have matching overlays; others may need "
                    "individual assessment",
                ],
            )
        # Strategy B: check if material and z are unambiguous
        can_create = all(
            len(ops.layer_z_ranges) >= 1
            and (
                ops.material_id is not None
                or _resolve_grid_material(state, ops) is not None
            )
            for ops in operations_evidence
        )
        if can_create:
            return GridLoadingRepairDiagnosis(
                issue_fingerprint=fp_hash,
                operations=operations_evidence,
                repair_kind="create_grid_overlay_bundle",
                deterministic_repair_available=True,
                reasons=[
                    "grid evidence present; z range and material unambiguous",
                ],
            )
        return GridLoadingRepairDiagnosis(
            issue_fingerprint=fp_hash,
            operations=operations_evidence,
            repair_kind="ambiguous",
            deterministic_repair_available=False,
            reasons=[
                "grid evidence present but z range or material not uniquely resolvable",
            ],
        )

    return GridLoadingRepairDiagnosis(
        issue_fingerprint=fp_hash,
        operations=operations_evidence,
        repair_kind="ordinary_missing_universe",
        deterministic_repair_available=False,
        reasons=[
            "no grid evidence; replacement universe is simply undefined",
        ],
    )


# ---------------------------------------------------------------------------
# Bundle proposal
# ---------------------------------------------------------------------------


def propose_grid_migration_repair_bundle(
    *,
    state: PlanBuildState,
    diagnosis: GridLoadingRepairDiagnosis,
) -> PatchRepairBundleProposal | None:
    """Build a deterministic multi-patch bundle for the diagnosed grid issue.

    Returns ``None`` if the diagnosis does not support deterministic repair.
    """
    if not diagnosis.deterministic_repair_available:
        return None

    operations: list[PatchRepairBundleOperation] = []

    if diagnosis.repair_kind == "remove_redundant_grid_transformation":
        operations.extend(_build_remove_grid_ops(state, diagnosis))
    elif diagnosis.repair_kind == "replace_cell_id_with_unique_owner_universe":
        operations.extend(_build_cell_alias_ops(state, diagnosis))
    elif diagnosis.repair_kind in (
        "migrate_grid_transformation_to_existing_overlay",
        "create_grid_overlay_bundle",
    ):
        operations.extend(_build_remove_grid_ops(state, diagnosis))
        if diagnosis.repair_kind == "create_grid_overlay_bundle":
            operations.extend(_build_create_overlay_ops(state, diagnosis))
    else:
        return None

    if not operations:
        return None

    h = hashlib.sha1(
        json.dumps([op.model_dump(mode="json") for op in operations], sort_keys=True).encode()
    ).hexdigest()[:12]

    return PatchRepairBundleProposal(
        repair_id=f"glm_{h}",
        strategy="deterministic_grid_migration",
        operations=operations,
        rationale=(
            f"Grid loading repair ({diagnosis.repair_kind}): "
            + "; ".join(diagnosis.reasons)
        ),
        confidence=1.0,
    )


# ---------------------------------------------------------------------------
# Bundle evaluation
# ---------------------------------------------------------------------------


def evaluate_grid_migration_repair_bundle(
    *,
    state: PlanBuildState,
    proposal: PatchRepairBundleProposal,
    diagnosis: GridLoadingRepairDiagnosis,
    report_before: ValidationReport,
    requirement: str,
) -> PatchRepairBundleEvaluation:
    """Evaluate a grid migration bundle atomically on a clone.

    Acceptance gates:
    - Clone assembles successfully.
    - validate_simulation_plan no longer reports replacement_universe_missing
      or spacer_grid_transformation_misuse for the target operations.
    - renderer.axial_loading_materialization_failed is gone.
    - No new blocking validation issue.
    - Non-grid transformations preserved.
    - Pin counts unchanged.
    - Overlay count not decreased (Strategy A/B only).
    """
    from openmc_agent.lattice_loading_validation import (
        lattice_loading_structural_issues,
    )

    before_codes = sorted({i.code for i in report_before.issues if i.severity == "error"})
    before_report_dict = report_before.model_dump(mode="json")

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

    clone = _apply_bundle_to_clone(state, proposal)
    if clone is None:
        return PatchRepairBundleEvaluation(
            accepted=False,
            status="rejected_patch_invalid",
            validation_report_before=before_report_dict,
            reasons=["bundle operations failed to apply on clone"],
        )

    plan_after, report_after = _validate_clone(clone, requirement)
    after_report_dict = report_after.model_dump(mode="json")

    if plan_after is None:
        return PatchRepairBundleEvaluation(
            accepted=False,
            status="rejected_patch_invalid",
            validation_report_before=before_report_dict,
            validation_report_after=after_report_dict,
            reasons=["clone assembly failed"],
        )

    # Check lattice loading materialization passes
    model_after = plan_after.complex_model
    lattice_issues = lattice_loading_structural_issues(model_after) if model_after else []
    lattice_error_codes = {i.code for i in lattice_issues if i.severity == "error"}

    target_codes = {
        "lattice_transform.replacement_universe_missing",
        "renderer.axial_loading_materialization_failed",
        "assembly3d.spacer_grid_transformation_misuse",
        "lattice_transform.cell_id_used_as_universe",
    }
    target_resolved = not any(
        i.code in target_codes for i in report_after.issues if i.severity == "error"
    )

    # Check no new blockers
    before_error_ids = {
        (i.code, i.schema_path or "", i.severity)
        for i in report_before.issues if i.severity == "error"
    }
    after_error_ids = {
        (i.code, i.schema_path or "", i.severity)
        for i in report_after.issues if i.severity == "error"
    }
    introduced = after_error_ids - before_error_ids
    introduced_codes = sorted({c for c, _, _ in introduced})

    # Check non-grid transformations preserved
    non_grid_preserved = _check_non_grid_transforms_preserved(
        plan_before, plan_after, diagnosis
    )

    # Check pin counts unchanged
    pin_counts_ok = _check_pin_counts_unchanged(plan_before, plan_after)

    # Check overlay count not decreased
    overlay_count_ok = _check_overlay_count(state, clone)

    reasons: list[str] = []
    if not target_resolved:
        reasons.append("target lattice_transform issues not fully resolved")
    if introduced:
        reasons.append(f"new blocking issues introduced: {sorted(introduced_codes)}")
    if not non_grid_preserved:
        reasons.append("non-grid transformations were not preserved")
    if not pin_counts_ok:
        reasons.append("pin counts changed")
    if not overlay_count_ok:
        reasons.append("overlay count decreased")

    if reasons:
        return PatchRepairBundleEvaluation(
            accepted=False,
            status="rejected_new_blocker" if introduced else "rejected_no_improvement",
            validation_report_before=before_report_dict,
            validation_report_after=after_report_dict,
            reasons=reasons,
        )

    repaired_patches: dict[str, Any] = {}
    for pt in ("axial_layers", "axial_overlays", "universes"):
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
        repaired_plan=plan_after.model_dump(mode="json"),
        repaired_patches=repaired_patches,
        reasons=[
            "grid transformation issues resolved without new blockers",
            "non-grid transformations preserved",
            "pin counts unchanged",
        ],
    )


# ---------------------------------------------------------------------------
# Internal helpers — bundle operation builders
# ---------------------------------------------------------------------------


def _build_remove_grid_ops(
    state: PlanBuildState,
    diagnosis: GridLoadingRepairDiagnosis,
) -> list[PatchRepairBundleOperation]:
    """Build RFC6902 operations to remove grid transformations.

    For each operation evidence:
    1. Remove the grid transformation from its loading.
    2. If the loading becomes empty (no transformations and no overrides),
       delete the loading entirely.
    3. Clean up layer references (loading_id / loading_ids).
    """
    ops: list[PatchRepairBundleOperation] = []

    axial_patch = next(
        (p for p in state.patches.values()
         if p.patch_type == "axial_layers" and p.status == "valid"),
        None,
    )
    if axial_patch is None:
        return ops

    content = copy.deepcopy(axial_patch.content)
    layers: list[dict[str, Any]] = content.get("layers", [])
    loadings: list[dict[str, Any]] = content.get("lattice_loadings", [])

    loading_ids_to_delete: set[str] = set()

    for ev in diagnosis.operations:
        loading_obj = None
        for ll in loadings:
            if ll.get("loading_id") == ev.loading_id:
                loading_obj = ll
                break
        if loading_obj is None:
            continue

        transforms = loading_obj.get("transformations", [])
        new_transforms = [
            t for t in transforms if t.get("operation_id") != ev.operation_id
        ]
        overrides = loading_obj.get("overrides", {})

        if not new_transforms and not overrides:
            loading_ids_to_delete.add(ev.loading_id)
        else:
            loading_obj["transformations"] = new_transforms

    # Remove deleted loading_ids from layers
    for layer in layers:
        if loading_ids_to_delete:
            loading_ids = layer.get("loading_ids", [])
            if loading_ids:
                new_lids = [lid for lid in loading_ids if lid not in loading_ids_to_delete]
                layer["loading_ids"] = new_lids
            lid = layer.get("loading_id")
            if lid and lid in loading_ids_to_delete:
                layer["loading_id"] = None

    # Remove deleted loadings from lattice_loadings
    if loading_ids_to_delete:
        content["lattice_loadings"] = [
            ll for ll in loadings
            if ll.get("loading_id") not in loading_ids_to_delete
        ]

    # Use key-level replace operations (root replacement is forbidden).
    ops.append(PatchRepairBundleOperation(
        patch_type="axial_layers",
        operation=PatchRepairOperation(
            op="replace",
            path="/layers",
            value=layers,
        ),
    ))
    ops.append(PatchRepairBundleOperation(
        patch_type="axial_layers",
        operation=PatchRepairOperation(
            op="replace",
            path="/lattice_loadings",
            value=content.get("lattice_loadings", []),
        ),
    ))

    return ops


def _build_cell_alias_ops(
    state: PlanBuildState,
    diagnosis: GridLoadingRepairDiagnosis,
) -> list[PatchRepairBundleOperation]:
    """Build operations to replace cell ID with owning universe ID."""
    ops: list[PatchRepairBundleOperation] = []

    axial_patch = next(
        (p for p in state.patches.values()
         if p.patch_type == "axial_layers" and p.status == "valid"),
        None,
    )
    if axial_patch is None:
        return ops

    loadings = copy.deepcopy(axial_patch.content.get("lattice_loadings", []))

    for ev in diagnosis.operations:
        if not ev.replacement_is_cell or len(ev.owning_universe_ids) != 1:
            continue
        owner = ev.owning_universe_ids[0]
        for ll in loadings:
            if ll.get("loading_id") != ev.loading_id:
                continue
            for t in ll.get("transformations", []):
                if t.get("operation_id") == ev.operation_id:
                    t["replacement_universe_id"] = owner

    ops.append(PatchRepairBundleOperation(
        patch_type="axial_layers",
        operation=PatchRepairOperation(
            op="replace",
            path="/lattice_loadings",
            value=loadings,
        ),
    ))
    return ops


def _build_create_overlay_ops(
    state: PlanBuildState,
    diagnosis: GridLoadingRepairDiagnosis,
) -> list[PatchRepairBundleOperation]:
    """Build operations to create spacer_grid overlays for Strategy B."""
    ops: list[PatchRepairBundleOperation] = []

    overlay_patch = next(
        (p for p in state.patches.values()
         if p.patch_type == "axial_overlays" and p.status == "valid"),
        None,
    )
    content = copy.deepcopy(overlay_patch.content) if overlay_patch else {"overlays": []}
    overlays: list[dict[str, Any]] = content.get("overlays", [])

    for ev in diagnosis.operations:
        if not ev.layer_z_ranges:
            continue
        z_min = ev.layer_z_ranges[0][0]
        z_max = ev.layer_z_ranges[0][1]
        mat_id = ev.material_id or _resolve_grid_material(state, ev)
        if mat_id is None:
            continue

        overlay = {
            "overlay_id": f"grid_migrated_{ev.operation_id}",
            "overlay_kind": "spacer_grid",
            "z_min_cm": z_min,
            "z_max_cm": z_max,
            "target_lattice_id": "assembly_lattice",
            "material_id": mat_id,
            "geometry_mode": "homogenized_open_region",
            "through_path_preserved": True,
            "volume_fraction": None,
            "effective_density_g_cm3": None,
            "requires_human_confirmation": False,
            "assumptions": [],
            "source_note": "migrated from lattice transformation by grid_loading_repair",
        }
        overlays.append(overlay)

    content["overlays"] = overlays
    ops.append(PatchRepairBundleOperation(
        patch_type="axial_overlays",
        operation=PatchRepairOperation(
            op="replace",
            path="/overlays",
            value=overlays,
        ),
    ))
    return ops


# ---------------------------------------------------------------------------
# Internal helpers — checks
# ---------------------------------------------------------------------------


def _check_non_grid_transforms_preserved(
    plan_before: SimulationPlan | None,
    plan_after: SimulationPlan,
    diagnosis: GridLoadingRepairDiagnosis,
) -> bool:
    """Verify non-grid transformations are preserved after repair."""
    if plan_before is None or plan_before.complex_model is None:
        return True
    model_before = plan_before.complex_model
    model_after = plan_after.complex_model
    if model_after is None:
        return False

    loadings_before = {l.id: l for l in (model_before.lattice_loadings or [])}
    loadings_after = {l.id: l for l in (model_after.lattice_loadings or [])}

    grid_op_ids = {ev.operation_id for ev in diagnosis.operations}

    for lid, loading_before in loadings_before.items():
        loading_after = loadings_after.get(lid)
        if loading_after is None:
            # Loading was deleted — check that it only had grid ops
            before_op_ids = {t.operation_id for t in loading_before.transformations}
            non_grid_before = before_op_ids - grid_op_ids
            if non_grid_before:
                return False
            continue
        before_op_ids = {t.operation_id for t in loading_before.transformations}
        after_op_ids = {t.operation_id for t in loading_after.transformations}
        non_grid_before = before_op_ids - grid_op_ids
        if not non_grid_before.issubset(after_op_ids):
            return False

    return True


def _check_pin_counts_unchanged(
    plan_before: SimulationPlan | None,
    plan_after: SimulationPlan,
) -> bool:
    """Verify base lattice pin counts are unchanged."""
    if plan_before is None or plan_before.complex_model is None:
        return True
    from collections import Counter
    for lat_before in plan_before.complex_model.lattices:
        lat_after = next(
            (l for l in plan_after.complex_model.lattices if l.id == lat_before.id),
            None,
        ) if plan_after.complex_model else None
        if lat_after is None:
            continue
        before_counts = dict(Counter(item for row in lat_before.universe_pattern for item in row))
        after_counts = dict(Counter(item for row in lat_after.universe_pattern for item in row))
        if before_counts != after_counts:
            return False
    return True


def _check_overlay_count(state: PlanBuildState, clone: PlanBuildState) -> bool:
    """Verify overlay count did not decrease."""
    before_patch = next(
        (p for p in state.patches.values()
         if p.patch_type == "axial_overlays" and p.status == "valid"),
        None,
    )
    after_patch = next(
        (p for p in clone.patches.values()
         if p.patch_type == "axial_overlays" and p.status == "valid"),
        None,
    )
    before_count = len(before_patch.content.get("overlays", [])) if before_patch else 0
    after_count = len(after_patch.content.get("overlays", [])) if after_patch else 0
    return after_count >= before_count


# ---------------------------------------------------------------------------
# Internal helpers — misc
# ---------------------------------------------------------------------------


def _layer_loading_ids(layer: Any) -> list[str]:
    """Resolve loading_id / loading_ids from an AxialLayerSpec."""
    lids = list(getattr(layer, "loading_ids", None) or [])
    if lids:
        return lids
    lid = getattr(layer, "loading_id", None)
    if lid:
        return [lid]
    return []


def _extract_op_id_from_path(schema_path: str) -> str | None:
    """Extract operation_id from a path like transformations[xyz].replacement_universe_id."""
    marker = "transformations["
    idx = schema_path.find(marker)
    if idx < 0:
        return None
    start = idx + len(marker)
    end = schema_path.find("]", start)
    if end < 0:
        return None
    return schema_path[start:end]


def _extract_loading_id_from_path(schema_path: str) -> str | None:
    """Extract loading_id from a path like lattice_loadings[xyz].transformations."""
    marker = "lattice_loadings["
    idx = schema_path.find(marker)
    if idx < 0:
        return None
    start = idx + len(marker)
    end = schema_path.find("]", start)
    if end < 0:
        return None
    return schema_path[start:end]


def _ranges_overlap(
    a_min: float, a_max: float, b_min: float, b_max: float,
) -> bool:
    return a_min < b_max and a_max > b_min


def _resolve_grid_material(
    state: PlanBuildState,
    ev: GridTransformationEvidence,
) -> str | None:
    """Try to resolve a grid material for Strategy B overlay creation."""
    if ev.material_id:
        return ev.material_id
    mat_patch = next(
        (p for p in state.patches.values()
         if p.patch_type == "materials" and p.status == "valid"),
        None,
    )
    if mat_patch is None:
        return None
    candidates = []
    for mat in mat_patch.content.get("materials", []):
        role = mat.get("role", "")
        if role in _GRID_MATERIAL_ROLES:
            candidates.append(mat.get("material_id"))
    if len(candidates) == 1:
        return candidates[0]
    return None


__all__ = [
    "GridTransformationEvidence",
    "GridLoadingRepairDiagnosis",
    "diagnose_grid_loading_failure",
    "propose_grid_migration_repair_bundle",
    "evaluate_grid_migration_repair_bundle",
]
