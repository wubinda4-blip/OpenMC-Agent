"""Tests for scope-aware count validation (P2-FULLCORE-1)."""

from openmc_agent.plan_builder.patches import FactsPatch, ScopedExpectedCount
from openmc_agent.plan_builder.scoped_counts import (
    ScopedCountValidationResult,
    validate_count_scope_compatibility,
    compare_scoped_expected_counts,
    normalize_scoped_counts,
)


def test_single_assembly_legacy_counts_ok():
    """Single-assembly with legacy counts should pass validation."""
    facts = FactsPatch(model_scope="single_assembly", expected_pin_count=264)
    counts = normalize_scoped_counts(facts)
    result = validate_count_scope_compatibility(facts, counts)
    assert result.ok


def test_multi_assembly_legacy_counts_fail():
    """Multi-assembly with only legacy un-scoped counts should fail."""
    facts = FactsPatch(
        model_scope="multi_assembly_core",
        assembly_count=4,
        expected_pin_count=264,  # ambiguous: is this per-assembly or core total?
    )
    counts = normalize_scoped_counts(facts)
    result = validate_count_scope_compatibility(facts, counts)
    assert not result.ok
    assert any(i.code == "facts.count_scope_ambiguous" for i in result.issues)


def test_multi_assembly_with_scoped_counts_ok():
    """Multi-assembly with explicit scoped counts should pass."""
    facts = FactsPatch(
        model_scope="multi_assembly_core",
        assembly_count=4,
        scoped_expected_counts=[
            ScopedExpectedCount(role="fuel_pin", value=1000, scope="core_total"),
            ScopedExpectedCount(role="fuel_pin", value=250, scope="assembly_type", assembly_type_id="type_a"),
        ],
    )
    counts = normalize_scoped_counts(facts)
    result = validate_count_scope_compatibility(facts, counts)
    assert result.ok


def test_compare_scoped_counts_match():
    expected = [
        ScopedExpectedCount(role="fuel_pin", value=1000, scope="core_total"),
        ScopedExpectedCount(role="guide_tube", value=96, scope="core_total"),
    ]
    actual = {"fuel_pin": 1000, "guide_tube": 96}
    result = compare_scoped_expected_counts(expected, actual, scope="core_total")
    assert result.ok


def test_compare_scoped_counts_mismatch():
    expected = [
        ScopedExpectedCount(role="fuel_pin", value=1000, scope="core_total"),
    ]
    actual = {"fuel_pin": 900}
    result = compare_scoped_expected_counts(expected, actual, scope="core_total")
    assert not result.ok
    assert any(i.code == "counts.scope_mismatch" for i in result.issues)


def test_compare_scoped_counts_wrong_scope_skipped():
    """Counts at different scope levels should NOT be compared."""
    expected = [
        ScopedExpectedCount(role="fuel_pin", value=264, scope="pin_map"),
    ]
    actual = {"fuel_pin": 1000}  # core_total
    result = compare_scoped_expected_counts(expected, actual, scope="core_total")
    assert result.ok  # no comparison because scope differs
