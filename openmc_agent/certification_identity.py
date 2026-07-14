"""Certification identity and stale-certification detection.

Binds a qualification or seed-stability result to the exact code and
contract version that produced it.  When any physics-critical file or
contract version changes, the certification becomes stale and
re-qualification is required.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


# --------------------------------------------------------------------------- #
# Contract versions
# --------------------------------------------------------------------------- #

MATERIAL_SEMANTICS_CONTRACT_VERSION = "1.0.0"
ACCEPTANCE_CONTRACT_VERSION = "1.0.0"
RENDERER_CONTRACT_VERSION = "1.0.0"
RUNTIME_POLICY_VERSION = "1.0.0"
SCHEMA_VERSION = "1.0.0"

# Files whose change invalidates physics certification.
PHYSICS_CRITICAL_PATHS: list[str] = [
    "openmc_agent/executor.py",
    "openmc_agent/material_semantics.py",
    "openmc_agent/material_normalization.py",
    "openmc_agent/material_validation.py",
    "openmc_agent/schemas.py",
    "openmc_agent/transport_seed_stability.py",
    "openmc_agent/real_campaign.py",
    "openmc_agent/p1_runtime_gate.py",
    "openmc_agent/campaign_eval/vera3_campaign_acceptance.py",
    "openmc_agent/material_policy.py",
]


# --------------------------------------------------------------------------- #
# Data classes
# --------------------------------------------------------------------------- #


@dataclass
class PhysicsContractIdentity:
    """Identity of the physics contract (code + versions)."""

    git_sha: str = ""
    tree_sha: str = ""
    physics_contract_hash: str = ""
    material_semantics_contract_version: str = MATERIAL_SEMANTICS_CONTRACT_VERSION
    acceptance_contract_version: str = ACCEPTANCE_CONTRACT_VERSION
    renderer_contract_version: str = RENDERER_CONTRACT_VERSION
    runtime_policy_version: str = RUNTIME_POLICY_VERSION
    schema_version: str = SCHEMA_VERSION


@dataclass
class CertificationIdentity:
    """Full identity of a certification run."""

    certified_git_sha: str = ""
    current_git_sha: str = ""
    qualification_git_sha: str = ""
    seed_stability_git_sha: str = ""
    input_sha: str = ""
    requirement_sha: str = ""
    physics_contract: PhysicsContractIdentity = field(
        default_factory=PhysicsContractIdentity
    )
    cross_sections_identity: str = ""
    openmc_version: str = ""
    model: str = ""
    temperature: str = ""
    campaign_config_hash: str = ""
    certification_created_at: str = ""
    acceptance_mode: str = ""
    status: str = ""

    # Valid status values:
    #   P1_RUNTIME_STAGE_COMPLETE_AT_SHA
    #   P1_RUNTIME_STAGE_COMPLETE_CURRENT_HEAD
    #   P1_RUNTIME_STAGE_REQUALIFICATION_REQUIRED
    #   P1_RUNTIME_STAGE_REQUALIFICATION_FAILED


# --------------------------------------------------------------------------- #
# Public API
# --------------------------------------------------------------------------- #


def get_git_sha(repo_dir: Path | None = None) -> str:
    """Get the current HEAD git SHA."""
    repo_dir = repo_dir or Path.cwd()
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=str(repo_dir),
            capture_output=True,
            text=True,
            timeout=10,
        )
        return result.stdout.strip() if result.returncode == 0 else ""
    except Exception:
        return ""


def get_tree_sha(repo_dir: Path | None = None) -> str:
    """Get the tree SHA of HEAD."""
    repo_dir = repo_dir or Path.cwd()
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD^{tree}"],
            cwd=str(repo_dir),
            capture_output=True,
            text=True,
            timeout=10,
        )
        return result.stdout.strip() if result.returncode == 0 else ""
    except Exception:
        return ""


def compute_physics_contract_hash(
    repo_dir: Path | None = None,
    *,
    extra_paths: list[str] | None = None,
) -> str:
    """Compute a SHA-256 hash of all physics-critical files and versions.

    The hash changes when any physics-critical file is modified, ensuring
    that certification is invalidated by code changes.
    """
    repo_dir = repo_dir or Path.cwd()
    paths = list(PHYSICS_CRITICAL_PATHS)
    if extra_paths:
        paths.extend(extra_paths)

    h = hashlib.sha256()

    # Hash file contents.
    for rel_path in sorted(paths):
        full_path = repo_dir / rel_path
        if full_path.exists():
            content = full_path.read_bytes()
            h.update(f"{rel_path}:".encode())
            h.update(content)
            h.update(b"\n")
        else:
            h.update(f"{rel_path}:MISSING\n".encode())

    # Hash contract versions.
    versions = (
        f"material={MATERIAL_SEMANTICS_CONTRACT_VERSION}"
        f"|acceptance={ACCEPTANCE_CONTRACT_VERSION}"
        f"|renderer={RENDERER_CONTRACT_VERSION}"
        f"|runtime={RUNTIME_POLICY_VERSION}"
        f"|schema={SCHEMA_VERSION}"
    )
    h.update(versions.encode())

    return h.hexdigest()


def build_physics_contract_identity(
    repo_dir: Path | None = None,
) -> PhysicsContractIdentity:
    """Build a PhysicsContractIdentity from the current repo state."""
    return PhysicsContractIdentity(
        git_sha=get_git_sha(repo_dir),
        tree_sha=get_tree_sha(repo_dir),
        physics_contract_hash=compute_physics_contract_hash(repo_dir),
    )


def build_certification_identity(
    *,
    qualification_git_sha: str = "",
    seed_stability_git_sha: str = "",
    input_sha: str = "",
    requirement_sha: str = "",
    cross_sections_identity: str = "",
    openmc_version: str = "",
    model: str = "",
    temperature: str = "",
    campaign_config_hash: str = "",
    acceptance_mode: str = "",
    repo_dir: Path | None = None,
) -> CertificationIdentity:
    """Build a CertificationIdentity from the current repo state."""
    repo_dir = repo_dir or Path.cwd()
    physics = build_physics_contract_identity(repo_dir)
    current_sha = physics.git_sha

    return CertificationIdentity(
        certified_git_sha=current_sha,
        current_git_sha=current_sha,
        qualification_git_sha=qualification_git_sha or current_sha,
        seed_stability_git_sha=seed_stability_git_sha or current_sha,
        input_sha=input_sha,
        requirement_sha=requirement_sha,
        physics_contract=physics,
        cross_sections_identity=cross_sections_identity,
        openmc_version=openmc_version,
        model=model,
        temperature=temperature,
        campaign_config_hash=campaign_config_hash,
        certification_created_at=datetime.now(timezone.utc).isoformat(),
        acceptance_mode=acceptance_mode,
    )


def check_certification_stale(
    certification: CertificationIdentity,
    repo_dir: Path | None = None,
) -> tuple[bool, str]:
    """Check if a certification is stale relative to the current HEAD.

    Returns ``(is_stale, reason)``.
    """
    repo_dir = repo_dir or Path.cwd()
    current_sha = get_git_sha(repo_dir)
    current_contract = compute_physics_contract_hash(repo_dir)

    if certification.certified_git_sha != current_sha:
        return True, (
            f"git_sha_mismatch: certified={certification.certified_git_sha[:12]}, "
            f"current={current_sha[:12]}"
        )

    if certification.physics_contract.physics_contract_hash != current_contract:
        return True, (
            "physics_contract_hash_mismatch: physics-critical files changed "
            "since certification"
        )

    if certification.qualification_git_sha != current_sha:
        return True, (
            f"qualification_sha_mismatch: qualification={certification.qualification_git_sha[:12]}, "
            f"current={current_sha[:12]}"
        )

    if certification.seed_stability_git_sha != current_sha:
        return True, (
            f"seed_stability_sha_mismatch: seed={certification.seed_stability_git_sha[:12]}, "
            f"current={current_sha[:12]}"
        )

    return False, ""


def certification_to_dict(cert: CertificationIdentity) -> dict[str, Any]:
    """Serialise a CertificationIdentity to a JSON-compatible dict."""
    d = asdict(cert)
    return d


def certification_from_dict(d: dict[str, Any]) -> CertificationIdentity:
    """Deserialise a CertificationIdentity from a dict."""
    physics = d.pop("physics_contract", {})
    return CertificationIdentity(
        physics_contract=PhysicsContractIdentity(**physics),
        **d,
    )


def write_certification(
    output_dir: Path,
    cert: CertificationIdentity,
) -> None:
    """Write certification identity as JSON."""
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "certification_identity.json").write_text(
        json.dumps(certification_to_dict(cert), indent=2, default=str),
        encoding="utf-8",
    )
