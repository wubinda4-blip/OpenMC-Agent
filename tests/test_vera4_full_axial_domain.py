"""Tests for VERA4 full axial domain coverage (P2-FULLCORE-2D-A)."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from vera4_base_fixture import (
    Z_DOMAIN_MIN, Z_DOMAIN_MAX,
    build_vera4_axial_layers, build_all_vera4_patches,
)
from openmc_agent.plan_builder.assembler import assemble_simulation_plan_from_patches
from openmc_agent.plan_builder.hierarchical_assembler import compile_global_axial_segments


class TestFullAxialDomain:
    def test_domain_spans_negative_to_positive(self):
        layers = build_vera4_axial_layers()
        assert layers.axial_domain_cm[0] == -55.000
        assert layers.axial_domain_cm[1] == 463.937

    def test_all_12_base_layers_present(self):
        layers = build_vera4_axial_layers()
        assert len(layers.layers) == 12

    def test_base_layer_roles(self):
        layers = build_vera4_axial_layers()
        roles = [l.role for l in layers.layers]
        expected = [
            "lower_moderator_buffer",
            "lower_core_plate",
            "lower_nozzle",
            "lower_shoulder_gap",
            "lower_fuel_endplug",
            "active_fuel",
            "upper_fuel_endplug",
            "fuel_upper_plenum",
            "upper_shoulder_gap",
            "upper_nozzle",
            "upper_core_plate",
            "upper_moderator_buffer",
        ]
        assert roles == expected

    def test_base_layer_fill_types(self):
        layers = build_vera4_axial_layers()
        # First 3 and last 3 should be material (whole-plane)
        for layer in layers.layers[:3]:
            assert layer.fill_type == "material"
        for layer in layers.layers[-3:]:
            assert layer.fill_type == "material"
        # Middle 6 should be lattice (detailed-core)
        for layer in layers.layers[3:9]:
            assert layer.fill_type == "lattice"

    def test_no_gap_in_axial_layers(self):
        layers = build_vera4_axial_layers()
        for i in range(len(layers.layers) - 1):
            gap = abs(layers.layers[i + 1].z_min_cm - layers.layers[i].z_max_cm)
            assert gap < 1e-6, f"Gap between layer {i} and {i+1}: {gap}"

    def test_no_overlap_in_axial_layers(self):
        layers = build_vera4_axial_layers()
        for i in range(len(layers.layers) - 1):
            overlap = layers.layers[i].z_max_cm - layers.layers[i + 1].z_min_cm
            assert overlap < 1e-6, f"Overlap between layer {i} and {i+1}: {overlap}"

    def test_domain_boundary_coverage(self):
        """First layer starts at domain min, last layer ends at domain max."""
        layers = build_vera4_axial_layers()
        assert layers.layers[0].z_min_cm == Z_DOMAIN_MIN
        assert layers.layers[-1].z_max_cm == Z_DOMAIN_MAX

    def test_assembled_axial_layers_cover_full_domain(self):
        """The assembled plan should have axial layers covering -55 to 463.937."""
        patches = build_all_vera4_patches()
        result = assemble_simulation_plan_from_patches(patches, strict=False)
        assert result.ok
        assert result.plan is not None
        layers = result.plan.complex_model.core.axial_layers
        assert layers[0].z_min_cm == Z_DOMAIN_MIN
        assert layers[-1].z_max_cm == Z_DOMAIN_MAX

    def test_assembled_has_whole_plane_and_detailed_mix(self):
        """Assembled layers should contain both material and lattice fills."""
        patches = build_all_vera4_patches()
        result = assemble_simulation_plan_from_patches(patches, strict=False)
        layers = result.plan.complex_model.core.axial_layers
        fill_types = {l.fill.type for l in layers}
        assert "material" in fill_types
        assert "lattice" in fill_types

    def test_no_segments_outside_domain(self):
        """No assembled axial layer should extend beyond the domain."""
        patches = build_all_vera4_patches()
        result = assemble_simulation_plan_from_patches(patches, strict=False)
        layers = result.plan.complex_model.core.axial_layers
        for ly in layers:
            assert ly.z_min_cm >= Z_DOMAIN_MIN - 1e-3, f"layer {ly.id} below domain"
            assert ly.z_max_cm <= Z_DOMAIN_MAX + 1e-3, f"layer {ly.id} above domain"
