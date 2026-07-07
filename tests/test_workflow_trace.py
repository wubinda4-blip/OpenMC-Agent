import json
from pathlib import Path

import pytest

from openmc_agent.error_catalog import issue_from_catalog
from openmc_agent.evidence_ranker import rank_and_select_evidence
from openmc_agent.evaluation import (
    EvaluationCase,
    EvaluationResult,
    aggregate_evaluation_results,
    evaluate_trace_against_case,
)
from openmc_agent.graph import (
    _ask_expert,
    _make_reflect_plan_node,
    _make_validate_plan_node,
)
from openmc_agent.grep_search import RetrievedEvidence
from openmc_agent.knowledge_graph import GraphContext
from openmc_agent.llm import StructuredOutputResult
from openmc_agent.retrieval_orchestrator import RetrievalContext
from openmc_agent.schemas import (
    AssemblySpec,
    CellSpec,
    ComplexMaterialSpec,
    ComplexModelSpec,
    GeometrySpec,
    LatticeSpec,
    MaterialSpec,
    NuclideSpec,
    PinCellSpec,
    PlotSpec,
    RenderCapabilityReport,
    RunSettingsSpec,
    SettingsSpec,
    SimulationPlan,
    SimulationSpec,
    UniverseSpec,
    ValidationReport,
)
from openmc_agent.workflow_trace import (
    TraceConfig,
    TraceRecorder,
    preview_plan,
    save_trace_json,
    save_trace_jsonl,
    summarize_capability_report,
    summarize_retrieval_context,
    summarize_validation_report,
    trace_from_raw,
)


def _pin_spec() -> SimulationSpec:
    fuel = MaterialSpec(
        name="UO2 fuel",
        density_unit="g/cm3",
        density_value=10.4,
        composition=[
            NuclideSpec(name="U235", percent=4.95),
            NuclideSpec(name="U238", percent=95.05),
            NuclideSpec(name="O16", percent=200.0),
        ],
    )
    water = MaterialSpec(
        name="Water",
        density_unit="g/cm3",
        density_value=1.0,
        composition=[NuclideSpec(name="H1", percent=2.0), NuclideSpec(name="O16", percent=1.0)],
    )
    return SimulationSpec(
        name="pin",
        pin_cell=PinCellSpec(
            fuel=fuel,
            moderator=water,
            geometry=GeometrySpec(fuel_radius_cm=0.41, pitch_cm=1.26),
        ),
        settings=SettingsSpec(batches=5, inactive=1, particles=100),
    )


def _pin_plan() -> SimulationPlan:
    return SimulationPlan(
        model_spec=_pin_spec(),
        capability_report=RenderCapabilityReport(
            renderability="runnable",
            supported_renderer="pin_cell",
        ),
        plot_specs=[PlotSpec(basis="xy", width_cm=(1.26, 1.26), filename="pin.png")],
        execution_check=RunSettingsSpec.model_construct()  # overwritten below by default coercion
        if False
        else None,
    )


def _simple_plan() -> SimulationPlan:
    return SimulationPlan(
        model_spec=_pin_spec(),
        capability_report=RenderCapabilityReport(
            renderability="runnable",
            supported_renderer="pin_cell",
        ),
        plot_specs=[PlotSpec(basis="xy", width_cm=(1.26, 1.26), filename="pin.png")],
    )


def _complex_plan_with_bad_universe() -> SimulationPlan:
    model = ComplexModelSpec(
        name="assembly",
        kind="assembly",
        materials=[
            ComplexMaterialSpec(
                id="fuel",
                name="fuel",
                density_unit="g/cm3",
                density_value=10.4,
                composition=[NuclideSpec(name="U235", percent=1.0)],
            )
        ],
        cells=[CellSpec(id="fuel_cell", name="fuel", fill_type="material", fill_id="fuel")],
        universes=[UniverseSpec(id="pin", name="pin", cell_ids=["missing_cell"])],
        lattices=[
            LatticeSpec(
                id="lat",
                name="lat",
                kind="rect",
                pitch_cm=(1.26, 1.26),
                universe_pattern=[["pin"]],
            )
        ],
        assemblies=[AssemblySpec(id="a", name="a", lattice_id="lat")],
    )
    return SimulationPlan(
        schema_version="simulation_plan.v2",
        model_spec=None,
        complex_model=model,
        capability_report=RenderCapabilityReport(
            renderability="skeleton",
            supported_renderer="none",
            issues=[issue_from_catalog("export_xml.dangling_lattice_universe")],
        ),
        plot_specs=[PlotSpec(basis="xy", width_cm=(1.26, 1.26), filename="assembly.png")],
    )


def _event_types(raw_trace: dict) -> list[str]:
    return [event["event_type"] for event in raw_trace["events"]]


def test_trace_recorder_exports_json_and_jsonl(tmp_path: Path) -> None:
    recorder = TraceRecorder(config=TraceConfig(max_preview_chars=20))
    recorder.add_event(
        "validation_completed",
        summary="x" * 100,
        metadata={"long": "y" * 100, "path": Path("model.py")},
    )
    recorder.add_event("workflow_completed", summary="done")

    payload = recorder.export_json()
    jsonl = recorder.export_jsonl()

    assert payload["events"][0]["summary"].endswith("...")
    assert len(payload["events"][0]["metadata"]["long"]) <= 20
    assert json.loads(jsonl.splitlines()[0])["event_type"] == "validation_completed"

    save_trace_json(recorder.trace, tmp_path / "trace.json")
    save_trace_jsonl(recorder.trace, tmp_path / "trace.jsonl")
    assert (tmp_path / "trace.json").exists()
    assert (tmp_path / "trace.jsonl").read_text(encoding="utf-8").count("\n") == 2


def test_preview_plan_length_is_bounded() -> None:
    preview = preview_plan(_simple_plan(), max_chars=80)

    assert len(preview) <= 80
    assert preview.endswith("...")


def test_summary_helpers_extract_structured_counts() -> None:
    issue = issue_from_catalog("runtime.geometry_overlap")
    report = ValidationReport.from_issues([issue])
    retrieval = RetrievalContext(
        issues=[issue],
        grep_evidence=[
            RetrievedEvidence(
                source_type="grep",
                locator="tests/x.py:1",
                text="overlap",
            )
        ],
        graph_context=GraphContext(nodes=[], edges=[]),
        rag_evidence=[
            RetrievedEvidence(source_type="rag", locator="docs/x.md:1", text="geometry")
        ],
        merged_evidence=[
            RetrievedEvidence(source_type="grep", locator="tests/x.py:1", text="overlap")
        ],
    )
    ranking = rank_and_select_evidence(retrieval.merged_evidence)
    retrieval.evidence_ranking_result = ranking
    retrieval.ranked_evidence = ranking.selected
    capability = RenderCapabilityReport(
        renderability="skeleton",
        supported_renderer="assembly",
        unsupported_subsystems=["hex_lattice"],
        required_human_confirmations=["cross sections path"],
    )

    validation_summary = summarize_validation_report(report)
    retrieval_summary = summarize_retrieval_context(retrieval)
    capability_summary = summarize_capability_report(capability)

    assert validation_summary["issue_count"] == 1
    assert validation_summary["issue_codes"] == ["runtime.geometry_overlap"]
    assert validation_summary["route_hints"] == ["reflect_plan"]
    assert validation_summary["requires_retrieval_count"] == 1
    assert retrieval_summary["grep_evidence_count"] == 1
    assert retrieval_summary["rag_evidence_count"] == 1
    assert retrieval_summary["merged_evidence_count"] == 1
    assert retrieval_summary["ranked_evidence_count"] == 1
    assert retrieval_summary["dropped_duplicate_count"] == 0
    assert retrieval_summary["evidence_score_max"] is not None
    assert capability_summary["renderability"] == "skeleton"
    assert capability_summary["supported_renderer"] == "assembly"


def test_validate_plan_node_records_validation_completed() -> None:
    node = _make_validate_plan_node(max_retries=1)

    updates = node({"simulation_plan": _simple_plan(), "requirement": "pin"})

    trace = trace_from_raw(updates["trace"])
    assert "validation_completed" in [event.event_type for event in trace.events]


def test_reflect_plan_records_retrieval_events(monkeypatch) -> None:
    issue = issue_from_catalog("runtime.geometry_overlap")
    report = ValidationReport.from_issues([issue])
    plan = _simple_plan()

    def fake_gather(_issues):
        return RetrievalContext(
            issues=[issue],
            graph_context=GraphContext(
                related_doc_refs=["openmc.usersguide.geometry"],
                retrieval_hints=["geometry overlap surfaces"],
            ),
            rag_evidence=[
                RetrievedEvidence(source_type="rag", locator="docs/g.md:1", text="geometry")
            ],
        )

    def fake_repair_plan(**_kwargs):
        return StructuredOutputResult(ok=True, value=plan)

    monkeypatch.setattr("openmc_agent.graph.gather_retrieval_context_for_issues", fake_gather)
    node = _make_reflect_plan_node(fake_repair_plan)

    updates = node(
        {
            "simulation_plan": plan,
            "validation_report": report,
            "requirement": "fix geometry overlap",
            "retry_count": 0,
        }
    )

    event_types = _event_types(updates["trace"])
    assert "retrieval_started" in event_types
    assert "retrieval_completed" in event_types
    assert "reflect_plan_completed" in event_types


def test_reflect_plan_auto_repair_success_records_no_llm(monkeypatch) -> None:
    plan = _complex_plan_with_bad_universe()
    issue = issue_from_catalog("export_xml.dangling_lattice_universe")
    report = ValidationReport.from_issues([issue])
    calls = {"repair": 0}

    def fake_auto_repair(_plan, issues, **_kwargs):
        return [
            {
                "op": "replace",
                "path": "/complex_model/universes/0/cell_ids",
                "value": ["fuel_cell"],
            }
        ]

    def fake_repair_plan(**_kwargs):
        calls["repair"] += 1
        return StructuredOutputResult(ok=True, value=plan)

    monkeypatch.setattr("openmc_agent.graph.auto_repair_lattice_structure", fake_auto_repair)
    node = _make_reflect_plan_node(fake_repair_plan)

    updates = node(
        {
            "simulation_plan": plan,
            "validation_report": report,
            "requirement": "fix dangling universe",
            "retry_count": 0,
        }
    )

    trace = trace_from_raw(updates["trace"])
    assert calls["repair"] == 0
    assert "auto_repair_attempted" in [event.event_type for event in trace.events]
    completed = [event for event in trace.events if event.event_type == "reflect_plan_completed"][-1]
    assert completed.metadata["llm_called"] is False


def test_ask_expert_records_human_confirmation_without_interrupt() -> None:
    issue = issue_from_catalog("runtime.cross_sections_missing")
    report = ValidationReport.from_issues([issue])
    plan = _simple_plan()

    updates = _ask_expert(
        {
            "simulation_plan": plan,
            "validation_report": report,
            "requirement": "missing cross sections",
            "max_expert_rounds": 0,
            "expert_round_count": 0,
        }
    )

    trace = trace_from_raw(updates["trace"])
    event_types = [event.event_type for event in trace.events]
    assert "ask_expert_started" in event_types
    assert "ask_expert_completed" in event_types
    assert trace.events[-1].metadata["requires_human_confirmation_count"] >= 1


def test_trace_failure_does_not_block_workflow(monkeypatch) -> None:
    class BrokenRecorder:
        def __init__(self, *args, **kwargs):
            raise RuntimeError("trace broken")

    monkeypatch.setattr("openmc_agent.graph.TraceRecorder", BrokenRecorder)
    node = _make_validate_plan_node(max_retries=1)

    updates = node({"simulation_plan": _simple_plan(), "requirement": "pin"})

    assert updates["validation_report"].is_valid is True
    assert "trace" not in updates


def test_evaluate_trace_against_case_detects_expected_fields() -> None:
    recorder = TraceRecorder()
    recorder.add_event(
        "retrieval_completed",
        issue_codes=["runtime.geometry_overlap"],
        metadata={"requires_human_confirmation_count": 0},
    )
    recorder.add_event(
        "workflow_completed",
        renderability="runnable",
        supported_renderer="pin_cell",
    )
    recorder.trace.final_status = "valid"
    recorder.trace.final_renderability = "runnable"
    recorder.trace.final_supported_renderer = "pin_cell"
    case = EvaluationCase(
        case_id="geometry-overlap",
        category="runtime_error",
        user_request="fix overlap",
        expected_issue_codes=["runtime.geometry_overlap"],
        expected_renderability="runnable",
        expected_supported_renderer="pin_cell",
        should_trigger_retrieval=True,
        should_require_human_confirmation=False,
    )

    result = evaluate_trace_against_case(recorder.trace, case)

    assert result.passed is True
    assert result.triggered_retrieval is True
    assert result.metrics["issue_code_recall"] == 1.0


def test_evaluate_trace_reports_renderability_mismatch() -> None:
    recorder = TraceRecorder()
    recorder.add_event("workflow_completed", renderability="skeleton")
    recorder.trace.final_renderability = "skeleton"

    result = evaluate_trace_against_case(
        recorder.trace,
        EvaluationCase(
            case_id="pin",
            category="pin_cell",
            user_request="pin",
            expected_renderability="runnable",
        ),
    )

    assert result.passed is False
    assert any("renderability mismatch" in reason for reason in result.failure_reasons)


def test_aggregate_evaluation_results_and_empty_results() -> None:
    passed = EvaluationResult(case_id="a", passed=True)
    failed = EvaluationResult(case_id="b", passed=False, triggered_retrieval=True)

    metrics = aggregate_evaluation_results([passed, failed])
    empty = aggregate_evaluation_results([])

    assert metrics.case_count == 2
    assert metrics.pass_rate == 0.5
    assert metrics.retrieval_trigger_rate == 0.5
    assert empty.case_count == 0
    assert empty.pass_rate == 0.0


def test_evaluation_fixture_contains_minimum_cases() -> None:
    path = Path("tests/fixtures/evaluation_cases.json")
    cases = [EvaluationCase(**item) for item in json.loads(path.read_text(encoding="utf-8"))]

    assert {case.case_id for case in cases} >= {
        "pin-cell-valid",
        "hex-lattice-unsupported",
        "runtime-geometry-overlap",
        "cross-sections-missing",
        "dangling-lattice-universe",
    }
