"""Tests for stale certification detection and requalification gate."""

from __future__ import annotations

from openmc_agent.certification_identity import (
    CertificationIdentity,
    PhysicsContractIdentity,
    check_certification_stale,
    compute_physics_contract_hash,
    get_git_sha,
)


def test_fresh_certification_not_stale():
    """A certification at current SHA with current contract → not stale."""
    current_sha = get_git_sha()
    current_hash = compute_physics_contract_hash()

    cert = CertificationIdentity(
        certified_git_sha=current_sha,
        current_git_sha=current_sha,
        qualification_git_sha=current_sha,
        seed_stability_git_sha=current_sha,
        physics_contract=PhysicsContractIdentity(
            git_sha=current_sha,
            physics_contract_hash=current_hash,
        ),
    )
    is_stale, reason = check_certification_stale(cert)
    assert not is_stale
    assert reason == ""


def test_sha_mismatch_makes_stale():
    """Different git SHA → stale."""
    cert = CertificationIdentity(
        certified_git_sha="aabbccdd" + "0" * 32,
        current_git_sha="11223344" + "0" * 32,
        qualification_git_sha="aabbccdd" + "0" * 32,
        seed_stability_git_sha="aabbccdd" + "0" * 32,
        physics_contract=PhysicsContractIdentity(
            git_sha="aabbccdd" + "0" * 32,
            physics_contract_hash=compute_physics_contract_hash(),
        ),
    )
    is_stale, reason = check_certification_stale(cert)
    assert is_stale
    assert "git_sha_mismatch" in reason


def test_contract_hash_mismatch_makes_stale():
    """Different physics contract hash → stale."""
    current_sha = get_git_sha()

    cert = CertificationIdentity(
        certified_git_sha=current_sha,
        current_git_sha=current_sha,
        qualification_git_sha=current_sha,
        seed_stability_git_sha=current_sha,
        physics_contract=PhysicsContractIdentity(
            git_sha=current_sha,
            physics_contract_hash="0" * 64,  # wrong hash
        ),
    )
    is_stale, reason = check_certification_stale(cert)
    assert is_stale
    assert "physics_contract_hash_mismatch" in reason


def test_qualification_sha_mismatch_makes_stale():
    """Qualification at different SHA → stale."""
    current_sha = get_git_sha()

    cert = CertificationIdentity(
        certified_git_sha=current_sha,
        current_git_sha=current_sha,
        qualification_git_sha="oldsha" + "0" * 34,
        seed_stability_git_sha=current_sha,
        physics_contract=PhysicsContractIdentity(
            git_sha=current_sha,
            physics_contract_hash=compute_physics_contract_hash(),
        ),
    )
    is_stale, reason = check_certification_stale(cert)
    assert is_stale
    assert "qualification_sha_mismatch" in reason


def test_old_qualification_cannot_certify_new_head():
    """Integration: a certification from old commit cannot certify current HEAD."""
    current_sha = get_git_sha()
    current_hash = compute_physics_contract_hash()

    # Simulate old certification at a previous SHA.
    old_cert = CertificationIdentity(
        certified_git_sha="previoussha1234567890" + "0" * 24,
        current_git_sha="previoussha1234567890" + "0" * 24,
        qualification_git_sha="previoussha1234567890" + "0" * 24,
        seed_stability_git_sha="previoussha1234567890" + "0" * 24,
        physics_contract=PhysicsContractIdentity(
            git_sha="previoussha1234567890" + "0" * 24,
            physics_contract_hash="oldhash" + "0" * 57,
        ),
    )
    is_stale, reason = check_certification_stale(old_cert)
    assert is_stale
    assert "git_sha_mismatch" in reason
