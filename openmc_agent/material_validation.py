"""Material validation: scientific invariants and render-readiness checks.

Checks that a material composition is physically valid and that the
normalized result satisfies the expected invariants (e.g., O/U ratio,
boron concentration, fraction sums, no negatives, no duplicates).

Produces ``MaterialInvariantResult`` with per-invariant pass/fail status
and ``MaterialSemanticIssue`` blockers/warnings.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from openmc_agent.material_normalization import MaterialNormalizationResult
from openmc_agent.material_semantics import (
    MaterialClassification,
    MaterialSemanticIssue,
    classify_material_semantics,
)
from openmc_agent.schemas import (
    CompositionValueBasis,
    ComplexMaterialSpec,
    MaterialSpec,
    NormalizationStatus,
)

_URANIUM_NUCLIDES = {"U233", "U234", "U235", "U236", "U238"}


# --------------------------------------------------------------------------- #
# Data classes
# --------------------------------------------------------------------------- #


@dataclass
class InvariantCheck:
    """A single invariant check result."""

    name: str
    passed: bool
    expected: str = ""
    actual: str = ""
    severity: str = "error"  # error | warning | info


@dataclass
class MaterialInvariantResult:
    """Result of checking all invariants for a material."""

    material_id: str
    material_name: str
    checks: list[InvariantCheck] = field(default_factory=list)
    all_passed: bool = True
    blockers: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def render_ready(self) -> bool:
        """True if no blockers prevent rendering."""
        return len(self.blockers) == 0


# --------------------------------------------------------------------------- #
# Public API
# --------------------------------------------------------------------------- #


def validate_normalized_material(
    material: MaterialSpec | ComplexMaterialSpec,
    normalization: MaterialNormalizationResult | None = None,
) -> MaterialInvariantResult:
    """Validate a normalized material for render readiness.

    Checks that the normalization status is acceptable for the executor
    (not ambiguous, not invalid) and that scientific invariants hold.
    """
    classification = classify_material_semantics(material)
    mat_id = getattr(material, "id", material.name)
    mat_name = material.name

    result = MaterialInvariantResult(
        material_id=mat_id,
        material_name=mat_name,
    )

    # Check normalization status.
    status = getattr(material, "normalization_status", None)
    if status is None:
        status = NormalizationStatus.NOT_REQUIRED

    if status in (NormalizationStatus.AMBIGUOUS,):
        result.blockers.append(
            f"material.{mat_name}.normalization_ambiguous: "
            "composition basis is ambiguous and must be resolved before rendering"
        )
        result.all_passed = False
        result.checks.append(InvariantCheck(
            name="normalization_status",
            passed=False,
            expected="not_required | deterministically_normalized | human_confirmed",
            actual=str(status),
        ))
        return result

    if status == NormalizationStatus.INVALID:
        result.blockers.append(
            f"material.{mat_name}.normalization_invalid"
        )
        result.all_passed = False
        result.checks.append(InvariantCheck(
            name="normalization_status",
            passed=False,
            expected="not_required | deterministically_normalized | human_confirmed",
            actual="invalid",
        ))
        return result

    result.checks.append(InvariantCheck(
        name="normalization_status",
        passed=True,
        actual=str(status),
    ))

    # Check scientific invariants.
    invariants = compute_material_invariants(material, classification)
    for inv in invariants:
        result.checks.append(inv)
        if not inv.passed:
            if inv.severity == "error":
                result.blockers.append(
                    f"material.{mat_name}.{inv.name}: "
                    f"expected {inv.expected}, got {inv.actual}"
                )
                result.all_passed = False
            elif inv.severity == "warning":
                result.warnings.append(
                    f"material.{mat_name}.{inv.name}: {inv.actual}"
                )

    return result


def compute_material_invariants(
    material: MaterialSpec | ComplexMaterialSpec,
    classification: MaterialClassification | None = None,
) -> list[InvariantCheck]:
    """Compute scientific invariants for a material.

    Returns a list of :class:`InvariantCheck` objects covering:
    - No negative fractions
    - No duplicate nuclides
    - Total fraction positive
    - UO2-specific: uranium sum, O/U ratio, enrichment
    - Borated water-specific: boron concentration, B10/B11 split
    - H/O ratio for water
    """
    if classification is None:
        classification = classify_material_semantics(material)

    composition = material.composition
    checks: list[InvariantCheck] = []

    # --- Universal checks ---

    # No negative fractions.
    negatives = [c.name for c in composition if c.percent < 0]
    checks.append(InvariantCheck(
        name="no_negative_fractions",
        passed=len(negatives) == 0,
        expected="all fractions >= 0",
        actual=f"negatives: {negatives}" if negatives else "none",
    ))

    # No duplicate nuclides.
    names = [c.name for c in composition]
    duplicates = [n for n in set(names) if names.count(n) > 1]
    checks.append(InvariantCheck(
        name="no_duplicate_nuclides",
        passed=len(duplicates) == 0,
        expected="no duplicate nuclide names",
        actual=f"duplicates: {duplicates}" if duplicates else "none",
    ))

    # Total positive.
    total = sum(c.percent for c in composition)
    checks.append(InvariantCheck(
        name="total_positive",
        passed=total > 0,
        expected="sum > 0",
        actual=f"sum = {total:.6f}",
    ))

    # --- UO2-specific invariants ---
    if classification.is_uo2_like:
        u_entries = [c for c in composition if c.name in _URANIUM_NUCLIDES]
        o_entries = [c for c in composition if c.name == "O16"]

        u_sum = sum(c.percent for c in u_entries)
        o16_val = o_entries[0].percent if o_entries else 0.0

        # Uranium isotope sum.
        checks.append(InvariantCheck(
            name="uranium_isotope_sum",
            passed=u_sum > 0,
            expected="U isotope sum > 0",
            actual=f"sum = {u_sum:.4f}",
            severity="warning",
        ))

        # O/U ratio.
        if u_sum > 0:
            o_per_u = o16_val / u_sum
            # For UO2, O/U should be approximately 2.
            # After normalization this should be exact.
            o_u_ok = 1.8 < o_per_u < 2.2
            checks.append(InvariantCheck(
                name="o_u_ratio",
                passed=o_u_ok,
                expected="~2.0 for UO2",
                actual=f"{o_per_u:.4f}",
                severity="warning",
            ))

        # U235 enrichment.
        u235 = next((c.percent for c in u_entries if c.name == "U235"), 0.0)
        if u_sum > 0:
            enrichment = u235 / u_sum * 100
            checks.append(InvariantCheck(
                name="u235_enrichment",
                passed=0 < enrichment < 100,
                expected="0 < enrichment < 100%",
                actual=f"{enrichment:.3f}%",
                severity="info",
            ))

    # --- Borated water invariants ---
    if classification.is_water_like:
        nuclide_map = {c.name: c.percent for c in composition}
        h1 = nuclide_map.get("H1", 0.0)
        o16 = nuclide_map.get("O16", 0.0)
        b10 = nuclide_map.get("B10", 0.0)
        b11 = nuclide_map.get("B11", 0.0)

        total_sum = sum(c.percent for c in composition) or 1.0

        # H/O ratio (should be ~2:1 for water).
        if o16 > 0:
            h_o = h1 / o16
            checks.append(InvariantCheck(
                name="h_o_ratio",
                passed=1.8 < h_o < 2.2,
                expected="~2.0 for H2O",
                actual=f"{h_o:.4f}",
                severity="warning",
            ))

        # Boron fraction.
        if b10 > 0:
            b10_frac = b10 / total_sum
            # After ppm normalization, B10 should be small (< 5e-4).
            checks.append(InvariantCheck(
                name="boron_b10_fraction",
                passed=b10_frac < 5e-4,
                expected="< 5e-4 for ppm-normalized boron",
                actual=f"{b10_frac:.8f}",
                severity="warning",
            ))

            # B10/B11 split.
            if b11 > 0:
                ratio = b10 / b11
                checks.append(InvariantCheck(
                    name="b10_b11_split",
                    passed=0.1 < ratio < 0.5,
                    expected="~0.199/0.801 ≈ 0.249 (natural)",
                    actual=f"{ratio:.4f}",
                    severity="info",
                ))

    return checks


def write_material_normalization_report(
    output_dir: Any,
    results: list[tuple[MaterialNormalizationResult, MaterialInvariantResult]],
    *,
    run_id: str = "",
    git_sha: str = "",
) -> str:
    """Write a material normalization provenance report as JSON.

    Returns the path to the written file.
    """
    import json
    from pathlib import Path

    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    report: dict[str, Any] = {
        "contract_version": "1.0.0",
        "run_id": run_id,
        "git_sha": git_sha,
        "materials": [],
    }

    for norm, inv in results:
        mat_entry: dict[str, Any] = {
            "material_id": norm.material_id,
            "material_name": norm.material_name,
            "original_basis": norm.original_basis.value,
            "normalized_basis": norm.normalized_basis.value,
            "normalization_status": norm.normalization_status.value,
            "original_composition": {
                k: v for k, v in norm.operations[0].input_summary.items()
            } if norm.operations else {},
            "normalized_composition": {
                k: v for k, v in norm.operations[-1].output_summary.items()
            } if norm.operations else {},
            "operations": [
                {
                    "operation": op.operation,
                    "reason": op.reason,
                    "parameters": op.parameters,
                }
                for op in norm.operations
            ],
            "operation_reason": "; ".join(op.reason for op in norm.operations),
            "evidence_refs": [],
            "deterministic": norm.normalization_status
            == NormalizationStatus.DETERMINISTICALLY_NORMALIZED,
            "requires_human_confirmation": norm.requires_human_confirmation,
            "original_hash": norm.original_hash,
            "normalized_hash": norm.normalized_hash,
            "renderer_input_hash": norm.normalized_hash,
            "rendered_material_summary": {},
            "warnings": inv.warnings,
            "blockers": inv.blockers,
            "invariant_results": [
                {
                    "name": c.name,
                    "passed": c.passed,
                    "expected": c.expected,
                    "actual": c.actual,
                    "severity": c.severity,
                }
                for c in inv.checks
            ],
        }
        report["materials"].append(mat_entry)

    # Summary.
    total = len(results)
    normalized_count = sum(
        1 for n, _ in results
        if n.normalization_status == NormalizationStatus.DETERMINISTICALLY_NORMALIZED
    )
    ambiguous_count = sum(
        1 for n, _ in results
        if n.normalization_status == NormalizationStatus.AMBIGUOUS
    )
    blocked_count = sum(1 for _, inv in results if inv.blockers)

    report["summary"] = {
        "total_materials": total,
        "deterministically_normalized": normalized_count,
        "ambiguous": ambiguous_count,
        "blocked": blocked_count,
        "all_render_ready": blocked_count == 0,
    }

    output_path = output_dir / "material_normalization_report.json"
    output_path.write_text(
        json.dumps(report, indent=2, default=str),
        encoding="utf-8",
    )
    return str(output_path)
