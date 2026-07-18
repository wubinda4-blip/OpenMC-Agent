"""Tests for AxialGeometryBindingView construction."""

from tests._axial_geometry_fixtures import state_with_axial_patches, make_facts_content, make_axial_layers_content, make_axial_overlays_content
from openmc_agent.plan_builder.closed_loop.axial_geometry_binding import build_axial_geometry_binding_view, derive_axial_geometry_segments
from openmc_agent.plan_builder.closed_loop.models import AxialLayerRecord, AxialOverlayRecord


def test_binding_view_extracts_domain_and_active_fuel():
    state = state_with_axial_patches()
    view = build_axial_geometry_binding_view(state=state)
    assert view.axial_domain_cm == (0.0, 100.0)
    assert view.active_fuel_region_cm == (10.0, 90.0)


def test_binding_view_extracts_layers():
    state = state_with_axial_patches()
    view = build_axial_geometry_binding_view(state=state)
    assert len(view.axial_layer_records) == 3
    fuel = next(l for l in view.axial_layer_records if l.role == "active_fuel")
    assert fuel.fill_type == "lattice"
    assert fuel.fill_id == "lat1"
    assert "ld1" in fuel.loading_ids


def test_binding_view_extracts_overlays():
    state = state_with_axial_patches()
    view = build_axial_geometry_binding_view(state=state)
    assert len(view.axial_overlay_records) == 2
    assert all(o.overlay_kind == "spacer_grid" for o in view.axial_overlay_records)


def test_binding_view_extracts_loadings():
    state = state_with_axial_patches()
    view = build_axial_geometry_binding_view(state=state)
    assert len(view.lattice_loading_records) >= 1
    loading = view.lattice_loading_records[0]
    assert loading.loading_id == "ld1"
    assert loading.base_lattice_id == "lat1"


def test_binding_view_builds_through_path_records():
    state = state_with_axial_patches()
    view = build_axial_geometry_binding_view(state=state)
    assert len(view.through_path_records) > 0
    lattice_tp = [t for t in view.through_path_records if t.path_kind == "base_lattice"]
    assert len(lattice_tp) >= 1
    assert all(t.preserved for t in lattice_tp)


def test_binding_view_builds_derived_segments():
    state = state_with_axial_patches()
    view = build_axial_geometry_binding_view(state=state)
    assert len(view.derived_segments) > 0
    seg = view.derived_segments[0]
    assert seg.z_min_cm < seg.z_max_cm


def test_derive_segments_stable_sort():
    layers = [AxialLayerRecord(layer_id="a", z_min_cm=0.0, z_max_cm=50.0), AxialLayerRecord(layer_id="b", z_min_cm=50.0, z_max_cm=100.0)]
    overlays = [AxialOverlayRecord(overlay_id="o1", z_min_cm=20.0, z_max_cm=20.5)]
    segs = derive_axial_geometry_segments(axial_domain=(0.0, 100.0), layers=layers, overlays=overlays, profiles=[], inserts=[])
    z_mins = [s.z_min_cm for s in segs]
    assert z_mins == sorted(z_mins)
    assert all(s.z_max_cm > s.z_min_cm for s in segs)


def test_derive_segments_zero_thickness_not_emitted():
    layers = [AxialLayerRecord(layer_id="a", z_min_cm=0.0, z_max_cm=50.0)]
    segs = derive_axial_geometry_segments(axial_domain=(0.0, 50.0), layers=layers, overlays=[], profiles=[], inserts=[])
    assert all(s.z_max_cm - s.z_min_cm > 1e-6 for s in segs)
