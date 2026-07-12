"""Tests for expert question grouping/dedup (P0-D5A Section 4)."""
from __future__ import annotations

import json

from openmc_agent.capability_blockers import classify_capability_blockers
from openmc_agent.expert_feedback import group_expert_questions
from openmc_agent.llm import normalize_capability_report
from openmc_agent.schemas import SimulationPlan


def _vera3b_plan() -> SimulationPlan:
    raw = json.loads(open("tests/fixtures/regressions/vera3b_pre_grid_repair_plan.json").read())
    normalize_capability_report(raw)
    return SimulationPlan.model_validate(raw)


def test_uo2_three_assumptions_merge_into_one_group() -> None:
    plan = _vera3b_plan()
    groups = group_expert_questions(plan, classify_capability_blockers(plan))
    uo2 = [g for g in groups if g.subject_id == "uo2_fuel_3B"]
    assert len(uo2) == 1
    assert len(uo2[0].source_items) == 3
    # All three UO2 assumptions are preserved in source_items.
    texts = "\n".join(uo2[0].source_items).lower()
    assert "enrichment" in texts
    assert "composition_status" in texts
    assert "o16" in texts or "stoichiometric" in texts


def test_borated_water_three_assumptions_merge_into_one_group() -> None:
    plan = _vera3b_plan()
    groups = group_expert_questions(plan, classify_capability_blockers(plan))
    bw = [g for g in groups if g.subject_id == "borated_water_3B"]
    assert len(bw) == 1
    assert len(bw[0].source_items) >= 3


def test_zircaloy_duplicates_merge_into_one_group() -> None:
    plan = _vera3b_plan()
    groups = group_expert_questions(plan, classify_capability_blockers(plan))
    zr = [g for g in groups if g.subject_id == "zircaloy4"]
    assert len(zr) == 1
    # The pure-Zr note + alloy_library substitution + composition_status + source.
    assert len(zr[0].source_items) >= 3


def test_grouping_preserves_all_source_items() -> None:
    """No assumption is dropped during grouping; every source_item is kept."""
    plan = _vera3b_plan()
    assumptions = list(plan.expert_assumptions)
    groups = group_expert_questions(plan, classify_capability_blockers(plan))
    grouped_items = [item for g in groups for item in g.source_items]
    assert len(grouped_items) == len(assumptions)
    # Every original assumption appears verbatim in some group.
    for original in assumptions:
        assert original in grouped_items


def test_no_eight_duplicate_questions() -> None:
    """VERA3B no longer surfaces 8 near-duplicate material questions.

    The 23 raw assumptions collapse into a small number of per-material groups,
    each with a single concise prompt.
    """
    plan = _vera3b_plan()
    groups = group_expert_questions(plan, classify_capability_blockers(plan))
    assert len(groups) <= 10
    # Each group is a single confirmable prompt (not a wall of duplicates).
    prompts = [g.prompt for g in groups]
    assert all(p for p in prompts)
    assert len(prompts) == len(set(prompts))


def test_grouping_is_deterministic_and_llm_free() -> None:
    plan = _vera3b_plan()
    summary = classify_capability_blockers(plan)
    g1 = group_expert_questions(plan, summary)
    g2 = group_expert_questions(plan, summary)
    assert [g.model_dump() for g in g1] == [g.model_dump() for g in g2]


def test_grouping_without_plan_uses_raw_assumptions() -> None:
    """The grouper works from a bare assumption list (no plan context)."""
    groups = group_expert_questions(
        None,
        assumptions=[
            "material fuel: composition_status=approximate",
            "enrichment is approximate",
            "boron concentration is approximate",
        ],
    )
    assert len(groups) >= 1
    assert all(g.source_items for g in groups)
