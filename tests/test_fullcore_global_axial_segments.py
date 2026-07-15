"""Tests for global full-core axial segment compilation (P2-FULLCORE-2A)."""

from openmc_agent.plan_builder.patches import (
    AssemblyCatalogPatch,
    AssemblyPinMapPatchItem,
    AssemblyTypePatchItem,
    FactsPatch,
    LocalizedInsertIntentPatchItem,
)
from openmc_agent.plan_builder.hierarchical_assembler import (
    AxialSegment,
    compile_global_axial_segments,
)


def _make_catalog(inserts_by_type=None):
    types = []
    for tid in (inserts_by_type or {}).keys():
        intents = []
        for spec in inserts_by_type[tid]:
            intents.append(LocalizedInsertIntentPatchItem(**spec))
        types.append(AssemblyTypePatchItem(
            assembly_type_id=tid,
            pin_map=AssemblyPinMapPatchItem(
                lattice_size=(3, 3),
                default_universe_id="fuel",
                guide_tube_coords=[(1, 1)],
                localized_insert_intents=intents,
            ),
        ))
    return AssemblyCatalogPatch(assembly_types=types)


def test_basic_segment_compilation():
    """Compile segments from axial domain boundaries."""
    catalog = _make_catalog({
        "type_a": [{
            "insert_id": "pyrex_a",
            "insert_kind": "pyrex_rod",
            "insert_universe_id": "pyrex",
            "coordinates": [(1, 1)],
            "z_min_cm": 50.0,
            "z_max_cm": 200.0,
        }],
    })
    facts = FactsPatch(axial_domain_cm=(0.0, 400.0))
    segments = compile_global_axial_segments(facts, catalog)
    assert len(segments) >= 2
    # First segment starts at 0, last ends at 400
    assert segments[0].z_min_cm == 0.0
    assert segments[-1].z_max_cm == 400.0


def test_insert_boundaries_create_segments():
    """Localized insert z boundaries should create new segment breakpoints."""
    catalog = _make_catalog({
        "type_a": [
            {
                "insert_id": "insert1",
                "insert_kind": "pyrex_rod",
                "insert_universe_id": "pyrex",
                "coordinates": [(1, 1)],
                "z_min_cm": 100.0,
                "z_max_cm": 300.0,
            },
        ],
    })
    facts = FactsPatch(axial_domain_cm=(0.0, 400.0))
    segments = compile_global_axial_segments(facts, catalog)
    # Breakpoints: 0, 100, 300, 400 → 3 segments
    assert len(segments) == 3
    assert segments[0].z_min_cm == 0.0
    assert segments[0].z_max_cm == 100.0
    assert segments[1].z_min_cm == 100.0
    assert segments[1].z_max_cm == 300.0
    assert segments[2].z_min_cm == 300.0
    assert segments[2].z_max_cm == 400.0


def test_segments_no_gap_no_overlap():
    """Compiled segments must be contiguous with no gaps or overlaps."""
    catalog = _make_catalog({
        "type_a": [
            {
                "insert_id": "i1",
                "insert_kind": "pyrex_rod",
                "insert_universe_id": "p",
                "coordinates": [(1, 1)],
                "z_min_cm": 50.0,
                "z_max_cm": 150.0,
            },
            {
                "insert_id": "i2",
                "insert_kind": "thimble_plug",
                "insert_universe_id": "t",
                "coordinates": [(0, 0)],
                "z_min_cm": 200.0,
                "z_max_cm": 350.0,
            },
        ],
    })
    facts = FactsPatch(axial_domain_cm=(0.0, 400.0))
    segments = compile_global_axial_segments(facts, catalog)
    for i in range(len(segments) - 1):
        assert segments[i].z_max_cm == segments[i + 1].z_min_cm


def test_active_inserts_per_segment():
    """Each segment should track which inserts are active."""
    catalog = _make_catalog({
        "type_a": [{
            "insert_id": "pyrex_a",
            "insert_kind": "pyrex_rod",
            "insert_universe_id": "pyrex",
            "coordinates": [(1, 1)],
            "z_min_cm": 0.0,
            "z_max_cm": 100.0,
        }],
        "type_b": [{
            "insert_id": "thimble_b",
            "insert_kind": "thimble_plug",
            "insert_universe_id": "thimble",
            "coordinates": [(0, 0)],
            "z_min_cm": 50.0,
            "z_max_cm": 200.0,
        }],
    })
    facts = FactsPatch(axial_domain_cm=(0.0, 400.0))
    segments = compile_global_axial_segments(facts, catalog)

    # Segment [0, 50]: only type_a pyrex active
    seg_0_50 = next(s for s in segments if s.z_min_cm == 0.0)
    assert "pyrex_a" in seg_0_50.active_inserts.get("type_a", [])
    assert "type_b" not in seg_0_50.active_inserts

    # Segment [50, 100]: both active
    seg_50_100 = next(s for s in segments if s.z_min_cm == 50.0)
    assert "pyrex_a" in seg_50_100.active_inserts.get("type_a", [])
    assert "thimble_b" in seg_50_100.active_inserts.get("type_b", [])

    # Segment [100, 200]: only type_b
    seg_100_200 = next(s for s in segments if s.z_min_cm == 100.0)
    assert "type_a" not in seg_100_200.active_inserts
    assert "thimble_b" in seg_100_200.active_inserts.get("type_b", [])

    # Segment [200, 400]: no inserts
    seg_200_400 = next(s for s in segments if s.z_min_cm == 200.0)
    assert len(seg_200_400.active_inserts) == 0


def test_different_types_different_inserts():
    """Different assembly types can have different insert z ranges."""
    catalog = _make_catalog({
        "corner": [{
            "insert_id": "thimble_corner",
            "insert_kind": "thimble_plug",
            "insert_universe_id": "thimble",
            "coordinates": [(1, 1)],
            "z_min_cm": 300.0,
            "z_max_cm": 400.0,
        }],
        "edge": [{
            "insert_id": "pyrex_edge",
            "insert_kind": "pyrex_rod",
            "insert_universe_id": "pyrex",
            "coordinates": [(0, 0)],
            "z_min_cm": 0.0,
            "z_max_cm": 100.0,
        }],
        "center": [{
            "insert_id": "rcca",
            "insert_kind": "control_rod",
            "insert_universe_id": "rcca",
            "coordinates": [(1, 1)],
            "z_min_cm": 257.9,
            "z_max_cm": 365.76,
        }],
    })
    facts = FactsPatch(axial_domain_cm=(0.0, 400.0))
    segments = compile_global_axial_segments(facts, catalog)
    # Should have breakpoints at 0, 100, 257.9, 300, 365.76, 400
    bps = [s.z_min_cm for s in segments] + [segments[-1].z_max_cm]
    assert 257.9 in bps
    assert 365.76 in bps


def test_no_axial_domain_returns_empty():
    """Without any breakpoints, return empty list."""
    catalog = AssemblyCatalogPatch(
        assembly_types=[
            AssemblyTypePatchItem(
                assembly_type_id="a",
                pin_map=AssemblyPinMapPatchItem(
                    lattice_size=(3, 3), default_universe_id="fuel",
                ),
            ),
        ],
    )
    segments = compile_global_axial_segments(None, catalog)
    assert len(segments) == 0


def test_spacer_grid_boundaries():
    """Spacer grid z boundaries should create additional breakpoints."""
    catalog = AssemblyCatalogPatch(
        assembly_types=[
            AssemblyTypePatchItem(
                assembly_type_id="a",
                pin_map=AssemblyPinMapPatchItem(
                    lattice_size=(3, 3), default_universe_id="fuel",
                ),
            ),
        ],
    )
    facts = FactsPatch(axial_domain_cm=(0.0, 400.0))
    segments = compile_global_axial_segments(
        facts, catalog,
        spacer_grid_z=[(50, 55), (150, 155), (250, 255)],
    )
    bps = [s.z_min_cm for s in segments] + [segments[-1].z_max_cm]
    assert 50 in bps
    assert 150 in bps
    assert 250 in bps
