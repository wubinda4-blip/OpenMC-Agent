"""Deterministic id-reference repair for repeated-geometry plans.

Produces RFC 6902 JSON Patch ops that fix *uniquely-solvable* id-reference
typos in a ``SimulationPlan``'s ``complex_model``: a ``cell.fill_id``, an
``universe.cell_ids`` entry, a ``lattice.universe_pattern`` entry, a
``region.surface_ids`` entry, a ``cell.region_id``, an axial-layer
``fill.id`` / ``loading_id``, or a lattice-loading reference that points at an
id which does not exist but resolves to exactly
one candidate by prefix/suffix or edit distance.

Design constraints:

* **Only unique-solution cases.**  Multi-solution and no-match references are
  left to the LLM (``reflect_plan`` / investigation). Pin-count and shape /
  dimension mismatches are NOT touched (user decision: id references only).
* **Never corrupts the plan.**  This module only produces patch ops; the caller
  applies them via ``_apply_json_patches`` + ``SimulationPlan.model_validate``,
  so a bad match is rejected and the patch path falls back.
* **Re-analyzes the plan directly** rather than parsing free-text diagnostics,
  so it does not depend on renderer message wording. The optional ``issues``
  argument only gates a fast-exit when there is clearly nothing to do.
"""

from __future__ import annotations

from typing import Any

from openmc_agent.schemas import SimulationPlan, ValidationIssue


# Codes whose only fix is renaming a typo'd reference to an existing id.
# Used purely for the fast-exit check; the repair itself re-analyzes the plan.
_ID_REF_CODES = frozenset({
    "cell.material_ref_missing",
    "cell.region_ref_missing",
    "cell.universe_ref_missing",
    "cell.lattice_ref_missing",
    "universe.cell_ref_missing",
    "region.surface_ref_missing",
    "lattice.universe_ref_missing",
    "axial_layer.fill_ref_missing",
    "axial_layer.loading_ref_missing",
    "lattice_loading.base_ref_missing",
    "lattice_loading.override_universe_ref_missing",
})


def _edit_distance(a: str, b: str) -> int:
    """Levenshtein edit distance (case-sensitive)."""
    if a == b:
        return 0
    if not a:
        return len(b)
    if not b:
        return len(a)
    previous = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        current = [i]
        for j, cb in enumerate(b, 1):
            cost = 0 if ca == cb else 1
            current.append(min(current[-1] + 1, previous[j] + 1, previous[j - 1] + cost))
        previous = current
    return previous[-1]


def _resolve_id(typo: str, pool: set[str]) -> str | None:
    """Return the unique matching id for ``typo`` in ``pool``, or None.

    Resolution order:
    1. exact match (defensive -- a *missing* ref should not match exactly);
    2. unique prefix/suffix overlap (typo truncates or extends exactly one id);
    3. unique nearest by edit distance within a small tolerance.
    """
    if not pool:
        return None
    if typo in pool:
        return typo
    boundary_hits = {
        pid
        for pid in pool
        if pid.startswith(typo)
        or typo.startswith(pid)
        or pid.endswith(typo)
        or typo.endswith(pid)
    }
    if len(boundary_hits) == 1:
        return next(iter(boundary_hits))
    tolerance = max(2, len(typo) // 4)
    scored = sorted((_edit_distance(typo, pid), pid) for pid in pool)
    if scored and scored[0][0] <= tolerance:
        best_distance, best_id = scored[0]
        # Unique: the runner-up must be strictly worse (or absent).
        if len(scored) == 1 or scored[1][0] > best_distance:
            return best_id
    return None


def auto_repair_lattice_structure(
    plan: SimulationPlan,
    issues: list[ValidationIssue] | None = None,
) -> list[dict[str, Any]] | None:
    """Return RFC 6902 patch ops for uniquely-solvable id-reference typos.

    Returns ``None`` when no deterministic repair applies (so the caller knows
    to fall through to the LLM path). When ``issues`` is provided and none carry
    an id-reference code, returns ``None`` immediately without scanning.
    """
    model = plan.complex_model
    if model is None:
        return None
    if issues is not None and not any(issue.code in _ID_REF_CODES for issue in issues):
        return None

    material_ids = {material.id for material in model.materials}
    cell_ids = {cell.id for cell in model.cells}
    universe_ids = {universe.id for universe in model.universes}
    region_ids = {region.id for region in model.regions}
    lattice_ids = {lattice.id for lattice in model.lattices}
    surface_ids = {surface.id for surface in model.surfaces}

    fill_pools = {
        "material": material_ids,
        "universe": universe_ids,
        "lattice": lattice_ids,
    }

    ops: list[dict[str, Any]] = []

    def replace_if_resolved(path: str, current: str, pool: set[str]) -> None:
        resolved = _resolve_id(current, pool)
        if resolved is not None and resolved != current:
            ops.append({"op": "replace", "path": path, "value": resolved})

    # Cells: region_id + fill_id (material/universe/lattice).
    for i, cell in enumerate(model.cells):
        if cell.region_id is not None and cell.region_id not in region_ids:
            replace_if_resolved(
                f"/complex_model/cells/{i}/region_id", cell.region_id, region_ids
            )
        if cell.fill_type == "void" or not cell.fill_id:
            continue
        pool = fill_pools.get(cell.fill_type)
        if pool is None or cell.fill_id in pool:
            continue
        replace_if_resolved(
            f"/complex_model/cells/{i}/fill_id", cell.fill_id, pool
        )

    # Universe -> cell ids.
    for i, universe in enumerate(model.universes):
        for j, cell_id in enumerate(universe.cell_ids):
            if cell_id in cell_ids:
                continue
            replace_if_resolved(
                f"/complex_model/universes/{i}/cell_ids/{j}", cell_id, cell_ids
            )

    # Region -> surface ids.
    for i, region in enumerate(model.regions):
        for j, surface_id in enumerate(region.surface_ids):
            if surface_id in surface_ids:
                continue
            replace_if_resolved(
                f"/complex_model/regions/{i}/surface_ids/{j}", surface_id, surface_ids
            )

    # Lattice universe_pattern -> universe ids.
    for i, lattice in enumerate(model.lattices):
        for r, row in enumerate(lattice.universe_pattern):
            for c, universe_id in enumerate(row):
                if universe_id in universe_ids:
                    continue
                replace_if_resolved(
                    f"/complex_model/lattices/{i}/universe_pattern/{r}/{c}",
                    universe_id,
                    universe_ids,
                )

    loading_ids = {loading.id for loading in model.lattice_loadings}

    # Axial-layer fill.id + loading_id.
    if model.core is not None:
        for i, layer in enumerate(model.core.axial_layers):
            if layer.fill.type != "void" and layer.fill.id:
                pool = fill_pools.get(layer.fill.type)
                if pool is not None and layer.fill.id not in pool:
                    replace_if_resolved(
                        f"/complex_model/core/axial_layers/{i}/fill/id",
                        layer.fill.id,
                        pool,
                    )
            if layer.loading_id is not None and layer.loading_id not in loading_ids:
                replace_if_resolved(
                    f"/complex_model/core/axial_layers/{i}/loading_id",
                    layer.loading_id,
                    loading_ids,
                )

    # Lattice loading base_lattice_id + override universe ids.
    for i, loading in enumerate(model.lattice_loadings):
        if loading.base_lattice_id not in lattice_ids:
            replace_if_resolved(
                f"/complex_model/lattice_loadings/{i}/base_lattice_id",
                loading.base_lattice_id,
                lattice_ids,
            )
        for universe_id in loading.overrides:
            if universe_id in universe_ids:
                continue
            resolved = _resolve_id(universe_id, universe_ids)
            if resolved is not None and resolved != universe_id:
                value = loading.overrides[universe_id]
                old_key = _encode_json_pointer_part(universe_id)
                new_key = _encode_json_pointer_part(resolved)
                ops.append({
                    "op": "remove",
                    "path": f"/complex_model/lattice_loadings/{i}/overrides/{old_key}",
                })
                ops.append({
                    "op": "add",
                    "path": f"/complex_model/lattice_loadings/{i}/overrides/{new_key}",
                    "value": value,
                })

    return ops or None


def _encode_json_pointer_part(part: str) -> str:
    return part.replace("~", "~0").replace("/", "~1")
