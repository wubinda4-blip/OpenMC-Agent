"""Tests for the deterministic id-reference repair (auto_repair)."""

from __future__ import annotations

import pytest

from openmc_agent.auto_repair import _resolve_id, auto_repair_lattice_structure
from openmc_agent.graph import _apply_json_patches
from openmc_agent.schemas import (
    AxialLayerSpec,
    CellSpec,
    ComplexMaterialSpec,
    ComplexModelSpec,
    CoreSpec,
    LatticeLoadingSpec,
    LatticeSpec,
    PlotSpec,
    RegionSpec,
    RenderCapabilityReport,
    SimulationPlan,
    SurfaceSpec,
    UniverseSpec,
    ValidationIssue,
)


def _mat(mid: str) -> ComplexMaterialSpec:
    return ComplexMaterialSpec(
        id=mid,
        name=mid,
        chemical_formula="UO2",
        density_value=10.0,
        density_unit="g/cm3",
    )


def _plan(model: ComplexModelSpec) -> SimulationPlan:
    return SimulationPlan(
        complex_model=model,
        capability_report=RenderCapabilityReport(
            renderability="none", is_executable=False, supported_renderer="none"
        ),
        plot_specs=[PlotSpec(basis="xy", width_cm=(10.0, 10.0), filename="x.png")],
    )


# ---------------------------------------------------------------------------
# _resolve_id unit tests
# ---------------------------------------------------------------------------


def test_resolve_exact_match():
    assert _resolve_id("fuel", {"fuel"}) == "fuel"


def test_resolve_unique_prefix():
    assert _resolve_id("fuel_univ", {"fuel_universe"}) == "fuel_universe"


def test_resolve_unique_edit_distance():
    # "fuel_unierse" is one edit from "fuel_universe"; no boundary overlap.
    assert _resolve_id("fuel_unierse", {"fuel_universe"}) == "fuel_universe"


def test_resolve_multi_solution_returns_none():
    # "fuel" prefix-matches two ids -> ambiguous.
    assert _resolve_id("fuel", {"fuel_cell", "fuel_material"}) is None


def test_resolve_no_close_candidate_returns_none():
    assert _resolve_id("xyz", {"fuel_universe"}) is None


def test_resolve_empty_pool_returns_none():
    assert _resolve_id("fuel", set()) is None


# ---------------------------------------------------------------------------
# auto_repair_lattice_structure integration
# ---------------------------------------------------------------------------


def test_repairs_unique_cell_fill_id_typo():
    model = ComplexModelSpec(
        name="m", kind="assembly",
        materials=[_mat("fuel_universe")],
        cells=[CellSpec(id="c1", name="c", fill_type="material", fill_id="fuel_univ")],
        universes=[UniverseSpec(id="u1", name="u", cell_ids=["c1"])],
    )
    patch = auto_repair_lattice_structure(_plan(model))
    assert patch == [
        {"op": "replace", "path": "/complex_model/cells/0/fill_id", "value": "fuel_universe"}
    ]


def test_skips_when_typo_is_ambiguous():
    model = ComplexModelSpec(
        name="m", kind="assembly",
        materials=[_mat("fuel_cell"), _mat("fuel_material")],
        cells=[CellSpec(id="c1", name="c", fill_type="material", fill_id="fuel")],
        universes=[UniverseSpec(id="u1", name="u", cell_ids=["c1"])],
    )
    assert auto_repair_lattice_structure(_plan(model)) is None


def test_pool_isolation_material_ref_ignores_universe_ids():
    # fill_type='material' but id matches a universe, not a material -> no repair.
    model = ComplexModelSpec(
        name="m", kind="assembly",
        materials=[_mat("fuel_universe")],
        cells=[CellSpec(id="c1", name="c", fill_type="material", fill_id="u1")],
        universes=[UniverseSpec(id="u1", name="u", cell_ids=["c1"])],
    )
    assert auto_repair_lattice_structure(_plan(model)) is None


def test_repairs_universe_cell_id_typo():
    model = ComplexModelSpec(
        name="m", kind="assembly",
        materials=[_mat("fuel")],
        cells=[CellSpec(id="fuel_cell", name="c", fill_type="material", fill_id="fuel")],
        universes=[UniverseSpec(id="u1", name="u", cell_ids=["fuel_cel"])],  # typo
    )
    patch = auto_repair_lattice_structure(_plan(model))
    assert patch == [
        {"op": "replace", "path": "/complex_model/universes/0/cell_ids/0", "value": "fuel_cell"}
    ]


def test_repairs_region_surface_id_typo():
    model = ComplexModelSpec(
        name="m", kind="assembly",
        materials=[_mat("fuel")],
        surfaces=[SurfaceSpec(id="fuel_outer", kind="zcylinder", parameters={"r": 0.4})],
        regions=[RegionSpec(id="r1", expression="-fuel_outer", surface_ids=["fuel_outr"])],
        cells=[CellSpec(id="c1", name="c", fill_type="material", fill_id="fuel", region_id="r1")],
        universes=[UniverseSpec(id="u1", name="u", cell_ids=["c1"])],
    )
    patch = auto_repair_lattice_structure(_plan(model))
    assert patch == [
        {"op": "replace", "path": "/complex_model/regions/0/surface_ids/0", "value": "fuel_outer"}
    ]


def test_repairs_lattice_universe_pattern_typo():
    model = ComplexModelSpec(
        name="m", kind="assembly",
        materials=[_mat("fuel")],
        cells=[CellSpec(id="c1", name="c", fill_type="material", fill_id="fuel")],
        universes=[UniverseSpec(id="fuel_universe", name="u", cell_ids=["c1"])],
        lattices=[
            LatticeSpec(
                id="lat", name="lat", kind="rect",
                pitch_cm=(1.26, 1.26), shape=(1, 1),
                universe_pattern=[["fuel_univ"]],  # typo
            )
        ],
    )
    patch = auto_repair_lattice_structure(_plan(model))
    assert patch == [
        {"op": "replace", "path": "/complex_model/lattices/0/universe_pattern/0/0",
         "value": "fuel_universe"}
    ]


def test_batch_repairs_multiple_typos():
    model = ComplexModelSpec(
        name="m", kind="assembly",
        materials=[_mat("fuel_universe"), _mat("moderator_universe")],
        cells=[
            CellSpec(id="c1", name="c", fill_type="material", fill_id="fuel_univ"),
            CellSpec(id="c2", name="c", fill_type="material", fill_id="moderator_univ"),
        ],
        universes=[UniverseSpec(id="u1", name="u", cell_ids=["c1", "c2"])],
    )
    patch = auto_repair_lattice_structure(_plan(model))
    assert patch is not None
    paths = [op["path"] for op in patch]
    assert "/complex_model/cells/0/fill_id" in paths
    assert "/complex_model/cells/1/fill_id" in paths


def test_issues_fast_exit_when_no_id_ref_code():
    model = ComplexModelSpec(
        name="m", kind="assembly",
        materials=[_mat("fuel_universe")],
        cells=[CellSpec(id="c1", name="c", fill_type="material", fill_id="fuel_univ")],
        universes=[UniverseSpec(id="u1", name="u", cell_ids=["c1"])],
    )
    # Renderer reported only a pin-count problem, not an id ref -> skip.
    issues = [ValidationIssue(severity="error", code="lattice.pin_count_mismatch", message="x")]
    assert auto_repair_lattice_structure(_plan(model), issues=issues) is None


def test_applied_patch_validates_and_clears_defect():
    model = ComplexModelSpec(
        name="m", kind="assembly",
        materials=[_mat("fuel_universe")],
        cells=[CellSpec(id="c1", name="c", fill_type="material", fill_id="fuel_univ")],
        universes=[UniverseSpec(id="u1", name="u", cell_ids=["c1"])],
    )
    plan = _plan(model)
    patch = auto_repair_lattice_structure(plan)
    assert patch is not None
    patched_payload = _apply_json_patches(plan.model_dump(mode="json"), patch)
    repaired = SimulationPlan.model_validate(patched_payload)
    assert repaired.complex_model.cells[0].fill_id == "fuel_universe"


def test_repairs_axial_layer_fill_ref_typo():
    model = ComplexModelSpec(
        name="m",
        kind="core",
        materials=[_mat("water")],
        cells=[CellSpec(id="c1", name="c", fill_type="material", fill_id="water")],
        universes=[UniverseSpec(id="u1", name="u", cell_ids=["c1"])],
        lattices=[
            LatticeSpec(
                id="core_lattice",
                name="lat",
                kind="rect",
                pitch_cm=(1.0, 1.0),
                universe_pattern=[["u1"]],
            )
        ],
        core=CoreSpec(
            id="core",
            name="core",
            lattice_id="core_lattice",
            axial_layers=[
                AxialLayerSpec(
                    id="water",
                    name="water",
                    z_min_cm=0.0,
                    z_max_cm=1.0,
                    fill={"type": "material", "id": "watr"},
                )
            ],
        ),
    )
    patch = auto_repair_lattice_structure(_plan(model))
    assert patch == [
        {"op": "replace", "path": "/complex_model/core/axial_layers/0/fill/id", "value": "water"}
    ]


def test_repairs_core_lattice_id_typo_even_when_issue_gates_scan():
    model = ComplexModelSpec(
        name="m",
        kind="core",
        materials=[_mat("fuel")],
        cells=[CellSpec(id="c1", name="c", fill_type="material", fill_id="fuel")],
        universes=[UniverseSpec(id="u1", name="u", cell_ids=["c1"])],
        lattices=[
            LatticeSpec(
                id="core_lattice",
                name="lat",
                kind="rect",
                pitch_cm=(1.0, 1.0),
                universe_pattern=[["u1"]],
            )
        ],
        core=CoreSpec(id="core", name="core", lattice_id="core_lattic"),
    )
    issue = ValidationIssue(
        severity="error",
        code="core.lattice_ref_missing",
        message="core references missing lattice_id='core_lattic'",
    )

    patch = auto_repair_lattice_structure(_plan(model), issues=[issue])

    assert patch == [
        {"op": "replace", "path": "/complex_model/core/lattice_id", "value": "core_lattice"}
    ]


def test_does_not_repair_core_lattice_id_when_candidate_is_ambiguous():
    model = ComplexModelSpec(
        name="m",
        kind="core",
        materials=[_mat("fuel")],
        cells=[CellSpec(id="c1", name="c", fill_type="material", fill_id="fuel")],
        universes=[UniverseSpec(id="u1", name="u", cell_ids=["c1"])],
        lattices=[
            LatticeSpec(
                id="core_lattice",
                name="lat",
                kind="rect",
                pitch_cm=(1.0, 1.0),
                universe_pattern=[["u1"]],
            ),
            LatticeSpec(
                id="core_lattice_alt",
                name="lat alt",
                kind="rect",
                pitch_cm=(1.0, 1.0),
                universe_pattern=[["u1"]],
            ),
        ],
        core=CoreSpec(id="core", name="core", lattice_id="core_latt"),
    )

    assert auto_repair_lattice_structure(_plan(model)) is None


def test_repairs_axial_layer_loading_id_and_base_lattice_typos():
    model = ComplexModelSpec(
        name="m",
        kind="core",
        materials=[_mat("fuel")],
        cells=[CellSpec(id="c1", name="c", fill_type="material", fill_id="fuel")],
        universes=[UniverseSpec(id="u1", name="u", cell_ids=["c1"])],
        lattices=[
            LatticeSpec(
                id="core_lattice",
                name="lat",
                kind="rect",
                pitch_cm=(1.0, 1.0),
                universe_pattern=[["u1"]],
            )
        ],
        lattice_loadings=[
            LatticeLoadingSpec(id="rodded_loading", base_lattice_id="core_lattic")
        ],
        core=CoreSpec(
            id="core",
            name="core",
            lattice_id="core_lattice",
            axial_layers=[
                AxialLayerSpec(
                    id="fuel",
                    name="fuel",
                    z_min_cm=0.0,
                    z_max_cm=1.0,
                    fill={"type": "lattice", "id": "rodded_loading_lattice"},
                    loading_id="rodded_loadin",
                )
            ],
        ),
    )
    patch = auto_repair_lattice_structure(_plan(model))
    assert patch == [
        {
            "op": "replace",
            "path": "/complex_model/core/axial_layers/0/loading_id",
            "value": "rodded_loading",
        },
        {
            "op": "replace",
            "path": "/complex_model/lattice_loadings/0/base_lattice_id",
            "value": "core_lattice",
        },
    ]


def test_repairs_lattice_loading_override_universe_key_typo():
    model = ComplexModelSpec(
        name="m",
        kind="core",
        materials=[_mat("fuel")],
        cells=[CellSpec(id="c1", name="c", fill_type="material", fill_id="fuel")],
        universes=[UniverseSpec(id="control_assembly", name="u", cell_ids=["c1"])],
        lattices=[
            LatticeSpec(
                id="core_lattice",
                name="lat",
                kind="rect",
                pitch_cm=(1.0, 1.0),
                universe_pattern=[["control_assembly"]],
            )
        ],
        lattice_loadings=[
            LatticeLoadingSpec(
                id="rodded_loading",
                base_lattice_id="core_lattice",
                overrides={"control_assembl": [(0, 0)]},
            )
        ],
        core=CoreSpec(id="core", name="core", lattice_id="core_lattice"),
    )
    plan = _plan(model)
    patch = auto_repair_lattice_structure(plan)
    assert patch == [
        {"op": "remove", "path": "/complex_model/lattice_loadings/0/overrides/control_assembl"},
        {
            "op": "add",
            "path": "/complex_model/lattice_loadings/0/overrides/control_assembly",
            "value": [(0, 0)],
        },
    ]
    repaired = SimulationPlan.model_validate(_apply_json_patches(plan.model_dump(mode="json"), patch))
    assert repaired.complex_model.lattice_loadings[0].overrides == {
        "control_assembly": [(0, 0)]
    }


def test_returns_none_for_clean_plan():
    model = ComplexModelSpec(
        name="m", kind="assembly",
        materials=[_mat("fuel")],
        cells=[CellSpec(id="c1", name="c", fill_type="material", fill_id="fuel")],
        universes=[UniverseSpec(id="u1", name="u", cell_ids=["c1"])],
    )
    assert auto_repair_lattice_structure(_plan(model)) is None
