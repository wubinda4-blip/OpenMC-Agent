"""VERA4 real-LLM render/smoke canary.

Loads an assembled SimulationPlan from a real-LLM incremental run,
renders it to model.py, exports XML, and runs a low-cost smoke test.
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from openmc_agent.schemas import SimulationPlan
from openmc_agent.renderers.core import CoreRenderer
from openmc_agent.campaign_eval.vera4_base_acceptance import run_full_acceptance
from openmc_agent.campaign_eval.canary_status import (
    evaluate_planning_canary,
    PLANNING_CANARY_PASSED,
    RENDER_CANARY_PASSED,
    BASE_GEOMETRY_CANARY_PASSED,
    BASE_SMOKE_CANARY_PASSED,
)
from openmc_agent.plan_builder.grid_geometry_validation import (
    build_grid_geometry_reachability_report,
    verify_fuel_variant_identity_after_decoration,
)


def main() -> int:
    state_path = Path("data/runs/VERA4_ds/fresh/plan_build_state.json")
    if not state_path.exists():
        print(f"Error: {state_path} not found")
        return 1

    with open(state_path) as f:
        state_dict = json.load(f)

    plan_dict = state_dict.get("assembled_plan")
    if not plan_dict:
        print("Error: no assembled_plan in state")
        return 1

    plan = SimulationPlan.model_validate(plan_dict)

    # Update capability report from renderer (assembled plans start with "none").
    renderer = CoreRenderer()
    cap = renderer.can_render(plan)
    plan.capability_report = cap
    if cap.supported_renderer == "none" and cap.renderability in ("exportable", "runnable"):
        plan.capability_report.supported_renderer = "core"

    model = plan.complex_model

    print("=" * 60)
    print("VERA4 Real-LLM Render/Smoke Canary")
    print("=" * 60)
    print(f"Materials: {len(model.materials)}")
    print(f"Universes: {len(model.universes)}")
    print(f"Cells: {len(model.cells)}")
    print(f"Lattices: {len(model.lattices)}")
    print(f"Assemblies: {len(model.assemblies)}")

    # ---- 1. VERA4 Acceptance A-G ----
    print("\n--- VERA4 Acceptance A-G ---")
    acceptance = run_full_acceptance(plan)
    print(f"Acceptance: ok={acceptance.ok}, pass={acceptance.passed_count}, fail={acceptance.failed_count}")
    level_f_ok = True
    level_g_ok = True
    for c in acceptance.checks:
        if c.level == "F" and not c.passed:
            level_f_ok = False
        if c.level == "G" and not c.passed:
            level_g_ok = False
    print(f"  Level F (grid): {'PASS' if level_f_ok else 'FAIL'}")
    print(f"  Level G (fuel):  {'PASS' if level_g_ok else 'FAIL'}")

    # ---- 2. Grid-decorated fuel identity ----
    print("\n--- Grid-decorated fuel identity ---")
    try:
        grid_report = build_grid_geometry_reachability_report(plan)
        print(f"  Grid reachability: {grid_report.result}")
        print(f"  Decorated universes: {len(grid_report.decorated_universe_ids)}")
    except Exception as e:
        print(f"  Grid reachability error: {e}")

    # ---- 3. Render model.py ----
    print("\n--- Render model.py ---")
    print(f"  Renderability: {cap.renderability}")

    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir = Path(tmpdir)
        rr = renderer.render(plan, tmpdir)
        model_file = tmpdir / "model.py"
        print(f"  Render result: renderability={rr.renderability}")
        print(f"  model.py exists: {model_file.exists()}")

        if not model_file.exists():
            print("  ERROR: model.py not generated")
            return 1

        # ---- 4. Python compile ----
        print("\n--- Python compile ---")
        result = subprocess.run(
            [sys.executable, "-c", f"import py_compile; py_compile.compile('{model_file}', doraise=True)"],
            capture_output=True, text=True,
        )
        if result.returncode != 0:
            print(f"  COMPILE FAILED: {result.stderr[:500]}")
            return 1
        print("  PASS")

        # ---- 5. XML export ----
        print("\n--- XML export ---")
        result = subprocess.run(
            [sys.executable, str(model_file)],
            cwd=str(tmpdir),
            capture_output=True, text=True, timeout=120,
        )
        if result.returncode != 0:
            print(f"  XML EXPORT FAILED (rc={result.returncode})")
            print(f"  stderr: {result.stderr[:500]}")
            return 1
        xml_files = list(tmpdir.glob("*.xml"))
        print(f"  XML files: {[f.name for f in xml_files]}")

        # Check essential XMLs exist
        for required_xml in ("materials.xml", "geometry.xml", "settings.xml"):
            if not (tmpdir / required_xml).exists():
                print(f"  MISSING: {required_xml}")
                return 1
        print("  PASS")

        # ---- 6. Geometry.from_xml ----
        print("\n--- Geometry.from_xml ---")
        try:
            import openmc
            geom = openmc.Geometry.from_xml(str(tmpdir / "geometry.xml"), str(tmpdir / "materials.xml"))
            print(f"  PASS (bounding_box={geom.bounding_box})")
        except Exception as e:
            print(f"  Geometry error: {e}")

        # ---- 7. Low-cost smoke test ----
        print("\n--- Low-cost smoke test ---")
        try:
            from openmc_agent.tools import run_smoke_test
            smoke = run_smoke_test(tmpdir, plan, max_particles=500, max_batches=5, timeout=300)
            if smoke.ok:
                print(f"  PASS")
                sp_files = list(tmpdir.glob("statepoint.*.h5"))
                if sp_files:
                    import h5py
                    with h5py.File(sp_files[0], "r") as h5:
                        keff = h5["k_execution"][-1] if "k_execution" in h5 else None
                        if keff is not None:
                            print(f"  keff = {keff[0]:.5f} ± {keff[1]:.5f}")
            else:
                print(f"  SMOKE FAILED: {smoke.error[:300]}")
        except Exception as e:
            print(f"  Smoke test error: {e}")

    print("\n" + "=" * 60)
    print("DONE")
    print("=" * 60)
    return 0


if __name__ == "__main__":
    sys.exit(main())
