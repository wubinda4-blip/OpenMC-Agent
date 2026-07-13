"""Patch-level early validation for component-profile material slabs.

Tests that the patch validator catches ``assembly3d.component_profile_as_material_slab``
before assembly, not after.
"""

from __future__ import annotations

from openmc_agent.plan_builder.patches import AxialLayerPatchItem, AxialLayersPatch
from openmc_agent.plan_builder.validators import (
    PatchValidationContext,
    validate_patch,
)


def _make_axial_patch(
    layer_id: str = "lower_shoulder_gap",
    role: str = "shoulder_gap",
    fill_type: str = "material",
    fill_id: str | None = "borated_water",
) -> AxialLayersPatch:
    layers = [
        AxialLayerPatchItem(
            layer_id="active_fuel",
            role="active_fuel",
            z_min_cm=0.0,
            z_max_cm=100.0,
            fill_type="lattice",
            fill_id="assembly_lattice",
        ),
        AxialLayerPatchItem(
            layer_id=layer_id,
            role=role,
            z_min_cm=-10.0,
            z_max_cm=0.0,
            fill_type=fill_type,
            fill_id=fill_id,
        ),
    ]
    return AxialLayersPatch(layers=layers, axial_domain_cm=(-10.0, 100.0))


def test_patch_validator_catches_component_profile_material_slab() -> None:
    """A shoulder_gap layer with fill_type=material is caught at patch level."""
    patch = _make_axial_patch()
    result = validate_patch(patch, PatchValidationContext())
    codes = [i.code for i in result.issues]
    assert "assembly3d.component_profile_as_material_slab" in codes
    assert not result.ok


def test_patch_validator_accepts_shoulder_gap_with_lattice() -> None:
    """A shoulder_gap layer with fill_type=lattice passes."""
    patch = _make_axial_patch(fill_type="lattice", fill_id="assembly_lattice")
    result = validate_patch(patch, PatchValidationContext())
    codes = [i.code for i in result.issues]
    assert "assembly3d.component_profile_as_material_slab" not in codes


def test_patch_validator_catches_gas_gap_material_slab() -> None:
    """A gas_gap role with material fill is caught."""
    patch = _make_axial_patch(layer_id="gas_gap_layer", role="gas_gap")
    result = validate_patch(patch, PatchValidationContext())
    codes = [i.code for i in result.issues]
    assert "assembly3d.component_profile_as_material_slab" in codes


def test_patch_validator_catches_end_plug_material_slab() -> None:
    """An end_plug role with material fill is caught."""
    patch = _make_axial_patch(layer_id="end_plug", role="upper_end_plug")
    result = validate_patch(patch, PatchValidationContext())
    codes = [i.code for i in result.issues]
    assert "assembly3d.component_profile_as_material_slab" in codes


def test_patch_validator_ignores_non_profile_material_layer() -> None:
    """A reflector layer with material fill is fine (not a component profile)."""
    patch = _make_axial_patch(layer_id="reflector", role="reflector")
    result = validate_patch(patch, PatchValidationContext())
    codes = [i.code for i in result.issues]
    assert "assembly3d.component_profile_as_material_slab" not in codes


def test_patch_validator_rejects_unknown_material_fill_reference() -> None:
    """A material layer may only use material IDs from upstream patches."""
    patch = _make_axial_patch(
        layer_id="core_plate",
        role="core_plate",
        fill_id="invented_core_plate_mix",
    )
    result = validate_patch(
        patch,
        PatchValidationContext(known_material_ids=["borated_water", "ss304"]),
    )
    codes = [i.code for i in result.issues]
    assert "patch.axial_layers.fill_ref_missing" in codes
    assert not result.ok


def test_patch_validator_rejects_unknown_universe_fill_reference() -> None:
    """A universe layer may only use universe IDs from upstream patches."""
    patch = _make_axial_patch(
        layer_id="universe_layer",
        role="custom",
        fill_type="universe",
        fill_id="invented_plenum_universe",
    )
    result = validate_patch(
        patch,
        PatchValidationContext(known_universe_ids=["fuel_pin", "guide_tube"]),
    )
    codes = [i.code for i in result.issues]
    assert "patch.axial_layers.fill_ref_missing" in codes
    assert not result.ok


def test_patch_validator_rejects_moderator_filled_structural_slab() -> None:
    """A core plate/nozzle cannot silently become a pure moderator slab."""
    patch = _make_axial_patch(
        layer_id="core_plate",
        role="core_plate",
        fill_id="borated_water",
    )
    result = validate_patch(
        patch,
        PatchValidationContext(
            known_material_ids=["borated_water", "core_plate_mix"],
            material_roles_by_id={
                "borated_water": "coolant",
                "core_plate_mix": "structural",
            },
        ),
    )
    codes = [issue.code for issue in result.issues]
    assert "assembly3d.structural_slab_as_moderator" in codes
    assert not result.ok


def test_patch_validator_accepts_structural_mixture_for_structural_slab() -> None:
    """An input-defined structural mixture is valid for a core plate/nozzle."""
    patch = _make_axial_patch(
        layer_id="core_plate",
        role="core_plate",
        fill_id="core_plate_mix",
    )
    result = validate_patch(
        patch,
        PatchValidationContext(
            known_material_ids=["borated_water", "core_plate_mix"],
            material_roles_by_id={
                "borated_water": "coolant",
                "core_plate_mix": "structural",
            },
        ),
    )
    codes = [issue.code for issue in result.issues]
    assert "assembly3d.structural_slab_as_moderator" not in codes
    assert result.ok


def test_shoulder_gap_role_accepted_by_schema() -> None:
    """The shoulder_gap role is accepted by AxialLayerPatchItem."""
    layer = AxialLayerPatchItem(
        layer_id="test_shoulder",
        role="shoulder_gap",
        z_min_cm=0.0,
        z_max_cm=5.0,
        fill_type="lattice",
        fill_id="assembly_lattice",
    )
    assert layer.role == "shoulder_gap"


def test_lower_and_upper_shoulder_gap_roles_accepted() -> None:
    """Both lower_shoulder_gap and upper_shoulder_gap roles are accepted."""
    for r in ("lower_shoulder_gap", "upper_shoulder_gap"):
        layer = AxialLayerPatchItem(
            layer_id=f"test_{r}",
            role=r,
            z_min_cm=0.0,
            z_max_cm=5.0,
            fill_type="lattice",
            fill_id="assembly_lattice",
        )
        assert layer.role == r
