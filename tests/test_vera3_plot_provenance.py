"""Tests for VERA3 plot provenance and correctness.

Verifies that:
- Plot generation uses the correct cwd (outdir)
- Stale XML files are not reused
- Plot exceptions are not silently swallowed
- Plot coordinates match expected pin positions
- Annotated plots have correct extent metadata
- 3A and 3B geometry hashes differ
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from openmc_agent.plan_builder.patches import parse_patch_content
from openmc_agent.plan_builder.assembler import assemble_simulation_plan_from_patches
from openmc_agent.renderers.assembly import RectAssemblyRenderer

FIXTURES = {
    "3A": ROOT / "tests/fixtures/vera3_patches/vera3_3a_patches.json",
    "3B": ROOT / "tests/fixtures/vera3_patches/vera3_3b_patches.json",
}


def _load_plan(variant: str):
    with open(FIXTURES[variant]) as f:
        data = json.load(f)
    patches = [parse_patch_content(p["patch_type"], p) for p in data["patches"]]
    result = assemble_simulation_plan_from_patches(patches)
    assert result.ok
    return result.plan


def _render_and_export(plan, tmp_path: Path) -> Path:
    renderer = RectAssemblyRenderer()
    renderer.render(plan, tmp_path)
    proc = subprocess.run(
        [sys.executable, str(tmp_path / "model.py")],
        cwd=str(tmp_path), capture_output=True, text=True, timeout=60,
    )
    assert proc.returncode == 0
    return tmp_path


class TestPlotProvenance:
    """Verify plot generation provenance."""

    @pytest.mark.openmc
    def test_plot_manifest_has_correct_hashes(self, tmp_path):
        from scripts.generate_vera3_geometry_acceptance import _generate_plots
        plan = _load_plan("3B")
        outdir = _render_and_export(plan, tmp_path)
        result = _generate_plots(outdir, "3B", plan)
        assert result["errors"] == [], f"Plot errors: {result['errors']}"
        assert len(result["generated"]) > 0
        geom_hash = hashlib.sha256((outdir / "geometry.xml").read_bytes()).hexdigest()
        assert result["geometry_xml_sha256"] == geom_hash

    @pytest.mark.openmc
    def test_plot_cwd_is_outdir(self, tmp_path):
        """Plots must be generated from XML in outdir, not cwd."""
        from scripts.generate_vera3_geometry_acceptance import _generate_plots
        plan = _load_plan("3B")
        outdir = _render_and_export(plan, tmp_path)
        result = _generate_plots(outdir, "3B", plan)
        assert result["errors"] == [], f"Plot errors: {result['errors']}"
        manifest = result["manifest"]
        for entry in manifest:
            raw_file = outdir / entry["generated_file"]
            assert raw_file.exists(), f"Plot file not found: {raw_file}"

    @pytest.mark.openmc
    def test_stale_xml_not_reused(self, tmp_path):
        """Stale XML files should not produce the same geometry hash."""
        from scripts.generate_vera3_geometry_acceptance import _generate_plots
        plan_3a = _load_plan("3A")
        outdir_3a = _render_and_export(plan_3a, tmp_path / "3A")
        plan_3b = _load_plan("3B")
        outdir_3b = _render_and_export(plan_3b, tmp_path / "3B")

        result_3a = _generate_plots(outdir_3a, "3A", plan_3a)
        result_3b = _generate_plots(outdir_3b, "3B", plan_3b)

        assert result_3a["geometry_xml_sha256"] != result_3b["geometry_xml_sha256"]

    @pytest.mark.openmc
    def test_no_silent_except(self, tmp_path):
        """Plot generation must report errors, not swallow them."""
        from scripts.generate_vera3_geometry_acceptance import _generate_plots
        plan = _load_plan("3B")
        outdir = _render_and_export(plan, tmp_path)
        # Delete plots.xml to force an error
        (outdir / "plots.xml").unlink(missing_ok=True)
        # The function should either regenerate plots.xml or report the error
        result = _generate_plots(outdir, "3B", plan)
        # It should either succeed (regenerating plots.xml) or have errors
        # but never silently produce nothing
        total = len(result["generated"]) + len(result["errors"])
        assert total > 0, "Plot generation silently produced nothing"


class TestPlotCoordinates:
    """Verify plot coordinates match actual pin positions."""

    def test_ordinary_fuel_not_center(self):
        """ordinary_fuel_pin must not use [9,9] (instrument tube)."""
        from scripts.generate_vera3_geometry_acceptance import _xz_positions
        positions = _xz_positions("3B")
        fuel_pos = [p for p in positions if "fuel" in p["label"]]
        assert len(fuel_pos) == 1
        assert fuel_pos[0]["row_col_1based"] != [9, 9], (
            "ordinary_fuel_pin should not use center position [9,9]"
        )

    def test_3b_guide_labels_are_specific(self):
        """3B XZ positions should use specific labels, not 'ordinary_guide_tube'."""
        from scripts.generate_vera3_geometry_acceptance import _xz_positions
        positions = _xz_positions("3B")
        labels = [p["label"] for p in positions]
        assert "ordinary_guide_tube" not in labels, (
            "3B should not have 'ordinary_guide_tube' — all guides are Pyrex or thimble"
        )

    def test_row_col_xy_roundtrip(self):
        """row/col → x/y → row/col should be identity."""
        from scripts.generate_vera3_geometry_acceptance import _row_col_to_xy, _xy_to_row_col
        for r in range(17):
            for c in range(17):
                x, y = _row_col_to_xy(r, c)
                r2, c2 = _xy_to_row_col(x, y)
                assert (r, c) == (r2, c2), f"Roundtrip failed: ({r},{c}) → ({r2},{c2})"

    def test_pyrex_coordinate_is_guide(self):
        """3B pyrex coordinate [3,6] must be in the guide tube set."""
        from scripts.generate_vera3_geometry_acceptance import _xz_positions
        positions = _xz_positions("3B")
        pyrex_pos = [p for p in positions if "pyrex" in p["label"]]
        assert pyrex_pos
        r, c = pyrex_pos[0]["row_col_1based"]
        assert [r, c] == [3, 6]


class TestAnnotatedPlots:
    """Verify annotated plot metadata."""

    @pytest.mark.openmc
    def test_annotated_plots_exist(self, tmp_path):
        from scripts.generate_vera3_geometry_acceptance import _generate_plots
        plan = _load_plan("3B")
        outdir = _render_and_export(plan, tmp_path)
        result = _generate_plots(outdir, "3B", plan)
        assert result["errors"] == [], f"Plot errors: {result['errors']}"
        ann_dir = outdir / "plots" / "annotated"
        ann_files = list(ann_dir.glob("*.png"))
        assert len(ann_files) > 0, "No annotated plot files generated"

    @pytest.mark.openmc
    def test_annotated_extent_metadata(self, tmp_path):
        """Annotated plot manifest must have correct extent."""
        from scripts.generate_vera3_geometry_acceptance import _generate_plots
        plan = _load_plan("3B")
        outdir = _render_and_export(plan, tmp_path)
        result = _generate_plots(outdir, "3B", plan)
        for entry in result["manifest"]:
            assert "width_cm" in entry
            assert "origin_cm" in entry
            assert len(entry["width_cm"]) == 2
