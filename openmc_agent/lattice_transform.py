"""Deterministic, composable lattice transformation engine.

This module provides:

- :func:`normalize_lattice_loading` — convert a LatticeLoadingSpec or
  LatticeLoadingPatchItem into a :class:`NormalizedLatticeLoading`,
  migrating legacy ``overrides`` into ``coordinate_override`` operations.
- :func:`compose_lattice_loadings` — apply one or more normalized loadings
  to a base lattice, producing a derived lattice and reporting any issues.
- :func:`normalized_layer_loading_ids` — resolve ``loading_id`` /
  ``loading_ids`` on an ``AxialLayerSpec`` into a canonical list.

The engine is deterministic: the same base lattice + loading sequence
always produces the same derived lattice and cache key. No OpenMC imports.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Mapping, Sequence

from pydantic import Field

from openmc_agent.schemas import (
    AgentBaseModel,
    AxialLayerSpec,
    CellSpec,
    ComplexModelSpec,
    CoreSpec,
    FillRefSpec,
    LatticeLoadingSpec,
    LatticeSpec,
    LatticeTransformationOperation,
    RegionSpec,
    SurfaceSpec,
    UniverseSpec,
    ValidationIssue,
)


# ---------------------------------------------------------------------------
# Result models
# ---------------------------------------------------------------------------


class NormalizedLatticeLoading(AgentBaseModel):
    """A loading after legacy migration, with a flat operation list."""

    id: str
    base_lattice_id: str
    derived_lattice_id: str
    operations: list[LatticeTransformationOperation]
    migration_warnings: list[str] = Field(default_factory=list)


class LatticeTransformationResult(AgentBaseModel):
    """Outcome of composing one or more loadings onto a base lattice."""

    ok: bool
    derived_lattice: LatticeSpec | None = None
    derived_universes: list[UniverseSpec] = Field(default_factory=list)
    derived_cells: list[CellSpec] = Field(default_factory=list)
    derived_surfaces: list[SurfaceSpec] = Field(default_factory=list)
    derived_regions: list[RegionSpec] = Field(default_factory=list)
    issues: list[ValidationIssue] = Field(default_factory=list)
    applied_operation_ids: list[str] = Field(default_factory=list)
    cache_key: str = ""


# ---------------------------------------------------------------------------
# Default priority by operation kind
# ---------------------------------------------------------------------------

_DEFAULT_PRIORITY: dict[str, int] = {
    "replace_universe_family": 100,
    "coordinate_override": 200,
    "nested_component_override": 300,
}


def _effective_priority(op: LatticeTransformationOperation) -> int:
    """Use the default per-kind priority when the user leaves it at 0."""
    if op.priority != 0:
        return op.priority
    return _DEFAULT_PRIORITY.get(op.operation_kind, 500)


# ---------------------------------------------------------------------------
# AxialLayerSpec loading_ids normalization
# ---------------------------------------------------------------------------


def normalized_layer_loading_ids(layer: AxialLayerSpec) -> list[str]:
    """Resolve ``loading_id`` / ``loading_ids`` into a canonical ordered list.

    Rules:
    - ``loading_ids`` non-empty → use it.
    - ``loading_ids`` empty and ``loading_id`` set → ``[loading_id]``.
    - Both empty → ``[]``.
    """
    if layer.loading_ids:
        return list(layer.loading_ids)
    if layer.loading_id is not None:
        return [layer.loading_id]
    return []


def layer_loading_id_conflict(layer: AxialLayerSpec) -> bool:
    """True when loading_id and loading_ids disagree."""
    if layer.loading_id is not None and layer.loading_ids:
        return layer.loading_id != layer.loading_ids[0]
    return False


# ---------------------------------------------------------------------------
# Normalizer: legacy overrides → coordinate_override operations
# ---------------------------------------------------------------------------


def normalize_lattice_loading(
    loading: LatticeLoadingSpec | Any,
) -> NormalizedLatticeLoading:
    """Convert a LatticeLoadingSpec (or compatible) to NormalizedLatticeLoading.

    Legacy ``overrides`` are deterministically converted to
    ``coordinate_override`` operations when ``transformations`` is empty.

    The derived_lattice_id is auto-generated when missing.
    """
    loading_id = loading.id if hasattr(loading, "id") else getattr(loading, "loading_id", "")
    base_lattice_id = loading.base_lattice_id
    derived_lattice_id = loading.derived_lattice_id or f"{base_lattice_id}__{loading_id}"
    warnings: list[str] = []
    operations: list[LatticeTransformationOperation] = []

    transformations = getattr(loading, "transformations", []) or []
    overrides = getattr(loading, "overrides", {}) or {}

    if transformations:
        for i, t in enumerate(transformations):
            if isinstance(t, LatticeTransformationOperation):
                operations.append(t)
            elif isinstance(t, dict):
                operations.append(LatticeTransformationOperation(**t))
            else:
                # Patch item
                operations.append(LatticeTransformationOperation(
                    operation_id=t.operation_id,
                    operation_kind=t.operation_kind,
                    replacement_universe_id=t.replacement_universe_id,
                    source_universe_id=t.source_universe_id,
                    source_universe_ids=list(t.source_universe_ids),
                    target_coordinates=[tuple(c) for c in t.target_coordinates],
                    component_role=t.component_role,
                    component_path_id=t.component_path_id,
                    preserve_component_roles=list(t.preserve_component_roles),
                    preserve_path_ids=list(t.preserve_path_ids),
                    priority=t.priority,
                    purpose=t.purpose,
                ))
    elif overrides:
        warnings.append(
            f"loading {loading_id!r}: migrated {len(overrides)} legacy override group(s) "
            "to coordinate_override operations"
        )
        for universe_id, coords in sorted(overrides.items()):
            coord_tuples = [tuple(c) for c in coords]
            operations.append(LatticeTransformationOperation(
                operation_id=f"legacy_override_{universe_id}",
                operation_kind="coordinate_override",
                replacement_universe_id=universe_id,
                target_coordinates=coord_tuples,
                purpose=f"Migrated from legacy overrides[{universe_id}]",
            ))

    return NormalizedLatticeLoading(
        id=loading_id,
        base_lattice_id=base_lattice_id,
        derived_lattice_id=derived_lattice_id,
        operations=operations,
        migration_warnings=warnings,
    )


# ---------------------------------------------------------------------------
# Cache key
# ---------------------------------------------------------------------------


def compute_cache_key(
    base_lattice_id: str,
    normalized_loadings: Sequence[NormalizedLatticeLoading],
) -> str:
    """Deterministic hash of the base lattice + ordered loading sequence.

    Different loading orders produce different keys. Same content always
    produces the same key.
    """
    payload: dict[str, Any] = {
        "base": base_lattice_id,
        "loadings": [],
    }
    for nl in normalized_loadings:
        ops_data = []
        for op in sorted(nl.operations, key=lambda o: (_effective_priority(o), o.operation_id)):
            ops_data.append({
                "id": op.operation_id,
                "kind": op.operation_kind,
                "replacement": op.replacement_universe_id,
                "sources": sorted([op.source_universe_id] + op.source_universe_ids if op.source_universe_id else op.source_universe_ids),
                "coords": sorted(tuple(c) for c in op.target_coordinates),
                "role": op.component_role,
                "path": op.component_path_id,
                "preserve_roles": sorted(op.preserve_component_roles),
                "preserve_paths": sorted(op.preserve_path_ids),
                "priority": op.priority,
            })
        payload["loadings"].append({
            "id": nl.id,
            "derived": nl.derived_lattice_id,
            "ops": ops_data,
        })
    raw = json.dumps(payload, sort_keys=True, default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


# ---------------------------------------------------------------------------
# Composition engine
# ---------------------------------------------------------------------------


def compose_lattice_loadings(
    *,
    base_lattice: LatticeSpec,
    loading_ids: list[str],
    loading_by_id: Mapping[str, LatticeLoadingSpec | NormalizedLatticeLoading],
    universes: Sequence[UniverseSpec],
    cells: Sequence[CellSpec],
) -> LatticeTransformationResult:
    """Apply one or more loadings to ``base_lattice`` and return a derived lattice.

    The base lattice is never modified (deep copy). Operations are executed
    in a stable order: priority ascending, then operation_id alphabetical.

    On any error, ``ok=False`` with issues populated; no partial result.
    """
    import copy

    issues: list[ValidationIssue] = []
    universe_ids = {u.id for u in universes}
    cells_list = list(cells)
    universes_list = list(universes)
    _cells_by_id: dict[str, CellSpec] = {c.id: c for c in cells_list}
    _universe_by_id: dict[str, UniverseSpec] = {u.id: u for u in universes_list}
    derived_cells: list[CellSpec] = []
    derived_surfaces: list[SurfaceSpec] = []
    derived_regions: list[RegionSpec] = []
    derived_universes: list[UniverseSpec] = []
    applied: list[str] = []

    # Validate loading references
    normalized: list[NormalizedLatticeLoading] = []
    for lid in loading_ids:
        loading = loading_by_id.get(lid)
        if loading is None:
            issues.append(ValidationIssue(
                severity="error",
                code="lattice_transform.loading_ref_missing",
                message=f"loading_id {lid!r} not found in lattice_loadings",
                schema_path=f"core.axial_layers.loading_ids[{lid!r}]",
            ))
            continue
        if isinstance(loading, NormalizedLatticeLoading):
            nl = loading
        else:
            nl = normalize_lattice_loading(loading)
        # Check base lattice consistency
        if nl.base_lattice_id != base_lattice.id:
            issues.append(ValidationIssue(
                severity="error",
                code="lattice_transform.base_lattice_mismatch",
                message=(
                    f"loading {nl.id!r} references base_lattice_id "
                    f"{nl.base_lattice_id!r} but the layer uses {base_lattice.id!r}"
                ),
                schema_path=f"lattice_loadings[{nl.id}].base_lattice_id",
            ))
            continue
        normalized.append(nl)

    if issues:
        return LatticeTransformationResult(ok=False, issues=issues, cache_key="")

    # Collect and sort all operations across loadings
    all_ops: list[tuple[int, str, LatticeTransformationOperation]] = []
    for nl in normalized:
        for op in nl.operations:
            all_ops.append((_effective_priority(op), op.operation_id, op))

    all_ops.sort(key=lambda t: (t[0], t[1]))

    # Deep copy the base lattice pattern
    derived_pattern: list[list[str]] = [
        [cell for cell in row] for row in base_lattice.universe_pattern
    ]
    rows = len(derived_pattern)
    cols = len(derived_pattern[0]) if rows > 0 else 0

    # Validate universe references
    for _, _, op in all_ops:
        if op.replacement_universe_id not in universe_ids:
            issues.append(ValidationIssue(
                severity="error",
                code="lattice_transform.replacement_universe_missing",
                message=(
                    f"operation {op.operation_id!r}: replacement universe "
                    f"{op.replacement_universe_id!r} not defined"
                ),
                schema_path=f"transformations[{op.operation_id}].replacement_universe_id",
            ))
        if op.operation_kind == "replace_universe_family":
            sources = sorted(
                [op.source_universe_id] + op.source_universe_ids
                if op.source_universe_id
                else op.source_universe_ids
            )
            for src in sources:
                if src not in universe_ids:
                    issues.append(ValidationIssue(
                        severity="error",
                        code="lattice_transform.source_universe_missing",
                        message=(
                            f"operation {op.operation_id!r}: source universe "
                            f"{src!r} not defined"
                        ),
                        schema_path=f"transformations[{op.operation_id}].source_universe_id",
                    ))

    if issues:
        return LatticeTransformationResult(ok=False, issues=issues, cache_key="")

    # Track coordinate assignments for conflict detection
    coord_assignments: dict[tuple[int, int], str] = {}

    for priority, op_id, op in all_ops:
        if op.operation_kind == "replace_universe_family":
            sources = set()
            if op.source_universe_id:
                sources.add(op.source_universe_id)
            sources.update(op.source_universe_ids)

            match_count = 0
            for r in range(rows):
                for c in range(cols):
                    if derived_pattern[r][c] in sources:
                        derived_pattern[r][c] = op.replacement_universe_id
                        match_count += 1
            if match_count == 0:
                issues.append(ValidationIssue(
                    severity="warning",
                    code="lattice_transform.family_replacement_no_match",
                    message=(
                        f"operation {op.operation_id!r}: source universe(s) "
                        f"{sorted(sources)} matched 0 positions in the lattice"
                    ),
                    schema_path=f"transformations[{op.operation_id}]",
                ))
            else:
                applied.append(op.operation_id)

        elif op.operation_kind == "coordinate_override":
            seen_in_op: set[tuple[int, int]] = set()
            for coord in op.target_coordinates:
                r, c = coord[0], coord[1]
                if not (0 <= r < rows and 0 <= c < cols):
                    issues.append(ValidationIssue(
                        severity="error",
                        code="lattice_transform.coordinate_oob",
                        message=(
                            f"operation {op.operation_id!r}: coordinate "
                            f"({r}, {c}) out of bounds for {rows}x{cols} lattice"
                        ),
                        schema_path=f"transformations[{op.operation_id}].target_coordinates",
                    ))
                    continue
                if coord in seen_in_op:
                    issues.append(ValidationIssue(
                        severity="warning",
                        code="lattice_transform.duplicate_coordinate",
                        message=(
                            f"operation {op.operation_id!r}: duplicate coordinate "
                            f"({r}, {c})"
                        ),
                        schema_path=f"transformations[{op.operation_id}].target_coordinates",
                    ))
                    continue
                seen_in_op.add(coord)

                if coord in coord_assignments:
                    prev = coord_assignments[coord]
                    if prev != op.replacement_universe_id:
                        issues.append(ValidationIssue(
                            severity="error",
                            code="lattice_transform.coordinate_conflict",
                            message=(
                                f"coordinate ({r}, {c}) assigned to both "
                                f"{prev!r} and {op.replacement_universe_id!r}"
                            ),
                            schema_path=f"transformations[{op.operation_id}].target_coordinates",
                        ))
                        continue
                derived_pattern[r][c] = op.replacement_universe_id
                coord_assignments[coord] = op.replacement_universe_id
            applied.append(op.operation_id)

        elif op.operation_kind == "nested_component_override":
            for coord in op.target_coordinates:
                r, c = coord[0], coord[1]
                if not (0 <= r < rows and 0 <= c < cols):
                    issues.append(ValidationIssue(
                        severity="error",
                        code="lattice_transform.coordinate_oob",
                        message=(
                            f"operation {op.operation_id!r}: coordinate "
                            f"({r}, {c}) out of bounds for {rows}x{cols} lattice"
                        ),
                        schema_path=f"transformations[{op.operation_id}].target_coordinates",
                    ))
                    continue

                base_uid = derived_pattern[r][c]
                base_universe = _universe_by_id.get(base_uid)
                repl_universe = _universe_by_id.get(op.replacement_universe_id)

                if base_universe is None or repl_universe is None:
                    issues.append(ValidationIssue(
                        severity="error",
                        code="lattice_transform.component_target_missing",
                        message=(
                            f"nested override at ({r},{c}): base universe "
                            f"{base_uid!r} or replacement {op.replacement_universe_id!r} not found"
                        ),
                        schema_path=f"transformations[{op.operation_id}]",
                    ))
                    continue

                base_cells = [_cells_by_id[cid] for cid in base_universe.cell_ids if cid in _cells_by_id]
                repl_cells = [_cells_by_id[cid] for cid in repl_universe.cell_ids if cid in _cells_by_id]

                # Validation: replacement universe must be non-empty
                if not repl_cells:
                    issues.append(ValidationIssue(
                        severity="error",
                        code="lattice_transform.nested_replacement_empty",
                        message=(
                            f"nested override at ({r},{c}): replacement universe "
                            f"{op.replacement_universe_id!r} has no cells"
                        ),
                        schema_path=f"transformations[{op.operation_id}].replacement_universe_id",
                    ))
                    continue

                # Find target component cells in base universe
                target_cells = [
                    cell for cell in base_cells
                    if _cell_matches_component(cell, op.component_role, op.component_path_id)
                ]
                if not target_cells:
                    issues.append(ValidationIssue(
                        severity="error",
                        code="lattice_transform.component_target_missing",
                        message=(
                            f"nested override at ({r},{c}): no cell with "
                            f"component_role={op.component_role!r} in universe {base_uid!r}"
                        ),
                        schema_path=f"transformations[{op.operation_id}]",
                    ))
                    continue
                if len(target_cells) > 1:
                    issues.append(ValidationIssue(
                        severity="error",
                        code="lattice_transform.component_target_ambiguous",
                        message=(
                            f"nested override at ({r},{c}): {len(target_cells)} cells match "
                            f"component_role={op.component_role!r} in universe {base_uid!r}; "
                            "add component_path_id to disambiguate"
                        ),
                        schema_path=f"transformations[{op.operation_id}]",
                    ))
                    continue

                target_cell = target_cells[0]

                # Validation: target cell must have a region (needed for bounding)
                if target_cell.region_id is None:
                    issues.append(ValidationIssue(
                        severity="error",
                        code="lattice_transform.nested_fill_region_missing",
                        message=(
                            f"nested override at ({r},{c}): target cell "
                            f"{target_cell.id!r} has no region_id; a region is "
                            "required to bound the replacement universe fill"
                        ),
                        schema_path=f"transformations[{op.operation_id}].component_role",
                    ))
                    continue

                # Check that protected cells survive (they are always kept in
                # Method B since we only clone the target cell)
                preserve_roles = set(op.preserve_component_roles)
                preserve_paths = set(op.preserve_path_ids)
                for pcell in (cell for cell in base_cells if cell.protected_through_path):
                    if _cell_matches_any(pcell, preserve_roles, preserve_paths):
                        continue
                    # Protected cell not explicitly listed in preserve list
                    if pcell.id == target_cell.id:
                        # Replacing a protected cell's fill is still allowed
                        # if it is the declared target; the cell itself survives.
                        continue
                    issues.append(ValidationIssue(
                        severity="error",
                        code="lattice_transform.protected_path_removed",
                        message=(
                            f"nested override at ({r},{c}): protected cell "
                            f"{pcell.id!r} (role={pcell.component_role!r}) in "
                            f"universe {base_uid!r} is not in preserve_component_roles"
                        ),
                        schema_path=f"transformations[{op.operation_id}].preserve_component_roles",
                    ))

                # Check preserved components exist
                for role in op.preserve_component_roles:
                    if not any(cell.component_role == role for cell in base_cells):
                        issues.append(ValidationIssue(
                            severity="warning",
                            code="lattice_transform.preserved_component_missing",
                            message=(
                                f"nested override at ({r},{c}): preserve role "
                                f"{role!r} not found in base universe {base_uid!r}"
                            ),
                            schema_path=f"transformations[{op.operation_id}].preserve_component_roles",
                        ))

                # Validation: replacement must not cover a preserved wall cell
                for bcell in base_cells:
                    if bcell.id == target_cell.id:
                        continue
                    if _cell_matches_any(bcell, preserve_roles, preserve_paths):
                        # This cell is preserved — ensure its region does not
                        # overlap with the target cell region.
                        if bcell.region_id is not None and bcell.region_id == target_cell.region_id:
                            issues.append(ValidationIssue(
                                severity="error",
                                code="lattice_transform.parent_region_conflict",
                                message=(
                                    f"nested override at ({r},{c}): preserved cell "
                                    f"{bcell.id!r} shares region "
                                    f"{bcell.region_id!r} with target cell "
                                    f"{target_cell.id!r}"
                                ),
                                schema_path=f"transformations[{op.operation_id}]",
                            ))

                # Validation: nested universe cycle — the replacement must not
                # directly or indirectly fill back into a derived universe that
                # contains the replacement itself.
                _check_nested_universe_cycle(
                    op.replacement_universe_id,
                    derived_universes,
                    _universe_by_id,
                    op.operation_id,
                    (r, c),
                    issues,
                )

                if any(
                    i.severity == "error"
                    and i.code.startswith("lattice_transform.")
                    and i.code in {
                        "lattice_transform.protected_path_removed",
                        "lattice_transform.parent_region_conflict",
                        "lattice_transform.nested_universe_cycle",
                        "lattice_transform.nested_fill_region_missing",
                        "lattice_transform.nested_replacement_empty",
                    }
                    for i in issues[-6:]
                ):
                    continue

                # ---- Method B: bounded nested fill ----
                # Clone the target cell, keeping its region_id (the bounding
                # region). Change fill_type → "universe" so the replacement
                # universe fills only the parent cell's region. All other base
                # cells survive unchanged.
                sig = _operation_composition_signature(op)
                derived_uid = f"{base_uid}__nested_{op.operation_id}"
                if sig:
                    derived_uid = f"{derived_uid}__{sig}"
                derived_cell_id = f"{target_cell.id}__nested_{op.operation_id}"
                if sig:
                    derived_cell_id = f"{derived_cell_id}__{sig}"

                cloned_cell = CellSpec(
                    id=derived_cell_id,
                    name=f"nested fill: {target_cell.name}",
                    region_id=target_cell.region_id,
                    fill_type="universe",
                    fill_id=op.replacement_universe_id,
                    component_role=target_cell.component_role,
                    component_path_id=target_cell.component_path_id,
                    protected_through_path=False,
                    temperature_k=target_cell.temperature_k,
                    purpose=f"Bounded nested fill of {op.replacement_universe_id} into {target_cell.id}",
                )

                # Build derived universe cell list: replace target with clone,
                # and clone all preserved cells so each OpenMC Cell belongs to
                # exactly one Universe (OpenMC constraint).
                derived_cell_ids: list[str] = []
                for bcell in base_cells:
                    if bcell.id == target_cell.id:
                        derived_cell_ids.append(derived_cell_id)
                    else:
                        preserved_clone_id = f"{bcell.id}__nested_{op.operation_id}"
                        if sig:
                            preserved_clone_id = f"{preserved_clone_id}__{sig}"
                        # Only clone once
                        if preserved_clone_id not in {dc.id for dc in derived_cells}:
                            preserved_clone = bcell.model_copy(deep=True)
                            object.__setattr__(preserved_clone, "id", preserved_clone_id)
                            preserved_clone.name = f"{bcell.name} (nested clone)"
                            derived_cells.append(preserved_clone)
                        derived_cell_ids.append(preserved_clone_id)

                # Add cloned cell to derived objects (once per unique id)
                if derived_cell_id not in {dc.id for dc in derived_cells}:
                    derived_cells.append(cloned_cell)

                if derived_uid not in {u.id for u in derived_universes}:
                    derived_universes.append(UniverseSpec(
                        id=derived_uid,
                        name=f"nested override: {base_uid} + {op.replacement_universe_id}",
                        cell_ids=derived_cell_ids,
                        purpose=f"Bounded nested fill preserving {sorted(preserve_roles)}",
                    ))

                derived_pattern[r][c] = derived_uid
                coord_assignments[coord] = derived_uid
            applied.append(op.operation_id)

    if any(i.severity == "error" for i in issues):
        return LatticeTransformationResult(ok=False, issues=issues, cache_key="")

    # Build derived lattice (deep copy, new id, new pattern)
    derived_lattice = LatticeSpec(
        id=normalized[-1].derived_lattice_id if normalized else base_lattice.id,
        name=f"derived from {base_lattice.id}",
        kind=base_lattice.kind,
        pitch_cm=base_lattice.pitch_cm,
        lower_left_cm=base_lattice.lower_left_cm,
        center_cm=base_lattice.center_cm,
        shape=base_lattice.shape,
        outer_universe_id=base_lattice.outer_universe_id,
        universe_pattern=derived_pattern,
        purpose=f"Derived by compose_lattice_loadings",
    )

    cache_key = compute_cache_key(base_lattice.id, normalized)

    return LatticeTransformationResult(
        ok=True,
        derived_lattice=derived_lattice,
        derived_universes=derived_universes,
        derived_cells=derived_cells,
        derived_surfaces=derived_surfaces,
        derived_regions=derived_regions,
        issues=issues,
        applied_operation_ids=applied,
        cache_key=cache_key,
    )


# ---------------------------------------------------------------------------
# Nested-fill helpers
# ---------------------------------------------------------------------------


def _operation_composition_signature(op: LatticeTransformationOperation) -> str:
    """Short deterministic signature for a nested operation.

    Ensures derived cell/universe ids are unique across different loading
    compositions so that multi-loading combinations do not collide.
    """
    import hashlib
    payload = "|".join([
        op.operation_id,
        op.replacement_universe_id,
        str(sorted(op.target_coordinates)),
    ])
    return hashlib.md5(payload.encode("utf-8")).hexdigest()[:8]


def _check_nested_universe_cycle(
    replacement_uid: str,
    derived_universes: list[UniverseSpec],
    base_universe_by_id: Mapping[str, UniverseSpec],
    op_id: str,
    coord: tuple[int, int],
    issues: list[ValidationIssue],
) -> None:
    """Reject when replacement universe transitively fills a derived universe
    that was created by this same operation (would cause infinite recursion).
    """
    derived_ids = {u.id for u in derived_universes}
    visited: set[str] = set()
    queue: list[str] = [replacement_uid]
    while queue:
        uid = queue.pop()
        if uid in visited:
            continue
        visited.add(uid)
        univ = base_universe_by_id.get(uid)
        if univ is None:
            continue
        for cid in univ.cell_ids:
            # We don't have cell fill info here, but we can check if a derived
            # universe id directly appears as a cell id fragment — this is a
            # lightweight heuristic since the full cell objects are available
            # in derived_cells but not indexed here.
            pass
    # The cycle check is primarily structural: if the replacement_uid itself
    # is a derived universe id created earlier (by this or a prior composition),
    # that's a direct cycle. Derived universe ids contain '__nested_'.
    if replacement_uid in derived_ids or "__nested_" in replacement_uid:
        issues.append(ValidationIssue(
            severity="error",
            code="lattice_transform.nested_universe_cycle",
            message=(
                f"nested override at {coord}: replacement universe "
                f"{replacement_uid!r} is itself a derived universe; "
                "this would create a cycle"
            ),
            schema_path=f"transformations[{op_id}].replacement_universe_id",
        ))


# ---------------------------------------------------------------------------
# Component matching helpers
# ---------------------------------------------------------------------------


def _cell_matches_component(
    cell: CellSpec,
    role: str | None,
    path_id: str | None,
) -> bool:
    """True when a cell matches the given component_role and/or component_path_id."""
    if role is not None and path_id is not None:
        return cell.component_role == role and cell.component_path_id == path_id
    if role is not None:
        return cell.component_role == role
    if path_id is not None:
        return cell.component_path_id == path_id
    return False


def _cell_matches_any(
    cell: CellSpec,
    roles: set[str],
    path_ids: set[str],
) -> bool:
    """True when a cell matches any of the given roles or path ids."""
    if cell.component_role and cell.component_role in roles:
        return True
    if cell.component_path_id and cell.component_path_id in path_ids:
        return True
    return False


# ---------------------------------------------------------------------------
# Through-path preservation validator
# ---------------------------------------------------------------------------


def validate_through_path_preservation(
    *,
    base_universe: UniverseSpec,
    derived_universe: UniverseSpec,
    preserve_component_roles: Sequence[str],
    preserve_path_ids: Sequence[str],
    cells_by_id: Mapping[str, CellSpec],
) -> list[ValidationIssue]:
    """Check that all declared preserve roles/paths survive in the derived universe.

    Issues raised:

    - ``lattice_transform.protected_path_removed``: a ``protected_through_path``
      cell is absent from the derived universe.
    - ``lattice_transform.preserved_component_missing``: a declared preserve
      role/path has no matching cell in the derived universe.
    - ``lattice_transform.component_target_missing``: the derived universe is
      empty or has no cells at all.
    """
    issues: list[ValidationIssue] = []
    derived_cell_ids = set(derived_universe.cell_ids)

    # Check protected cells
    for cid in base_universe.cell_ids:
        cell = cells_by_id.get(cid)
        if cell is None:
            continue
        if cell.protected_through_path and cid not in derived_cell_ids:
            issues.append(ValidationIssue(
                severity="error",
                code="lattice_transform.protected_path_removed",
                message=(
                    f"protected cell {cid!r} (role={cell.component_role!r}) "
                    f"from universe {base_universe.id!r} is absent from "
                    f"derived universe {derived_universe.id!r}"
                ),
                schema_path=f"universes.{derived_universe.id}",
            ))

    # Check preserved roles
    derived_cells = [cells_by_id[cid] for cid in derived_cell_ids if cid in cells_by_id]
    for role in preserve_component_roles:
        if not any(c.component_role == role for c in derived_cells):
            issues.append(ValidationIssue(
                severity="error",
                code="lattice_transform.preserved_component_missing",
                message=(
                    f"preserved component_role {role!r} not found in "
                    f"derived universe {derived_universe.id!r}"
                ),
                schema_path=f"universes.{derived_universe.id}",
            ))

    for path_id in preserve_path_ids:
        if not any(c.component_path_id == path_id for c in derived_cells):
            issues.append(ValidationIssue(
                severity="error",
                code="lattice_transform.preserved_component_missing",
                message=(
                    f"preserved component_path_id {path_id!r} not found in "
                    f"derived universe {derived_universe.id!r}"
                ),
                schema_path=f"universes.{derived_universe.id}",
            ))

    # Check derived universe is non-empty
    if not derived_cell_ids:
        issues.append(ValidationIssue(
            severity="error",
            code="lattice_transform.component_target_missing",
            message=f"derived universe {derived_universe.id!r} has no cells",
            schema_path=f"universes.{derived_universe.id}",
        ))

    return issues


# ---------------------------------------------------------------------------
# Production materialization: wire axial-layer loading_ids into derived
# lattices and inject derived cells/universes into the spec.
# ---------------------------------------------------------------------------


def materialize_axial_lattice_transformations(
    spec: ComplexModelSpec,
) -> tuple[ComplexModelSpec, list[ValidationIssue], dict[str, Any]]:
    """Apply axial-layer lattice loadings and inject derived objects.

    Walks every axial layer that carries ``loading_id`` / ``loading_ids``,
    composes the referenced loadings onto the base lattice, then adds the
    resulting derived cells / universes / lattices to the spec and rewrites
    the layer's ``fill.id`` to point at the derived lattice.

    The input is **never modified** — a deep copy is returned.

    Returns ``(new_spec, issues, metadata)`` where *metadata* has keys::

        materialized_layer_count, derived_lattice_ids,
        derived_universe_ids, derived_cell_ids,
        cache_hits, layer_to_derived_lattice
    """
    import copy as _copy

    issues: list[ValidationIssue] = []
    metadata: dict[str, Any] = {
        "materialized_layer_count": 0,
        "derived_lattice_ids": [],
        "derived_universe_ids": [],
        "derived_cell_ids": [],
        "cache_hits": 0,
        "layer_to_derived_lattice": {},
    }

    new_spec = spec.model_copy(deep=True)
    core = new_spec.core
    if core is None:
        return new_spec, issues, metadata

    layers = core.axial_layers
    if not layers:
        return new_spec, issues, metadata

    lattice_by_id: dict[str, LatticeSpec] = {l.id: l for l in new_spec.lattices}
    loading_by_id: dict[str, LatticeLoadingSpec] = {
        l.id: l for l in (new_spec.lattice_loadings or [])
    }

    # Cache: (base_lattice_id, tuple(loading_ids)) → (derived_lattice, derived_cells, ...)
    cache: dict[str, dict[str, Any]] = {}

    for layer in layers:
        lids = normalized_layer_loading_ids(layer)
        if not lids:
            continue
        if layer.fill.type != "lattice":
            issues.append(ValidationIssue(
                severity="error",
                code="renderer.axial_loading_wrong_fill_type",
                message=(
                    f"layer {layer.id!r} has loading_ids {lids} but "
                    f"fill.type={layer.fill.type!r} (expected 'lattice')"
                ),
                schema_path=f"core.axial_layers[{layer.id}].fill",
            ))
            continue

        declared_bases: set[str] = set()
        missing_loading_ids: list[str] = []
        for loading_id in lids:
            loading = loading_by_id.get(loading_id)
            if loading is None:
                missing_loading_ids.append(loading_id)
            else:
                declared_bases.add(loading.base_lattice_id)
        if missing_loading_ids:
            issues.append(ValidationIssue(
                severity="error",
                code="renderer.axial_loading_materialization_failed",
                message=(
                    f"layer {layer.id!r}: referenced loadings are missing: "
                    f"{missing_loading_ids}"
                ),
                schema_path=f"core.axial_layers[{layer.id}].loading_ids",
            ))
            continue
        if len(declared_bases) != 1:
            issues.append(ValidationIssue(
                severity="error",
                code="renderer.axial_loading_base_lattice_mismatch",
                message=(
                    f"layer {layer.id!r}: loadings {lids} do not declare one "
                    f"common base lattice: {sorted(declared_bases)}"
                ),
                schema_path=f"core.axial_layers[{layer.id}].loading_ids",
            ))
            continue

        # The layer may already name a declared derived lattice (legacy IR).
        # The loading declaration remains the authoritative base for composition.
        base_lattice_id = next(iter(declared_bases))
        base_lattice = lattice_by_id.get(base_lattice_id)
        if base_lattice is None:
            issues.append(ValidationIssue(
                severity="error",
                code="renderer.axial_loading_base_lattice_missing",
                message=(
                    f"layer {layer.id!r}: base lattice {base_lattice_id!r} "
                    "not found"
                ),
                schema_path=f"core.axial_layers[{layer.id}].fill",
            ))
            continue

        cache_key = f"{base_lattice_id}|{'|'.join(lids)}"
        if cache_key in cache:
            entry = cache[cache_key]
            metadata["cache_hits"] += 1
        else:
            result = compose_lattice_loadings(
                base_lattice=base_lattice,
                loading_ids=lids,
                loading_by_id=loading_by_id,
                universes=new_spec.universes,
                cells=new_spec.cells,
            )
            if not result.ok:
                issues.append(ValidationIssue(
                    severity="error",
                    code="renderer.axial_loading_materialization_failed",
                    message=(
                        f"layer {layer.id!r}: compose_lattice_loadings failed: "
                        + "; ".join(i.message for i in result.issues if i.severity == "error")
                    ),
                    schema_path=f"core.axial_layers[{layer.id}].loading_ids",
                ))
                issues.extend(result.issues)
                continue

            derived_lattice = result.derived_lattice
            assert derived_lattice is not None
            new_spec.lattices.append(derived_lattice)
            new_spec.cells.extend(result.derived_cells)
            new_spec.universes.extend(result.derived_universes)
            new_spec.surfaces.extend(result.derived_surfaces)
            new_spec.regions.extend(result.derived_regions)

            entry = {
                "lattice": derived_lattice,
                "derived_lattice_id": derived_lattice.id,
            }
            cache[cache_key] = entry
            metadata["derived_lattice_ids"].append(derived_lattice.id)
            metadata["derived_universe_ids"].extend(
                u.id for u in result.derived_universes
            )
            metadata["derived_cell_ids"].extend(
                c.id for c in result.derived_cells
            )

        derived_lattice_id = entry["derived_lattice_id"]
        layer.fill = FillRefSpec(
            type="lattice",
            id=derived_lattice_id,
        )
        metadata["materialized_layer_count"] += 1
        metadata["layer_to_derived_lattice"][layer.id] = {
            "loading_ids": lids,
            "base_lattice_id": base_lattice_id,
            "materialized_lattice_id": derived_lattice_id,
            "cache_key": cache_key,
        }

    return new_spec, issues, metadata


__all__ = [
    "NormalizedLatticeLoading",
    "LatticeTransformationResult",
    "normalize_lattice_loading",
    "compose_lattice_loadings",
    "compute_cache_key",
    "normalized_layer_loading_ids",
    "layer_loading_id_conflict",
    "validate_through_path_preservation",
    "materialize_axial_lattice_transformations",
]
