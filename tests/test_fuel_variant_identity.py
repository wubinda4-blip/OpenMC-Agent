"""Tests for post-decoration fuel variant identity verification."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest

from openmc_agent.plan_builder.grid_geometry_validation import (
    FuelVariantIdentityReport,
    verify_fuel_variant_identity_after_decoration,
    CODE_FUEL_IDENTITY_LOST,
    CODE_GRID_REPLACED_PROTECTED_PATH,
    CODE_FUEL_BINDING_MISMATCH,
)


@dataclass
class _MockCell:
    id: str
    role: str
    material_id: str | None = None
    component_role: str | None = None


@dataclass
class _MockUniverse:
    id: str
    cells: list[_MockCell]


# ---------------------------------------------------------------------------
# Helpers (reactor-neutral)
# ---------------------------------------------------------------------------

def _base_fuel_universe(uid: str = "u_fuel_low", fuel_mat: str = "fuel_low") -> _MockUniverse:
    return _MockUniverse(id=uid, cells=[
        _MockCell(id="pellet", role="fuel", material_id=fuel_mat),
        _MockCell(id="gap", role="gap", material_id="helium"),
        _MockCell(id="clad", role="cladding", material_id="zircaloy"),
        _MockCell(id="bg", role="background", material_id="water"),
    ])


def _decorated_fuel_universe(uid: str = "u_fuel_low_grid_abc",
                             fuel_mat: str = "fuel_low") -> _MockUniverse:
    return _MockUniverse(id=uid, cells=[
        _MockCell(id="pellet", role="fuel", material_id=fuel_mat),
        _MockCell(id="gap", role="gap", material_id="helium"),
        _MockCell(id="clad", role="cladding", material_id="zircaloy"),
        _MockCell(id="grid_frame", role="grid_frame", material_id="grid_inconel"),
        _MockCell(id="bg", role="background", material_id="water"),
    ])


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_fuel_identity_preserved_after_decoration() -> None:
    """Grid decoration preserves all protected paths."""
    base = [_base_fuel_universe()]
    decorated = [_decorated_fuel_universe()]
    report = verify_fuel_variant_identity_after_decoration(
        base_universes=base,
        decorated_universes=decorated,
    )
    assert report.result == "pass"
    assert len(report.issues) == 0


def test_fuel_identity_lost_when_material_changed() -> None:
    """Decorated universe changes fuel material → identity lost."""
    base = [_base_fuel_universe(fuel_mat="fuel_low")]
    decorated = [_decorated_fuel_universe(fuel_mat="fuel_high")]
    report = verify_fuel_variant_identity_after_decoration(
        base_universes=base,
        decorated_universes=decorated,
    )
    assert report.result == "fail"
    codes = [i["code"] for i in report.issues]
    assert CODE_FUEL_IDENTITY_LOST in codes


def test_protected_path_removed_by_decoration() -> None:
    """Grid decoration removes a protected cell → error."""
    base = [_base_fuel_universe()]
    bad_decorated = [_MockUniverse(id="u_fuel_low_grid_abc", cells=[
        _MockCell(id="pellet", role="fuel", material_id="fuel_low"),
        # gap removed!
        _MockCell(id="clad", role="cladding", material_id="zircaloy"),
        _MockCell(id="grid_frame", role="grid_frame", material_id="grid_inconel"),
        _MockCell(id="bg", role="background", material_id="water"),
    ])]
    report = verify_fuel_variant_identity_after_decoration(
        base_universes=base,
        decorated_universes=bad_decorated,
    )
    assert report.result == "fail"
    codes = [i["code"] for i in report.issues]
    assert CODE_GRID_REPLACED_PROTECTED_PATH in codes


def test_grid_frame_uses_fuel_material_error() -> None:
    """Grid frame cell using fuel material → error."""
    base = [_base_fuel_universe()]
    bad_decorated = [_MockUniverse(id="u_fuel_low_grid_abc", cells=[
        _MockCell(id="pellet", role="fuel", material_id="fuel_low"),
        _MockCell(id="gap", role="gap", material_id="helium"),
        _MockCell(id="clad", role="cladding", material_id="zircaloy"),
        _MockCell(id="grid_frame", role="grid_frame", material_id="fuel_low"),
        _MockCell(id="bg", role="background", material_id="water"),
    ])]
    report = verify_fuel_variant_identity_after_decoration(
        base_universes=base,
        decorated_universes=bad_decorated,
        fuel_variant_requirements=[{"variant_id": "fuel_low"}],
    )
    assert report.result == "fail"
    codes = [i["code"] for i in report.issues]
    assert CODE_GRID_REPLACED_PROTECTED_PATH in codes


def test_fuel_binding_mismatch_detected() -> None:
    """Assembly references unknown fuel variant → error."""
    report = verify_fuel_variant_identity_after_decoration(
        base_universes=[],
        decorated_universes=[],
        fuel_variant_requirements=[{"variant_id": "v1"}, {"variant_id": "v2"}],
        assembly_fuel_bindings=[
            {"assembly_type_id": "a", "fuel_variant_id": "v1"},
            {"assembly_type_id": "b", "fuel_variant_id": "vX"},  # unknown
        ],
    )
    assert report.result == "fail"
    codes = [i["code"] for i in report.issues]
    assert CODE_FUEL_BINDING_MISMATCH in codes


def test_two_variants_both_preserved() -> None:
    """Two fuel variants both preserved after independent decoration."""
    base = [
        _base_fuel_universe("u_fuel_a", "fuel_a"),
        _base_fuel_universe("u_fuel_b", "fuel_b"),
    ]
    decorated = [
        _decorated_fuel_universe("u_fuel_a_grid_x", "fuel_a"),
        _decorated_fuel_universe("u_fuel_b_grid_y", "fuel_b"),
    ]
    report = verify_fuel_variant_identity_after_decoration(
        base_universes=base,
        decorated_universes=decorated,
    )
    assert report.result == "pass"


def test_no_decorated_universes_passes() -> None:
    """No decorated universes → trivially passes."""
    report = verify_fuel_variant_identity_after_decoration(
        base_universes=[_base_fuel_universe()],
        decorated_universes=[],
    )
    assert report.result == "pass"
