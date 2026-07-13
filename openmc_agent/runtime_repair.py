"""Runtime repair: deterministic targeted repair for OpenMC runtime failures.

This module provides the one-shot deterministic repair pipeline:

    RuntimeFailure
    → build_runtime_repair_request (locate owning patch)
    → propose_deterministic_repair (source oracle / geometry diagnosis)
    → evaluate_deterministic_runtime_repair (clone-only acceptance)
    → commit_accepted_runtime_repair (write back to source patch)

No LLM is used. No monolithic regeneration. One repair per fingerprint,
one repair per workflow run.
"""

from __future__ import annotations

import copy
import hashlib
import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4

from openmc_agent.runtime_feedback import (
    RuntimeFailure,
    RuntimeFailureClass,
    classify_runtime_tool_results,
    normalize_runtime_error,
)
from openmc_agent.runtime_repair_policy import (
    RUNTIME_REPAIR_POLICIES,
    RuntimeRepairPolicy,
    get_repair_policy,
    is_environment_blocked,
)
from openmc_agent.schemas import (
    AgentBaseModel,
    SimulationPlan,
    ValidationIssue,
    ValidationReport,
)
from openmc_agent.tools import ToolResult
from pydantic import Field


# --------------------------------------------------------------------------- #
# Disposition enum
# --------------------------------------------------------------------------- #

RuntimeRepairDisposition = Literal[
    "applicable",
    "not_applicable",
    "blocked_environment",
    "blocked_human_fact",
    "ambiguous_owner",
    "no_safe_repair",
    "candidate_generated",
    "candidate_rejected",
    "accepted",
    "no_improvement",
    "duplicate_candidate",
    "budget_exhausted",
]


# --------------------------------------------------------------------------- #
# Request model
# --------------------------------------------------------------------------- #

class RuntimeRepairRequest(AgentBaseModel):
    request_id: str
    runtime_failure: dict[str, Any] = Field(default_factory=dict)
    source_plan_hash: str | None = None
    source_build_state_hash: str | None = None
    target_patch_type: str | None = None
    target_patch_id: str | None = None
    allowed_paths: list[str] = Field(default_factory=list)
    forbidden_paths: list[str] = Field(default_factory=list)
    current_patch: dict[str, Any] | None = None
    relevant_plan_excerpt: dict[str, Any] = Field(default_factory=dict)
    evidence: dict[str, Any] = Field(default_factory=dict)
    expected_postconditions: list[str] = Field(default_factory=list)
    prior_attempt_count: int = 0
    metadata: dict[str, Any] = Field(default_factory=dict)


# --------------------------------------------------------------------------- #
# Proposal model
# --------------------------------------------------------------------------- #

class DeterministicRuntimeRepairProposal(AgentBaseModel):
    proposal_id: str
    request_id: str
    target_patch_type: str
    operations: list[dict[str, Any]] = Field(default_factory=list)
    diagnosis: str = ""
    changed_paths: list[str] = Field(default_factory=list)
    expected_effect: str = ""
    provenance: str = "deterministic"
    confidence: float = Field(ge=0.0, le=1.0, default=0.0)
    deterministic_rule_id: str = ""


# --------------------------------------------------------------------------- #
# Evaluation model
# --------------------------------------------------------------------------- #

class RuntimeRepairEvaluation(AgentBaseModel):
    accepted: bool = False
    disposition: RuntimeRepairDisposition = "not_applicable"
    request_id: str = ""
    proposal_id: str | None = None
    plan_hash_before: str | None = None
    plan_hash_after: str | None = None
    patch_hash_before: str | None = None
    patch_hash_after: str | None = None
    changed_paths: list[str] = Field(default_factory=list)
    issue_codes_before: list[str] = Field(default_factory=list)
    issue_codes_after: list[str] = Field(default_factory=list)
    resolved_issue_codes: list[str] = Field(default_factory=list)
    remaining_issue_codes: list[str] = Field(default_factory=list)
    introduced_issue_codes: list[str] = Field(default_factory=list)
    runtime_failure_before: dict[str, Any] | None = None
    runtime_failure_after: dict[str, Any] | None = None
    validation_before: dict[str, Any] | None = None
    validation_after: dict[str, Any] | None = None
    tool_stage_rechecked: str | None = None
    artifacts: list[str] = Field(default_factory=list)
    reasons: list[str] = Field(default_factory=list)
    duration_ms: float | None = None


# --------------------------------------------------------------------------- #
# Hashing helpers
# --------------------------------------------------------------------------- #

def stable_json_hash(value: Any) -> str:
    """SHA-256 prefix of canonical JSON."""
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def _plan_hash(plan: Any) -> str:
    if plan is None:
        return "none"
    if isinstance(plan, dict):
        return stable_json_hash(plan)
    if hasattr(plan, "model_dump"):
        return stable_json_hash(plan.model_dump(mode="json"))
    return stable_json_hash(str(plan))


# --------------------------------------------------------------------------- #
# Build repair request
# --------------------------------------------------------------------------- #

def build_runtime_repair_request(
    failure: RuntimeFailure,
    plan: SimulationPlan | dict[str, Any] | None,
    plan_build_state: dict[str, Any] | Any,
    tool_results: list[ToolResult] | list[dict[str, Any]],
    output_dir: str | Path | None = None,
) -> RuntimeRepairRequest | RuntimeRepairEvaluation:
    """Locate the owning patch and build a repair request.

    Returns a :class:`RuntimeRepairRequest` if a deterministic repair is
    possible, or a :class:`RuntimeRepairEvaluation` with a blocking
    disposition if not.
    """
    policy = get_repair_policy(failure.primary_issue_code)
    if policy is None:
        return _blocked_eval(
            failure, "no_policy",
            f"No repair policy for {failure.primary_issue_code}",
        )

    # Environment / human_fact / unknown / transient: blocked.
    if policy.classification in (
        RuntimeFailureClass.ENVIRONMENT,
        RuntimeFailureClass.HUMAN_FACT,
        RuntimeFailureClass.UNKNOWN,
        RuntimeFailureClass.TRANSIENT,
    ):
        disposition = {
            RuntimeFailureClass.ENVIRONMENT: "blocked_environment",
            RuntimeFailureClass.HUMAN_FACT: "blocked_human_fact",
            RuntimeFailureClass.UNKNOWN: "no_safe_repair",
            RuntimeFailureClass.TRANSIENT: "no_safe_repair",
        }[policy.classification]
        return _blocked_eval(failure, disposition, policy.description)

    if not policy.deterministic_repair_supported:
        return _blocked_eval(
            failure, "no_safe_repair",
            f"Deterministic repair not supported for {failure.primary_issue_code}",
        )

    # Must have a preferred patch type.
    if not policy.preferred_patch_type:
        return _blocked_eval(
            failure, "ambiguous_owner",
            "No preferred patch type; diagnosis required.",
        )

    # Locate the target patch in PlanBuildState.
    from openmc_agent.plan_builder.state import PlanBuildState

    build_state: PlanBuildState
    if isinstance(plan_build_state, dict):
        build_state = PlanBuildState.model_validate(plan_build_state)
    elif isinstance(plan_build_state, PlanBuildState):
        build_state = plan_build_state
    else:
        return _blocked_eval(failure, "no_safe_repair", "No PlanBuildState available")

    target_patches = build_state.get_valid_patches(policy.preferred_patch_type)
    if not target_patches:
        return _blocked_eval(
            failure, "no_safe_repair",
            f"No valid '{policy.preferred_patch_type}' patch to repair",
        )
    if len(target_patches) > 1:
        return _blocked_eval(
            failure, "ambiguous_owner",
            f"Multiple '{policy.preferred_patch_type}' patches; cannot pick one",
        )

    target_envelope = target_patches[0]
    source_hash = _plan_hash(plan)
    bs_hash = stable_json_hash(
        build_state.model_dump(mode="json") if hasattr(build_state, "model_dump") else {}
    )

    return RuntimeRepairRequest(
        request_id=f"rr_{uuid4().hex[:12]}",
        runtime_failure=failure.to_dict(),
        source_plan_hash=source_hash,
        source_build_state_hash=bs_hash,
        target_patch_type=policy.preferred_patch_type,
        target_patch_id=target_envelope.patch_id,
        allowed_paths=list(policy.allowed_path_patterns),
        forbidden_paths=list(policy.forbidden_path_patterns),
        current_patch=copy.deepcopy(target_envelope.content),
        evidence={
            "issue_code": failure.primary_issue_code,
            "tool_stage": failure.stage,
            "tool_name": failure.tool_name,
            "normalized_message": failure.normalized_message,
            "raw_error_excerpt": failure.raw_error_excerpt,
        },
        expected_postconditions=[
            f"Primary issue {failure.primary_issue_code} resolved",
            "No new blocking issues introduced",
            "Protected scientific facts unchanged",
        ],
    )


def _blocked_eval(
    failure: RuntimeFailure,
    disposition: RuntimeRepairDisposition,
    reason: str,
) -> RuntimeRepairEvaluation:
    return RuntimeRepairEvaluation(
        accepted=False,
        disposition=disposition,
        request_id="",
        runtime_failure_before=failure.to_dict(),
        reasons=[reason],
    )


# --------------------------------------------------------------------------- #
# R3-A: Source binding oracle
# --------------------------------------------------------------------------- #

def diagnose_source_runtime_failure(
    failure: RuntimeFailure,
    plan: SimulationPlan | dict[str, Any] | None,
    build_state: Any,
) -> dict[str, Any]:
    """Diagnose a source-related runtime failure.

    Returns a diagnosis dict with ``safe_repair_available``, ``reasons``,
    and derived evidence (active fuel bounds, current strategy, etc.).
    """
    from openmc_agent.plan_builder.state import PlanBuildState
    from openmc_agent.source_settings import (
        SourceBounds,
        active_fuel_z_bounds,
        fissionable_material_ids,
        source_bounds_for_plan,
    )

    diagnosis: dict[str, Any] = {
        "issue_code": failure.primary_issue_code,
        "current_source_strategy": None,
        "active_fuel_z_bounds": None,
        "source_bounds": None,
        "fissionable_material_count": 0,
        "safe_repair_available": False,
        "reasons": [],
    }

    if plan is None:
        diagnosis["reasons"].append("No plan available")
        return diagnosis

    # Coerce plan to SimulationPlan if needed.
    sim_plan = plan
    if isinstance(plan, dict):
        try:
            sim_plan = SimulationPlan.model_validate(plan)
        except Exception:
            diagnosis["reasons"].append("Cannot parse plan")
            return diagnosis

    # Get current settings patch strategy.
    bs: PlanBuildState | None = None
    if isinstance(build_state, dict):
        try:
            bs = PlanBuildState.model_validate(build_state)
        except Exception:
            pass
    elif isinstance(build_state, PlanBuildState):
        bs = build_state

    if bs is not None:
        settings_patches = bs.get_valid_patches("settings")
        if settings_patches:
            content = settings_patches[0].content
            diagnosis["current_source_strategy"] = content.get("source_strategy")

    # Check active fuel bounds.
    model = sim_plan.complex_model if hasattr(sim_plan, "complex_model") else None
    if model is not None:
        z_bounds = active_fuel_z_bounds(model)
        diagnosis["active_fuel_z_bounds"] = z_bounds

        src = source_bounds_for_plan(model)
        if src is not None:
            diagnosis["source_bounds"] = {
                "x_min": src.x_min, "x_max": src.x_max,
                "y_min": src.y_min, "y_max": src.y_max,
                "z_min": src.z_min, "z_max": src.z_max,
            }

        fiss_ids = fissionable_material_ids(model)
        diagnosis["fissionable_material_count"] = len(fiss_ids)

    # Determine if safe repair is available.
    strategy = diagnosis["current_source_strategy"]
    z_bounds = diagnosis["active_fuel_z_bounds"]
    fiss_count = diagnosis["fissionable_material_count"]

    if z_bounds is None:
        diagnosis["reasons"].append("Cannot identify active fuel z-bounds")
        return diagnosis
    if fiss_count == 0:
        diagnosis["reasons"].append("No fissionable material found")
        return diagnosis

    # If strategy is already active_fuel_box + fissionable, no-op guard.
    if strategy == "active_fuel_box":
        diagnosis["reasons"].append(
            "Source strategy is already active_fuel_box; "
            "if rejection persists, this is likely a renderer/source-binding bug"
        )
        diagnosis["safe_repair_available"] = False
        return diagnosis

    diagnosis["safe_repair_available"] = True
    diagnosis["reasons"].append(
        f"Strategy '{strategy}' → 'active_fuel_box' with fissionable constraint"
    )
    return diagnosis


def propose_source_binding_repair(
    request: RuntimeRepairRequest,
    diagnosis: dict[str, Any],
) -> DeterministicRuntimeRepairProposal | RuntimeRepairEvaluation:
    """Propose a deterministic source binding repair.

    Returns a proposal that switches ``source_strategy`` to
    ``active_fuel_box`` and sets ``source_requires_fissionable_constraint``
    to ``True``. Does NOT write benchmark-specific bounds.
    """
    if not diagnosis.get("safe_repair_available"):
        return RuntimeRepairEvaluation(
            accepted=False,
            disposition="no_safe_repair",
            request_id=request.request_id,
            reasons=diagnosis.get("reasons", ["Source repair not safe"]),
        )

    current = request.current_patch or {}
    current_strategy = current.get("source_strategy")
    current_fiss = current.get("source_requires_fissionable_constraint", True)

    # No-op guard: if already correct, don't produce a patch.
    if current_strategy == "active_fuel_box" and current_fiss is True:
        return RuntimeRepairEvaluation(
            accepted=False,
            disposition="no_safe_repair",
            request_id=request.request_id,
            reasons=[
                "Settings already active_fuel_box + fissionable; "
                "rejection is likely a renderer/source-binding bug, not a plan issue"
            ],
        )

    operations: list[dict[str, Any]] = []
    changed_paths: list[str] = []

    if current_strategy != "active_fuel_box":
        operations.append({
            "op": "replace",
            "path": "/source_strategy",
            "value": "active_fuel_box",
        })
        changed_paths.append("/source_strategy")

    if current_fiss is not True:
        operations.append({
            "op": "replace",
            "path": "/source_requires_fissionable_constraint",
            "value": True,
        })
        changed_paths.append("/source_requires_fissionable_constraint")

    return DeterministicRuntimeRepairProposal(
        proposal_id=f"rp_{uuid4().hex[:12]}",
        request_id=request.request_id,
        target_patch_type="settings",
        operations=operations,
        diagnosis="; ".join(diagnosis.get("reasons", [])),
        changed_paths=changed_paths,
        expected_effect="Source bounds will be derived from active fuel region "
                        "at render time via source_bounds_for_plan()",
        provenance="deterministic_source_binding_oracle",
        confidence=0.95,
        deterministic_rule_id="source_binding_active_fuel_box",
    )


# --------------------------------------------------------------------------- #
# R3-B: Geometry diagnosis (locate only, no auto-repair in R3)
# --------------------------------------------------------------------------- #

def diagnose_geometry_runtime_failure(
    failure: RuntimeFailure,
    plan: SimulationPlan | dict[str, Any] | None,
    output_dir: str | Path | None = None,
) -> dict[str, Any]:
    """Diagnose a geometry overlap or lost-particle failure.

    Returns a diagnosis dict. R3 does NOT auto-repair geometry; it only
    locates the rendered objects and patch owners.
    """
    diagnosis: dict[str, Any] = {
        "issue_code": failure.primary_issue_code,
        "reported_cell_ids": [],
        "mapped_ir_objects": [],
        "mapped_patch_paths": [],
        "candidate_patch_owners": [],
        "common_surfaces": [],
        "diagnosis_confidence": 0.0,
        "safe_repair_available": False,
        "reasons": [],
    }

    # Try to extract cell IDs from the error message.
    import re
    text = failure.raw_error_excerpt or failure.normalized_message
    cell_ids = re.findall(r"cell[s]?\s+(\d+)", text, re.IGNORECASE)
    # Also capture "between cells X and Y" second number.
    cell_ids.extend(re.findall(r"\band\s+(\d+)", text, re.IGNORECASE))
    diagnosis["reported_cell_ids"] = list(dict.fromkeys(cell_ids))

    # Try to load rendered_object_map.json if available.
    if output_dir is not None:
        obj_map_path = Path(output_dir) / "rendered_object_map.json"
        if obj_map_path.exists():
            try:
                obj_map = json.loads(obj_map_path.read_text(encoding="utf-8"))
                for cid in cell_ids:
                    cell_entry = obj_map.get("cells", {}).get(str(cid))
                    if cell_entry:
                        diagnosis["mapped_ir_objects"].append(cell_entry)
                        if cell_entry.get("patch_type"):
                            diagnosis["candidate_patch_owners"].append(
                                cell_entry["patch_type"]
                            )
                        if cell_entry.get("patch_path"):
                            diagnosis["mapped_patch_paths"].append(
                                cell_entry["patch_path"]
                            )
            except Exception as exc:
                diagnosis["reasons"].append(f"Cannot read rendered_object_map: {exc}")

    # R3 does not auto-repair geometry.
    if not diagnosis["mapped_ir_objects"]:
        diagnosis["reasons"].append(
            "No rendered_object_map or no matching cell entries; "
            "cannot locate owning IR objects"
        )
    else:
        diagnosis["reasons"].append(
            f"Located {len(diagnosis['mapped_ir_objects'])} IR object(s); "
            "geometry auto-repair not supported in R3 (requires unique proof)"
        )

    diagnosis["candidate_patch_owners"] = list(
        dict.fromkeys(diagnosis["candidate_patch_owners"])
    )
    return diagnosis


# --------------------------------------------------------------------------- #
# Apply operations to cloned patch content
# --------------------------------------------------------------------------- #

def _apply_operations_to_clone(
    content: dict[str, Any],
    operations: list[dict[str, Any]],
) -> dict[str, Any]:
    """Apply simple JSON-pointer replace/add operations to a deep clone."""
    cloned = copy.deepcopy(content)
    for op in operations:
        path = op.get("path", "")
        value = op.get("value")
        action = op.get("op", "replace")

        # Simple top-level key operations for flat settings patches.
        if path.startswith("/") and "/" not in path[1:]:
            key = path[1:]
            if action in ("replace", "add"):
                cloned[key] = value
            elif action == "remove" and key in cloned:
                del cloned[key]
        else:
            raise ValueError(f"Unsupported operation path: {path}")
    return cloned


# --------------------------------------------------------------------------- #
# Clone-only evaluation
# --------------------------------------------------------------------------- #

def evaluate_deterministic_runtime_repair(
    request: RuntimeRepairRequest,
    proposal: DeterministicRuntimeRepairProposal,
    state: Any,
    *,
    output_dir: str | Path | None = None,
    prior_candidate_hashes: list[str] | None = None,
) -> RuntimeRepairEvaluation:
    """Evaluate a deterministic repair proposal on a deep clone.

    Accepts only if:
    - Patch and plan schema are valid
    - Target primary issue is resolved (static validation)
    - No new blocking issues
    - Plan hash changed
    - Protected facts unchanged
    - Operations are within allowed paths
    - Candidate is not a duplicate
    """
    started = time.perf_counter()
    from openmc_agent.plan_builder.patches import (
        PatchParseError,
        parse_patch_content,
    )
    from openmc_agent.plan_builder.state import (
        PlanBuildState,
        add_validated_patch_to_state,
        assemble_state_if_ready,
    )
    from openmc_agent.plan_builder.validators import validate_patch
    from openmc_agent.validator import validate_simulation_plan

    reasons: list[str] = []
    patch_hash_before = stable_json_hash(request.current_patch or {})

    # Check for duplicate candidate.
    if prior_candidate_hashes:
        preview = _apply_operations_to_clone(
            request.current_patch or {}, proposal.operations
        )
        candidate_hash = stable_json_hash(preview)
        if candidate_hash in prior_candidate_hashes:
            return RuntimeRepairEvaluation(
                accepted=False,
                disposition="duplicate_candidate",
                request_id=request.request_id,
                proposal_id=proposal.proposal_id,
                patch_hash_before=patch_hash_before,
                patch_hash_after=candidate_hash,
                reasons=["Candidate hash matches a prior rejected attempt"],
            )

    # Build cloned state.
    build_state: PlanBuildState
    if isinstance(state, dict):
        build_state = PlanBuildState.model_validate(state)
    elif isinstance(state, PlanBuildState):
        build_state = state.model_copy(deep=True)
    else:
        return RuntimeRepairEvaluation(
            accepted=False,
            disposition="no_safe_repair",
            request_id=request.request_id,
            reasons=["No PlanBuildState available for clone evaluation"],
        )

    # Clone target patch content and apply operations.
    target_envelopes = [
        env for env in build_state.patches.values()
        if env.patch_type == request.target_patch_type
        and env.status == "valid"
    ]
    if not target_envelopes:
        return RuntimeRepairEvaluation(
            accepted=False,
            disposition="no_safe_repair",
            request_id=request.request_id,
            reasons=[f"No valid '{request.target_patch_type}' patch in clone"],
        )

    target_envelope = target_envelopes[0]
    try:
        repaired_content = _apply_operations_to_clone(
            target_envelope.content, proposal.operations
        )
    except Exception as exc:
        return RuntimeRepairEvaluation(
            accepted=False,
            disposition="candidate_rejected",
            request_id=request.request_id,
            proposal_id=proposal.proposal_id,
            reasons=[f"Cannot apply operations: {exc}"],
        )

    patch_hash_after = stable_json_hash(repaired_content)
    if patch_hash_after == patch_hash_before:
        reasons.append("Patch content unchanged after operations")

    # Parse repaired patch.
    try:
        parsed = parse_patch_content(request.target_patch_type, repaired_content)
    except PatchParseError as exc:
        return RuntimeRepairEvaluation(
            accepted=False,
            disposition="candidate_rejected",
            request_id=request.request_id,
            proposal_id=proposal.proposal_id,
            patch_hash_before=patch_hash_before,
            patch_hash_after=patch_hash_after,
            reasons=[f"Patch parse failed: {exc}"],
        )

    # Replace the patch in cloned state.
    cloned_env = target_envelope.model_copy(
        update={"content": repaired_content, "source": "repair"}
    )
    build_state.patches[target_envelope.patch_id] = cloned_env
    build_state.patch_status[target_envelope.patch_id] = "valid"

    # Assemble plan from cloned state.
    try:
        build_state = assemble_state_if_ready(build_state, strict=True)
    except Exception as exc:
        return RuntimeRepairEvaluation(
            accepted=False,
            disposition="candidate_rejected",
            request_id=request.request_id,
            proposal_id=proposal.proposal_id,
            patch_hash_before=patch_hash_before,
            patch_hash_after=patch_hash_after,
            reasons=[f"Assembly failed: {exc}"],
        )

    if build_state.assembled_plan is None:
        return RuntimeRepairEvaluation(
            accepted=False,
            disposition="candidate_rejected",
            request_id=request.request_id,
            proposal_id=proposal.proposal_id,
            patch_hash_before=patch_hash_before,
            patch_hash_after=patch_hash_after,
            reasons=["Assembly produced no plan"],
        )

    # Validate plan.
    try:
        repaired_plan = SimulationPlan.model_validate(build_state.assembled_plan)
    except Exception as exc:
        return RuntimeRepairEvaluation(
            accepted=False,
            disposition="candidate_rejected",
            request_id=request.request_id,
            proposal_id=proposal.proposal_id,
            patch_hash_before=patch_hash_before,
            patch_hash_after=patch_hash_after,
            reasons=[f"Plan schema validation failed: {exc}"],
        )

    repaired_report = validate_simulation_plan(repaired_plan)
    plan_hash_after = _plan_hash(repaired_plan)

    # Check improvement: primary issue code should be gone from preflight.
    from openmc_agent.source_settings import validate_source_settings

    source_issues_after = validate_source_settings(repaired_plan)
    source_issue_codes_after = [i.code for i in source_issues_after]
    issue_codes_after = [i.code for i in repaired_report.issues]

    failure = request.runtime_failure or {}
    primary_code = failure.get("primary_issue_code", "")
    resolved = primary_code and primary_code not in source_issue_codes_after

    # Check for new blockers.
    new_blockers = [
        code for code in issue_codes_after
        if code not in (request.evidence.get("issue_codes_before") or [])
        and code != primary_code
    ]

    # Protected facts check: compare non-settings patch hashes.
    original_state = state
    if isinstance(original_state, PlanBuildState):
        original_patches = original_state.patches
    else:
        original_patches = {}
    facts_unchanged = True
    for pid, env in original_patches.items():
        if env.patch_type != request.target_patch_type and env.status == "valid":
            cloned_env2 = build_state.patches.get(pid)
            if cloned_env2 is None or stable_json_hash(cloned_env2.content) != stable_json_hash(env.content):
                facts_unchanged = False
                reasons.append(f"Non-target patch {pid} ({env.patch_type}) was modified")
                break

    # Acceptance conditions.
    accepted = (
        resolved
        and not new_blockers
        and plan_hash_after != request.source_plan_hash
        and facts_unchanged
        and patch_hash_after != patch_hash_before
    )

    disposition: RuntimeRepairDisposition = "accepted" if accepted else "candidate_rejected"
    if not resolved:
        reasons.append(f"Primary issue {primary_code} not resolved")
    if new_blockers:
        reasons.append(f"New blockers introduced: {new_blockers}")
    if not facts_unchanged:
        reasons.append("Protected scientific facts were modified")

    elapsed = (time.perf_counter() - started) * 1000

    return RuntimeRepairEvaluation(
        accepted=accepted,
        disposition=disposition,
        request_id=request.request_id,
        proposal_id=proposal.proposal_id,
        plan_hash_before=request.source_plan_hash,
        plan_hash_after=plan_hash_after,
        patch_hash_before=patch_hash_before,
        patch_hash_after=patch_hash_after,
        changed_paths=proposal.changed_paths,
        issue_codes_before=[primary_code] if primary_code else [],
        issue_codes_after=issue_codes_after,
        resolved_issue_codes=[primary_code] if resolved else [],
        remaining_issue_codes=[] if resolved else [primary_code],
        introduced_issue_codes=new_blockers,
        validation_after=repaired_report.model_dump(mode="json") if hasattr(repaired_report, "model_dump") else None,
        reasons=reasons,
        duration_ms=elapsed,
    )


# --------------------------------------------------------------------------- #
# Commit accepted repair
# --------------------------------------------------------------------------- #

def commit_accepted_runtime_repair(
    request: RuntimeRepairRequest,
    proposal: DeterministicRuntimeRepairProposal,
    evaluation: RuntimeRepairEvaluation,
    state: Any,
) -> Any:
    """Commit an accepted repair to the source PlanBuildState.

    Returns the updated PlanBuildState. Does NOT modify model.py or XML.
    """
    from openmc_agent.plan_builder.state import PlanBuildState

    build_state: PlanBuildState
    if isinstance(state, dict):
        build_state = PlanBuildState.model_validate(state)
    elif isinstance(state, PlanBuildState):
        build_state = state
    else:
        raise ValueError("Cannot commit without PlanBuildState")

    target_id = request.target_patch_id
    if not target_id:
        targets = [
            env for env in build_state.patches.values()
            if env.patch_type == request.target_patch_type
            and env.status == "valid"
        ]
        if not targets:
            raise ValueError(f"No target patch for {request.target_patch_type}")
        target_id = targets[0].patch_id

    envelope = build_state.patches[target_id]
    repaired_content = _apply_operations_to_clone(envelope.content, proposal.operations)

    # Update the source patch envelope, preserving patch_id.
    updated = envelope.model_copy(update={
        "content": repaired_content,
        "source": "repair",
        "status": "repaired",
    })
    build_state.patches[target_id] = updated
    build_state.patch_status[target_id] = "repaired"

    # Mark status back to valid after repair (so assembly picks it up).
    build_state.mark_patch_status(target_id, "valid")

    # Clear assembled plan to force re-assembly.
    build_state.assembled_plan = None
    build_state.validation_issues = []

    # Record repair history.
    build_state.validation_repair_history.append({
        "repair_id": proposal.proposal_id,
        "target_patch_type": request.target_patch_type,
        "target_patch_id": target_id,
        "operations": proposal.operations,
        "changed_paths": proposal.changed_paths,
        "deterministic_rule_id": proposal.deterministic_rule_id,
        "accepted": True,
    })

    return build_state
