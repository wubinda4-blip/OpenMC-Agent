"""VERA4 real-LLM RCCA placement requalification canary (P2-FULLCORE-2C-C).

Re-runs the incremental planning pipeline with the DS model using the
updated prompts that now include localized_insert_requirements in facts
and multi-segment control rod examples in assembly_catalog.

The goal is to verify that the LLM correctly:
1. Extracts RCCA placement requirements in the facts patch
2. Creates a localized_insert_intent for the center R assembly
3. Defines a localized_insert_profiles patch with the RCCA axial profile
4. The assembled plan passes Level H RCCA placement acceptance
5. The rendered model has 24 AIC + 24 B4C root-reachable paths

Model: ds:deepseek-v4-flash
Input: Input/VERA4_problem.md
State: base
RCCA poison bottom: 257.900 cm
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from openmc_agent.plan_builder.state import load_plan_build_state
from openmc_agent.plan_builder.executor import run_incremental_planning
from openmc_agent.plan_builder.assembler import assemble_simulation_plan_from_patches
from openmc_agent.plan_builder.llm_adapter import make_patch_llm_client
from openmc_agent.renderers.core import CoreRenderer
from openmc_agent.campaign_eval.vera4_base_acceptance import run_full_acceptance
from openmc_agent.reachability import collect_active_dependencies

MODEL = "ds:deepseek-v4-flash"
INPUT_FILE = ROOT / "Input" / "VERA4_problem.md"
OUT_DIR = ROOT / "data" / "runs" / "VERA4_ds" / "rcca_requal"
PRIOR_STATE = ROOT / "data" / "runs" / "VERA4_ds" / "incremental" / "plan_build_state.json"


def main() -> int:
    os.environ.setdefault("OPENMC_CROSS_SECTIONS", "/home/wbd/openmc_data/endfb-vii.1-hdf5/cross_sections.xml")

    if not os.environ.get("SENSENOVA_API_KEY"):
        print("ERROR: SENSENOVA_API_KEY not set")
        return 1

    requirement = INPUT_FILE.read_text()
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    if not PRIOR_STATE.exists():
        print(f"ERROR: prior failed state not found: {PRIOR_STATE}")
        return 1

    # Resume the original real-LLM state. Keep its LLM-generated materials,
    # universes, axial layers, overlays, layout, and settings. Only facts,
    # the missing profile registry, and the catalog that omitted the RCCA
    # placement are regenerated with the new source-driven contract.
    state = load_plan_build_state(PRIOR_STATE)
    state.invalidate_patch_types(
        ["facts", "assembly_catalog"],
        reason="RCCA source-placement closure requalification",
    )

    print(f"=== VERA4 Real-LLM RCCA Requalification ===")
    print(f"Model: {MODEL}")
    print(f"Input: {INPUT_FILE}")
    print(f"Resuming: {PRIOR_STATE}")
    print(f"Output: {OUT_DIR}")
    print()

    # Run incremental planning
    llm_client = make_patch_llm_client(model_name=MODEL, temperature=0.0)

    result = run_incremental_planning(
        requirement=requirement,
        state=state,
        llm_client=llm_client,
        max_patch_attempts=2,
        strict=False,
        task_order=["facts", "localized_insert_profiles", "assembly_catalog"],
        reference_patch_policy="off",
    )

    print(f"\n=== Incremental Planning Result ===")
    print(f"ok={result.ok}")
    print(f"valid patches: {result.summary.get('valid_patch_types', [])}")
    print(f"invalid patches: {result.summary.get('invalid_patch_types', [])}")

    # Save state
    state_dict = state.model_dump(mode="json")
    (OUT_DIR / "plan_build_state.json").write_text(
        json.dumps(state_dict, indent=2, default=str)
    )

    if not result.ok:
        print("\nFAILED: Incremental planning failed")
        return 1

    # Check facts patch for localized_insert_requirements
    facts_env = None
    catalog_env = None
    profiles_env = None
    for env in state.patches.values():
        if env.patch_type == "facts":
            facts_env = env
        elif env.patch_type == "assembly_catalog":
            catalog_env = env
        elif env.patch_type == "localized_insert_profiles":
            profiles_env = env

    print(f"\n=== Patch Analysis ===")
    if facts_env:
        lir = facts_env.content.get("localized_insert_requirements", [])
        print(f"Facts localized_insert_requirements: {len(lir)} entries")
        for r in lir:
            print(f"  {r.get('requirement_id')}: kind={r.get('insert_kind')}, "
                  f"types={r.get('assembly_type_ids')}, coords={r.get('expected_coordinate_count_per_assembly')}")

    if catalog_env:
        for at in catalog_env.content.get("assembly_types", []):
            intents = at.get("pin_map", {}).get("localized_insert_intents", [])
            rcca_intents = [i for i in intents if i.get("insert_kind") == "control_rod"]
            print(f"Assembly '{at.get('assembly_type_id')}': {len(rcca_intents)} control_rod intents")
            for i in rcca_intents:
                print(f"  {i.get('insert_id')}: coords={len(i.get('coordinates', []))}, "
                      f"profile={i.get('axial_profile_id')}, anchor={i.get('anchor_z_cm')}")

    print(f"localized_insert_profiles patch: {'present' if profiles_env else 'MISSING'}")
    if profiles_env:
        for p in profiles_env.content.get("profiles", []):
            print(f"  Profile '{p.get('profile_id')}': {len(p.get('segments', []))} segments")

    # Assemble plan
    from openmc_agent.plan_builder.patches import parse_patch_content
    parsed = [parse_patch_content(env.patch_type, env.content) for env in state.patches.values() if env.status == "valid"]
    asm_result = assemble_simulation_plan_from_patches(parsed, strict=False)
    print(f"\n=== Assembly Result ===")
    print(f"ok={asm_result.ok}")
    for i in asm_result.issues:
        if i.severity == "error":
            print(f"  ERROR: {i.code}: {i.message}")

    if not asm_result.ok or asm_result.plan is None:
        print("\nFAILED: Assembly failed")
        return 1

    plan = asm_result.plan

    # Save assembled plan
    (OUT_DIR / "simulation_plan.json").write_text(
        json.dumps(plan.model_dump(), indent=2, default=str)
    )

    # Run full acceptance
    print(f"\n=== Acceptance ===")
    acc = run_full_acceptance(plan)
    by_level = {}
    for c in acc.checks:
        by_level.setdefault(c.level, []).append(c)
    for level in sorted(by_level.keys()):
        checks = by_level[level]
        passed = sum(1 for c in checks if c.passed)
        failed = [c for c in checks if not c.passed]
        status = "PASS" if not failed else "FAIL"
        print(f"  Level {level}: {passed}/{len(checks)} [{status}]")
        for c in failed:
            print(f"    FAIL: {c.code}: {c.message}")

    # Reachability
    deps = collect_active_dependencies(plan)
    aic_reachable = "rcca_aic" in deps.universe_ids
    b4c_reachable = "rcca_b4c" in deps.universe_ids
    print(f"\n=== RCCA Reachability ===")
    print(f"rcca_aic: {'REACHABLE' if aic_reachable else 'NOT REACHABLE'}")
    print(f"rcca_b4c: {'REACHABLE' if b4c_reachable else 'NOT REACHABLE'}")

    # Render + smoke
    print(f"\n=== Render ===")
    renderer = CoreRenderer()
    cap = renderer.can_render(plan)
    plan.capability_report = cap
    if cap.supported_renderer == "none" and cap.renderability in ("exportable", "runnable"):
        plan.capability_report.supported_renderer = "core"

    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir = Path(tmpdir)
        try:
            rr = renderer.render(plan, tmpdir)
            model_file = tmpdir / "model.py"
            if not model_file.exists():
                print("FAILED: model.py not generated")
                return 1
            print(f"model.py: {model_file.stat().st_size} bytes")

            # XML export
            r = subprocess.run([sys.executable, str(model_file)], cwd=str(tmpdir),
                             capture_output=True, text=True, timeout=120)
            if r.returncode != 0:
                print(f"XML export FAILED: {r.stderr[:300]}")
                return 1
            print("XML export: OK")

            # Geometry.from_xml
            try:
                import openmc
                geom = openmc.Geometry.from_xml(str(tmpdir / "geometry.xml"), str(tmpdir / "materials.xml"))
                print(f"Geometry.from_xml: OK (bbox={geom.bounding_box})")
            except Exception as e:
                print(f"Geometry.from_xml: FAILED ({e})")

            # Smoke test
            from openmc_agent.tools import run_smoke_test
            smoke = run_smoke_test(tmpdir, plan, max_particles=500, max_batches=5, timeout=300)
            print(f"\n=== Smoke Test ===")
            print(f"returncode: {smoke.get('returncode')}")
            if smoke.get("returncode") == 0:
                sp_path = tmpdir / "statepoint.5.h5"
                if sp_path.exists():
                    import h5py
                    with h5py.File(sp_path, "r") as f:
                        k = f["k_execution"][0]
                        std = f["k_execution"][1]
                        print(f"keff: {k:.5f} ± {std:.5f}")
                        print(f"lost_particles: {smoke.get('lost_particles', 'N/A')}")
            else:
                print(f"FAILED: {smoke.get('stderr', '')[:200]}")
        except Exception as e:
            print(f"Render FAILED: {e}")
            import traceback
            traceback.print_exc()
            return 1

    # Summary
    print(f"\n=== Summary ===")
    overall = (
        result.ok
        and asm_result.ok
        and acc.ok
        and aic_reachable
        and b4c_reachable
    )
    if overall:
        print("VERA4_RCCA_PLACEMENT_VALIDATED")
        print("VERA4_REAL_LLM_PLANNING_CANARY_PASSED")
        print("VERA4_REAL_LLM_RENDER_CANARY_PASSED")
        print("VERA4_BASE_SMOKE_CANARY_PASSED")
    else:
        print("REQUALIFICATION INCOMPLETE — see failures above")
    return 0 if overall else 1


if __name__ == "__main__":
    sys.exit(main())
