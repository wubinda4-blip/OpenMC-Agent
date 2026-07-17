"""VERA4 Phase-3B offline mutation replay script.

Runs each mutation challenge against the deterministic VERA4 fixture and
verifies the typed retry protocol produces the expected classification.
No LLM, no OpenMC, no reference data.

Usage:
    conda run -n openmc-env python scripts/vera4_phase3b_offline_mutations.py
"""

from __future__ import annotations

import copy
import json
import sys
from pathlib import Path
from typing import Any

# Ensure repo root is importable.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from openmc_agent.plan_builder.closed_loop.models import PlanClosedLoopPolicy
from openmc_agent.plan_builder.closed_loop.placement_preflight import run_placement_preflight
from openmc_agent.plan_builder.closed_loop.retry_controller import normalize_retry_request
from openmc_agent.plan_builder.closed_loop.retry_models import RetryTriggerOrigin
from openmc_agent.plan_builder.closed_loop.retry_request_builders import (
    build_retry_request_from_facts_issue,
    build_retry_request_from_material_readiness,
    build_retry_request_from_placement_dependency,
)
from openmc_agent.plan_builder.material_execution_readiness import validate_material_execution_readiness
from openmc_agent.plan_builder.state import PlanBuildState, PlanPatchEnvelope
from scripts.vera4_base_fixture import build_all_vera4_patches


def _vera4_state() -> PlanBuildState:
    state = PlanBuildState(state_id="vera4-phase3b", requirement_text="VERA4 Phase 3B offline mutation", benchmark_id="VERA4")
    for patch in build_all_vera4_patches():
        content = patch.model_dump(mode="json")
        state.add_patch(PlanPatchEnvelope(patch_id=content["patch_type"], patch_type=content["patch_type"], content=content, status="valid", source="fixture"))
    return state


def mutation_facts_scope() -> dict[str, Any]:
    """20.1: multi feature + single Facts → Facts owner."""
    state = _vera4_state()
    facts_env = next(item for item in state.patches.values() if item.patch_type == "facts")
    facts_env.content = dict(facts_env.content)
    facts_env.content["localized_insert_requirements"] = []
    request = build_retry_request_from_facts_issue(
        issue_code="facts.localized_insert_contract_missing",
        affected_json_paths=["/localized_insert_requirements"],
        finding_ids=[],
        state=state,
    )
    return {"mutation": "facts_scope", "ok": request is not None and request.owner_patch_types == ["facts"], "owner": request.owner_patch_types if request else None}


def mutation_missing_localized_insert_contract() -> dict[str, Any]:
    """20.2: delete Facts.localized_insert_requirements → Facts owner."""
    state = _vera4_state()
    facts_env = next(item for item in state.patches.values() if item.patch_type == "facts")
    facts_env.content = copy.deepcopy(facts_env.content)
    facts_env.content["localized_insert_requirements"] = []
    request = build_retry_request_from_facts_issue(
        issue_code="facts.localized_insert_contract_missing",
        affected_json_paths=["/localized_insert_requirements"],
        finding_ids=[],
        state=state,
    )
    return {"mutation": "missing_localized_insert_contract", "ok": request is not None and "facts" in request.owner_patch_types, "owner": request.owner_patch_types if request else None}


def mutation_material_density() -> dict[str, Any]:
    """20.3: delete grid-shared structural material density → Materials owner."""
    state = _vera4_state()
    materials_env = next(item for item in state.patches.values() if item.patch_type == "materials")
    mutated = copy.deepcopy(materials_env.content)
    for material in mutated.get("materials", []):
        if material.get("material_id") in {"zircaloy4", "inconel718"}:
            material["density_g_cm3"] = None
    materials_env.content = mutated
    overlays_env = next(item for item in state.patches.values() if item.patch_type == "axial_overlays")
    readiness = validate_material_execution_readiness(materials_patch=materials_env.content, axial_overlays_patch=overlays_env.content, policy="approved_library")
    if not readiness.issues:
        return {"mutation": "material_density", "ok": False, "detail": "no density issues detected"}
    request = build_retry_request_from_material_readiness(material_id=readiness.issues[0].material_id, consumer_ids=readiness.issues[0].affected_consumer_ids, required_property=readiness.issues[0].required_property, state=state)
    return {"mutation": "material_density", "ok": request is not None and request.owner_patch_types == ["materials"], "owner": request.owner_patch_types if request else None, "issue_count": len(readiness.issues)}


def mutation_required_universe() -> dict[str, Any]:
    """20.4: delete RCCA universe → Universes owner with exact required ID."""
    state = _vera4_state()
    universes_env = next(item for item in state.patches.values() if item.patch_type == "universes")
    mutated = copy.deepcopy(universes_env.content)
    mutated["universes"] = [u for u in mutated["universes"] if u.get("universe_id") != "rcca_aic"]
    universes_env.content = mutated
    request = build_retry_request_from_placement_dependency(
        dependency_patch_type="universes",
        issue_codes=["localized_insert.required_universe_missing"],
        finding_ids=["f1"],
        required_ids=["rcca_aic"],
        reason="RCCA AIC universe deleted",
        state=state,
    )
    return {"mutation": "required_universe", "ok": request is not None and "rcca_aic" in request.targets[0].required_ids, "owner": request.owner_patch_types if request else None}


def mutation_placement_intent() -> dict[str, Any]:
    """20.5: delete required placement intent → Placement preflight detects it."""
    state = _vera4_state()
    catalog_env = next(item for item in state.patches.values() if item.patch_type == "assembly_catalog")
    mutated = copy.deepcopy(catalog_env.content)
    center = next(item for item in mutated["assembly_types"] if item["assembly_type_id"] == "center_rcca")
    center["pin_map"]["localized_insert_intents"] = []
    catalog_env.content = mutated
    preflight = run_placement_preflight(state=state)
    codes = {item["code"] for item in preflight["issues"]}
    return {"mutation": "placement_intent", "ok": "localized_insert.required_placement_missing" in codes, "detected_codes": sorted(codes)}


def mutation_duplicate_candidate() -> dict[str, Any]:
    """20.6: duplicate candidate → no_progress after first detection."""
    from openmc_agent.plan_builder.closed_loop.retry_controller import execute_plan_retry_loop
    state = _vera4_state()
    # Simplify: strip most patches, keep universes+materials.
    state = PlanBuildState(state_id="vera4-phase3b-dup", requirement_text="r")
    state.add_patch(PlanPatchEnvelope(patch_id="universes", patch_type="universes", content={"patch_type": "universes", "universes": [{"universe_id": "fuel", "kind": "fuel_pin", "cells": [{"id": "c", "role": "fuel", "material_id": "fuel"}]}]}, status="valid"))
    state.add_patch(PlanPatchEnvelope(patch_id="materials", patch_type="materials", content={"patch_type": "materials", "materials": [{"material_id": "fuel", "density_g_cm3": 10.0}]}, status="valid"))
    normalize_retry_request(
        {"issue_codes": ["localized_insert.required_universe_missing"], "dependency_patch_type": "universes", "required_ids": ["abs"], "reason": "x"},
        state=state, origin=RetryTriggerOrigin.PLACEMENT_GATE,
    )
    call_count = {"n": 0}

    def _same_producer(req: Any, plan: Any, clone: PlanBuildState) -> dict[str, dict[str, Any]]:
        call_count["n"] += 1
        return {"universes": clone.patches["universes"].content}

    def _validator(req: Any, plan: Any, clone: PlanBuildState) -> list[dict[str, Any]]:
        return [{"code": "retry.required_universe_ids_missing", "severity": "error", "missing_ids": ["abs"]}]

    policy = PlanClosedLoopPolicy(mode="controlled", max_attempts_per_retry_request=3)
    outcome = execute_plan_retry_loop(state=state, policy=policy, candidate_producer=_same_producer, candidate_validator=_validator)
    return {"mutation": "duplicate_candidate", "ok": outcome.status.value == "no_progress", "outcome": outcome.status.value, "producer_calls": call_count["n"]}


def main() -> int:
    results = [
        mutation_facts_scope(),
        mutation_missing_localized_insert_contract(),
        mutation_material_density(),
        mutation_required_universe(),
        mutation_placement_intent(),
        mutation_duplicate_candidate(),
    ]
    print(json.dumps(results, indent=2, ensure_ascii=False))
    all_ok = all(item["ok"] for item in results)
    print(f"\n{'PASS' if all_ok else 'FAIL'}: {sum(item['ok'] for item in results)}/{len(results)} mutations passed")
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
