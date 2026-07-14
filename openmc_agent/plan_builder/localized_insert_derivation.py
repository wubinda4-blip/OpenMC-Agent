"""Generic deterministic localized insert loading derivation.

Replaces the pyrex-only ``_normalize_axial_insert_pin_map`` with a
reactor-neutral pipeline that processes **all** localized insert intents
(Pyrex, thimble plugs, absorbers, control rods, ...) through the same
code path.

Pipeline per intent:
1. Validate host/insert universe existence.
2. Normalize coordinates to 0-based row/col.
3. Create a unique lattice loading with nested_component_override.
4. Split axial layers at insert z boundaries.
5. Attach loading to layers within the insert z interval.
6. Preserve existing fuel-profile loadings.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from openmc_agent.plan_builder.patches import (
    AxialLayerPatchItem,
    AxialLayersPatch,
    CoordinateConvention,
    LatticeLoadingPatchItem,
    LatticeTransformationPatchItem,
    LocalizedInsertIntentPatchItem,
    PinMapPatch,
    UniversesPatch,
    normalized_coords,
)

LOCALIZED_INSERT_CONTRACT_VERSION = "1.0.0"


# --------------------------------------------------------------------------- #
# Data classes
# --------------------------------------------------------------------------- #


@dataclass
class LocalizedInsertDerivationReport:
    """Result of deriving loadings from localized insert intents."""

    intents_processed: int = 0
    loadings_created: list[str] = field(default_factory=list)
    layers_split: int = 0
    loadings_attached: list[str] = field(default_factory=list)
    issues: list[dict[str, Any]] = field(default_factory=list)
    legacy_intents_migrated: int = 0


# --------------------------------------------------------------------------- #
# Public API
# --------------------------------------------------------------------------- #


def migrate_legacy_coords_to_intents(
    pin_map: PinMapPatch,
    universes_patch: UniversesPatch | None,
) -> tuple[PinMapPatch, list[dict[str, Any]]]:
    """Migrate legacy pyrex_rod_coords / thimble_plug_coords to
    localized_insert_intents.

    Returns ``(new_pin_map, issues)``.
    """
    issues: list[dict[str, Any]] = []
    intents = list(pin_map.localized_insert_intents)

    kind_map = _build_kind_to_universe_id(universes_patch) if universes_patch else {}

    legacy_groups = [
        ("pyrex_rod", pin_map.pyrex_rod_coords, "pyrex_rod"),
        ("thimble_plug", pin_map.thimble_plug_coords, "thimble_plug"),
    ]

    for legacy_name, coords, insert_kind in legacy_groups:
        if not coords:
            continue
        uid = kind_map.get(insert_kind, "")
        if not uid:
            issues.append({
                "code": f"localized_insert.legacy_{legacy_name}_universe_missing",
                "severity": "warning",
                "message": f"Legacy {legacy_name}_coords found but no {insert_kind} universe defined",
            })
            continue

        # Check if intent already exists for this kind.
        existing = [i for i in intents if i.insert_kind == insert_kind]
        if existing:
            issues.append({
                "code": f"localized_insert.legacy_{legacy_name}_already_migrated",
                "severity": "info",
                "message": f"Legacy {legacy_name}_coords ignored; localized_insert_intents already has {insert_kind}",
            })
            continue

        intent = LocalizedInsertIntentPatchItem(
            insert_id=f"legacy_{legacy_name}",
            insert_kind=insert_kind,
            host_kind="guide_tube",
            insert_universe_id=uid,
            coordinates=list(coords),
            z_min_cm=None,
            z_max_cm=None,
            application_mode="nested_component_override",
            requires_human_confirmation=True,
            assumptions=[f"Migrated from legacy {legacy_name}_coords; axial extent unknown"],
        )
        intents.append(intent)
        issues.append({
            "code": f"localized_insert.legacy_{legacy_name}_migrated",
            "severity": "warning",
            "message": f"Legacy {legacy_name}_coords ({len(coords)} positions) migrated to localized_insert_intents. "
            f"Axial extent unknown — requires_human_confirmation=true.",
        })

    # Clear legacy coords.
    new_pin_map = pin_map.model_copy(update={
        "localized_insert_intents": intents,
        "pyrex_rod_coords": [],
        "thimble_plug_coords": [],
    })

    return new_pin_map, issues


def derive_localized_insert_loadings(
    pin_map: PinMapPatch,
    axial_layers: AxialLayersPatch | None,
    universes_patch: UniversesPatch | None,
) -> tuple[PinMapPatch, AxialLayersPatch | None, list[dict[str, Any]], LocalizedInsertDerivationReport]:
    """Derive lattice loadings from localized insert intents.

    This function replaces ``_normalize_axial_insert_pin_map`` with a
    generic pipeline that handles ALL insert kinds.

    Returns ``(new_pin_map, new_axial_layers, issues, report)``.
    """
    report = LocalizedInsertDerivationReport()
    issues: list[dict[str, Any]] = []

    # Step 1: Migrate legacy coords.
    pin_map, migrate_issues = migrate_legacy_coords_to_intents(pin_map, universes_patch)
    for mi in migrate_issues:
        if "migrated" in mi["code"] and mi["severity"] == "warning":
            report.legacy_intents_migrated += 1
    issues.extend(migrate_issues)

    intents = pin_map.localized_insert_intents
    if not intents:
        return pin_map, axial_layers, issues, report

    if axial_layers is None:
        for intent in intents:
            issues.append({
                "code": "localized_insert.loading_missing",
                "severity": "error",
                "message": f"Intent {intent.insert_id!r} has no axial layers to attach to",
            })
        return pin_map, None, issues, report

    # Step 2: Ensure guide_tube_coords includes all insert host coordinates.
    guide_set = set(pin_map.guide_tube_coords)
    missing_coords = set()
    for intent in intents:
        for coord in intent.coordinates:
            if coord not in guide_set:
                # Check if it's in instrument_tube_coords.
                if coord not in set(pin_map.instrument_tube_coords):
                    missing_coords.add(coord)
    if missing_coords:
        # Auto-add missing coords to guide_tube_coords.
        updated_guide = list(pin_map.guide_tube_coords) + [
            c for c in missing_coords if c not in guide_set
        ]
        pin_map = pin_map.model_copy(update={"guide_tube_coords": updated_guide})
        issues.append({
            "code": "assembly.guide_tube_coords_extended_for_inserts",
            "severity": "info",
            "message": f"Added {len(missing_coords)} coordinates to guide_tube_coords for insert hosts",
        })

    # Step 3: Process each intent.
    all_loadings = list(axial_layers.lattice_loadings)
    layers = list(axial_layers.layers)

    for intent in intents:
        report.intents_processed += 1

        # Validate z range.
        if intent.z_min_cm is None or intent.z_max_cm is None:
            issues.append({
                "code": "localized_insert.axial_extent_invalid",
                "severity": "warning",
                "message": f"Intent {intent.insert_id!r} has no axial extent (z_min/z_max missing). "
                           f"Loading will not be derived; requires_human_confirmation needed.",
            })
            continue

        if intent.z_min_cm >= intent.z_max_cm:
            issues.append({
                "code": "localized_insert.axial_extent_invalid",
                "severity": "error",
                "message": f"Intent {intent.insert_id!r} z_min >= z_max",
            })
            continue

        # Normalize coordinates.
        conv = pin_map.coordinate_convention
        nx, ny = pin_map.lattice_size
        try:
            norm_coords = normalized_coords(intent.coordinates, conv, (nx, ny))
        except (ValueError, IndexError) as exc:
            issues.append({
                "code": "localized_insert.intent_coordinate_out_of_bounds",
                "severity": "error",
                "message": f"Intent {intent.insert_id!r} coordinate error: {exc}",
            })
            continue

        # Create loading.
        loading_id = f"localized_insert_{intent.insert_id}"
        derived_lattice_id = f"assembly_lattice_{loading_id}"

        # Check if loading already exists (either from LLM or from previous derivation).
        existing_loading = next(
            (l for l in all_loadings
             if l.loading_id == loading_id
             or any(
                 t.replacement_universe_id == intent.insert_universe_id
                 for t in l.transformations
             )
             or intent.insert_universe_id in l.overrides),
            None,
        )
        if existing_loading:
            issues.append({
                "code": "localized_insert.loading_already_exists",
                "severity": "info",
                "message": f"Loading for {intent.insert_id!r} (universe={intent.insert_universe_id!r}) "
                           f"already exists as {existing_loading.loading_id!r}",
            })
            # Use the existing loading_id for layer attachment.
            loading_id = existing_loading.loading_id

            # Upgrade coordinate_override to nested_component_override if the
            # intent declares nested mode and the existing loading uses coordinate_override.
            if intent.application_mode == "nested_component_override":
                _upgrade_to_nested_override(
                    existing_loading, intent, norm_coords,
                )
        else:
            # Build transformation.
            transformation = LatticeTransformationPatchItem(
                operation_id=f"{intent.insert_id}_transform",
                operation_kind=intent.application_mode,
                replacement_universe_id=intent.insert_universe_id,
                target_coordinates=[tuple(c) for c in norm_coords],
                component_role=intent.component_role,
                component_path_id=intent.component_path_id,
                preserve_component_roles=list(intent.preserve_component_roles),
                preserve_path_ids=list(intent.preserve_path_ids),
                priority=intent.priority,
                purpose=f"localized insert: {intent.insert_kind} ({intent.insert_id})",
            )

            loading = LatticeLoadingPatchItem(
                loading_id=loading_id,
                base_lattice_id="assembly_lattice",
                derived_lattice_id=derived_lattice_id,
                transformations=[transformation],
                overrides={},  # Use transformations, not raw overrides
                purpose=f"localized insert: {intent.insert_kind} ({intent.insert_id}) "
                        f"at z={intent.z_min_cm}-{intent.z_max_cm} cm",
            )
            all_loadings.append(loading)
            report.loadings_created.append(loading_id)

        # Split layers at insert z boundaries.
        layers, n_splits = _split_layers_at_boundaries(
            layers, intent.z_min_cm, intent.z_max_cm,
        )
        report.layers_split += n_splits

        # Attach loading to layers within the insert z interval.
        attached = _attach_loading_to_interval(
            layers, loading_id, intent.z_min_cm, intent.z_max_cm,
        )
        if attached:
            report.loadings_attached.append(loading_id)
        else:
            issues.append({
                "code": "localized_insert.loading_missing",
                "severity": "error",
                "message": f"No lattice layers overlap with intent {intent.insert_id!r} "
                           f"z range [{intent.z_min_cm}, {intent.z_max_cm}]",
            })

    # Build updated axial layers patch.
    new_axial_layers = axial_layers.model_copy(update={
        "layers": layers,
        "lattice_loadings": all_loadings,
    })

    return pin_map, new_axial_layers, issues, report


# --------------------------------------------------------------------------- #
# Internal helpers
# --------------------------------------------------------------------------- #


def _upgrade_to_nested_override(
    loading: LatticeLoadingPatchItem,
    intent: LocalizedInsertIntentPatchItem,
    norm_coords: list[tuple[int, int]],
) -> None:
    """Upgrade a coordinate_override transformation to nested_component_override.

    This is a deterministic upgrade: when a localized_insert_intent declares
    ``application_mode='nested_component_override'`` but the LLM-generated
    loading uses ``coordinate_override``, we convert it to preserve the host
    tube wall.
    """
    new_transforms: list[LatticeTransformationPatchItem] = []
    upgraded = False
    for t in loading.transformations:
        if (
            t.operation_kind == "coordinate_override"
            and t.replacement_universe_id == intent.insert_universe_id
        ):
            new_transforms.append(t.model_copy(update={
                "operation_kind": "nested_component_override",
                "component_role": intent.component_role or t.component_role,
                "component_path_id": intent.component_path_id or t.component_path_id,
                "preserve_component_roles": list(intent.preserve_component_roles) or list(t.preserve_component_roles),
                "preserve_path_ids": list(intent.preserve_path_ids) or list(t.preserve_path_ids),
                "target_coordinates": [tuple(c) for c in norm_coords],
            }))
            upgraded = True
        else:
            new_transforms.append(t)
    if upgraded:
        loading.transformations = new_transforms


def _build_kind_to_universe_id(
    universes_patch: UniversesPatch | None,
) -> dict[str, str]:
    """Build kind → universe_id mapping from universes patch."""
    if universes_patch is None:
        return {}
    kind_map: dict[str, str] = {}
    for u in universes_patch.universes:
        k = u.kind
        if k and k not in kind_map:
            kind_map[k] = u.universe_id
    return kind_map


def _split_layers_at_boundaries(
    layers: list[AxialLayerPatchItem],
    z_min: float,
    z_max: float,
) -> tuple[list[AxialLayerPatchItem], int]:
    """Split any layer that crosses z_min or z_max into sub-layers.

    Returns ``(new_layers, n_splits)``.
    """
    new_layers: list[AxialLayerPatchItem] = []
    n_splits = 0

    for layer in layers:
        lz_min = layer.z_min_cm
        lz_max = layer.z_max_cm

        if lz_min is None or lz_max is None:
            new_layers.append(layer)
            continue

        # Check if layer crosses z_min or z_max.
        crosses_min = lz_min < z_min < lz_max
        crosses_max = lz_min < z_max < lz_max

        if not crosses_min and not crosses_max:
            new_layers.append(layer)
            continue

        # Build split boundaries.
        boundaries = {lz_min, lz_max}
        if crosses_min:
            boundaries.add(z_min)
        if crosses_max:
            boundaries.add(z_max)
        sorted_bounds = sorted(boundaries)

        for i in range(len(sorted_bounds) - 1):
            seg_min = sorted_bounds[i]
            seg_max = sorted_bounds[i + 1]
            if seg_min >= seg_max:
                continue

            if i == 0:
                seg_id = layer.layer_id
                seg = layer.model_copy(update={
                    "z_min_cm": seg_min,
                    "z_max_cm": seg_max,
                })
            else:
                n_splits += 1
                seg_id = f"{layer.layer_id}_seg{n_splits}"
                seg = layer.model_copy(update={
                    "layer_id": seg_id,
                    "z_min_cm": seg_min,
                    "z_max_cm": seg_max,
                })
            new_layers.append(seg)

    return new_layers, n_splits


def _attach_loading_to_interval(
    layers: list[AxialLayerPatchItem],
    loading_id: str,
    z_min: float,
    z_max: float,
) -> bool:
    """Attach a loading_id to all lattice layers overlapping [z_min, z_max].

    Modifies layers **in place** (they are Pydantic model_copy objects).

    Returns True if at least one layer was attached.
    """
    attached = False
    for i, layer in enumerate(layers):
        lz_min = layer.z_min_cm
        lz_max = layer.z_max_cm
        if lz_min is None or lz_max is None:
            continue

        # Check overlap: layer overlaps with [z_min, z_max].
        overlaps = lz_min < z_max and lz_max > z_min
        if not overlaps:
            continue

        # Only attach to lattice-type layers.
        if layer.fill_type != "lattice":
            continue

        # Build new loading_ids list.
        existing_ids = list(layer.loading_ids) if layer.loading_ids else []
        existing_id = layer.loading_id

        combined: list[str] = []
        if existing_id:
            combined.append(existing_id)
        combined.extend(existing_ids)
        if loading_id not in combined:
            combined.append(loading_id)

        # Deduplicate while preserving order.
        seen: set[str] = set()
        deduped: list[str] = []
        for lid in combined:
            if lid not in seen:
                seen.add(lid)
                deduped.append(lid)

        layers[i] = layer.model_copy(update={
            "loading_ids": deduped,
        })
        attached = True

    return attached
