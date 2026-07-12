"""Patch-level early validation for axial-layer lattice transformations.

Tests that ``_validate_axial_layers`` (via :func:`validate_patch`) catches
lattice-transformation cross-reference defects — undefined replacement /
source universes, cell IDs used as universe IDs, and spacer-grid misuse —
before assembly, not after.
"""

from __future__ import annotations

from openmc_agent.plan_builder.patches import (
    AxialLayerPatchItem,
    AxialLayersPatch,
    LatticeLoadingPatchItem,
    LatticeTransformationPatchItem,
)
from openmc_agent.plan_builder.validators import (
    PatchValidationContext,
    validate_patch,
)


def _make_patch(
    *,
    operation_id: str = "op1",
    operation_kind: str = "replace_universe_family",
    replacement_universe_id: str = "ghost_universe",
    source_universe_id: str | None = "fuel_pin",
    purpose: str = "",
) -> AxialLayersPatch:
    return AxialLayersPatch(
        layers=[
            AxialLayerPatchItem(
                layer_id="active_fuel",
                role="active_fuel",
                z_min_cm=0.0,
                z_max_cm=100.0,
                fill_type="lattice",
                fill_id="assembly_lattice",
                loading_id="test_loading",
            ),
        ],
        lattice_loadings=[
            LatticeLoadingPatchItem(
                loading_id="test_loading",
                base_lattice_id="assembly_lattice",
                transformations=[
                    LatticeTransformationPatchItem(
                        operation_id=operation_id,
                        operation_kind=operation_kind,  # type: ignore[arg-type]
                        replacement_universe_id=replacement_universe_id,
                        source_universe_id=source_universe_id,
                        purpose=purpose,
                    ),
                ],
            ),
        ],
    )


def test_replacement_universe_id_missing_detected() -> None:
    patch = _make_patch(replacement_universe_id="ghost_universe")
    context = PatchValidationContext(
        known_universe_ids=["fuel_pin", "water_cell"],
        known_lattice_ids=["assembly_lattice"],
    )
    result = validate_patch(patch, context)
    codes = [i.code for i in result.issues]
    assert "lattice_transform.replacement_universe_missing" in codes, (
        f"expected replacement_universe_missing in {codes}"
    )


def test_source_universe_id_missing_detected() -> None:
    patch = _make_patch(
        replacement_universe_id="fuel_pin",
        source_universe_id="ghost_source",
    )
    context = PatchValidationContext(
        known_universe_ids=["fuel_pin", "water_cell"],
    )
    result = validate_patch(patch, context)
    codes = [i.code for i in result.issues]
    assert "lattice_transform.source_universe_missing" in codes, (
        f"expected source_universe_missing in {codes}"
    )


def test_cell_id_used_as_universe_detected() -> None:
    patch = _make_patch(
        replacement_universe_id="clad",
        source_universe_id="fuel_pin",
    )
    context = PatchValidationContext(
        known_universe_ids=["fuel_pin", "water_cell"],
        known_cell_ids=["clad"],
        cell_owner_universe_ids={"clad": ["fuel_pin"]},
    )
    result = validate_patch(patch, context)
    codes = [i.code for i in result.issues]
    assert "lattice_transform.cell_id_used_as_universe" in codes, (
        f"expected cell_id_used_as_universe in {codes}"
    )


def test_cell_id_unique_owner_repairable() -> None:
    patch = _make_patch(
        replacement_universe_id="clad",
        source_universe_id="fuel_pin",
    )
    context = PatchValidationContext(
        known_universe_ids=["fuel_pin", "water_cell"],
        known_cell_ids=["clad"],
        cell_owner_universe_ids={"clad": ["fuel_pin"]},
    )
    result = validate_patch(patch, context)
    cell_issue = next(
        i for i in result.issues
        if i.code == "lattice_transform.cell_id_used_as_universe"
    )
    assert "fuel_pin" in cell_issue.message, (
        f"repair message should name owning universe: {cell_issue.message!r}"
    )


def test_spacer_grid_misuse_detected() -> None:
    patch = _make_patch(
        operation_id="replace_water_with_grid",
        replacement_universe_id="grid_cell",
        source_universe_id="water_cell",
    )
    context = PatchValidationContext(
        known_universe_ids=["fuel_pin", "water_cell"],
        has_spacer_grids=True,
    )
    result = validate_patch(patch, context)
    codes = [i.code for i in result.issues]
    assert "lattice_transform.replacement_universe_missing" in codes, (
        f"expected replacement_universe_missing in {codes}"
    )
    assert "assembly3d.spacer_grid_transformation_misuse" in codes, (
        f"expected spacer_grid_transformation_misuse in {codes}"
    )


def test_valid_transformation_no_issues() -> None:
    patch = _make_patch(
        replacement_universe_id="fuel_pin",
        source_universe_id="water_cell",
    )
    context = PatchValidationContext(
        known_universe_ids=["fuel_pin", "water_cell"],
        known_lattice_ids=["assembly_lattice"],
    )
    result = validate_patch(patch, context)
    codes = [i.code for i in result.issues]
    assert not any(
        c.startswith("lattice_transform.")
        or c == "assembly3d.spacer_grid_transformation_misuse"
        for c in codes
    ), f"valid transformation produced issues: {codes}"
    assert result.ok, f"valid transformation should pass validation: {codes}"


def test_no_grid_misuse_without_spacer_grids() -> None:
    patch = _make_patch(
        operation_id="replace_water_with_grid",
        replacement_universe_id="grid_cell",
        source_universe_id="water_cell",
    )
    context = PatchValidationContext(
        known_universe_ids=["fuel_pin", "water_cell"],
        has_spacer_grids=False,
        known_overlay_summaries=[],
    )
    result = validate_patch(patch, context)
    codes = [i.code for i in result.issues]
    assert "lattice_transform.replacement_universe_missing" in codes, (
        f"expected replacement_universe_missing in {codes}"
    )
    assert "assembly3d.spacer_grid_transformation_misuse" not in codes, (
        f"grid misuse should not fire without spacer grids: {codes}"
    )
