"""Tests for core layout validator (P2-FULLCORE-1)."""

from openmc_agent.plan_builder.patches import CoreLayoutPatch
from openmc_agent.plan_builder.validators import (
    PatchValidationContext,
    validate_patch,
)


def test_valid_core_layout():
    layout = CoreLayoutPatch(
        shape=(2, 2),
        assembly_pitch_cm=21.5,
        assembly_pattern=[["a", "b"], ["b", "a"]],
        expected_assembly_type_counts={"a": 2, "b": 2},
        boundary="reflective",
    )
    ctx = PatchValidationContext(known_assembly_type_ids=["a", "b"])
    result = validate_patch(layout, ctx)
    assert result.ok


def test_shape_mismatch():
    layout = CoreLayoutPatch(
        shape=(3, 2),
        assembly_pattern=[["a", "b"]],  # 1 row but shape says 3
        boundary="vacuum",
    )
    result = validate_patch(layout)
    assert not result.ok
    assert any(i.code == "core_layout.shape_mismatch" for i in result.issues)


def test_row_length_mismatch():
    layout = CoreLayoutPatch(
        shape=(2, 2),
        assembly_pattern=[["a", "b"], ["a"]],  # second row too short
        boundary="vacuum",
    )
    result = validate_patch(layout)
    assert not result.ok
    assert any(i.code == "core_layout.row_length_mismatch" for i in result.issues)


def test_assembly_type_missing():
    layout = CoreLayoutPatch(
        shape=(1, 1),
        assembly_pattern=[["unknown_type"]],
        boundary="vacuum",
    )
    ctx = PatchValidationContext(known_assembly_type_ids=["type_a"])
    result = validate_patch(layout, ctx)
    assert not result.ok
    assert any(i.code == "core_layout.assembly_type_missing" for i in result.issues)


def test_multiplicity_mismatch():
    layout = CoreLayoutPatch(
        shape=(2, 2),
        assembly_pattern=[["a", "a"], ["a", "a"]],
        expected_assembly_type_counts={"a": 3},  # pattern has 4
        boundary="reflective",
    )
    result = validate_patch(layout)
    assert not result.ok
    assert any(i.code == "core_layout.multiplicity_mismatch" for i in result.issues)


def test_pitch_invalid():
    layout = CoreLayoutPatch(
        shape=(1, 1),
        assembly_pitch_cm=-1.0,
        assembly_pattern=[["a"]],
        boundary="vacuum",
    )
    result = validate_patch(layout)
    assert not result.ok
    assert any(i.code == "core_layout.pitch_invalid" for i in result.issues)


def test_boundary_empty_warning():
    layout = CoreLayoutPatch(
        shape=(1, 1),
        assembly_pattern=[["a"]],
        boundary="",
    )
    result = validate_patch(layout)
    assert result.ok  # warnings don't block
    assert any(i.code == "core_layout.boundary_missing" for i in result.issues)
