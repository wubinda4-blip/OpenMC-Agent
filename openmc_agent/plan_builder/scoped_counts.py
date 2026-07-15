"""Scope-aware count validation and aggregation (P2-FULLCORE-1).

This module provides deterministic, reactor-neutral utilities for:

* Normalizing legacy un-scoped counts into :class:`ScopedExpectedCount`.
* Aggregating per-assembly-type local counts into core-level totals.
* Validating that expected and actual counts are compared only at the
  same scope level.
* Deriving homogeneous per-assembly counts under strictly proven
  conditions.

No LLM, no OpenMC, no reactor-specific assumptions.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from openmc_agent.plan_builder.patches import (
    CountScope,
    FactsPatch,
    ModelScope,
    ScopedExpectedCount,
)


# ---------------------------------------------------------------------------
# Data containers
# ---------------------------------------------------------------------------


@dataclass
class ScopedCountIssue:
    """A single issue found during scoped-count validation."""

    code: str
    severity: str  # "error", "warning", "info"
    message: str
    scope: CountScope = "unknown"
    role: str | None = None
    expected: int | None = None
    actual: int | None = None
    assembly_type_id: str | None = None


@dataclass
class ScopedCountValidationResult:
    """Result of scoped-count validation."""

    ok: bool
    issues: list[ScopedCountIssue] = field(default_factory=list)

    @property
    def errors(self) -> list[ScopedCountIssue]:
        return [i for i in self.issues if i.severity == "error"]


@dataclass
class AssemblyTypeCountSummary:
    """Local pin counts for a single assembly type."""

    assembly_type_id: str
    lattice_size: tuple[int, int]
    total_cells: int
    fuel_pin_count: int
    guide_tube_count: int
    instrument_tube_count: int
    water_cell_count: int
    localized_insert_counts: dict[str, int] = field(default_factory=dict)


@dataclass
class CoreCountAggregation:
    """Aggregated core-level counts from per-type local counts × multiplicity."""

    assembly_type_summaries: dict[str, AssemblyTypeCountSummary] = field(default_factory=dict)
    multiplicities: dict[str, int] = field(default_factory=dict)
    core_totals: dict[str, int] = field(default_factory=dict)
    total_assembly_instances: int = 0

    def core_total_for_role(self, role: str) -> int:
        return self.core_totals.get(role, 0)


# ---------------------------------------------------------------------------
# Normalize legacy un-scoped counts
# ---------------------------------------------------------------------------

_LEGACY_ROLE_MAP: dict[str, str] = {
    "expected_pin_count": "fuel_pin",
    "expected_guide_tube_count": "guide_tube",
    "expected_instrument_tube_count": "instrument_tube",
    "expected_pyrex_count": "pyrex_rod",
    "expected_thimble_plug_count": "thimble_plug",
}


def normalize_scoped_counts(
    facts: FactsPatch,
    *,
    legacy_scope: CountScope = "pin_map",
) -> list[ScopedExpectedCount]:
    """Convert legacy un-scoped facts fields into ScopedExpectedCount list.

    If ``scoped_expected_counts`` is already populated, it is returned
    directly.  Otherwise, the legacy ``expected_pin_count`` etc. fields are
    converted with the given *legacy_scope*.
    """
    if facts.scoped_expected_counts:
        return list(facts.scoped_expected_counts)

    result: list[ScopedExpectedCount] = []
    is_single = _is_single_assembly(facts)
    default_scope: CountScope = "pin_map" if is_single else "unknown"

    for legacy_field, role in _LEGACY_ROLE_MAP.items():
        val = getattr(facts, legacy_field, None)
        if val is not None:
            result.append(
                ScopedExpectedCount(
                    role=role,
                    value=val,
                    scope=default_scope,
                    source_note=f"legacy:{legacy_field}",
                )
            )

    if facts.expected_spacer_grid_count is not None:
        result.append(
            ScopedExpectedCount(
                role="spacer_grid",
                value=facts.expected_spacer_grid_count,
                scope=default_scope,
                source_note="legacy:expected_spacer_grid_count",
            )
        )

    return result


def _is_single_assembly(facts: FactsPatch) -> bool:
    """Check if facts describe a single assembly."""
    scope = facts.model_scope
    if scope in ("single_pin", "single_assembly"):
        return True
    if scope in ("multi_assembly_core", "full_core"):
        return False
    # unknown — infer from assembly_count
    if facts.assembly_count is not None and facts.assembly_count > 1:
        return False
    return True


# ---------------------------------------------------------------------------
# Per-assembly-type local count computation
# ---------------------------------------------------------------------------

def compute_assembly_pin_counts(
    lattice_size: tuple[int, int],
    guide_tube_coords: list[tuple[int, int]],
    instrument_tube_coords: list[tuple[int, int]],
    water_cell_coords: list[tuple[int, int]],
    localized_insert_counts: dict[str, int] | None = None,
    *,
    assembly_type_id: str = "",
) -> AssemblyTypeCountSummary:
    """Deterministically compute local pin counts for a single assembly type.

    Does NOT divide any core total — counts are derived purely from the
    sparse pin map geometry.
    """
    nx, ny = lattice_size
    total_cells = nx * ny
    gt = len(guide_tube_coords)
    inst = len(instrument_tube_coords)
    water = len(water_cell_coords)

    special = gt + inst + water
    fuel = max(total_cells - special, 0)

    return AssemblyTypeCountSummary(
        assembly_type_id=assembly_type_id,
        lattice_size=lattice_size,
        total_cells=total_cells,
        fuel_pin_count=fuel,
        guide_tube_count=gt,
        instrument_tube_count=inst,
        water_cell_count=water,
        localized_insert_counts=dict(localized_insert_counts or {}),
    )


# ---------------------------------------------------------------------------
# Core count aggregation
# ---------------------------------------------------------------------------

def aggregate_core_counts(
    type_summaries: dict[str, AssemblyTypeCountSummary],
    multiplicities: dict[str, int],
) -> CoreCountAggregation:
    """Aggregate per-type local counts into core totals.

    core_total[role] = Σ multiplicity[type] × local_count[type, role]

    Localized insert counts are aggregated separately by insert kind.
    """
    core_totals: dict[str, int] = {}
    total_instances = 0

    for type_id, summary in type_summaries.items():
        mult = multiplicities.get(type_id, 0)
        total_instances += mult

        for role, count in [
            ("fuel_pin", summary.fuel_pin_count),
            ("guide_tube", summary.guide_tube_count),
            ("instrument_tube", summary.instrument_tube_count),
            ("water_cell", summary.water_cell_count),
        ]:
            core_totals[role] = core_totals.get(role, 0) + mult * count

        for insert_kind, count in summary.localized_insert_counts.items():
            role_key = f"localized_{insert_kind}"
            core_totals[role_key] = core_totals.get(role_key, 0) + mult * count

    return CoreCountAggregation(
        assembly_type_summaries=type_summaries,
        multiplicities=dict(multiplicities),
        core_totals=core_totals,
        total_assembly_instances=total_instances,
    )


# ---------------------------------------------------------------------------
# Scope compatibility validation
# ---------------------------------------------------------------------------

def validate_count_scope_compatibility(
    facts: FactsPatch,
    scoped_counts: list[ScopedExpectedCount],
) -> ScopedCountValidationResult:
    """Check that scoped counts are compatible with the model scope.

    For multi-assembly / full-core models, legacy un-scoped counts are
    insufficient and will produce a ``facts.count_scope_ambiguous`` issue.
    """
    issues: list[ScopedCountIssue] = []
    is_multi = facts.model_scope in ("multi_assembly_core", "full_core")

    if is_multi:
        has_scoped = any(
            sc.scope in ("assembly_type", "core_total", "assembly_instance")
            for sc in scoped_counts
        )
        has_legacy_unscoped = any(
            sc.scope in ("pin_map", "unknown")
            and not sc.derived
            for sc in scoped_counts
        )
        if not has_scoped and has_legacy_unscoped:
            issues.append(
                ScopedCountIssue(
                    code="facts.count_scope_ambiguous",
                    severity="error",
                    message=(
                        "Multi-assembly core has legacy un-scoped counts but no "
                        "assembly-type or core-total scoped counts. Cannot "
                        "validate pin_map against core-level totals."
                    ),
                )
            )

    return ScopedCountValidationResult(
        ok=len(issues) == 0 or all(i.severity != "error" for i in issues),
        issues=issues,
    )


# ---------------------------------------------------------------------------
# Scoped count comparison
# ---------------------------------------------------------------------------

def compare_scoped_expected_counts(
    expected: list[ScopedExpectedCount],
    actual: dict[str, int],
    *,
    scope: CountScope = "core_total",
) -> ScopedCountValidationResult:
    """Compare expected scoped counts with actual aggregated counts.

    Only compares entries at the same scope level.
    """
    issues: list[ScopedCountIssue] = []

    for exp in expected:
        if exp.scope != scope:
            continue
        actual_val = actual.get(exp.role, 0)
        if actual_val != exp.value:
            issues.append(
                ScopedCountIssue(
                    code="counts.scope_mismatch",
                    severity="error",
                    message=(
                        f"Scoped count mismatch for role '{exp.role}' "
                        f"at scope '{scope}': expected {exp.value}, "
                        f"actual {actual_val}"
                    ),
                    scope=scope,
                    role=exp.role,
                    expected=exp.value,
                    actual=actual_val,
                )
            )

    return ScopedCountValidationResult(
        ok=len(issues) == 0,
        issues=issues,
    )


# ---------------------------------------------------------------------------
# Homogeneous per-assembly count derivation
# ---------------------------------------------------------------------------

def derive_homogeneous_local_counts_if_proven(
    core_total: int,
    assembly_count: int,
    *,
    assembly_type_count: int = 1,
    input_states_homogeneous: bool = False,
    input_states_identical: bool = False,
) -> tuple[int | None, str | None]:
    """Derive homogeneous per-assembly count under strictly proven conditions.

    Returns (per_assembly_count, derivation_note).

    Returns (None, reason) if derivation is not proven.
    """
    if assembly_count <= 0:
        return None, "assembly_count <= 0"

    if assembly_count % assembly_type_count != 0:
        return None, (
            f"assembly_count ({assembly_count}) is not divisible by "
            f"assembly_type_count ({assembly_type_count})"
        )

    per_type = assembly_count // assembly_type_count
    if per_type <= 0:
        return None, "per-type multiplicity is zero"

    if core_total % assembly_count != 0:
        return None, (
            f"core_total ({core_total}) is not divisible by "
            f"assembly_count ({assembly_count})"
        )

    if assembly_type_count > 1:
        if not input_states_homogeneous or not input_states_identical:
            return None, (
                "Multiple assembly types exist but input does not confirm "
                "they are identical"
            )

    per_assembly = core_total // assembly_count
    return per_assembly, (
        f"core_total ({core_total}) / assembly_count ({assembly_count}) "
        f"= {per_assembly}; homogeneous=True, identical=True, "
        f"types={assembly_type_count}"
    )
