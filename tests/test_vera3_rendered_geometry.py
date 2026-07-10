"""Regression tests for VERA3 rendered geometry via the production pipeline.

These tests exercise the **full production chain**:

    patch fixture → assemble → RectAssemblyRenderer.render → execute model.py
    → load geometry.xml → point-probe actual OpenMC geometry

They verify that ``compose_lattice_loadings`` is actually called by the
production renderer (Root Cause A) and that axial loading materialization
produces the expected materials at physically meaningful points.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest
import openmc

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from openmc_agent.plan_builder.patches import parse_patch_content
from openmc_agent.plan_builder.assembler import assemble_simulation_plan_from_patches
from openmc_agent.renderers.assembly import RectAssemblyRenderer

PITCH_CM = 1.26
LATTICE_SIZE = 17
ASSEMBLY_PITCH = 21.50

FIXTURES = {
    "3A": ROOT / "tests/fixtures/vera3_patches/vera3_3a_patches.json",
    "3B": ROOT / "tests/fixtures/vera3_patches/vera3_3b_patches.json",
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _load_and_assemble(variant: str):
    with open(FIXTURES[variant]) as f:
        data = json.load(f)
    patches = [parse_patch_content(p["patch_type"], p) for p in data["patches"]]
    result = assemble_simulation_plan_from_patches(patches)
    assert result.ok, f"Assembly failed: {[i.message for i in result.issues if i.severity == 'error']}"
    return result.plan


def _render_and_export(plan, tmp_path: Path) -> Path:
    """Render model.py, execute to XML, return the output directory."""
    renderer = RectAssemblyRenderer()
    rr = renderer.render(plan, tmp_path)
    assert rr.renderability in ("exportable", "runnable"), f"renderability={rr.renderability}"
    model_py = tmp_path / "model.py"
    proc = subprocess.run(
        [sys.executable, str(model_py)],
        cwd=str(tmp_path), capture_output=True, text=True, timeout=60,
    )
    assert proc.returncode == 0, f"model.py execution failed:\n{proc.stderr[-500:]}"
    return tmp_path


def _row_col_to_xy(row_0: int, col_0: int) -> tuple[float, float]:
    """Convert 0-based (row, col) to (x, y) in cm.

    Lattice lower_left is (0, 0), rows go top-to-bottom in the pin map but
    OpenMC RectLattice indexes bottom-to-top, so y is flipped.
    """
    x = (col_0 + 0.5) * PITCH_CM
    y = (LATTICE_SIZE - 1 - row_0 + 0.5) * PITCH_CM
    return x, y


def _load_geometry(xml_dir: Path):
    """Load OpenMC geometry from exported XML."""
    import openmc
    materials = openmc.Materials.from_xml(xml_dir / "materials.xml")
    geom = openmc.Geometry.from_xml(xml_dir / "geometry.xml", materials)
    return geom


def material_names_at_point(
    geometry,
    point: tuple[float, float, float],
) -> list[str]:
    """Return material name(s) at the given 3-D point via geometry.find."""
    import numpy as np
    try:
        result = geometry.find(np.array(point))
    except Exception:
        return []
    if not result:
        return []
    names = []
    for item in result:
        cell = None
        if isinstance(item, openmc.Cell):
            cell = item
        elif isinstance(item, tuple) and len(item) >= 1 and isinstance(item[0], openmc.Cell):
            cell = item[0]
        if cell is not None:
            fill = cell.fill
            if isinstance(fill, openmc.Material):
                names.append(fill.name)
    return names


# ---------------------------------------------------------------------------
# Plan-level tests (no OpenMC execution needed)
# ---------------------------------------------------------------------------


class TestMaterializationInProductionRenderer:
    """Verify that the production renderer materializes lattice loadings."""

    def test_3b_plenum_layer_not_base_lattice(self, tmp_path):
        """After rendering, the plenum layer must not still reference assembly_lattice."""
        plan = _load_and_assemble("3B")
        outdir = _render_and_export(plan, tmp_path)
        model_py = outdir / "model.py"
        content = model_py.read_text()
        assert "assembly_lattice_plenum" in content, (
            "Derived plenum lattice not found in rendered model.py — "
            "compose_lattice_loadings was not called by the production renderer"
        )

    def test_3b_pyrex_derived_lattice_in_model(self, tmp_path):
        """The Pyrex nested lattice must appear in rendered model.py."""
        plan = _load_and_assemble("3B")
        outdir = _render_and_export(plan, tmp_path)
        content = (outdir / "model.py").read_text()
        assert "assembly_lattice_pyrex_nested" in content, (
            "Derived Pyrex lattice not found in rendered model.py"
        )

    def test_3b_thimble_derived_lattice_in_model(self, tmp_path):
        """The thimble nested lattice must appear in rendered model.py."""
        plan = _load_and_assemble("3B")
        outdir = _render_and_export(plan, tmp_path)
        content = (outdir / "model.py").read_text()
        assert "assembly_lattice_thimble_nested" in content, (
            "Derived thimble lattice not found in rendered model.py"
        )

    def test_3b_shoulder_lattice_in_model(self, tmp_path):
        """The shoulder water lattice must appear in rendered model.py."""
        plan = _load_and_assemble("3B")
        outdir = _render_and_export(plan, tmp_path)
        content = (outdir / "model.py").read_text()
        assert "assembly_lattice_shoulder_water" in content, (
            "Derived shoulder lattice not found in rendered model.py"
        )


# ---------------------------------------------------------------------------
# Point-probe tests (require OpenMC geometry loading)
# ---------------------------------------------------------------------------


@pytest.mark.openmc
class TestRenderedGeometryPointProbes:
    """Point-probe the actual rendered OpenMC geometry."""

    @pytest.fixture(scope="class")
    def geometry_3b(self, tmp_path_factory):
        plan = _load_and_assemble("3B")
        outdir = tmp_path_factory.mktemp("vera3_3b_geom")
        outdir = _render_and_export(plan, outdir)
        return _load_geometry(outdir)

    def test_fuel_active_is_uo2(self, geometry_3b):
        """At z=100 cm, fuel pin center [1,1] should be UO2/fuel."""
        x, y = _row_col_to_xy(0, 0)  # [1,1] 1-based
        z = 100.0
        names = material_names_at_point(geometry_3b, (x, y, z))
        assert any("uo2" in n.lower() or "fuel" in n.lower() for n in names), (
            f"Expected UO2/fuel at fuel pin center, got {names}"
        )

    def test_plenum_pin_center_is_helium(self, geometry_3b):
        """At z=382 cm, fuel pin center [1,1] should be helium."""
        x, y = _row_col_to_xy(0, 0)  # [1,1] 1-based
        z = 382.0
        names = material_names_at_point(geometry_3b, (x, y, z))
        assert any("he" in n.lower() or "helium" in n.lower() for n in names), (
            f"Expected helium at plenum pin center, got {names}"
        )

    def test_fuel_cladding_is_zircaloy(self, geometry_3b):
        """At z=100 cm, r~0.44 from fuel pin [1,1] center should be Zircaloy."""
        x_center, y_center = _row_col_to_xy(0, 0)
        offset = 0.44
        x, y = x_center + offset, y_center
        z = 100.0
        names = material_names_at_point(geometry_3b, (x, y, z))
        assert any("zirc" in n.lower() or "zr" in n.lower() or "clad" in n.lower() for n in names), (
            f"Expected Zircaloy at cladding radius, got {names}"
        )

    def test_pyrex_annulus_is_pyrex(self, geometry_3b):
        """At z=100 cm, Pyrex coordinate [3,6], r~0.30 should be Pyrex material."""
        x_center, y_center = _row_col_to_xy(2, 5)  # [3,6] 1-based
        offset = 0.30
        x, y = x_center + offset, y_center
        z = 100.0
        names = material_names_at_point(geometry_3b, (x, y, z))
        assert any("pyrex" in n.lower() or "borosil" in n.lower() for n in names), (
            f"Expected Pyrex at poison annulus, got {names}"
        )

    def test_pyrex_guide_wall_is_zircaloy(self, geometry_3b):
        """At z=100 cm, Pyrex coordinate [3,6], r~0.58 should be Zircaloy guide wall."""
        x_center, y_center = _row_col_to_xy(2, 5)  # [3,6] 1-based
        offset = 0.58
        x, y = x_center + offset, y_center
        z = 100.0
        names = material_names_at_point(geometry_3b, (x, y, z))
        assert any("zirc" in n.lower() or "zr" in n.lower() for n in names), (
            f"Expected Zircaloy at guide wall radius, got {names}"
        )

    def test_thimble_center_is_ss304(self, geometry_3b):
        """At z=384 cm, thimble coordinate [3,9] center should be SS304."""
        x, y = _row_col_to_xy(2, 8)  # [3,9] 1-based
        z = 384.0
        names = material_names_at_point(geometry_3b, (x, y, z))
        assert any("ss304" in n.lower() or "ss-304" in n.lower() or "stainless" in n.lower() for n in names), (
            f"Expected SS304 thimble plug at z=384, got {names}"
        )

    def test_thimble_outside_plug_is_water(self, geometry_3b):
        """At z=382 cm (below plug), thimble coordinate [3,9] center should be water."""
        x, y = _row_col_to_xy(2, 8)  # [3,9] 1-based
        z = 382.0
        names = material_names_at_point(geometry_3b, (x, y, z))
        assert any("water" in n.lower() or "borated" in n.lower() for n in names), (
            f"Expected water below thimble plug at z=382, got {names}"
        )

    def test_thimble_above_plug_is_water(self, geometry_3b):
        """At z=394.5 cm (above plug), thimble coordinate [3,9] center should be water."""
        x, y = _row_col_to_xy(2, 8)  # [3,9] 1-based
        z = 394.5
        names = material_names_at_point(geometry_3b, (x, y, z))
        assert any("water" in n.lower() or "borated" in n.lower() for n in names), (
            f"Expected water above thimble plug at z=394.5, got {names}"
        )

    def test_lower_shoulder_guide_wall(self, geometry_3b):
        """At z=7 cm (lower shoulder), guide tube wall should still exist."""
        x_center, y_center = _row_col_to_xy(2, 5)  # [3,6] guide position
        offset = 0.58
        x, y = x_center + offset, y_center
        z = 7.0
        names = material_names_at_point(geometry_3b, (x, y, z))
        assert any("zirc" in n.lower() or "zr" in n.lower() for n in names), (
            f"Expected Zircaloy guide wall in lower shoulder, got {names}"
        )

    def test_upper_shoulder_guide_wall(self, geometry_3b):
        """At z=396 cm (upper shoulder), guide tube wall should still exist."""
        x_center, y_center = _row_col_to_xy(2, 5)  # [3,6] guide position
        offset = 0.58
        x, y = x_center + offset, y_center
        z = 396.0
        names = material_names_at_point(geometry_3b, (x, y, z))
        assert any("zirc" in n.lower() or "zr" in n.lower() for n in names), (
            f"Expected Zircaloy guide wall in upper shoulder, got {names}"
        )

    def test_shoulder_fuel_position_is_water(self, geometry_3b):
        """At z=7 cm (lower shoulder), fuel position [1,1] center should be water."""
        x, y = _row_col_to_xy(0, 0)  # [1,1] 1-based
        z = 7.0
        names = material_names_at_point(geometry_3b, (x, y, z))
        assert any("water" in n.lower() or "borated" in n.lower() for n in names), (
            f"Expected water at fuel position in shoulder, got {names}"
        )


# ---------------------------------------------------------------------------
# Variant differentiation
# ---------------------------------------------------------------------------


class TestVariantDifferentiation:
    """3A and 3B must produce different geometry."""

    def test_3a_3b_geometry_hashes_differ(self, tmp_path):
        import hashlib

        dir_3a = tmp_path / "3A"
        dir_3b = tmp_path / "3B"
        dir_3a.mkdir()
        dir_3b.mkdir()

        for variant, d in [("3A", dir_3a), ("3B", dir_3b)]:
            plan = _load_and_assemble(variant)
            _render_and_export(plan, d)

        geom_3a = (dir_3a / "geometry.xml").read_bytes()
        geom_3b = (dir_3b / "geometry.xml").read_bytes()
        hash_3a = hashlib.sha256(geom_3a).hexdigest()
        hash_3b = hashlib.sha256(geom_3b).hexdigest()
        assert hash_3a != hash_3b, "3A and 3B geometry.xml are identical"
