"""Constrained runtime patch proposer: generates safe RFC6902 patches from
validated diagnoses.

The proposer receives a validated diagnosis, a target patch, deterministic
allowed/forbidden paths, and hard evidence. It outputs operations that are
then statically validated and clone-tested before acceptance.
"""

from __future__ import annotations

from typing import Any, Literal
from uuid import uuid4

from pydantic import Field

from openmc_agent.plan_builder.validation_repair import (
    GLOBAL_FORBIDDEN_PATH_PATTERNS,
    PatchRepairOperation,
    stable_json_hash,
)
from openmc_agent.repair_policy import is_protected_path, match_json_pointer_pattern
from openmc_agent.repair_proposal import apply_json_patch_to_clone
from openmc_agent.runtime_diagnostician import ValidatedRuntimeDiagnosis
from openmc_agent.schemas import AgentBaseModel


class LLMRuntimeRepairProposal(AgentBaseModel):
    """A constrained LLM-generated patch proposal for runtime repair."""

    proposal_id: str
    diagnosis_id: str
    request_id: str = ""
    failure_id: str = ""
    target_patch_type: str
    target_patch_id: str | None = None
    source_issue_codes: list[str] = Field(default_factory=list)
    repair_kind: str = "no_safe_repair"
    operations: list[dict[str, Any]] = Field(default_factory=list)
    rationale: str = ""
    expected_effect: str = ""
    evidence_refs: list[str] = Field(default_factory=list)
    requires_human_confirmation: bool = False
    confidence: float = Field(ge=0.0, le=1.0, default=0.0)
    model: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class ProposalValidationResult(AgentBaseModel):
    accepted: bool = False
    rejection_codes: list[str] = Field(default_factory=list)
    proposal: LLMRuntimeRepairProposal | None = None
    reasons: list[str] = Field(default_factory=list)
    operations_validated: int = 0


# --------------------------------------------------------------------------- #
# Static proposal validation
# --------------------------------------------------------------------------- #

def validate_llm_runtime_proposal(
    raw_proposal: dict[str, Any],
    validated_diagnosis: ValidatedRuntimeDiagnosis,
    current_patch_content: dict[str, Any],
    *,
    max_mutating_operations: int = 4,
    max_total_operations: int = 8,
) -> ProposalValidationResult:
    """Statically validate an LLM patch proposal before clone testing.

    Checks:
    - Schema correct
    - diagnosis/failure IDs consistent
    - target patch consistent
    - operations non-empty
    - test ops match current values
    - paths in allowlist
    - no forbidden/protected paths
    - operation count within budget
    - no root replacement
    - no cross-patch editing
    """
    rejection_codes: list[str] = []
    reasons: list[str] = []

    # 1. Parse schema.
    try:
        proposal = LLMRuntimeRepairProposal.model_validate(raw_proposal)
    except Exception as exc:
        return ProposalValidationResult(
            rejection_codes=["schema_invalid"],
            reasons=[f"Schema validation failed: {exc}"],
        )

    # 2. Diagnosis/failure ID consistency.
    if proposal.diagnosis_id != validated_diagnosis.failure_id:
        # Be lenient: diagnosis_id may differ from failure_id; check failure_id
        if proposal.failure_id and proposal.failure_id != validated_diagnosis.failure_id:
            rejection_codes.append("failure_id_mismatch")
            reasons.append(
                f"failure_id {proposal.failure_id} != "
                f"{validated_diagnosis.failure_id}"
            )

    # 3. Target patch consistency.
    if proposal.target_patch_type != validated_diagnosis.target_patch_type:
        rejection_codes.append("target_patch_type_mismatch")
        reasons.append(
            f"target_patch_type {proposal.target_patch_type} != "
            f"{validated_diagnosis.target_patch_type}"
        )

    # 4. Operations non-empty.
    if not proposal.operations:
        return ProposalValidationResult(
            accepted=False,
            rejection_codes=["empty_operations"],
            proposal=proposal,
            reasons=["No operations in proposal"],
        )

    # 5. Count operations.
    mutating_ops = [op for op in proposal.operations if op.get("op") in ("add", "replace", "remove")]
    total_ops = len(proposal.operations)
    if len(mutating_ops) > max_mutating_operations:
        rejection_codes.append("too_many_mutating_ops")
        reasons.append(
            f"{len(mutating_ops)} mutating ops > limit {max_mutating_operations}"
        )
    if total_ops > max_total_operations:
        rejection_codes.append("too_many_total_ops")
        reasons.append(f"{total_ops} total ops > limit {max_total_operations}")

    # 6. Validate each operation.
    allowed_patterns = validated_diagnosis.deterministically_allowed_paths
    forbidden_patterns = validated_diagnosis.deterministically_forbidden_paths

    for idx, raw_op in enumerate(proposal.operations):
        op_action = raw_op.get("op", "")
        op_path = raw_op.get("path", "")

        # Root replacement forbidden.
        if op_path == "":
            rejection_codes.append(f"op_{idx}_root_replacement")
            reasons.append(f"Operation {idx}: root replacement forbidden")
            continue

        # Protected path check.
        if is_protected_path(op_path):
            rejection_codes.append(f"op_{idx}_protected_path")
            reasons.append(f"Operation {idx}: protected path {op_path}")
            continue

        # Global forbidden patterns.
        is_forbidden = False
        for pattern in GLOBAL_FORBIDDEN_PATH_PATTERNS:
            if match_json_pointer_pattern(op_path, pattern):
                is_forbidden = True
                rejection_codes.append(f"op_{idx}_global_forbidden")
                reasons.append(f"Operation {idx}: globally forbidden pattern {pattern}")
                break
        if is_forbidden:
            continue

        # Policy forbidden patterns.
        for pattern in forbidden_patterns:
            if match_json_pointer_pattern(op_path, pattern):
                is_forbidden = True
                rejection_codes.append(f"op_{idx}_policy_forbidden")
                reasons.append(f"Operation {idx}: policy forbidden pattern")
                break
        if is_forbidden:
            continue

        # Must match at least one allowed pattern.
        if allowed_patterns:
            matched = any(
                match_json_pointer_pattern(op_path, pat)
                for pat in allowed_patterns
            )
            if not matched:
                rejection_codes.append(f"op_{idx}_not_in_allowlist")
                reasons.append(f"Operation {idx}: path {op_path} not in allowlist")

        # Test operation: value must match current patch content.
        if op_action == "test":
            try:
                current_value = _resolve_pointer(current_patch_content, op_path)
                if current_value != raw_op.get("value"):
                    rejection_codes.append(f"op_{idx}_test_mismatch")
                    reasons.append(
                        f"Operation {idx}: test value != current value at {op_path}"
                    )
            except Exception:
                rejection_codes.append(f"op_{idx}_test_path_not_found")
                reasons.append(f"Operation {idx}: test path {op_path} not found")

    accepted = not rejection_codes
    return ProposalValidationResult(
        accepted=accepted,
        rejection_codes=rejection_codes,
        proposal=proposal if accepted else proposal,
        reasons=reasons,
        operations_validated=total_ops,
    )


def _resolve_pointer(document: dict[str, Any], pointer: str) -> Any:
    """Resolve a JSON pointer in a dict, returning the value or raising."""
    from openmc_agent.repair_policy import decode_json_pointer
    parts = decode_json_pointer(pointer)
    current: Any = document
    for part in parts:
        if isinstance(current, list):
            current = current[int(part)]
        elif isinstance(current, dict):
            current = current[part]
        else:
            raise KeyError(f"Cannot resolve {part} in non-container")
    return current


# --------------------------------------------------------------------------- #
# Apply proposal to clone (reusing existing engine)
# --------------------------------------------------------------------------- #

def apply_proposal_to_clone(
    current_patch_content: dict[str, Any],
    proposal: LLMRuntimeRepairProposal,
) -> tuple[dict[str, Any] | None, str | None]:
    """Apply proposal operations to a deep clone using the shared RFC6902 engine.

    Returns (repaired_content, error).
    """
    result = apply_json_patch_to_clone(current_patch_content, proposal.operations)
    if result.ok and result.plan is not None:
        return result.plan, None
    return None, result.error or "application failed"


# --------------------------------------------------------------------------- #
# Client protocol
# --------------------------------------------------------------------------- #

class RuntimePatchProposerClient:
    """Protocol for LLM patch proposer clients."""

    def propose(
        self,
        proposal_input: dict[str, Any],
        *,
        prompt: str,
        json_schema: dict[str, Any],
    ) -> str | dict[str, Any]:
        raise NotImplementedError


class FakeRuntimePatchProposerClient:
    """Deterministic fake. Always returns empty operations (safe stop)."""

    def propose(
        self,
        proposal_input: dict[str, Any],
        *,
        prompt: str,
        json_schema: dict[str, Any],
    ) -> dict[str, Any]:
        return {
            "proposal_id": f"rp_fake_{uuid4().hex[:8]}",
            "diagnosis_id": proposal_input.get("diagnosis_id", ""),
            "failure_id": proposal_input.get("failure_id", ""),
            "target_patch_type": proposal_input.get("target_patch_type", ""),
            "repair_kind": "no_safe_repair",
            "operations": [],
            "rationale": "Fake proposer: no safe repair.",
            "confidence": 0.0,
        }


def make_runtime_patch_proposer_client(
    *,
    llm: Any | None = None,
    model_name: str | None = None,
) -> RuntimePatchProposerClient:
    """Build a proposer client. Falls back to Fake only if llm is None."""
    if llm is None:
        return FakeRuntimePatchProposerClient()

    class _WrappedClient(RuntimePatchProposerClient):
        def __init__(self, fn):
            self._fn = fn

        def propose(self, proposal_input, *, prompt, json_schema):
            return self._fn(proposal_input, prompt=prompt, json_schema=json_schema)

    return _WrappedClient(llm)
