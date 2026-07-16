"""Tests for patch schema parsing (Phase 2)."""

from __future__ import annotations

import pytest

from openmc_agent.plan_builder.patches import (
    AxialLayersPatch,
    AxialOverlaysPatch,
    FactsPatch,
    MaterialsPatch,
    PatchParseError,
    PinMapPatch,
    SettingsPatch,
    UniversesPatch,
    parse_patch_content,
    parse_patch_envelope,
)


# ---------------------------------------------------------------------------
# 17. parse_patch_content routes correctly
# ---------------------------------------------------------------------------


class TestParsePatchContent:
    def test_facts(self) -> None:
        patch = parse_patch_content("facts", {"benchmark_id": "VERA3"})
        assert isinstance(patch, FactsPatch)
        assert patch.benchmark_id == "VERA3"

    def test_materials(self) -> None:
        patch = parse_patch_content("materials", {
            "materials": [
                {"material_id": "fuel", "name": "UO2", "role": "fuel"}
            ]
        })
        assert isinstance(patch, MaterialsPatch)
        assert len(patch.materials) == 1

    def test_universes(self) -> None:
        patch = parse_patch_content("universes", {
            "universes": [
                {"universe_id": "fuel_pin", "kind": "fuel_pin",
                 "cells": [{"id": "fuel_cell", "role": "fuel"}]}
            ]
        })
        assert isinstance(patch, UniversesPatch)
        assert len(patch.universes) == 1

    def test_pin_map(self) -> None:
        patch = parse_patch_content("pin_map", {
            "lattice_size": [17, 17],
            "default_universe_id": "fuel_pin",
        })
        assert isinstance(patch, PinMapPatch)
        assert patch.lattice_size == (17, 17)

    def test_axial_layers(self) -> None:
        patch = parse_patch_content("axial_layers", {
            "layers": [
                {"layer_id": "active", "role": "active_fuel",
                 "z_min_cm": 0.0, "z_max_cm": 365.76}
            ]
        })
        assert isinstance(patch, AxialLayersPatch)
        assert len(patch.layers) == 1

    def test_axial_overlays(self) -> None:
        patch = parse_patch_content("axial_overlays", {
            "overlays": [
                {"overlay_id": "grid1", "overlay_kind": "spacer_grid",
                 "geometry_mode": "skeleton"}
            ]
        })
        assert isinstance(patch, AxialOverlaysPatch)
        assert len(patch.overlays) == 1

    def test_settings(self) -> None:
        patch = parse_patch_content("settings", {"plot_strategy": "full_assembly"})
        assert isinstance(patch, SettingsPatch)
        assert patch.plot_strategy == "full_assembly"

    def test_unknown_type_raises(self) -> None:
        with pytest.raises(PatchParseError) as exc_info:
            parse_patch_content("bogus", {})
        assert "bogus" in str(exc_info.value)

    def test_invalid_content_raises(self) -> None:
        with pytest.raises(PatchParseError) as exc_info:
            parse_patch_content("materials", {"materials": "not_a_list"})
        assert "materials" in str(exc_info.value)


class TestParsePatchEnvelope:
    def test_envelope_dict(self) -> None:
        patch = parse_patch_envelope({
            "patch_type": "facts",
            "content": {"benchmark_id": "VERA3"},
        })
        assert isinstance(patch, FactsPatch)

    def test_envelope_unknown_type_raises(self) -> None:
        with pytest.raises(PatchParseError):
            parse_patch_envelope({
                "patch_type": "unknown",
                "content": {},
            })


class TestCoordinateConvention:
    def test_defaults(self) -> None:
        from openmc_agent.plan_builder.patches import CoordinateConvention

        conv = CoordinateConvention()
        assert conv.index_base == 0
        assert conv.row_origin == "top"
        assert conv.ordering == "row_col"

    def test_normalized_coords(self) -> None:
        from openmc_agent.plan_builder.patches import (
            CoordinateConvention,
            normalized_coords,
        )

        conv = CoordinateConvention(index_base=1)
        result = normalized_coords([(1, 1), (2, 3)], conv, (17, 17))
        assert result == [(0, 0), (1, 2)]


# ---------------------------------------------------------------------------
# Null-collection coercion: LLMs emit `null` for list/dict fields they leave
# empty (e.g. "loading_ids": null). Pydantic's default_factory only applies to
# ABSENT fields, so _PatchBase coerces explicit null -> empty collection.
# ---------------------------------------------------------------------------


class TestNullCollectionCoercion:
    def test_axial_layer_loading_ids_null_coerced_to_empty(self) -> None:
        from openmc_agent.plan_builder.patches import AxialLayerPatchItem

        layer = AxialLayerPatchItem(
            layer_id="active_fuel",
            role="active_fuel",
            loading_ids=None,
            assumptions=None,
        )
        assert layer.loading_ids == []
        assert layer.assumptions == []

    def test_full_patch_null_list_fields_coerced(self) -> None:
        patch = parse_patch_content(
            "axial_layers",
            {
                "patch_type": "axial_layers",
                "layers": [
                    {
                        "layer_id": "active_fuel",
                        "role": "active_fuel",
                        "z_min_cm": 0.0,
                        "z_max_cm": 100.0,
                        "fill_type": "lattice",
                        "fill_id": "asm",
                        "loading_ids": None,
                        "assumptions": None,
                    }
                ],
            },
        )
        assert isinstance(patch, AxialLayersPatch)
        assert patch.layers[0].loading_ids == []

    def test_absent_list_fields_still_use_default(self) -> None:
        # Absent (not null) must keep working via default_factory.
        from openmc_agent.plan_builder.patches import AxialLayerPatchItem

        layer = AxialLayerPatchItem(layer_id="x", role="custom")
        assert layer.loading_ids == []
