"""Tests for exact VERA4 Pyrex coordinates (P2-FULLCORE-2D-A)."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from vera4_base_fixture import (
    PYREX_COORDS_1B, GT_COORDS_1B, THIMBLE_CORNER_1B, THIMBLE_EDGE_1B,
    build_vera4_assembly_catalog, build_all_vera4_patches,
)
from openmc_agent.plan_builder.assembler import assemble_simulation_plan_from_patches


class TestPyrexCoordinates:
    def test_pyrex_count_is_20(self):
        assert len(PYREX_COORDS_1B) == 20

    def test_pyrex_all_within_guide_tubes(self):
        """Every Pyrex coordinate must be a guide tube position."""
        gt_set = set(GT_COORDS_1B)
        for coord in PYREX_COORDS_1B:
            assert coord in gt_set, f"Pyrex coord {coord} not in guide tubes"

    def test_pyrex_excludes_instrument_tube(self):
        """Instrument tube at (9,9) must NOT be a Pyrex position."""
        assert (9, 9) not in PYREX_COORDS_1B

    def test_pyrex_no_duplicates(self):
        assert len(PYREX_COORDS_1B) == len(set(PYREX_COORDS_1B))

    def test_pyrex_exact_positions(self):
        """Verify the exact Pyrex positions from the VERA4 spec."""
        expected = {
            (3, 6), (3, 12),
            (4, 4), (4, 14),
            (6, 3), (6, 6), (6, 9), (6, 12), (6, 15),
            (9, 6), (9, 12),
            (12, 3), (12, 6), (12, 9), (12, 12), (12, 15),
            (14, 4), (14, 14),
            (15, 6), (15, 12),
        }
        assert set(PYREX_COORDS_1B) == expected

    def test_edge_assembly_has_pyrex_intent(self):
        catalog = build_vera4_assembly_catalog()
        edge = next(at for at in catalog.assembly_types if at.assembly_type_id == "edge")
        pyrex_intents = [i for i in edge.pin_map.localized_insert_intents if i.insert_kind == "pyrex_rod"]
        assert len(pyrex_intents) >= 1
        all_pyrex_coords = []
        for pi in pyrex_intents:
            all_pyrex_coords.extend(pi.coordinates)
        assert len(all_pyrex_coords) >= 20

    def test_corner_assembly_has_no_pyrex(self):
        catalog = build_vera4_assembly_catalog()
        corner = next(at for at in catalog.assembly_types if at.assembly_type_id == "corner")
        pyrex_intents = [i for i in corner.pin_map.localized_insert_intents if i.insert_kind == "pyrex_rod"]
        assert len(pyrex_intents) == 0

    def test_core_total_pyrex_is_80(self):
        """4 edge assemblies × 20 Pyrex = 80 total."""
        catalog = build_vera4_assembly_catalog()
        edge = next(at for at in catalog.assembly_types if at.assembly_type_id == "edge")
        pyrex_intents = [i for i in edge.pin_map.localized_insert_intents if i.insert_kind == "pyrex_rod"]
        coords_per_edge = len(pyrex_intents[0].coordinates) if pyrex_intents else 0
        assert coords_per_edge * 4 == 80


class TestThimbleCoordinates:
    def test_corner_thimble_count_is_24(self):
        assert len(THIMBLE_CORNER_1B) == 24

    def test_edge_thimble_count_is_4(self):
        assert len(THIMBLE_EDGE_1B) == 4

    def test_edge_thimble_exact_positions(self):
        expected = {(3, 9), (9, 3), (9, 15), (15, 9)}
        assert set(THIMBLE_EDGE_1B) == expected

    def test_edge_thimble_within_guide_tubes(self):
        gt_set = set(GT_COORDS_1B)
        for coord in THIMBLE_EDGE_1B:
            assert coord in gt_set

    def test_corner_thimble_within_guide_tubes(self):
        gt_set = set(GT_COORDS_1B)
        for coord in THIMBLE_CORNER_1B:
            assert coord in gt_set

    def test_core_total_thimble_is_112(self):
        """4 corners × 24 + 4 edges × 4 = 96 + 16 = 112."""
        assert 4 * len(THIMBLE_CORNER_1B) + 4 * len(THIMBLE_EDGE_1B) == 112

    def test_thimble_excludes_instrument_tube(self):
        assert (9, 9) not in THIMBLE_CORNER_1B
        assert (9, 9) not in THIMBLE_EDGE_1B
