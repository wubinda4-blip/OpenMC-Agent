from __future__ import annotations

import pytest

from tests.test_pin_map_repair_diagnosis import _state_and_mismatch


def test_deterministic_pin_map_repair_skips_llm_and_global_retry(tmp_path) -> None:
    pytest.importorskip("openmc")
    from openmc_agent.graph import _make_validate_plan_node, _try_incremental_validation_patch_repair

    state, plan, report, _replacement_id = _state_and_mismatch()

    class NeverCalled:
        def generate_patch_json(self, **_kwargs):
            raise AssertionError("deterministic pin-map repair must not call the LLM")

    repaired, evaluation, meta = _try_incremental_validation_patch_repair(
        state={
            "plan_build_state": state.model_dump(mode="json"),
            "simulation_plan": plan,
            "output_dir": str(tmp_path),
            "requirement": "generic assembly",
        },
        report=report,
        target_patch_types=["pin_map"],
        llm_client=NeverCalled(),
    )
    assert repaired is not None
    assert evaluation is not None and evaluation.accepted
    assert meta["strategy"] == "deterministic_pin_map_count_repair"
    assert (tmp_path / "validation_repair" / "pin_map_diagnosis_0.json").exists()
    assert (tmp_path / "validation_repair" / "pin_map_candidate_preview_0.json").exists()

    updates = _make_validate_plan_node(2)(
        {
            "simulation_plan": plan,
            "requirement": "generic assembly",
            "retry_count": 0,
            "plan_build_state": state.model_dump(mode="json"),
            "incremental_execution_result": {
                "planning_mode": "incremental",
                "monolithic_reflect_plan_allowed": False,
            },
        }
    )
    # The normal node may construct a real client only when the oracle has no
    # proof. Here the assembler plan already validates after the precedence fix.
    assert updates["retry_count"] == 0


def test_ambiguous_pin_map_context_is_sent_to_llm(tmp_path) -> None:
    pytest.importorskip("openmc")
    from openmc_agent.graph import _try_incremental_validation_patch_repair

    state, plan, report, _replacement_id = _state_and_mismatch()
    plan = plan.model_copy(deep=True)
    plan.complex_model.lattices[0].universe_pattern[0][0] = "guide_tube"
    state.assembled_plan = plan.model_dump(mode="json")

    class CapturingClient:
        request = None
        def generate_patch_json(self, **kwargs):
            self.request = kwargs
            return {"operations": []}

    client = CapturingClient()
    _repaired, evaluation, _meta = _try_incremental_validation_patch_repair(
        state={"plan_build_state": state.model_dump(mode="json"), "simulation_plan": plan, "output_dir": str(tmp_path), "requirement": "generic assembly"},
        report=report, target_patch_types=["pin_map"], llm_client=client,
    )
    assert evaluation is not None and evaluation.status == "rejected_no_improvement"
    assert client.request is not None
    prompt = client.request["prompt"]
    assert "expected_counts" in prompt and "actual_counts" in prompt and "axial_profile_source_ids" in prompt
