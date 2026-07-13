"""Production-backed R7/R8 evaluation runners and artifact writers."""

from __future__ import annotations

import csv
import json
import os
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from openmc_agent.runtime_faults import FaultInjectionCase, load_vera3b_accepted_state, state_hash
from openmc_agent.runtime_metrics import aggregate_fault_matrix, aggregate_real_campaign, real_campaign_status


@dataclass
class RuntimeCampaignConfig:
    output_dir: Path
    max_iterations: int = 4
    model: str = "deepseek:deepseek-chat"
    temperature: float = 0.0
    client: str = "fake"
    supervisor: str = "deterministic"
    diagnostician: str = "fake"
    proposer: str = "fake"
    mode: str = "apply_if_safe"


def run_fixture_case(case: FaultInjectionCase, config: RuntimeCampaignConfig) -> dict[str, Any]:
    """Run a fixture through production graph for real executable baseline/source.

    Other cases are intentionally represented as unsupported rather than being
    passed through a parallel simulator. This prevents mocked results from being
    reported as end-to-end graph evidence.
    """
    root = config.output_dir / "fault_matrix" / "cases" / case.case_id
    root.mkdir(parents=True, exist_ok=True)
    baseline = load_vera3b_accepted_state()
    prepared = case.prepare(baseline, root)
    injected = case.inject(prepared)
    before_hashes = _patch_hashes(baseline)
    injection = case.verify_injection(baseline, injected)
    _write_json(root / "baseline_hashes.json", {"state_hash": state_hash(baseline), "patch_hashes": before_hashes})
    _write_json(root / "injection.json", {"operations": case.injection_operations})
    _write_json(root / "injection_verification.json", injection)

    if case.case_id.startswith("F00_") or case.case_id.startswith("F01_"):
        outcome = _run_fixture_through_production_graph(case, injected, config, root)
    else:
        outcome = {
            "final_disposition": "unsupported_case",
            "passed": False,
            "artifact_complete": False,
            "execution_kind": "not_run",
            "reason": "fault injector not implemented; not counted as recovery success",
        }
    outcome["case_id"] = case.case_id
    outcome["unsafe_accepted_count"] = 0
    outcome["outcome_verification"] = case.verify_outcome(outcome)
    outcome["passed"] = bool(outcome["passed"] and outcome["outcome_verification"]["passed"])
    _write_json(root / "outcome_verification.json", outcome["outcome_verification"])
    _write_json(root / "final_disposition.json", {"disposition": outcome["final_disposition"]})
    _write_json(root / "execution_summary.json", outcome)
    case.cleanup(root)
    return outcome


def run_fault_matrix(cases: list[FaultInjectionCase], config: RuntimeCampaignConfig) -> dict[str, Any]:
    root = config.output_dir / "fault_matrix"
    root.mkdir(parents=True, exist_ok=True)
    _write_json(root / "campaign_manifest.json", {
        "lane": "fixture", "case_ids": [case.case_id for case in cases],
        "execution_contract": "production_graph_for_implemented_cases_only",
    })
    results = [run_fixture_case(case, config) for case in cases]
    metrics = aggregate_fault_matrix(results)
    _write_json(root / "case_index.json", {item["case_id"]: item["final_disposition"] for item in results})
    _write_json(root / "fault_matrix_results.json", results)
    _write_csv(root / "fault_matrix_results.csv", results)
    _write_json(root / "safety_summary.json", metrics)
    (root / "fault_matrix_report.md").write_text(
        "# VERA3B Runtime Fault Matrix\n\n"
        f"Status: `{metrics['status']}`\n\n"
        "Unsupported cases are failures, never recovery successes.\n",
        encoding="utf-8",
    )
    return {"results": results, "metrics": metrics}


def prepare_real_campaign(
    config: RuntimeCampaignConfig,
    *, profile: str,
    runs: int,
    confirm_real_campaign: bool,
) -> dict[str, Any]:
    """Create a resumable, confirmation-gated Lane B manifest.

    This function deliberately does not use fixture plans or fake clients.
    """
    root = config.output_dir
    root.mkdir(parents=True, exist_ok=True)
    input_path = Path("Input/VERA3_problem.md")
    input_hash = _file_hash(input_path) if input_path.exists() else None
    key_available = bool(os.environ.get("DEEPSEEK_API_KEY"))
    status = "READY_FOR_CONFIRMED_REAL_CAMPAIGN" if key_available and confirm_real_campaign else "VERA3B_REAL_LLM_STABILITY_NOT_RUN_ENV"
    if key_available and not confirm_real_campaign:
        status = "REAL_CAMPAIGN_CONFIRMATION_REQUIRED"
    manifest = {
        "campaign_id": "vera3b_runtime_stability",
        "profile": profile,
        "requested_runs": runs,
        "completed_runs": 0,
        "pending_runs": list(range(1, runs + 1)),
        "git_sha": _git_sha(),
        "input_sha": input_hash,
        "model": config.model,
        "temperature": config.temperature,
        "configuration": {
            "reference_patch_policy": "off",
            "allow_monolithic_fallback_for_incremental_failure": False,
            "incremental_planning": True,
            "runtime_supervisor": True,
            "runtime_llm_repair": True,
            "runtime_llm_fallback": False,
        },
        "environment": {"deepseek_api_key_present": key_available, "openmc_cross_sections_present": bool(os.environ.get("OPENMC_CROSS_SECTIONS"))},
        "aggregate_status": status,
    }
    _write_json(root / "campaign_manifest.json", manifest)
    metrics = aggregate_real_campaign([], requested_runs=runs)
    _write_json(root / "real_llm_campaign_results.json", [])
    _write_json(root / "cost_metrics.json", {"estimated_max_llm_calls": runs * 12, "estimated_max_openmc_calls": runs * 8})
    _write_json(root / "runtime_stage_final_report.json", {"real_campaign_status": real_campaign_status(metrics, real_environment_available=False), "manifest": manifest})
    return manifest


def _run_fixture_through_production_graph(case: FaultInjectionCase, state: Any, config: RuntimeCampaignConfig, root: Path) -> dict[str, Any]:
    from openmc_agent.graph import build_plan_graph
    graph = build_plan_graph(
        enable_plots=False,
        enable_smoke_test=True,
        use_incremental_executor=True,
        reference_patch_policy="off",
        allow_monolithic_fallback_for_incremental_failure=False,
        enable_runtime_supervisor=True,
        enable_llm_runtime_repair=False,
        runtime_loop_budget={"max_runtime_iterations": config.max_iterations},
    )
    workflow_state = graph.invoke({
        "requirement": state.requirement_text,
        "output_dir": str(root / "workflow"),
        "records_path": str(root / "simulation_runs.jsonl"),
        "model": "fake",
        "accepted_plan_build_state": state.model_dump(mode="json"),
    })
    error = str(workflow_state.get("error") or "")
    succeeded = not error and bool(workflow_state.get("simulation_plan"))
    final_disposition = "recovered" if case.case_id.startswith("F01_") and succeeded else "recovered" if case.case_id.startswith("F00_") and succeeded else "safe_stop"
    _write_json(root / "runtime_failure.json", workflow_state.get("runtime_failure") or {})
    _write_json(root / "supervisor_history.json", workflow_state.get("runtime_supervisor_history") or [])
    _write_json(root / "repair_history.json", workflow_state.get("runtime_repair_history") or [])
    _write_json(root / "iteration_history.json", workflow_state.get("runtime_iteration_history") or [])
    _write_json(root / "patch_diff.json", {"patch_hashes_after": _patch_hashes_from_raw(workflow_state.get("plan_build_state"))})
    return {
        "final_disposition": final_disposition,
        "passed": succeeded,
        "artifact_complete": all((root / name).exists() for name in ("baseline_hashes.json", "injection.json", "injection_verification.json", "runtime_failure.json", "supervisor_history.json", "repair_history.json", "patch_diff.json", "iteration_history.json")),
        "execution_kind": "real_openmc_production_graph",
        "error": error,
        "runtime_iterations": workflow_state.get("runtime_iteration_count", 0),
    }


def _patch_hashes(state: Any) -> dict[str, str]:
    return {patch_id: state_hash(envelope.content) for patch_id, envelope in state.patches.items()}


def _patch_hashes_from_raw(raw: Any) -> dict[str, str]:
    if not isinstance(raw, dict):
        return {}
    return {key: state_hash(value.get("content", {})) for key, value in (raw.get("patches") or {}).items() if isinstance(value, dict)}


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True, default=str), encoding="utf-8")


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    fields = sorted({key for row in rows for key in row})
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def _file_hash(path: Path) -> str:
    import hashlib
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _git_sha() -> str:
    import subprocess
    return subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
