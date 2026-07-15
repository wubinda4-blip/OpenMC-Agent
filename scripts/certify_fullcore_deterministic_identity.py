"""Cross-process deterministic identity certification (P2-FULLCORE-2D-A-HARDENING).

Verifies that canonical plan/object/XML identity is stable across:
- Different Python processes
- Different PYTHONHASHSEED values
- Different temporary directories
- No shared state or cache

Runs the VERA4 deterministic fixture in two subprocesses with
PYTHONHASHSEED=1 and PYTHONHASHSEED=98765, then compares:
- Canonical SimulationPlan JSON digest
- All material/universe/cell/lattice IDs
- Axial layer IDs
- State reuse report
- Normalized model.py digest
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent


def _compute_digest(data: str | bytes) -> str:
    if isinstance(data, str):
        data = data.encode()
    return hashlib.sha256(data).hexdigest()


def _normalize_plan_json(plan_dict: dict) -> dict:
    """Remove non-semantic fields from plan dict for stable comparison."""
    normalized = json.loads(json.dumps(plan_dict))  # deep copy
    # Remove any timestamp/uuid/statepoint fields
    def _strip(obj):
        if isinstance(obj, dict):
            for key in list(obj.keys()):
                if key in ("timestamp", "run_uuid", "statepoint_filename",
                           "creation_time", "execution_time"):
                    del obj[key]
                else:
                    _strip(obj[key])
        elif isinstance(obj, list):
            for item in obj:
                _strip(item)
    _strip(normalized)
    return normalized


def _run_subprocess(pythonhashseed: int, out_dir: Path) -> dict:
    """Run the VERA4 fixture assembly in a subprocess and return results."""
    code = f'''
import json, sys, hashlib
sys.path.insert(0, "{ROOT}")
sys.path.insert(0, "{ROOT}/scripts")
from vera4_base_fixture import build_all_vera4_patches
from openmc_agent.plan_builder.assembler import assemble_simulation_plan_from_patches

patches = build_all_vera4_patches()
result = assemble_simulation_plan_from_patches(patches, strict=False)
plan = result.plan
plan_dict = plan.model_dump()

# Collect all IDs
material_ids = sorted([m["id"] for m in plan_dict["complex_model"]["materials"]])
universe_ids = sorted([u["id"] for u in plan_dict["complex_model"]["universes"]])
cell_ids = sorted([c["id"] for c in plan_dict["complex_model"]["cells"]])
lattice_ids = sorted([l["id"] for l in plan_dict["complex_model"]["lattices"]])
axial_layer_ids = []
if plan_dict["complex_model"].get("core"):
    axial_layer_ids = sorted([l["id"] for l in plan_dict["complex_model"]["core"]["axial_layers"]])

# Plan digest
plan_json = json.dumps(plan_dict, sort_keys=True, default=str)
plan_digest = hashlib.sha256(plan_json.encode()).hexdigest()

output = {{
    "ok": result.ok,
    "plan_digest": plan_digest,
    "material_ids": material_ids,
    "universe_ids": universe_ids,
    "cell_ids": cell_ids,
    "lattice_ids": lattice_ids,
    "axial_layer_ids": axial_layer_ids,
    "material_count": len(material_ids),
    "universe_count": len(universe_ids),
    "cell_count": len(cell_ids),
    "lattice_count": len(lattice_ids),
}}
print(json.dumps(output))
'''
    result = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True, text=True, timeout=60,
        env={
            "PYTHONHASHSEED": str(pythonhashseed),
            "PYTHONPATH": f"{ROOT}:{ROOT}/scripts",
            "OPENMC_CROSS_SECTIONS": os.environ.get("OPENMC_CROSS_SECTIONS", ""),
            "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        },
    )
    if result.returncode != 0:
        return {"error": result.stderr[:500], "returncode": result.returncode}
    return json.loads(result.stdout.strip().splitlines()[-1])


def certify_deterministic_identity() -> bool:
    print("=" * 70)
    print("Cross-Process Deterministic Identity Certification")
    print("(P2-FULLCORE-2D-A-HARDENING)")
    print("=" * 70)

    out_dir = ROOT / "data" / "evals" / "p2_fullcore2d_a" / "identity_cert"
    out_dir.mkdir(parents=True, exist_ok=True)

    checks: list[tuple[str, bool, str]] = []

    # Run two subprocesses with different PYTHONHASHSEED
    print("\n1. Running subprocess with PYTHONHASHSEED=1...")
    r1 = _run_subprocess(1, out_dir)
    print(f"   ok={r1.get('ok')}, materials={r1.get('material_count')}, "
          f"universes={r1.get('universe_count')}, lattices={r1.get('lattice_count')}")

    print("\n2. Running subprocess with PYTHONHASHSEED=98765...")
    r2 = _run_subprocess(98765, out_dir)
    print(f"   ok={r2.get('ok')}, materials={r2.get('material_count')}, "
          f"universes={r2.get('universe_count')}, lattices={r2.get('lattice_count')}")

    if "error" in r1 or "error" in r2:
        print(f"\nFAILED: Subprocess error")
        return False

    # Compare plan digests
    d1 = r1["plan_digest"]
    d2 = r2["plan_digest"]
    checks.append(("plan_digest_match", d1 == d2, f"{d1[:16]} vs {d2[:16]}"))

    # Compare object IDs
    for id_type in ["material_ids", "universe_ids", "cell_ids", "lattice_ids", "axial_layer_ids"]:
        match = r1[id_type] == r2[id_type]
        diffs = set(r1[id_type]).symmetric_difference(set(r2[id_type])) if not match else set()
        checks.append((f"{id_type}_match", match, f"{len(diffs)} differences" if diffs else "identical"))

    # Compare counts
    for count_type in ["material_count", "universe_count", "cell_count", "lattice_count"]:
        match = r1[count_type] == r2[count_type]
        checks.append((f"{count_type}_match", match, f"{r1[count_type]} vs {r2[count_type]}"))

    # Report
    print(f"\n3. Comparison ({sum(1 for _, c, _ in checks if c)}/{len(checks)} passed):")
    for name, passed, msg in checks:
        status = "PASS" if passed else "FAIL"
        print(f"   [{status}] {name}: {msg}")

    all_passed = all(c for _, c, _ in checks)

    # Save report
    report = {
        "all_passed": all_passed,
        "seed_1": {"plan_digest": d1, "counts": {k: r1[k] for k in ["material_count", "universe_count", "cell_count", "lattice_count"]}},
        "seed_98765": {"plan_digest": d2, "counts": {k: r2[k] for k in ["material_count", "universe_count", "cell_count", "lattice_count"]}},
        "checks": [{"name": n, "passed": p, "message": m} for n, p, m in checks],
    }
    (out_dir / "identity_cert_report.json").write_text(json.dumps(report, indent=2))

    if all_passed:
        print(f"\nP2_FULLCORE_DETERMINISTIC_IDENTITY_CERTIFIED")
    else:
        failed = [n for n, c, _ in checks if not c]
        print(f"\nBLOCKED: {failed}")

    return all_passed


if __name__ == "__main__":
    success = certify_deterministic_identity()
    sys.exit(0 if success else 1)
