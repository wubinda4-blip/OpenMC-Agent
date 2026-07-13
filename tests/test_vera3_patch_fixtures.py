"""Tests for VERA3 3A/3B patch fixtures end-to-end (Phase 3).

Proves that VERA3 3B — which fails under monolithic LLM generation (25K JSON
parse errors) — can be assembled from small, independently-validatable patches
into a structurally complete SimulationPlan that passes the assembly3d guard.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from openmc_agent.plan_builder.assembler import assemble_simulation_plan_from_patches
from openmc_agent.plan_builder.patches import parse_patch_content
from openmc_agent.plan_builder.validators import (
    PatchValidationContext,
    validate_patch,
)
from openmc_agent.assembly3d_guard import validate_assembly3d_plan
from openmc_agent.schemas import SimulationPlan

_FIXTURE_DIR = Path(__file__).parent / "fixtures" / "vera3_patches"


# ---------------------------------------------------------------------------
# Fixture loader
# ---------------------------------------------------------------------------


def _load_fixture(variant: str) -> list:
    """Load and parse all patches from a VERA3 fixture file."""
    path = _FIXTURE_DIR / f"vera3_{variant}_patches.json"
    raw = json.loads(path.read_text(encoding="utf-8"))
    patches = []
    for entry in raw["patches"]:
        ptype = entry["patch_type"]
        parsed = parse_patch_content(ptype, entry)
        patches.append(parsed)
    return patches


@pytest.fixture
def vera3_3a_patches() -> list:
    return _load_fixture("3a")


@pytest.fixture
def vera3_3b_patches() -> list:
    return _load_fixture("3b")


# ---------------------------------------------------------------------------
# 10. VERA3 3A fixture assembles
# ---------------------------------------------------------------------------


class TestVERA3AAssembly:
    def test_assembles_ok(self, vera3_3a_patches: list) -> None:
        result = assemble_simulation_plan_from_patches(vera3_3a_patches)
        assert result.ok is True, [
            (i.code, i.message[:80]) for i in result.issues if i.severity == "error"
        ]
        assert result.plan is not None

    def test_lattice_17x17(self, vera3_3a_patches: list) -> None:
        result = assemble_simulation_plan_from_patches(vera3_3a_patches)
        lattice = result.plan.complex_model.lattices[0]
        assert len(lattice.universe_pattern) == 17
        assert all(len(row) == 17 for row in lattice.universe_pattern)

    def test_special_pin_counts(self, vera3_3a_patches: list) -> None:
        result = assemble_simulation_plan_from_patches(vera3_3a_patches)
        lattice = result.plan.complex_model.lattices[0]
        flat = [uid for row in lattice.universe_pattern for uid in row]
        gt_count = sum(1 for u in flat if u == "guide_tube")
        it_count = sum(1 for u in flat if u == "instrument_tube")
        fuel_count = sum(1 for u in flat if u == "fuel_pin")
        assert gt_count == 24
        assert it_count == 1
        assert fuel_count == 264
        assert gt_count + it_count + fuel_count == 289

    def test_axial_layers_present(self, vera3_3a_patches: list) -> None:
        result = assemble_simulation_plan_from_patches(vera3_3a_patches)
        layers = result.plan.complex_model.core.axial_layers
        assert len(layers) == 12
        fuel = next(l for l in layers if l.id == "active_fuel")
        assert fuel.z_min_cm == pytest.approx(11.951)
        assert fuel.z_max_cm == pytest.approx(377.711)

    def test_overlays_present(self, vera3_3a_patches: list) -> None:
        result = assemble_simulation_plan_from_patches(vera3_3a_patches)
        overlays = result.plan.complex_model.core.axial_overlays
        assert len(overlays) == 8
        assert all(o.geometry_mode == "mass_conserving_outer_frame" for o in overlays)
        assert all(o.through_path_preserved is True for o in overlays)

    def test_no_pyrex_positions(self, vera3_3a_patches: list) -> None:
        result = assemble_simulation_plan_from_patches(vera3_3a_patches)
        lattice = result.plan.complex_model.lattices[0]
        flat = [uid for row in lattice.universe_pattern for uid in row]
        assert "pyrex_rod" not in flat
        assert "thimble_plug" not in flat

    def test_passes_assembly3d_guard(self, vera3_3a_patches: list) -> None:
        result = assemble_simulation_plan_from_patches(vera3_3a_patches)
        plan = result.plan
        # The guard needs the requirement text for feature detection
        requirement = (
            "VERA3 3A benchmark: 3D assembly with axial layers, spacer grids, "
            "17x17 lattice, guide tubes, instrument tube"
        )
        issues = validate_assembly3d_plan(plan, requirement=requirement)
        error_codes = [i.code for i in issues if i.severity == "error"]
        assert "assembly3d.axial_layers_required" not in error_codes
        assert "assembly3d.default_z_extent_for_axial_problem" not in error_codes
        assert "assembly3d.spacer_grid_material_slab" not in error_codes


# ---------------------------------------------------------------------------
# 11. VERA3 3B fixture assembles
# ---------------------------------------------------------------------------


class TestVERA3BAssembly:
    def test_assembles_ok(self, vera3_3b_patches: list) -> None:
        result = assemble_simulation_plan_from_patches(vera3_3b_patches)
        assert result.ok is True, [
            (i.code, i.message[:80]) for i in result.issues if i.severity == "error"
        ]
        assert result.plan is not None

    def test_lattice_17x17(self, vera3_3b_patches: list) -> None:
        result = assemble_simulation_plan_from_patches(vera3_3b_patches)
        lattice = result.plan.complex_model.lattices[0]
        assert len(lattice.universe_pattern) == 17
        assert all(len(row) == 17 for row in lattice.universe_pattern)

    def test_pyrex_count(self, vera3_3b_patches: list) -> None:
        result = assemble_simulation_plan_from_patches(vera3_3b_patches)
        loading = next(l for l in result.plan.complex_model.lattice_loadings if l.id == "pyrex_active_loading")
        # Pyrex is now a nested_component_override
        nested_ops = [t for t in loading.transformations if t.operation_kind == "nested_component_override"]
        assert len(nested_ops) == 1
        assert len(nested_ops[0].target_coordinates) == 16
        assert nested_ops[0].replacement_universe_id == "pyrex_inner_profile"

    def test_thimble_plug_count(self, vera3_3b_patches: list) -> None:
        result = assemble_simulation_plan_from_patches(vera3_3b_patches)
        lattice = result.plan.complex_model.lattices[0]
        flat = [uid for row in lattice.universe_pattern for uid in row]
        plug_count = sum(1 for u in flat if u == "thimble_plug")
        assert plug_count == 0

    def test_guide_tube_count_preserved_in_base_lattice(self, vera3_3b_patches: list) -> None:
        """The base lattice keeps guide tubes water-filled; inserts are axial loadings."""
        result = assemble_simulation_plan_from_patches(vera3_3b_patches)
        lattice = result.plan.complex_model.lattices[0]
        flat = [uid for row in lattice.universe_pattern for uid in row]
        gt_count = sum(1 for u in flat if u == "guide_tube")
        assert gt_count == 24
        assert lattice.universe_pattern[2][8] == "guide_tube"  # 1-based (3,9)
        assert lattice.universe_pattern[5][5] == "guide_tube"  # 1-based (6,6)
        assert lattice.universe_pattern[8][2] == "guide_tube"  # 1-based (9,3)

    def test_instrument_tube_count(self, vera3_3b_patches: list) -> None:
        result = assemble_simulation_plan_from_patches(vera3_3b_patches)
        lattice = result.plan.complex_model.lattices[0]
        flat = [uid for row in lattice.universe_pattern for uid in row]
        it_count = sum(1 for u in flat if u == "instrument_tube")
        assert it_count == 1

    def test_fuel_count_264(self, vera3_3b_patches: list) -> None:
        result = assemble_simulation_plan_from_patches(vera3_3b_patches)
        lattice = result.plan.complex_model.lattices[0]
        flat = [uid for row in lattice.universe_pattern for uid in row]
        fuel_count = sum(1 for u in flat if u == "fuel_pin")
        assert fuel_count == 264

    def test_total_289(self, vera3_3b_patches: list) -> None:
        result = assemble_simulation_plan_from_patches(vera3_3b_patches)
        lattice = result.plan.complex_model.lattices[0]
        total = sum(len(row) for row in lattice.universe_pattern)
        assert total == 289

    def test_axial_layers_16(self, vera3_3b_patches: list) -> None:
        result = assemble_simulation_plan_from_patches(vera3_3b_patches)
        layers = result.plan.complex_model.core.axial_layers
        assert len(layers) == 16  # upper plenum split into 3 segments
        loaded = next(l for l in layers if l.id == "active_fuel_pyrex_span")
        assert loaded.loading_id == "pyrex_active_loading"

    def test_overlays_8(self, vera3_3b_patches: list) -> None:
        result = assemble_simulation_plan_from_patches(vera3_3b_patches)
        overlays = result.plan.complex_model.core.axial_overlays
        assert len(overlays) == 8

    def test_mid_grid_material_alias_canonicalized(self, vera3_3b_patches: list) -> None:
        result = assemble_simulation_plan_from_patches(vera3_3b_patches)
        assert result.ok is True
        overlays = result.plan.complex_model.core.axial_overlays
        mid_grid_materials = {
            ov.material_id for ov in overlays if "_mid" in ov.id
        }
        assert mid_grid_materials == {"zircaloy4"}
        assert result.summary["material_aliases_applied"] == {
            "grid_zircaloy4": "zircaloy4"
        }

    def test_actual_pin_counts_summary(self, vera3_3b_patches: list) -> None:
        result = assemble_simulation_plan_from_patches(vera3_3b_patches)
        assert result.summary["actual_pin_counts"] == {
            "fuel_pin": 264,
            "guide_tube": 24,
            "instrument_tube": 1,
            "pyrex_rod": 0,
            "thimble_plug": 0,
        }
        # end_plug + plenum + pyrex + pyrex_upper_gas + thimble + shoulder water = 6 loadings
        assert result.summary["lattice_loading_count"] == 6

    def test_bad_facts_guide_count_does_not_pollute_lattice_expected_counts(
        self,
        vera3_3b_patches: list,
    ) -> None:
        facts = next(p for p in vera3_3b_patches if p.patch_type == "facts")
        facts.expected_guide_tube_count = 0

        result = assemble_simulation_plan_from_patches(vera3_3b_patches)
        assert result.ok is True
        lattice = result.plan.complex_model.lattices[0]
        assert lattice.expected_counts == {
            "fuel_pin": 264,
            "guide_tube": 24,
            "instrument_tube": 1,
            "pyrex_rod": 0,
            "thimble_plug": 0,
        }
        assert "assembly.expected_counts_reconciled" in [
            issue.code for issue in result.issues
        ]

    def test_passes_assembly3d_guard(self, vera3_3b_patches: list) -> None:
        result = assemble_simulation_plan_from_patches(vera3_3b_patches)
        plan = result.plan
        requirement = (
            "VERA3 3B benchmark: 3D assembly with axial layers, spacer grids, "
            "三维, 轴向反射, 定位格架, 格架, 气腔, z = 1, "
            "Pyrex rods, thimble plugs, 17x17 lattice"
        )
        issues = validate_assembly3d_plan(plan, requirement=requirement)
        error_codes = [i.code for i in issues if i.severity == "error"]
        assert "assembly3d.axial_layers_required" not in error_codes
        assert "assembly3d.default_z_extent_for_axial_problem" not in error_codes
        assert "assembly3d.spacer_grid_material_slab" not in error_codes

    def test_3b_assembles_without_25k_json(self, vera3_3b_patches: list) -> None:
        """The key proof: 3B assembles from small patches, no monolithic JSON needed."""
        total_patch_bytes = sum(
            len(json.dumps(p.model_dump(mode="json"), ensure_ascii=False))
            for p in vera3_3b_patches
        )
        result = assemble_simulation_plan_from_patches(vera3_3b_patches)
        assert result.ok is True
        # Total patch size should be much smaller than 25K
        assert total_patch_bytes < 30000, f"patches total {total_patch_bytes} bytes"


# ---------------------------------------------------------------------------
# 12-13. Patch validation of fixtures
# ---------------------------------------------------------------------------


class TestVERA3PatchValidation:
    @pytest.mark.parametrize("variant", ["3a", "3b"])
    def test_all_patches_validate(self, variant: str) -> None:
        patches = _load_fixture(variant)
        context = PatchValidationContext(
            benchmark_id="VERA3",
            selected_variant=variant.upper(),
            strict_benchmark=True,
        )
        for patch in patches:
            result = validate_patch(patch, context)
            error_codes = [i.code for i in result.issues if i.severity == "error"]
            assert not error_codes, (
                f"{patch.patch_type} has errors: {error_codes}"
            )
