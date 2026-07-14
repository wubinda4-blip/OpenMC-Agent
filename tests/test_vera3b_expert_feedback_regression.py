"""VERA3B expert-feedback regression tests (P0-D5A Section 1, 14).

Freezes the real failure mode so the expert-feedback semantics never regress:
the real blocker (axial materialization) must be surfaced, material assumptions
must be grouped (not 8 duplicates), empty input must not produce a vague
continue, and the skeleton outcome must be BLOCKED_REVIEW_ONLY.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from openmc_agent.capability_blockers import classify_capability_blockers
from openmc_agent.expert_feedback import (
    group_expert_questions,
    interpret_empty_feedback,
)
from openmc_agent.llm import normalize_capability_report
from openmc_agent.schemas import SimulationPlan

FIXTURE = Path("tests/fixtures/regressions/vera3b_empty_expert_feedback_skeleton.json")
DIAGNOSIS = Path("data/runs/VERA_3B/expert_feedback_failure_diagnosis.json")
PLAN_FIXTURE = Path("tests/fixtures/regressions/vera3b_pre_grid_repair_plan.json")


@pytest.fixture(scope="module")
def vera3b_plan() -> SimulationPlan:
    """Load the frozen pre-repair VERA3B plan with the grid_cell loading issue.

    The mutable ``data/runs/VERA_3B/simulation_plan.json`` was repaired by
    P0-D5B grid migration.  This frozen fixture preserves the pre-repair
    structural state so the regression tests remain stable.
    """
    raw = json.loads(PLAN_FIXTURE.read_text())
    normalize_capability_report(raw)
    return SimulationPlan.model_validate(raw)


def test_fixture_exists_and_records_real_blocker() -> None:
    assert FIXTURE.exists(), "regression fixture must be committed"
    fixture = json.loads(FIXTURE.read_text())
    assert "lattice_transform.replacement_universe_missing" in fixture["real_blocking_issue_codes"]
    assert fixture["structural_issue_not_visible_to_validate_plan"] is True
    assert fixture["legacy_final_status"] == "FAIL"
    assert fixture["legacy_empty_input_expert_feedback_action"] == "continue"


@pytest.mark.skipif(
    not DIAGNOSIS.exists(),
    reason="diagnosis JSON is a local run artifact (data/runs/VERA_3B is gitignored)",
)
def test_diagnosis_records_supervisor_and_empty_input_state() -> None:
    assert DIAGNOSIS.exists(), "diagnosis JSON must be generated"
    diag = json.loads(DIAGNOSIS.read_text())
    assert diag["run_supervisor"]["action"] == "request_human_confirmation"
    assert diag["empty_input_state"]["expert_feedback_action"] == "continue"
    assert diag["empty_input_state"]["pending_questions_retained"] is True
    assert "lattice_transform.replacement_universe_missing" in diag["real_blocking_issue_codes"]


def test_real_primary_blocker_code_in_summary(vera3b_plan: SimulationPlan) -> None:
    summary = classify_capability_blockers(vera3b_plan)
    assert "lattice_transform.replacement_universe_missing" in summary.primary_blocker_codes
    assert summary.has_blocking_issue


def test_no_eight_duplicate_questions(vera3b_plan: SimulationPlan) -> None:
    """The fixture's 23 raw assumptions must collapse to a small number of
    per-material groups, never the legacy 8-truncated duplicate wall."""
    summary = classify_capability_blockers(vera3b_plan)
    groups = group_expert_questions(vera3b_plan, summary)
    fixture = json.loads(FIXTURE.read_text())
    assert fixture["expected"]["no_duplicate_question_wall"] is True
    assert len(groups) == fixture["expected"]["expert_question_group_count"]
    # No two groups share the same prompt (no duplicate questions).
    prompts = [g.prompt for g in groups]
    assert len(prompts) == len(set(prompts))


def test_empty_input_does_not_produce_vague_continue(vera3b_plan: SimulationPlan) -> None:
    """Empty input on the VERA3B skeleton -> accept_review_only, not the legacy
    vague 'continue' that retained pending questions and rendered anyway."""
    summary = classify_capability_blockers(vera3b_plan)
    decision = interpret_empty_feedback(
        renderability=summary.renderability,
        has_blocking_issue=summary.has_blocking_issue,
    )
    assert decision.action == "accept_review_only"
    assert decision.action != "continue"


def test_structural_blocker_visible_at_assess_time(vera3b_plan: SimulationPlan) -> None:
    """Lattice-loading structural blockers are visible at validate_plan time via
    the shared validator, and the defensive probe does not duplicate them."""
    from openmc_agent.graph import _probe_axial_materialization_blockers
    from openmc_agent.lattice_loading_validation import lattice_loading_structural_issues

    model = vera3b_plan.complex_model
    assert model is not None

    shared_issues = lattice_loading_structural_issues(model)
    shared_codes = {i.code for i in shared_issues}
    assert "lattice_transform.replacement_universe_missing" in shared_codes
    assert "renderer.axial_loading_materialization_failed" in shared_codes
    assert all(i.severity == "error" for i in shared_issues)

    probe_issues = _probe_axial_materialization_blockers(vera3b_plan)
    probe_codes = {i.code for i in probe_issues}
    assert probe_codes.isdisjoint(shared_codes), (
        "defensive probe must not duplicate issues already caught by the "
        "shared validator"
    )


def test_blocking_issue_codes_are_structural_not_material(vera3b_plan: SimulationPlan) -> None:
    """The real blocker is a structural plan defect, not a material-fact gap."""
    from openmc_agent.capability_blockers import is_structural_blocker_code

    summary = classify_capability_blockers(vera3b_plan)
    assert summary.structural_agent_fixable
    assert all(
        is_structural_blocker_code(i.code) for i in summary.structural_agent_fixable
    )
    # Material assumptions are recorded but are NOT the blocker.
    assert summary.material_assumptions
    assert not any(
        is_structural_blocker_code(c) for c in ("enrichment", "composition_status")
    )
