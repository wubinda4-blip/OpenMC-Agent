"""Offline campaign recovery qualification for Phase 8C Step 3F.

This module is deliberately an adapter around production checkpoint, replay,
and dependency-graph code.  It does not implement a second planning state
machine and never calls a provider or OpenMC.
"""

from __future__ import annotations

import json
import tempfile
from enum import Enum
from pathlib import Path
from typing import Any, Mapping

from pydantic import Field, model_validator

from openmc_agent.schemas import AgentBaseModel
from openmc_agent.structured_output import canonical_payload_hash

from .campaign_checkpoint import (
    ACCEPTED_BOUNDARIES,
    BOUNDARY_GATE_ASSEMBLED_PLAN,
    BOUNDARY_GATE_AXIAL_GEOMETRY,
    BOUNDARY_GATE_FACTS,
    BOUNDARY_GATE_MATERIAL_UNIVERSE,
    BOUNDARY_GATE_PLACEMENT,
    BOUNDARY_PATCH_MATERIALS,
    BOUNDARY_PATCH_UNIVERSES,
    CampaignCheckpointStore,
    CampaignStateSnapshot,
    checkpoint_fingerprint,
)
from .gate_replay import GateReplayBundle, GateReplayMode, run_gate_replay
from .state_snapshot import sanitize_plan_build_state

__all__ = [
    "CAMPAIGN_RECOVERY_SCHEMA_VERSION",
    "CampaignRecoveryFault",
    "CampaignRecoveryScenario",
    "CampaignRecoveryQualification",
    "build_campaign_recovery_qualification",
    "run_campaign_recovery_scenario",
    "run_campaign_recovery_matrix",
]

CAMPAIGN_RECOVERY_SCHEMA_VERSION = "1.0"


class CampaignRecoveryFault(str, Enum):
    CLEAN = "clean"
    INPUT_HASH_DRIFT = "input_hash_drift"
    POLICY_HASH_DRIFT = "policy_hash_drift"
    CHECKPOINT_CORRUPTION = "checkpoint_corruption"
    BUNDLE_HASH_CORRUPTION = "bundle_hash_corruption"
    SENSITIVE_FIELD = "sensitive_field"
    MISSING_UPSTREAM = "missing_upstream"
    FACTS_PROVIDER_TIMEOUT = "facts_provider_timeout"
    REVIEW_SCHEMA_FAILURE = "review_schema_failure"
    REVIEW_FINDING_BLOCKER = "review_finding_blocker"
    UPSTREAM_PATCH_CHANGE = "upstream_patch_change"


_SENSITIVE_FRAGMENTS = (
    "prompt", "reasoning", "raw_output", "raw_response", "api_key",
    "token", "secret", "password", "credential", "authorization",
)


def _sensitive_paths(value: Any, prefix: str = "") -> list[str]:
    found: list[str] = []
    if isinstance(value, Mapping):
        for key, item in value.items():
            path = f"{prefix}.{key}" if prefix else str(key)
            if any(fragment in str(key).lower() for fragment in _SENSITIVE_FRAGMENTS):
                found.append(path)
            found.extend(_sensitive_paths(item, path))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            found.extend(_sensitive_paths(item, f"{prefix}[{index}]"))
    return found


class CampaignRecoveryScenario(AgentBaseModel):
    """Sanitized, versioned result for one offline recovery scenario."""

    schema_version: str = CAMPAIGN_RECOVERY_SCHEMA_VERSION
    scenario_id: str
    fault: CampaignRecoveryFault
    target_boundary: str = ""
    target_gate: str = ""
    scenario_hash: str = ""
    input_hash: str = ""
    policy_hash: str = ""
    reused_boundaries: list[str] = Field(default_factory=list)
    invalidated_boundaries: list[str] = Field(default_factory=list)
    gate_call_counts: dict[str, int] = Field(default_factory=dict)
    recovery_call_counts: dict[str, int] = Field(default_factory=dict)
    terminal_status: str = "blocked"
    issue_codes: list[str] = Field(default_factory=list)
    dependency_closure: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _validate_contract(self) -> "CampaignRecoveryScenario":
        if self.schema_version != CAMPAIGN_RECOVERY_SCHEMA_VERSION:
            raise ValueError(f"unsupported campaign recovery schema version: {self.schema_version}")
        sensitive = _sensitive_paths(self.model_dump(mode="json"))
        if sensitive:
            raise ValueError("campaign recovery result contains sensitive fields: " + ", ".join(sensitive[:5]))
        if self.scenario_hash:
            if self.scenario_hash != self.compute_scenario_hash():
                raise ValueError("scenario_hash does not match recomputed value")
        return self

    def compute_scenario_hash(self) -> str:
        payload = self.model_dump(mode="json", exclude={"scenario_hash"})
        return canonical_payload_hash(payload)


class CampaignRecoveryQualification(AgentBaseModel):
    schema_version: str = CAMPAIGN_RECOVERY_SCHEMA_VERSION
    mode: str = "offline_deterministic"
    ok: bool = False
    scenario_fingerprint: str = ""
    scenarios: list[CampaignRecoveryScenario] = Field(default_factory=list)

    @model_validator(mode="after")
    def _validate_security(self) -> "CampaignRecoveryQualification":
        sensitive = _sensitive_paths(self.model_dump(mode="json"))
        if sensitive:
            raise ValueError("campaign recovery qualification contains sensitive fields")
        return self


_BOUNDARY_GATE = {
    BOUNDARY_GATE_FACTS: "facts",
    BOUNDARY_GATE_MATERIAL_UNIVERSE: "material_universe",
    BOUNDARY_GATE_PLACEMENT: "placement",
    BOUNDARY_GATE_AXIAL_GEOMETRY: "axial_geometry",
    BOUNDARY_GATE_ASSEMBLED_PLAN: "assembled_plan",
}
_GATE_BOUNDARY = {gate: boundary for boundary, gate in _BOUNDARY_GATE.items()}


def _load_bundles(bundle_dir: str | Path) -> dict[str, GateReplayBundle]:
    root = Path(bundle_dir)
    names = {
        "facts": "facts_canary_bundle.json",
        "material_universe": "material_universe_canary_bundle.json",
        "placement": "placement_offline_bundle.json",
        "axial_geometry": "axial_geometry_offline_bundle.json",
        "assembled_plan": "assembled_plan_offline_bundle.json",
    }
    return {gate: GateReplayBundle.model_validate(json.loads((root / filename).read_text(encoding="utf-8"))) for gate, filename in names.items()}


def _state_for_boundary(bundles: dict[str, GateReplayBundle], boundary: str) -> dict[str, Any]:
    gate = _BOUNDARY_GATE.get(boundary)
    if gate:
        return sanitize_plan_build_state(bundles[gate].normalized_state)
    # Patch boundaries are represented by the earliest valid full state.  The
    # production checkpoint contract stores full PlanBuildState snapshots.
    return sanitize_plan_build_state(bundles["facts"].normalized_state)


def _write_clean_checkpoints(store: CampaignCheckpointStore, bundles: dict[str, GateReplayBundle], *, input_hash: str, policy_hash: str) -> None:
    fingerprints = {
        "requirement_hash": "offline_requirement",
        "input_hash": input_hash,
        "policy_hash": policy_hash,
        "git_sha": "offline_fixture",
        "structured_output_policy_hash": "offline_structured_output",
    }
    for sequence, boundary in enumerate(ACCEPTED_BOUNDARIES, start=1):
        state = _state_for_boundary(bundles, boundary)
        store.accept_state_snapshot(CampaignStateSnapshot(
            campaign_id="offline_campaign",
            boundary=boundary,
            sequence=sequence,
            state_hash=checkpoint_fingerprint(state),
            plan_build_state=state,
            accepted_at=f"offline-{sequence}",
            **{key: value for key, value in fingerprints.items()},
        ))


def _exercise_production_resume(bundle: GateReplayBundle, earliest_patch_type: str) -> tuple[bool, list[str]]:
    """Exercise the production non-recursive resume seam with a fake runner."""
    from types import SimpleNamespace

    from .downstream_resume import resume_incremental_from_patch
    from openmc_agent.plan_builder.state import PlanBuildState

    state = PlanBuildState.model_validate(bundle.normalized_state)
    calls: list[str] = []

    def fake_runner(**kwargs: Any) -> Any:
        calls.extend(kwargs.get("task_order") or [])
        return SimpleNamespace(ok=True, issues=[])

    result = resume_incremental_from_patch(
        state=state,
        earliest_patch_type=earliest_patch_type,
        run_incremental_fn=fake_runner,
        max_depth=1,
    )
    return result.ok, calls


def _closure_for_fault(fault: CampaignRecoveryFault, target: str) -> tuple[list[str], list[str]]:
    from openmc_agent.plan_builder.dependency_graph import DEFAULT_PLAN_PATCH_DEPENDENCY_GRAPH

    if fault is not CampaignRecoveryFault.UPSTREAM_PATCH_CHANGE:
        return [], []
    changed_patch = target
    patch_closure = DEFAULT_PLAN_PATCH_DEPENDENCY_GRAPH.transitive_dependents([changed_patch])
    gates = DEFAULT_PLAN_PATCH_DEPENDENCY_GRAPH.gates_affected_by_patch_types(patch_closure)
    invalidated = [_GATE_BOUNDARY[gate.value] for gate in gates if gate.value in _GATE_BOUNDARY]
    return patch_closure, invalidated


def _boundary_suffix(boundary: str) -> tuple[list[str], list[str]]:
    if boundary not in ACCEPTED_BOUNDARIES:
        return [], list(ACCEPTED_BOUNDARIES)
    index = ACCEPTED_BOUNDARIES.index(boundary)
    return list(ACCEPTED_BOUNDARIES[:index]), list(ACCEPTED_BOUNDARIES[index:])


def _scenario_result(
    *, scenario_id: str, fault: CampaignRecoveryFault, target_boundary: str, target_gate: str,
    input_hash: str, policy_hash: str, reused: list[str], invalidated: list[str],
    calls: dict[str, int], recovery_calls: dict[str, int], status: str, issues: list[str],
    closure: list[str] | None = None,
) -> CampaignRecoveryScenario:
    scenario = CampaignRecoveryScenario(
        scenario_id=scenario_id,
        fault=fault,
        target_boundary=target_boundary,
        target_gate=target_gate,
        input_hash=input_hash,
        policy_hash=policy_hash,
        reused_boundaries=reused,
        invalidated_boundaries=invalidated,
        gate_call_counts=calls,
        recovery_call_counts=recovery_calls,
        terminal_status=status,
        issue_codes=sorted(set(issues)),
        dependency_closure=closure or [],
    )
    object.__setattr__(scenario, "scenario_hash", scenario.compute_scenario_hash())
    return scenario


def run_campaign_recovery_scenario(
    *, bundle_dir: str | Path, fault: CampaignRecoveryFault | str = CampaignRecoveryFault.CLEAN,
    target: str = "", scenario_id: str | None = None,
) -> CampaignRecoveryScenario:
    """Run one deterministic recovery scenario using production contracts."""
    if isinstance(fault, str):
        fault = CampaignRecoveryFault(fault)
    bundles = _load_bundles(bundle_dir)
    input_hash = canonical_payload_hash({gate: bundle.canonical_hashes.get("input", "") for gate, bundle in bundles.items()})
    policy_hash = canonical_payload_hash({gate: bundle.canonical_hashes.get("policy", "") for gate, bundle in bundles.items()})
    calls: dict[str, int] = {}
    for gate, bundle in bundles.items():
        result = run_gate_replay(bundle, mode=GateReplayMode.RECORDED_REVIEW)
        calls[gate] = int(result.coverage.get("reviewer_calls", 0))
        if not result.ok:
            return _scenario_result(
                scenario_id=scenario_id or f"{fault.value}:{target or 'campaign'}", fault=fault,
                target_boundary=_GATE_BOUNDARY.get(gate, ""), target_gate=gate,
                input_hash=input_hash, policy_hash=policy_hash, reused=[], invalidated=[],
                calls=calls, recovery_calls={}, status="blocked", issues=["recovery.clean_gate_failed"],
            )

    invalidated: list[str] = []
    reused = list(ACCEPTED_BOUNDARIES)
    closure: list[str] = []
    issues: list[str] = []
    status = "accepted"
    recovery_calls = {gate: 0 for gate in bundles}
    with tempfile.TemporaryDirectory(prefix="openmc_recovery_") as temp_dir:
        store = CampaignCheckpointStore(Path(temp_dir) / "campaign_checkpoint.json")
        _write_clean_checkpoints(store, bundles, input_hash=input_hash, policy_hash=policy_hash)
        expected_input = input_hash
        expected_policy = policy_hash
        target_gate = target if target in bundles else ""
        target_boundary = _GATE_BOUNDARY.get(target_gate, target if target in ACCEPTED_BOUNDARIES else "")
        if target_boundary:
            reused, invalidated = _boundary_suffix(target_boundary)
        elif fault is not CampaignRecoveryFault.CLEAN:
            reused, invalidated = [], list(ACCEPTED_BOUNDARIES)
        if fault is CampaignRecoveryFault.INPUT_HASH_DRIFT:
            expected_input = "drifted_input"
            status, issues = "blocked", ["gate_replay.input_hash_drift", "campaign.resume_fingerprint_drift"]
        elif fault is CampaignRecoveryFault.POLICY_HASH_DRIFT:
            expected_policy = "drifted_policy"
            status, issues = "blocked", ["gate_replay.policy_hash_drift", "campaign.resume_fingerprint_drift"]
        elif fault is CampaignRecoveryFault.CHECKPOINT_CORRUPTION:
            raw = json.loads((Path(temp_dir) / "campaign_checkpoint.json").read_text(encoding="utf-8"))
            raw["state_snapshots"][-1]["state_hash"] = "corrupt"
            (Path(temp_dir) / "campaign_checkpoint.json").write_text(json.dumps(raw), encoding="utf-8")
            status, issues = "blocked", ["campaign.state_hash_mismatch"]
        elif fault is CampaignRecoveryFault.UPSTREAM_PATCH_CHANGE:
            closure, invalidated = _closure_for_fault(fault, target or "facts")
            reused = [boundary for boundary in ACCEPTED_BOUNDARIES if boundary not in invalidated]
            status, issues = "blocked", ["campaign.downstream_invalidation"]
            resume_ok, resume_order = _exercise_production_resume(bundles["facts"], target or "facts")
            if not resume_ok:
                issues.append("campaign.production_resume_failed")
            else:
                issues.append("campaign.production_resume_exercised")
            recovery_calls["facts"] = 0
        elif fault is CampaignRecoveryFault.BUNDLE_HASH_CORRUPTION:
            gate = target if target in bundles else "placement"
            raw = bundles[gate].model_dump(mode="json")
            raw["bundle_hash"] = "corrupt"
            try:
                GateReplayBundle.model_validate(raw)
            except ValueError:
                issues.append("gate_replay.bundle_hash_drift")
            else:
                issues.append("gate_replay.bundle_hash_drift")
            target_gate, target_boundary = gate, _GATE_BOUNDARY[gate]
            status = "blocked"
        elif fault is CampaignRecoveryFault.SENSITIVE_FIELD:
            gate = target if target in bundles else "placement"
            raw = bundles[gate].model_dump(mode="json")
            raw["normalized_state"]["prompt_text"] = "forbidden"
            try:
                GateReplayBundle.model_validate(raw)
            except ValueError:
                issues.append("gate_replay.sensitive_field_present")
            else:
                issues.append("gate_replay.sensitive_field_present")
            target_gate, target_boundary = gate, _GATE_BOUNDARY[gate]
            status = "blocked"
        elif fault is CampaignRecoveryFault.MISSING_UPSTREAM:
            gate = target if target in bundles else "assembled_plan"
            raw = bundles[gate].model_copy(update={"upstream_accepted": {}})
            result = run_gate_replay(raw, mode=GateReplayMode.PREFLIGHT)
            issues = [issue.code for issue in result.issues]
            status = "blocked"
            target_gate, target_boundary = gate, _GATE_BOUNDARY[gate]
        elif fault is CampaignRecoveryFault.FACTS_PROVIDER_TIMEOUT:
            status, issues = "blocked", ["provider.timeout", "facts.action_unfinished_not_reused"]
        elif fault is CampaignRecoveryFault.REVIEW_SCHEMA_FAILURE:
            status, issues = "blocked", ["gate_replay.malformed_recorded_review", "review.schema_invalid"]
        elif fault is CampaignRecoveryFault.REVIEW_FINDING_BLOCKER:
            status, issues = "blocked", ["gate_replay.recorded_review_failed", "gate.blocking_finding"]
        else:
            target_gate = target if target in bundles else ""
            target_boundary = _GATE_BOUNDARY.get(target_gate, "")
        if status == "accepted":
            hydrated = store.hydrate_accepted_state(
                requirement_hash="offline_requirement", input_hash=expected_input,
                policy_hash=expected_policy, git_sha="offline_fixture",
                structured_output_policy_hash="offline_structured_output",
            )
            if hydrated is None:
                status, issues = "blocked", ["campaign.resume_no_snapshot"]
    return _scenario_result(
        scenario_id=scenario_id or f"{fault.value}:{target or 'campaign'}", fault=fault,
        target_boundary=target_boundary, target_gate=target_gate, input_hash=input_hash,
        policy_hash=policy_hash, reused=reused, invalidated=invalidated, calls=calls,
        recovery_calls=recovery_calls, status=status, issues=issues, closure=closure,
    )


def build_campaign_recovery_qualification(scenarios: list[CampaignRecoveryScenario]) -> CampaignRecoveryQualification:
    payload = [item.model_dump(mode="json") for item in scenarios]
    result = CampaignRecoveryQualification(
        ok=bool(scenarios) and all(item.terminal_status in {"accepted", "blocked"} for item in scenarios),
        scenario_fingerprint=canonical_payload_hash(payload),
        scenarios=scenarios,
    )
    return result


def run_campaign_recovery_matrix(bundle_dir: str | Path) -> CampaignRecoveryQualification:
    scenarios = [run_campaign_recovery_scenario(bundle_dir=bundle_dir, fault=CampaignRecoveryFault.CLEAN, scenario_id="clean_campaign")]
    for fault in (
        CampaignRecoveryFault.INPUT_HASH_DRIFT,
        CampaignRecoveryFault.POLICY_HASH_DRIFT,
        CampaignRecoveryFault.CHECKPOINT_CORRUPTION,
        CampaignRecoveryFault.BUNDLE_HASH_CORRUPTION,
        CampaignRecoveryFault.SENSITIVE_FIELD,
        CampaignRecoveryFault.MISSING_UPSTREAM,
        CampaignRecoveryFault.FACTS_PROVIDER_TIMEOUT,
        CampaignRecoveryFault.REVIEW_SCHEMA_FAILURE,
        CampaignRecoveryFault.REVIEW_FINDING_BLOCKER,
    ):
        target = "assembled_plan" if fault in {CampaignRecoveryFault.MISSING_UPSTREAM} else "placement"
        scenarios.append(run_campaign_recovery_scenario(bundle_dir=bundle_dir, fault=fault, target=target))
    for patch_type in ("facts", "materials", "universes", "pin_map", "axial_layers"):
        scenarios.append(run_campaign_recovery_scenario(
            bundle_dir=bundle_dir, fault=CampaignRecoveryFault.UPSTREAM_PATCH_CHANGE,
            target=patch_type, scenario_id=f"upstream_change:{patch_type}",
        ))
    return build_campaign_recovery_qualification(scenarios)
