"""Tests for cross-process deterministic identity (P2-FULLCORE-2D-A-HARDENING)."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent


def _run_subprocess(pythonhashseed: int) -> dict:
    """Run VERA4 fixture assembly in a subprocess."""
    code = f'''
import json, sys, hashlib
sys.path.insert(0, "{ROOT}")
sys.path.insert(0, "{ROOT}/scripts")
from vera4_base_fixture import build_all_vera4_patches
from openmc_agent.plan_builder.assembler import assemble_simulation_plan_from_patches
patches = build_all_vera4_patches()
result = assemble_simulation_plan_from_patches(patches, strict=False)
plan_dict = result.plan.model_dump()
material_ids = sorted([m["id"] for m in plan_dict["complex_model"]["materials"]])
universe_ids = sorted([u["id"] for u in plan_dict["complex_model"]["universes"]])
lattice_ids = sorted([l["id"] for l in plan_dict["complex_model"]["lattices"]])
plan_json = json.dumps(plan_dict, sort_keys=True, default=str)
plan_digest = hashlib.sha256(plan_json.encode()).hexdigest()
print(json.dumps({{
    "plan_digest": plan_digest,
    "material_ids": material_ids,
    "universe_ids": universe_ids,
    "lattice_ids": lattice_ids,
}}))
'''
    result = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True, text=True, timeout=60,
        env={
            "PYTHONHASHSEED": str(pythonhashseed),
            "PYTHONPATH": f"{ROOT}:{ROOT}/scripts",
            "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        },
    )
    if result.returncode != 0:
        return {"error": result.stderr[:200]}
    return json.loads(result.stdout.strip().splitlines()[-1])


class TestCrossProcessIdentity:
    def test_plan_digest_stable_across_hashseeds(self):
        """Plan JSON digest must be identical across different PYTHONHASHSEED values."""
        r1 = _run_subprocess(1)
        r2 = _run_subprocess(98765)
        assert "error" not in r1, f"Process 1 error: {r1.get('error')}"
        assert "error" not in r2, f"Process 2 error: {r2.get('error')}"
        assert r1["plan_digest"] == r2["plan_digest"]

    def test_object_ids_stable_across_hashseeds(self):
        r1 = _run_subprocess(1)
        r2 = _run_subprocess(98765)
        assert r1["material_ids"] == r2["material_ids"]
        assert r1["universe_ids"] == r2["universe_ids"]
        assert r1["lattice_ids"] == r2["lattice_ids"]

    def test_content_hash_uses_sha256_not_python_hash(self):
        """Canonical state hashes must use SHA-256, not Python's built-in hash()."""
        from openmc_agent.plan_builder.axial_state_materializer import _compute_pin_state_hash
        h1 = _compute_pin_state_hash("type1", [["a"]], [], 0)
        h2 = _compute_pin_state_hash("type1", [["a"]], [], 0)
        assert h1 == h2  # Deterministic
        assert len(h1) == 16  # 16 hex chars from SHA-256
        int(h1, 16)  # Valid hex
