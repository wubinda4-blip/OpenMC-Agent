#!/usr/bin/env python3
"""Focused test: Materials fragment pipeline with real GLM-5.2."""
import json, os, sys, time

from openmc_agent.llm import _client_for_model
from openmc_agent.plan_builder.llm_adapter import make_patch_llm_client
from openmc_agent.plan_builder.materials_fragment_generation import (
    build_material_manifest, validate_material_manifest,
)
from openmc_agent.plan_builder.materials_patch_pipeline import generate_materials_patch
from openmc_agent.plan_builder.material_requirements import (
    MaterialGenerationRequirement, MaterialGenerationRequirementSet,
)
from openmc_agent.plan_builder.state import PlanBuildState

reqs = [
    MaterialGenerationRequirement(requirement_id="mreq_fuel_1", role="fuel", source_variant_id="region_1", preferred_name="UO2 Fuel 2.11 wt%"),
    MaterialGenerationRequirement(requirement_id="mreq_coolant", role="coolant", preferred_name="Borated Water"),
]
rs = MaterialGenerationRequirementSet(requirements=tuple(reqs), inventory_hash="test_inv")
manifest = build_material_manifest(rs)
print(f"Manifest: {len(manifest.items)} items", flush=True)
for item in manifest.items:
    print(f"  {item.material_id} role={item.role} variant={item.source_variant_id}", flush=True)

req_text = open("Input/VERA4_problem.md").read()
state = PlanBuildState(state_id="test_materials", requirement_text=req_text[:5000])
state.metadata["planning_material_requirement_set"] = rs.model_dump(mode="json")

base_llm = _client_for_model("zhipu:glm-5.2")
llm = make_patch_llm_client(llm=base_llm, model_name="zhipu:glm-5.2", temperature=0.0, output_mode="json_object")

print("\n=== Generating materials patch (fragmented mode) ===", flush=True)
t0 = time.time()
result = generate_materials_patch(
    requirement=req_text[:8000], state=state, llm_client=llm,
    mode="fragmented", max_fragment_attempts=2, max_total_llm_calls=15,
)
elapsed = time.time() - t0
print(f"\nResult: ok={result.ok} elapsed={elapsed:.1f}s", flush=True)
if result.ok:
    mats = result.parsed_patch.get("materials", [])
    print(f"Materials generated: {len(mats)}", flush=True)
    for m in mats:
        print(f"  {m['material_id']} role={m['role']} density={m.get('density_g_cm3')} variant={m.get('source_variant_id')}", flush=True)
    os.makedirs("data/runs", exist_ok=True)
    with open("data/runs/materials_fragment_real_test.json", "w") as f:
        json.dump(result.parsed_patch, f, indent=2, ensure_ascii=False)
    print("Saved to data/runs/materials_fragment_real_test.json", flush=True)
else:
    for issue in result.issues:
        print(f"  ISSUE: {issue.get('code')}: {issue.get('message', '')[:150]}", flush=True)

sessions = state.metadata.get("large_patch_generation_sessions", {})
for key, sess in sessions.items():
    if key.startswith("materials:"):
        print(f"\nSession: llm_calls={sess.get('llm_call_count')} completed={sess.get('completed')}", flush=True)
        for fs in sess.get("fragment_statuses", []):
            print(f"  {fs['material_id']}: status={fs['status']} qual={fs.get('qualification_status')}", flush=True)
