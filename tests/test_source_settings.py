"""Tests for OpenMC source/settings validation, active-fuel source binding, and
the source-rejection runtime parser (Step 5).

The VERA3 smoke-test crash ('Too few source sites satisfied the constraints')
came from binding the initial source to the full axial domain with
only_fissionable=True. These tests confirm the source is now bound to the
active-fuel region and that an OpenMC source-rejection stderr is parsed into a
structured primary issue (not masked by the later segfault noise).
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.openmc
openmc = pytest.importorskip(
    "openmc", reason="OpenMC is required for this integration test"
)

from helpers.vera3_acceptance import build_vera3_like_plan, load_vera3_reference
from openmc_agent.renderers.assembly import RectAssemblyRenderer
from openmc_agent.schemas import (
    AxialLayerSpec,
    CellSpec,
    ComplexMaterialSpec,
    ComplexModelSpec,
    CoreSpec,
    LatticeSpec,
    NuclideSpec,
    UniverseSpec,
)
from openmc_agent.source_settings import (
    SourceBounds,
    active_fuel_z_bounds,
    alloy_pure_element_issues,
    fuel_material_ids,
    source_bounds_for_plan,
    validate_source_settings,
)
from openmc_agent.tools import parse_openmc_output

REFERENCE = load_vera3_reference()


# -- 1/2. source z uses active fuel region; x/y uses lattice footprint --------


def test_source_z_bound_to_active_fuel_region() -> None:
    plan = build_vera3_like_plan(REFERENCE, variant="3A")
    bounds = source_bounds_for_plan(plan.complex_model)
    assert bounds is not None
    assert bounds.z_bound_to_active_fuel is True
    # active fuel is 11.951 ~ 377.711 cm (from the reference axial layers).
    assert bounds.z_min == pytest.approx(11.951)
    assert bounds.z_max == pytest.approx(377.711)
    # NOT the full domain, NOT -1..1.
    assert bounds.z_min > 0.0
    assert bounds.z_max < 400.0


def test_rendered_script_source_uses_active_fuel_z(tmp_path) -> None:
    plan = build_vera3_like_plan(REFERENCE, variant="3A")
    script = RectAssemblyRenderer().render(plan, tmp_path).script
    assert "source_z_min = 11.951" in script
    assert "source_z_max = 377.711" in script
    assert "only_fissionable=True" in script


# -- 3. default z extent triggers validator --------------------------------


def test_default_z_source_triggers_issue() -> None:
    plan = build_vera3_like_plan(REFERENCE, variant="3A")
    bounds = SourceBounds(-10.0, 10.0, -10.0, 10.0, -1.0, 1.0, z_bound_to_active_fuel=False)
    issues = validate_source_settings(plan, source_bounds=bounds)
    codes = {i.code for i in issues}
    assert "runtime.source_default_z_extent" in codes


# -- 4. source outside active fuel triggers issue --------------------------


def test_source_outside_active_fuel_triggers_issue() -> None:
    plan = build_vera3_like_plan(REFERENCE, variant="3A")
    # active fuel is 11.951~377.711; source at 400~450 does not overlap.
    bounds = SourceBounds(-10.0, 10.0, -10.0, 10.0, 400.0, 450.0, z_bound_to_active_fuel=False)
    issues = validate_source_settings(plan, source_bounds=bounds)
    codes = {i.code for i in issues}
    assert "runtime.source_not_in_active_fuel_region" in codes


# -- 5. source covers nonfuel axial regions warning ------------------------


def test_source_covers_nonfuel_regions_warning() -> None:
    plan = build_vera3_like_plan(REFERENCE, variant="3A")
    # active fuel 11.951~377.711; source -55~463 spans far beyond -> >50% nonfuel.
    bounds = SourceBounds(-10.0, 10.0, -10.0, 10.0, -55.0, 463.937, z_bound_to_active_fuel=False)
    issues = validate_source_settings(plan, source_bounds=bounds)
    codes = {i.code for i in issues}
    assert "runtime.source_covers_nonfuel_axial_regions" in codes


# -- 6. missing fissionable fuel material ----------------------------------


def test_fuel_material_not_fissionable_triggers_issue() -> None:
    plan = build_vera3_like_plan(REFERENCE, variant="3A")
    # Strip every fissile nuclide from the fuel material.
    for m in plan.complex_model.materials:
        if m.id == "fuel":
            m.composition = [NuclideSpec(name="O16", percent=1.0)]
    issues = validate_source_settings(plan)
    codes = {i.code for i in issues}
    assert "runtime.fuel_material_not_fissionable" in codes


def test_fuel_material_unreferenced_triggers_issue() -> None:
    plan = build_vera3_like_plan(REFERENCE, variant="3A")
    # Disconnect the fuel material from every cell.
    for c in plan.complex_model.cells:
        if c.fill_id == "fuel":
            c.fill_id = "clad"
    issues = validate_source_settings(plan)
    codes = {i.code for i in issues}
    assert "runtime.active_fuel_geometry_missing" in codes


# -- 7. OpenMC source rejection stderr parsed ------------------------------


def test_openmc_source_rejection_stderr_parsed_as_primary() -> None:
    stderr = (
        "Too few source sites satisfied the constraints\n"
        "minimum source rejection fraction = 0.05\n"
        "double free or corruption\n"
        "Segmentation fault\n"
        "MPI abort"
    )
    report = parse_openmc_output("", stderr)
    assert not report.is_valid
    codes = [i.code for i in report.issues]
    assert "runtime.openmc_source_rejection_failure" in codes
    # The segfault / double-free must NOT override source rejection as primary.
    assert codes[0] == "runtime.openmc_source_rejection_failure"
    assert "too few source sites" in report.issues[0].message.lower()


def test_clean_openmc_output_has_no_source_rejection() -> None:
    report = parse_openmc_output("k-eff = 1.0\n", "")
    assert "runtime.openmc_source_rejection_failure" not in {i.code for i in report.issues}


# -- 8. source rejection fallback only after validation --------------------


def test_valid_vera3_source_has_no_blocking_source_issues() -> None:
    plan = build_vera3_like_plan(REFERENCE, variant="3A")
    bounds = source_bounds_for_plan(plan.complex_model)
    issues = validate_source_settings(plan, source_bounds=bounds)
    blocking = [i for i in issues if i.severity == "error"]
    assert blocking == [], [str(i) for i in blocking]
    # The fallback lowering would only be applied when the above is clean AND a
    # real smoke test still rejects -- this test confirms the precondition.


# -- 9. VERA3-like smoke settings ------------------------------------------


def test_vera3_like_smoke_settings_are_sound() -> None:
    plan = build_vera3_like_plan(REFERENCE, variant="3A")
    bounds = source_bounds_for_plan(plan.complex_model)
    assert bounds.z_bound_to_active_fuel is True
    assert bounds.z_min == pytest.approx(11.951)
    assert bounds.z_max == pytest.approx(377.711)
    issues = validate_source_settings(plan, source_bounds=bounds)
    assert not any(i.severity == "error" for i in issues)
    # fissionable fuel present and referenced.
    assert "fuel" in fuel_material_ids(plan.complex_model)


# -- 10. guide tube wall validation ----------------------------------------


def test_guide_tube_wall_missing_flagged() -> None:
    plan = build_vera3_like_plan(REFERENCE, variant="3A", pure_water_guide=True)
    from helpers.vera3_acceptance import validate_vera3_plan_structure
    issues = validate_vera3_plan_structure(plan, REFERENCE, variant="3A")
    codes = {i.code for i in issues}
    assert "vera3.guide_tube_wall_missing" in codes


def test_guide_tube_with_wall_passes() -> None:
    plan = build_vera3_like_plan(REFERENCE, variant="3A")
    from helpers.vera3_acceptance import validate_vera3_plan_structure
    issues = validate_vera3_plan_structure(plan, REFERENCE, variant="3A")
    codes = {i.code for i in issues}
    assert "vera3.guide_tube_wall_missing" not in codes


# -- 11. alloy pure-element validation -------------------------------------


def _model_with(materials):
    cells = [CellSpec(id="c1", name="c", fill_type="material", fill_id=materials[0].id)]
    universes = [UniverseSpec(id="u", name="u", cell_ids=["c1"])]
    lattice = LatticeSpec(id="lat", name="lat", kind="rect", pitch_cm=(1.26, 1.26),
                          universe_pattern=[["u"]])
    return ComplexModelSpec(name="m", kind="assembly", materials=materials,
                            cells=cells, universes=universes, lattices=[lattice])


def test_zircaloy_as_pure_zr_flagged() -> None:
    zr_pure = ComplexMaterialSpec(id="clad", name="Zircaloy-4", density_unit="g/cm3",
                                  density_value=6.56, composition=[NuclideSpec(name="Zr90", percent=1.0)])
    issues = alloy_pure_element_issues(_model_with([zr_pure]))
    assert any(i.code == "materials.alloy_reduced_to_pure_element" for i in issues)


def test_inconel_as_pure_ni_flagged() -> None:
    ni_pure = ComplexMaterialSpec(id="grid", name="Inconel-718 grid", density_unit="g/cm3",
                                  density_value=8.19, composition=[NuclideSpec(name="Ni58", percent=1.0)])
    issues = alloy_pure_element_issues(_model_with([ni_pure]))
    assert any(i.code == "materials.alloy_reduced_to_pure_element" for i in issues)


def test_alloy_with_minor_constituents_passes() -> None:
    zr4_full = ComplexMaterialSpec(id="clad", name="Zircaloy-4", density_unit="g/cm3",
                                   density_value=6.56, composition=[
                                       NuclideSpec(name="Zr90", percent=98.0),
                                       NuclideSpec(name="Sn120", percent=1.45),
                                   ])
    issues = alloy_pure_element_issues(_model_with([zr4_full]))
    assert not any(i.code == "materials.alloy_reduced_to_pure_element" for i in issues)
