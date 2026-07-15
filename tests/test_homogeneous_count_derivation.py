"""Tests for homogeneous per-assembly count derivation rules (P2-FULLCORE-1).

Verifies that homogeneous per-assembly counts can only be derived under
strict proven conditions.
"""

from openmc_agent.plan_builder.scoped_counts import derive_homogeneous_local_counts_if_proven


def test_homogeneous_derivation_success():
    """All conditions met: homogeneous derivation allowed."""
    per_assembly, note = derive_homogeneous_local_counts_if_proven(
        core_total=1000,
        assembly_count=4,
        assembly_type_count=1,
        input_states_homogeneous=True,
        input_states_identical=True,
    )
    assert per_assembly == 250
    assert note is not None
    assert "1000" in note


def test_homogeneous_derivation_not_divisible():
    """Core total not divisible by assembly count → cannot derive."""
    per_assembly, note = derive_homogeneous_local_counts_if_proven(
        core_total=1001,
        assembly_count=4,
    )
    assert per_assembly is None
    assert "not divisible" in note


def test_homogeneous_derivation_multiple_types_not_identical():
    """Multiple types but input doesn't confirm identical → cannot derive."""
    per_assembly, note = derive_homogeneous_local_counts_if_proven(
        core_total=1000,
        assembly_count=4,
        assembly_type_count=2,
        input_states_homogeneous=False,
        input_states_identical=False,
    )
    assert per_assembly is None
    assert "identical" in note.lower() or "not" in note.lower()


def test_homogeneous_derivation_zero_assembly_count():
    """Assembly count = 0 → cannot derive."""
    per_assembly, note = derive_homogeneous_local_counts_if_proven(
        core_total=1000,
        assembly_count=0,
    )
    assert per_assembly is None


def test_homogeneous_derivation_single_type():
    """Single assembly type with homogeneous flag → allowed even without identical flag."""
    per_assembly, note = derive_homogeneous_local_counts_if_proven(
        core_total=1000,
        assembly_count=4,
        assembly_type_count=1,
    )
    assert per_assembly == 250
