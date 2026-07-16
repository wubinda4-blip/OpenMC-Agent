"""Strict, reactor-neutral grid geometry materialization validator.

When an ``axial_overlays`` patch contains one or more ``spacer_grid`` overlays
with a physical ``geometry_mode`` (not ``skeleton``), the assembler must inject
grid-decorated universes into the final IR.  This module verifies that the full
chain — overlay → derived lattice → decorated universe → frame cell → frame
region → frame surfaces → grid material — is structurally complete and
reachable, producing fail-closed issues when any link is missing.

This validator is intentionally **reactor-neutral**: it does not hardcode
VERA4-specific counts, names, or material IDs.  VERA4-specific quantity checks
live in the campaign-eval acceptance module.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any

from openmc_agent.schemas import (
    CellSpec,
    ComplexMaterialSpec,
    ComplexModelSpec,
    LatticeSpec,
    RegionSpec,
    SimulationPlan,
    SurfaceSpec,
    UniverseSpec,
)

__all__ = [
    "GridGeometryValidationIssue",
    "GridGeometryValidationResult",
    "GridReachabilityReport",
    "validate_grid_geometry_materialization",
    "build_grid_geometry_reachability_report",
    "compute_geometry_structural_digest",
]

_VALIDATOR_CONTRACT_VERSION = "1.0.0"

# Primary issue code — always emitted when any sub-check fails.
PRIMARY_CODE = "fullcore.grid_geometry_not_materialized"

# Secondary codes — provide actionable diagnostics.
CODE_DECORATED_UNIVERSE_MISSING = "fullcore.grid_decorated_universe_missing"
CODE_LATTICE_REFERENCE_MISSING = "fullcore.grid_lattice_reference_missing"
CODE_FRAME_CELL_MISSING = "fullcore.grid_frame_cell_missing"
CODE_FRAME_REGION_MISSING = "fullcore.grid_frame_region_missing"
CODE_MATERIAL_UNREACHABLE = "fullcore.grid_material_unreachable"
CODE_DIGEST_UNCHANGED = "fullcore.grid_geometry_digest_unchanged"
CODE_DANGLING_REFERENCE = "fullcore.grid_dangling_reference"


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class GridGeometryValidationIssue:
    code: str
    severity: str  # "error" | "warning" | "info"
    message: str
    detail: dict[str, Any] = field(default_factory=dict)


@dataclass
class GridGeometryValidationResult:
    ok: bool
    issues: list[GridGeometryValidationIssue] = field(default_factory=list)
    summary: dict[str, Any] = field(default_factory=dict)

    @property
    def errors(self) -> list[GridGeometryValidationIssue]:
        return [i for i in self.issues if i.severity == "error"]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _is_physical_grid_overlay(ov: Any) -> bool:
    """Return True when an overlay requires grid geometry injection."""
    kind = getattr(ov, "overlay_kind", None) or ""
    mode = getattr(ov, "geometry_mode", None) or "skeleton"
    return kind == "spacer_grid" and mode != "skeleton"


def _active_grid_overlays(model: ComplexModelSpec) -> list[Any]:
    """Return physical (non-skeleton) spacer_grid overlays from the model."""
    core = model.core
    if core is None:
        return []
    return [ov for ov in (core.axial_overlays or []) if _is_physical_grid_overlay(ov)]


def _grid_material_ids(overlays: list[Any]) -> set[str]:
    """Collect grid material IDs from overlays."""
    ids: set[str] = set()
    for ov in overlays:
        mid = getattr(ov, "material_id", None)
        if mid:
            ids.add(mid)
    return ids


def _decorated_universe_ids(universes: list[UniverseSpec]) -> set[str]:
    """Return IDs of grid-decorated universes (contain ``__grid__`` marker)."""
    return {u.id for u in universes if "__grid__" in u.id}


def _cell_id_set(cells: list[CellSpec]) -> set[str]:
    return {c.id for c in cells}


def _universe_id_set(universes: list[UniverseSpec]) -> set[str]:
    return {u.id for u in universes}


def _lattice_id_set(lattices: list[LatticeSpec]) -> set[str]:
    return {l.id for l in lattices}


def _material_id_set(materials: list[ComplexMaterialSpec]) -> set[str]:
    return {m.id for m in materials}


def _region_id_set(regions: list[RegionSpec]) -> set[str]:
    return {r.id for r in regions}


def _surface_id_set(surfaces: list[SurfaceSpec]) -> set[str]:
    return {s.id for s in surfaces}


# ---------------------------------------------------------------------------
# Structural digest (grid-on vs grid-off comparison)
# ---------------------------------------------------------------------------

def compute_geometry_structural_digest(model: ComplexModelSpec) -> str:
    """Compute a deterministic structural digest of the geometry IR.

    Two models with the same universe/cell/surface/region/lattice structure
    will produce the same digest, enabling grid-on/grid-off comparison.
    """
    h = hashlib.sha256()

    def _feed_sorted(items: list[Any], key_fn: Any) -> None:
        for item in sorted(items, key=key_fn):
            h.update(repr(item).encode())

    _feed_sorted(model.universes, lambda u: u.id)
    _feed_sorted(model.cells, lambda c: c.id)
    _feed_sorted(model.surfaces, lambda s: s.id)
    _feed_sorted(model.regions, lambda r: r.id)
    # Lattice digest includes universe_pattern to catch grid-universe replacements
    for lat in sorted(model.lattices, key=lambda l: l.id):
        h.update(f"lat:{lat.id}".encode())
        for row in (lat.universe_pattern or []):
            h.update("|".join(row).encode())
        h.update(f"|ou={lat.outer_universe_id}".encode())

    return h.hexdigest()[:16]


# ---------------------------------------------------------------------------
# Main validator
# ---------------------------------------------------------------------------

def validate_grid_geometry_materialization(
    plan: SimulationPlan,
    *,
    grid_off_model: ComplexModelSpec | None = None,
) -> GridGeometryValidationResult:
    """Validate that active spacer_grid overlays produce physical geometry.

    Returns a fail-closed result: when grid overlays exist, every check must
    pass.  Any failure produces an error-severity issue with the primary code
    ``fullcore.grid_geometry_not_materialized`` plus a secondary code for
    actionable diagnostics.

    Parameters
    ----------
    plan
        The assembled ``SimulationPlan``.
    grid_off_model
        Optional: the same model with grid overlays removed, for digest
        comparison.  When provided, the digest must differ.
    """
    issues: list[GridGeometryValidationIssue] = []
    model = plan.complex_model

    if model is None:
        # No model — nothing to validate (other validators handle this).
        return GridGeometryValidationResult(ok=True, summary={"reason": "no_complex_model"})

    overlays = _active_grid_overlays(model)

    # No active grid overlays → validator passes (nothing to check).
    if not overlays:
        return GridGeometryValidationResult(
            ok=True,
            summary={"active_grid_overlays": 0, "decorated_universes": 0},
        )

    grid_mat_ids = _grid_material_ids(overlays)
    uv_ids = _universe_id_set(model.universes)
    cell_ids = _cell_id_set(model.cells)
    mat_ids = _material_id_set(model.materials)
    region_ids = _region_id_set(model.regions)
    surface_ids = _surface_id_set(model.surfaces)
    lat_ids = _lattice_id_set(model.lattices)

    decorated_ids = _decorated_universe_ids(model.universes)

    summary: dict[str, Any] = {
        "active_grid_overlays": len(overlays),
        "grid_material_ids": sorted(grid_mat_ids),
        "decorated_universe_count": len(decorated_ids),
        "decorated_universe_ids": sorted(decorated_ids),
    }

    # ------------------------------------------------------------------
    # Check 1: grid_decorated_universe_patches non-empty
    # ------------------------------------------------------------------
    if not decorated_ids:
        issues.append(GridGeometryValidationIssue(
            code=CODE_DECORATED_UNIVERSE_MISSING,
            severity="error",
            message=(
                f"{len(overlays)} active spacer_grid overlay(s) exist but zero "
                "grid-decorated universes were materialized"
            ),
            detail={"overlay_count": len(overlays), "decorated_count": 0},
        ))

    # ------------------------------------------------------------------
    # Check 2: at least one lattice references a decorated universe
    # ------------------------------------------------------------------
    lattices_with_grid: list[str] = []
    for lat in model.lattices:
        for row in (lat.universe_pattern or []):
            for uid in row:
                if uid in decorated_ids:
                    lattices_with_grid.append(lat.id)
                    break
            else:
                continue
            break

    if decorated_ids and not lattices_with_grid:
        issues.append(GridGeometryValidationIssue(
            code=CODE_LATTICE_REFERENCE_MISSING,
            severity="error",
            message=(
                f"{len(decorated_ids)} grid-decorated universe(s) exist but no "
                "lattice references them"
            ),
            detail={"decorated_ids": sorted(decorated_ids)},
        ))

    # ------------------------------------------------------------------
    # Check 3: decorated universes exist in final universe catalog
    # (Implicitly satisfied — we built decorated_ids FROM model.universes)
    # But we must check for dangling lattice references to non-existent IDs.
    # ------------------------------------------------------------------
    for lat in model.lattices:
        for row in (lat.universe_pattern or []):
            for uid in row:
                if uid not in uv_ids:
                    issues.append(GridGeometryValidationIssue(
                        code=CODE_DANGLING_REFERENCE,
                        severity="error",
                        message=(
                            f"Lattice {lat.id!r} references universe {uid!r} "
                            "not in final universe catalog"
                        ),
                        detail={"lattice_id": lat.id, "missing_universe_id": uid},
                    ))

    # ------------------------------------------------------------------
    # Check 4: decorated universe contains a grid frame cell
    # ------------------------------------------------------------------
    frame_cells_by_universe: dict[str, list[str]] = {}
    for uv in model.universes:
        if uv.id not in decorated_ids:
            continue
        for cid in (uv.cell_ids or []):
            cell = next((c for c in model.cells if c.id == cid), None)
            if cell is None:
                continue
            # Frame cell detection: component_role or id contains "grid_frame"
            role = (cell.component_role or "").lower()
            cid_lower = cell.id.lower()
            if "grid_frame" in role or "grid_frame" in cid_lower:
                frame_cells_by_universe.setdefault(uv.id, []).append(cell.id)

    decorated_without_frame = sorted(decorated_ids - set(frame_cells_by_universe.keys()))
    if decorated_without_frame:
        issues.append(GridGeometryValidationIssue(
            code=CODE_FRAME_CELL_MISSING,
            severity="error",
            message=(
                f"{len(decorated_without_frame)} decorated universe(s) lack a "
                "grid_frame cell"
            ),
            detail={"universes_missing_frame": decorated_without_frame},
        ))

    # ------------------------------------------------------------------
    # Check 5: frame cell references grid material
    # ------------------------------------------------------------------
    frame_cells_total: set[str] = set()
    for cells_list in frame_cells_by_universe.values():
        frame_cells_total.update(cells_list)

    frame_materials_found: set[str] = set()
    for cid in frame_cells_total:
        cell = next((c for c in model.cells if c.id == cid), None)
        if cell and cell.fill_type == "material" and cell.fill_id:
            frame_materials_found.add(cell.fill_id)

    if frame_cells_total:
        missing_mats = grid_mat_ids - frame_materials_found
        if missing_mats:
            issues.append(GridGeometryValidationIssue(
                code=CODE_FRAME_CELL_MISSING,
                severity="error",
                message=(
                    "Frame cells do not reference expected grid material(s): "
                    f"{sorted(missing_mats)}"
                ),
                detail={
                    "expected_grid_materials": sorted(grid_mat_ids),
                    "actual_frame_materials": sorted(frame_materials_found),
                },
            ))

    # Check 5b: frame cell materials exist in the material catalog
    frame_mats_not_in_catalog = frame_materials_found - mat_ids
    if frame_mats_not_in_catalog:
        issues.append(GridGeometryValidationIssue(
            code=CODE_MATERIAL_UNREACHABLE,
            severity="error",
            message=(
                "Frame cell material(s) not in material catalog: "
                f"{sorted(frame_mats_not_in_catalog)}"
            ),
            detail={
                "missing_from_catalog": sorted(frame_mats_not_in_catalog),
                "catalog_material_count": len(mat_ids),
            },
        ))

    # ------------------------------------------------------------------
    # Check 6: square_frame region exists
    # ------------------------------------------------------------------
    frame_regions: list[str] = []
    for uv_id, fc_ids in frame_cells_by_universe.items():
        for fc_id in fc_ids:
            cell = next((c for c in model.cells if c.id == fc_id), None)
            if cell is None or cell.region_id is None:
                issues.append(GridGeometryValidationIssue(
                    code=CODE_FRAME_REGION_MISSING,
                    severity="error",
                    message=f"Frame cell {fc_id!r} has no region_id",
                    detail={"cell_id": fc_id},
                ))
                continue
            region = next((r for r in model.regions if r.id == cell.region_id), None)
            if region is None:
                issues.append(GridGeometryValidationIssue(
                    code=CODE_FRAME_REGION_MISSING,
                    severity="error",
                    message=(
                        f"Frame cell {fc_id!r} references region "
                        f"{cell.region_id!r} not in region catalog"
                    ),
                    detail={"cell_id": fc_id, "missing_region_id": cell.region_id},
                ))
            else:
                frame_regions.append(cell.region_id)

    # ------------------------------------------------------------------
    # Check 7: frame surfaces exist
    # ------------------------------------------------------------------
    for rid in frame_regions:
        region = next((r for r in model.regions if r.id == rid), None)
        if region is None:
            continue
        for sid in (region.surface_ids or []):
            if sid not in surface_ids:
                issues.append(GridGeometryValidationIssue(
                    code=CODE_DANGLING_REFERENCE,
                    severity="error",
                    message=(
                        f"Frame region {rid!r} references surface {sid!r} "
                        "not in surface catalog"
                    ),
                    detail={"region_id": rid, "missing_surface_id": sid},
                ))

    # ------------------------------------------------------------------
    # Check 8: grid material reachable from axial layer
    # ------------------------------------------------------------------
    reachable_materials = _compute_reachable_material_ids(model)
    unreachable_grid_mats = grid_mat_ids - reachable_materials
    if unreachable_grid_mats:
        issues.append(GridGeometryValidationIssue(
            code=CODE_MATERIAL_UNREACHABLE,
            severity="error",
            message=(
                "Grid material(s) unreachable from core lattice: "
                f"{sorted(unreachable_grid_mats)}"
            ),
            detail={
                "grid_material_ids": sorted(grid_mat_ids),
                "reachable_materials_count": len(reachable_materials),
                "unreachable": sorted(unreachable_grid_mats),
            },
        ))

    # ------------------------------------------------------------------
    # Check 9: grid-on vs grid-off structural digest comparison
    # ------------------------------------------------------------------
    if grid_off_model is not None:
        digest_on = compute_geometry_structural_digest(model)
        digest_off = compute_geometry_structural_digest(grid_off_model)
        if digest_on == digest_off:
            issues.append(GridGeometryValidationIssue(
                code=CODE_DIGEST_UNCHANGED,
                severity="error",
                message=(
                    "Grid-on and grid-off geometry structural digests are "
                    "identical — grid geometry was not injected"
                ),
                detail={
                    "digest_on": digest_on,
                    "digest_off": digest_off,
                },
            ))

    # ------------------------------------------------------------------
    # Check 10: all decorated refs resolvable
    # ------------------------------------------------------------------
    for lat in model.lattices:
        for row in (lat.universe_pattern or []):
            for uid in row:
                if "__grid__" in uid and uid not in uv_ids:
                    issues.append(GridGeometryValidationIssue(
                        code=CODE_DANGLING_REFERENCE,
                        severity="error",
                        message=(
                            f"Lattice {lat.id!r} references decorated universe "
                            f"{uid!r} not in universe catalog"
                        ),
                        detail={"lattice_id": lat.id, "missing_id": uid},
                    ))

    # ------------------------------------------------------------------
    # Finalise
    # ------------------------------------------------------------------
    error_count = sum(1 for i in issues if i.severity == "error")
    if error_count > 0:
        # Wrap with primary code
        issues.insert(0, GridGeometryValidationIssue(
            code=PRIMARY_CODE,
            severity="error",
            message=(
                f"Grid geometry validation failed with {error_count} error(s); "
                "spacer_grid overlays require physical geometry injection"
            ),
            detail={
                "error_count": error_count,
                "secondary_codes": sorted({i.code for i in issues[1:]}),
            },
        ))

    summary["error_count"] = error_count
    summary["frame_cell_count"] = len(frame_cells_total)
    summary["frame_region_count"] = len(frame_regions)
    summary["reachable_material_count"] = len(reachable_materials)
    summary["lattices_with_grid"] = lattices_with_grid
    summary["validator_contract_version"] = _VALIDATOR_CONTRACT_VERSION

    return GridGeometryValidationResult(
        ok=error_count == 0,
        issues=issues,
        summary=summary,
    )


# ---------------------------------------------------------------------------
# Reachability
# ---------------------------------------------------------------------------

def _compute_reachable_material_ids(model: ComplexModelSpec) -> set[str]:
    """Compute the set of material IDs reachable from **any** lattice.

    The full-core IR contains both template lattices (``core_lattice``,
    ``assembly_lattice__*``) and per-segment concrete lattices (with
    hashed suffixes).  The template ``core.lattice_id`` links to template
    assembly lattices, while grid-decorated universes appear only in the
    segment-specific lattices.  These segment-specific lattices will be
    selected at render time by ``materialize_axial_lattice_transformations``,
    so we treat all lattices in the model as reachable roots.
    """
    reachable: set[str] = set()
    visited_cells: set[str] = set()
    visited_universes: set[str] = set()
    visited_lattices: set[str] = set()

    cells_by_id = {c.id: c for c in model.cells}
    universes_by_id = {u.id: u for u in model.universes}
    lattices_by_id = {l.id: l for l in model.lattices}

    # All lattices in the IR are potential roots (segment-specific lattices
    # are selected at render time via axial lattice transformations).
    queue: list[str] = [l.id for l in model.lattices]

    while queue:
        lat_id = queue.pop(0)
        if lat_id in visited_lattices:
            continue
        visited_lattices.add(lat_id)

        lat = lattices_by_id.get(lat_id)
        if lat is None:
            continue

        # Collect all universe IDs referenced by this lattice
        lat_uv_ids: set[str] = set()
        for row in (lat.universe_pattern or []):
            lat_uv_ids.update(row)
        if lat.outer_universe_id:
            lat_uv_ids.add(lat.outer_universe_id)

        for uid in lat_uv_ids:
            if uid in visited_universes:
                continue
            queue_uvs: list[str] = [uid]
            while queue_uvs:
                uv_id = queue_uvs.pop(0)
                if uv_id in visited_universes:
                    continue
                visited_universes.add(uv_id)
                uv = universes_by_id.get(uv_id)
                if uv is None:
                    continue
                for cid in (uv.cell_ids or []):
                    if cid in visited_cells:
                        continue
                    visited_cells.add(cid)
                    cell = cells_by_id.get(cid)
                    if cell is None:
                        continue
                    if cell.fill_type == "material" and cell.fill_id:
                        reachable.add(cell.fill_id)
                        mat = next((m for m in model.materials if m.id == cell.fill_id), None)
                        if mat and mat.mixture_component_ids:
                            reachable.update(mat.mixture_component_ids)
                    elif cell.fill_type == "universe" and cell.fill_id:
                        if cell.fill_id not in visited_universes:
                            queue_uvs.append(cell.fill_id)
                    elif cell.fill_type == "lattice" and cell.fill_id:
                        if cell.fill_id not in visited_lattices:
                            queue.append(cell.fill_id)

    return reachable


# ---------------------------------------------------------------------------
# Hierarchical reachability report
# ---------------------------------------------------------------------------

@dataclass
class GridReachabilityReport:
    result: str  # "pass" | "fail"
    active_overlay_ids: list[str] = field(default_factory=list)
    active_axial_layer_ids: list[str] = field(default_factory=list)
    core_lattice_ids: list[str] = field(default_factory=list)
    derived_lattice_ids: list[str] = field(default_factory=list)
    decorated_universe_ids: list[str] = field(default_factory=list)
    frame_cell_ids: list[str] = field(default_factory=list)
    frame_region_ids: list[str] = field(default_factory=list)
    grid_material_ids: list[str] = field(default_factory=list)
    missing_refs: list[dict[str, str]] = field(default_factory=list)
    unreachable_refs: list[dict[str, str]] = field(default_factory=list)
    detail: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "result": self.result,
            "active_overlay_ids": self.active_overlay_ids,
            "active_axial_layer_ids": self.active_axial_layer_ids,
            "core_lattice_ids": self.core_lattice_ids,
            "derived_lattice_ids": self.derived_lattice_ids,
            "decorated_universe_ids": self.decorated_universe_ids,
            "frame_cell_ids": self.frame_cell_ids,
            "frame_region_ids": self.frame_region_ids,
            "grid_material_ids": self.grid_material_ids,
            "missing_refs": self.missing_refs,
            "unreachable_refs": self.unreachable_refs,
            "detail": self.detail,
        }


def build_grid_geometry_reachability_report(
    plan: SimulationPlan,
) -> GridReachabilityReport:
    """Trace the full hierarchy from grid-active layers to grid materials.

    Traverses: axial layer → core lattice → assembly universe →
    derived pin lattice → grid-decorated universe → frame cell →
    square_frame region → grid material.

    Returns a structured report with all IDs at each level plus any
    missing or unreachable references.
    """
    model = plan.complex_model
    if model is None or model.core is None:
        return GridReachabilityReport(result="pass", detail={"reason": "no_core"})

    overlays = _active_grid_overlays(model)
    if not overlays:
        return GridReachabilityReport(result="pass", detail={"reason": "no_active_grids"})

    grid_mat_ids = _grid_material_ids(overlays)
    cells_by_id = {c.id: c for c in model.cells}
    universes_by_id = {u.id: u for u in model.universes}
    lattices_by_id = {l.id: l for l in model.lattices}
    regions_by_id = {r.id: r for r in model.regions}

    overlay_ids = [getattr(ov, "id", f"overlay_{i}") for i, ov in enumerate(overlays)]

    # Active axial layers — those whose z-range overlaps a grid overlay
    active_layer_ids: list[str] = []
    core = model.core
    for layer in (core.axial_layers or []):
        layer_z_min = getattr(layer, "z_min_cm", None)
        layer_z_max = getattr(layer, "z_max_cm", None)
        if layer_z_min is None or layer_z_max is None:
            continue
        for ov in overlays:
            ov_min = getattr(ov, "z_min_cm", None)
            ov_max = getattr(ov, "z_max_cm", None)
            if ov_min is None or ov_max is None:
                continue
            if layer_z_min < ov_max and layer_z_max > ov_min:
                active_layer_ids.append(layer.id if hasattr(layer, "id") else f"layer_{layer_z_min}")
                break

    # Core lattice
    core_lattice_ids: list[str] = []
    if core.lattice_id:
        core_lattice_ids.append(core.lattice_id)

    # Derived lattices referencing decorated universes
    decorated_ids = _decorated_universe_ids(model.universes)
    derived_lattice_ids: list[str] = []
    for lat in model.lattices:
        for row in (lat.universe_pattern or []):
            for uid in row:
                if uid in decorated_ids:
                    if lat.id not in derived_lattice_ids:
                        derived_lattice_ids.append(lat.id)
                    break
            else:
                continue
            break

    # Frame cells
    frame_cell_ids: list[str] = []
    frame_region_ids: list[str] = []
    for uv in model.universes:
        if uv.id not in decorated_ids:
            continue
        for cid in (uv.cell_ids or []):
            cell = cells_by_id.get(cid)
            if cell is None:
                continue
            role = (cell.component_role or "").lower()
            if "grid_frame" in role or "grid_frame" in cid.lower():
                frame_cell_ids.append(cid)
                if cell.region_id:
                    frame_region_ids.append(cell.region_id)

    # Missing / unreachable
    missing_refs: list[dict[str, str]] = []
    unreachable_refs: list[dict[str, str]] = []

    # Check decorated universes referenced by lattices actually exist
    for lat in model.lattices:
        for row in (lat.universe_pattern or []):
            for uid in row:
                if "__grid__" in uid and uid not in universes_by_id:
                    missing_refs.append({
                        "type": "decorated_universe",
                        "lattice_id": lat.id,
                        "missing_id": uid,
                    })

    # Check frame cells reference valid materials
    for cid in frame_cell_ids:
        cell = cells_by_id.get(cid)
        if cell is None:
            continue
        if cell.fill_type == "material" and cell.fill_id:
            if cell.fill_id not in {m.id for m in model.materials}:
                unreachable_refs.append({
                    "type": "grid_material",
                    "cell_id": cid,
                    "missing_material": cell.fill_id,
                })

    # Check frame regions reference valid surfaces
    for rid in frame_region_ids:
        region = regions_by_id.get(rid)
        if region is None:
            continue
        surf_ids = {s.id for s in model.surfaces}
        for sid in (region.surface_ids or []):
            if sid not in surf_ids:
                unreachable_refs.append({
                    "type": "frame_surface",
                    "region_id": rid,
                    "missing_surface": sid,
                })

    # Compute reachable materials
    reachable_mats = _compute_reachable_material_ids(model)
    for gmid in grid_mat_ids:
        if gmid not in reachable_mats:
            unreachable_refs.append({
                "type": "grid_material_unreachable",
                "material_id": gmid,
            })

    ok = (
        len(missing_refs) == 0
        and len(unreachable_refs) == 0
        and len(decorated_ids) > 0
        and len(frame_cell_ids) > 0
    )

    return GridReachabilityReport(
        result="pass" if ok else "fail",
        active_overlay_ids=overlay_ids,
        active_axial_layer_ids=active_layer_ids,
        core_lattice_ids=core_lattice_ids,
        derived_lattice_ids=derived_lattice_ids,
        decorated_universe_ids=sorted(decorated_ids),
        frame_cell_ids=frame_cell_ids,
        frame_region_ids=frame_region_ids,
        grid_material_ids=sorted(grid_mat_ids),
        missing_refs=missing_refs,
        unreachable_refs=unreachable_refs,
        detail={
            "active_overlay_count": len(overlays),
            "decorated_universe_count": len(decorated_ids),
            "reachable_material_count": len(reachable_mats),
            "validator_contract_version": _VALIDATOR_CONTRACT_VERSION,
        },
    )
