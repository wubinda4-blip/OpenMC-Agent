from openmc_agent.executor import render_openmc_script
from openmc_agent.schemas import (
    ComplexMaterialSpec,
    ComplexModelSpec,
    GeometrySpec,
    LatticeSpec,
    MaterialSpec,
    NuclideSpec,
    PinCellSpec,
    PlotSpec,
    RenderCapabilityReport,
    SettingsSpec,
    SimulationPlan,
    SimulationSpec,
)
from openmc_agent.lattice_validation import (
    canonical_pin_map_rows,
    extract_canonical_pin_map,
    is_structural_error_confirmation,
    lattice_cell_mismatches,
)
from openmc_agent.validator import (
    validate_openmc_script,
    validate_simulation_plan,
    validate_simulation_spec,
)


def make_standard_spec() -> SimulationSpec:
    fuel = MaterialSpec(
        name="UO2 fuel",
        density_unit="g/cm3",
        density_value=10.4,
        composition=[
            NuclideSpec(name="U235", percent=4.95),
            NuclideSpec(name="U238", percent=95.05),
            NuclideSpec(name="O16", percent=200.0),
        ],
    )
    moderator = MaterialSpec(
        name="Water moderator",
        density_unit="g/cm3",
        density_value=1.0,
        composition=[
            NuclideSpec(name="H1", percent=2.0),
            NuclideSpec(name="O16", percent=1.0),
        ],
    )
    return SimulationSpec(
        name="UO2 pin-cell criticality",
        pin_cell=PinCellSpec(
            fuel=fuel,
            moderator=moderator,
            geometry=GeometrySpec(fuel_radius_cm=0.41, pitch_cm=1.26),
        ),
        settings=SettingsSpec(batches=50, inactive=10, particles=1000),
    )


def test_validate_simulation_spec_accepts_standard_pin_cell() -> None:
    report = validate_simulation_spec(make_standard_spec())

    assert report.is_valid is True
    assert report.errors == []


def test_validate_simulation_spec_rejects_obvious_bad_fuel_radius() -> None:
    spec = make_standard_spec()
    spec.pin_cell.geometry = GeometrySpec.model_construct(
        fuel_radius_cm=10.0,
        pitch_cm=1.26,
        clad_inner_radius_cm=None,
        clad_outer_radius_cm=None,
    )

    report = validate_simulation_spec(spec)

    assert report.is_valid is False
    assert any("fuel_radius_cm" in error and "10.0" in error for error in report.errors)


def test_validate_openmc_script_requires_core_structures() -> None:
    report = validate_openmc_script("import openmc\nmodel.export_to_xml()\n")

    assert report.is_valid is False
    assert "materials" in " ".join(report.errors)
    assert "geometry" in " ".join(report.errors)
    assert "settings" in " ".join(report.errors)
    assert "tallies" in " ".join(report.errors)


def test_validate_openmc_script_accepts_rendered_script() -> None:
    script = render_openmc_script(make_standard_spec())

    report = validate_openmc_script(script, make_standard_spec())

    assert report.is_valid is True
    assert report.errors == []


def _plan_with_lattice(
    expected_counts: dict[str, int] | None,
    pattern: list[list[str]],
) -> SimulationPlan:
    """Minimal core plan carrying one lattice for pin-count validation tests."""
    return SimulationPlan(
        schema_version="simulation_plan.v2",
        model_spec=None,
        complex_model=ComplexModelSpec(
            name="pin-count check",
            kind="core",
            lattices=[
                LatticeSpec(
                    id="assm",
                    name="assm",
                    kind="rect",
                    pitch_cm=(1.26, 1.26),
                    universe_pattern=pattern,
                    expected_counts=expected_counts,
                )
            ],
        ),
        capability_report=RenderCapabilityReport(
            is_executable=True, supported_renderer="core"
        ),
        plot_specs=[PlotSpec(basis="xy", width_cm=(1.0, 1.0), filename="p.png")],
    )


def test_validate_plan_flags_lattice_pin_count_mismatch() -> None:
    """A wrong pin map is caught with a per-material diff routed to reflect_plan.

    This is the C5G7 MOX regression: the LLM hand-expands a dense coordinate
    description and miscounts. ``expected_counts`` (transcribed from the input
    document) is the deterministic ground truth; the validator surfaces the exact
    diff so reflect_plan can fix it instead of guessing.
    """
    plan = _plan_with_lattice(
        expected_counts={"A": 2, "B": 4},
        pattern=[["A", "A", "A"], ["A", "B", "B"]],
    )

    report = validate_simulation_plan(plan)

    assert report.is_valid is False
    mismatches = [i for i in report.issues if i.code == "lattice.pin_count_mismatch"]
    assert len(mismatches) == 1
    msg = mismatches[0].message
    assert "A: actual=4 expected=2 (diff +2)" in msg
    assert "B: actual=2 expected=4 (diff -2)" in msg
    assert mismatches[0].route_hint == "reflect_plan"
    assert mismatches[0].severity == "error"
    assert mismatches[0].schema_path == "complex_model.lattices.assm.universe_pattern"


def test_validate_plan_accepts_matching_expected_counts() -> None:
    plan = _plan_with_lattice(
        expected_counts={"A": 4, "B": 2},
        pattern=[["A", "A", "A"], ["A", "B", "B"]],
    )

    report = validate_simulation_plan(plan)

    assert report.is_valid is True
    assert not any(i.code == "lattice.pin_count_mismatch" for i in report.issues)


def test_validate_plan_skips_lattices_without_expected_counts() -> None:
    """Legacy plans without expected_counts are unaffected (opt-in check)."""
    plan = _plan_with_lattice(
        expected_counts=None,
        pattern=[["A", "A", "A"], ["A", "B", "B"]],
    )

    report = validate_simulation_plan(plan)

    assert not any(i.code == "lattice.pin_count_mismatch" for i in report.issues)


def test_validate_plan_pin_count_mismatch_includes_shape_note() -> None:
    """When expected_counts sum != rows*cols, the diff message says so."""
    plan = _plan_with_lattice(
        expected_counts={"A": 2, "B": 1},
        pattern=[["A", "A", "A"], ["A", "B", "B"]],
    )

    report = validate_simulation_plan(plan)

    mismatch = next(
        i for i in report.issues if i.code == "lattice.pin_count_mismatch"
    )
    assert "expected_counts sum 3 != rows*cols 6" in mismatch.message


def test_extract_canonical_pin_map_parses_mox_rows_and_symbols() -> None:
    """The MOX canonical pin map is parsed into a universe-id grid + symbol map.

    This is the data reflect_plan needs to locate mis-positioned pins instead of
    re-guessing a 17x17 pattern from a count diff alone (the C5G7 MOX regression
    where three LLM reflections returned a byte-identical wrong pattern).
    """
    requirement = (
        "### 7.1 MOX 符号表\n\n"
        "| 符号 | 含义 | 建议 pin universe |\n"
        "|---|---|---|\n"
        "| A | 4.3% MOX 燃料棒 | `mox43_pin` |\n"
        "| B | 7.0% MOX 燃料棒 | `mox7_pin` |\n"
        "| C | 8.7% MOX 燃料棒 | `mox87_pin` |\n"
        "| F | 裂变室 | `fiss_chamber_pin` |\n\n"
        "### 7.2 MOX canonical pin map\n\n"
        "```text\n"
        "R01: A A A\n"
        "R02: A B A\n"
        "R03: A A A\n"
        "```\n"
    )
    cmap = extract_canonical_pin_map(requirement, "mox_assembly")
    assert cmap is not None
    assert len(cmap.rows) == 3
    assert cmap.rows[0] == ["mox43_pin", "mox43_pin", "mox43_pin"]
    assert cmap.rows[1] == ["mox43_pin", "mox7_pin", "mox43_pin"]
    assert cmap.symbol_map["B"] == "mox7_pin"
    assert cmap.symbol_map["F"] == "fiss_chamber_pin"


def test_extract_canonical_pin_map_returns_none_when_absent() -> None:
    assert extract_canonical_pin_map("no pin map here", "mox_assembly") is None


def test_extract_canonical_pin_map_selects_section_by_lattice_id() -> None:
    """When the requirement carries both UO2 and MOX maps, lattice id picks.

    The section keyword is read from the symbol table's universe values, not
    from the surrounding prose: an earlier paragraph that mentions 'MOX' near
    the UO2 map must NOT re-bind the UO2 section to the mox keyword (the C5G7
    case3 regression where mox_assembly was handed the UO2 R01: U U U... map).
    """
    requirement = (
        "前面提到 MOX 组件计数：A=64, B=100, C=100。\n\n"
        "### UO2 canonical pin map\n\n"
        "| 符号 | 含义 | 建议 pin universe |\n|---|---|---|\n"
        "| U | UO2 | `uo2_pin` |\n\n"
        "```text\nR01: U U\nR02: U U\n```\n\n"
        "### MOX canonical pin map\n\n"
        "| 符号 | 含义 | 建议 pin universe |\n|---|---|---|\n"
        "| A | MOX | `mox43_pin` |\n\n"
        "```text\nR01: A A\nR02: A A\n```\n"
    )
    mox = extract_canonical_pin_map(requirement, "mox_assembly")
    assert mox is not None
    assert mox.rows[0] == ["mox43_pin", "mox43_pin"]
    uo2 = extract_canonical_pin_map(requirement, "uo2_assembly")
    assert uo2 is not None
    assert uo2.rows[0] == ["uo2_pin", "uo2_pin"]


def test_lattice_cell_mismatches_locates_mispositioned_pins() -> None:
    """A single swapped pin is reported as a 1-indexed (row, col, expected, actual)."""
    canonical = [
        ["mox43_pin", "mox43_pin", "mox43_pin"],
        ["mox43_pin", "mox7_pin", "mox43_pin"],
        ["mox43_pin", "mox43_pin", "mox43_pin"],
    ]
    actual = [
        ["mox43_pin", "mox43_pin", "mox43_pin"],
        ["mox43_pin", "mox87_pin", "mox43_pin"],
        ["mox43_pin", "mox43_pin", "mox43_pin"],
    ]
    assert lattice_cell_mismatches(actual, canonical) == [
        (2, 2, "mox7_pin", "mox87_pin")
    ]


def test_lattice_cell_mismatches_empty_when_shapes_differ() -> None:
    """Differing grid shapes cannot be cell-compared; return empty (count diff still fires)."""
    assert lattice_cell_mismatches([["A"]], [["A", "B"], ["C", "D"]]) == []


_MOX3X3_PIN_MAP_REQ = (
    "### MOX 符号表\n\n"
    "| 符号 | 含义 | 建议 pin universe |\n|---|---|---|\n"
    "| A | 4.3% MOX | `mox43_pin` |\n"
    "| B | 7.0% MOX | `mox7_pin` |\n"
    "| C | 8.7% MOX | `mox87_pin` |\n\n"
    "### MOX canonical pin map\n\n```text\n"
    "R01: A A A\nR02: A B A\nR03: A A A\n```\n"
)


def _mox3x3_lattice(pattern: list[list[str]]) -> LatticeSpec:
    return LatticeSpec(
        id="mox_assembly",
        name="MOX assembly",
        kind="rect",
        pitch_cm=(1.26, 1.26),
        universe_pattern=pattern,
        expected_counts={"mox43_pin": 8, "mox7_pin": 1},
    )


def test_canonical_pin_map_rows_returns_grid_when_pattern_differs() -> None:
    """A mismatched lattice yields the canonical universe grid to overwrite it."""
    lattice = _mox3x3_lattice(
        [
            ["mox43_pin", "mox43_pin", "mox43_pin"],
            ["mox43_pin", "mox87_pin", "mox43_pin"],  # canonical says mox7_pin
            ["mox43_pin", "mox43_pin", "mox43_pin"],
        ]
    )
    rows = canonical_pin_map_rows(lattice, _MOX3X3_PIN_MAP_REQ)
    assert rows is not None
    assert rows[1][1] == "mox7_pin"
    assert rows[0] == ["mox43_pin", "mox43_pin", "mox43_pin"]


def test_canonical_pin_map_rows_none_when_no_canonical_map() -> None:
    lattice = _mox3x3_lattice([["mox43_pin"]])
    assert canonical_pin_map_rows(lattice, "no pin map here") is None


def test_canonical_pin_map_rows_none_when_shape_mismatch() -> None:
    """A 1x2 pattern vs a 3x3 canonical map cannot be safely overwritten."""
    lattice = _mox3x3_lattice([["mox43_pin", "mox87_pin"]])
    assert canonical_pin_map_rows(lattice, _MOX3X3_PIN_MAP_REQ) is None


def test_canonical_pin_map_rows_none_when_already_matches() -> None:
    """No patch when the pattern already equals the canonical grid."""
    lattice = _mox3x3_lattice(
        [
            ["mox43_pin", "mox43_pin", "mox43_pin"],
            ["mox43_pin", "mox7_pin", "mox43_pin"],
            ["mox43_pin", "mox43_pin", "mox43_pin"],
        ]
    )
    assert canonical_pin_map_rows(lattice, _MOX3X3_PIN_MAP_REQ) is None


def test_is_structural_error_confirmation_flags_structural_defects() -> None:
    """LLM-written confirmations that describe structural defects must be flagged
    so they are not turned into expert questions (the agent fixes them itself)."""
    assert is_structural_error_confirmation(
        "pin count mismatch vs expected_counts: mox7_pin: expected 100, got 98"
    )
    assert is_structural_error_confirmation("lattice references empty universes")
    assert is_structural_error_confirmation("universe_pattern is missing")


def test_is_structural_error_confirmation_preserves_physics_questions() -> None:
    """Genuine physics/modeling confirmations are left alone."""
    assert not is_structural_error_confirmation("fuel temperature 900K")
    assert not is_structural_error_confirmation("UO2 density 10.4 g/cm3")
    assert not is_structural_error_confirmation("boundary condition reflective")


def _plan_with_materials(materials: list[ComplexMaterialSpec]) -> SimulationPlan:
    """Minimal core plan carrying materials for percent-type validation tests."""
    return SimulationPlan(
        schema_version="simulation_plan.v2",
        model_spec=None,
        complex_model=ComplexModelSpec(
            name="material check",
            kind="core",
            materials=materials,
        ),
        capability_report=RenderCapabilityReport(
            is_executable=True, supported_renderer="core"
        ),
        plot_specs=[PlotSpec(basis="xy", width_cm=(1.0, 1.0), filename="p.png")],
    )


def test_validate_plan_flags_material_mixed_percent_type() -> None:
    """Mixed ao/wo without chemical_formula is flagged and routed to reflect_plan.

    The VERA UO2 regression: U isotopes in 'wo' but O16 in 'ao' from a
    stoichiometric O/U ratio. The validator surfaces it at plan-validation time so
    SELF_REPAIRABLE_CODES routes it to reflect_plan instead of ask_expert.
    """
    plan = _plan_with_materials(
        [
            ComplexMaterialSpec(
                id="fuel",
                name="UO2 fuel",
                density_unit="g/cm3",
                density_value=10.4,
                composition=[
                    NuclideSpec(name="U235", percent=3.1, percent_type="wo"),
                    NuclideSpec(name="U238", percent=96.9, percent_type="wo"),
                    NuclideSpec(name="O16", percent=2.0, percent_type="ao"),
                ],
            ),
        ]
    )

    report = validate_simulation_plan(plan)

    assert report.is_valid is False
    issues = [i for i in report.issues if i.code == "material.mixed_percent_type"]
    assert len(issues) == 1
    assert issues[0].route_hint == "reflect_plan"
    assert issues[0].severity == "error"
    assert "'ao'" in issues[0].message and "'wo'" in issues[0].message


def test_validate_plan_accepts_mixed_percent_with_chemical_formula() -> None:
    """Mixed ao/wo with chemical_formula is legal (executor formula fallback)."""
    plan = _plan_with_materials(
        [
            ComplexMaterialSpec(
                id="fuel",
                name="UO2 fuel",
                density_unit="g/cm3",
                density_value=10.4,
                composition=[
                    NuclideSpec(name="U235", percent=3.1, percent_type="wo"),
                    NuclideSpec(name="U238", percent=96.9, percent_type="wo"),
                    NuclideSpec(name="O16", percent=2.0, percent_type="ao"),
                ],
                chemical_formula="UO2",
            ),
        ]
    )

    report = validate_simulation_plan(plan)

    assert not any(i.code == "material.mixed_percent_type" for i in report.issues)


def test_validate_plan_skips_macroscopic_materials_for_percent_check() -> None:
    """Macroscopic materials carry no nuclide composition and are exempt."""
    plan = _plan_with_materials(
        [
            ComplexMaterialSpec(
                id="water",
                name="C5G7 water",
                density_unit="macro",
                macroscopic="water",
            ),
        ]
    )

    report = validate_simulation_plan(plan)

    assert not any(i.code == "material.mixed_percent_type" for i in report.issues)
