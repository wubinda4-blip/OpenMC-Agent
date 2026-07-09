"""Tests for the compare_material_policies.py CLI (no OpenMC required)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


SCRIPT_PATH = REPO_ROOT / "scripts" / "compare_material_policies.py"


def _import_main():
    import importlib.util

    spec = importlib.util.spec_from_file_location("compare_material_policies", SCRIPT_PATH)
    assert spec and spec.loader, "could not load compare_material_policies module"
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _make_input_file(tmp_path: Path) -> Path:
    p = tmp_path / "problem.md"
    p.write_text(
        "# VERA3 problem description\n"
        "17x17 lattice with guide tubes, instrument tube, spacer grids, "
        "and axial layers.\n"
    )
    return p


def test_dry_run_exits_zero_without_openmc(tmp_path: Path) -> None:
    module = _import_main()
    input_path = _make_input_file(tmp_path)
    out_dir = tmp_path / "out"
    argv = [
        "--benchmark", "VERA3",
        "--variant", "3A",
        "--input", str(input_path),
        "--model", "fake",
        "--reference-patch-policy", "off",
        "--dry-run",
        "--out", str(out_dir),
    ]
    rc = module.main(argv)
    assert rc == 0
    report_path = out_dir / "comparison_report.json"
    assert report_path.exists()


def test_dry_run_report_structure(tmp_path: Path) -> None:
    module = _import_main()
    input_path = _make_input_file(tmp_path)
    out_dir = tmp_path / "out"
    argv = [
        "--benchmark", "VERA3",
        "--variant", "3A",
        "--input", str(input_path),
        "--model", "fake",
        "--dry-run",
        "--out", str(out_dir),
    ]
    rc = module.main(argv)
    assert rc == 0

    report = json.loads((out_dir / "comparison_report.json").read_text())
    assert report["benchmark"] == "VERA3"
    assert report["variant"] == "3A"
    assert set(report["cases"].keys()) == {"preserve_plan", "apply_alloy_library"}
    for policy_name, case in report["cases"].items():
        assert case["policy"] == policy_name
        assert case["ok"] is True
        # keff is None in dry-run.
        assert case["keff"] is None
    # delta_pcm is None because keff values are None.
    assert report["delta_pcm"] is None
    # Notes include the smoke-level disclaimer.
    assert any("not benchmark agreement" in note.lower() for note in report["notes"])


def test_dry_run_writes_per_policy_subdirs(tmp_path: Path) -> None:
    module = _import_main()
    input_path = _make_input_file(tmp_path)
    out_dir = tmp_path / "out"
    argv = [
        "--benchmark", "VERA3",
        "--variant", "3B",
        "--input", str(input_path),
        "--model", "fake",
        "--dry-run",
        "--out", str(out_dir),
    ]
    rc = module.main(argv)
    assert rc == 0
    assert (out_dir / "preserve_plan").is_dir()
    assert (out_dir / "apply_alloy_library").is_dir()


def test_dry_run_resolves_requirement_references(tmp_path: Path) -> None:
    """Dry-run should inline the input file content and detect structural features."""
    module = _import_main()
    input_path = _make_input_file(tmp_path)
    out_dir = tmp_path / "out"
    argv = [
        "--benchmark", "VERA3",
        "--variant", "3A",
        "--input", str(input_path),
        "--model", "fake",
        "--dry-run",
        "--out", str(out_dir),
    ]
    rc = module.main(argv)
    assert rc == 0

    report = json.loads((out_dir / "comparison_report.json").read_text())
    al = report["cases"]["apply_alloy_library"]
    # Requirement resolution metadata is recorded.
    req_res = al.get("requirement_resolution", {})
    assert req_res.get("referenced_files"), "expected at least one referenced file"
    assert req_res.get("resolved_requirement_chars", 0) > req_res.get(
        "original_requirement_chars", 0
    ), "resolved requirement should be larger than original"
    # Feature detection sees the inlined content.
    fs = al.get("feature_summary", {})
    assert fs.get("has_special_pin_map") is True, "guide tube should be detected"
    assert fs.get("has_spacer_grid") is True, "spacer grid should be detected"
    # pin_map and axial_overlays entered the task order.
    task_order = al.get("task_order", [])
    assert "pin_map" in task_order, f"pin_map missing from {task_order}"
    assert "axial_overlays" in task_order, f"axial_overlays missing from {task_order}"


def test_refuses_real_model_without_allow_flag(tmp_path: Path) -> None:
    module = _import_main()
    input_path = _make_input_file(tmp_path)
    argv = [
        "--benchmark", "VERA3",
        "--variant", "3A",
        "--input", str(input_path),
        "--model", "deepseek:deepseek-chat",
        "--dry-run",
        "--out", str(tmp_path / "out"),
    ]
    rc = module.main(argv)
    assert rc == 2


def test_require_openmc_flag_fails_when_openmc_absent(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    module = _import_main()
    input_path = _make_input_file(tmp_path)
    # Force _openmc_available to return False.
    monkeypatch.setattr(module, "_openmc_available", lambda: False)
    argv = [
        "--benchmark", "VERA3",
        "--variant", "3A",
        "--input", str(input_path),
        "--model", "fake",
        "--require-openmc",
        "--dry-run",
        "--out", str(tmp_path / "out"),
    ]
    rc = module.main(argv)
    assert rc == 3
