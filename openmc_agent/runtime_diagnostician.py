"""Runtime LLM diagnostician: structured diagnosis of OpenMC runtime failures.

The diagnostician observes runtime failures and produces a structured
:class:`RuntimeDiagnosis`. It never modifies the plan directly. The diagnosis
is then deterministically validated by :func:`validate_runtime_diagnosis` to
narrow LLM-proposed permissions to a safe allowlist.

No LLM result is trusted without deterministic validation.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4

from pydantic import Field

from openmc_agent.runtime_feedback import RuntimeFailure, RuntimeFailureClass
from openmc_agent.runtime_repair_policy import RuntimeRepairPolicy, get_repair_policy
from openmc_agent.schemas import AgentBaseModel


# --------------------------------------------------------------------------- #
# Controlled enums
# --------------------------------------------------------------------------- #

RuntimeDiagnosisDisposition = Literal[
    "safe_to_propose",
    "diagnose_only",
    "ambiguous_owner",
    "insufficient_evidence",
    "renderer_bug",
    "environment_blocked",
    "human_fact_required",
    "transient_failure",
    "no_safe_repair",
    "invalid_diagnosis",
]

RepairKind = Literal[
    "reference_correction",
    "duplicate_reference_removal",
    "restore_existing_topology_constraint",
    "restore_existing_background_exclusion",
    "align_redundant_boundary_to_existing_value",
    "source_binding_implementation_bug",
    "renderer_implementation_bug",
    "environment_fix_required",
    "human_fact_required",
    "no_safe_repair",
]

EvidenceType = Literal[
    "runtime_log",
    "validation_issue",
    "rendered_object_map",
    "plan_excerpt",
    "patch_excerpt",
    "repair_policy",
    "error_catalog",
    "source_requirement",
    "code_retrieval",
    "openmc_documentation",
    "previous_attempt",
]


# --------------------------------------------------------------------------- #
# Data models
# --------------------------------------------------------------------------- #

class RuntimeDiagnosticEvidence(AgentBaseModel):
    evidence_id: str
    evidence_type: EvidenceType
    source: str = ""
    locator: str = ""
    summary: str = ""
    hard_evidence: bool = False
    confidence: float = Field(ge=0.0, le=1.0, default=1.0)
    metadata: dict[str, Any] = Field(default_factory=dict)


class RuntimeDiagnosis(AgentBaseModel):
    diagnosis_id: str
    failure_id: str
    primary_issue_code: str
    classification: str
    root_cause_summary: str = ""
    disposition: RuntimeDiagnosisDisposition = "no_safe_repair"
    target_patch_type: str | None = None
    target_patch_id: str | None = None
    target_object_ids: list[str] = Field(default_factory=list)
    target_patch_paths: list[str] = Field(default_factory=list)
    candidate_paths_rejected: list[str] = Field(default_factory=list)
    repair_kind: RepairKind = "no_safe_repair"
    evidence_refs: list[str] = Field(default_factory=list)
    missing_evidence: list[str] = Field(default_factory=list)
    contradictions: list[str] = Field(default_factory=list)
    expected_postconditions: list[str] = Field(default_factory=list)
    requires_human_confirmation: bool = False
    confidence: float = Field(ge=0.0, le=1.0, default=0.0)
    model: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class ValidatedRuntimeDiagnosis(AgentBaseModel):
    """Deterministic validation result of an LLM-produced diagnosis."""

    accepted: bool = False
    rejection_codes: list[str] = Field(default_factory=list)
    failure_id: str = ""
    primary_issue_code: str = ""
    target_patch_type: str | None = None
    target_patch_id: str | None = None
    deterministically_allowed_paths: list[str] = Field(default_factory=list)
    deterministically_forbidden_paths: list[str] = Field(default_factory=list)
    repair_kind: RepairKind = "no_safe_repair"
    risk_level: str = "unknown"
    proposal_allowed: bool = False
    reasons: list[str] = Field(default_factory=list)


# --------------------------------------------------------------------------- #
# Build diagnosis input (evidence package)
# --------------------------------------------------------------------------- #

def build_runtime_diagnosis_input(
    failure: RuntimeFailure,
    plan: Any,
    plan_build_state: Any,
    tool_results: list[dict[str, Any]],
    output_dir: str | Path | None = None,
    *,
    max_log_lines: int = 150,
    max_log_chars: int = 4000,
) -> dict[str, Any]:
    """Build a minimal evidence package for the LLM diagnostician.

    Does NOT include the full SimulationPlan. Includes only relevant excerpts,
    the current failure, policy constraints, and truncated logs.
    """
    from openmc_agent.runtime_repair_policy import get_repair_policy

    policy = get_repair_policy(failure.primary_issue_code)
    evidence_list: list[dict[str, Any]] = []

    # 1. RuntimeFailure summary.
    evidence_list.append({
        "evidence_id": "ev_failure",
        "evidence_type": "runtime_log",
        "source": "tool_result",
        "locator": failure.tool_name,
        "summary": failure.normalized_message[:500],
        "hard_evidence": True,
        "metadata": {
            "primary_issue_code": failure.primary_issue_code,
            "secondary_issue_codes": failure.secondary_issue_codes,
            "classification": failure.classification.value,
            "returncode": failure.returncode,
            "fingerprint": failure.error_fingerprint,
        },
    })

    # 2. Policy constraints.
    if policy is not None:
        evidence_list.append({
            "evidence_id": "ev_policy",
            "evidence_type": "repair_policy",
            "source": "runtime_repair_policy",
            "summary": f"Policy for {policy.issue_code}",
            "hard_evidence": True,
            "metadata": {
                "candidate_patch_types": policy.candidate_patch_types,
                "allowed_repair_kinds": policy.allowed_repair_kinds,
                "llm_proposal_supported": policy.llm_proposal_supported,
                "max_mutating_operations": policy.max_mutating_operations,
            },
        })

    # 3. Current target patch summaries (not full content).
    from openmc_agent.plan_builder.state import PlanBuildState

    bs: PlanBuildState | None = None
    if isinstance(plan_build_state, dict):
        try:
            bs = PlanBuildState.model_validate(plan_build_state)
        except Exception:
            pass
    elif isinstance(plan_build_state, PlanBuildState):
        bs = plan_build_state

    if bs is not None and policy is not None:
        for ptype in policy.candidate_patch_types:
            for env in bs.get_valid_patches(ptype):
                evidence_list.append({
                    "evidence_id": f"ev_patch_{env.patch_id}",
                    "evidence_type": "patch_excerpt",
                    "source": "plan_build_state",
                    "locator": env.patch_id,
                    "summary": f"{ptype} patch (status={env.status})",
                    "hard_evidence": True,
                    "metadata": {
                        "patch_type": ptype,
                        "top_level_keys": list(env.content.keys()),
                    },
                })

    # 4. Plan excerpt (minimal — just capability report and kind).
    if plan is not None:
        plan_dict = plan
        if hasattr(plan, "model_dump"):
            plan_dict = plan.model_dump(mode="json")
        cap = plan_dict.get("capability_report", {}) if isinstance(plan_dict, dict) else {}
        cm = plan_dict.get("complex_model", {}) if isinstance(plan_dict, dict) else {}
        evidence_list.append({
            "evidence_id": "ev_plan",
            "evidence_type": "plan_excerpt",
            "source": "simulation_plan",
            "summary": f"renderability={cap.get('renderability')}, "
                       f"kind={cm.get('kind') if isinstance(cm, dict) else None}",
            "hard_evidence": True,
            "metadata": {"capability": cap},
        })

    # 5. Rendered object map (if available).
    if output_dir is not None:
        obj_map_path = Path(output_dir) / "rendered_object_map.json"
        if obj_map_path.exists():
            try:
                obj_map = json.loads(obj_map_path.read_text(encoding="utf-8"))
                evidence_list.append({
                    "evidence_id": "ev_obj_map",
                    "evidence_type": "rendered_object_map",
                    "source": "rendered_object_map.json",
                    "summary": f"{len(obj_map.get('cells', {}))} cells mapped",
                    "hard_evidence": True,
                    "metadata": {
                        k: (v if isinstance(v, dict) else str(v))
                        for k, v in list(obj_map.items())[:5]
                    },
                })
            except Exception:
                pass

    # 6. Truncated OpenMC log.
    raw_text = failure.raw_error_excerpt or ""
    if len(raw_text) > max_log_chars:
        raw_text = raw_text[:max_log_chars] + "...[truncated]"
    evidence_list.append({
        "evidence_id": "ev_log",
        "evidence_type": "runtime_log",
        "source": "openmc_output",
        "summary": raw_text,
        "hard_evidence": True,
    })

    return {
        "failure": failure.to_dict(),
        "policy_issue_code": failure.primary_issue_code,
        "evidence": evidence_list,
        "max_mutating_operations": policy.max_mutating_operations if policy else 4,
    }


# --------------------------------------------------------------------------- #
# Validate LLM diagnosis
# --------------------------------------------------------------------------- #

def validate_runtime_diagnosis(
    diagnosis: RuntimeDiagnosis,
    failure: RuntimeFailure,
    plan_build_state: Any,
    *,
    policy: RuntimeRepairPolicy | None = None,
) -> ValidatedRuntimeDiagnosis:
    """Deterministically validate an LLM-produced diagnosis.

    The LLM can only narrow permissions, never expand them.
    """
    rejection_codes: list[str] = []
    reasons: list[str] = []

    if policy is None:
        policy = get_repair_policy(failure.primary_issue_code)
    if policy is None:
        return ValidatedRuntimeDiagnosis(
            failure_id=failure.failure_id,
            primary_issue_code=failure.primary_issue_code,
            rejection_codes=["no_policy"],
            reasons=[f"No repair policy for {failure.primary_issue_code}"],
        )

    # 1. failure_id must match.
    if diagnosis.failure_id != failure.failure_id:
        rejection_codes.append("failure_id_mismatch")
        reasons.append(f"failure_id {diagnosis.failure_id} != {failure.failure_id}")

    # 2. primary_issue_code must match deterministic root cause.
    if diagnosis.primary_issue_code != failure.primary_issue_code:
        rejection_codes.append("issue_code_mismatch")
        reasons.append(
            f"issue_code {diagnosis.primary_issue_code} != {failure.primary_issue_code}"
        )

    # 3. Classification cannot be changed.
    if diagnosis.classification != failure.classification.value:
        rejection_codes.append("classification_changed")
        reasons.append(
            f"classification {diagnosis.classification} != {failure.classification.value}"
        )

    # 4. Environment/human/transient: no LLM proposal.
    if policy.classification in (
        RuntimeFailureClass.ENVIRONMENT,
        RuntimeFailureClass.HUMAN_FACT,
        RuntimeFailureClass.UNKNOWN,
        RuntimeFailureClass.TRANSIENT,
    ):
        return ValidatedRuntimeDiagnosis(
            accepted=False,
            rejection_codes=["blocked_classification"],
            failure_id=failure.failure_id,
            primary_issue_code=failure.primary_issue_code,
            repair_kind=diagnosis.repair_kind,
            reasons=[f"Classification {policy.classification.value} blocks LLM proposal"],
        )

    # 5. target_patch_type must be in policy candidates.
    if diagnosis.target_patch_type and diagnosis.target_patch_type not in policy.candidate_patch_types:
        rejection_codes.append("target_patch_type_not_in_policy")
        reasons.append(
            f"target_patch_type {diagnosis.target_patch_type} not in "
            f"policy candidates {policy.candidate_patch_types}"
        )

    # 6. repair_kind must be allowed by policy.
    if diagnosis.repair_kind != "no_safe_repair":
        if diagnosis.repair_kind not in policy.allowed_repair_kinds:
            rejection_codes.append("repair_kind_not_allowed")
            reasons.append(
                f"repair_kind {diagnosis.repair_kind} not in "
                f"allowed {policy.allowed_repair_kinds}"
            )

    # 7. Confidence threshold.
    if diagnosis.confidence < policy.minimum_diagnosis_confidence:
        rejection_codes.append("low_confidence")
        reasons.append(
            f"confidence {diagnosis.confidence} < "
            f"minimum {policy.minimum_diagnosis_confidence}"
        )

    # 8. Unresolved contradictions block.
    if diagnosis.contradictions:
        rejection_codes.append("unresolved_contradictions")
        reasons.append(f"Contradictions: {diagnosis.contradictions}")

    # 9. Disposition check.
    if diagnosis.disposition != "safe_to_propose":
        return ValidatedRuntimeDiagnosis(
            accepted=False,
            rejection_codes=[diagnosis.disposition],
            failure_id=failure.failure_id,
            primary_issue_code=failure.primary_issue_code,
            target_patch_type=diagnosis.target_patch_type,
            target_patch_id=diagnosis.target_patch_id,
            repair_kind=diagnosis.repair_kind,
            reasons=reasons or [f"Disposition: {diagnosis.disposition}"],
        )

    # 10. Check target patch exists in build state.
    from openmc_agent.plan_builder.state import PlanBuildState

    target_patch_id = diagnosis.target_patch_id
    if target_patch_id and plan_build_state is not None:
        bs = plan_build_state
        if isinstance(bs, dict):
            try:
                bs = PlanBuildState.model_validate(bs)
            except Exception:
                bs = None
        if bs is not None:
            if target_patch_id not in bs.patches:
                rejection_codes.append("target_patch_not_found")
                reasons.append(f"Patch {target_patch_id} not in build state")
            elif bs.patches[target_patch_id].status != "valid":
                rejection_codes.append("target_patch_not_valid")
                reasons.append(
                    f"Patch {target_patch_id} status="
                    f"{bs.patches[target_patch_id].status}"
                )

    accepted = not rejection_codes
    proposal_allowed = (
        accepted
        and policy.llm_proposal_supported
        and diagnosis.repair_kind != "no_safe_repair"
        and diagnosis.repair_kind != "renderer_implementation_bug"
        and diagnosis.repair_kind != "environment_fix_required"
        and diagnosis.repair_kind != "human_fact_required"
    )

    # Deterministic allowlist: intersect policy paths with diagnosis paths.
    det_allowed = list(policy.allowed_path_patterns)
    if diagnosis.target_patch_paths:
        # LLM can only narrow, not expand.
        det_allowed = [
            p for p in det_allowed
            if any(_path_matches(p, tp) for tp in diagnosis.target_patch_paths)
        ] or det_allowed  # Keep policy paths if intersection is empty

    det_forbidden = list(policy.forbidden_path_patterns)

    # An empty policy-derived allowlist is not permissive. It means Python has
    # not yet proven a concrete patch-relative path from provenance/evidence.
    # The LLM must never supply that missing authority.
    if not det_allowed:
        reasons.append(
            "No concrete deterministic patch-relative allowlist exists for this diagnosis"
        )
        proposal_allowed = False

    return ValidatedRuntimeDiagnosis(
        accepted=accepted,
        rejection_codes=rejection_codes,
        failure_id=failure.failure_id,
        primary_issue_code=failure.primary_issue_code,
        target_patch_type=diagnosis.target_patch_type,
        target_patch_id=diagnosis.target_patch_id,
        deterministically_allowed_paths=det_allowed,
        deterministically_forbidden_paths=det_forbidden,
        repair_kind=diagnosis.repair_kind,
        risk_level="low" if accepted else "unknown",
        proposal_allowed=proposal_allowed,
        reasons=reasons,
    )


def _path_matches(pattern: str, path: str) -> bool:
    """Simple glob match for path patterns."""
    from openmc_agent.repair_policy import match_json_pointer_pattern
    try:
        return match_json_pointer_pattern(path, pattern)
    except Exception:
        return False


# --------------------------------------------------------------------------- #
# Client protocol
# --------------------------------------------------------------------------- #

class RuntimeDiagnosticianClient:
    """Protocol for LLM diagnostician clients."""

    def diagnose(
        self,
        diagnosis_input: dict[str, Any],
        *,
        prompt: str,
        json_schema: dict[str, Any],
    ) -> str | dict[str, Any]:
        raise NotImplementedError


class FakeRuntimeDiagnosticianClient:
    """Deterministic fake for testing. Never impersonates a real LLM."""

    def __init__(self, disposition: RuntimeDiagnosisDisposition = "no_safe_repair"):
        self._disposition = disposition

    def diagnose(
        self,
        diagnosis_input: dict[str, Any],
        *,
        prompt: str,
        json_schema: dict[str, Any],
    ) -> dict[str, Any]:
        failure = diagnosis_input.get("failure", {})
        return {
            "diagnosis_id": f"diag_{uuid4().hex[:12]}",
            "failure_id": failure.get("failure_id", ""),
            "primary_issue_code": failure.get("primary_issue_code", ""),
            "classification": failure.get("classification", "unknown"),
            "root_cause_summary": "Fake diagnostician: deterministic no-safe-repair.",
            "disposition": self._disposition,
            "repair_kind": "no_safe_repair",
            "confidence": 0.0,
            "evidence_refs": ["ev_failure"],
        }


def make_runtime_diagnostician_client(
    *,
    llm: Any | None = None,
    model_name: str | None = None,
) -> RuntimeDiagnosticianClient:
    """Build a diagnostician client. Falls back to Fake only if llm is None."""
    if llm is None:
        return FakeRuntimeDiagnosticianClient()
    # Wrap callable into a client.
    class _WrappedClient(RuntimeDiagnosticianClient):
        def __init__(self, fn):
            self._fn = fn

        def diagnose(self, diagnosis_input, *, prompt, json_schema):
            return self._fn(diagnosis_input, prompt=prompt, json_schema=json_schema)

    return _WrappedClient(llm)
