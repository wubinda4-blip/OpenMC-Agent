import json
import subprocess
import sys
from pathlib import Path


def _write_cases(path: Path) -> Path:
    path.write_text(
        json.dumps(
            [
                {
                    "case_id": "cli-pin-cell",
                    "category": "pin_cell",
                    "user_request": "Build a simple pin cell.",
                    "expected_issue_codes": [],
                    "expected_renderability": "runnable",
                    "expected_supported_renderer": "pin_cell",
                    "should_trigger_retrieval": False,
                    "should_require_human_confirmation": False,
                    "expected_planning_mode": "monolithic",
                    "expected_plan_schema_success": True,
                    "expected_artifact_complete": True,
                    "expected_artifact_keys": ["workflow_trace", "capability_report"],
                }
            ]
        ),
        encoding="utf-8",
    )
    return path


def test_cli_dry_fake_run_succeeds(tmp_path):
    cases_path = _write_cases(tmp_path / "cases.json")
    out = tmp_path / "workflow_out"

    completed = subprocess.run(
        [
            sys.executable,
            "scripts/run_workflow_benchmark.py",
            "--cases",
            str(cases_path),
            "--model",
            "fake",
            "--mode",
            "plan-only",
            "--max-cases",
            "1",
            "--out",
            str(out),
        ],
        cwd=Path(__file__).resolve().parents[1],
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    assert (out / "evaluation_report.json").exists()
    assert (out / "benchmark_summary.md").exists()
    assert "Wrote evaluation report" in completed.stdout


def test_cli_refuses_real_model_without_allow_flag(tmp_path):
    cases_path = _write_cases(tmp_path / "cases.json")

    completed = subprocess.run(
        [
            sys.executable,
            "scripts/run_workflow_benchmark.py",
            "--cases",
            str(cases_path),
            "--model",
            "deepseek:deepseek-chat",
            "--mode",
            "plan-only",
            "--max-cases",
            "1",
            "--out",
            str(tmp_path / "workflow_out"),
        ],
        cwd=Path(__file__).resolve().parents[1],
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode != 0
    assert "--allow-real-llm" in completed.stderr
