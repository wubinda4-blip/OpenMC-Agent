"""Real campaign gate barrier tests.

Validates that the Final Gate accepted-before-render rule is enforced
truthfully: no render / export / smoke tool calls occur before the
Assembled Plan Gate is accepted.
"""

from openmc_agent.real_campaign import RealCampaignRunResult
from openmc_agent.real_campaign_harness import (
    validate_real_canary_truthfulness,
)


def _result(**kwargs) -> RealCampaignRunResult:
    base = dict(
        run_id="r1", status="completed", final_disposition="UNKNOWN",
        started_at="", completed_at="", duration_s=0.0,
        git_sha="", input_sha="", configuration_hash="",
        provider="deepseek", model="deepseek:deepseek-chat",
        real_llm_verified=True, real_openmc_verified=False,
        llm_call_count=1,
    )
    base.update(kwargs)
    return RealCampaignRunResult(**base)


def test_render_before_final_gate_accepted_is_a_violation():
    result = _result(
        five_gate_accepted=False,
        geometry_debug_passed=True,
    )
    ws = {"tool_results": [{"name": "render_plan", "ok": True}]}
    violations = validate_real_canary_truthfulness(
        result, ws, expected_fragmented_universes=False,
    )
    assert "render_before_final_gate_accepted" in violations


def test_export_before_final_gate_accepted_is_a_violation():
    result = _result(
        five_gate_accepted=False,
        xml_exported=True,
    )
    ws = {"tool_results": [{"name": "export_xml", "ok": True}]}
    violations = validate_real_canary_truthfulness(
        result, ws, expected_fragmented_universes=False,
    )
    assert "export_before_final_gate_accepted" in violations


def test_smoke_before_final_gate_accepted_is_a_violation():
    result = _result(
        five_gate_accepted=False,
        smoke_passed=True,
    )
    ws = {"tool_results": [{"name": "run_smoke_test", "ok": True}]}
    violations = validate_real_canary_truthfulness(
        result, ws, expected_fragmented_universes=False,
    )
    assert "smoke_before_final_gate_accepted" in violations


def test_no_render_violation_when_final_gate_accepted():
    result = _result(
        five_gate_accepted=True,
        xml_exported=True,
        smoke_passed=True,
        geometry_debug_passed=True,
    )
    ws = {
        "plan_build_state": {
            "plan_loop_stages": {
                "assembled_plan": {
                    "gate_id": "assembled_plan",
                    "status": "accepted",
                    "review_count": 1,
                },
            },
        },
        "tool_results": [
            {"name": "render_plan", "ok": True},
            {"name": "export_xml", "ok": True},
            {"name": "run_smoke_test", "ok": True},
        ],
    }
    violations = validate_real_canary_truthfulness(
        result, ws, expected_fragmented_universes=False,
    )
    assert "render_before_final_gate_accepted" not in violations
    assert "export_before_final_gate_accepted" not in violations
    assert "smoke_before_final_gate_accepted" not in violations


def test_blocked_gate_not_silently_passed():
    """A blocked gate must not be treatable as 'accepted'."""
    from openmc_agent.real_campaign_harness import FiveGateStatusSnapshot, extract_five_gate_status
    state = {
        "plan_loop_stages": {
            "facts": {"gate_id": "facts", "status": "accepted", "review_count": 1, "metadata": {}},
            "material_universe": {"gate_id": "material_universe", "status": "accepted", "review_count": 1, "metadata": {}},
            "placement": {"gate_id": "placement", "status": "accepted", "review_count": 1, "metadata": {}},
            "axial_geometry": {"gate_id": "axial_geometry", "status": "accepted", "review_count": 1, "metadata": {}},
            "assembled_plan": {"gate_id": "assembled_plan", "status": "blocked", "review_count": 1, "metadata": {}},
        }
    }
    snapshot = extract_five_gate_status(state)
    assert snapshot.all_accepted is False
    assert snapshot.blocked_gate == "assembled_plan"
