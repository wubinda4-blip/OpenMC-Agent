"""Small owner-aware retry classifier; deliberately not a generic retry loop."""
from __future__ import annotations
from typing import Any
import hashlib
from pydantic import Field
from openmc_agent.schemas import AgentBaseModel
from .closed_loop.fingerprints import canonical_json_dumps

class PlanningRootCause(AgentBaseModel):
    root_cause_id: str
    code: str
    owner_patch_types: list[str]
    dependent_patch_types: list[str] = Field(default_factory=list)
    original_issue_codes: list[str] = Field(default_factory=list)
    affected_ids: list[str] = Field(default_factory=list)
    canonical_owner_patch_hashes: dict[str, str] = Field(default_factory=dict)
    severity: str = "error"
    repair_action: str = "block"
    fingerprint: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)
    def model_post_init(self, __context: Any) -> None:
        if not self.fingerprint:
            payload = {"code": self.code, "owners": self.owner_patch_types, "hashes": self.canonical_owner_patch_hashes, "affected": self.affected_ids, "property": self.metadata.get("required_property")}
            self.fingerprint = hashlib.sha256(canonical_json_dumps(payload).encode()).hexdigest()

def classify_planning_root_causes(issues: list[dict[str, Any]], owner_hashes: dict[str, str]) -> list[PlanningRootCause]:
    result: list[PlanningRootCause] = []
    if any(item.get("code") in {"assembly.missing_patch", "assembly.model_scope_patch_family_conflict", "facts.model_scope_conflicts_with_planning_features"} for item in issues):
        result.append(PlanningRootCause(root_cause_id="scope", code="facts.model_scope_conflicts_with_planning_features", owner_patch_types=["facts"], dependent_patch_types=["materials", "universes", "pin_map", "assembly_catalog", "core_layout"], original_issue_codes=[str(x.get("code")) for x in issues], canonical_owner_patch_hashes={"facts": owner_hashes.get("facts", "")}, repair_action="targeted_facts"))
    density: dict[str, list[str]] = {}
    for item in issues:
        if item.get("code") in {"fullcore.grid_density_missing", "materials.execution_density_required"}:
            material = str(item.get("material_id") or item.get("actual") or "unknown")
            density.setdefault(material, []).append(str(item.get("consumer_id") or item.get("path") or ""))
    for material, consumers in density.items():
        result.append(PlanningRootCause(root_cause_id=f"density:{material}", code="materials.execution_density_required", owner_patch_types=["materials"], dependent_patch_types=["universes", "axial_overlays"], original_issue_codes=["fullcore.grid_density_missing"], affected_ids=consumers, canonical_owner_patch_hashes={"materials": owner_hashes.get("materials", "")}, repair_action="targeted_materials", metadata={"material_id": material, "required_property": "density_g_cm3"}))
    if any(item.get("code") in {"planning.required_patch_omitted", "facts.localized_insert_profile_contract_missing"} for item in issues):
        result.append(PlanningRootCause(root_cause_id="profile_plan", code="planning.required_patch_omitted", owner_patch_types=["planning_task_plan"], dependent_patch_types=["localized_insert_profiles", "assembly_catalog", "core_layout"], original_issue_codes=[str(x.get("code")) for x in issues], canonical_owner_patch_hashes={"planning_task_plan": owner_hashes.get("planning_task_plan", "")}, repair_action="recompute_task_plan"))
    return result


def record_targeted_retry_attempt(state: Any, cause: PlanningRootCause, *, candidate_hash: str | None = None, max_attempts: int = 2) -> dict[str, Any]:
    """Persist a bounded owner-level attempt without clearing unrelated state."""
    candidate = candidate_hash or next(iter(cause.canonical_owner_patch_hashes.values()), "")
    attempts = int(state.root_cause_attempts_by_fingerprint.get(cause.fingerprint, 0))
    candidates = list(state.root_cause_candidate_hashes.get(cause.fingerprint, []))
    same_candidate = candidate in candidates
    attempts += 1
    state.root_cause_attempts_by_fingerprint[cause.fingerprint] = attempts
    candidates.append(candidate)
    state.root_cause_candidate_hashes[cause.fingerprint] = candidates
    no_progress = same_candidate and attempts >= 2
    record = {"fingerprint": cause.fingerprint, "code": cause.code, "owner_patch_types": cause.owner_patch_types, "candidate_hash": candidate, "attempt": attempts, "same_candidate": same_candidate, "no_progress": no_progress, "budget_exhausted": attempts > max_attempts}
    state.root_cause_retry_history.append(record)
    return record
