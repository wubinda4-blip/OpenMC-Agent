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
            # Nested component override is validated but the actual derived
            # universe creation is handled in Commit 3. For now, coordinate
            # positions still point to the replacement universe so the derived
            # lattice is valid. The through-path preservation check is added
            # in the through-path validator module.
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
                derived_pattern[r][c] = op.replacement_universe_id
                coord_assignments[coord] = op.replacement_universe_id
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


__all__ = [
    "NormalizedLatticeLoading",
    "LatticeTransformationResult",
    "normalize_lattice_loading",
    "compose_lattice_loadings",
    "compute_cache_key",
    "normalized_layer_loading_ids",
    "layer_loading_id_conflict",
]
