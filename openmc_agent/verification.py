"""Expert verification digest for rendered models (reactor-type agnostic).

A structured report a human expert can scan in seconds to judge whether a
rendered model is structurally correct, complementing the visual plots:

* pin-map counts (vs expected_counts when the plan states them),
* axial-layer table (z-range, fill, contains-fuel-lattice flag),
* spacer/overlay placement (z-range, target, material, mode, rendered segments),
* material roster (density, nuclides, reachable from the geometry),
* bounds summary (geometry / source / active-fuel / symmetry),
* an invariant checklist (fuel in active layers, source overlaps fuel, all
  universes/materials defined, no material-slab grid, full assembly).

Everything is derived from the IR + rendered geometry metadata -- no benchmark
facts, no reactor-type assumptions.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from openmc_agent.axial_overlay import (
    classify_material_role,
    compute_axial_segments,
    overlay_is_structurally_renderable,
    universe_open_cell_ids,
)
from openmc_agent.geometry_bounds import (
    GeometryBounds,
    compute_geometry_bounds,
    infer_symmetry_policy,
)
from openmc_agent.schemas import ComplexModelSpec
from openmc_agent.source_settings import (
    active_fuel_z_bounds,
    fuel_material_ids,
    source_bounds_for_plan,
)

__all__ = ["build_verification_digest", "write_verification_digest"]


def _pin_counts(model: ComplexModelSpec) -> dict[str, int]:
    counts: dict[str, int] = {}
    for lat in model.lattices:
        for row in lat.universe_pattern:
            for uid in row:
                counts[uid] = counts.get(uid, 0) + 1
    return counts


def _expected_counts(model: ComplexModelSpec) -> dict[str, dict[str, int]]:
    out: dict[str, dict[str, int]] = {}
    for lat in model.lattices:
        if lat.expected_counts:
            out[lat.id] = dict(lat.expected_counts)
    return out


def _axial_layer_rows(model: ComplexModelSpec) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if model.core is None:
        return rows
    for layer in model.core.axial_layers:
        rows.append({
            "id": layer.id,
            "z_min_cm": layer.z_min_cm,
            "z_max_cm": layer.z_max_cm,
            "height_cm": round(layer.z_max_cm - layer.z_min_cm, 4),
            "fill_type": layer.fill.type,
            "fill_id": layer.fill.id,
            "contains_fuel_lattice": layer.fill.type == "lattice",
        })
    return rows


def _overlay_rows(model: ComplexModelSpec) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if model.core is None:
        return rows
    segments = compute_axial_segments(model)
    seg_count_by_overlay: dict[str, int] = {}
    for seg in segments:
        if seg.overlay is not None:
            seg_count_by_overlay[seg.overlay.id] = seg_count_by_overlay.get(seg.overlay.id, 0) + 1
    for ov in model.core.axial_overlays:
        rows.append({
            "id": ov.id,
            "kind": ov.overlay_kind,
            "z_min_cm": ov.z_min_cm,
            "z_max_cm": ov.z_max_cm,
            "target_lattice_id": ov.target_lattice_id,
            "material_id": ov.material_id,
            "geometry_mode": ov.geometry_mode,
            "through_path_preserved": ov.through_path_preserved,
            "renderable": overlay_is_structurally_renderable(ov, model),
            "rendered_segments": seg_count_by_overlay.get(ov.id, 0),
        })
    return rows


def _material_roster(model: ComplexModelSpec) -> list[dict[str, Any]]:
    reachable_material_ids: set[str] = set()
    for cell in model.cells:
        if cell.fill_type == "material" and cell.fill_id:
            reachable_material_ids.add(cell.fill_id)
    # materials reachable via any lattice universe's cells
    universes_by_id = {u.id: u for u in model.universes}
    cells_by_id = {c.id: c for c in model.cells}
    for lat in model.lattices:
        for row in lat.universe_pattern:
            for uid in row:
                u = universes_by_id.get(uid)
                if not u:
                    continue
                for cid in u.cell_ids:
                    c = cells_by_id.get(cid)
                    if c and c.fill_type == "material" and c.fill_id:
                        reachable_material_ids.add(c.fill_id)
    roster: list[dict[str, Any]] = []
    for m in model.materials:
        roster.append({
            "id": m.id,
            "name": m.name,
            "density": f"{m.density_value} {m.density_unit}" if m.density_value else None,
            "nuclides": [n.name for n in m.composition] or (
                [f"macro:{m.macroscopic}"] if m.macroscopic else (
                    [f"formula:{m.chemical_formula}"] if m.chemical_formula else []
                )
            ),
            "role": classify_material_role(m),
            "reachable_from_geometry": m.id in reachable_material_ids,
        })
    return roster


def _bounds_summary(model: ComplexModelSpec) -> dict[str, Any]:
    gb: GeometryBounds | None = compute_geometry_bounds(model)
    policy = infer_symmetry_policy(model, gb)
    sb = source_bounds_for_plan(model)
    af = active_fuel_z_bounds(model)
    summary: dict[str, Any] = {
        "symmetry": policy.mode,
        "lattice_shape": [gb.lattice_rows, gb.lattice_cols] if gb else None,
    }
    if gb is not None:
        summary["geometry_xy_cm"] = [gb.geom_x_min, gb.geom_x_max, gb.geom_y_min, gb.geom_y_max]
        summary["axial_domain_cm"] = [gb.geom_z_min, gb.geom_z_max]
    if af is not None:
        summary["active_fuel_z_cm"] = [af[0], af[1]]
    if sb is not None:
        summary["source_xy_cm"] = [sb.x_min, sb.x_max, sb.y_min, sb.y_max]
        summary["source_z_cm"] = [sb.z_min, sb.z_max]
        summary["source_bound_to_active_fuel"] = sb.z_bound_to_active_fuel
    return summary


def _checks(model: ComplexModelSpec) -> list[dict[str, Any]]:
    """Pass/fail invariant checklist. Each entry: {check, status, detail}."""
    results: list[dict[str, Any]] = []
    tol = 1e-6

    def add(check: str, ok: bool | None, detail: str = "") -> None:
        results.append({"check": check, "status": {True: "pass", False: "fail"}.get(ok, "warn"), "detail": detail})

    # 1. all lattice universes defined
    defined = {u.id for u in model.universes}
    for lat in model.lattices:
        missing = sorted({uid for row in lat.universe_pattern for uid in row} - defined)
        add(f"lattice '{lat.id}' universes defined", not missing,
            f"missing: {missing}" if missing else f"{len(defined)} universes")

    # 2. fuel material exists and is reachable
    fmat = fuel_material_ids(model)
    add("fuel material present", bool(fmat), f"fuel material ids: {sorted(fmat)}" if fmat else "none")
    roster = _material_roster(model)
    unreachable_fuel = [m["id"] for m in roster if m["id"] in fmat and not m["reachable_from_geometry"]]
    add("fuel material reachable from geometry", not unreachable_fuel,
        f"unreachable: {unreachable_fuel}" if unreachable_fuel else "reachable")

    # 3. fuel lattice in an axial layer
    has_fuel_layer = (model.core is not None and any(
        L.fill.type == "lattice" for L in model.core.axial_layers))
    add("active-fuel lattice layer present", has_fuel_layer)

    # 4. source overlaps active fuel
    sb = source_bounds_for_plan(model)
    af = active_fuel_z_bounds(model)
    if sb is not None and af is not None:
        overlap = sb.z_min < af[1] - tol and sb.z_max > af[0] + tol
        add("source z overlaps active fuel", overlap,
            f"source z=[{sb.z_min},{sb.z_max}] vs active fuel=[{af[0]},{af[1]}]")
    elif sb is not None:
        add("source z overlaps active fuel", None, "no active-fuel layer to compare")

    # 5. all overlay materials defined
    mat_ids = {m.id for m in model.materials}
    if model.core is not None:
        for ov in model.core.axial_overlays:
            if ov.material_id and ov.material_id not in mat_ids:
                add(f"overlay '{ov.id}' material defined", False, f"missing {ov.material_id}")
        # 6. no axial layer is a grid material slab
        grid_mats = {m.id for m in model.materials
                     if classify_material_role(m) == "protected"
                     and any(t in (m.id + m.name).lower() for t in ("grid", "inconel"))}
        slab = [L.id for L in model.core.axial_layers
                if L.fill.type == "material" and L.fill.id in grid_mats]
        add("no grid material-slab axial layer", not slab, f"slab layers: {slab}" if slab else "")

    # 7. full assembly (not quarter)
    policy = infer_symmetry_policy(model, compute_geometry_bounds(model))
    add("full assembly (no quarter symmetry)", policy.mode == "full", f"mode={policy.mode}")

    # 8. pin counts vs expected_counts
    for lat in model.lattices:
        if not lat.expected_counts:
            continue
        actual = _pin_counts(model)
        mismatches = {uid: (exp, actual.get(uid, 0)) for uid, exp in lat.expected_counts.items()
                      if actual.get(uid, 0) != exp}
        add(f"lattice '{lat.id}' pin counts match expected", not mismatches,
            f"mismatch: {mismatches}" if mismatches else "all match")

    return results


def build_verification_digest(model: ComplexModelSpec) -> dict[str, Any]:
    """Build the structured verification digest for a rendered model."""
    return {
        "model_name": model.name,
        "kind": model.kind,
        "pin_counts": _pin_counts(model),
        "expected_counts": _expected_counts(model),
        "axial_layers": _axial_layer_rows(model),
        "overlays": _overlay_rows(model),
        "materials": _material_roster(model),
        "bounds": _bounds_summary(model),
        "checks": _checks(model),
    }


def _markdown(digest: dict[str, Any]) -> str:
    lines: list[str] = [f"# Verification digest — {digest['model_name']}", "",
                        f"kind: `{digest['kind']}`", ""]

    # Checks first (the quick pass/fail scan)
    lines += ["## Invariant checks", "",
              "| check | status | detail |", "|---|---|---|"]
    for c in digest["checks"]:
        lines.append(f"| {c['check']} | {c['status']} | {c['detail']} |")
    lines.append("")

    # Pin counts
    lines += ["## Pin counts", "",
              "| universe | count |", "|---|---|"]
    for uid, n in sorted(digest["pin_counts"].items()):
        lines.append(f"| {uid} | {n} |")
    lines.append("")

    # Axial layers
    if digest["axial_layers"]:
        lines += ["## Axial layers", "",
                  "| id | z_min | z_max | height | fill | fuel-lattice |", "|---|---|---|---|---|---|"]
        for r in digest["axial_layers"]:
            lines.append(f"| {r['id']} | {r['z_min_cm']} | {r['z_max_cm']} | {r['height_cm']} | "
                         f"{r['fill_type']}:{r['fill_id']} | {r['contains_fuel_lattice']} |")
        lines.append("")

    # Overlays
    if digest["overlays"]:
        lines += ["## Overlays", "",
                  "| id | z_min | z_max | target | material | mode | renderable | segments |",
                  "|---|---|---|---|---|---|---|---|"]
        for r in digest["overlays"]:
            lines.append(f"| {r['id']} | {r['z_min_cm']} | {r['z_max_cm']} | {r['target_lattice_id']} | "
                         f"{r['material_id']} | {r['geometry_mode']} | {r['renderable']} | {r['rendered_segments']} |")
        lines.append("")

    # Materials
    lines += ["## Materials", "",
              "| id | name | density | role | reachable | nuclides |", "|---|---|---|---|---|---|"]
    for m in digest["materials"]:
        lines.append(f"| {m['id']} | {m['name']} | {m['density']} | {m['role']} | "
                     f"{m['reachable_from_geometry']} | {', '.join(m['nuclides'])} |")
    lines.append("")

    # Bounds
    b = digest["bounds"]
    lines += ["## Bounds", "", f"- symmetry: `{b.get('symmetry')}`",
              f"- lattice shape: {b.get('lattice_shape')}",
              f"- geometry xy cm: {b.get('geometry_xy_cm')}",
              f"- axial domain cm: {b.get('axial_domain_cm')}",
              f"- active fuel z cm: {b.get('active_fuel_z_cm')}",
              f"- source xy cm: {b.get('source_xy_cm')}",
              f"- source z cm: {b.get('source_z_cm')}  (bound to active fuel: {b.get('source_bound_to_active_fuel')})",
              ""]
    return "\n".join(lines)


def write_verification_digest(model: ComplexModelSpec, outdir: Path) -> dict[str, str]:
    """Write ``verification_digest.json`` + ``verification_digest.md`` to outdir.

    Returns the written file paths.
    """
    digest = build_verification_digest(model)
    json_path = outdir / "verification_digest.json"
    md_path = outdir / "verification_digest.md"
    json_path.write_text(json.dumps(digest, ensure_ascii=False, indent=2), encoding="utf-8")
    md_path.write_text(_markdown(digest), encoding="utf-8")
    return {"json": str(json_path), "markdown": str(md_path)}
