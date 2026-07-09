"""Tests for scripts/diff_evaluation_reports.py."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

SCRIPT_PATH = REPO_ROOT / "scripts" / "diff_evaluation_reports.py"


def _import_main():
    import importlib.util

    spec = importlib.util.spec_from_file_location("diff_evaluation_reports", SCRIPT_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _make_report(
    *,
    pass_rate: float = 1.0,
    plan_schema_success_rate: float | None = 1.0,
    artifact_completeness_rate: float | None = 1.0,
    planning_mode_accuracy: float | None = 1.0,
    cases: list[dict] | None = None,
) -> dict:
    if cases is None:
        cases = [
            {"case_id": "a", "passed": True},
            {"case_id": "b", "passed": True},
        ]
    pass_count = sum(1 for c in cases if c.get("passed"))
    return {
        "run_id": "test",
        "model": "fake",
        "case_count": len(cases),
        "metrics": {
            "case_count": len(cases),
            "pass_count": pass_count,
            "fail_count": len(cases) - pass_count,
            "pass_rate": pass_rate,
            "plan_schema_success_rate": plan_schema_success_rate,
            "incremental_patch_success_rate": 1.0,
            "artifact_completeness_rate": artifact_completeness_rate,
            "planning_mode_accuracy": planning_mode_accuracy,
            "issue_code_precision": 1.0,
            "issue_code_recall": 1.0,
        },
        "cases": cases,
    }


def test_diff_detects_metric_deltas() -> None:
    module = _import_main()
    base = _make_report(pass_rate=1.0)
    head = _make_report(pass_rate=0.5, plan_schema_success_rate=0.8)
    diff = module.build_diff(base, head)
    metric_map = {m["metric"]: m for m in diff["metric_changes"]}
    assert metric_map["pass_rate"]["delta"] == pytest.approx(-0.5)
    assert metric_map["plan_schema_success_rate"]["delta"] == pytest.approx(-0.2)


def test_diff_detects_new_failed_case() -> None:
    module = _import_main()
    base = _make_report(cases=[
        {"case_id": "a", "passed": True},
        {"case_id": "b", "passed": True},
    ])
    head = _make_report(cases=[
        {"case_id": "a", "passed": True},
        {"case_id": "b", "passed": False, "failed_stage": "validate_plan",
         "failed_patch_type": None, "issue_codes": ["schema.invalid"],
         "failure_reasons": ["bad"]},
    ], pass_rate=0.5)
    diff = module.build_diff(base, head)
    assert len(diff["new_failures"]) == 1
    assert diff["new_failures"][0]["case_id"] == "b"
    assert diff["new_failures"][0]["failed_stage"] == "validate_plan"


def test_diff_detects_fixed_case() -> None:
    module = _import_main()
    base = _make_report(cases=[
        {"case_id": "a", "passed": False, "failed_stage": "render",
         "failed_patch_type": "pin_map"},
        {"case_id": "b", "passed": True},
    ], pass_rate=0.5)
    head = _make_report(cases=[
        {"case_id": "a", "passed": True},
        {"case_id": "b", "passed": True},
    ])
    diff = module.build_diff(base, head)
    assert len(diff["fixed_cases"]) == 1
    assert diff["fixed_cases"][0]["case_id"] == "a"
    assert diff["fixed_cases"][0]["previous_failure"]["failed_stage"] == "render"


def test_diff_no_changes_when_identical() -> None:
    module = _import_main()
    report = _make_report()
    diff = module.build_diff(report, report)
    # All deltas are 0.
    for m in diff["metric_changes"]:
        if m["delta"] is not None:
            assert m["delta"] == 0.0
    assert diff["new_failures"] == []
    assert diff["fixed_cases"] == []
    assert diff["case_status_changes"] == []


def test_fail_on_regression_exits_nonzero(tmp_path: Path) -> None:
    module = _import_main()
    base = _make_report(pass_rate=1.0)
    head = _make_report(pass_rate=0.5)
    base_path = tmp_path / "base.json"
    head_path = tmp_path / "head.json"
    base_path.write_text(json.dumps(base))
    head_path.write_text(json.dumps(head))
    rc = module.main([
        "--base", str(base_path),
        "--head", str(head_path),
        "--fail-on-regression",
    ])
    assert rc == 1


def test_no_regression_exits_zero(tmp_path: Path) -> None:
    module = _import_main()
    report = _make_report(pass_rate=1.0)
    base_path = tmp_path / "base.json"
    head_path = tmp_path / "head.json"
    base_path.write_text(json.dumps(report))
    head_path.write_text(json.dumps(report))
    rc = module.main([
        "--base", str(base_path),
        "--head", str(head_path),
        "--fail-on-regression",
    ])
    assert rc == 0


def test_allow_new_failures_flag(tmp_path: Path) -> None:
    """New failures alone don't fail if --allow-new-failures is set and metrics hold."""
    module = _import_main()
    base = _make_report(cases=[
        {"case_id": "a", "passed": True},
        {"case_id": "b", "passed": True},
    ])
    # Head has a new failure but pass_rate metric unchanged (simulated).
    head = _make_report(cases=[
        {"case_id": "a", "passed": True},
        {"case_id": "b", "passed": False, "failed_stage": "validate_plan",
         "issue_codes": ["x"]},
    ])
    # Keep pass_rate at 1.0 in metrics (inconsistent with cases, but tests the flag).
    head["metrics"]["pass_rate"] = 1.0
    base_path = tmp_path / "base.json"
    head_path = tmp_path / "head.json"
    base_path.write_text(json.dumps(base))
    head_path.write_text(json.dumps(head))
    rc = module.main([
        "--base", str(base_path),
        "--head", str(head_path),
        "--fail-on-regression",
        "--allow-new-failures",
    ])
    assert rc == 0


def test_writes_markdown_out(tmp_path: Path) -> None:
    module = _import_main()
    base = _make_report(pass_rate=1.0)
    head = _make_report(pass_rate=0.9)
    base_path = tmp_path / "base.json"
    head_path = tmp_path / "head.json"
    out_path = tmp_path / "diff.md"
    base_path.write_text(json.dumps(base))
    head_path.write_text(json.dumps(head))
    rc = module.main([
        "--base", str(base_path),
        "--head", str(head_path),
        "--out", str(out_path),
    ])
    assert rc == 0
    assert out_path.exists()
    content = out_path.read_text()
    assert "# Evaluation Report Diff" in content
    assert "pass_rate" in content


def test_threshold_allows_small_delta(tmp_path: Path) -> None:
    """A small pass_rate decrease within threshold does not fail."""
    module = _import_main()
    base = _make_report(pass_rate=1.0)
    head = _make_report(pass_rate=0.95)
    base_path = tmp_path / "base.json"
    head_path = tmp_path / "head.json"
    base_path.write_text(json.dumps(base))
    head_path.write_text(json.dumps(head))
    # Allow up to 10pp decrease.
    rc = module.main([
        "--base", str(base_path),
        "--head", str(head_path),
        "--fail-on-regression",
        "--min-pass-rate-delta", "0.10",
    ])
    assert rc == 0
