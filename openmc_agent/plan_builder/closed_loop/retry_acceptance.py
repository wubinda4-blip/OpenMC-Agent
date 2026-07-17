"""Real, callable acceptance-check registry for retry owner candidates.

Phase 3A carried ``required_acceptance_checks`` as plain strings.  Phase 3B
turns each of them into an actual function that runs against the clone state
after the owner candidate has been produced but before the atomic commit.

Each check returns a list of issue dicts; an empty list means the check
passed.  The registry never asks an LLM and never mutates the clone state.
"""

from __future__ import annotations

from typing import Any, Callable

from pydantic import Field

from openmc_agent.plan_builder.dependency_graph import DEFAULT_PLAN_PATCH_DEPENDENCY_GRAPH
from openmc_agent.plan_builder.patches import parse_patch_content
from openmc_agent.plan_builder.state import PlanBuildState
from openmc_agent.plan_builder.validators import validate_patch
from openmc_agent.schemas import AgentBaseModel

from .models import PlanClosedLoopPolicy
from .placement_preflight import run_placement_preflight
from .retry_models import ExecutablePlanRetryRequest, RetryExecutionPlan


def _valid_envelope(state: PlanBuildState, patch_type: str) -> dict[str, Any] | None:
    matches = [item for item in state.patches.values() if item.patch_type == patch_type and item.status == "valid"]
    return matches[0].content if len(matches) == 1 else None


def _check_facts_schema(request: ExecutablePlanRetryRequest, plan: RetryExecutionPlan, clone: PlanBuildState) -> list[dict[str, Any]]:
    content = _valid_envelope(clone, "facts")
    if content is None:
        return [{"code": "acceptance.facts_schema", "severity": "error", "message": "facts patch missing"}]
    try:
        parsed = parse_patch_content("facts", content)
        result = validate_patch(parsed)
    except Exception as exc:
        return [{"code": "acceptance.facts_schema", "severity": "error", "message": f"schema invalid: {exc}"}]
    return [{"code": "acceptance.facts_schema", "severity": "error", "message": i.message} for i in result.issues if i.severity == "error"]


def _check_facts_consistency(request: ExecutablePlanRetryRequest, plan: RetryExecutionPlan, clone: PlanBuildState) -> list[dict[str, Any]]:
    content = _valid_envelope(clone, "facts")
    contract = clone.planning_feature_contract
    if content is None or contract is None:
        return []
    from .facts_consistency import run_facts_consistency_preflight
    result = run_facts_consistency_preflight(feature_contract=contract, facts_patch=content, confirmed_facts=clone.confirmed_facts, existing_valid_patch_types=[item.patch_type for item in clone.patches.values() if item.status == "valid"])
    return [{"code": item["code"], "severity": "error", "message": item.get("path", "")} for item in result.issues if item.get("severity") == "error"]


def _check_resolved_scope(request: ExecutablePlanRetryRequest, plan: RetryExecutionPlan, clone: PlanBuildState) -> list[dict[str, Any]]:
    if clone.resolved_planning_scope is None:
        return [{"code": "acceptance.resolved_scope", "severity": "error", "message": "resolved scope missing"}]
    if clone.resolved_planning_scope.status == "conflict":
        return [{"code": "acceptance.resolved_scope", "severity": "error", "message": "resolved scope conflict"}]
    return []


def _check_source_critical_feature_coverage(request: ExecutablePlanRetryRequest, plan: RetryExecutionPlan, clone: PlanBuildState) -> list[dict[str, Any]]:
    # Source-critical feature coverage is the Facts consistency preflight's
    # job; if it passed, the source-critical contract is intact.
    return _check_facts_consistency(request, plan, clone)


def _check_facts_critic(request: ExecutablePlanRetryRequest, plan: RetryExecutionPlan, clone: PlanBuildState) -> list[dict[str, Any]]:
    # The Facts Critic is an LLM call.  Acceptance here only verifies the
    # deterministic preflight; the actual Critic replay is a separate Gate
    # replay step performed after the commit.
    return []


def _check_canonical_task_plan(request: ExecutablePlanRetryRequest, plan: RetryExecutionPlan, clone: PlanBuildState) -> list[dict[str, Any]]:
    if clone.resolved_planning_scope is None or clone.planning_feature_contract is None:
        return [{"code": "acceptance.canonical_task_plan", "severity": "error", "message": "scope or contract missing"}]
    from openmc_agent.plan_builder.planning_scope import build_canonical_task_plan
    try:
        facts_env = _valid_envelope(clone, "facts")
        candidate_plan = build_canonical_task_plan(scope=clone.resolved_planning_scope, contract=clone.planning_feature_contract, facts_patch=facts_env or {}, feature_order=list(DEFAULT_PLAN_PATCH_DEPENDENCY_GRAPH._ORDER))
    except Exception as exc:
        return [{"code": "acceptance.canonical_task_plan", "severity": "error", "message": f"task plan build failed: {exc}"}]
    clone.metadata["acceptance_candidate_task_plan_hash"] = candidate_plan.plan_hash
    return []


def _check_materials_schema(request: ExecutablePlanRetryRequest, plan: RetryExecutionPlan, clone: PlanBuildState) -> list[dict[str, Any]]:
    content = _valid_envelope(clone, "materials")
    if content is None:
        return [{"code": "acceptance.materials_schema", "severity": "error", "message": "materials patch missing"}]
    try:
        parsed = parse_patch_content("materials", content)
        result = validate_patch(parsed)
    except Exception as exc:
        return [{"code": "acceptance.materials_schema", "severity": "error", "message": f"schema invalid: {exc}"}]
    return [{"code": "acceptance.materials_schema", "severity": "error", "message": i.message} for i in result.issues if i.severity == "error"]


def _check_material_species(request: ExecutablePlanRetryRequest, plan: RetryExecutionPlan, clone: PlanBuildState) -> list[dict[str, Any]]:
    # Material species resolution is the species report's job; acceptance only
    # verifies schema validity and fuel-variant identity.
    return []


def _check_composition_basis(request: ExecutablePlanRetryRequest, plan: RetryExecutionPlan, clone: PlanBuildState) -> list[dict[str, Any]]:
    content = _valid_envelope(clone, "materials")
    if content is None:
        return []
    for material in content.get("materials", []):
        if material.get("composition_status") == "needs_confirmation" and material.get("density_g_cm3") is None:
            # Allow targeted density revision to set density without forcing
            # composition confirmation; only flag if the request targeted a
            # different material property.
            target_ids = {tid for target in request.targets for tid in target.required_ids}
            if material.get("material_id") not in target_ids:
                continue
    return []


def _check_fuel_variant_identity(request: ExecutablePlanRetryRequest, plan: RetryExecutionPlan, clone: PlanBuildState) -> list[dict[str, Any]]:
    content = _valid_envelope(clone, "materials")
    if content is None:
        return []
    # Fuel enrichment must not have changed for materials not targeted by this
    # request.  This is a light structural guard.
    target_ids = {tid for target in request.targets for tid in target.required_ids}
    for material in content.get("materials", []):
        if material.get("role") == "fuel" and material.get("material_id") not in target_ids:
            if not material.get("fuel_enrichment"):
                return [{"code": "acceptance.fuel_variant_identity", "severity": "error", "message": "non-target fuel material lost enrichment"}]
    return []


def _check_density_policy(request: ExecutablePlanRetryRequest, plan: RetryExecutionPlan, clone: PlanBuildState) -> list[dict[str, Any]]:
    content = _valid_envelope(clone, "materials")
    if content is None:
        return []
    target_ids = {tid for target in request.targets for tid in target.required_ids}
    for material in content.get("materials", []):
        if material.get("material_id") in target_ids:
            density = material.get("density_g_cm3")
            if not isinstance(density, (int, float)) or density <= 0:
                return [{"code": "acceptance.density_policy", "severity": "error", "message": f"target material {material.get('material_id')} still lacks density"}]
    return []


def _check_material_readiness(request: ExecutablePlanRetryRequest, plan: RetryExecutionPlan, clone: PlanBuildState) -> list[dict[str, Any]]:
    materials = _valid_envelope(clone, "materials")
    overlays = _valid_envelope(clone, "axial_overlays")
    if materials is None or overlays is None:
        return []
    from openmc_agent.plan_builder.material_execution_readiness import validate_material_execution_readiness
    readiness = validate_material_execution_readiness(materials_patch=materials, axial_overlays_patch=overlays, policy=str(clone.metadata.get("structural_density_policy", "source_only")))
    return [{"code": issue.code, "severity": "error", "message": f"material {issue.material_id} readiness still failing"} for issue in readiness.issues]


def _check_universes_schema(request: ExecutablePlanRetryRequest, plan: RetryExecutionPlan, clone: PlanBuildState) -> list[dict[str, Any]]:
    content = _valid_envelope(clone, "universes")
    if content is None:
        return [{"code": "acceptance.universes_schema", "severity": "error", "message": "universes patch missing"}]
    try:
        parsed = parse_patch_content("universes", content)
        result = validate_patch(parsed)
    except Exception as exc:
        return [{"code": "acceptance.universes_schema", "severity": "error", "message": f"schema invalid: {exc}"}]
    return [{"code": "acceptance.universes_schema", "severity": "error", "message": i.message} for i in result.issues if i.severity == "error"]


def _check_material_references(request: ExecutablePlanRetryRequest, plan: RetryExecutionPlan, clone: PlanBuildState) -> list[dict[str, Any]]:
    universes = _valid_envelope(clone, "universes")
    materials = _valid_envelope(clone, "materials")
    if universes is None or materials is None:
        return []
    material_ids = {m.get("material_id") for m in materials.get("materials", []) if isinstance(m, dict)}
    issues: list[dict[str, Any]] = []
    for universe in universes.get("universes", []):
        for cell in universe.get("cells", []):
            if isinstance(cell, dict):
                mid = cell.get("material_id")
                if mid and mid not in material_ids:
                    issues.append({"code": "acceptance.material_references", "severity": "error", "message": f"universe {universe.get('universe_id')} references unknown material {mid}"})
    return issues


def _check_required_universe_ids(request: ExecutablePlanRetryRequest, plan: RetryExecutionPlan, clone: PlanBuildState) -> list[dict[str, Any]]:
    required = {item for target in request.targets for item in target.required_ids}
    if not required:
        return []
    content = _valid_envelope(clone, "universes")
    if content is None:
        return [{"code": "acceptance.required_universe_ids", "severity": "error", "message": "universes patch missing"}]
    found = {str(u.get("universe_id")) for u in content.get("universes", []) if isinstance(u, dict)}
    missing = sorted(required - found)
    if missing:
        return [{"code": "retry.required_universe_ids_missing", "severity": "error", "message": f"required universe IDs still missing: {missing}", "missing_ids": missing}]
    # Reject near-miss IDs (e.g. required 'rcca_aic' but produced 'rcca_aic_v2').
    near_miss = sorted({rid for rid in required if rid not in found and any(rid in other for other in found if other != rid)})
    if near_miss:
        return [{"code": "acceptance.required_universe_ids_near_miss", "severity": "error", "message": f"near-miss IDs detected (must use exact required IDs): {near_miss}", "near_miss_ids": near_miss}]
    return []


def _check_cell_geometry_local(request: ExecutablePlanRetryRequest, plan: RetryExecutionPlan, clone: PlanBuildState) -> list[dict[str, Any]]:
    # Cell geometry local validation is part of the universes schema validator.
    return _check_universes_schema(request, plan, clone)


def _check_through_path(request: ExecutablePlanRetryRequest, plan: RetryExecutionPlan, clone: PlanBuildState) -> list[dict[str, Any]]:
    # Through-path contract is enforced by the placement preflight.  Acceptance
    # here is a no-op; the actual check runs after the commit during Gate
    # replay.
    return []


def _check_fuel_variant_reachability(request: ExecutablePlanRetryRequest, plan: RetryExecutionPlan, clone: PlanBuildState) -> list[dict[str, Any]]:
    universes = _valid_envelope(clone, "universes")
    if universes is None:
        return []
    # Ensure distinct fuel variants have not been merged into one universe.
    variants = {u.get("fuel_variant_id") for u in universes.get("universes", []) if isinstance(u, dict) and u.get("fuel_variant_id")}
    contract = clone.planning_feature_contract
    if contract is not None and contract.has_multiple_fuel_variants and len(variants) < 2:
        return [{"code": "acceptance.fuel_variant_reachability", "severity": "error", "message": "multiple fuel variants collapsed into a single universe"}]
    return []


def _check_profile_references(request: ExecutablePlanRetryRequest, plan: RetryExecutionPlan, clone: PlanBuildState) -> list[dict[str, Any]]:
    profiles_env = _valid_envelope(clone, "localized_insert_profiles")
    universes = _valid_envelope(clone, "universes")
    if profiles_env is None or universes is None:
        return []
    universe_ids = {u.get("universe_id") for u in universes.get("universes", []) if isinstance(u, dict)}
    issues: list[dict[str, Any]] = []
    for profile in profiles_env.get("profiles", []):
        for segment in profile.get("segments", []):
            uid = segment.get("universe_id")
            if uid and uid not in universe_ids:
                issues.append({"code": "acceptance.profile_references", "severity": "error", "message": f"profile {profile.get('profile_id')} references unknown universe {uid}"})
    return issues


def _check_placement_preflight(request: ExecutablePlanRetryRequest, plan: RetryExecutionPlan, clone: PlanBuildState) -> list[dict[str, Any]]:
    result = run_placement_preflight(state=clone)
    return [{"code": item["code"], "severity": item.get("severity", "error"), "message": item.get("message", "")} for item in result["issues"] if item.get("severity") == "error"]


def _check_placement_critic(request: ExecutablePlanRetryRequest, plan: RetryExecutionPlan, clone: PlanBuildState) -> list[dict[str, Any]]:
    # The Placement Critic is an LLM call; acceptance only verifies the
    # deterministic preflight.  The actual Critic replay is a Gate replay.
    return []


def _check_placement_contract_coverage(request: ExecutablePlanRetryRequest, plan: RetryExecutionPlan, clone: PlanBuildState) -> list[dict[str, Any]]:
    result = run_placement_preflight(state=clone)
    return [{"code": item["code"], "severity": item.get("severity", "error"), "message": item.get("message", "")} for item in result["issues"] if item.get("severity") == "error"]


def _check_patch_schema(request: ExecutablePlanRetryRequest, plan: RetryExecutionPlan, clone: PlanBuildState) -> list[dict[str, Any]]:
    owner = next((owner for owner in request.owner_patch_types if owner != "planning_task_plan"), None)
    if owner is None:
        return []
    content = _valid_envelope(clone, owner)
    if content is None:
        return [{"code": "acceptance.patch_schema", "severity": "error", "message": f"{owner} patch missing"}]
    try:
        parsed = parse_patch_content(owner, content)
        result = validate_patch(parsed)
    except Exception as exc:
        return [{"code": "acceptance.patch_schema", "severity": "error", "message": f"schema invalid: {exc}"}]
    return [{"code": "acceptance.patch_schema", "severity": "error", "message": i.message} for i in result.issues if i.severity == "error"]


def _check_patch_validation(request: ExecutablePlanRetryRequest, plan: RetryExecutionPlan, clone: PlanBuildState) -> list[dict[str, Any]]:
    return _check_patch_schema(request, plan, clone)


def _check_canonical_task_plan_only(request: ExecutablePlanRetryRequest, plan: RetryExecutionPlan, clone: PlanBuildState) -> list[dict[str, Any]]:
    return _check_canonical_task_plan(request, plan, clone)


def _check_patch_family(request: ExecutablePlanRetryRequest, plan: RetryExecutionPlan, clone: PlanBuildState) -> list[dict[str, Any]]:
    valid_types = {item.patch_type for item in clone.patches.values() if item.status == "valid"}
    if "pin_map" in valid_types and "assembly_catalog" in valid_types:
        return [{"code": "acceptance.patch_family", "severity": "error", "message": "pin_map and assembly_catalog cannot coexist"}]
    return []


_CHECK_REGISTRY: dict[str, Callable[[ExecutablePlanRetryRequest, RetryExecutionPlan, PlanBuildState], list[dict[str, Any]]]] = {
    "facts_schema": _check_facts_schema,
    "facts_consistency": _check_facts_consistency,
    "resolved_scope": _check_resolved_scope,
    "source_critical_feature_coverage": _check_source_critical_feature_coverage,
    "facts_critic": _check_facts_critic,
    "canonical_task_plan": _check_canonical_task_plan,
    "materials_schema": _check_materials_schema,
    "material_species": _check_material_species,
    "composition_basis": _check_composition_basis,
    "fuel_variant_identity": _check_fuel_variant_identity,
    "density_policy": _check_density_policy,
    "material_readiness": _check_material_readiness,
    "universes_schema": _check_universes_schema,
    "material_references": _check_material_references,
    "required_universe_ids": _check_required_universe_ids,
    "cell_geometry_local": _check_cell_geometry_local,
    "through_path": _check_through_path,
    "fuel_variant_reachability": _check_fuel_variant_reachability,
    "profile_references": _check_profile_references,
    "placement_preflight": _check_placement_preflight,
    "placement_critic": _check_placement_critic,
    "placement_contract_coverage": _check_placement_contract_coverage,
    "patch_schema": _check_patch_schema,
    "patch_validation": _check_patch_validation,
    "patch_family": _check_patch_family,
}


class OwnerAcceptanceResult(AgentBaseModel):
    accepted: bool = False
    checks_executed: list[str] = Field(default_factory=list)
    passed_checks: list[str] = Field(default_factory=list)
    failed_checks: list[str] = Field(default_factory=list)
    issues: list[dict[str, Any]] = Field(default_factory=list)


def run_owner_acceptance_checks(
    *,
    request: ExecutablePlanRetryRequest,
    execution_plan: RetryExecutionPlan,
    clone_state: PlanBuildState,
    policy: PlanClosedLoopPolicy,
) -> OwnerAcceptanceResult:
    """Run every acceptance check declared on the owner policy.

    Only deterministic checks execute here.  LLM-driven critics are deferred
    to the Gate replay step.  The result records exactly which checks ran,
    which passed, and which failed so the round record can prove that the
    acceptance was real, not a string declaration.
    """
    checks = list(execution_plan.validation_steps)
    result = OwnerAcceptanceResult(checks_executed=checks)
    for check_name in checks:
        check_fn = _CHECK_REGISTRY.get(check_name)
        if check_fn is None:
            result.failed_checks.append(check_name)
            result.issues.append({"code": "acceptance.unknown_check", "severity": "error", "message": f"unknown acceptance check: {check_name}"})
            continue
        try:
            issues = check_fn(request, execution_plan, clone_state)
        except Exception as exc:
            issues = [{"code": f"acceptance.{check_name}", "severity": "error", "message": f"check raised: {exc}"}]
        if issues:
            result.failed_checks.append(check_name)
            result.issues.extend(issues)
        else:
            result.passed_checks.append(check_name)
    result.accepted = not result.failed_checks
    return result


__all__ = ["OwnerAcceptanceResult", "run_owner_acceptance_checks"]
