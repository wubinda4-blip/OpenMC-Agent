"""Final localized insert placement reachability report.

Traces the complete chain from source requirement → root-reachable
material path, and produces a structured report that can be serialized
as ``localized_insert_placement_report.json``.

Required reachability semantics (not blanket "all universes must be used"):
- Universes/profiles/intents marked as required by the source contract
  MUST be root-reachable in the final geometry.
- Library universes not selected by the current state may remain unused.
- Profile segments completely outside the detailed domain → ``clipped_out``.
- Profile segments overlapping the detailed domain MUST appear in derived lattices.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from openmc_agent.plan_builder.patches import (
    AssemblyCatalogPatch,
    CoreLayoutPatch,
    FactsPatch,
    LocalizedInsertPlacementRequirementPatchItem,
    LocalizedInsertProfilesPatch,
    UniversesPatch,
)
from openmc_agent.plan_builder.localized_insert_profiles import (
    ResolvedLocalizedInsertProfile,
    resolve_profile_for_intent,
)
from openmc_agent.reachability import collect_active_dependencies


@dataclass
class SegmentPlacementInfo:
    segment_id: str
    absolute_z_min_cm: float
    absolute_z_max_cm: float
    universe_id: str
    role: str
    clipping: str = "reachable"  # reachable | clipped_out | inactive_state
    derived_lattice_ids: list[str] = field(default_factory=list)
    path_count: int = 0


@dataclass
class RequirementPlacementInfo:
    requirement_id: str
    insert_kind: str
    assembly_type_ids: list[str]
    core_coordinates: list[tuple[int, int]] = field(default_factory=list)
    intent_id: str | None = None
    profile_id: str | None = None
    anchor_z_cm: float | None = None
    control_state_id: str | None = None
    source_coordinates: list[tuple[int, int]] = field(default_factory=list)
    expected_coordinate_count: int | None = None
    resolved_segments: list[SegmentPlacementInfo] = field(default_factory=list)
    derived_lattice_ids: list[str] = field(default_factory=list)
    wrapper_universe_ids: list[str] = field(default_factory=list)
    core_lattice_ids: list[str] = field(default_factory=list)
    axial_layer_ids: list[str] = field(default_factory=list)
    reachable_universe_ids: list[str] = field(default_factory=list)
    reachable_material_ids: list[str] = field(default_factory=list)
    per_segment_path_counts: dict[str, int] = field(default_factory=dict)
    total_physical_path_count: int = 0
    orphan_universe_ids: list[str] = field(default_factory=list)
    missing_references: list[str] = field(default_factory=list)
    result: str = "pending"  # pass | fail | clipped_out | inactive_state


@dataclass
class PlacementReachabilityReport:
    requirements: list[RequirementPlacementInfo] = field(default_factory=list)
    overall_result: str = "pending"  # pass | fail
    issues: list[dict[str, str]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "requirements": [
                {
                    "requirement_id": r.requirement_id,
                    "insert_kind": r.insert_kind,
                    "assembly_type_ids": r.assembly_type_ids,
                    "core_coordinates": [list(c) for c in r.core_coordinates],
                    "intent_id": r.intent_id,
                    "profile_id": r.profile_id,
                    "anchor_z_cm": r.anchor_z_cm,
                    "control_state_id": r.control_state_id,
                    "source_coordinates": [list(c) for c in r.source_coordinates],
                    "expected_coordinate_count": r.expected_coordinate_count,
                    "resolved_segments": [
                        {
                            "segment_id": s.segment_id,
                            "absolute_z_min_cm": s.absolute_z_min_cm,
                            "absolute_z_max_cm": s.absolute_z_max_cm,
                            "universe_id": s.universe_id,
                            "role": s.role,
                            "clipping": s.clipping,
                            "derived_lattice_ids": s.derived_lattice_ids,
                            "path_count": s.path_count,
                        }
                        for s in r.resolved_segments
                    ],
                    "derived_lattice_ids": r.derived_lattice_ids,
                    "wrapper_universe_ids": r.wrapper_universe_ids,
                    "core_lattice_ids": r.core_lattice_ids,
                    "axial_layer_ids": r.axial_layer_ids,
                    "reachable_universe_ids": r.reachable_universe_ids,
                    "reachable_material_ids": r.reachable_material_ids,
                    "per_segment_path_counts": r.per_segment_path_counts,
                    "total_physical_path_count": r.total_physical_path_count,
                    "orphan_universe_ids": r.orphan_universe_ids,
                    "missing_references": r.missing_references,
                    "result": r.result,
                }
                for r in self.requirements
            ],
            "overall_result": self.overall_result,
            "issues": self.issues,
        }


def build_localized_insert_placement_report(
    facts_patch: FactsPatch | None,
    universes_patch: UniversesPatch | None,
    profiles_patch: LocalizedInsertProfilesPatch | None,
    assembly_catalog_patch: AssemblyCatalogPatch | None,
    core_layout_patch: CoreLayoutPatch | None,
    *,
    complex_model=None,
    axial_domain_cm: tuple[float, float] | None = None,
    plan=None,
) -> PlacementReachabilityReport:
    """Build a placement reachability report tracing the full chain.

    Parameters
    ----------
    complex_model
        The assembled :class:`ComplexModelSpec` for reachability analysis.
    plan
        Optional full :class:`SimulationPlan` for reachability computation.
    """
    report = PlacementReachabilityReport()

    if facts_patch is None or not facts_patch.localized_insert_requirements:
        report.overall_result = "pass"
        return report

    requirements = facts_patch.localized_insert_requirements

    # Build lookups
    catalog_map: dict[str, Any] = {}
    if assembly_catalog_patch is not None:
        for at in assembly_catalog_patch.assembly_types:
            catalog_map[at.assembly_type_id] = at

    profile_map = {}
    if profiles_patch is not None:
        for p in profiles_patch.profiles:
            profile_map[p.profile_id] = p

    # Core layout positions
    layout_positions: dict[str, list[tuple[int, int]]] = {}
    if core_layout_patch is not None:
        for r_idx, row in enumerate(core_layout_patch.assembly_pattern):
            for c_idx, tid in enumerate(row):
                layout_positions.setdefault(tid, []).append((r_idx, c_idx))

    # Compute reachable universe/material IDs from the plan
    reachable_universe_ids: set[str] = set()
    reachable_material_ids: set[str] = set()
    if plan is not None:
        deps = collect_active_dependencies(plan)
        reachable_universe_ids = set(deps.universe_ids)
        reachable_material_ids = set(deps.material_ids)
    elif complex_model is not None:
        # Scan lattices for universe patterns
        for lat in complex_model.lattices:
            for row in lat.universe_pattern:
                for uid in row:
                    reachable_universe_ids.add(uid)
            if lat.outer_universe_id:
                reachable_universe_ids.add(lat.outer_universe_id)
        for mat in complex_model.materials:
            reachable_material_ids.add(mat.id)

    domain_z_min, domain_z_max = (None, None)
    if axial_domain_cm is not None:
        domain_z_min, domain_z_max = axial_domain_cm

    # Scan derived lattices for universe occurrences
    all_lattices = []
    if complex_model is not None:
        all_lattices = complex_model.lattices
    elif plan is not None and plan.complex_model is not None:
        all_lattices = plan.complex_model.lattices

    for req in requirements:
        info = RequirementPlacementInfo(
            requirement_id=req.requirement_id,
            insert_kind=req.insert_kind,
            assembly_type_ids=list(req.assembly_type_ids),
            expected_coordinate_count=req.expected_coordinate_count_per_assembly,
            anchor_z_cm=req.anchor_z_cm,
            control_state_id=req.control_state_id,
        )

        # Core coordinates of assembly instances
        for tid in req.assembly_type_ids:
            info.core_coordinates.extend(layout_positions.get(tid, []))

        # Find matching intents
        for tid in req.assembly_type_ids:
            at = catalog_map.get(tid)
            if at is None:
                continue
            for intent in at.pin_map.localized_insert_intents:
                if intent.insert_kind != req.insert_kind:
                    continue
                info.intent_id = intent.insert_id
                info.profile_id = intent.axial_profile_id
                info.source_coordinates = list(intent.coordinates)

                # Resolve profile segments
                if intent.axial_profile_id and intent.axial_profile_id in profile_map:
                    resolved = resolve_profile_for_intent(
                        intent, profile_map, tid
                    )
                    if resolved:
                        for seg in resolved.resolved_segments:
                            seg_info = SegmentPlacementInfo(
                                segment_id=seg.segment_id,
                                absolute_z_min_cm=seg.absolute_z_min_cm,
                                absolute_z_max_cm=seg.absolute_z_max_cm,
                                universe_id=seg.universe_id,
                                role=seg.role,
                            )
                            # Determine clipping
                            if domain_z_min is not None and domain_z_max is not None:
                                overlap_min = max(seg.absolute_z_min_cm, domain_z_min)
                                overlap_max = min(seg.absolute_z_max_cm, domain_z_max)
                                if overlap_max <= overlap_min:
                                    seg_info.clipping = "clipped_out"
                                else:
                                    seg_info.clipping = "reachable"
                                    # Count paths in derived lattices
                                    count = 0
                                    for lat in all_lattices:
                                        for row in lat.universe_pattern:
                                            for uid in row:
                                                if uid == seg.universe_id:
                                                    count += 1
                                    seg_info.path_count = count
                                    if count > 0:
                                        seg_info.derived_lattice_ids = [
                                            lat.id for lat in all_lattices
                                            if any(seg.universe_id in row for row in lat.universe_pattern)
                                        ]
                            else:
                                seg_info.clipping = "inactive_state"

                            info.resolved_segments.append(seg_info)
                            info.per_segment_path_counts[seg.segment_id] = seg_info.path_count

                    # Collect derived lattice IDs that reference the insert universes
                    for lat in all_lattices:
                        for uid in req.expected_insert_universe_ids:
                            if any(uid in row for row in lat.universe_pattern):
                                if lat.id not in info.derived_lattice_ids:
                                    info.derived_lattice_ids.append(lat.id)

        # Compute total physical paths (sum of absorber segment paths)
        info.total_physical_path_count = sum(
            s.path_count for s in info.resolved_segments
            if s.clipping == "reachable"
            and any(t in s.role.lower() for t in ("absorber", "aic", "b4c", "poison"))
        )

        # Check reachability of expected universes
        for uid in req.expected_insert_universe_ids:
            # Determine if this universe only appears in clipped-out segments
            has_reachable_segment = False
            for seg in info.resolved_segments:
                if seg.universe_id == uid and seg.clipping == "reachable":
                    has_reachable_segment = True
                    break
            if not has_reachable_segment and uid not in reachable_universe_ids:
                # Universe not reachable and no segment overlaps domain → clipped_out (OK)
                # Only flag as orphaned if required_in_detailed_domain is True
                # AND at least one segment for this universe was expected to be in domain
                if req.required_in_detailed_domain:
                    # Check if ALL segments for this universe are clipped_out
                    seg_for_uid = [s for s in info.resolved_segments if s.universe_id == uid]
                    if seg_for_uid and all(s.clipping == "clipped_out" for s in seg_for_uid):
                        continue  # All segments clipped_out → not an orphan, just clipped
                    info.orphan_universe_ids.append(uid)

        # Determine result
        if not info.intent_id:
            info.result = "fail"
            report.issues.append({
                "requirement_id": req.requirement_id,
                "code": "localized_insert.required_insert_orphaned",
                "message": f"No matching intent found for requirement '{req.requirement_id}'",
            })
        elif info.orphan_universe_ids:
            info.result = "fail"
            for uid in info.orphan_universe_ids:
                report.issues.append({
                    "requirement_id": req.requirement_id,
                    "code": "localized_insert.required_universe_orphaned",
                    "message": f"Required universe '{uid}' is not root-reachable",
                })
        elif req.required_in_detailed_domain and info.total_physical_path_count == 0:
            # Check if all absorber segments are clipped_out (which is OK for non-domain requirements)
            all_clipped = all(
                s.clipping == "clipped_out"
                for s in info.resolved_segments
                if any(t in s.role.lower() for t in ("absorber", "aic", "b4c", "poison"))
            )
            if all_clipped and info.resolved_segments:
                info.result = "clipped_out"
            else:
                info.result = "fail"
                report.issues.append({
                    "requirement_id": req.requirement_id,
                    "code": "localized_insert.required_segment_unreachable",
                    "message": (
                        f"Required absorber segments for '{req.requirement_id}' "
                        f"have 0 root-reachable paths in the detailed domain"
                    ),
                })
        else:
            info.result = "pass"

        report.requirements.append(info)

    # Overall result
    report.overall_result = "fail" if any(r.result == "fail" for r in report.requirements) else "pass"

    return report


def validate_final_localized_insert_reachability(
    facts_patch: FactsPatch | None,
    universes_patch: UniversesPatch | None,
    profiles_patch: LocalizedInsertProfilesPatch | None,
    assembly_catalog_patch: AssemblyCatalogPatch | None,
    core_layout_patch: CoreLayoutPatch | None,
    *,
    plan=None,
    complex_model=None,
    axial_domain_cm: tuple[float, float] | None = None,
) -> PlacementReachabilityReport:
    """Validate final placement reachability. Alias for build_localized_insert_placement_report."""
    return build_localized_insert_placement_report(
        facts_patch,
        universes_patch,
        profiles_patch,
        assembly_catalog_patch,
        core_layout_patch,
        complex_model=complex_model if complex_model is not None else (plan.complex_model if plan else None),
        axial_domain_cm=axial_domain_cm,
        plan=plan,
    )
