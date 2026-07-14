"""Tests for transport seed stability evaluator (S14)."""

from __future__ import annotations

import math

from openmc_agent.transport_seed_stability import (
    SeedRunResult,
    TransportSeedStabilityResult,
    compute_pairwise_z,
    _select_seed_model,
)


def _seed_result(seed, keff, std, rc=0, lost=0, rej=0):
    return SeedRunResult(
        seed=seed, output_dir="", started_at="", completed_at="",
        duration_s=10, returncode=rc, keff=keff, keff_std=std,
        lost_particles=lost, source_rejections=rej,
        statepoint_path="", geometry_hash="g1",
        materials_hash="m1", settings_hash="s1",
    )


def test_pairwise_z_identical_keff():
    results = [_seed_result(10101, 1.0, 0.001), _seed_result(20202, 1.0, 0.001)]
    pairs = compute_pairwise_z(results)
    assert len(pairs) == 1
    assert pairs[0]["z"] == 0.0


def test_pairwise_z_significant_difference():
    results = [_seed_result(10101, 1.0, 0.001), _seed_result(20202, 1.1, 0.001)]
    pairs = compute_pairwise_z(results)
    assert pairs[0]["z"] > 5.0


def test_pairwise_z_three_seeds():
    results = [
        _seed_result(10101, 1.0, 0.001),
        _seed_result(20202, 1.001, 0.001),
        _seed_result(30303, 0.999, 0.001),
    ]
    pairs = compute_pairwise_z(results)
    assert len(pairs) == 3  # C(3,2) = 3


def test_select_model_prefers_first_pass():
    results = [
        {"run_id": "run_001", "final_disposition": "PLANNING_FAILURE",
         "vera3_acceptance_passed": False, "real_openmc_verified": False,
         "lost_particle_count": 0, "artifact_complete": False},
        {"run_id": "run_002", "final_disposition": "FIRST_PASS_SUCCESS",
         "vera3_acceptance_passed": True, "real_openmc_verified": True,
         "lost_particle_count": 0, "artifact_complete": True},
        {"run_id": "run_003", "final_disposition": "RECOVERED_SUCCESS",
         "vera3_acceptance_passed": True, "real_openmc_verified": True,
         "lost_particle_count": 0, "artifact_complete": True},
    ]
    selected = _select_seed_model(results)
    assert selected["run_id"] == "run_002"


def test_select_model_falls_back_to_recovered():
    results = [
        {"run_id": "run_001", "final_disposition": "PLANNING_FAILURE"},
        {"run_id": "run_002", "final_disposition": "RECOVERED_SUCCESS",
         "vera3_acceptance_passed": True, "real_openmc_verified": True,
         "lost_particle_count": 0, "artifact_complete": True},
    ]
    selected = _select_seed_model(results)
    assert selected["run_id"] == "run_002"


def test_select_model_returns_none_when_no_success():
    results = [
        {"run_id": "run_001", "final_disposition": "PLANNING_FAILURE"},
    ]
    selected = _select_seed_model(results)
    assert selected is None


def test_select_model_excludes_lost_particle():
    results = [
        {"run_id": "run_001", "final_disposition": "FIRST_PASS_SUCCESS",
         "vera3_acceptance_passed": True, "real_openmc_verified": True,
         "lost_particle_count": 5, "artifact_complete": True},
    ]
    selected = _select_seed_model(results)
    assert selected is None


def test_all_seeds_succeed():
    results = [
        _seed_result(10101, 1.0, 0.001),
        _seed_result(20202, 1.001, 0.001),
        _seed_result(30303, 0.999, 0.001),
    ]
    all_ok = all(
        r.returncode == 0 and r.keff > 0 and r.keff_std > 0
        and r.lost_particles == 0 and r.source_rejections == 0
        for r in results
    )
    assert all_ok


def test_lost_particle_blocks_pass():
    results = [
        _seed_result(10101, 1.0, 0.001),
        _seed_result(20202, 1.001, 0.001, lost=1),
        _seed_result(30303, 0.999, 0.001),
    ]
    all_ok = all(
        r.returncode == 0 and r.keff > 0 and r.keff_std > 0
        and r.lost_particles == 0 and r.source_rejections == 0
        for r in results
    )
    assert not all_ok
