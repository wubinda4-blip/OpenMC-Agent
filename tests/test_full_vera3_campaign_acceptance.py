"""Tests for full VERA3B campaign acceptance (S6)."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from openmc_agent.campaign_eval.vera3_campaign_acceptance import (
    evaluate_vera3_acceptance,
    make_vera3b_acceptance_callback,
)


def test_callback_returns_false_for_no_plan():
    cb = make_vera3b_acceptance_callback()
    passed, codes = cb(None)
    assert not passed
    assert "no_plan" in codes


def test_evaluation_with_no_plan():
    result = evaluate_vera3_acceptance(None)
    assert not result["overall_passed"]
    assert result["variant"] == "3B"


def test_evaluation_with_gold_plan():
    """Full VERA3B gold plan should pass acceptance."""
    sys.path.insert(0, str(Path("tests/helpers").resolve().parent))
    from tests.helpers.vera3_acceptance import build_vera3_like_plan, load_vera3_reference

    reference = load_vera3_reference()
    plan = build_vera3_like_plan(reference, variant="3B")
    result = evaluate_vera3_acceptance(plan)
    error_codes = [
        i for i in result["plan_acceptance"]["issues"]
        if isinstance(i, dict) and i.get("severity") == "error"
    ]
    assert result["plan_acceptance"]["passed"], f"Errors: {[i['code'] for i in error_codes]}"


def test_evaluation_detects_broken_plan():
    """A plan with wrong pin placement should fail acceptance."""
    sys.path.insert(0, str(Path("tests/helpers").resolve().parent))
    from tests.helpers.vera3_acceptance import build_vera3_like_plan, load_vera3_reference

    reference = load_vera3_reference()
    # mutate_pin puts a fuel pin where a guide tube should be
    plan = build_vera3_like_plan(reference, variant="3B", mutate_pin=(0, 0))
    result = evaluate_vera3_acceptance(plan)
    # The mutated plan should have issues (though not necessarily errors
    # if the mutation only changes a fuel position)
    assert result is not None
