"""Tests for the XML integrity gate (non-OpenMC).

Uses synthetic XML files to verify the integrity checker logic.
"""

from __future__ import annotations

import pytest
from pathlib import Path

from openmc_agent.plan_builder.grid_xml_integrity import (
    check_grid_xml_integrity,
    XMLIntegrityReport,
)


def _write_xml(tmp_path: Path, *, n_decorated: int = 2, n_frames: int = 2,
               with_materials: bool = True, with_xplanes: bool = True):
    """Write synthetic XML files and a minimal model.py for testing."""
    materials_lines = ['<?xml version="1.0"?>', '<materials>']
    if with_materials:
        materials_lines.append('  <material id="1" name="Inconel-718">')
        materials_lines.append('    <density units="g/cc" value="8.19" />')
        materials_lines.append('    <nuclide name="Ni58" wo="1.0" />')
        materials_lines.append('  </material>')
        materials_lines.append('  <material id="2" name="Zircaloy-4">')
        materials_lines.append('    <density units="g/cc" value="6.55" />')
        materials_lines.append('  </material>')
    materials_lines.append('</materials>')
    (tmp_path / "materials.xml").write_text("\n".join(materials_lines))

    geo_lines = ['<?xml version="1.0"?>', '<geometry>']
    for i in range(n_decorated):
        geo_lines.append(f'  <universe id="{10+i}">')
        geo_lines.append(f'    <cell id="{i*10+1}" name="fuel" />')
        geo_lines.append(f'    <cell id="{i*10+3}" name="grid_frame" />')
        geo_lines.append('  </universe>')
    if with_xplanes:
        for i in range(n_frames * 8):
            geo_lines.append(f'  <surface id="{100+i}" type="x-plane" coeffs="0.5" />')
    geo_lines.append('</geometry>')
    (tmp_path / "geometry.xml").write_text("\n".join(geo_lines))

    # Write minimal settings.xml
    (tmp_path / "settings.xml").write_text('<?xml version="1.0"?>\n<settings></settings>')

    # Write minimal model.py with __grid__ references
    model_lines = ['import openmc', '']
    for i in range(n_decorated):
        model_lines.append(f"surfaces['surf_pin__grid__{i:012x}_frame'] = openmc.XPlane()")
    if with_materials:
        model_lines.append(f"materials_by_id['inconel718'] = openmc.Material(name='Inconel-718')")
        model_lines.append(f"materials_by_id['zircaloy4'] = openmc.Material(name='Zircaloy-4')")
    (tmp_path / "model.py").write_text("\n".join(model_lines))


class TestXMLIntegrityPositive:
    def test_all_present(self, tmp_path):
        _write_xml(tmp_path, n_decorated=2, n_frames=2, with_materials=True, with_xplanes=True)
        report = check_grid_xml_integrity(
            tmp_path,
            expected_decorated_count=2,
            expected_grid_materials={"inconel718", "zircaloy4"},
            expected_frame_cell_count=2,
        )
        assert report.ok
        assert len(report.errors) == 0


class TestXMLIntegrityNegative:
    def test_missing_materials_xml(self, tmp_path):
        (tmp_path / "geometry.xml").write_text("<geometry></geometry>")
        report = check_grid_xml_integrity(tmp_path)
        assert not report.ok

    def test_missing_geometry_xml(self, tmp_path):
        (tmp_path / "materials.xml").write_text("<materials></materials>")
        report = check_grid_xml_integrity(tmp_path)
        assert not report.ok

    def test_missing_grid_material(self, tmp_path):
        _write_xml(tmp_path, n_decorated=1, n_frames=1, with_materials=False)
        report = check_grid_xml_integrity(
            tmp_path,
            expected_grid_materials={"inconel718"},
        )
        assert not report.ok
        assert any(i.code == "xml.grid_material_missing" for i in report.errors)

    def test_insufficient_decorated_universes(self, tmp_path):
        _write_xml(tmp_path, n_decorated=1, n_frames=1)
        report = check_grid_xml_integrity(
            tmp_path,
            expected_decorated_count=5,
        )
        assert not report.ok
        assert any(i.code == "xml.decorated_universe_count_mismatch" for i in report.errors)

    def test_insufficient_frame_cells(self, tmp_path):
        _write_xml(tmp_path, n_decorated=1, n_frames=0)
        report = check_grid_xml_integrity(
            tmp_path,
            expected_frame_cell_count=3,
        )
        assert not report.ok
        assert any(i.code == "xml.frame_cell_count_mismatch" for i in report.errors)


class TestXMLIntegrityEmpty:
    def test_no_expectations_passes(self, tmp_path):
        _write_xml(tmp_path, n_decorated=0, n_frames=0)
        report = check_grid_xml_integrity(tmp_path)
        assert report.ok
