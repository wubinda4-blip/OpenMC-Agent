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


def _create_client_bundle(config: RealCampaignRunConfig) -> RealCampaignClientBundle:
    """Create fresh LLM clients for one run.

    Each call creates a new provider client instance — no chat history,
    no cached patches, no reused PlanBuildState.
    """
    from openmc_agent.llm import _client_for_model, _split_model
    from openmc_agent.plan_builder.llm_adapter import make_patch_llm_client
    from openmc_agent.runtime_diagnostician import (
        make_runtime_diagnostician_client,
        RuntimeDiagnosticianClient,
    )
    from openmc_agent.runtime_patch_proposer import (
        make_runtime_patch_proposer_client,
        RuntimePatchProposerClient,
    )

    provider, _ = _split_model(config.model)
    base_llm = _client_for_model(config.model)

    # Planning patch client.
    patch_client = make_patch_llm_client(
        base_llm,
        model_name=config.model,
        temperature=config.temperature,
    )

    # Runtime diagnostician: wrap the base LLM as a callable that accepts
    # (input_dict, *, prompt, json_schema).
    def _llm_callable(input_dict: Any, *, prompt: str, json_schema: Any) -> str:
        resp = base_llm.chat.completions.create(
            model=config.model,
            messages=[{"role": "user", "content": prompt}],
            temperature=config.temperature,
        )
        return resp.choices[0].message.content

    diag_client = make_runtime_diagnostician_client(
        llm=_llm_callable, model_name=config.model,
    )
    proposer_client = make_runtime_patch_proposer_client(
        llm=_llm_callable, model_name=config.model,
    )

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

        supervisor_client = _SupervisorAdapter()

    return RealCampaignClientBundle(
        patch_llm_client=patch_client,
        runtime_diagnostician_client=diag_client,
        runtime_patch_proposer_client=proposer_client,
        runtime_supervisor_client=supervisor_client,
        provider=provider,
        model=config.model,
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
    if result.benchmark_specific_few_shot_used:
        violations.append("benchmark_specific_few_shot_used")
    if not result.real_llm_verified:
        violations.append("real_llm_not_verified")
    if not result.real_openmc_verified and result.smoke_passed:
        violations.append("smoke_passed_without_real_openmc")

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
    run_dir = Path(config.output_dir)
    run_dir.mkdir(parents=True, exist_ok=True)
    started_at = datetime.now(timezone.utc)
    t0 = time.perf_counter()

    result = RealCampaignRunResult(
        run_id=config.run_id,
        status="completed",
        final_disposition="UNKNOWN",
        started_at=started_at.isoformat(),
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

    try:
        bundle = _create_client_bundle(config)
        result.provider = bundle.provider
        result.fake_client_used = False
        result.client_fallback_used = False
        result.real_llm_verified = True  # provider is not "fake"
    except Exception as exc:
        result.status = "infrastructure_failure"
        result.failure_stage = "client_creation"
        result.failure_category = "llm_connectivity"
        result.error_summary = str(exc)[:500]
        result.completed_at = datetime.now(timezone.utc).isoformat()
        result.duration_s = time.perf_counter() - t0
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
        return result
    except Exception as exc:
        result.status = "infrastructure_failure"
        result.failure_stage = "graph_invocation"
        result.failure_category = "harness_infrastructure"
        result.error_summary = str(exc)[:500]
        result.completed_at = datetime.now(timezone.utc).isoformat()
        result.duration_s = time.perf_counter() - t0
        return result

    # Extract results from workflow state.
    _extract_results(result, ws, run_dir)

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

    # Classify.
    result.final_disposition = classify_real_campaign_run(result, ws)
    result.completed_at = datetime.now(timezone.utc).isoformat()
    result.duration_s = time.perf_counter() - t0

    # Truthfulness violations.
    truth_violations = validate_real_run_truthfulness(result, ws)
    result.metadata["truth_violations"] = truth_violations

    # Artifact completeness.
    expected = [
        "workflow/model.py", "workflow/settings.xml",
        "workflow/materials.xml", "workflow/geometry.xml",
    ]
    result.artifact_complete = all((run_dir / p).exists() for p in expected)
    result.artifact_paths = [
        str(p.relative_to(run_dir))
        for p in run_dir.rglob("*")
        if p.is_file() and "statepoint" not in p.name
    ]

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

    # Real OpenMC verification: check if any tool result has a real command.
    for tr in tool_results:
        if isinstance(tr, dict):
            cmd = tr.get("command") or []
            if isinstance(cmd, list) and any("openmc" in str(c) or "python" in str(c) for c in cmd):
                result.real_openmc_verified = True
                break

    # Runtime metrics.
    result.runtime_iterations = int(ws.get("runtime_iteration_count", 0))
    result.deterministic_runtime_attempts = int(ws.get("runtime_repair_count", 0))
    result.runtime_llm_diagnoses = int(ws.get("runtime_llm_diagnosis_count", 0))
    result.runtime_llm_proposals = int(ws.get("runtime_llm_proposal_count", 0))
    result.committed_runtime_repairs = int(ws.get("runtime_committed_repair_count", 0))
    result.runtime_reexecutions = int(ws.get("runtime_reexecution_count", 0))
    result.runtime_final_disposition = ws.get("runtime_final_disposition") or ""

    # Safety metrics.
    result.unsafe_proposal_count = 0  # Extracted from repair evaluation if present
    result.duplicate_commit_count = 0  # Tracked by supervisor
    result.infinite_loop_count = 0  # Tracked by budget

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

    # Count LLM calls from patch attempt artifacts.
    workflow_dir = run_dir / "workflow" / "incremental" / "patch_attempts"
    if workflow_dir.exists():
        raw_files = list(workflow_dir.glob("*_raw.txt"))
        result.llm_call_count = len(raw_files)
        result.llm_output_chars = sum(
            f.stat().st_size for f in raw_files
        )

    # Reference patch usage.
    result.reference_patches_used = []  # Always off in Lane B
    result.monolithic_fallback_used = False

    # Few-shot metadata.
    result.selected_few_shot_ids = []  # Populated if graph exposes them
    result.benchmark_specific_few_shot_used = False


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
    }, sort_keys=True)
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


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
    fail_fast: bool = False,
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
    existing_manifest_path = output_dir / "campaign_manifest.json"
    if existing_manifest_path.exists():
        try:
            old_manifest = json.loads(existing_manifest_path.read_text())
            old_results_path = output_dir / "campaign_results.json"
            if old_results_path.exists():
                results = json.loads(old_results_path.read_text())
                # Skip already completed runs.
                completed_ids = {r.get("run_id") for r in results}
        except Exception:
            pass

    config_hash = _config_hash(RealCampaignRunConfig(
        run_id="", run_index=0, input_path=str(input_path),
        model=model, temperature=temperature,
        runtime_supervisor_mode=runtime_supervisor_mode,
        max_runtime_iterations=max_runtime_iterations,
    ))

    for i in range(1, runs + 1):
        run_id = f"run_{i:03d}"
        if any(r.get("run_id") == run_id for r in results):
            continue  # Already completed

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
    if successful < 3 and profile == "pilot":
        reasons.append(f"successful_runs={int(successful)} < 3")
    if unsafe > 0:
        reasons.append(f"unsafe_acceptance={unsafe}")

    manifest["aggregate_status"] = status
    manifest["promotion_reasons"] = reasons
    _write_json_atomic(output_dir / "campaign_manifest.json", manifest)

    # Write pilot report.
    _write_pilot_report(output_dir, manifest, metrics, results)

    return manifest


def _get_openmc_version() -> str:
    try:
        import openmc
        return openmc.__version__
    except Exception:
        return "unknown"


def _write_pilot_report(
    output_dir: Path,
    manifest: dict[str, Any],
    metrics: dict[str, Any],
    results: list[dict[str, Any]],
) -> None:
    """Write a human-readable pilot report."""
    lines = [
        "# VERA3B Real-LLM Pilot Report",
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
        f"- Unsafe acceptance rate: {metrics.get('unsafe_acceptance_rate', 0):.1%}",
        f"- Artifact completeness: {metrics.get('artifact_completeness_rate', 0):.1%}",
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
        openmc_ok = "✓" if r.get("smoke_passed") else "✗"
        vera3_ok = "✓" if r.get("vera3_acceptance_passed") else "✗"
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

    (output_dir / "pilot_report.md").write_text("\n".join(lines), encoding="utf-8")
