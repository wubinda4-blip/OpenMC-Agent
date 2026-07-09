"""Tests for the requirement reference resolver."""

from __future__ import annotations

from pathlib import Path

import pytest

from openmc_agent.requirement_resolver import (
    ResolvedRequirement,
    resolve_requirement_references,
    resolved_requirement_summary,
)


def test_resolves_local_md_file(tmp_path: Path) -> None:
    problem = tmp_path / "problem.md"
    problem.write_text("17x17 guide tubes spacer grids axial layers\n")
    requirement = f"Build the model described in {problem}."

    resolved = resolve_requirement_references(requirement, base_dir=tmp_path)

    assert resolved.original_requirement == requirement
    assert "17x17" in resolved.resolved_requirement
    assert "guide tubes" in resolved.resolved_requirement
    assert "spacer grids" in resolved.resolved_requirement
    assert str(problem) in resolved.referenced_files or problem.name in str(
        resolved.referenced_files
    )


def test_resolves_relative_path(tmp_path: Path) -> None:
    (tmp_path / "Input").mkdir()
    problem = tmp_path / "Input" / "VERA3_problem.md"
    problem.write_text("content with 17x17 lattice\n")
    requirement = "Build the model described in Input/VERA3_problem.md."

    resolved = resolve_requirement_references(requirement, base_dir=tmp_path)

    assert "Input/VERA3_problem.md" in resolved.referenced_files
    assert "17x17" in resolved.resolved_requirement


def test_missing_file_warning(tmp_path: Path) -> None:
    requirement = "Build model described in nonexistent_file.md."
    resolved = resolve_requirement_references(requirement, base_dir=tmp_path)

    assert resolved.referenced_files == []
    assert any("file_not_found" in w for w in resolved.warnings)
    # No exception; resolved text equals original.
    assert resolved.resolved_requirement == requirement


def test_does_not_read_url(tmp_path: Path) -> None:
    requirement = "Check https://example.com/problem.md for details."
    resolved = resolve_requirement_references(requirement, base_dir=tmp_path)

    assert resolved.referenced_files == []
    # The URL is not read; no URL content appears in resolved text.
    assert "example.com" not in resolved.resolved_requirement or requirement in resolved.resolved_requirement


def test_suffix_not_allowed(tmp_path: Path) -> None:
    (tmp_path / "script.py").write_text("print('hello')\n")
    requirement = "Run script.py for details."
    resolved = resolve_requirement_references(requirement, base_dir=tmp_path)

    assert resolved.referenced_files == []
    assert any("suffix_not_allowed" in w for w in resolved.warnings)


def test_truncates_large_file(tmp_path: Path) -> None:
    big = tmp_path / "big.md"
    big.write_text("A" * 5000 + "\nEND_MARKER\n")
    resolved = resolve_requirement_references(
        f"See {big}.", base_dir=tmp_path, max_file_chars=100,
    )
    assert str(big) in resolved.referenced_files or big.name in str(resolved.referenced_files)
    assert any("file_truncated" in w for w in resolved.warnings)
    # The truncated content should NOT contain the end marker.
    assert "END_MARKER" not in resolved.resolved_requirement


def test_reads_txt_and_json(tmp_path: Path) -> None:
    (tmp_path / "note.txt").write_text("some text content\n")
    (tmp_path / "data.json").write_text('{"key": "value"}\n')
    requirement = "See note.txt and data.json."
    resolved = resolve_requirement_references(requirement, base_dir=tmp_path)

    assert "note.txt" in resolved.referenced_files
    assert "data.json" in resolved.referenced_files
    assert "some text content" in resolved.resolved_requirement
    assert "value" in resolved.resolved_requirement


def test_multiple_references(tmp_path: Path) -> None:
    (tmp_path / "a.md").write_text("content A\n")
    (tmp_path / "b.md").write_text("content B\n")
    requirement = "See a.md and also b.md."
    resolved = resolve_requirement_references(requirement, base_dir=tmp_path)

    assert set(resolved.referenced_files) == {"a.md", "b.md"}
    assert "content A" in resolved.resolved_requirement
    assert "content B" in resolved.resolved_requirement


def test_summary_is_compact(tmp_path: Path) -> None:
    problem = tmp_path / "problem.md"
    problem.write_text("17x17\n")
    resolved = resolve_requirement_references(
        f"See {problem}.", base_dir=tmp_path,
    )
    summary = resolved_requirement_summary(resolved)
    assert "original_requirement_chars" in summary
    assert "resolved_requirement_chars" in summary
    assert "referenced_files" in summary
    assert "requirement_resolution_warnings" in summary
    # The full resolved text is NOT in the summary (to keep traces small).
    assert "17x17" not in str(summary)


def test_empty_requirement() -> None:
    resolved = resolve_requirement_references("")
    assert resolved.resolved_requirement == ""
    assert resolved.referenced_files == []


def test_resolved_requirement_model_fields() -> None:
    r = ResolvedRequirement(
        original_requirement="x",
        resolved_requirement="x",
        referenced_files=[],
        file_excerpt_by_path={},
        warnings=[],
    )
    assert r.original_requirement == "x"


def test_feature_detection_sees_resolved_content(tmp_path: Path) -> None:
    """Integration: feature detection on resolved requirement detects structural signals."""
    from openmc_agent.plan_builder.mode import should_use_incremental_planning

    problem = tmp_path / "Input" / "VERA3_problem.md"
    problem.parent.mkdir()
    problem.write_text(
        "17x17 lattice with guide tubes, instrument tube, "
        "spacer grids, and axial layers.\n"
    )
    original = "Build the VERA3 3A model described in Input/VERA3_problem.md."
    resolved = resolve_requirement_references(original, base_dir=tmp_path)

    # Original requirement has no structural keywords.
    d_orig = should_use_incremental_planning(original)
    assert not d_orig.feature_summary.get("has_special_pin_map")
    assert not d_orig.feature_summary.get("has_spacer_grid")

    # Resolved requirement does.
    d_resolved = should_use_incremental_planning(resolved.resolved_requirement)
    assert d_resolved.feature_summary.get("has_special_pin_map")
    assert d_resolved.feature_summary.get("has_spacer_grid")
    assert d_resolved.mode == "incremental"


def test_required_patch_types_include_pin_map_after_resolution(tmp_path: Path) -> None:
    """Integration: after resolution, required_patch_types includes pin_map."""
    from openmc_agent.plan_builder.executor import required_patch_types_for_state
    from openmc_agent.plan_builder.mode import should_use_incremental_planning
    from openmc_agent.plan_builder.state import initialize_plan_build_state

    problem = tmp_path / "Input" / "VERA3_problem.md"
    problem.parent.mkdir()
    problem.write_text(
        "17x17 lattice with guide tubes, instrument tube, spacer grids.\n"
    )
    original = "Build the VERA3 3A model described in Input/VERA3_problem.md."
    resolved = resolve_requirement_references(original, base_dir=tmp_path)
    decision = should_use_incremental_planning(resolved.resolved_requirement)
    state = initialize_plan_build_state(resolved.resolved_requirement, decision)

    required = required_patch_types_for_state(state)
    assert "pin_map" in required
    assert "axial_layers" in required
    assert "settings" in required
