"""Transactional, issue-scoped multi-patch Placement revision evaluation."""

from __future__ import annotations

import json
from typing import Any

from pydantic import Field

from openmc_agent.plan_builder.patches import parse_patch_content
from openmc_agent.plan_builder.state import PlanBuildState, PlanPatchEnvelope
from openmc_agent.plan_builder.validation_repair import PatchRepairOperation
from openmc_agent.plan_builder.validators import validate_patch
from openmc_agent.repair_proposal import apply_json_patch_to_clone
from openmc_agent.schemas import AgentBaseModel

from .fingerprints import compute_candidate_hash, compute_evidence_pack_hash
from .models import PlacementRevisionProposal, PlanReviewFinding
from .placement_issue_policy import placement_issue_owner
from .placement_preflight import run_placement_preflight


class PlacementRevisionEvaluation(AgentBaseModel):
    accepted: bool = False
    clone_state: dict[str, Any] | None = None
    candidate_hash: str | None = None
    changed_patch_types: list[str] = Field(default_factory=list)
    reasons: list[str] = Field(default_factory=list)
    before_issue_codes: list[str] = Field(default_factory=list)
    after_issue_codes: list[str] = Field(default_factory=list)


def normalize_placement_revision(raw: str | dict[str, Any]) -> PlacementRevisionProposal:
    proposal = PlacementRevisionProposal.model_validate(json.loads(raw) if isinstance(raw, str) else raw)
    for edit in proposal.edits:
        operations = [PatchRepairOperation.model_validate(item) for item in edit.operations]
        if not operations or any(item.op not in {"add", "replace", "remove"} for item in operations):
            raise ValueError("placement revision permits non-empty add/replace/remove operations only")
        edit.operations = operations
    return proposal


def allowed_paths_for_placement_findings(findings: list[PlanReviewFinding]) -> dict[str, list[str]]:
    result: dict[str, set[str]] = {}
    for finding in findings:
        if not finding.repairable_by_llm or finding.requires_human:
            continue
        owner = placement_issue_owner(finding.code)
        for patch_type in owner.get("owner_patch_types", []):
            result.setdefault(patch_type, set()).update(owner.get("repairable_paths", []))
            result[patch_type].update({"/assumptions", "/source_note"})
    return {key: sorted(value) for key, value in result.items()}


def _valid_envelope(state: PlanBuildState, patch_type: str) -> PlanPatchEnvelope | None:
    values = [item for item in state.patches.values() if item.patch_type == patch_type and item.status == "valid"]
    return values[0] if len(values) == 1 else None


def evaluate_placement_revision(*, state: PlanBuildState, proposal: PlacementRevisionProposal, findings: list[PlanReviewFinding], prior_candidate_hashes: list[str]) -> PlacementRevisionEvaluation:
    allowed = allowed_paths_for_placement_findings(findings)
    known_finding_ids = {item.finding_id for item in findings}
    if not proposal.resolved_finding_ids or not set(proposal.resolved_finding_ids).issubset(known_finding_ids):
        return PlacementRevisionEvaluation(reasons=["placement_revision.unknown_resolved_finding"])
    clone = state.model_copy(deep=True)
    changed: dict[str, dict[str, Any]] = {}
    for edit in proposal.edits:
        operations = [PatchRepairOperation.model_validate(item) for item in edit.operations]
        if not operations or any(item.op not in {"add", "replace", "remove"} for item in operations):
            return PlacementRevisionEvaluation(reasons=[f"placement_revision.invalid_operations:{edit.patch_type}"])
        env = _valid_envelope(clone, edit.patch_type)
        if env is None:
            return PlacementRevisionEvaluation(reasons=[f"placement_revision.patch_unavailable:{edit.patch_type}"])
        current_hash = compute_candidate_hash(target_patch_type=edit.patch_type, candidate_patch=env.content)
        if edit.expected_patch_hash != current_hash:
            return PlacementRevisionEvaluation(reasons=[f"placement_revision.patch_hash_mismatch:{edit.patch_type}"])
        for operation in operations:
            if operation.path in {"", "/patch_type"} or not any(operation.path == path or operation.path.startswith(path + "/") for path in allowed.get(edit.patch_type, [])):
                return PlacementRevisionEvaluation(reasons=[f"placement_revision.path_out_of_scope:{edit.patch_type}:{operation.path}"])
        try:
            applied = apply_json_patch_to_clone(env.content, [operation.model_dump(mode="json", exclude_none=True) for operation in operations])
            if not applied.ok:
                return PlacementRevisionEvaluation(reasons=[f"placement_revision.apply_failed:{applied.error}"])
            candidate = applied.plan
        except Exception as exc:
            return PlacementRevisionEvaluation(reasons=[f"placement_revision.apply_failed:{exc}"])
        if not isinstance(candidate, dict):
            return PlacementRevisionEvaluation(reasons=["placement_revision.root_replacement_forbidden"])
        try:
            parsed = parse_patch_content(edit.patch_type, candidate)
            validation = validate_patch(parsed)
        except Exception as exc:
            return PlacementRevisionEvaluation(reasons=[f"placement_revision.schema_invalid:{exc}"])
        if not validation.ok:
            return PlacementRevisionEvaluation(reasons=[f"placement_revision.validator_failed:{edit.patch_type}"])
        env.content = candidate
        changed[edit.patch_type] = candidate
    candidate_hash = compute_evidence_pack_hash({key: changed[key] for key in sorted(changed)})
    if candidate_hash in prior_candidate_hashes:
        return PlacementRevisionEvaluation(candidate_hash=candidate_hash, reasons=["placement_revision.duplicate_candidate"])
    before = run_placement_preflight(state=state)
    after = run_placement_preflight(state=clone)
    before_codes = sorted(item["code"] for item in before["issues"] if item.get("severity") == "error")
    after_codes = sorted(item["code"] for item in after["issues"] if item.get("severity") == "error")
    if len(after_codes) >= len(before_codes):
        return PlacementRevisionEvaluation(candidate_hash=candidate_hash, reasons=["placement_revision.blocking_issues_not_reduced"], before_issue_codes=before_codes, after_issue_codes=after_codes)
    return PlacementRevisionEvaluation(accepted=True, clone_state=clone.model_dump(mode="json"), candidate_hash=candidate_hash, changed_patch_types=sorted(changed), before_issue_codes=before_codes, after_issue_codes=after_codes)


def commit_placement_revision(*, state: PlanBuildState, evaluated: PlacementRevisionEvaluation, proposal_id: str) -> list[str]:
    """Atomically replace only evaluated valid envelopes; caller reruns critic."""
    if not evaluated.accepted or evaluated.clone_state is None:
        raise ValueError("cannot commit rejected placement revision")
    clone = PlanBuildState.model_validate(evaluated.clone_state)
    original = state.model_dump(mode="json")
    try:
        changed_ids: list[str] = []
        for patch_type in evaluated.changed_patch_types:
            old = _valid_envelope(state, patch_type)
            new = _valid_envelope(clone, patch_type)
            if old is None or new is None:
                raise ValueError(f"missing transactional envelope: {patch_type}")
            old.status = "repaired"
            replacement = new.model_copy(deep=True)
            replacement.patch_id = f"{old.patch_id}_repair_{proposal_id[:12]}"
            replacement.source = "repair"
            replacement.status = "valid"
            replacement.metadata = {**replacement.metadata, "repair_proposal_id": proposal_id, "group_candidate_hash": evaluated.candidate_hash}
            state.add_patch(replacement)
            changed_ids.append(replacement.patch_id)
        # Do not retain a placement dependent built from the superseded
        # contract.  A transaction containing both edits stays valid; an
        # omitted dependent is conservatively invalidated for resume.
        invalidated: list[str] = []
        if "localized_insert_profiles" in evaluated.changed_patch_types and "assembly_catalog" not in evaluated.changed_patch_types:
            invalidated.extend(state.invalidate_patch_types(["assembly_catalog", "core_layout", "axial_layers", "axial_overlays"], reason="placement profile revision requires dependent rebuild", issues=[{"code": "placement_revision.dependent_stale"}]))
        if "assembly_catalog" in evaluated.changed_patch_types and "core_layout" not in evaluated.changed_patch_types:
            invalidated.extend(state.invalidate_patch_types(["core_layout", "axial_layers", "axial_overlays"], reason="placement catalog revision requires dependent rebuild", issues=[{"code": "placement_revision.dependent_stale"}]))
        state.assembled_plan = None
        state.add_event("planning.placement_revision_atomic_commit", "placement revision committed after clone validation", {"proposal_id": proposal_id, "candidate_hash": evaluated.candidate_hash, "patch_ids": changed_ids, "invalidated_patch_ids": invalidated})
        return changed_ids
    except Exception:
        restored = PlanBuildState.model_validate(original)
        state.__dict__.update(restored.__dict__)
        state.add_event("planning.placement_revision_rolled_back", "placement revision commit rolled back", {"proposal_id": proposal_id})
        raise
