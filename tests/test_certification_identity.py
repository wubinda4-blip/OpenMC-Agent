"""Tests for certification identity."""

from __future__ import annotations

from openmc_agent.certification_identity import (
    ACCEPTANCE_CONTRACT_VERSION,
    MATERIAL_SEMANTICS_CONTRACT_VERSION,
    CertificationIdentity,
    PhysicsContractIdentity,
    build_certification_identity,
    build_physics_contract_identity,
    certification_from_dict,
    certification_to_dict,
    compute_physics_contract_hash,
    get_git_sha,
)


def test_git_sha_returns_string():
    sha = get_git_sha()
    assert isinstance(sha, str)
    assert len(sha) >= 7  # short or full SHA


def test_physics_contract_hash_is_stable():
    """Same code → same hash."""
    h1 = compute_physics_contract_hash()
    h2 = compute_physics_contract_hash()
    assert h1 == h2
    assert len(h1) == 64  # SHA-256 hex


def test_physics_contract_identity_built():
    identity = build_physics_contract_identity()
    assert identity.git_sha
    assert identity.physics_contract_hash
    assert identity.material_semantics_contract_version == MATERIAL_SEMANTICS_CONTRACT_VERSION
    assert identity.acceptance_contract_version == ACCEPTANCE_CONTRACT_VERSION


def test_certification_identity_built():
    cert = build_certification_identity(
        input_sha="abc",
        acceptance_mode="full",
    )
    assert cert.current_git_sha
    assert cert.certified_git_sha == cert.current_git_sha
    assert cert.input_sha == "abc"
    assert cert.acceptance_mode == "full"
    assert cert.certification_created_at


def test_certification_serialisation():
    cert = build_certification_identity(input_sha="test")
    d = certification_to_dict(cert)
    assert "physics_contract" in d
    assert "current_git_sha" in d

    restored = certification_from_dict(d)
    assert restored.current_git_sha == cert.current_git_sha
    assert restored.physics_contract.git_sha == cert.physics_contract.git_sha


def test_contract_versions_defined():
    assert MATERIAL_SEMANTICS_CONTRACT_VERSION
    assert ACCEPTANCE_CONTRACT_VERSION
    assert isinstance(PhysicsContractIdentity(), PhysicsContractIdentity)
