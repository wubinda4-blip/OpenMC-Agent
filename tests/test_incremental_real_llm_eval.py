"""Tests for incremental evaluation harness and real LLM eval (Phase 7).

Real LLM tests are opt-in only: they skip unless ``OPENMC_AGENT_RUN_REAL_LLM_TESTS=1``.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from openmc_agent.plan_builder.evaluation import (
    EvaluationReport,
    run_incremental_evaluation,
)
from openmc_agent.plan_builder.patch_generator import FakePatchLLM


_FIXTURE_DIR = Path(__file__).parent / "fixtures" / "vera3_patches"


def _load_fixture_raw(variant: str) -> list[dict]:
    raw = json.loads((_FIXTURE_DIR / f"vera3_{variant}_patches.json").read_text("utf-8"))
    return raw["patches"]


_VERA3_3B_REQ = (
    "VERA3 3B benchmark: 3D assembly with axial layers, spacer grids, "
    "三维, 定位格架, Pyrex rods, thimble plugs, 17x17 lattice"
)


# ---------------------------------------------------------------------------
# 7. VERA3 3B fake eval report
# ---------------------------------------------------------------------------


def test_vera3_3b_fake_eval_report(tmp_path: Path) -> None:
    """Run fake-LLM evaluation and check report structure."""
    raw_patches = _load_fixture_raw("3b")
    llm_responses = [json.dumps(p) for p in raw_patches if p["patch_type"] != "settings"]
    fake = FakePatchLLM(llm_responses)

    report, state = run_incremental_evaluation(
        requirement=_VERA3_3B_REQ,
        benchmark_id="VERA3",
        selected_variant="3B",
        llm_client=fake,
        model="fake:model",
        max_patch_attempts=1,
        output_dir=tmp_path,
    )

    assert report.ok is True
    assert report.benchmark == "VERA3"
    assert report.variant == "3B"
    assert report.planning_mode == "incremental"
    assert report.no_monolithic_plan_requested is True

    # Patch metrics should exist for all generated types.
    assert "facts" in report.patch_metrics
    assert "pin_map" in report.patch_metrics
    assert "axial_layers" in report.patch_metrics
    assert "axial_overlays" in report.patch_metrics

    # Assembly summary.
    assert report.assembly.ok is True
    assert report.assembly.lattice_size == [17, 17]
    assert report.assembly.pyrex_count == 0
    assert report.assembly.thimble_plug_count == 0
    assert report.assembly.axial_layer_count == 14
    assert report.assembly.overlay_count == 8
    plan = state.assembled_plan["complex_model"]
    pyrex_loading = next(l for l in plan["lattice_loadings"] if l["id"] == "pyrex_active_loading")
    assert len(pyrex_loading["overrides"]["pyrex_rod"]) == 16

    # Guard check.
    assert report.guard.blocking_issue_count == 0

    # Output files written.
    assert (tmp_path / "evaluation_report.json").exists()
    assert (tmp_path / "plan_build_state.json").exists()
    assert (tmp_path / "assembled_plan.json").exists()
    assert (tmp_path / "patches" / "facts.json").exists()


# ---------------------------------------------------------------------------
# 5. Patch raw length metrics recorded
# ---------------------------------------------------------------------------


def test_patch_raw_chars_recorded(tmp_path: Path) -> None:
    raw = json.dumps({"patch_type": "facts", "benchmark_id": "T"})
    fake = FakePatchLLM([raw])
    report, _ = run_incremental_evaluation(
        requirement=_VERA3_3B_REQ,
        benchmark_id="T",
        llm_client=fake,
        max_patch_attempts=1,
    )
    # Report should exist even if it failed (only facts generated).
    assert "facts" in report.patch_metrics


# ---------------------------------------------------------------------------
# 6. Pin_map full lattice detection
# ---------------------------------------------------------------------------


def test_pin_map_full_lattice_detection(tmp_path: Path) -> None:
    """A pin_map response with hundreds of coords should be flagged."""
    # Create a response that looks like a full 17x17 lattice.
    full_pattern = []
    for r in range(17):
        for c in range(17):
            full_pattern.append([r, c, "fuel_pin"])
    raw = json.dumps({
        "patch_type": "pin_map",
        "lattice_size": [17, 17],
        "default_universe_id": "fuel_pin",
        "coordinate_convention": {"index_base": 0},
        "guide_tube_coords": full_pattern,  # 289 coords!
    })
    fake = FakePatchLLM([raw])

    from openmc_agent.plan_builder.patch_generator import generate_patch

    result = generate_patch(
        patch_type="pin_map",
        requirement="17x17 assembly",
        llm_client=fake,
        max_attempts=1,
    )
    # Check that the diagnostic was recorded.
    assert len(result.attempts) == 1
    attempt = result.attempts[0]
    assert attempt.contains_full_lattice_suspected is True
    error_codes = [i["code"] for i in attempt.issues if i.get("severity") == "error"]
    assert "patch_generation.pin_map_full_lattice_forbidden" in error_codes


# ---------------------------------------------------------------------------
# 8. Real LLM test skipped by default
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    not pytest.importorskip("os").environ.get("OPENMC_AGENT_RUN_REAL_LLM_TESTS"),
    reason="OPENMC_AGENT_RUN_REAL_LLM_TESTS not set",
)
class TestRealLLMEvaluation:
    """Real LLM tests — opt-in only."""

    def test_vera3_3b_real_llm(self, tmp_path: Path) -> None:
        """Run VERA3 3B evaluation with real LLM."""
        import os

        model = os.environ.get("OPENMC_AGENT_MODEL", "zhipu:glm-5.2")
        from openmc_agent.plan_builder.llm_adapter import make_patch_llm_client

        client = make_patch_llm_client(model_name=model)
        report, _ = run_incremental_evaluation(
            requirement=_VERA3_3B_REQ,
            benchmark_id="VERA3",
            selected_variant="3B",
            llm_client=client,
            model=model,
            max_patch_attempts=2,
            output_dir=tmp_path,
        )
        # Just check it runs without crashing; success depends on LLM quality.
        assert isinstance(report, EvaluationReport)
        assert report.no_monolithic_plan_requested is True


# ---------------------------------------------------------------------------
# 9. CLI dry-run
# ---------------------------------------------------------------------------


def test_cli_dry_run(tmp_path: Path) -> None:
    """The --dry-run flag should print patch order without calling LLM."""
    import subprocess
    import sys

    result = subprocess.run(
        [sys.executable, "scripts/evaluate_incremental_planning.py", "--dry-run",
         "--benchmark", "VERA3", "--variant", "3B"],
        capture_output=True,
        text=True,
        cwd=str(Path(__file__).resolve().parent.parent),
    )
    assert result.returncode == 0
    assert "Patch order:" in result.stdout
    assert "facts" in result.stdout
    assert "[Dry-run]" in result.stdout


# ---------------------------------------------------------------------------
# Evaluation failure report
# ---------------------------------------------------------------------------


def test_eval_failure_report(tmp_path: Path) -> None:
    """When LLM always fails, the report should record the failure."""
    bad_pin = json.dumps({
        "patch_type": "pin_map", "lattice_size": [17, 17],
        "default_universe_id": "fp",
        "coordinate_convention": {"index_base": 0},
        "guide_tube_coords": [[5, 5]],
        "pyrex_rod_coords": [[5, 5]],
    })
    responses = [
        json.dumps({"patch_type": "facts", "benchmark_id": "T",
                     "has_axial_geometry": True, "has_spacer_grids": True}),
        json.dumps({"patch_type": "materials", "materials": [
            {"material_id": "m", "name": "M", "role": "fuel", "density_g_cm3": 10.0}]}),
        json.dumps({"patch_type": "universes", "universes": [
            {"universe_id": "fp", "kind": "fuel_pin", "cells": [{"id": "c", "role": "fuel"}]}]}),
        bad_pin, bad_pin,
    ]
    fake = FakePatchLLM(responses)
    report, state = run_incremental_evaluation(
        requirement=_VERA3_3B_REQ,
        benchmark_id="T",  # short placeholder, won't trigger LLM matching
        selected_variant="3B",
        llm_client=fake,
        max_patch_attempts=2,
        output_dir=tmp_path,
    )
    assert report.ok is False
    assert report.error is not None
    # Earlier patches should have metrics.
    assert "facts" in report.patch_metrics
    assert "materials" in report.patch_metrics
