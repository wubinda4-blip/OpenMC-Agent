from __future__ import annotations

import json
import hashlib
from pathlib import Path

from openmc_agent.plan_builder import executor
from openmc_agent.plan_builder.executor import run_incremental_planning
from openmc_agent.plan_builder.closed_loop.fingerprints import canonical_json_dumps
from openmc_agent.plan_builder.mode import should_use_incremental_planning
from openmc_agent.plan_builder.patch_generator import FakePatchLLM
from openmc_agent.plan_builder.state import PlanBuildState, initialize_plan_build_state


def _empty_pipeline(monkeypatch) -> None:
    monkeypatch.setattr(executor, "default_patch_task_order", lambda state: [])
    monkeypatch.setattr(executor, "required_patch_types_for_state", lambda state: [])

    def assemble(state, **_kwargs):
        state.assembled_plan = {"phase0": True}
        return state

    monkeypatch.setattr(executor, "assemble_state_if_ready", assemble)


def _canonical_hash(value) -> str:
    return hashlib.sha256(canonical_json_dumps(value).encode("utf-8")).hexdigest()


def test_off_is_quiet_and_advisory_writes_without_llm(monkeypatch, tmp_path) -> None:
    _empty_pipeline(monkeypatch)
    no_llm = lambda _prompt: (_ for _ in ()).throw(AssertionError("LLM must not be called"))
    off = PlanBuildState(state_id="off", requirement_text="r")
    off_result = run_incremental_planning(requirement="r", state=off, llm_client=no_llm)
    assert off_result.ok and not off.plan_loop_stages and not off.plan_loop_artifacts

    advisory = PlanBuildState(state_id="advisory", requirement_text="r")
    result = run_incremental_planning(
        requirement="r", state=advisory, llm_client=no_llm,
        plan_loop_policy={"mode": "advisory"}, plan_loop_output_dir=tmp_path,
    )
    assert result.ok and result.plan_loop_outcome["additional_llm_calls_used"] == 0
    assert all(stage.metadata.get("review_not_implemented") for stage in advisory.plan_loop_stages.values())
    assert (tmp_path / "incremental" / "plan_closed_loop" / "plan_loop_state.json").exists()


def test_controlled_no_longer_silently_downgrades_to_off() -> None:
    state = PlanBuildState(state_id="controlled", requirement_text="r")
    result = run_incremental_planning(
        requirement="r", state=state,
        llm_client=lambda _prompt: (_ for _ in ()).throw(AssertionError("LLM must not be called")),
        plan_loop_policy={"mode": "controlled"},
    )
    assert not result.ok
    assert [issue.code for issue in result.issues] == ["incremental.patch_generation_failed"]
    assert "planning.closed_loop.controlled_not_implemented" not in [issue.code for issue in result.issues]


def test_advisory_preserves_real_fixture_patch_and_plan_hashes(tmp_path) -> None:
    requirement = "VERA3 3B benchmark: 3D assembly with axial layers, spacer grids, 三维, 定位格架, Pyrex rods, thimble plugs, 17x17 lattice"
    fixture = Path(__file__).parent / "fixtures" / "vera3_patches" / "vera3_3b_patches.json"
    patches = json.loads(fixture.read_text("utf-8"))["patches"]
    responses = [json.dumps(item) for item in patches if item["patch_type"] != "settings"]

    def run(mode: str):
        state = initialize_plan_build_state(requirement, should_use_incremental_planning(requirement), benchmark_id="VERA3", selected_variant="3B")
        llm = FakePatchLLM(list(responses))
        result = run_incremental_planning(
            requirement=requirement, state=state, llm_client=llm, max_patch_attempts=1,
            plan_loop_policy={"mode": mode}, plan_loop_output_dir=tmp_path / mode,
        )
        return result, llm

    off, off_llm = run("off")
    advisory, advisory_llm = run("advisory")
    assert off.ok == advisory.ok
    assert len(off_llm.prompts) == len(advisory_llm.prompts)
    assert _canonical_hash(off.assembled_plan) == _canonical_hash(advisory.assembled_plan)
    assert {
        patch.patch_type: _canonical_hash(patch.content)
        for patch in off.state.patches.values()
    } == {
        patch.patch_type: _canonical_hash(patch.content)
        for patch in advisory.state.patches.values()
    }
    assert advisory.plan_loop_outcome["additional_llm_calls_used"] == 0
