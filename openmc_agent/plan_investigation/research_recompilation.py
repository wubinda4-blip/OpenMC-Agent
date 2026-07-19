"""Phase 8A Step 7 — requirement recompilation diff + scoped invalidation.

Sections 8-9.  After the research evidence is committed to the Ledger,
the GeometryComponentInventory / MaterialRequirementSet /
UniverseRequirementSet must be recompiled to reflect any new claims.
The :class:`ResearchCompilationDiff] records what changed; the
:class:`ResearchInvalidationPlan] records which patches/gates must be
regenerated/replayed.
"""

from __future__ import annotations

from typing import Any

from pydantic import Field, model_validator

from openmc_agent.schemas import AgentBaseModel

from .hashing import content_hash, short_id

__all__ = [
    "ResearchCompilationDiff",
    "ResearchInvalidationPlan",
    "compute_compilation_diff",
    "build_research_invalidation_plan",
]


# ---------------------------------------------------------------------------
# Compilation diff
# ---------------------------------------------------------------------------


class ResearchCompilationDiff(AgentBaseModel):
    """Diff between requirement sets before and after a research commit."""

    diff_id: str = ""
    request_id: str
    inventory_hash_before: str = ""
    inventory_hash_after: str = ""
    material_requirement_hash_before: str = ""
    material_requirement_hash_after: str = ""
    universe_requirement_hash_before: str = ""
    universe_requirement_hash_after: str = ""
    added_component_ids: tuple[str, ...] = Field(default_factory=tuple)
    removed_component_ids: tuple[str, ...] = Field(default_factory=tuple)
    changed_component_ids: tuple[str, ...] = Field(default_factory=tuple)
    added_material_requirement_ids: tuple[str, ...] = Field(default_factory=tuple)
    changed_material_requirement_ids: tuple[str, ...] = Field(default_factory=tuple)
    added_universe_requirement_ids: tuple[str, ...] = Field(default_factory=tuple)
    changed_universe_requirement_ids: tuple[str, ...] = Field(default_factory=tuple)
    unresolved_before: int = 0
    unresolved_after: int = 0
    conflict_before: int = 0
    conflict_after: int = 0
    diff_hash: str = ""

    @property
    def has_changes(self) -> bool:
        """True when any requirement-set hash changed."""

        return (
            self.inventory_hash_before != self.inventory_hash_after
            or self.material_requirement_hash_before != self.material_requirement_hash_after
            or self.universe_requirement_hash_before != self.universe_requirement_hash_after
        )

    @model_validator(mode="after")
    def _compute_hash(self) -> "ResearchCompilationDiff":
        body = {
            "inventory_before": self.inventory_hash_before,
            "inventory_after": self.inventory_hash_after,
            "mreq_before": self.material_requirement_hash_before,
            "mreq_after": self.material_requirement_hash_after,
            "ureq_before": self.universe_requirement_hash_before,
            "ureq_after": self.universe_requirement_hash_after,
        }
        h = content_hash(body)
        object.__setattr__(self, "diff_hash", h)
        if not self.diff_id:
            object.__setattr__(self, "diff_id", short_id("cdiff", h))
        return self


def compute_compilation_diff(
    *,
    request_id: str,
    inventory_before: Any,
    inventory_after: Any,
    material_req_before: Any,
    material_req_after: Any,
    universe_req_before: Any,
    universe_req_after: Any,
) -> ResearchCompilationDiff:
    """Compare before/after requirement sets and produce a typed diff."""

    def _hash(obj: Any, fallback_field: str = "requirement_set_hash") -> str:
        if obj is None:
            return ""
        return getattr(obj, fallback_field, "") or getattr(obj, "inventory_hash", "") or ""

    def _ids(obj: Any, field: str = "requirements") -> set[str]:
        if obj is None:
            return set()
        items = getattr(obj, field, []) or []
        return {getattr(item, "requirement_id", "") or getattr(item, "component_id", "") for item in items}

    inv_before_ids = _ids(inventory_before, "radial_profiles") if inventory_before else set()
    inv_after_ids = _ids(inventory_after, "radial_profiles") if inventory_after else set()
    added_components = sorted(inv_after_ids - inv_before_ids)
    removed_components = sorted(inv_before_ids - inv_after_ids)
    mreq_before_ids = _ids(material_req_before)
    mreq_after_ids = _ids(material_req_after)
    added_mreq = sorted(mreq_after_ids - mreq_before_ids)
    ureq_before_ids = _ids(universe_req_before)
    ureq_after_ids = _ids(universe_req_after)
    added_ureq = sorted(ureq_after_ids - ureq_before_ids)
    return ResearchCompilationDiff(
        request_id=request_id,
        inventory_hash_before=_hash(inventory_before, "inventory_hash"),
        inventory_hash_after=_hash(inventory_after, "inventory_hash"),
        material_requirement_hash_before=_hash(material_req_before),
        material_requirement_hash_after=_hash(material_req_after),
        universe_requirement_hash_before=_hash(universe_req_before),
        universe_requirement_hash_after=_hash(universe_req_after),
        added_component_ids=tuple(added_components),
        removed_component_ids=tuple(removed_components),
        added_material_requirement_ids=tuple(added_mreq),
        added_universe_requirement_ids=tuple(added_ureq),
        unresolved_before=sum(1 for _ in getattr(material_req_before, "unresolved_requirements", []) or []) if material_req_before else 0,
        unresolved_after=sum(1 for _ in getattr(material_req_after, "unresolved_requirements", []) or []) if material_req_after else 0,
    )


# ---------------------------------------------------------------------------
# Scoped invalidation plan
# ---------------------------------------------------------------------------


class ResearchInvalidationPlan(AgentBaseModel):
    """Scoped invalidation plan after a research commit.

    Phase 8A Step 7 (Section 9): only invalidate patches/gates whose
    inputs actually changed.  Never clear the whole state; never
    unconditionally regenerate Facts.
    """

    request_id: str
    evidence_delta_hash: str = ""
    compilation_diff_hash: str = ""
    owner_patch_types: tuple[str, ...] = Field(default_factory=tuple)
    invalidated_patch_types: tuple[str, ...] = Field(default_factory=tuple)
    preserved_patch_types: tuple[str, ...] = Field(default_factory=tuple)
    invalidated_gate_ids: tuple[str, ...] = Field(default_factory=tuple)
    gate_replay_required: bool = False
    reason_by_patch_type: dict[str, str] = Field(default_factory=dict)
    dependency_graph_hash: str = ""
    invalidation_hash: str = ""

    @model_validator(mode="after")
    def _compute_hash(self) -> "ResearchInvalidationPlan":
        body = {
            "owner_patch_types": list(self.owner_patch_types),
            "invalidated_patch_types": list(self.invalidated_patch_types),
            "invalidated_gate_ids": list(self.invalidated_gate_ids),
            "gate_replay_required": self.gate_replay_required,
        }
        object.__setattr__(self, "invalidation_hash", content_hash(body))
        return self


def build_research_invalidation_plan(
    *,
    request_id: str,
    diff: ResearchCompilationDiff,
    gate_id: str = "material_universe",
    blocking_finding_owners: tuple[str, ...] = (),
) -> ResearchInvalidationPlan:
    """Build a scoped invalidation plan from a compilation diff.

    Rules (Section 9):

    * Material role/density/composition change → invalidate Materials
      (+ Universes if material bindings changed).
    * Radial profile / cell-role change → invalidate Universes only.
    * Evidence-only change (no requirement-set hash change) →
      invalidate nothing, just replay the gate.
    """

    invalidated: list[str] = []
    reasons: dict[str, str] = {}
    if diff.material_requirement_hash_before != diff.material_requirement_hash_after:
        invalidated.append("materials")
        reasons["materials"] = "MaterialRequirementSet hash changed"
        # Universes depend on Materials; invalidate if material bindings
        # might be affected.  We err on the side of caution when the
        # diff shows added/changed material requirements.
        if diff.added_material_requirement_ids or diff.changed_material_requirement_ids:
            invalidated.append("universes")
            reasons["universes"] = "MaterialRequirementSet added/changed requirements affect universe bindings"
    if diff.universe_requirement_hash_before != diff.universe_requirement_hash_after:
        if "universes" not in invalidated:
            invalidated.append("universes")
            reasons["universes"] = "UniverseRequirementSet hash changed"
    # Also honour the blocking-finding owners directly.
    for owner in blocking_finding_owners:
        if owner in {"materials", "universes"} and owner not in invalidated:
            invalidated.append(owner)
            reasons[owner] = f"blocking finding owner={owner}"
    # Deduplicate, preserving order.
    seen: set[str] = set()
    deduped: list[str] = []
    for p in invalidated:
        if p not in seen:
            deduped.append(p)
            seen.add(p)
    # Preserved: everything in the dependency graph not invalidated.
    # For Step 7 we only touch Materials + Universes; everything else
    # is preserved.
    preserved = [p for p in ("facts", "materials", "universes") if p not in seen]
    # Gate replay required when any patch was invalidated OR when the
    # evidence-only path still needs a fresh reviewer pass.
    gate_replay_required = bool(deduped) or (
        diff.material_requirement_hash_before == diff.material_requirement_hash_after
        and diff.universe_requirement_hash_before == diff.universe_requirement_hash_after
    )
    return ResearchInvalidationPlan(
        request_id=request_id,
        evidence_delta_hash="",
        compilation_diff_hash=diff.diff_hash,
        owner_patch_types=tuple(deduped),
        invalidated_patch_types=tuple(deduped),
        preserved_patch_types=tuple(preserved),
        invalidated_gate_ids=(gate_id,) if gate_replay_required else (),
        gate_replay_required=gate_replay_required,
        reason_by_patch_type=reasons,
    )
