"""P0-V2: VERA3B Pyrex upper-gas axial profile tests.

Covers:
- pyrex_upper_gas_inner_profile universe structure
- upper profile excludes Pyrex material
- SS304 inner tube and outer clad preserved
- water gap background preserved
- no guide wall in nested profile
- radial continuity (no gap/overlap)
- pyrex_upper_gas_loading exists with 16 coordinates
- coordinates match poison loading
- Pyrex/thimble coordinate mutual exclusion
- coverage from 376.441 to 397.510 continuous
- each axial layer has correct loading combination
- top grid overlay remains independent
- pin counts preserved
- 3A has no Pyrex structure
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from openmc_agent.plan_builder.patches import parse_patch_content
from openmc_agent.plan_builder.assembler import assemble_simulation_plan_from_patches
from openmc_agent.radial_profile_validation import validate_concentric_radial_profile

FIXTURE_3B = Path("tests/fixtures/vera3_patches/vera3_3b_patches.json")
FIXTURE_3A = Path("tests/fixtures/vera3_patches/vera3_3a_patches.json")


@pytest.fixture
def assembled_3b():
    with open(FIXTURE_3B) as f:
        data = json.load(f)
    patches = [parse_patch_content(p["patch_type"], p) for p in data["patches"]]
    return assemble_simulation_plan_from_patches(patches)


@pytest.fixture
def assembled_3a():
    with open(FIXTURE_3A) as f:
        data = json.load(f)
    patches = [parse_patch_content(p["patch_type"], p) for p in data["patches"]]
    return assemble_simulation_plan_from_patches(patches)


@pytest.fixture
def raw_3b():
    return json.loads(FIXTURE_3B.read_text())


@pytest.fixture
def upper_gas_universe(raw_3b):
    universes_patch = next(p for p in raw_3b["patches"] if p["patch_type"] == "universes")
    return next(u for u in universes_patch["universes"] if u["universe_id"] == "pyrex_upper_gas_inner_profile")


@pytest.fixture
def upper_gas_loading(raw_3b):
    axial = next(p for p in raw_3b["patches"] if p["patch_type"] == "axial_layers")
    return next(l for l in axial["lattice_loadings"] if l["loading_id"] == "pyrex_upper_gas_loading")


@pytest.fixture
def poison_loading(raw_3b):
    axial = next(p for p in raw_3b["patches"] if p["patch_type"] == "axial_layers")
    return next(l for l in axial["lattice_loadings"] if l["loading_id"] == "pyrex_active_loading")


@pytest.fixture
def thimble_loading(raw_3b):
    axial = next(p for p in raw_3b["patches"] if p["patch_type"] == "axial_layers")
    return next(l for l in axial["lattice_loadings"] if l["loading_id"] == "thimble_plug_loading")


# ---------------------------------------------------------------------------
# 1-9: Upper-gas profile structure
# ---------------------------------------------------------------------------


class TestUpperGasProfileStructure:
    def test_upper_gas_profile_exists(self, upper_gas_universe):
        assert upper_gas_universe is not None

    def test_upper_profile_no_pyrex_material(self, upper_gas_universe):
        mats = {c["material_id"] for c in upper_gas_universe["cells"]}
        assert "pyrex" not in mats

    def test_upper_profile_has_ss304_inner_tube(self, upper_gas_universe):
        roles = {(c.get("role"), c.get("material_id")) for c in upper_gas_universe["cells"]}
        assert ("inner_tube", "ss304") in roles

    def test_upper_profile_has_ss304_outer_clad(self, upper_gas_universe):
        roles = {(c.get("role"), c.get("material_id")) for c in upper_gas_universe["cells"]}
        assert ("outer_clad", "ss304") in roles

    def test_upper_profile_has_water_gap(self, upper_gas_universe):
        bg = [c for c in upper_gas_universe["cells"] if c.get("role") == "inner_flow_background"]
        assert len(bg) == 1
        assert bg[0]["material_id"] == "borated_water_3b"

    def test_upper_profile_no_guide_wall(self, upper_gas_universe):
        roles = {c.get("role") for c in upper_gas_universe["cells"]}
        assert "tube_wall" not in roles

    def test_upper_profile_no_outer_moderator(self, upper_gas_universe):
        roles = {c.get("role") for c in upper_gas_universe["cells"]}
        assert "outer_moderator" not in roles

    def test_upper_profile_helium_plenum(self, upper_gas_universe):
        gas = [c for c in upper_gas_universe["cells"] if c.get("role") == "gas_plenum"]
        assert len(gas) == 1
        assert gas[0]["material_id"] == "helium"
        assert abs(gas[0]["r_min_cm"] - 0.231) < 1e-6
        assert abs(gas[0]["r_max_cm"] - 0.437) < 1e-6

    def test_upper_profile_ss304_radii_preserved(self, upper_gas_universe):
        tube = next(c for c in upper_gas_universe["cells"] if c.get("role") == "inner_tube")
        assert abs(tube["r_min_cm"] - 0.214) < 1e-6
        assert abs(tube["r_max_cm"] - 0.231) < 1e-6
        clad = next(c for c in upper_gas_universe["cells"] if c.get("role") == "outer_clad")
        assert abs(clad["r_min_cm"] - 0.437) < 1e-6
        assert abs(clad["r_max_cm"] - 0.484) < 1e-6


# ---------------------------------------------------------------------------
# 10-18: Upper-gas loading and coordinates
# ---------------------------------------------------------------------------


class TestUpperGasLoading:
    def test_upper_gas_loading_exists(self, upper_gas_loading):
        assert upper_gas_loading is not None

    def test_operation_kind_nested(self, upper_gas_loading):
        op = upper_gas_loading["transformations"][0]
        assert op["operation_kind"] == "nested_component_override"

    def test_component_role_inner_flow(self, upper_gas_loading):
        op = upper_gas_loading["transformations"][0]
        assert op["component_role"] == "inner_flow"

    def test_preserve_tube_wall(self, upper_gas_loading):
        op = upper_gas_loading["transformations"][0]
        assert "tube_wall" in op["preserve_component_roles"]

    def test_preserve_outer_moderator(self, upper_gas_loading):
        op = upper_gas_loading["transformations"][0]
        assert "outer_moderator" in op["preserve_component_roles"]

    def test_16_coordinates(self, upper_gas_loading):
        op = upper_gas_loading["transformations"][0]
        assert len(op["target_coordinates"]) == 16

    def test_coords_match_poison(self, upper_gas_loading, poison_loading):
        ug = upper_gas_loading["transformations"][0]["target_coordinates"]
        pn = poison_loading["transformations"][0]["target_coordinates"]
        assert set(tuple(c) for c in ug) == set(tuple(c) for c in pn)

    def test_no_duplicate_coords(self, upper_gas_loading):
        coords = upper_gas_loading["transformations"][0]["target_coordinates"]
        assert len(coords) == len(set(tuple(c) for c in coords))

    def test_pyrex_thimble_no_overlap(self, upper_gas_loading, thimble_loading):
        ug = set(tuple(c) for c in upper_gas_loading["transformations"][0]["target_coordinates"])
        th = set(tuple(c) for c in thimble_loading["transformations"][0]["target_coordinates"])
        assert ug & th == set()


# ---------------------------------------------------------------------------
# 19-27: Axial layer loading combinations
# ---------------------------------------------------------------------------


class TestAxialLayerLoadings:
    @pytest.fixture
    def layers(self, raw_3b):
        axial = next(p for p in raw_3b["patches"] if p["patch_type"] == "axial_layers")
        return {l["layer_id"]: l for l in axial["layers"]}

    def test_active_fuel_tail_has_upper_gas(self, layers):
        l = layers["active_fuel_upper_water_guides"]
        lids = l.get("loading_ids") or [l["loading_id"]]
        assert "pyrex_upper_gas_loading" in lids

    def test_upper_end_plug_has_end_plug_and_upper_gas(self, layers):
        l = layers["upper_end_plug"]
        lids = l.get("loading_ids") or [l["loading_id"]]
        assert "end_plug_loading" in lids
        assert "pyrex_upper_gas_loading" in lids

    def test_upper_plenum_lower_has_plenum_and_upper_gas(self, layers):
        l = layers["upper_plenum_lower"]
        lids = l.get("loading_ids") or [l["loading_id"]]
        assert "plenum_loading" in lids
        assert "pyrex_upper_gas_loading" in lids

    def test_middle_plenum_has_all_three(self, layers):
        l = layers["upper_plenum_middle_thimble"]
        lids = l.get("loading_ids") or [l["loading_id"]]
        assert "plenum_loading" in lids
        assert "pyrex_upper_gas_loading" in lids
        assert "thimble_plug_loading" in lids

    def test_upper_plenum_upper_has_plenum_and_upper_gas(self, layers):
        l = layers["upper_plenum_upper"]
        lids = l.get("loading_ids") or [l["loading_id"]]
        assert "plenum_loading" in lids
        assert "pyrex_upper_gas_loading" in lids

    def test_upper_shoulder_has_shoulder_and_upper_gas(self, layers):
        l = layers["upper_shoulder_gap"]
        lids = l.get("loading_ids") or [l["loading_id"]]
        assert "shoulder_water_loading" in lids
        assert "pyrex_upper_gas_loading" in lids

    def test_upper_nozzle_no_upper_gas(self, layers):
        l = layers["upper_nozzle"]
        assert l["fill_type"] == "material"


# ---------------------------------------------------------------------------
# 28-30: Coverage continuity
# ---------------------------------------------------------------------------


class TestCoverageContinuity:
    def test_coverage_376_441_to_397_510(self, raw_3b):
        axial = next(p for p in raw_3b["patches"] if p["patch_type"] == "axial_layers")
        z = 376.441
        for layer in axial["layers"]:
            if layer["z_min_cm"] < 376.441 or layer["z_min_cm"] >= 397.510:
                continue
            if layer["fill_type"] != "lattice":
                continue
            lids = layer.get("loading_ids") or ([layer["loading_id"]] if layer.get("loading_id") else [])
            assert "pyrex_upper_gas_loading" in lids, f"Layer {layer['layer_id']} missing upper-gas"

    def test_coverage_no_gap(self, raw_3b):
        axial = next(p for p in raw_3b["patches"] if p["patch_type"] == "axial_layers")
        lattice_layers = sorted(
            (l for l in axial["layers"] if 376.441 <= l["z_min_cm"] < 397.510 and l["fill_type"] == "lattice"),
            key=lambda l: l["z_min_cm"],
        )
        assert abs(lattice_layers[0]["z_min_cm"] - 376.441) < 1e-3
        assert abs(lattice_layers[-1]["z_max_cm"] - 397.51) < 1e-2
        for prev, curr in zip(lattice_layers, lattice_layers[1:]):
            assert abs(curr["z_min_cm"] - prev["z_max_cm"]) < 1e-6

    def test_top_grid_overlay_independent(self, assembled_3b):
        overlays = assembled_3b.plan.complex_model.core.axial_overlays
        top = next(o for o in overlays if o.id == "grid_7_end_top")
        assert top.overlay_kind == "spacer_grid"
        assert top.through_path_preserved is True


# ---------------------------------------------------------------------------
# 31-34: Radial validation
# ---------------------------------------------------------------------------


class TestRadialValidation:
    def _cells(self, upper_gas_universe):
        from types import SimpleNamespace
        return [SimpleNamespace(**c) for c in upper_gas_universe["cells"]]

    def test_upper_profile_radial_continuity(self, upper_gas_universe):
        cells = self._cells(upper_gas_universe)
        issues = validate_concentric_radial_profile("pyrex_upper_gas_inner_profile", cells)
        assert not [i for i in issues if i.severity == "error"]

    def test_upper_profile_no_gap(self, upper_gas_universe):
        cells = self._cells(upper_gas_universe)
        issues = validate_concentric_radial_profile("pyrex_upper_gas_inner_profile", cells)
        assert not [i for i in issues if "gap" in i.code.lower()]

    def test_upper_profile_no_overlap(self, upper_gas_universe):
        cells = self._cells(upper_gas_universe)
        issues = validate_concentric_radial_profile("pyrex_upper_gas_inner_profile", cells)
        assert not [i for i in issues if "overlap" in i.code.lower()]


# ---------------------------------------------------------------------------
# 35: Materialization
# ---------------------------------------------------------------------------


class TestMaterialization:
    def test_multi_loading_materialization_ok(self, assembled_3b):
        from openmc_agent.lattice_transform import materialize_axial_lattice_transformations
        new_spec, issues, meta = materialize_axial_lattice_transformations(assembled_3b.plan.complex_model)
        errors = [i for i in issues if i.severity == "error"]
        assert not errors, f"Materialization errors: {[i.message for i in errors]}"

    def test_materialization_deterministic(self, assembled_3b):
        from openmc_agent.lattice_transform import materialize_axial_lattice_transformations
        _, _, meta1 = materialize_axial_lattice_transformations(assembled_3b.plan.complex_model)
        _, _, meta2 = materialize_axial_lattice_transformations(assembled_3b.plan.complex_model)
        assert meta1["derived_lattice_ids"] == meta2["derived_lattice_ids"]

    def test_unique_lattice_ids(self, assembled_3b):
        from openmc_agent.lattice_transform import materialize_axial_lattice_transformations
        new_spec, issues, meta = materialize_axial_lattice_transformations(assembled_3b.plan.complex_model)
        all_ids = [l.id for l in new_spec.lattices]
        assert len(all_ids) == len(set(all_ids)), f"Duplicate lattice IDs: {all_ids}"


# ---------------------------------------------------------------------------
# 38-42: Pin counts and structure preserved
# ---------------------------------------------------------------------------


class TestPinCounts:
    def test_pin_counts_264_24_1(self, assembled_3b):
        lattice = assembled_3b.plan.complex_model.lattices[0]
        flat = [uid for row in lattice.universe_pattern for uid in row]
        assert sum(1 for u in flat if u == "fuel_pin") == 264
        assert sum(1 for u in flat if u == "guide_tube") == 24
        assert sum(1 for u in flat if u == "instrument_tube") == 1

    def test_base_lattice_no_upper_gas_profile(self, assembled_3b):
        lattice = assembled_3b.plan.complex_model.lattices[0]
        flat = [uid for row in lattice.universe_pattern for uid in row]
        assert "pyrex_upper_gas_inner_profile" not in flat


# ---------------------------------------------------------------------------
# 45-47: 3A has no Pyrex structure
# ---------------------------------------------------------------------------


class Test3ANoPyrex:
    def test_3a_no_pyrex_loading(self, assembled_3a):
        loadings = assembled_3a.plan.complex_model.lattice_loadings
        pyrex_ids = [l.id for l in loadings if "pyrex" in l.id.lower()]
        assert pyrex_ids == []

    def test_3a_no_pyrex_universe(self, assembled_3a):
        universes = assembled_3a.plan.complex_model.universes
        pyrex_u = [u.id for u in universes if "pyrex" in u.id.lower()]
        assert pyrex_u == []


# ---------------------------------------------------------------------------
# 50-52: Regression checks
# ---------------------------------------------------------------------------


class TestRegression:
    def test_v1_radial_tests_still_pass(self, assembled_3b):
        """P0-V1 radial structure still intact."""
        raw = json.loads(FIXTURE_3B.read_text())
        univ_patch = next(p for p in raw["patches"] if p["patch_type"] == "universes")
        fuel_u = next(u for u in univ_patch["universes"] if u["universe_id"] == "fuel_pin")
        gap = next(c for c in fuel_u["cells"] if c.get("role") == "gap")
        assert abs(gap["r_min_cm"] - 0.4096) < 1e-6
        assert abs(gap["r_max_cm"] - 0.418) < 1e-6

    def test_poison_profile_still_correct(self, assembled_3b):
        """P0-D5 Pyrex poison profile still intact."""
        model = assembled_3b.plan.complex_model
        pyrex_u = next((u for u in model.universes if u.id == "pyrex_inner_profile"), None)
        assert pyrex_u is not None
        cells = [c for c in model.cells if c.id in pyrex_u.cell_ids]
        poison = next((c for c in cells if c.component_role == "poison"), None)
        assert poison is not None

    def test_thimble_profile_still_correct(self, assembled_3b):
        """P0-D5 thimble profile still intact."""
        model = assembled_3b.plan.complex_model
        thimble_u = next((u for u in model.universes if u.id == "thimble_inner_profile"), None)
        assert thimble_u is not None
