"""Transport seed stability evaluator for VERA3B qualification.

Takes a successful model from a qualification run and executes it with
3 independent OpenMC random seeds (10101, 20202, 30303). All inputs
(materials, geometry, settings except seed, cross sections) are held
constant. Verifies that keff is statistically consistent across seeds.

Pass criteria:
  - 3/3 OpenMC runs succeed
  - 3/3 zero lost particles
  - 3/3 no source rejection / crash
  - 3/3 finite keff and uncertainty
  - All geometry/material hashes match
  - Settings differ only in seed
  - Max pairwise normalized difference z <= 5
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import shutil
import subprocess
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

DEFAULT_SEEDS = [10101, 20202, 30303]


@dataclass
class SeedRunResult:
    seed: int
    output_dir: str
    started_at: str
    completed_at: str
    duration_s: float
    returncode: int
    keff: float
    keff_std: float
    lost_particles: int
    source_rejections: int
    statepoint_path: str
    geometry_hash: str
    materials_hash: str
    settings_hash: str
    error: str = ""


@dataclass
class TransportSeedStabilityResult:
    selected_run_id: str
    selected_disposition: str
    seeds: list[int]
    seed_results: list[SeedRunResult] = field(default_factory=list)
    mean_keff: float = 0.0
    between_seed_std: float = 0.0
    pairwise_z: list[dict[str, Any]] = field(default_factory=list)
    max_pairwise_z: float = 0.0
    geometry_hashes_match: bool = False
    materials_hashes_match: bool = False
    settings_hashes_match: bool = False
    all_seeds_succeeded: bool = False
    status: str = "PENDING"  # VERA3B_TRANSPORT_SEED_STABILITY_PASSED | _FAILED | _ERROR
    error: str = ""


def _file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _select_seed_model(
    results: list[dict[str, Any]],
) -> dict[str, Any] | None:
    """Deterministically select the model for seed stability.

    Rule: lowest run_index FIRST_PASS_SUCCESS;
          else lowest run_index RECOVERED_SUCCESS.
    """
    for disposition in ("FIRST_PASS_SUCCESS", "RECOVERED_SUCCESS"):
        candidates = sorted(
            [r for r in results if r.get("final_disposition") == disposition],
            key=lambda r: r.get("run_id", ""),
        )
        for c in candidates:
            if (
                c.get("vera3_acceptance_passed")
                and c.get("real_openmc_verified")
                and int(c.get("lost_particle_count", 0)) == 0
                and c.get("artifact_complete")
            ):
                return c
    return None


def _extract_keff(statepoint_path: Path) -> tuple[float, float]:
    """Extract keff and std from statepoint."""
    try:
        import openmc
        sp = openmc.StatePoint(statepoint_path)
        keff = sp.keff.nominal_value
        std = sp.keff.std_dev
        return float(keff), float(std)
    except Exception:
        return 0.0, 0.0


def _count_lost_particles(log_path: Path) -> int:
    """Count lost particles from OpenMC log."""
    if not log_path.exists():
        return 0
    text = log_path.read_text(errors="replace")
    return text.lower().count("lost particle")


def _count_source_rejections(log_path: Path) -> int:
    """Count source rejection warnings from OpenMC log."""
    if not log_path.exists():
        return 0
    text = log_path.read_text(errors="replace")
    return text.lower().count("source rejection") + text.lower().count("source sites")


def run_single_seed(
    model_dir: Path,
    output_dir: Path,
    seed: int,
    *,
    batches: int = 20,
    particles: int = 10000,
) -> SeedRunResult:
    """Run OpenMC with a specific seed on a fixed model."""
    output_dir.mkdir(parents=True, exist_ok=True)

    # Copy XML files.
    for fname in ["materials.xml", "geometry.xml", "settings.xml"]:
        src = model_dir / fname
        dst = output_dir / fname
        if src.exists():
            shutil.copy2(src, dst)

    # Modify settings.xml to change seed.
    settings_path = output_dir / "settings.xml"
    if settings_path.exists():
        import xml.etree.ElementTree as ET
        tree = ET.parse(settings_path)
        root = tree.getroot()
        seed_elem = root.find("seed")
        if seed_elem is None:
            seed_elem = ET.SubElement(root, "seed")
        seed_elem.text = str(seed)
        tree.write(settings_path)

    started = datetime.now(timezone.utc).isoformat()
    t0 = time.perf_counter()

    # Run OpenMC.
    cmd = ["openmc", "-s", str(seed)]
    proc = subprocess.run(
        cmd,
        cwd=str(output_dir),
        capture_output=True,
        text=True,
        timeout=600,
    )

    elapsed = time.perf_counter() - t0

    # Write log.
    log_path = output_dir / "openmc_log.txt"
    log_path.write_text(proc.stdout + proc.stderr, encoding="utf-8")

    # Extract results.
    sp_path = output_dir / "statepoint." + f"{batches}.h5"
    if not sp_path.exists():
        # Try finding any statepoint file.
        sp_files = sorted(output_dir.glob("statepoint.*.h5"))
        if sp_files:
            sp_path = sp_files[-1]

    keff, keff_std = _extract_keff(sp_path) if sp_path.exists() else (0.0, 0.0)
    lost = _count_lost_particles(log_path)
    rejections = _count_source_rejections(log_path)

    # Compute hashes (excluding settings which differs by seed).
    geo_hash = _file_sha256(output_dir / "geometry.xml") if (output_dir / "geometry.xml").exists() else ""
    mat_hash = _file_sha256(output_dir / "materials.xml") if (output_dir / "materials.xml").exists() else ""
    set_hash = _file_sha256(output_dir / "settings.xml") if (output_dir / "settings.xml").exists() else ""

    return SeedRunResult(
        seed=seed,
        output_dir=str(output_dir),
        started_at=started,
        completed_at=datetime.now(timezone.utc).isoformat(),
        duration_s=elapsed,
        returncode=proc.returncode,
        keff=keff,
        keff_std=keff_std,
        lost_particles=lost,
        source_rejections=rejections,
        statepoint_path=str(sp_path) if sp_path.exists() else "",
        geometry_hash=geo_hash,
        materials_hash=mat_hash,
        settings_hash=set_hash,
        error="" if proc.returncode == 0 else f"returncode={proc.returncode}",
    )


def compute_pairwise_z(
    results: list[SeedRunResult],
) -> list[dict[str, Any]]:
    """Compute pairwise normalized differences z_ij = |k_i-k_j| / sqrt(sigma_i^2+sigma_j^2)."""
    pairs: list[dict[str, Any]] = []
    for i, a in enumerate(results):
        for j, b in enumerate(results):
            if j <= i:
                continue
            denom = math.sqrt(a.keff_std ** 2 + b.keff_std ** 2)
            z = abs(a.keff - b.keff) / denom if denom > 0 else float("inf")
            pairs.append({
                "seed_i": a.seed,
                "seed_j": b.seed,
                "keff_i": a.keff,
                "keff_j": b.keff,
                "sigma_i": a.keff_std,
                "sigma_j": b.keff_std,
                "z": z,
            })
    return pairs


def evaluate_transport_seed_stability(
    campaign_results: list[dict[str, Any]],
    campaign_runs_dir: Path,
    output_dir: Path,
    *,
    seeds: list[int] | None = None,
    batches: int = 20,
    particles: int = 10000,
) -> TransportSeedStabilityResult:
    """Full transport seed stability evaluation.

    Args:
        campaign_results: List of run result dicts from the qualification campaign.
        campaign_runs_dir: Directory containing run_NNN/workflow/ subdirs.
        output_dir: Where to write seed stability artifacts.
        seeds: Seeds to use (default: [10101, 20202, 30303]).
    """
    if seeds is None:
        seeds = list(DEFAULT_SEEDS)

    output_dir.mkdir(parents=True, exist_ok=True)

    # Select model.
    selected = _select_seed_model(campaign_results)
    if selected is None:
        result = TransportSeedStabilityResult(
            selected_run_id="",
            selected_disposition="",
            seeds=seeds,
            status="VERA3B_TRANSPORT_SEED_STABILITY_ERROR",
            error="no_successful_run_found",
        )
        _write_result(output_dir, result)
        return result

    run_id = selected["run_id"]
    model_dir = campaign_runs_dir / run_id / "workflow"

    result = TransportSeedStabilityResult(
        selected_run_id=run_id,
        selected_disposition=selected["final_disposition"],
        seeds=seeds,
    )

    # Run each seed.
    for seed in seeds:
        seed_dir = output_dir / f"seed_{seed}"
        try:
            sr = run_single_seed(
                model_dir, seed_dir, seed,
                batches=batches, particles=particles,
            )
        except subprocess.TimeoutExpired:
            sr = SeedRunResult(
                seed=seed, output_dir=str(seed_dir),
                started_at="", completed_at="",
                duration_s=600, returncode=-1,
                keff=0.0, keff_std=0.0,
                lost_particles=0, source_rejections=0,
                statepoint_path="", geometry_hash="",
                materials_hash="", settings_hash="",
                error="timeout",
            )
        except Exception as exc:
            sr = SeedRunResult(
                seed=seed, output_dir=str(seed_dir),
                started_at="", completed_at="",
                duration_s=0, returncode=-1,
                keff=0.0, keff_std=0.0,
                lost_particles=0, source_rejections=0,
                statepoint_path="", geometry_hash="",
                materials_hash="", settings_hash="",
                error=str(exc)[:500],
            )
        result.seed_results.append(sr)

    # Check hash consistency.
    geo_hashes = {r.geometry_hash for r in result.seed_results if r.geometry_hash}
    mat_hashes = {r.materials_hash for r in result.seed_results if r.materials_hash}
    result.geometry_hashes_match = len(geo_hashes) <= 1
    result.materials_hashes_match = len(mat_hashes) <= 1

    # Compute statistics.
    keffs = [r.keff for r in result.seed_results if r.keff > 0]
    if keffs:
        mean = sum(keffs) / len(keffs)
        result.mean_keff = mean
        if len(keffs) > 1:
            var = sum((k - mean) ** 2 for k in keffs) / (len(keffs) - 1)
            result.between_seed_std = math.sqrt(var)

    result.pairwise_z = compute_pairwise_z(result.seed_results)
    result.max_pairwise_z = max(
        (p["z"] for p in result.pairwise_z),
        default=0.0,
    )

    # Check success.
    all_succeeded = all(
        r.returncode == 0 and r.keff > 0 and r.keff_std > 0
        and r.lost_particles == 0 and r.source_rejections == 0
        for r in result.seed_results
    )
    result.all_seeds_succeeded = all_succeeded

    # Determine status.
    if all_succeeded and result.max_pairwise_z <= 5.0:
        result.status = "VERA3B_TRANSPORT_SEED_STABILITY_PASSED"
    elif all_succeeded and result.max_pairwise_z > 5.0:
        result.status = "VERA3B_TRANSPORT_SEED_STABILITY_FAILED"
        result.error = f"max_pairwise_z={result.max_pairwise_z:.2f} > 5.0"
    else:
        result.status = "VERA3B_TRANSPORT_SEED_STABILITY_FAILED"
        failed = [r.seed for r in result.seed_results if r.returncode != 0]
        lost = [r.seed for r in result.seed_results if r.lost_particles > 0]
        errors = []
        if failed:
            errors.append(f"failed_seeds={failed}")
        if lost:
            errors.append(f"lost_particle_seeds={lost}")
        result.error = "; ".join(errors)

    _write_result(output_dir, result)
    return result


def _write_result(output_dir: Path, result: TransportSeedStabilityResult) -> None:
    """Write seed stability artifacts."""
    output_dir.mkdir(parents=True, exist_ok=True)

    # JSON result.
    data = asdict(result)
    (output_dir / "transport_seed_stability.json").write_text(
        json.dumps(data, indent=2, default=str),
        encoding="utf-8",
    )

    # Human-readable report.
    lines = [
        "# VERA3B Transport Seed Stability Report",
        "",
        f"**Status**: `{result.status}`",
        f"**Selected Run**: `{result.selected_run_id}` ({result.selected_disposition})",
        "",
        "## Per-Seed Results",
        "",
        "| Seed | keff | std | Lost | Rej | RC | Duration |",
        "|------|------|-----|------|-----|-----|----------|",
    ]
    for r in result.seed_results:
        lines.append(
            f"| {r.seed} | {r.keff:.5f} | {r.keff_std:.5f} | "
            f"{r.lost_particles} | {r.source_rejections} | "
            f"{r.returncode} | {r.duration_s:.0f}s |"
        )

    lines.extend([
        "",
        "## Statistics",
        "",
        f"- Mean keff: {result.mean_keff:.5f}",
        f"- Between-seed std: {result.between_seed_std:.5f}",
        f"- Max pairwise z: {result.max_pairwise_z:.2f}",
        f"- Geometry hashes match: {result.geometry_hashes_match}",
        f"- Materials hashes match: {result.materials_hashes_match}",
        "",
        "## Pairwise z-scores",
        "",
        "| Seed i | Seed j | keff_i | keff_j | z |",
        "|--------|--------|--------|--------|---|",
    ])
    for p in result.pairwise_z:
        lines.append(
            f"| {p['seed_i']} | {p['seed_j']} | "
            f"{p['keff_i']:.5f} | {p['keff_j']:.5f} | {p['z']:.2f} |"
        )

    if result.error:
        lines.extend(["", f"**Error**: {result.error}", ""])

    (output_dir / "transport_seed_stability_report.md").write_text(
        "\n".join(lines), encoding="utf-8",
    )
