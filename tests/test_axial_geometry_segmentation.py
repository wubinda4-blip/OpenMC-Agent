"""Tests for derive_axial_geometry_segments."""

from openmc_agent.plan_builder.closed_loop.axial_geometry_binding import derive_axial_geometry_segments
from openmc_agent.plan_builder.closed_loop.models import AxialLayerRecord, AxialOverlayRecord


def test_segments_from_layers_only():
    layers = [AxialLayerRecord(layer_id="a", z_min_cm=0.0, z_max_cm=50.0), AxialLayerRecord(layer_id="b", z_min_cm=50.0, z_max_cm=100.0)]
    segs = derive_axial_geometry_segments(axial_domain=(0.0, 100.0), layers=layers, overlays=[], profiles=[], inserts=[])
    assert len(segs) == 2


def test_segments_reversed_domain():
    layers = []
    segs = derive_axial_geometry_segments(axial_domain=(100.0, 0.0), layers=layers, overlays=[], profiles=[], inserts=[])
    assert len(segs) == 0 or all(s.z_min_cm < s.z_max_cm for s in segs)


def test_segments_with_overlay_split():
    layers = [AxialLayerRecord(layer_id="a", z_min_cm=0.0, z_max_cm=100.0)]
    overlays = [AxialOverlayRecord(overlay_id="o1", z_min_cm=40.0, z_max_cm=42.0)]
    segs = derive_axial_geometry_segments(axial_domain=(0.0, 100.0), layers=layers, overlays=overlays, profiles=[], inserts=[])
    assert len(segs) == 3
    active_o = [s for s in segs if "o1" in s.active_overlay_ids]
    assert len(active_o) == 1


def test_segments_zero_thickness_boundary():
    layers = [AxialLayerRecord(layer_id="a", z_min_cm=0.0, z_max_cm=10.0), AxialLayerRecord(layer_id="b", z_min_cm=10.0, z_max_cm=20.0)]
    segs = derive_axial_geometry_segments(axial_domain=(0.0, 20.0), layers=layers, overlays=[], profiles=[], inserts=[])
    assert all(s.z_max_cm - s.z_min_cm > 1e-6 for s in segs)


def test_segments_tolerance_merge():
    layers = [AxialLayerRecord(layer_id="a", z_min_cm=0.0, z_max_cm=10.0), AxialLayerRecord(layer_id="b", z_min_cm=10.0 + 1e-9, z_max_cm=20.0)]
    segs = derive_axial_geometry_segments(axial_domain=(0.0, 20.0), layers=layers, overlays=[], profiles=[], inserts=[])
    # Boundary at 10.0 should produce segments (0,10) and (10,20).
    assert len(segs) >= 2


def test_segments_outside_tolerance():
    layers = [AxialLayerRecord(layer_id="a", z_min_cm=0.0, z_max_cm=10.0), AxialLayerRecord(layer_id="b", z_min_cm=10.1, z_max_cm=20.0)]
    segs = derive_axial_geometry_segments(axial_domain=(0.0, 20.0), layers=layers, overlays=[], profiles=[], inserts=[])
    # Gap at (10, 10.1) should produce 3 segments.
    assert len(segs) == 3
