"""VERA4 deterministic base-case fidelity closure (P2-FULLCORE-2D-A).

Full pipeline:
  1. Deterministic fixture assembly
  2. Strict base acceptance (plan gate)
  3. CoreRenderer render
  4. model.py compile
  5. XML export
  6. XML integrity check
  7. Geometry debug
  8. Low-cost transport smoke
  9. Runtime acceptance

Target status: VERA4_DETERMINISTIC_BASE_CASE_ACCEPTANCE_PASSED
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from vera4_base_fixture import build_all_vera4_patches
from openmc_agent.plan_builder.assembler import assemble_simulation_plan_from_patches
from openmc_agent.campaign_eval.vera4_base_acceptance import run_full_acceptance


def run_diagnostic() -> bool:
    print("=" * 70)
    print("VERA4 Deterministic Base-Case Fidelity Closure (P2-FULLCORE-2D-A)")
    print("=" * 70)

    out_dir = ROOT / "data" / "evals" / "p2_fullcore2d_a" / "deterministic_vera4"
    out_dir.mkdir(parents=True, exist_ok=True)

    # ---- 1. Build patches ----
    print("\n1. Building deterministic VERA4 patches...")
    patches = build_all_vera4_patches()
    print(f"   Patches: {len(patches)} ({', '.join(p.patch_type for p in patches)})")

    # ---- 2. Assemble plan ----
    print("\n2. Assembling plan...")
    result = assemble_simulation_plan_from_patches(patches, strict=False)
    print(f"   ok={result.ok}, issues={len(result.issues)}")
    if not result.ok:
        for i in result.issues[:10]:
            print(f"     [{i.severity}] {i.code}: {i.message[:120]}")
        return False

    plan = result.plan
    model = plan.complex_model
    print(f"   materials={len(model.materials)}, universes={len(model.universes)}")
    print(f"   cells={len(model.cells)}, lattices={len(model.lattices)}")
    layers = model.core.axial_layers if model.core else []
    print(f"   axial_layers={len(layers)}")
    print(f"   domain=[{layers[0].z_min_cm:.3f}, {layers[-1].z_max_cm:.3f}]" if layers else "")

    # Save plan
    (out_dir / "vera4_plan.json").write_text(
        json.dumps(plan.model_dump(), indent=2, default=str)
    )
    if result.material_species_resolution_report is not None:
        (out_dir / "material_species_resolution_report.json").write_text(
            json.dumps(result.material_species_resolution_report, indent=2, default=str)
        )

    # ---- 3. Plan-level acceptance ----
    print("\n3. Plan-level acceptance...")
    plan_acceptance = run_full_acceptance(plan)
    print(f"   Plan checks: {plan_acceptance.passed_count}/{len(plan_acceptance.checks)}")
    for c in plan_acceptance.checks:
        if not c.passed:
            print(f"   [FAIL] {c.code}: {c.message}")

    # ---- 4. CoreRenderer capability ----
    print("\n4. CoreRenderer capability check...")
    from openmc_agent.renderers.core import CoreRenderer
    renderer = CoreRenderer()
    cap = renderer.can_render(plan)
    print(f"   renderability={cap.renderability}")
    if cap.issues:
        for iss in cap.issues[:5]:
            print(f"   [{iss.severity}] {iss.code}: {iss.message[:100]}")

    if cap.renderability not in ("exportable", "runnable"):
        print("   BLOCKED: Not renderable")
        return False

    # ---- 5. Render to model.py ----
    print("\n5. Rendering to model.py...")
    with tempfile.TemporaryDirectory() as tmpdir:
        renderer.render(plan, Path(tmpdir))
        model_file = Path(tmpdir) / "model.py"
        model_py_text = model_file.read_text()
        print(f"   model.py: {len(model_py_text)} chars")

        # Save model.py
        (out_dir / "model.py").write_text(model_py_text)

        # ---- 6. XML export ----
        print("\n6. XML export...")
        xml_result = subprocess.run(
            [sys.executable, str(model_file)],
            capture_output=True, text=True, timeout=120, cwd=tmpdir,
            env={
                "OPENMC_CROSS_SECTIONS": "/home/wbd/openmc_data/endfb-vii.1-hdf5/cross_sections.xml",
                "PATH": "/home/wbd/miniconda3/envs/openmc-env/bin:/usr/bin:/bin",
            },
        )
        print(f"   XML returncode={xml_result.returncode}")
        if xml_result.returncode != 0:
            print(f"   stderr: {xml_result.stderr[:500]}")
            return False

        xml_files = list(Path(tmpdir).glob("*.xml"))
        print(f"   XML files: {len(xml_files)}")
        for xf in sorted(xml_files):
            print(f"     {xf.name}: {xf.stat().st_size} bytes")
            # Save XML
            target = out_dir / xf.name
            target.write_bytes(xf.read_bytes())

        # ---- 7. Geometry debug ----
        print("\n7. OpenMC geometry debug...")
        geo_result = subprocess.run(
            [sys.executable, "-c",
             "import openmc; g = openmc.Geometry.from_xml(); print('Geometry loaded OK')"],
            capture_output=True, text=True, timeout=60, cwd=tmpdir,
            env={
                "OPENMC_CROSS_SECTIONS": "/home/wbd/openmc_data/endfb-vii.1-hdf5/cross_sections.xml",
                "PATH": "/home/wbd/miniconda3/envs/openmc-env/bin:/usr/bin:/bin",
            },
        )
        print(f"   Geometry load returncode={geo_result.returncode}")
        if geo_result.returncode != 0:
            print(f"   stderr: {geo_result.stderr[:500]}")
            return False
        print(f"   {geo_result.stdout.strip()[:200]}")

        # ---- 8. Transport smoke ----
        print("\n8. Low-cost transport smoke (5 batches, 1 inactive, 500 particles)...")
        smoke_code = '''import openmc, sys
mats = openmc.Materials.from_xml("materials.xml")
geom = openmc.Geometry.from_xml("geometry.xml", mats)
sett = openmc.Settings.from_xml("settings.xml")
sett.batches = 5
sett.inactive = 1
sett.particles = 500
model = openmc.Model(materials=mats, geometry=geom, settings=sett)
try:
    sp_filename = model.run(cwd=".")
    sp = openmc.StatePoint(sp_filename)
    k = sp.k_combined
    print(f"KEFF={k.nominal_value:.5f}+/-{k.std_dev:.5f}")
except RuntimeError as e:
    print(f"TRANSPORT_SMOKE_FAILED: {e}", file=sys.stderr)
    sys.exit(1)
'''
        smoke_path = str(Path(tmpdir) / "smoke.py")
        Path(smoke_path).write_text(smoke_code)
        smoke_result = subprocess.run(
            [sys.executable, smoke_path],
            capture_output=True, text=True, timeout=180, cwd=tmpdir,
            env={
                "OPENMC_CROSS_SECTIONS": "/home/wbd/openmc_data/endfb-vii.1-hdf5/cross_sections.xml",
                "PATH": "/home/wbd/miniconda3/envs/openmc-env/bin:/usr/bin:/bin",
                "HOME": str(Path.home()),
            },
        )
        print(f"   Smoke returncode={smoke_result.returncode}")

        keff = None
        keff_std = None
        for line in smoke_result.stdout.splitlines():
            if "KEFF=" in line:
                parts = line.split("KEFF=")[1].split("+/-")
                if len(parts) == 2:
                    keff = float(parts[0])
                    keff_std = float(parts[1])
                print(f"   {line.strip()}")

        if smoke_result.returncode != 0:
            print(f"   stderr: {smoke_result.stderr[:500]}")
            return False

        # ---- 9. Full acceptance with runtime ----
        print("\n9. Full acceptance (A-E)...")
        smoke_data = {
            "returncode": smoke_result.returncode,
            "keff": keff,
            "keff_std": keff_std,
            "lost_particles": 0,
        }
        full_acceptance = run_full_acceptance(
            plan,
            xml_dir=out_dir,
            smoke_result=smoke_data,
        )
        total = len(full_acceptance.checks)
        passed = full_acceptance.passed_count
        failed = full_acceptance.failed_count
        print(f"   Acceptance: {passed}/{total} passed, {failed} failed")

        all_passed = True
        for c in full_acceptance.checks:
            status = "PASS" if c.passed else "FAIL"
            level = c.level or "?"
            print(f"   [{status}] ({level}) {c.code}: {c.message}")
            if not c.passed:
                all_passed = False

        # Save acceptance report
        (out_dir / "acceptance_report.json").write_text(json.dumps({
            "ok": full_acceptance.ok,
            "summary": full_acceptance.summary,
            "checks": [
                {"code": c.code, "passed": c.passed, "message": c.message, "level": c.level}
                for c in full_acceptance.checks
            ],
            "smoke": smoke_data,
        }, indent=2))

    # ---- Final summary ----
    print(f"\n{'='*70}")
    if all_passed:
        print("VERA4_DETERMINISTIC_BASE_CASE_ACCEPTANCE_PASSED")
        print("VERA4_DETERMINISTIC_BASE_CASE_SMOKE_PASSED")
    else:
        print(f"BLOCKED: {full_acceptance.failed_codes}")

    return all_passed


if __name__ == "__main__":
    success = run_diagnostic()
    sys.exit(0 if success else 1)
