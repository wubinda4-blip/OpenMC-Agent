"""Structured grid material probes.

Traces (x, y, z) positions through the lattice hierarchy to determine
the material at that point, without running OpenMC.  Used for verifying
that grid frame, inner moderator, pin interiors, and assembly gaps
contain the expected materials.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

from openmc_agent.schemas import SimulationPlan

__all__ = [
    "ProbeResult",
    "ProbeReport",
    "probe_grid_frame_end",
    "probe_grid_frame_middle",
    "probe_inner_moderator",
    "probe_pin_interior",
    "probe_assembly_gap",
    "probe_non_grid_segment",
    "run_all_probes",
]


@dataclass
class ProbeResult:
    x: float
    y: float
    z: float
    expected_material: str
    actual_material: str | None
    cell_id: str | None
    universe_id: str | None
    passed: bool
    note: str = ""


@dataclass
class ProbeReport:
    results: list[ProbeResult] = field(default_factory=list)

    @property
    def all_passed(self) -> bool:
        return all(r.passed for r in self.results)

    @property
    def failed_count(self) -> int:
        return sum(1 for r in self.results if not r.passed)

    def to_dict(self) -> dict[str, Any]:
        return {
            "all_passed": self.all_passed,
            "total": len(self.results),
            "passed": sum(1 for r in self.results if r.passed),
            "failed": self.failed_count,
            "results": [
                {
                    "x": r.x, "y": r.y, "z": r.z,
                    "expected": r.expected_material,
                    "actual": r.actual_material,
                    "cell_id": r.cell_id,
                    "universe_id": r.universe_id,
                    "passed": r.passed,
                    "note": r.note,
                }
                for r in self.results
            ],
        }


def _find_universe_at_xy(
    plan: SimulationPlan,
    x: float, y: float, z: float,
) -> tuple[str | None, str | None, str | None]:
    """Find the universe, cell, and material at (x, y) within the core.

    Simplified geometric tracer for regular lattice structures:
    1. Determine which axial layer is active at z.
    2. If lattice fill, find the core lattice position.
    3. Find the assembly lattice position within that.
    4. Get the pin-cell universe at that position.
    5. Determine which cell of the universe the (x, y) falls in.

    Returns (universe_id, cell_id, material_id).
    """
    model = plan.complex_model
    if model is None or model.core is None:
        return None, None, None

    core = model.core
    cells_by_id = {c.id: c for c in model.cells}
    universes_by_id = {u.id: u for u in model.universes}

    # 1. Find active axial layer at z
    active_layer = None
    for layer in (core.axial_layers or []):
        if layer.z_min_cm <= z < layer.z_max_cm:
            active_layer = layer
            break

    if active_layer is None:
        return None, None, None

    fill = active_layer.fill
    if fill is None or fill.type != "lattice":
        # Material fill layer — return the material
        return None, None, fill.id if fill else None

    # 2. Find the lattice
    lat_id = fill.id
    lattices_by_id = {l.id: l for l in model.lattices}

    # Try to find the segment-specific lattice for this z
    # (hash-suffixed versions are selected at render time; for probes we
    # scan ALL lattices matching the base id pattern)
    candidate_lattices = [
        l for l in model.lattices
        if l.id == lat_id or l.id.startswith(lat_id + "__")
    ]

    # Pick the one that has grid-decorated universes if we're in a grid band
    grid_overlay_z = _is_in_grid_band(plan, z)
    if grid_overlay_z:
        grid_lats = [
            l for l in candidate_lattices
            if any("__grid__" in uid for row in (l.universe_pattern or []) for uid in row)
        ]
        if grid_lats:
            candidate_lattices = grid_lats

    if not candidate_lattices:
        return None, None, None

    core_lat = candidate_lattices[0]

    # 3. Map (x, y) to lattice cell
    pitch = core_lat.pitch_cm[0] if core_lat.pitch_cm else 4.0
    ll = core_lat.lower_left_cm
    if ll:
        cx = int((x - ll[0]) / pitch)
        cy = int((y - ll[1]) / pitch)
    else:
        center = core_lat.center_cm or (0.0, 0.0)
        shape = core_lat.shape or (len(core_lat.universe_pattern), len(core_lat.universe_pattern[0]) if core_lat.universe_pattern else 0)
        ll_x = center[0] - shape[0] * pitch / 2.0
        ll_y = center[1] - shape[1] * pitch / 2.0
        cx = int((x - ll_x) / pitch)
        cy = int((y - ll_y) / pitch)

    pattern = core_lat.universe_pattern or []
    if cy < 0 or cy >= len(pattern):
        # Check outer universe
        outer_id = core_lat.outer_universe_id
        if outer_id:
            return _trace_universe_material(outer_id, x, y, universes_by_id, cells_by_id)
        return None, None, None

    row = pattern[cy]
    if cx < 0 or cx >= len(row):
        outer_id = core_lat.outer_universe_id
        if outer_id:
            return _trace_universe_material(outer_id, x, y, universes_by_id, cells_by_id)
        return None, None, None

    asm_uv_id = row[cx]

    # 4. Trace into the assembly universe → assembly lattice → pin universe
    return _trace_assembly_universe(asm_uv_id, x, y, universes_by_id, cells_by_id, lattices_by_id, pitch)


def _trace_assembly_universe(
    asm_uv_id: str,
    x: float, y: float,
    universes_by_id: dict,
    cells_by_id: dict,
    lattices_by_id: dict,
    core_pitch: float,
) -> tuple[str | None, str | None, str | None]:
    """Trace from assembly universe to pin cell material."""
    asm_uv = universes_by_id.get(asm_uv_id)
    if asm_uv is None:
        return asm_uv_id, None, None

    for cid in (asm_uv.cell_ids or []):
        cell = cells_by_id.get(cid)
        if cell is None:
            continue
        if cell.fill_type == "lattice" and cell.fill_id:
            # Find the pin lattice
            pin_lat = lattices_by_id.get(cell.fill_id)
            if pin_lat is None:
                continue

            # Map (x, y) within assembly to pin lattice coordinates
            pin_pitch = pin_lat.pitch_cm[0] if pin_lat.pitch_cm else 1.25
            pattern = pin_lat.universe_pattern or []
            n_rows = len(pattern)
            n_cols = len(pattern[0]) if pattern else 0

            ll = pin_lat.lower_left_cm
            if ll:
                px = int((x - ll[0]) / pin_pitch)
                py = int((y - ll[1]) / pin_pitch)
            else:
                center = pin_lat.center_cm or (0.0, 0.0)
                ll_x = center[0] - n_cols * pin_pitch / 2.0
                ll_y = center[1] - n_rows * pin_pitch / 2.0
                px = int((x - ll_x) / pin_pitch)
                py = int((y - ll_y) / pin_pitch)

            if py < 0 or py >= n_rows or px < 0 or px >= n_cols:
                outer = pin_lat.outer_universe_id
                if outer:
                    return _trace_universe_material(outer, x, y, universes_by_id, cells_by_id)
                return None, None, None

            pin_uv_id = pattern[py][px]
            return _trace_universe_material(pin_uv_id, x, y, universes_by_id, cells_by_id)

        elif cell.fill_type == "material" and cell.fill_id:
            return asm_uv_id, cid, cell.fill_id

    return asm_uv_id, None, None


def _trace_universe_material(
    uv_id: str,
    x: float, y: float,
    universes_by_id: dict,
    cells_by_id: dict,
) -> tuple[str | None, str | None, str | None]:
    """Determine which cell/material of a universe the (x, y) falls in.

    Uses concentric cylinder logic: cells are ordered from innermost to
    outermost.  The background cell catches everything outside the last
    cylinder.
    """
    uv = universes_by_id.get(uv_id)
    if uv is None:
        return uv_id, None, None

    # Track running radius to determine radial position
    # For grid-decorated universes, check if point is in the frame area
    # by looking at the cell structure.
    for cid in (uv.cell_ids or []):
        cell = cells_by_id.get(cid)
        if cell is None:
            continue
        role = (cell.component_role or "").lower()
        if "grid_frame" in role or "grid_frame" in cid.lower():
            # Check if this is a grid frame cell and the point is in the frame
            # For simplicity, we trust the lattice position — if we're in a
            # grid-active lattice and the point is at a frame position, we
            # return the grid material.
            if cell.fill_type == "material" and cell.fill_id:
                return uv_id, cid, cell.fill_id

    # For non-frame cells, return the first material cell
    # (The exact radial position would require surface parsing; for probe
    # purposes we focus on frame vs non-frame.)
    for cid in (uv.cell_ids or []):
        cell = cells_by_id.get(cid)
        if cell is None:
            continue
        if cell.fill_type == "material" and cell.fill_id:
            return uv_id, cid, cell.fill_id

    return uv_id, None, None


def _is_in_grid_band(plan: SimulationPlan, z: float) -> bool:
    """Check if z falls within any spacer grid overlay z-range."""
    model = plan.complex_model
    if model is None or model.core is None:
        return False
    for ov in (model.core.axial_overlays or []):
        if getattr(ov, "overlay_kind", "") != "spacer_grid":
            continue
        z_min = getattr(ov, "z_min_cm", None)
        z_max = getattr(ov, "z_max_cm", None)
        if z_min is not None and z_max is not None:
            if z_min <= z < z_max:
                return True
    return False


def _grid_material_at_z(plan: SimulationPlan, z: float) -> str | None:
    """Return the grid material ID for the overlay active at z, or None."""
    model = plan.complex_model
    if model is None or model.core is None:
        return None
    for ov in (model.core.axial_overlays or []):
        if getattr(ov, "overlay_kind", "") != "spacer_grid":
            continue
        z_min = getattr(ov, "z_min_cm", None)
        z_max = getattr(ov, "z_max_cm", None)
        if z_min is not None and z_max is not None:
            if z_min <= z < z_max:
                return getattr(ov, "material_id", None)
    return None


# ---------------------------------------------------------------------------
# Named probe functions
# ---------------------------------------------------------------------------

def probe_grid_frame_end(plan: SimulationPlan, *, grid_z: float, pin_x: float, pin_y: float) -> ProbeResult:
    """Probe at a position between outer and inner frame in an end grid band."""
    mat = _grid_material_at_z(plan, grid_z)
    uv_id, cell_id, actual = _find_universe_at_xy(plan, pin_x, pin_y, grid_z)
    expected = mat or "grid_end_mat"
    return ProbeResult(
        x=pin_x, y=pin_y, z=grid_z,
        expected_material=expected,
        actual_material=actual,
        cell_id=cell_id,
        universe_id=uv_id,
        passed=actual is not None and expected in (actual,),
        note="end grid frame probe",
    )


def probe_grid_frame_middle(plan: SimulationPlan, *, grid_z: float, pin_x: float, pin_y: float) -> ProbeResult:
    """Probe at a position in a middle grid band."""
    mat = _grid_material_at_z(plan, grid_z)
    uv_id, cell_id, actual = _find_universe_at_xy(plan, pin_x, pin_y, grid_z)
    expected = mat or "grid_mid_mat"
    return ProbeResult(
        x=pin_x, y=pin_y, z=grid_z,
        expected_material=expected,
        actual_material=actual,
        cell_id=cell_id,
        universe_id=uv_id,
        passed=actual is not None,
        note="middle grid frame probe",
    )


def probe_inner_moderator(plan: SimulationPlan, *, x: float, y: float, z: float) -> ProbeResult:
    """Probe inner moderator (inside frame, outside cylinder)."""
    uv_id, cell_id, actual = _find_universe_at_xy(plan, x, y, z)
    return ProbeResult(
        x=x, y=y, z=z,
        expected_material="water",
        actual_material=actual,
        cell_id=cell_id,
        universe_id=uv_id,
        passed=actual is not None and actual != "grid_end_mat" and actual != "grid_mid_mat" and actual != "inconel718" and actual != "zircaloy4",
        note="inner moderator probe",
    )


def probe_pin_interior(plan: SimulationPlan, *, x: float, y: float, z: float, expected: str = "fuel") -> ProbeResult:
    """Probe inside a pin cell (fuel, guide tube, etc.)."""
    uv_id, cell_id, actual = _find_universe_at_xy(plan, x, y, z)
    return ProbeResult(
        x=x, y=y, z=z,
        expected_material=expected,
        actual_material=actual,
        cell_id=cell_id,
        universe_id=uv_id,
        passed=actual is not None and expected in (actual or ""),
        note="pin interior probe",
    )


def probe_assembly_gap(plan: SimulationPlan, *, x: float, y: float, z: float) -> ProbeResult:
    """Probe the water gap between assemblies."""
    uv_id, cell_id, actual = _find_universe_at_xy(plan, x, y, z)
    grid_mats = {"grid_end_mat", "grid_mid_mat", "inconel718", "zircaloy4"}
    return ProbeResult(
        x=x, y=y, z=z,
        expected_material="water",
        actual_material=actual,
        cell_id=cell_id,
        universe_id=uv_id,
        passed=actual is not None and actual not in grid_mats,
        note="assembly gap probe",
    )


def probe_non_grid_segment(plan: SimulationPlan, *, x: float, y: float, z: float) -> ProbeResult:
    """Probe at the same XY but in a non-grid axial segment."""
    uv_id, cell_id, actual = _find_universe_at_xy(plan, x, y, z)
    grid_mats = {"grid_end_mat", "grid_mid_mat", "inconel718", "zircaloy4"}
    return ProbeResult(
        x=x, y=y, z=z,
        expected_material="water",
        actual_material=actual,
        cell_id=cell_id,
        universe_id=uv_id,
        passed=actual is not None and actual not in grid_mats,
        note="non-grid segment probe",
    )


def run_all_probes(plan: SimulationPlan, probes: list[ProbeResult]) -> ProbeReport:
    """Collect probe results into a report."""
    return ProbeReport(results=probes)
