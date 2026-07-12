"""Tests for the inspect CLI skeleton outcome display (P0-D5A Section 9)."""
from __future__ import annotations

from pathlib import Path

from openmc_agent.inspect import InspectResult, _format_compact_summary


def _result(ok: bool, **data) -> InspectResult:
    transcript_data = {
        "ok": ok,
        "validation_report": {"is_valid": True},
        "capability_report": data.get("capability_report", {"renderability": "skeleton", "supported_renderer": "assembly"}),
        "render_outcome": {"lines": ["Generated model.py skeleton", "Status: NOT EXECUTABLE"]},
        "retry_count": 0,
        "workflow_outcome": data.get("workflow_outcome", {}),
        "capability_blocker_summary": data.get("capability_blocker_summary", {}),
        "expert_feedback_decision": data.get("expert_feedback_decision", {}),
        "model_path": "data/runs/x/model.py",
    }
    return InspectResult(
        ok=ok,
        transcript="...",
        transcript_data=transcript_data,
        model_path=Path("data/runs/x/model.py"),
    )


def test_skeleton_shows_blocked_review_only_not_fail() -> None:
    """A skeleton outcome displays BLOCKED_REVIEW_ONLY, not a vague FAIL."""
    result = _result(
        ok=False,
        workflow_outcome={"status": "blocked_review_only", "reason_codes": ["lattice_transform.replacement_universe_missing"]},
        capability_blocker_summary={"primary_blocker_codes": ["lattice_transform.replacement_universe_missing"]},
        capability_report={"renderability": "skeleton", "supported_renderer": "assembly"},
    )
    summary = _format_compact_summary(result, Path("data/runs/x"))
    assert "BLOCKED_REVIEW_ONLY" in summary
    assert "FAIL" not in summary
    assert "lattice_transform.replacement_universe_missing" in summary


def test_skeleton_internal_ok_still_false() -> None:
    """The internal ok flag stays False so CI never treats a skeleton as success."""
    result = _result(
        ok=False,
        workflow_outcome={"status": "blocked_review_only"},
        capability_report={"renderability": "skeleton", "supported_renderer": "assembly"},
    )
    assert result.ok is False


def test_blocked_review_only_notes_openmc_not_attempted() -> None:
    result = _result(
        ok=False,
        workflow_outcome={"status": "blocked_review_only"},
        capability_report={"renderability": "skeleton", "supported_renderer": "assembly"},
    )
    summary = _format_compact_summary(result, Path("data/runs/x"))
    assert "OpenMC execution: not attempted" in summary


def test_pass_shows_pass() -> None:
    result = _result(
        ok=True,
        workflow_outcome={"status": "ok"},
        capability_report={"renderability": "runnable", "supported_renderer": "assembly"},
    )
    summary = _format_compact_summary(result, Path("data/runs/x"))
    assert "PASS" in summary


def test_expert_decision_shown_in_blocked_summary() -> None:
    result = _result(
        ok=False,
        workflow_outcome={"status": "blocked_review_only"},
        expert_feedback_decision={"action": "accept_review_only"},
        capability_report={"renderability": "skeleton", "supported_renderer": "assembly"},
    )
    summary = _format_compact_summary(result, Path("data/runs/x"))
    assert "accept_review_only" in summary
