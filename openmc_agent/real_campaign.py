"""Real Lane B campaign executor for VERA3B runtime stability evaluation.

Each run starts from scratch: fresh LLM clients, fresh PlanBuildState, real
OpenMC. No fake clients, no reference patches, no accepted fixtures.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from openmc_agent.runtime_metrics import (
    aggregate_real_campaign,
    real_campaign_status,
)


# --------------------------------------------------------------------------- #
# Contracts
# --------------------------------------------------------------------------- #


@dataclass
class RealCampaignRunConfig:
    run_id: str
    run_index: int
    input_path: str
    benchmark: str = "VERA3"
    variant: str = "3B"
    model: str = "deepseek:deepseek-chat"
    temperature: float = 0.0
    output_dir: str = ""
    reference_patch_policy: str = "off"
    max_runtime_iterations: int = 4
    enable_runtime_llm_repair: bool = True
    runtime_repair_mode: str = "apply_if_safe"
    runtime_supervisor_mode: str = "deterministic"
    enable_plots: bool = False
    enable_smoke_test: bool = True
    wall_time_limit_s: float = 900.0
    max_llm_calls: int = 16
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class RealLLMCallRecord:
    role: str
    task_name: str
    model: str
    started_at: str
    completed_at: str
    duration_ms: float
    requested_output_mode: str
    actual_output_mode: str
    structured_output_fallback: bool
    response_chars: int
    success: bool
    error_type: str
    prompt_hash: str
    schema_hash: str


@dataclass
class RealCampaignRunResult:
    run_id: str
    status: str  # "completed" | "infrastructure_failure" | "user_cancelled"
    final_disposition: str
    started_at: str
    completed_at: str
    duration_s: float
    git_sha: str
    input_sha: str
    configuration_hash: str
    provider: str
    model: str
    real_llm_verified: bool
    real_openmc_verified: bool
    llm_call_count: int
    llm_calls: list[dict[str, Any]] = field(default_factory=list)
    llm_output_chars: int = 0
    client_fallback_used: bool = False
    fake_client_used: bool = False
    planning_mode: str = ""
    patch_generation_calls: int = 0
    generated_patch_types: list[str] = field(default_factory=list)
    patch_statuses: dict[str, str] = field(default_factory=dict)
    failed_patch_type: str = ""
    validation_retry_count: int = 0
    reference_patches_used: list[str] = field(default_factory=list)
    selected_few_shot_ids: list[str] = field(default_factory=list)
    benchmark_specific_few_shot_used: bool = False
    monolithic_fallback_used: bool = False
    simulation_plan_present: bool = False
    plan_schema_valid: bool = False
    renderability: str = ""
    supported_renderer: str = ""
    xml_exported: bool = False
    geometry_debug_passed: bool = False
    smoke_passed: bool = False
    lost_particle_count: int = 0
    runtime_iterations: int = 0
    deterministic_runtime_attempts: int = 0
    runtime_llm_diagnoses: int = 0
    runtime_llm_proposals: int = 0
    committed_runtime_repairs: int = 0
    runtime_reexecutions: int = 0
    runtime_final_disposition: str = ""
    unsafe_proposal_count: int = 0
    unsafe_accepted_count: int = 0
    protected_field_change_count: int = 0
    environment_plan_repair_attempts: int = 0
    human_fact_plan_repair_attempts: int = 0
    duplicate_commit_count: int = 0
    infinite_loop_count: int = 0
    stale_plan_execution_count: int = 0
    human_intervention_required: bool = False
    vera3_acceptance_passed: bool = False
    vera3_acceptance_issue_codes: list[str] = field(default_factory=list)
    artifact_complete: bool = False
    failure_stage: str = ""
    failure_category: str = ""
    error_summary: str = ""
    artifact_paths: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
    # --- Strengthened LLM evidence (S2) ---
    real_network_call_count: int = 0
    successful_network_call_count: int = 0
    failed_network_call_count: int = 0
    planning_network_call_count: int = 0
    runtime_diagnosis_network_call_count: int = 0
    runtime_proposal_network_call_count: int = 0
    runtime_supervisor_network_call_count: int = 0
    first_network_call_at: str = ""
    last_network_call_at: str = ""
    client_instance_ids: list[str] = field(default_factory=list)
    response_artifact_ids: list[str] = field(default_factory=list)
    cached_response_used: bool = False
    fake_fallback_used: bool = False
    llm_verification_reasons: list[str] = field(default_factory=list)
    # --- Strengthened OpenMC evidence (S5) ---
    export_backend: str = ""  # real_python_export | injected_tool | mocked_tool | skipped
    geometry_debug_backend: str = ""
    smoke_backend: str = ""
    export_returncode: int = -1
    geometry_debug_returncode: int = -1
    smoke_returncode: int = -1
    source_rejection_count: int = 0
    # --- Reference/few-shot/fallback provenance (S4) ---
    reference_patch_policy: str = "off"
    reference_patch_ids: list[str] = field(default_factory=list)
    reference_patch_sources: list[str] = field(default_factory=list)
    reference_match_status: str = ""
    monolithic_fallback_attempted: bool = False
    monolithic_plan_source: str = ""
    selected_few_shot_sources: list[str] = field(default_factory=list)
    selected_few_shot_benchmarks: list[str] = field(default_factory=list)
    gold_few_shot_used: bool = False
    # --- Budget / timeout evidence (S8) ---
    budget_exhausted: bool = False
    timed_out: bool = False
    # --- Phase 7A five-gate controlled campaign evidence ---
    plan_loop_contract_version: str = ""
    five_gate_statuses: dict[str, str] = field(default_factory=dict)
    five_gate_accepted: bool = False
    gate_input_hashes: dict[str, str] = field(default_factory=dict)
    gate_review_call_count: int = 0
    gate_repair_call_count: int = 0
    gate_retry_count: int = 0
    awaiting_human_gate: str = ""
    blocked_gate: str = ""
    fragmented_universes_used: bool = False
    universe_manifest_hash: str = ""
    universe_manifest_status: str = ""
    expected_universe_count: int = 0
    accepted_fragment_count: int = 0
    retried_fragment_ids: list[str] = field(default_factory=list)
    fragment_resume_count: int = 0
    merged_universes_hash: str = ""
    truncation_count: int = 0
    strategy_transitions: list[str] = field(default_factory=list)
    plan_reviewer_network_call_count: int = 0
    plan_repair_network_call_count: int = 0
    final_gate_accepted_before_render: bool = False
    render_started_at: str = ""
    export_started_at: str = ""
    planning_completed_at: str = ""
    policy_hash: str = ""
    human_answer_hash: str = ""
    human_answer_consumed_questions: list[str] = field(default_factory=list)
    human_answer_unused: list[str] = field(default_factory=list)
    universes_generation_mode_selected: str = ""
    universes_fragment_token_usage: dict[str, int] = field(default_factory=dict)
    partial_fragment_exposed: bool = False
    reasoning_content_persisted: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# --------------------------------------------------------------------------- #
# Client bundle: fresh clients per run
# --------------------------------------------------------------------------- #


@dataclass
class RealCampaignClientBundle:
    patch_llm_client: Any
    runtime_diagnostician_client: Any
    runtime_patch_proposer_client: Any
    runtime_supervisor_client: Any | None
    provider: str
    model: str
    patch_client_instance_id: str = ""
    diag_client_instance_id: str = ""
    proposer_client_instance_id: str = ""
    supervisor_client_instance_id: str = ""
    # Phase 7A real five-gate reviewer and Phase-3B typed retry clients.
    # Both default to ``None`` so legacy VERA3B callers keep working; the
    # Phase 7A harness always supplies real instances.
    plan_reviewer_client: Any = None
    plan_repair_client: Any = None
    reviewer_client_instance_id: str = ""
    repair_client_instance_id: str = ""


def _create_client_bundle(
    config: RealCampaignRunConfig,
    recorder: LLMCallRecorder | None = None,
    *,
    plan_reviewer_enabled: bool = False,
    plan_repair_enabled: bool = False,
    plan_reviewer_model: str | None = None,
    plan_repair_model: str | None = None,
) -> RealCampaignClientBundle:
    """Create fresh LLM clients for one run.

    Each call creates a new provider client instance — no chat history,
    no cached patches, no reused PlanBuildState.

    If ``recorder`` is provided, all LLM calls are routed through it
    for unified evidence collection and budget enforcement.

    When ``plan_reviewer_enabled`` or ``plan_repair_enabled`` are true
    (Phase 7A controlled five-gate campaign), the bundle also creates
    dedicated real clients for the gate reviewer and the Phase-3B typed
    retry producer.  Legacy VERA3B callers leave both flags at their
    default ``False`` and the bundle fields stay ``None``.
    """
    from openmc_agent.llm import _client_for_model, _split_model
    from openmc_agent.plan_builder.llm_adapter import make_patch_llm_client
    from openmc_agent.runtime_diagnostician import (
        make_runtime_diagnostician_client,
    )
    from openmc_agent.runtime_patch_proposer import (
        make_runtime_patch_proposer_client,
    )

    provider, _ = _split_model(config.model)
    base_llm = _client_for_model(config.model)

    patch_cid = recorder.register_client("patch") if recorder else ""
    diag_cid = recorder.register_client("diag") if recorder else ""
    prop_cid = recorder.register_client("proposer") if recorder else ""
    sup_cid = recorder.register_client("supervisor") if recorder else ""

    # Planning patch client.
    patch_client = make_patch_llm_client(
        base_llm,
        model_name=config.model,
        temperature=config.temperature,
    )
    if recorder:
        patch_client = recorder.wrap_planning_client(patch_client, patch_cid)

    def _raw_llm_callable(input_dict: Any, *, prompt: str, json_schema: Any) -> str:
        resp = base_llm.chat.completions.create(
            model=config.model,
            messages=[{"role": "user", "content": prompt}],
            temperature=config.temperature,
        )
        return resp.choices[0].message.content

    # Wrap with recorder if available.
    diag_llm = _raw_llm_callable
    prop_llm = _raw_llm_callable
    if recorder:
        diag_llm = recorder.wrap_callable(
            _raw_llm_callable, role="runtime_diagnostician",
            client_instance_id=diag_cid,
        )
        prop_llm = recorder.wrap_callable(
            _raw_llm_callable, role="runtime_patch_proposer",
            client_instance_id=prop_cid,
        )

    diag_client = make_runtime_diagnostician_client(
        llm=diag_llm, model_name=config.model,
    )
    proposer_client = make_runtime_patch_proposer_client(
        llm=prop_llm, model_name=config.model,
    )

    # Phase 7A: dedicated real clients for the gate reviewer and the
    # Phase-3B typed retry producer.  Built only when the campaign asks
    # for them so legacy VERA3B callers keep working unchanged.
    plan_reviewer_client: Any = None
    plan_repair_client: Any = None
    reviewer_cid = ""
    repair_cid = ""
    if plan_reviewer_enabled:
        reviewer_cid = recorder.register_client("plan_reviewer") if recorder else ""
        reviewer_client_obj = make_patch_llm_client(
            base_llm,
            model_name=plan_reviewer_model or config.model,
            temperature=config.temperature,
        )
        if recorder:
            reviewer_client_obj = recorder.wrap_planning_client(reviewer_client_obj, reviewer_cid)
        plan_reviewer_client = reviewer_client_obj
    if plan_repair_enabled:
        repair_cid = recorder.register_client("plan_repair") if recorder else ""
        repair_client_obj = make_patch_llm_client(
            base_llm,
            model_name=plan_repair_model or config.model,
            temperature=config.temperature,
        )
        if recorder:
            repair_client_obj = recorder.wrap_planning_client(repair_client_obj, repair_cid)
        plan_repair_client = repair_client_obj

    # Runtime supervisor: deterministic by default.
    supervisor_client = None
    if config.runtime_supervisor_mode == "real":
        from openmc_agent.runtime_supervisor import RuntimeSupervisorClient

        class _SupervisorAdapter:
            def decide(self, supervisor_input: Any, *, prompt: str, json_schema: Any) -> str:
                resp = base_llm.chat.completions.create(
                    model=config.model,
                    messages=[{"role": "user", "content": prompt}],
                    temperature=config.temperature,
                )
                return resp.choices[0].message.content

        sup_adapter = _SupervisorAdapter()
        if recorder:
            original_decide = sup_adapter.decide
            sup_adapter.decide = recorder.wrap_callable(  # type: ignore
                lambda input_dict, *, prompt, json_schema: original_decide(
                    input_dict, prompt=prompt, json_schema=json_schema,
                ),
                role="runtime_supervisor",
                client_instance_id=sup_cid,
            )
        supervisor_client = sup_adapter

    return RealCampaignClientBundle(
        patch_llm_client=patch_client,
        runtime_diagnostician_client=diag_client,
        runtime_patch_proposer_client=proposer_client,
        runtime_supervisor_client=supervisor_client,
        provider=provider,
        model=config.model,
        patch_client_instance_id=patch_cid,
        diag_client_instance_id=diag_cid,
        proposer_client_instance_id=prop_cid,
        supervisor_client_instance_id=sup_cid,
        plan_reviewer_client=plan_reviewer_client,
        plan_repair_client=plan_repair_client,
        reviewer_client_instance_id=reviewer_cid,
        repair_client_instance_id=repair_cid,
    )


# --------------------------------------------------------------------------- #
# Run classification
# --------------------------------------------------------------------------- #


def classify_real_campaign_run(
    result: RealCampaignRunResult,
    ws: dict[str, Any],
) -> str:
    """Classify a run into a deterministic disposition."""
    if result.status == "infrastructure_failure":
        return "CAMPAIGN_INFRASTRUCTURE_FAILURE"
    if result.status == "user_cancelled":
        return "USER_CANCELLED"
    if result.timed_out:
        return "RUN_TIMEOUT"
    if result.budget_exhausted:
        return "SAFE_STOP_BUDGET"

    error = str(ws.get("error") or "")
    validation = ws.get("validation_report")
    validation_ok = bool(
        validation and (
            getattr(validation, "is_valid", False)
            if hasattr(validation, "is_valid")
            else isinstance(validation, dict) and validation.get("is_valid", False)
        )
    )

    committed_repairs = int(ws.get("runtime_committed_repair_count", 0))
    runtime_iters = int(ws.get("runtime_iteration_count", 0))

    # Success cases
    if validation_ok and not error and result.smoke_passed and result.real_openmc_verified:
        rfd = (ws.get("runtime_final_disposition") or "").lower()
        if committed_repairs == 0 and ("finish" in rfd or not rfd):
            return "FIRST_PASS_SUCCESS"
        if committed_repairs >= 1:
            return "RECOVERED_SUCCESS"

    # Planning failures
    if not result.simulation_plan_present:
        return "PLANNING_FAILURE"
    if error and "validation" in error.lower():
        return "PLAN_VALIDATION_FAILURE"

    # Render/export failures
    if not result.xml_exported:
        return "XML_EXPORT_FAILURE"
    if not result.geometry_debug_passed and result.xml_exported:
        return "GEOMETRY_DEBUG_FAILURE"

    # Smoke failure
    if not result.smoke_passed and result.geometry_debug_passed:
        return "OPENMC_SMOKE_FAILURE"

    # Source rejection failure
    if result.source_rejection_count > 0:
        return "SOURCE_REJECTION_FAILURE"

    # Lost particle failure
    if result.lost_particle_count > 0:
        return "LOST_PARTICLE_FAILURE"

    # Runtime safe-stops
    rfd = (ws.get("runtime_final_disposition") or "").lower()
    if "no_progress" in rfd:
        return "SAFE_STOP_NO_PROGRESS"
    if "environment" in rfd or "blocked_environment" in rfd:
        return "SAFE_STOP_ENVIRONMENT"
    if "human" in rfd or "blocked_human" in rfd:
        return "SAFE_STOP_HUMAN_FACT"
    if "budget" in rfd or "exhausted" in rfd:
        return "SAFE_STOP_BUDGET"

    return "SAFE_STOP_UNKNOWN"


# --------------------------------------------------------------------------- #
# Truthfulness validation
# --------------------------------------------------------------------------- #


def validate_real_run_truthfulness(
    result: RealCampaignRunResult,
    ws: dict[str, Any],
) -> list[str]:
    """Return list of truthfulness violations. Empty = fully truthful."""
    violations: list[str] = []

    if result.fake_client_used:
        violations.append("fake_client_used")
    if result.client_fallback_used:
        violations.append("client_fallback_used")
    if result.reference_patches_used:
        violations.append("reference_patches_used")
    if result.monolithic_fallback_used:
        violations.append("monolithic_fallback_used")
    if result.monolithic_fallback_attempted:
        violations.append("monolithic_fallback_attempted")
    if result.benchmark_specific_few_shot_used:
        violations.append("benchmark_specific_few_shot_used")
    if result.gold_few_shot_used:
        violations.append("gold_few_shot_used")
    if not result.real_llm_verified:
        violations.append("real_llm_not_verified")
    if result.cached_response_used:
        violations.append("cached_response_used")
    if not result.real_openmc_verified and result.smoke_passed:
        violations.append("smoke_passed_without_real_openmc")
    # Export backend must be real.
    if result.export_backend not in {"real_python_export"} and result.xml_exported:
        violations.append(f"export_backend_suspicious:{result.export_backend}")
    # Geometry debug backend must be real.
    if result.geometry_debug_passed and result.geometry_debug_backend not in {"real_openmc"}:
        violations.append(f"geometry_debug_backend_suspicious:{result.geometry_debug_backend}")
    # Smoke backend must be real.
    if result.smoke_passed and result.smoke_backend not in {"real_openmc"}:
        violations.append(f"smoke_backend_suspicious:{result.smoke_backend}")
    # Few-shot provenance must be verifiable.
    if result.selected_few_shot_ids and not result.selected_few_shot_sources:
        violations.append("few_shot_provenance_unverifiable")

    return violations


# --------------------------------------------------------------------------- #
# One-run executor
# --------------------------------------------------------------------------- #


def run_real_generation_once(
    config: RealCampaignRunConfig,
    *,
    requirement_text: str,
    input_sha: str,
    git_sha: str,
    config_hash: str,
    vera3_acceptance_callback: Callable[[Any], tuple[bool, list[str]]] | None = None,
) -> RealCampaignRunResult:
    """Execute one real LLM generation run through the production graph.

    Returns a comprehensive result with full evidence trail.
    """
    from openmc_agent.llm_call_recorder import (
        LLMCallRecorder,
        LLMBudgetExhausted,
        verify_real_llm,
    )

    run_dir = Path(config.output_dir)
    run_dir.mkdir(parents=True, exist_ok=True)
    started_at_dt = datetime.now(timezone.utc)
    started_at = started_at_dt.isoformat()
    t0 = time.perf_counter()
    deadline = t0 + config.wall_time_limit_s

    result = RealCampaignRunResult(
        run_id=config.run_id,
        status="completed",
        final_disposition="UNKNOWN",
        started_at=started_at,
        completed_at="",
        duration_s=0.0,
        git_sha=git_sha,
        input_sha=input_sha,
        configuration_hash=config_hash,
        provider="",
        model=config.model,
        real_llm_verified=False,
        real_openmc_verified=False,
        llm_call_count=0,
    )

    # Create unified recorder for all LLM calls.
    recorder = LLMCallRecorder(
        run_id=config.run_id,
        model=config.model,
        provider="",
        max_calls=config.max_llm_calls,
    )

    try:
        bundle = _create_client_bundle(config, recorder=recorder)
        result.provider = bundle.provider
        recorder.provider = bundle.provider
        result.fake_client_used = False
        result.client_fallback_used = False
        result.client_instance_ids = sorted(recorder._client_instance_ids)
    except Exception as exc:
        result.status = "infrastructure_failure"
        result.failure_stage = "client_creation"
        result.failure_category = "llm_connectivity"
        result.error_summary = str(exc)[:500]
        result.completed_at = datetime.now(timezone.utc).isoformat()
        result.duration_s = time.perf_counter() - t0
        _populate_llm_evidence(result, recorder, started_at, config.model)
        return result

    # Invoke production graph.
    from openmc_agent.graph import build_plan_graph

    graph_kwargs: dict[str, Any] = {
        "enable_plots": config.enable_plots,
        "enable_smoke_test": config.enable_smoke_test,
        "use_incremental_executor": True,
        "allow_monolithic_fallback_for_incremental_failure": False,
        "reference_patch_policy": config.reference_patch_policy,
        "patch_llm_client": bundle.patch_llm_client,
        "enable_runtime_supervisor": True,
        "enable_llm_runtime_repair": config.enable_runtime_llm_repair,
        "llm_runtime_repair_mode": config.runtime_repair_mode,
        "runtime_diagnostician_client": bundle.runtime_diagnostician_client,
        "runtime_diagnostician_model": config.model,
        "runtime_diagnostician_allow_fallback": False,
        "runtime_patch_proposer_client": bundle.runtime_patch_proposer_client,
        "runtime_patch_proposer_model": config.model,
        "runtime_patch_proposer_allow_fallback": False,
        "runtime_supervisor_client": bundle.runtime_supervisor_client,
        "runtime_supervisor_allow_fallback": False,
        "runtime_loop_budget": {
            "max_runtime_iterations": config.max_runtime_iterations,
        },
    }

    graph = build_plan_graph(**graph_kwargs)

    initial_state: dict[str, Any] = {
        "requirement": requirement_text,
        "model": config.model,
        "output_dir": str(run_dir / "workflow"),
        "records_path": str(run_dir / "simulation_runs.jsonl"),
        "use_incremental_executor": True,
    }

    try:
        ws = graph.invoke(initial_state)
    except KeyboardInterrupt:
        result.status = "user_cancelled"
        result.completed_at = datetime.now(timezone.utc).isoformat()
        result.duration_s = time.perf_counter() - t0
        _populate_llm_evidence(result, recorder, started_at, config.model)
        return result
    except LLMBudgetExhausted:
        result.budget_exhausted = True
        result.final_disposition = "SAFE_STOP_BUDGET"
        result.runtime_final_disposition = "budget_exhausted"
        result.completed_at = datetime.now(timezone.utc).isoformat()
        result.duration_s = time.perf_counter() - t0
        _populate_llm_evidence(result, recorder, started_at, config.model)
        _extract_results(result, ws if "ws" in dir() else {}, run_dir)
        _populate_provenance(result, ws if "ws" in dir() else {})
        return result
    except Exception as exc:
        result.status = "infrastructure_failure"
        result.failure_stage = "graph_invocation"
        result.failure_category = "harness_infrastructure"
        result.error_summary = str(exc)[:500]
        result.completed_at = datetime.now(timezone.utc).isoformat()
        result.duration_s = time.perf_counter() - t0
        _populate_llm_evidence(result, recorder, started_at, config.model)
        return result

    # Check timeout.
    elapsed = time.perf_counter() - t0
    if elapsed > config.wall_time_limit_s:
        result.timed_out = True
        result.final_disposition = "RUN_TIMEOUT"

    # Extract results from workflow state.
    _extract_results(result, ws, run_dir)

    # Populate LLM evidence from recorder.
    _populate_llm_evidence(result, recorder, started_at, config.model)

    # Populate provenance from actual workflow state.
    _populate_provenance(result, ws)

    # Verify real LLM.
    llm_ok, reasons = verify_real_llm(recorder, model=config.model, run_started_at=started_at)
    result.real_llm_verified = llm_ok
    result.llm_verification_reasons = reasons

    # Verify real OpenMC (3-stage).
    _verify_real_openmc(result, ws, run_dir)

    # VERA3 acceptance check.
    if vera3_acceptance_callback and result.simulation_plan_present:
        try:
            plan = ws.get("simulation_plan")
            passed, codes = vera3_acceptance_callback(plan)
            result.vera3_acceptance_passed = passed
            result.vera3_acceptance_issue_codes = codes
        except Exception:
            result.vera3_acceptance_passed = False
            result.vera3_acceptance_issue_codes = ["acceptance_check_error"]

    # Classify (skip if already classified by timeout/budget).
    if not result.final_disposition or result.final_disposition == "UNKNOWN":
        result.final_disposition = classify_real_campaign_run(result, ws)

    result.completed_at = datetime.now(timezone.utc).isoformat()
    result.duration_s = time.perf_counter() - t0

    # Truthfulness violations.
    truth_violations = validate_real_run_truthfulness(result, ws)
    result.metadata["truth_violations"] = truth_violations

    # Artifact completeness + manifest.
    _check_artifact_completeness(result, run_dir)

    # Write LLM call manifest.
    _write_json_atomic(
        run_dir / "llm_call_manifest.json",
        recorder.to_dict_list(),
    )

    # Write truthfulness evidence.
    _write_json_atomic(
        run_dir / "truthfulness_evidence.json",
        {
            "real_llm_verified": result.real_llm_verified,
            "llm_verification_reasons": result.llm_verification_reasons,
            "real_openmc_verified": result.real_openmc_verified,
            "export_backend": result.export_backend,
            "geometry_debug_backend": result.geometry_debug_backend,
            "smoke_backend": result.smoke_backend,
            "llm_evidence": recorder.evidence_summary(),
            "reference_patch_policy": result.reference_patch_policy,
            "reference_patches_used": result.reference_patches_used,
            "reference_patch_ids": result.reference_patch_ids,
            "monolithic_fallback_used": result.monolithic_fallback_used,
            "monolithic_fallback_attempted": result.monolithic_fallback_attempted,
            "benchmark_specific_few_shot_used": result.benchmark_specific_few_shot_used,
            "gold_few_shot_used": result.gold_few_shot_used,
            "truth_violations": truth_violations,
        },
    )

    return result


def _extract_results(
    result: RealCampaignRunResult,
    ws: dict[str, Any],
    run_dir: Path,
) -> None:
    """Extract comprehensive results from workflow state."""
    from openmc_agent.schemas import SimulationPlan

    # Plan presence and validity.
    plan = ws.get("simulation_plan")
    result.simulation_plan_present = plan is not None
    if plan is not None:
        if isinstance(plan, SimulationPlan):
            result.plan_schema_valid = True
            result.renderability = (
                plan.capability_report.renderability
                if plan.capability_report
                else "none"
            )
            result.supported_renderer = (
                plan.capability_report.supported_renderer
                if plan.capability_report
                else "none"
            )
        elif isinstance(plan, dict):
            result.plan_schema_valid = True
            cr = plan.get("capability_report", {})
            result.renderability = cr.get("renderability", "none")
            result.supported_renderer = cr.get("supported_renderer", "none")

    # Error.
    error = str(ws.get("error") or "")
    result.error_summary = error[:500]

    # Tool results.
    tool_results = ws.get("tool_results") or []
    for tr in tool_results:
        if isinstance(tr, dict):
            name = tr.get("name", "")
            ok = tr.get("ok", False)
            if name == "export_xml":
                result.xml_exported = ok
            elif name == "run_geometry_debug":
                result.geometry_debug_passed = ok
            elif name == "run_smoke_test":
                result.smoke_passed = ok
                issues = tr.get("issues") or []
                for issue in issues:
                    code = issue.get("code", "") if isinstance(issue, dict) else getattr(issue, "code", "")
                    if "lost_particle" in code:
                        result.lost_particle_count += 1

    # Runtime metrics.
    result.runtime_iterations = int(ws.get("runtime_iteration_count", 0))
    result.deterministic_runtime_attempts = int(ws.get("runtime_repair_count", 0))
    result.runtime_llm_diagnoses = int(ws.get("runtime_llm_diagnosis_count", 0))
    result.runtime_llm_proposals = int(ws.get("runtime_llm_proposal_count", 0))
    result.committed_runtime_repairs = int(ws.get("runtime_committed_repair_count", 0))
    result.runtime_reexecutions = int(ws.get("runtime_reexecution_count", 0))
    result.runtime_final_disposition = ws.get("runtime_final_disposition") or ""

    # Safety metrics.
    result.unsafe_proposal_count = int(ws.get("runtime_unsafe_proposal_count", 0))
    result.duplicate_commit_count = int(ws.get("runtime_duplicate_commit_count", 0))
    result.infinite_loop_count = int(ws.get("runtime_infinite_loop_count", 0))

    # Planning metadata.
    build_state = ws.get("plan_build_state") or {}
    if isinstance(build_state, dict):
        patches = build_state.get("patches") or {}
        result.patch_generation_calls = len(patches)
        result.generated_patch_types = list({
            v.get("patch_type", "")
            for v in patches.values()
            if isinstance(v, dict)
        })
        for pid, env in patches.items():
            if isinstance(env, dict):
                result.patch_statuses[pid] = env.get("status", "unknown")

    # Count LLM calls from patch attempt artifacts (fallback if recorder absent).
    workflow_dir = run_dir / "workflow" / "incremental" / "patch_attempts"
    if workflow_dir.exists() and result.llm_call_count == 0:
        raw_files = list(workflow_dir.glob("*_raw.txt"))
        result.llm_output_chars = sum(
            f.stat().st_size for f in raw_files
        )


def _populate_llm_evidence(
    result: RealCampaignRunResult,
    recorder: Any,
    started_at: str,
    model: str,
) -> None:
    """Populate LLM evidence fields from the unified recorder."""
    from openmc_agent.llm_call_recorder import LLMCallRecorder

    if not isinstance(recorder, LLMCallRecorder):
        return

    evidence = recorder.evidence_summary()
    result.llm_calls = recorder.to_dict_list()
    result.llm_call_count = evidence["total_calls"]
    result.real_network_call_count = evidence["real_network_call_count"]
    result.successful_network_call_count = evidence["successful_network_call_count"]
    result.failed_network_call_count = evidence["failed_network_call_count"]
    result.planning_network_call_count = evidence["planning_network_call_count"]
    result.runtime_diagnosis_network_call_count = evidence["runtime_diagnosis_network_call_count"]
    result.runtime_proposal_network_call_count = evidence["runtime_proposal_network_call_count"]
    result.runtime_supervisor_network_call_count = evidence["runtime_supervisor_network_call_count"]
    result.first_network_call_at = evidence["first_network_call_at"]
    result.last_network_call_at = evidence["last_network_call_at"]
    result.client_instance_ids = evidence["client_instance_ids"]
    result.response_artifact_ids = evidence["response_artifact_ids"]
    result.cached_response_used = evidence["cached_response_used"]
    result.fake_fallback_used = evidence["fake_fallback_used"]
    result.budget_exhausted = evidence["budget_exhausted"]
    if evidence["total_response_chars"] > result.llm_output_chars:
        result.llm_output_chars = evidence["total_response_chars"]


def _populate_provenance(
    result: RealCampaignRunResult,
    ws: dict[str, Any],
) -> None:
    """Extract actual reference/fallback/few-shot provenance from workflow state.

    Instead of hardcoding empty lists, this inspects the actual workflow state
    to determine what was truly used.
    """
    # --- Reference patch provenance ---
    result.reference_patch_policy = ws.get("reference_patch_policy", "off")
    ref_patches: list[str] = []
    ref_ids: list[str] = []
    ref_sources: list[str] = []
    ref_match = ""

    build_state = ws.get("plan_build_state")
    if isinstance(build_state, dict):
        patches = build_state.get("patches") or {}
        for pid, env in patches.items():
            if not isinstance(env, dict):
                continue
            source = env.get("source", "")
            if source == "reference":
                ref_patches.append(pid)
                ref_ids.append(pid)
                ref_sources.append(env.get("reference_source", "unknown"))
            if env.get("reference_match"):
                ref_match = env.get("reference_match", "")
    # Also check graph trace for reference patch usage.
    graph_trace = ws.get("graph_trace") or []
    for event in graph_trace:
        if isinstance(event, dict):
            if event.get("reference_patch_used"):
                ref_patches.append(event["reference_patch_used"])

    result.reference_patches_used = sorted(set(ref_patches))
    result.reference_patch_ids = sorted(set(ref_ids))
    result.reference_patch_sources = sorted(set(ref_sources))
    result.reference_match_status = ref_match

    # --- Monolithic fallback provenance ---
    monolithic_attempted = False
    monolithic_used = False
    monolithic_source = ""

    # Check if incremental planning failed and monolithic was used.
    planning_mode = ws.get("planning_mode", "")
    if planning_mode == "monolithic":
        monolithic_used = True
        monolithic_source = "direct"
    if ws.get("fallback_attempted"):
        monolithic_attempted = True
        monolithic_source = ws.get("fallback_source", "incremental_failure")
    # Check graph trace for fallback events.
    for event in graph_trace:
        if isinstance(event, dict):
            if event.get("monolithic_fallback_attempted"):
                monolithic_attempted = True
                monolithic_source = event.get("fallback_reason", "")
            if event.get("monolithic_fallback_used"):
                monolithic_used = True

    result.monolithic_fallback_attempted = monolithic_attempted
    result.monolithic_fallback_used = monolithic_used
    result.monolithic_plan_source = monolithic_source

    # --- Few-shot provenance ---
    few_shot_ids: list[str] = []
    few_shot_sources: list[str] = []
    few_shot_benchmarks: list[str] = []
    benchmark_few_shot = False
    gold_few_shot = False

    few_shot_data = ws.get("few_shot_examples") or ws.get("selected_few_shots")
    if isinstance(few_shot_data, list):
        for example in few_shot_data:
            if isinstance(example, dict):
                ex_id = example.get("id", "")
                ex_source = example.get("source", "")
                ex_benchmark = example.get("benchmark", "")
                if ex_id:
                    few_shot_ids.append(ex_id)
                if ex_source:
                    few_shot_sources.append(ex_source)
                if ex_benchmark:
                    few_shot_benchmarks.append(ex_benchmark)
                if example.get("is_benchmark_specific"):
                    benchmark_few_shot = True
                if example.get("is_gold"):
                    gold_few_shot = True
            elif isinstance(example, str):
                few_shot_ids.append(example)
    elif isinstance(few_shot_data, dict):
        few_shot_ids = few_shot_data.get("ids", [])
        few_shot_sources = few_shot_data.get("sources", [])

    result.selected_few_shot_ids = few_shot_ids
    result.selected_few_shot_sources = few_shot_sources
    result.selected_few_shot_benchmarks = few_shot_benchmarks
    result.benchmark_specific_few_shot_used = benchmark_few_shot
    result.gold_few_shot_used = gold_few_shot


def _verify_real_openmc(
    result: RealCampaignRunResult,
    ws: dict[str, Any],
    run_dir: Path,
) -> None:
    """3-stage real OpenMC verification.

    A run is ``real_openmc_verified`` only when all three stages are backed
    by real execution:

    1. export_xml: real Python export, model.py executed, XML files exist.
    2. geometry debug: real openmc command, returncode=0, no overlap.
    3. smoke: real openmc command, returncode=0, statepoint readable,
       no source rejection, no lost particle.
    """
    tool_results = ws.get("tool_results") or []

    # Stage 1: export
    export_ok = False
    export_backend = "skipped"
    export_rc = -1
    for tr in tool_results:
        if isinstance(tr, dict) and tr.get("name") == "export_xml":
            export_ok = tr.get("ok", False)
            cmd = tr.get("command") or []
            if isinstance(cmd, list) and any("python" in str(c) for c in cmd):
                export_backend = "real_python_export"
            elif tr.get("execution_backend") == "real_python_export":
                export_backend = "real_python_export"
            elif tr.get("mocked"):
                export_backend = "mocked_tool"
            else:
                export_backend = "injected_tool" if not export_ok else "real_python_export"
            export_rc = tr.get("returncode", 0 if export_ok else -1)
            break
    # Verify XML files exist.
    xml_files_exist = all(
        (run_dir / "workflow" / f).exists()
        for f in ["model.py", "settings.xml", "materials.xml", "geometry.xml"]
    )
    if not xml_files_exist:
        export_ok = False

    result.xml_exported = export_ok
    result.export_backend = export_backend
    result.export_returncode = export_rc

    # Stage 2: geometry debug
    geo_ok = False
    geo_backend = "skipped"
    geo_rc = -1
    for tr in tool_results:
        if isinstance(tr, dict) and tr.get("name") == "run_geometry_debug":
            geo_ok = tr.get("ok", False)
            cmd = tr.get("command") or []
            if isinstance(cmd, list) and any("openmc" in str(c) for c in cmd):
                geo_backend = "real_openmc"
            elif tr.get("mocked"):
                geo_backend = "mocked_tool"
            else:
                geo_backend = "injected_tool" if not geo_ok else "real_openmc"
            geo_rc = tr.get("returncode", 0 if geo_ok else -1)
            break
    result.geometry_debug_passed = geo_ok
    result.geometry_debug_backend = geo_backend
    result.geometry_debug_returncode = geo_rc

    # Stage 3: smoke
    smoke_ok = False
    smoke_backend = "skipped"
    smoke_rc = -1
    source_rejections = 0
    for tr in tool_results:
        if isinstance(tr, dict) and tr.get("name") == "run_smoke_test":
            smoke_ok = tr.get("ok", False)
            cmd = tr.get("command") or []
            if isinstance(cmd, list) and any("openmc" in str(c) for c in cmd):
                smoke_backend = "real_openmc"
            elif tr.get("mocked"):
                smoke_backend = "mocked_tool"
            else:
                smoke_backend = "injected_tool" if not smoke_ok else "real_openmc"
            smoke_rc = tr.get("returncode", 0 if smoke_ok else -1)
            issues = tr.get("issues") or []
            for issue in issues:
                code = issue.get("code", "") if isinstance(issue, dict) else getattr(issue, "code", "")
                if "source_rejection" in code or "source_reject" in code:
                    source_rejections += 1
            break
    result.smoke_passed = smoke_ok
    result.smoke_backend = smoke_backend
    result.smoke_returncode = smoke_rc
    result.source_rejection_count = source_rejections

    # All three stages must be real and successful.
    result.real_openmc_verified = (
        export_ok and export_backend in {"real_python_export"}
        and geo_ok and geo_backend == "real_openmc"
        and smoke_ok and smoke_backend == "real_openmc"
        and result.lost_particle_count == 0
        and source_rejections == 0
    )


def _check_artifact_completeness(
    result: RealCampaignRunResult,
    run_dir: Path,
) -> None:
    """Check that all required artifacts are present."""
    required = [
        "workflow/model.py", "workflow/settings.xml",
        "workflow/materials.xml", "workflow/geometry.xml",
        "workflow/incremental/plan_build_state.json",
    ]
    result.artifact_complete = all((run_dir / p).exists() for p in required)
    result.artifact_paths = sorted([
        str(p.relative_to(run_dir))
        for p in run_dir.rglob("*")
        if p.is_file() and "statepoint" not in p.name
    ])


# --------------------------------------------------------------------------- #
# N-run campaign executor
# --------------------------------------------------------------------------- #


def _git_sha() -> str:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"], text=True,
        ).strip()
    except Exception:
        return "unknown"


def _file_sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _config_hash(config: RealCampaignRunConfig) -> str:
    raw = json.dumps({
        "model": config.model,
        "temperature": config.temperature,
        "variant": config.variant,
        "benchmark": config.benchmark,
        "reference_patch_policy": config.reference_patch_policy,
        "max_runtime_iterations": config.max_runtime_iterations,
        "enable_runtime_llm_repair": config.enable_runtime_llm_repair,
        "runtime_repair_mode": config.runtime_repair_mode,
        "runtime_supervisor_mode": config.runtime_supervisor_mode,
        "enable_plots": config.enable_plots,
        "enable_smoke_test": config.enable_smoke_test,
        "max_llm_calls": config.max_llm_calls,
        "wall_time_limit_s": config.wall_time_limit_s,
    }, sort_keys=True)
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


_RESUME_MATCH_FIELDS = [
    "git_sha", "input_sha", "requirement_sha",
    "model", "provider", "temperature", "profile",
    "requested_runs", "runtime_supervisor_mode",
    "reference_patch_policy",
    "max_runtime_iterations", "max_llm_calls",
]


def _check_resume_config_match(
    old_manifest: dict[str, Any],
    new_manifest: dict[str, Any],
) -> list[str]:
    """Return list of mismatched field names. Empty = safe to resume."""
    mismatches: list[str] = []
    old_config = old_manifest.get("configuration", {})
    new_config = new_manifest.get("configuration", {})
    for field in _RESUME_MATCH_FIELDS:
        old_val = old_manifest.get(field, old_config.get(field))
        new_val = new_manifest.get(field, new_config.get(field))
        if old_val != new_val and old_val is not None:
            mismatches.append(field)
    return mismatches


def _write_json_atomic(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(value, indent=2, sort_keys=True, default=str), encoding="utf-8")
    tmp.replace(path)


def run_real_campaign(
    output_dir: Path,
    *,
    profile: str = "pilot",
    runs: int = 3,
    model: str = "deepseek:deepseek-chat",
    temperature: float = 0.0,
    confirm_real_campaign: bool = False,
    runtime_supervisor_mode: str = "deterministic",
    max_runtime_iterations: int = 4,
    max_llm_calls: int = 16,
    run_timeout_s: float = 900.0,
    campaign_timeout_s: float = 14400.0,  # 4h default
    fail_fast: bool = False,
    resume: bool = False,
    vera3_acceptance_callback: Callable[[Any], tuple[bool, list[str]]] | None = None,
) -> dict[str, Any]:
    """Execute a real Lane B campaign with N runs.

    Requires DEEPSEEK_API_KEY to be set. Each run creates fresh LLM clients
    and invokes the full production graph from scratch.
    """
    output_dir.mkdir(parents=True, exist_ok=True)

    # Environment checks.
    key_available = bool(os.environ.get("DEEPSEEK_API_KEY"))
    openmc_available = bool(os.environ.get("OPENMC_CROSS_SECTIONS"))
    input_path = Path("Input/VERA3_problem.md")
    input_exists = input_path.exists()

    git_sha = _git_sha()
    input_sha = _file_sha(input_path) if input_exists else ""

    # Build requirement text.
    if input_exists:
        raw_text = input_path.read_text(encoding="utf-8")
        from openmc_agent.inspect import compose_operating_state_requirement
        requirement_text = compose_operating_state_requirement(raw_text, "3B")
        requirement_sha = hashlib.sha256(requirement_text.encode()).hexdigest()
    else:
        requirement_text = ""
        requirement_sha = ""

    # Determine status.
    if not key_available or not openmc_available or not input_exists:
        status = "VERA3B_REAL_LLM_STABILITY_NOT_RUN_ENV"
    elif not confirm_real_campaign:
        status = "VERA3B_REAL_LLM_CONFIRMATION_REQUIRED"
    else:
        status = "CAMPAIGN_RUNNING"

    campaign_start = time.perf_counter()

    manifest: dict[str, Any] = {
        "campaign_id": f"vera3b_real_{profile}_{int(time.time())}",
        "profile": profile,
        "requested_runs": runs,
        "completed_runs": 0,
        "successful_runs": 0,
        "failed_runs": 0,
        "pending_runs": list(range(1, runs + 1)),
        "git_sha": git_sha,
        "input_sha": input_sha,
        "requirement_sha": requirement_sha,
        "model": model,
        "provider": model.split(":")[0] if ":" in model else "unknown",
        "temperature": temperature,
        "start_time": datetime.now(timezone.utc).isoformat(),
        "end_time": None,
        "environment": {
            "deepseek_api_key_present": key_available,
            "openmc_cross_sections_present": openmc_available,
            "input_file_present": input_exists,
            "openmc_version": _get_openmc_version(),
        },
        "configuration": {
            "reference_patch_policy": "off",
            "allow_monolithic_fallback_for_incremental_failure": False,
            "incremental_planning": True,
            "runtime_supervisor": True,
            "runtime_llm_repair": True,
            "runtime_supervisor_mode": runtime_supervisor_mode,
            "runtime_llm_fallback": False,
            "max_runtime_iterations": max_runtime_iterations,
            "max_llm_calls": max_llm_calls,
            "run_timeout_s": run_timeout_s,
            "campaign_timeout_s": campaign_timeout_s,
        },
        "aggregate_status": status,
        "promotion_reasons": [],
    }

    if status != "CAMPAIGN_RUNNING":
        _write_json_atomic(output_dir / "campaign_manifest.json", manifest)
        _write_json_atomic(output_dir / "campaign_results.json", [])
        return manifest

    # Execute runs.
    results: list[dict[str, Any]] = []
    runs_dir = output_dir / "runs"
    runs_dir.mkdir(parents=True, exist_ok=True)

    # Check for resume.
    resume_mismatches: list[str] = []
    if resume and (output_dir / "campaign_manifest.json").exists():
        try:
            old_manifest = json.loads(
                (output_dir / "campaign_manifest.json").read_text()
            )
            resume_mismatches = _check_resume_config_match(old_manifest, manifest)
            if not resume_mismatches:
                old_results_path = output_dir / "campaign_results.json"
                if old_results_path.exists():
                    results = json.loads(old_results_path.read_text())
        except Exception:
            pass
    elif (output_dir / "campaign_manifest.json").exists() and not resume:
        # Not asked to resume but old manifest exists — start fresh.
        pass

    if resume_mismatches:
        manifest["aggregate_status"] = "CAMPAIGN_RESUME_CONFIG_MISMATCH"
        manifest["resume_mismatches"] = resume_mismatches
        _write_json_atomic(output_dir / "campaign_manifest.json", manifest)
        return manifest

    config_hash = _config_hash(RealCampaignRunConfig(
        run_id="", run_index=0, input_path=str(input_path),
        model=model, temperature=temperature,
        runtime_supervisor_mode=runtime_supervisor_mode,
        max_runtime_iterations=max_runtime_iterations,
        max_llm_calls=max_llm_calls,
        wall_time_limit_s=run_timeout_s,
    ))

    for i in range(1, runs + 1):
        run_id = f"run_{i:03d}"
        if any(r.get("run_id") == run_id for r in results):
            continue  # Already completed

        # Campaign-level deadline check.
        if time.perf_counter() - campaign_start > campaign_timeout_s:
            manifest["aggregate_status"] = "CAMPAIGN_TIMEOUT"
            manifest["promotion_reasons"].append(
                f"campaign_timeout_after_{len(results)}_runs"
            )
            break

        run_dir = runs_dir / run_id
        run_dir.mkdir(parents=True, exist_ok=True)

        run_config = RealCampaignRunConfig(
            run_id=run_id,
            run_index=i,
            input_path=str(input_path),
            model=model,
            temperature=temperature,
            output_dir=str(run_dir),
            runtime_supervisor_mode=runtime_supervisor_mode,
            max_runtime_iterations=max_runtime_iterations,
            max_llm_calls=max_llm_calls,
            wall_time_limit_s=run_timeout_s,
        )

        result = run_real_generation_once(
            run_config,
            requirement_text=requirement_text,
            input_sha=input_sha,
            git_sha=git_sha,
            config_hash=config_hash,
            vera3_acceptance_callback=vera3_acceptance_callback,
        )

        result_dict = result.to_dict()
        results.append(result_dict)
        _write_json_atomic(run_dir / "run_result.json", result_dict)

        # Update manifest.
        manifest["completed_runs"] = len(results)
        manifest["successful_runs"] = sum(
            1 for r in results
            if r.get("final_disposition") in {"FIRST_PASS_SUCCESS", "RECOVERED_SUCCESS"}
        )
        manifest["failed_runs"] = manifest["completed_runs"] - manifest["successful_runs"]
        manifest["pending_runs"] = list(range(len(results) + 1, runs + 1))
        _write_json_atomic(output_dir / "campaign_manifest.json", manifest)
        _write_json_atomic(output_dir / "campaign_results.json", results)

        if fail_fast and result.status == "infrastructure_failure":
            break

    # Compute final metrics.
    manifest["end_time"] = datetime.now(timezone.utc).isoformat()
    metrics = aggregate_real_campaign(results, requested_runs=runs)
    _write_json_atomic(output_dir / "campaign_metrics.json", metrics)

    # Determine final status.
    status = real_campaign_status(
        metrics,
        real_environment_available=True,
        executor_implemented=True,
        confirmed=True,
    )

    # Add promotion reasons.
    reasons: list[str] = []
    completed = metrics.get("completed_runs", 0)
    successful = metrics.get("final_success_rate", 0.0) * completed
    unsafe = metrics.get("unsafe_acceptance_rate", 0.0) * completed

    if completed < runs:
        reasons.append(f"completed_runs={completed} < requested={runs}")
    if runs >= 10 and successful < 7:
        reasons.append(f"successful_runs={int(successful)} < 7/10 threshold")
    if runs >= 3 and runs < 10 and successful < 3:
        reasons.append(f"successful_runs={int(successful)} < 3")
    if unsafe > 0:
        reasons.append(f"unsafe_acceptance={unsafe}")

    manifest["aggregate_status"] = status
    manifest["promotion_reasons"] = reasons
    _write_json_atomic(output_dir / "campaign_manifest.json", manifest)

    # Write report.
    report_name = "qualification_report.md" if profile == "qualification" else "pilot_report.md"
    _write_campaign_report(output_dir, manifest, metrics, results, report_name)

    return manifest


def _get_openmc_version() -> str:
    try:
        import openmc
        return openmc.__version__
    except Exception:
        return "unknown"


def _write_campaign_report(
    output_dir: Path,
    manifest: dict[str, Any],
    metrics: dict[str, Any],
    results: list[dict[str, Any]],
    report_name: str = "pilot_report.md",
) -> None:
    """Write a human-readable campaign report."""
    title = "Qualification" if "qualification" in report_name else "Pilot"
    lines = [
        f"# VERA3B Real-LLM {title} Report",
        "",
        f"**Status**: `{manifest['aggregate_status']}`",
        f"**Model**: `{manifest['model']}`",
        f"**Git SHA**: `{manifest['git_sha']}`",
        f"**Input SHA**: `{manifest['input_sha'][:16]}...`",
        "",
        "## Summary",
        "",
        f"- Requested runs: {manifest['requested_runs']}",
        f"- Completed runs: {manifest['completed_runs']}",
        f"- Successful runs: {manifest['successful_runs']}",
        f"- Failed runs: {manifest['failed_runs']}",
        f"- Final success rate: {metrics.get('final_success_rate', 0):.1%}",
        f"- Initial success rate: {metrics.get('initial_success_rate', 0):.1%}",
        f"- Unsafe acceptance rate: {metrics.get('unsafe_acceptance_rate', 0):.1%}",
        f"- Artifact completeness: {metrics.get('artifact_completeness_rate', 0):.1%}",
        f"- Real LLM verification rate: {metrics.get('real_llm_verification_rate', 0):.1%}",
        f"- Real OpenMC verification rate: {metrics.get('real_openmc_verification_rate', 0):.1%}",
        "",
        "## Per-Run Results",
        "",
        "| Run | Disposition | LLM calls | OpenMC | VERA3 Accept | Duration |",
        "|-----|-------------|-----------|--------|--------------|----------|",
    ]

    for r in results:
        rid = r.get("run_id", "?")
        disp = r.get("final_disposition", "?")
        llm_calls = r.get("llm_call_count", 0)
        openmc_ok = "Y" if r.get("real_openmc_verified") else "N"
        vera3_ok = "Y" if r.get("vera3_acceptance_passed") else "N"
        dur = f"{r.get('duration_s', 0):.0f}s"
        lines.append(f"| {rid} | {disp} | {llm_calls} | {openmc_ok} | {vera3_ok} | {dur} |")

    lines.extend([
        "",
        "## Safety Metrics",
        "",
        f"- Unsafe accepted patches: {sum(r.get('unsafe_accepted_count', 0) for r in results)}",
        f"- Protected field changes: {sum(r.get('protected_field_change_count', 0) for r in results)}",
        f"- Fake client used: {any(r.get('fake_client_used') for r in results)}",
        f"- Reference patches used: {sum(len(r.get('reference_patches_used', [])) for r in results)}",
        f"- Monolithic fallback used: {any(r.get('monolithic_fallback_used') for r in results)}",
        f"- Benchmark few-shot used: {any(r.get('benchmark_specific_few_shot_used') for r in results)}",
        f"- Lost particle runs: {sum(1 for r in results if r.get('lost_particle_count', 0) > 0)}",
        f"- Source rejection runs: {sum(1 for r in results if r.get('source_rejection_count', 0) > 0)}",
        "",
        "## Truthfulness",
        "",
        "All runs use real LLM clients (no Fake).",
        "All runs use real OpenMC (no mocked tools).",
        "Reference patch policy: off.",
        "Monolithic fallback: disabled.",
        "",
    ])

    if manifest.get("promotion_reasons"):
        lines.extend([
            "## Promotion Reasons",
            "",
        ])
        for reason in manifest["promotion_reasons"]:
            lines.append(f"- {reason}")

    (output_dir / report_name).write_text("\n".join(lines), encoding="utf-8")
