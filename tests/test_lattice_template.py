"""Tests for the LatticeSpec compact rect-lattice template (fill_universe + overrides)
and deterministic expander.

C5G7's 17x17 assemblies are the motivating case: the LLM should describe the pin map as
"fill with the majority pin, override these positions" instead of hand-enumerating 289
universe ids. The schema expands the template into universe_pattern deterministically and
can lock benchmark pin counts via expected_counts.
"""

import pytest
from pydantic import ValidationError

from openmc_agent.schemas import (
    AssemblySpec,
    CellSpec,
    ComplexMaterialSpec,
    ComplexModelSpec,
    LatticeSpec,
    PlotSpec,
    RenderCapabilityReport,
    SimulationPlan,
    UniverseSpec,
)
from openmc_agent.validator import validate_simulation_plan


# C5G7 UO2 17x17 guide-tube positions (Section VI of the case description),
# 0-indexed (row, col), row 0 = top. The fission chamber sits at the center (8, 8).
_UO2_GUIDE_TUBES = [
    (2, 5), (2, 8), (2, 11),
    (3, 3), (3, 13),
    (5, 2), (5, 5), (5, 8), (5, 11), (5, 14),
    (8, 2), (8, 5), (8, 11), (8, 14),
    (11, 2), (11, 5), (11, 8), (11, 11), (11, 14),
    (13, 3), (13, 13),
    (14, 5), (14, 8), (14, 11),
]
_UO2_FISSION_CHAMBER = (8, 8)


def test_rect_compact_template_expands_c5g7_uo2_assembly() -> None:
    lattice = LatticeSpec(
        id="uo2_assembly_lattice",
        name="UO2 17x17",
        kind="rect",
        pitch_cm=(1.26, 1.26),
        shape=(17, 17),
        fill_universe="uo2_pin",
        overrides={
            "guide_tube_pin": _UO2_GUIDE_TUBES,
            "fission_chamber_pin": [_UO2_FISSION_CHAMBER],
        },
        expected_counts={
            "uo2_pin": 264,
            "guide_tube_pin": 24,
            "fission_chamber_pin": 1,
        },
    )

    pattern = lattice.universe_pattern
    assert len(pattern) == 17
    assert {len(row) for row in pattern} == {17}
    # Center is the fission chamber (row 8 = 9th row, col 8 = 9th column).
    assert pattern[8][8] == "fission_chamber_pin"
    # Guide tubes landed exactly where specified.
    for position in _UO2_GUIDE_TUBES:
        assert pattern[position[0]][position[1]] == "guide_tube_pin"

    # Benchmark counts enforced: 264 + 24 + 1 = 289.
    counts: dict[str, int] = {}
    for row in pattern:
        for universe_id in row:
            counts[universe_id] = counts.get(universe_id, 0) + 1
    assert counts == {"uo2_pin": 264, "guide_tube_pin": 24, "fission_chamber_pin": 1}


def test_rect_compact_template_supports_multiple_override_zones() -> None:
    """A small 3x3 lattice with three fuel zones + a guide tube exercises multi-layer
    overrides and the count summing, without claiming to be an exact C5G7 MOX map."""
    lattice = LatticeSpec(
        id="mini_mox",
        name="mini mox",
        kind="rect",
        pitch_cm=(1.26, 1.26),
        shape=(3, 3),
        fill_universe="mox43_pin",
        overrides={
            "mox87_pin": [(1, 1)],
            "mox70_pin": [(0, 0), (0, 1)],
            "guide_tube_pin": [(2, 2)],
        },
        expected_counts={
            "mox43_pin": 5,
            "mox70_pin": 2,
            "mox87_pin": 1,
            "guide_tube_pin": 1,
        },
    )

    pattern = lattice.universe_pattern
    assert pattern[0][0] == "mox70_pin"
    assert pattern[1][1] == "mox87_pin"
    assert pattern[2][2] == "guide_tube_pin"
    assert pattern[0][2] == "mox43_pin"  # untouched fill


def test_explicit_universe_pattern_wins_over_compact_template() -> None:
    """If the LLM provides a full universe_pattern, the template must not clobber it."""
    lattice = LatticeSpec(
        id="explicit",
        name="explicit",
        kind="rect",
        pitch_cm=(1.26, 1.26),
        shape=(2, 2),
        fill_universe="a",
        overrides={"b": [(0, 0)]},
        universe_pattern=[["x", "y"], ["z", "w"]],
    )

    assert lattice.universe_pattern == [["x", "y"], ["z", "w"]]


def test_compact_template_requires_shape() -> None:
    with pytest.raises(ValidationError) as exc_info:
        LatticeSpec(
            id="no_shape",
            name="no_shape",
            kind="rect",
            pitch_cm=(1.26, 1.26),
            fill_universe="a",
            overrides={"b": [(0, 0)]},
        )
    assert "shape" in str(exc_info.value).lower()


def test_override_out_of_bounds_raises() -> None:
    with pytest.raises(ValidationError) as exc_info:
        LatticeSpec(
            id="oob",
            name="oob",
            kind="rect",
            pitch_cm=(1.26, 1.26),
            shape=(2, 2),
            fill_universe="a",
            overrides={"b": [(5, 0)]},
        )
    assert "out of bounds" in str(exc_info.value)


def test_expected_counts_mismatch_is_flagged_not_raised() -> None:
    """A count mismatch must NOT kill plan construction. It is flagged for human
    review so the workflow can still emit a reviewable skeleton; the renderer blocks
    export separately. (Killing construction here would turn a recoverable gap into
    'produce nothing after retries'.)"""
    lattice = LatticeSpec(
        id="bad_counts",
        name="bad_counts",
        kind="rect",
        pitch_cm=(1.26, 1.26),
        shape=(3, 3),
        fill_universe="a",
        overrides={"b": [(0, 0)]},
        expected_counts={"a": 100, "b": 1},  # actually a=8, b=1
    )

    # Construction succeeded (no raise):
    assert len(lattice.universe_pattern) == 3
    flagged = " ".join(lattice.requires_human_confirmation)
    assert "pin count mismatch vs expected_counts" in flagged
    assert "a: expected 100, got 8" in flagged


@pytest.mark.openmc
def test_expected_counts_mismatch_blocks_export() -> None:
    pytest.importorskip("openmc", reason="OpenMC is required for renderer import")
    """The renderer must block XML export on a count mismatch so a wrong pin map
    can never silently become a runnable model."""
    from openmc_agent.renderers.assembly import _lattice_pattern_errors

    lattice = LatticeSpec(
        id="bad_counts",
        name="bad_counts",
        kind="rect",
        pitch_cm=(1.26, 1.26),
        shape=(3, 3),
        fill_universe="a",
        overrides={"b": [(0, 0)]},
        expected_counts={"a": 100, "b": 1},
    )
    errors = _lattice_pattern_errors(lattice, universe_ids={"a", "b"})
    assert any("pin counts do not match expected_counts" in err for err in errors)
    assert any("a: expected 100, got 8" in err for err in errors)


@pytest.mark.openmc
def test_expected_counts_match_passes_export_check() -> None:
    pytest.importorskip("openmc", reason="OpenMC is required for renderer import")
    from openmc_agent.renderers.assembly import _lattice_pattern_errors

    lattice = LatticeSpec(
        id="good_counts",
        name="good_counts",
        kind="rect",
        pitch_cm=(1.26, 1.26),
        shape=(3, 3),
        fill_universe="a",
        overrides={"b": [(0, 0)]},
        expected_counts={"a": 8, "b": 1},
    )
    assert _lattice_pattern_errors(lattice, universe_ids={"a", "b"}) == []


def test_compact_template_tolerates_null_overrides() -> None:
    """LLMs sometimes emit ``null``; overrides must coerce to an empty map."""
    lattice = LatticeSpec(
        id="null_ovr",
        name="null_ovr",
        kind="rect",
        pitch_cm=(1.26, 1.26),
        shape=(2, 2),
        fill_universe="a",
        overrides=None,
    )
    assert lattice.universe_pattern == [["a", "a"], ["a", "a"]]


def test_single_value_shape_is_square() -> None:
    lattice = LatticeSpec(
        id="square",
        name="square",
        kind="rect",
        pitch_cm=(1.26, 1.26),
        shape=(4,),  # 4x4
        fill_universe="a",
    )
    assert len(lattice.universe_pattern) == 4
    assert all(len(row) == 4 for row in lattice.universe_pattern)


def test_compact_template_expanded_pattern_is_rectangular_and_consistent() -> None:
    """The expanded pattern must satisfy the assembly renderer's shape check."""
    lattice = LatticeSpec(
        id="uo2_assembly_lattice",
        name="UO2 17x17",
        kind="rect",
        pitch_cm=(1.26, 1.26),
        shape=(17, 17),
        fill_universe="uo2_pin",
        overrides={"guide_tube_pin": _UO2_GUIDE_TUBES},
    )
    rows = len(lattice.universe_pattern)
    cols = len(lattice.universe_pattern[0])
    # Renderer derives (rows, cols) = (ny, nx) from shape (nx, ny) = (17, 17).
    assert (rows, cols) == lattice._rect_shape()


def test_compact_template_lattice_inside_simulation_plan_validates() -> None:
    plan = SimulationPlan(
        schema_version="simulation_plan.v2",
        model_spec=None,
        complex_model=ComplexModelSpec(
            name="C5G7 UO2 assembly IR",
            kind="assembly",
            materials=[
                ComplexMaterialSpec(
                    id="uo2",
                    name="UO2",
                    chemical_formula="UO2",
                    requires_human_confirmation=["enrichment"],
                ),
                ComplexMaterialSpec(
                    id="guide_tube",
                    name="guide tube",
                    chemical_formula="Zr",
                    requires_human_confirmation=["alloy composition"],
                ),
            ],
            cells=[
                CellSpec(id="uo2_cell", name="uo2", fill_type="material", fill_id="uo2"),
                CellSpec(
                    id="gt_cell", name="guide tube", fill_type="material", fill_id="guide_tube"
                ),
            ],
            universes=[
                UniverseSpec(id="uo2_pin", name="uo2 pin", cell_ids=["uo2_cell"]),
                UniverseSpec(id="guide_tube_pin", name="gt pin", cell_ids=["gt_cell"]),
            ],
            lattices=[
                LatticeSpec(
                    id="uo2_assembly_lattice",
                    name="UO2 17x17",
                    kind="rect",
                    pitch_cm=(1.26, 1.26),
                    shape=(17, 17),
                    fill_universe="uo2_pin",
                    overrides={"guide_tube_pin": _UO2_GUIDE_TUBES},
                )
            ],
            assemblies=[
                AssemblySpec(
                    id="assembly", name="UO2 assembly", lattice_id="uo2_assembly_lattice"
                )
            ],
        ),
        capability_report=RenderCapabilityReport(
            is_executable=False, supported_renderer="none"
        ),
        plot_specs=[PlotSpec(basis="xy", width_cm=(21.42, 21.42), filename="uo2_xy.png")],
    )

    report = validate_simulation_plan(plan)
    assert report.is_valid is True

    lattice = plan.complex_model.lattices[0]
    assert len(lattice.universe_pattern) == 17  # expanded, not deferred
    # No "universe_pattern is missing" confirmation because the template filled it.
    assert "rect lattice universe_pattern is missing" not in (
        lattice.requires_human_confirmation
    )
