import pytest


def test_accepted_repair_does_not_require_graph_retry_increment(monkeypatch) -> None:
    pytest.importorskip("openmc")
    from openmc_agent.graph import _make_validate_plan_node
    from openmc_agent.schemas import ValidationIssue
    from openmc_agent.plan_builder.validation_repair import PatchRepairEvaluation
    from tests.test_workflow_trace import _complex_plan_with_pin_count_mismatch

    repaired = _complex_plan_with_pin_count_mismatch().model_copy(deep=True)
    repaired.complex_model.lattices[0].universe_pattern = [["fuel_pin", "guide_tube"], ["fuel_pin", "instrument_tube"]]
    evaluation = PatchRepairEvaluation(accepted=True, status="accepted", issues_before=["lattice.pin_count_mismatch"], issues_after=[], resolved_issue_codes=["lattice.pin_count_mismatch"], introduced_issue_codes=[], issue_fingerprint_before="f", repaired_plan=repaired.model_dump(mode="json"))
    monkeypatch.setattr("openmc_agent.graph._try_incremental_validation_patch_repair", lambda **_kw: (None, evaluation, {"status": "accepted"}))
    updates = _make_validate_plan_node(2)({"simulation_plan": _complex_plan_with_pin_count_mismatch(), "requirement": "assembly", "retry_count": 0, "plan_build_state": {"patches": {"x": {}}}, "incremental_execution_result": {"planning_mode": "incremental", "monolithic_reflect_plan_allowed": False}})
    assert updates["retry_count"] == 0
    assert updates["incremental_patch_repair_accepted"] is True


def test_real_model_path_lazily_constructs_repair_client(monkeypatch) -> None:
    """A real model must not skip repair merely because no client was injected."""
    pytest.importorskip("openmc")
    from openmc_agent.graph import _make_validate_plan_node
    from tests.test_workflow_trace import _complex_plan_with_pin_count_mismatch

    sentinel = object()
    captured = {}
    monkeypatch.setattr(
        "openmc_agent.plan_builder.llm_adapter.make_patch_llm_client",
        lambda *, model_name: sentinel if model_name == "deepseek:deepseek-chat" else None,
    )

    def fake_repair(**kwargs):
        captured["client"] = kwargs["llm_client"]
        return None, None, {"status": "unavailable"}

    monkeypatch.setattr("openmc_agent.graph._try_incremental_validation_patch_repair", fake_repair)
    _make_validate_plan_node(2)({
        "simulation_plan": _complex_plan_with_pin_count_mismatch(),
        "requirement": "assembly",
        "model": "deepseek:deepseek-chat",
        "retry_count": 0,
        "plan_build_state": {"patches": {"pin_map": {}}},
        "incremental_execution_result": {
            "planning_mode": "incremental",
            "monolithic_reflect_plan_allowed": False,
        },
    })
    assert captured["client"] is sentinel


def test_repair_invocation_prefers_structured_patch_adapter() -> None:
    from openmc_agent.graph import _invoke_patch_repair_llm
    from openmc_agent.plan_builder.validation_repair import PatchRepairRequest

    class StructuredClient:
        def __init__(self) -> None:
            self.kwargs = None

        def generate_patch_json(self, **kwargs):
            self.kwargs = kwargs
            return {"repair_id": "r", "target_patch_type": "pin_map", "operations": [], "rationale": "x", "confidence": 0.1}

    request = PatchRepairRequest(
        repair_id="r",
        issue_fingerprint="f",
        target_patch_type="pin_map",
        issues=[],
        previous_patch_content={},
        previous_patch_hash="h",
        relevant_plan_fragment={},
        valid_upstream_patch_summaries={},
        allowed_path_patterns=[],
        forbidden_path_patterns=[],
    )
    client = StructuredClient()
    assert _invoke_patch_repair_llm(client, request, "prompt")["repair_id"] == "r"
    assert client.kwargs["patch_type"] == "pin_map"
    assert client.kwargs["json_schema"]["title"] == "PatchRepairModelOutput"


def test_missing_metadata_real_model_like_proposal_is_normalized_and_evaluated(tmp_path, monkeypatch) -> None:
    pytest.importorskip("openmc")
    from openmc_agent.graph import _try_incremental_validation_patch_repair
    from openmc_agent.plan_builder.state import PlanBuildState
    from openmc_agent.schemas import ValidationIssue, ValidationReport
    from tests.test_workflow_trace import _complex_plan_with_pin_count_mismatch

    plan = _complex_plan_with_pin_count_mismatch()
    state = PlanBuildState(
        state_id="repair-schema-test",
        requirement_text="assembly",
        planning_mode="incremental",
    )
    state.assembled_plan = plan.model_dump(mode="json")
    state.patches = {}
    # Reuse the focused evaluator setup rather than a full executor run.
    from openmc_agent.plan_builder.state import PlanPatchEnvelope
    state.patches["pin"] = PlanPatchEnvelope(
        patch_id="pin", patch_type="pin_map", status="valid",
        content={"default_universe_id": "fuel_pin", "guide_tube_coords": [], "instrument_tube_coords": [], "water_cell_coords": []},
    )
    report = ValidationReport(is_valid=False, issues=[ValidationIssue(code="lattice.pin_count_mismatch", severity="error", schema_path="complex_model.lattices.assembly_lattice.universe_pattern", message="mismatch")])

    class MissingMetadataClient:
        def generate_patch_json(self, **_kwargs):
            return {"operations": []}

    repaired, evaluation, meta = _try_incremental_validation_patch_repair(
        state={"plan_build_state": state.model_dump(mode="json"), "output_dir": str(tmp_path), "requirement": "assembly"},
        report=report,
        target_patch_types=["pin_map"],
        llm_client=MissingMetadataClient(),
    )
    assert repaired is not None
    assert evaluation is not None and evaluation.status == "rejected_no_improvement"
    assert meta["status"] == "rejected_no_improvement"
    saved = (tmp_path / "validation_repair" / "evaluation_0.json").read_text()
    assert "rejected_no_improvement" in saved
    normalized = (tmp_path / "validation_repair" / "normalized_proposal_0.json").read_text()
    assert "Model did not provide a rationale." in normalized


def test_malformed_operations_get_one_schema_only_correction(tmp_path) -> None:
    pytest.importorskip("openmc")
    from openmc_agent.graph import _try_incremental_validation_patch_repair
    from openmc_agent.plan_builder.state import PlanBuildState, PlanPatchEnvelope
    from openmc_agent.schemas import ValidationIssue, ValidationReport
    from tests.test_workflow_trace import _complex_plan_with_pin_count_mismatch

    plan = _complex_plan_with_pin_count_mismatch()
    build_state = PlanBuildState(state_id="format-correction", requirement_text="assembly")
    build_state.assembled_plan = plan.model_dump(mode="json")
    build_state.add_patch(PlanPatchEnvelope(
        patch_id="pin", patch_type="pin_map", status="valid",
        content={"default_universe_id": "fuel_pin", "guide_tube_coords": [], "instrument_tube_coords": [], "water_cell_coords": []},
    ))
    report = ValidationReport(is_valid=False, issues=[ValidationIssue(
        code="lattice.pin_count_mismatch", severity="error",
        schema_path="complex_model.lattices.assembly_lattice.universe_pattern", message="mismatch",
    )])

    class CorrectionClient:
        calls = 0
        def generate_patch_json(self, **_kwargs):
            self.calls += 1
            return {} if self.calls == 1 else {"operations": []}

    client = CorrectionClient()
    _, evaluation, meta = _try_incremental_validation_patch_repair(
        state={"plan_build_state": build_state.model_dump(mode="json"), "output_dir": str(tmp_path), "requirement": "assembly"},
        report=report, target_patch_types=["pin_map"], llm_client=client,
    )
    assert client.calls == 2
    assert evaluation is not None and evaluation.status == "rejected_no_improvement"
    assert meta["format_correction_count"] == 1
    assert (tmp_path / "validation_repair" / "raw_response_0_format_correction_1.json").exists()


def test_incremental_patch_generation_failure_schedules_regeneration() -> None:
    """When incremental patch generation fails (plan=None), the workflow must
    schedule a fresh regeneration instead of dead-ending."""
    from openmc_agent.graph import _make_validate_plan_node

    updates = _make_validate_plan_node(3)({
        "simulation_plan": None,
        "requirement": "VERA3B assembly model",
        "retry_count": 0,
        "error": "incremental.execution_failed: incremental.patch_generation_failed",
        "plan_build_state": {"patches": {"facts": {"content": {}}}},
        "incremental_execution_result": {
            "planning_mode": "incremental",
            "monolithic_reflect_plan_allowed": False,
            "ok": False,
            "issues": [
                {
                    "code": "incremental.patch_generation_failed",
                    "severity": "error",
                    "message": "universes generation failed",
                    "patch_type": "universes",
                }
            ],
        },
    })

    assert updates["incremental_regeneration_pending"] is True
    assert updates["retry_count"] == 1
    assert updates["simulation_plan"] is None
    # Valid patches must be preserved (not cleared) for incremental retry.
    assert "patches" in updates["plan_build_state"]
    assert "facts" in updates["plan_build_state"]["patches"]
    assert "Incremental planner correction required" in updates["requirement"]


def test_incremental_patch_generation_failure_respects_retry_budget() -> None:
    """When retry_count >= max_retries, no regeneration is scheduled."""
    from openmc_agent.graph import _make_validate_plan_node

    updates = _make_validate_plan_node(3)({
        "simulation_plan": None,
        "requirement": "VERA3B assembly model",
        "retry_count": 3,
        "error": "incremental.execution_failed: incremental.patch_generation_failed",
        "plan_build_state": {"patches": {"facts": {"content": {}}}},
        "incremental_execution_result": {
            "planning_mode": "incremental",
            "monolithic_reflect_plan_allowed": False,
            "ok": False,
            "issues": [],
        },
    })

    assert "incremental_regeneration_pending" not in updates or updates.get("incremental_regeneration_pending") is not True


def test_non_incremental_failure_does_not_trigger_regeneration() -> None:
    """A non-incremental plan=None failure must not trigger the regeneration path."""
    from openmc_agent.graph import _make_validate_plan_node

    updates = _make_validate_plan_node(3)({
        "simulation_plan": None,
        "requirement": "simple pin cell",
        "retry_count": 0,
        "error": "Could not validate model response",
        "plan_build_state": {},
        "incremental_execution_result": None,
    })

    assert not updates.get("incremental_regeneration_pending")
