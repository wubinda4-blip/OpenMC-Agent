"""Deterministic id-reference repair for repeated-geometry plans.

Produces RFC 6902 JSON Patch ops that fix *uniquely-solvable* id-reference
typos in a ``SimulationPlan``'s ``complex_model``: a ``cell.fill_id``, a core
``lattice_id``, an ``universe.cell_ids`` entry, a ``lattice.universe_pattern`` entry,
or a missing empty assembly wrapper universe for a core lattice; a
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

from openmc_agent.lattice_validation import (
    canonical_pin_map_rows,
    is_structural_error_confirmation,
    lattice_id_from_schema_path,
)
from openmc_agent.schemas import AssemblySpec, SimulationPlan, ValidationIssue


# Codes whose only fix is renaming a typo'd reference to an existing id.
# Used purely for the fast-exit check; the repair itself re-analyzes the plan.
_ID_REF_CODES = frozenset({
    "cell.material_ref_missing",
    "cell.region_ref_missing",
    "cell.universe_ref_missing",
    "cell.lattice_ref_missing",
    "core.lattice_ref_missing",
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


def _associate_assembly_wrapper(
    universe_id: str,
    assemblies: list[AssemblySpec],
) -> int | None:
    """Return the index of the assembly a core-lattice wrapper reference points at.

    The LLM often names the wrapper universe after the assembly lattice id or
    assembly id rather than copying assembly.id exactly -- e.g. it writes
    ``uo2_assembly_univ`` in ``core_lattice.universe_pattern`` while
    ``assembly.lattice_id`` is ``uo2_assembly`` and ``assembly.id`` is
    ``uo2_assy``. This returns the unique assembly whose ``lattice_id`` or
    ``id`` is a substring of ``universe_id``, so the caller can add the wrapper
    universe and rename ``assembly.id`` to match the reference. Returns
    ``None`` on zero or ambiguous matches so the caller falls back to
    edit-distance repair.
    """
    candidates: list[int] = []
    for idx, assembly in enumerate(assemblies):
        if assembly.lattice_id and assembly.lattice_id in universe_id:
            candidates.append(idx)
        elif assembly.id and assembly.id in universe_id:
            candidates.append(idx)
    if len(candidates) == 1:
        return candidates[0]
    return None


def auto_repair_lattice_structure(
    plan: SimulationPlan,
    issues: list[ValidationIssue] | None = None,
    *,
    requirement: str = "",
) -> list[dict[str, Any]] | None:
    """Return RFC 6902 patch ops for deterministic lattice repairs.

    Two repair families, both applied as RFC 6902 ops:

    1. Uniquely-solvable id-reference typos (cell.fill_id, universe.cell_ids,
       lattice.universe_pattern entries, etc.).
    2. Pin-count mismatch on a lattice whose canonical pin map is carried in
       ``requirement``: the parsed canonical grid is the ground truth and
       overwrites the LLM's expanded ``universe_pattern`` directly. The LLM
       cannot reliably hand-edit a dense matrix even with cell-level
       coordinates (C5G7 case3: three reflections returned a byte-identical
       wrong 17x17), so this deterministic overwrite is the reliable fix.

    Returns ``None`` when no deterministic repair applies (so the caller knows
    to fall through to the LLM path). When ``issues`` is provided and none
    carry a repairable code, returns ``None`` immediately without scanning.
    """
    model = plan.complex_model
    if model is None:
        return None
    has_id_ref = issues is not None and any(
        issue.code in _ID_REF_CODES for issue in issues
    )
    has_pin_mismatch = issues is not None and any(
        issue.code == "lattice.pin_count_mismatch" for issue in issues
    )
    if (
        issues is not None
        and not has_id_ref
        and not (has_pin_mismatch and requirement)
    ):
        return None

    material_ids = {material.id for material in model.materials}
    cell_ids = {cell.id for cell in model.cells}
    universe_ids = {universe.id for universe in model.universes}
    region_ids = {region.id for region in model.regions}
    lattice_ids = {lattice.id for lattice in model.lattices}
    surface_ids = {surface.id for surface in model.surfaces}
    assembly_lattice_by_id = {
        assembly.id: assembly.lattice_id
        for assembly in model.assemblies
        if assembly.lattice_id in lattice_ids
    }

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

    # Lattice universe_pattern -> universe ids.  For core lattices, a pattern
    # may intentionally reference assembly wrapper universes. Two naming shapes
    # are supported:
    # 1. the reference equals an AssemblySpec id -> add the empty wrapper shell;
    # 2. the reference embeds the assembly lattice_id/id (e.g. 'uo2_assembly_univ'
    #    for lattice_id 'uo2_assembly') but differs from assembly.id -> add the
    #    wrapper AND rename assembly.id to the referenced name, otherwise the
    #    core renderer's assembly.id lookup misses and the plan loops.
    missing_assembly_universes: list[str] = []
    seen_missing_assembly_universes: set[str] = set()
    assembly_renames: dict[int, str] = {}
    for i, lattice in enumerate(model.lattices):
        for r, row in enumerate(lattice.universe_pattern):
            for c, universe_id in enumerate(row):
                if universe_id in universe_ids:
                    continue
                if universe_id in assembly_lattice_by_id:
                    if universe_id not in seen_missing_assembly_universes:
                        seen_missing_assembly_universes.add(universe_id)
                        missing_assembly_universes.append(universe_id)
                    continue
                assoc_idx = _associate_assembly_wrapper(universe_id, model.assemblies)
                if assoc_idx is not None:
                    if universe_id not in seen_missing_assembly_universes:
                        seen_missing_assembly_universes.add(universe_id)
                        missing_assembly_universes.append(universe_id)
                        assembly_renames[assoc_idx] = universe_id
                    continue
                replace_if_resolved(
                    f"/complex_model/lattices/{i}/universe_pattern/{r}/{c}",
                    universe_id,
                    universe_ids,
                )
    for offset, universe_id in enumerate(missing_assembly_universes):
        ops.append({
            "op": "add",
            "path": f"/complex_model/universes/{len(model.universes) + offset}",
            "value": {
                "id": universe_id,
                "name": universe_id,
                "cell_ids": [],
                "purpose": (
                    "Auto-added empty assembly wrapper universe for a core lattice "
                    f"reference to AssemblySpec {universe_id!r}."
                ),
            },
        })
    for assembly_idx, new_id in assembly_renames.items():
        if model.assemblies[assembly_idx].id != new_id:
            ops.append({
                "op": "replace",
                "path": f"/complex_model/assemblies/{assembly_idx}/id",
                "value": new_id,
            })

    loading_ids = {loading.id for loading in model.lattice_loadings}

    # Core lattice_id + axial-layer fill.id + loading_id.
    if model.core is not None:
        if model.core.lattice_id is not None and model.core.lattice_id not in lattice_ids:
            replace_if_resolved(
                "/complex_model/core/lattice_id",
                model.core.lattice_id,
                lattice_ids,
            )
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

    # Pin-count mismatch: overwrite universe_pattern from the requirement's
    # canonical pin map. Only lattices the caller flagged (via a mismatch issue
    # with a schema_path) are touched, and only when the canonical shape matches
    # exactly -- never overwrite a lattice that was not reported as mismatched.
    if has_pin_mismatch and requirement:
        mismatched_ids = {
            lattice_id_from_schema_path(issue.schema_path)
            for issue in issues or []
            if issue.code == "lattice.pin_count_mismatch"
        }
        for index, lattice in enumerate(model.lattices):
            if lattice.id not in mismatched_ids:
                continue
            rows = canonical_pin_map_rows(lattice, requirement)
            if rows is not None:
                ops.append(
                    {
                        "op": "replace",
                        "path": f"/complex_model/lattices/{index}/universe_pattern",
                        "value": rows,
                    }
                )
                # Drop any stale structural confirmation on this lattice (e.g.
                # "pin count mismatch ..."): the pattern is now canonical, and a
                # stale confirmation would be re-asked by ask_expert, whose
                # feedback can trigger regenerate_plan and discard this fix.
                existing = list(lattice.requires_human_confirmation or [])
                kept = [
                    item
                    for item in existing
                    if not is_structural_error_confirmation(item)
                ]
                if kept != existing:
                    ops.append(
                        {
                            "op": "replace",
                            "path": f"/complex_model/lattices/{index}/requires_human_confirmation",
                            "value": kept,
                        }
                    )

    return ops or None


def _encode_json_pointer_part(part: str) -> str:
    return part.replace("~", "~0").replace("/", "~1")
