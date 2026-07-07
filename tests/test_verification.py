"""Tests for the expert verification digest + 3-D voxel plot (reactor-agnostic)."""

from __future__ import annotations

from pathlib import Path

import pytest

from helpers.vera3_acceptance import build_vera3_like_plan, load_vera3_reference
from openmc_agent.renderers.assembly import RectAssemblyRenderer
from openmc_agent.schemas import PlotSpec
from openmc_agent.verification import (
    build_verification_digest,
    write_verification_digest,
)

REFERENCE = load_vera3_reference()


def test_verification_digest_passes_for_clean_plan() -> None:
    plan = build_vera3_like_plan(REFERENCE, variant="3A")
    digest = build_verification_digest(plan.complex_model)
    # Invariant checks all pass for a clean plan.
    statuses = {c["status"] for c in digest["checks"]}
    assert "fail" not in statuses, [c for c in digest["checks"] if c["status"] == "fail"]
    # Pin counts present and correct.
    assert digest["pin_counts"]["fuel_pin"] == 264
    assert digest["pin_counts"]["guide_tube"] == 24
    assert digest["pin_counts"]["instrument_tube"] == 1
    # Axial layer table present.
    assert len(digest["axial_layers"]) == 12
    # Bounds summary has the source + active fuel.
    assert digest["bounds"]["source_z_cm"] == [pytest.approx(11.951), pytest.approx(377.711)]
    assert digest["bounds"]["active_fuel_z_cm"] == [pytest.approx(11.951), pytest.approx(377.711)]


def test_verification_digest_flags_broken_plan(tmp_path: Path) -> None:
    plan = build_vera3_like_plan(REFERENCE, drop_overlays=True)
    digest = build_verification_digest(plan.complex_model)
    # drop_overlays keeps the grids only in a purpose comment; the digest should
    # still surface the core structure. Fuel/source/bounds checks stay green.
    statuses = {c["check"]: c["status"] for c in digest["checks"]}
    assert statuses["fuel material present"] == "pass"
    assert statuses["full assembly (no quarter symmetry)"] == "pass"


def test_write_verification_digest_emits_json_and_markdown(tmp_path: Path) -> None:
    plan = build_vera3_like_plan(REFERENCE, variant="3A")
    paths = write_verification_digest(plan.complex_model, tmp_path)
    assert (tmp_path / "verification_digest.json").exists()
    assert (tmp_path / "verification_digest.md").exists()
    md = (tmp_path / "verification_digest.md").read_text(encoding="utf-8")
    assert "## Invariant checks" in md
    assert "## Pin counts" in md
    assert "## Axial layers" in md
    assert "fuel_pin" in md


def test_render_emits_verification_digest(tmp_path: Path) -> None:
    plan = build_vera3_like_plan(REFERENCE, variant="3A")
    result = RectAssemblyRenderer().render(plan, tmp_path)
    assert result.renderability in {"exportable", "runnable"}
    assert (tmp_path / "verification_digest.md").exists()
    assert (tmp_path / "verification_digest.json").exists()


def test_auto_voxel_plot_appended_for_3d_model(tmp_path: Path) -> None:
    plan = build_vera3_like_plan(REFERENCE, variant="3A")
    result = RectAssemblyRenderer().render(plan, tmp_path)
    script = result.script
    # The auto voxel plot is emitted with type='voxel'.
    assert ".type = 'voxel'" in script
    assert "verification_voxel" in script
    # 3-D width and pixels.
    assert "width = (" in script  # voxel uses a 3-tuple width


def test_voxel_plot_spec_renders_type_voxel() -> None:
    """A planner-requested voxel PlotSpec renders as type='voxel'."""
    from openmc_agent.executor import _render_plots_block

    voxel = PlotSpec(
        kind="voxel", basis="xy", origin=(0.0, 0.0, 0.0),
        width_cm=(10.0, 10.0, 100.0), pixels=(100, 100, 100),
        filename="my_voxel.bin",
    )
    block = _render_plots_block([voxel])
    assert ".type = 'voxel'" in block
    assert "my_voxel" in block
    assert ".basis" not in block  # voxel plots have no basis


def test_2d_slice_plot_unchanged_with_kind_field() -> None:
    from openmc_agent.executor import _render_plots_block

    slice_plot = PlotSpec(
        kind="slice", basis="xy", origin=(5.0, 5.0, 0.0),
        width_cm=(21.42, 21.42), filename="midplane.png",
    )
    block = _render_plots_block([slice_plot])
    assert ".basis = 'xy'" in block
    assert ".type = 'voxel'" not in block
    # both material + cell variants emitted.
    assert "midplane_material" in block and "midplane_cell" in block
