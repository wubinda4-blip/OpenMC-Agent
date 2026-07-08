"""Tests for the deterministic id-reference repair (auto_repair)."""

from __future__ import annotations

import pytest

from openmc_agent.auto_repair import _resolve_id, auto_repair_lattice_structure
from openmc_agent.error_catalog import issue_from_catalog
from openmc_agent.graph import _apply_json_patches
from openmc_agent.schemas import (
    AssemblySpec,
    AxialLayerSpec,
    AxialOverlaySpec,
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


def test_repairs_axial_overlay_material_id_typo():
    """An overlay material_id that uniquely resolves to a defined material
    (e.g. 'grid_zircaloy4' -> 'zircaloy4') is auto-repaired instead of
    forcing the overlay to skeleton."""
    model = ComplexModelSpec(
        name="m", kind="assembly",
        materials=[_mat("zircaloy4"), _mat("inconel718")],
        lattices=[LatticeSpec(id="lat", name="lat", kind="rect", pitch_cm=(1.26, 1.26), universe_pattern=[["fuel_pin"]])],
        core=CoreSpec(
            id="core", name="core",
            lattice_id=None,
            axial_layers=[],
            axial_overlays=[
                AxialOverlaySpec(
                    id="grid_1", overlay_kind="spacer_grid",
                    z_min_cm=10.0, z_max_cm=12.0, target_lattice_id="lat",
                    material_id="grid_zircaloy4",
                    geometry_mode="homogenized_open_region",
                    through_path_preserved=True,
                ),
            ],
        ),
    )
    patch = auto_repair_lattice_structure(_plan(model))
    assert patch is not None
    assert {"op": "replace", "path": "/complex_model/core/axial_overlays/0/material_id", "value": "zircaloy4"} in patch


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


def test_adds_missing_core_assembly_wrapper_universes():
    model = ComplexModelSpec(
        name="m",
        kind="core",
        materials=[_mat("fuel")],
        cells=[CellSpec(id="pin_cell", name="pin", fill_type="material", fill_id="fuel")],
        universes=[UniverseSpec(id="pin_universe", name="pin", cell_ids=["pin_cell"])],
        lattices=[
            LatticeSpec(
                id="uo2_lattice",
                name="uo2 assembly",
                kind="rect",
                pitch_cm=(1.0, 1.0),
                universe_pattern=[["pin_universe"]],
            ),
            LatticeSpec(
                id="mox_lattice",
                name="mox assembly",
                kind="rect",
                pitch_cm=(1.0, 1.0),
                universe_pattern=[["pin_universe"]],
            ),
            LatticeSpec(
                id="core_lattice",
                name="core",
                kind="rect",
                pitch_cm=(10.0, 10.0),
                universe_pattern=[["mox_assembly", "uo2_assembly"]],
            ),
        ],
        assemblies=[
            AssemblySpec(id="mox_assembly", name="mox", lattice_id="mox_lattice"),
            AssemblySpec(id="uo2_assembly", name="uo2", lattice_id="uo2_lattice"),
        ],
        core=CoreSpec(id="core", name="core", lattice_id="core_lattice"),
    )
    issues = [
        ValidationIssue(
            severity="error",
            code="lattice.universe_ref_missing",
            message="lattice 'core_lattice' references missing universes: ['mox_assembly', 'uo2_assembly']",
        )
    ]

    patch = auto_repair_lattice_structure(_plan(model), issues=issues)

    assert patch == [
        {
            "op": "add",
            "path": "/complex_model/universes/1",
            "value": {
                "id": "mox_assembly",
                "name": "mox_assembly",
                "cell_ids": [],
                "purpose": (
                    "Auto-added empty assembly wrapper universe for a core lattice "
                    "reference to AssemblySpec 'mox_assembly'."
                ),
            },
        },
        {
            "op": "add",
            "path": "/complex_model/universes/2",
            "value": {
                "id": "uo2_assembly",
                "name": "uo2_assembly",
                "cell_ids": [],
                "purpose": (
                    "Auto-added empty assembly wrapper universe for a core lattice "
                    "reference to AssemblySpec 'uo2_assembly'."
                ),
            },
        },
    ]
    patched_payload = _apply_json_patches(_plan(model).model_dump(mode="json"), patch)
    repaired = SimulationPlan.model_validate(patched_payload)
    assert [universe.id for universe in repaired.complex_model.universes] == [
        "pin_universe",
        "mox_assembly",
        "uo2_assembly",
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


# ---------------------------------------------------------------------------
# Canonical pin-map overwrite (pin-count mismatch repair)
# ---------------------------------------------------------------------------

_MOX3X3_REQ = (
    "### MOX 符号表\n\n"
    "| 符号 | 含义 | 建议 pin universe |\n|---|---|---|\n"
    "| A | 4.3% MOX | `mox43_pin` |\n"
    "| B | 7.0% MOX | `mox7_pin` |\n"
    "| C | 8.7% MOX | `mox87_pin` |\n\n"
    "### MOX canonical pin map\n\n```text\n"
    "R01: A A A\nR02: A B A\nR03: A A A\n```\n"
)


def _mox_pin_count_model() -> ComplexModelSpec:
    return ComplexModelSpec(
        name="mox check",
        kind="assembly",
        cells=[
            CellSpec(id="c43", name="c", fill_type="void"),
            CellSpec(id="c7", name="c", fill_type="void"),
            CellSpec(id="c87", name="c", fill_type="void"),
        ],
        universes=[
            UniverseSpec(id="mox43_pin", name="A", cell_ids=["c43"]),
            UniverseSpec(id="mox7_pin", name="B", cell_ids=["c7"]),
            UniverseSpec(id="mox87_pin", name="C", cell_ids=["c87"]),
        ],
        lattices=[
            LatticeSpec(
                id="mox_assembly",
                name="MOX assembly",
                kind="rect",
                pitch_cm=(1.26, 1.26),
                universe_pattern=[
                    ["mox43_pin", "mox43_pin", "mox43_pin"],
                    ["mox43_pin", "mox87_pin", "mox43_pin"],  # canonical says mox7_pin
                    ["mox43_pin", "mox43_pin", "mox43_pin"],
                ],
                expected_counts={"mox43_pin": 8, "mox7_pin": 1},
            )
        ],
    )


def _pin_count_mismatch_issue() -> ValidationIssue:
    return issue_from_catalog(
        "lattice.pin_count_mismatch",
        message="lattice 'mox_assembly' pin counts mismatch",
        schema_path="complex_model.lattices.mox_assembly.universe_pattern",
        route_hint="reflect_plan",
    )


def test_auto_repair_overwrites_pattern_from_canonical_pin_map() -> None:
    """Pin-count mismatch + canonical map => deterministic pattern overwrite.

    The LLM cannot reliably hand-edit a dense universe_pattern even with
    cell-level coordinates (C5G7 case3: three reflections returned a byte-
    identical wrong 17x17). auto_repair emits a single replace op restoring
    the canonical ground-truth grid.
    """
    plan = _plan(_mox_pin_count_model())
    patch = auto_repair_lattice_structure(
        plan, issues=[_pin_count_mismatch_issue()], requirement=_MOX3X3_REQ
    )
    assert patch is not None
    replace_ops = [
        op
        for op in patch
        if op.get("op") == "replace"
        and op.get("path") == "/complex_model/lattices/0/universe_pattern"
    ]
    assert len(replace_ops) == 1
    assert replace_ops[0]["value"][1][1] == "mox7_pin"
    patched = _apply_json_patches(plan.model_dump(mode="json"), patch)
    grid = patched["complex_model"]["lattices"][0]["universe_pattern"]
    assert grid[1][1] == "mox7_pin"


def test_auto_repair_skips_canonical_overwrite_without_requirement() -> None:
    """No requirement text => no canonical map => no overwrite (and no id-ref ops
    because the pattern universe ids are all valid)."""
    plan = _plan(_mox_pin_count_model())
    assert (
        auto_repair_lattice_structure(plan, issues=[_pin_count_mismatch_issue()])
        is None
    )


def test_auto_repair_skips_canonical_overwrite_without_mismatch_issue() -> None:
    """A requirement alone must not overwrite a lattice the caller did not flag."""
    plan = _plan(_mox_pin_count_model())
    patch = auto_repair_lattice_structure(plan, issues=None, requirement=_MOX3X3_REQ)
    if patch:
        assert not any(
            op.get("op") == "replace"
            and "universe_pattern" in op.get("path", "")
            for op in patch
        )


def test_auto_repair_canonical_overwrite_clears_stale_pin_count_confirmation() -> None:
    """Overwriting universe_pattern must also drop a stale pin-count confirmation.

    The C5G7 regression: the LLM wrote 'pin count mismatch' into
    lattice.requires_human_confirmation. Even after the deterministic overwrite
    fixed the counts, ask_expert kept re-surfacing that confirmation, and the
    resulting expert feedback triggered regenerate_plan -- discarding the fix.
    Clearing the stale confirmation breaks that loop.
    """
    model = _mox_pin_count_model()
    model.lattices[0].requires_human_confirmation = [
        "pin count mismatch vs expected_counts: mox7_pin: expected 100, got 90"
    ]
    plan = _plan(model)
    patch = auto_repair_lattice_structure(
        plan, issues=[_pin_count_mismatch_issue()], requirement=_MOX3X3_REQ
    )
    assert patch is not None
    conf_ops = [
        op
        for op in patch
        if op.get("op") == "replace"
        and op.get("path") == "/complex_model/lattices/0/requires_human_confirmation"
    ]
    assert len(conf_ops) == 1
    assert conf_ops[0]["value"] == []


def test_auto_repair_unifies_core_assembly_wrapper_naming() -> None:
    """core_lattice references wrapper universes whose names differ from assembly.id.

    The C5G7 regression: the LLM put 'uo2_assembly_univ' / 'mox_assembly_univ'
    in core_lattice.universe_pattern, but assembly.id is 'uo2_assy' / 'mox_assy'.
    auto_repair must (a) add the wrapper universes under the referenced names
    and (b) rename assembly.id to match, so the renderer's assembly.id lookup
    succeeds. Without this, the missing-universe error loops until retry exhaust
    and regenerate_plan re-introduces the pin-count defect.
    """
    model = ComplexModelSpec(
        name="core",
        kind="core",
        materials=[_mat("water")],
        cells=[CellSpec(id="c1", name="c", fill_type="material", fill_id="water")],
        universes=[
            UniverseSpec(id="water_reflector", name="water", cell_ids=["c1"]),
            UniverseSpec(id="uo2_pin", name="uo2", cell_ids=["c1"]),
            UniverseSpec(id="mox_pin", name="mox", cell_ids=["c1"]),
        ],
        lattices=[
            LatticeSpec(
                id="uo2_assembly",
                name="uo2",
                kind="rect",
                pitch_cm=(1.0, 1.0),
                universe_pattern=[["uo2_pin"]],
            ),
            LatticeSpec(
                id="mox_assembly",
                name="mox",
                kind="rect",
                pitch_cm=(1.0, 1.0),
                universe_pattern=[["mox_pin"]],
            ),
            LatticeSpec(
                id="core_lattice",
                name="core",
                kind="rect",
                pitch_cm=(1.0, 1.0),
                universe_pattern=[
                    ["uo2_assembly_univ", "mox_assembly_univ", "water_reflector"],
                    ["mox_assembly_univ", "uo2_assembly_univ", "water_reflector"],
                    ["water_reflector", "water_reflector", "water_reflector"],
                ],
            ),
        ],
        assemblies=[
            AssemblySpec(id="uo2_assy", name="uo2 assembly", lattice_id="uo2_assembly"),
            AssemblySpec(id="mox_assy", name="mox assembly", lattice_id="mox_assembly"),
        ],
        core=CoreSpec(id="core", name="core", lattice_id="core_lattice"),
    )
    issue = issue_from_catalog(
        "lattice.universe_ref_missing",
        message=(
            "lattice 'core_lattice' references missing universes: "
            "['uo2_assembly_univ', 'mox_assembly_univ']"
        ),
        schema_path="complex_model.lattices.core_lattice.universe_pattern",
        route_hint="auto_repair",
    )
    patch = auto_repair_lattice_structure(_plan(model), issues=[issue])

    # wrapper universes added under the referenced names
    added_ids = {
        o.get("value", {}).get("id")
        for o in (patch or [])
        if o.get("op") == "add" and "/universes/" in o.get("path", "")
    }
    assert "uo2_assembly_univ" in added_ids
    assert "mox_assembly_univ" in added_ids

    # assembly.id renamed to match the core reference
    renamed_values = {
        o["value"]
        for o in (patch or [])
        if o.get("op") == "replace"
        and "/assemblies/" in o.get("path", "")
        and o.get("path", "").endswith("/id")
    }
    assert "uo2_assembly_univ" in renamed_values
    assert "mox_assembly_univ" in renamed_values
