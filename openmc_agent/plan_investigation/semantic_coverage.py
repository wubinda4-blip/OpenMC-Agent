"""Input-driven semantic coverage for investigation sessions."""

from __future__ import annotations

import json
from typing import Any
from pydantic import Field
from openmc_agent.schemas import AgentBaseModel


class SemanticCoverageTarget(AgentBaseModel):
    target_id: str
    semantic_kind: str
    required: bool = True
    covered: bool = False
    source_backed: bool = False
    human_confirmed: bool = False
    evidence_claim_ids: list[str] = Field(default_factory=list)
    unresolved_reason: str | None = None


class SemanticCoverage(AgentBaseModel):
    patch_type: str
    targets: list[SemanticCoverageTarget] = Field(default_factory=list)
    total_targets: int = 0
    covered_targets: int = 0
    source_backed_targets: int = 0
    human_confirmed_targets: int = 0
    unresolved_targets: int = 0
    explicit_unresolved_targets: int = 0
    coverage_complete: bool = False

    def to_dict(self) -> dict[str, Any]:
        return self.model_dump(mode="json")


def _claim_tokens(claim: Any) -> str:
    payload = {
        "subject": getattr(claim, "subject", ""),
        "predicate": getattr(claim, "predicate", ""),
        "qualifiers": getattr(claim, "qualifiers", {}),
        "value": getattr(claim, "value", None),
        "metadata": getattr(claim, "metadata", {}),
        "required_by_json_paths": getattr(claim, "required_by_json_paths", ()),
    }
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)


def _targets_for_context(context: Any) -> list[SemanticCoverageTarget]:
    patch_type = str(getattr(context, "patch_type", ""))
    if patch_type == "facts":
        try:
            from .executor_injection import _semantic_targets_for_feature_contract

            return [
                SemanticCoverageTarget(
                    target_id=item.target_id,
                    semantic_kind=item.semantic_kind,
                    required=item.required,
                )
                for item in _semantic_targets_for_feature_contract(
                    getattr(context, "feature_contract", None)
                )
            ]
        except Exception:
            return [
                SemanticCoverageTarget(
                    target_id="model_scope", semantic_kind="model_scope", required=True
                )
            ]

    requirements = None
    if patch_type == "materials":
        requirements = getattr(getattr(context, "material_requirement_set", None), "requirements", None)
    elif patch_type == "universes":
        requirements = getattr(getattr(context, "universe_requirement_set", None), "requirements", None)
    targets: list[SemanticCoverageTarget] = []
    for requirement in requirements or ():
        requirement_id = str(getattr(requirement, "requirement_id", "")).strip()
        if not requirement_id:
            continue
        kind = str(
            getattr(requirement, "role", None)
            or getattr(requirement, "profile_kind", None)
            or getattr(requirement, "component_kind", None)
            or patch_type
        )
        targets.append(
            SemanticCoverageTarget(
                target_id=f"{patch_type}:{requirement_id}",
                semantic_kind=kind,
            )
        )
    if not targets:
        targets.append(
            SemanticCoverageTarget(
                target_id=f"{patch_type}:source_backed_claim",
                semantic_kind="source_backed_claim",
            )
        )
    return targets


def compile_semantic_coverage(
    *, context: Any, ledger: Any, evidence_claim_ids: list[str] | tuple[str, ...] = ()
) -> SemanticCoverage:
    """Compile coverage from deterministic targets and ledger claims.

    A target is covered only by a claim whose serialized semantic payload
    contains the deterministic requirement id, or by a Facts predicate with
    the exact semantic kind. Source-backed and human-confirmed evidence are
    tracked separately; no value is inferred from the LLM output.
    """

    targets = _targets_for_context(context)
    claims = getattr(ledger, "claims", {}) if ledger is not None else {}
    selected_ids = set(evidence_claim_ids)
    selected_claims = {
        claim_id: claim
        for claim_id, claim in claims.items()
        if not selected_ids or claim_id in selected_ids
    }
    patch_type = str(getattr(context, "patch_type", ""))
    explicit_unresolved_count = 0
    for target in targets:
        for claim_id, claim in selected_claims.items():
            predicate = str(getattr(claim, "predicate", ""))
            token_text = _claim_tokens(claim)
            exact_fact = patch_type == "facts" and predicate == target.semantic_kind
            exact_requirement = patch_type != "facts" and target.target_id.split(":", 1)[-1] in token_text
            generic_source = target.semantic_kind == "source_backed_claim" and bool(
                getattr(claim, "source_refs", ())
            )
            if not (exact_fact or exact_requirement or generic_source):
                continue
            source_backed = bool(getattr(claim, "source_refs", ()))
            human_confirmed = bool(getattr(claim, "confirmed_by_human", False))
            status = getattr(claim, "status", "")
            status_value = getattr(status, "value", status)
            explicit_unresolved = str(status_value).lower() == "unresolved"
            if not (source_backed or human_confirmed or explicit_unresolved):
                continue
            target.covered = True
            target.source_backed = source_backed
            target.human_confirmed = human_confirmed
            target.evidence_claim_ids.append(claim_id)
            if explicit_unresolved:
                explicit_unresolved_count += 1
                target.unresolved_reason = "explicit unresolved input"
            break
        if not target.covered:
            target.unresolved_reason = "required target not covered"

    total = len(targets)
    covered = sum(1 for target in targets if target.covered)
    source_backed = sum(1 for target in targets if target.source_backed)
    human_confirmed = sum(1 for target in targets if target.human_confirmed)
    unresolved = sum(
        1
        for target in targets
        if target.required and not target.covered
    )
    return SemanticCoverage(
        patch_type=patch_type,
        targets=targets,
        total_targets=total,
        covered_targets=covered,
        source_backed_targets=source_backed,
        human_confirmed_targets=human_confirmed,
        unresolved_targets=unresolved,
        explicit_unresolved_targets=explicit_unresolved_count,
        coverage_complete=unresolved == 0,
    )


__all__ = ["SemanticCoverage", "SemanticCoverageTarget", "compile_semantic_coverage"]
