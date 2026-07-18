"""Deterministic Material-Universe preflight.

Produces the canonical set of cross-patch issues that the Critic is *not*
allowed to recompute.  Reuses single-patch validators, the material species
resolver, and radial profile validation wherever possible.
"""

from __future__ import annotations

from typing import Any

from pydantic import Field

from openmc_agent.plan_builder.patches import parse_patch_content
from openmc_agent.plan_builder.validators import validate_patch
from openmc_agent.schemas import AgentBaseModel

from .fingerprints import compute_evidence_pack_hash
from .material_universe_binding import _valid, build_material_universe_binding_view
from .material_universe_evidence import build_material_universe_contract_matrix, material_universe_gate_input_hash
from .models import MaterialUniverseBindingView, PlanClosedLoopPolicy


class MaterialUniversePreflightResult(AgentBaseModel):
    ok: bool = False
    binding_view: MaterialUniverseBindingView | None = None
    issues: list[dict[str, Any]] = Field(default_factory=list)
    summary: dict[str, Any] = Field(default_factory=dict)
    input_hash: str = ""

    @property
    def blocking_issues(self) -> list[dict[str, Any]]:
        return [item for item in self.issues if item.get("severity") == "error"]


def _issue(code: str, message: str, *, severity: str = "error", row_kind: str = "source_material_coverage", row_key: str = "", **extra: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {"code": code, "severity": severity, "blocking": severity == "error", "message": message, "row_kind": row_kind, "row_key": row_key}
    payload.update({k: v for k, v in extra.items() if v is not None})
    return payload


def _collect_materials_issues(materials_patch: Any, view: MaterialUniverseBindingView, species_report: dict[str, Any]) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for material in materials_patch.materials:
        mid = material.material_id
        if mid in seen_ids:
            issues.append(_issue("material_universe.material_duplicate", f"duplicate material_id {mid}", row_kind="source_material_coverage", row_key=mid, material_id=mid))
            continue
        seen_ids.add(mid)
        if material.density_g_cm3 is not None and material.density_g_cm3 <= 0:
            issues.append(_issue("material_universe.material_density_invalid", f"material {mid} density <= 0", row_kind="source_material_coverage", row_key=mid, material_id=mid, density=material.density_g_cm3))
        # Compound formula must not live in transport composition.
        if material.compound_components:
            for element, fraction in material.composition.items():
                # Compound symbols like B2O3 / SiO2 contain digits and uppercase pairs.
                if any(ch.isdigit() for ch in element) and element.upper() == element and len(element) > 2:
                    issues.append(_issue("material_universe.compound_in_transport_composition", f"material {mid} has compound formula {element} in transport composition", row_kind="source_material_coverage", row_key=mid, material_id=mid, compound=element))
                    break
            if not material.composition_basis or material.composition_basis == "unknown":
                issues.append(_issue("material_universe.compound_fraction_basis_missing", f"material {mid} has compound_components but no composition_basis", row_kind="source_material_coverage", row_key=mid, material_id=mid))
        # Fissile isotope policy: fuel material with composition U235 but no explicit policy warning.
        if material.role == "fuel" and material.composition:
            u235 = material.composition.get("U235")
            if u235 is not None and u235 > 0 and not material.source_variant_id:
                issues.append(_issue("material_universe.fissile_isotope_policy_missing", f"fuel material {mid} has U235 but no source_variant_id", row_kind="source_material_coverage", row_key=mid, material_id=mid, severity="warning"))
        # Approximate composition must not be marked confirmed.
        if material.composition_status == "confirmed" and material.source_variant_id is None and material.role in {"structural", "cladding"}:
            # Commercial alloy with no source disclosure cannot be confirmed.
            if not material.composition:
                issues.append(_issue("material_universe.alloy_reduced_without_disclosure", f"structural material {mid} marked confirmed without composition disclosure", row_kind="source_material_coverage", row_key=mid, material_id=mid, severity="warning"))
        if material.composition_status == "placeholder":
            issues.append(_issue("material_universe.placeholder_material_unresolved", f"material {mid} is still a placeholder", row_kind="source_material_coverage", row_key=mid, material_id=mid))
        # Species resolver warnings become deterministic issues.
        resolver_entry = species_report.get("materials", {}).get(mid, {}) if isinstance(species_report, dict) else {}
        for warning in resolver_entry.get("warnings", []):
            if "error" in str(warning).lower():
                issues.append(_issue("material_universe.transport_species_invalid", f"material {mid} resolver: {warning}", row_kind="source_material_coverage", row_key=mid, material_id=mid))
    # Source-required materials missing.
    for contract in view.required_material_contracts:
        if contract.get("implicit"):
            continue
        role = contract.get("expected_role")
        variant = contract.get("expected_variant_id")
        if variant:
            # Fuel variant: check by source_variant_id.
            found = any(m.source_variant_id == variant for m in materials_patch.materials)
            if not found:
                issues.append(_issue("material_universe.required_fuel_variant_material_missing", f"fuel variant {variant} has no material", row_kind="source_material_coverage", row_key=contract["requirement_id"], requirement_id=contract["requirement_id"]))
        elif role:
            # "poison" and "absorber" are semantically equivalent for
            # material role matching — both describe neutron-absorbing
            # materials.  LLMs may use either term depending on training
            # data, and the physics is identical.
            equivalent_roles = {role}
            if role in {"poison", "absorber"}:
                equivalent_roles = {"poison", "absorber"}
            found = any(m.role in equivalent_roles for m in materials_patch.materials)
            if not found and role in {"absorber", "poison"}:
                issues.append(_issue("material_universe.required_material_missing", f"required material role {role} not found", row_kind="source_material_coverage", row_key=contract["requirement_id"], requirement_id=contract["requirement_id"], expected_role=role))
    # Duplicate fuel variant materials (two materials same source_variant_id).
    variant_counts: dict[str, int] = {}
    for m in materials_patch.materials:
        if m.source_variant_id:
            variant_counts[m.source_variant_id] = variant_counts.get(m.source_variant_id, 0) + 1
    for variant_id, count in variant_counts.items():
        if count > 1:
            issues.append(_issue("material_universe.fuel_variant_material_duplicate", f"source_variant_id {variant_id} used by {count} materials", row_kind="source_material_coverage", row_key=variant_id, source_variant_id=variant_id))
    return issues


def _collect_universes_issues(universes_patch: Any, view: MaterialUniverseBindingView) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    material_ids = {m.material_id for m in view.material_records}
    for universe in universes_patch.universes:
        uid = universe.universe_id
        if uid in seen_ids:
            issues.append(_issue("material_universe.universe_duplicate", f"duplicate universe_id {uid}", row_kind="required_universe_material_structure", row_key=uid, universe_id=uid))
            continue
        seen_ids.add(uid)
        if not universe.cells:
            issues.append(_issue("material_universe.universe_empty", f"universe {uid} has no cells", row_kind="required_universe_material_structure", row_key=uid, universe_id=uid))
            continue
        seen_cell_ids: set[str] = set()
        radii: list[tuple[str, float | None, float | None]] = []
        for cell in universe.cells:
            if cell.id in seen_cell_ids:
                issues.append(_issue("material_universe.cell_duplicate", f"duplicate cell_id {cell.id} in universe {uid}", row_kind="required_universe_material_structure", row_key=uid, universe_id=uid, cell_id=cell.id))
                continue
            seen_cell_ids.add(cell.id)
            if cell.material_id and cell.material_id not in material_ids:
                issues.append(_issue("material_universe.material_reference_missing", f"universe {uid} cell {cell.id} references unknown material {cell.material_id}", row_kind="material_to_cell_binding", row_key=f"{uid}:{cell.id}", universe_id=uid, cell_id=cell.id, material_id=cell.material_id))
            if cell.r_min_cm is not None and cell.r_max_cm is not None:
                radii.append((cell.id, cell.r_min_cm, cell.r_max_cm))
                if cell.r_min_cm > cell.r_max_cm:
                    issues.append(_issue("material_universe.invalid_radial_order", f"cell {cell.id} r_min > r_max", row_kind="required_universe_material_structure", row_key=uid, universe_id=uid, cell_id=cell.id, severity="error"))
        # Radial gap/overlap detection (concentric cells).
        sorted_radii = sorted([r for r in radii if r[1] is not None and r[2] is not None], key=lambda item: item[1] or 0.0)
        for i in range(1, len(sorted_radii)):
            prev_id, _, prev_max = sorted_radii[i - 1]
            cur_id, cur_min, _ = sorted_radii[i]
            if cur_min is None or prev_max is None:
                continue
            if cur_min > prev_max + 1e-6:
                issues.append(_issue("material_universe.radial_gap", f"radial gap between {prev_id} and {cur_id}", row_kind="required_universe_material_structure", row_key=uid, universe_id=uid, severity="warning"))
            elif cur_min < prev_max - 1e-6:
                issues.append(_issue("material_universe.radial_overlap", f"radial overlap between {prev_id} and {cur_id}", row_kind="required_universe_material_structure", row_key=uid, universe_id=uid))
    # Cell role / material role mismatches from the binding view.
    for binding in view.cell_material_bindings:
        for code in binding.issue_codes:
            issues.append(_issue(code, f"binding {binding.binding_id} failed", row_kind="material_to_cell_binding", row_key=binding.binding_id, universe_id=binding.universe_id, cell_id=binding.cell_id, material_id=binding.material_id))
    return issues


def _collect_fuel_variant_issues(view: MaterialUniverseBindingView) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    for variant in view.fuel_variant_bindings:
        for code in variant.issue_codes:
            issues.append(_issue(code, f"fuel variant {variant.variant_id} failed", row_kind="fuel_variant_identity", row_key=variant.variant_id, variant_id=variant.variant_id, material_id=variant.material_id))
        # Enrichment contract mismatch (deterministic numeric comparison).
        if variant.source_enrichment_wt_percent is not None and variant.material_enrichment_wt_percent is not None:
            if abs(variant.source_enrichment_wt_percent - variant.material_enrichment_wt_percent) > 0.01:
                issues.append(_issue("material_universe.enrichment_contract_mismatch", f"variant {variant.variant_id} source={variant.source_enrichment_wt_percent} material={variant.material_enrichment_wt_percent}", row_kind="fuel_variant_identity", row_key=variant.variant_id, variant_id=variant.variant_id, severity="warning"))
    return issues


def _collect_background_issues(view: MaterialUniverseBindingView) -> list[dict[str, Any]]:
    """Background cell presence for fuel/guide-tube universes."""
    issues: list[dict[str, Any]] = []
    for universe in view.universe_records:
        if universe.kind in {"fuel_pin", "guide_tube", "instrument_tube"} and universe.background_cell_id is None:
            issues.append(_issue("material_universe.background_missing", f"universe {universe.universe_id} ({universe.kind}) has no background cell", row_kind="required_universe_material_structure", row_key=universe.universe_id, universe_id=universe.universe_id, severity="warning"))
    return issues


def run_material_universe_preflight(*, state: Any, policy: PlanClosedLoopPolicy, species_report: dict[str, Any] | None = None) -> MaterialUniversePreflightResult:
    """Run every deterministic check; never invoke an LLM."""
    if not material_universe_gate_applicable(state):
        return MaterialUniversePreflightResult(ok=True, summary={"applicable": False}, input_hash="")
    materials_env = _valid(state, "materials")
    universes_env = _valid(state, "universes")
    if materials_env is None or universes_env is None:
        return MaterialUniversePreflightResult(ok=False, issues=[_issue("material_universe.required_patch_missing", "materials or universes patch missing")], summary={"applicable": True, "ready": False})
    view = build_material_universe_binding_view(state=state, species_report=species_report)
    issues: list[dict[str, Any]] = []
    materials_parsed = None
    universes_parsed = None
    # Reuse single-patch validators.
    try:
        materials_parsed = parse_patch_content("materials", materials_env.content)
        materials_validation = validate_patch(materials_parsed)
        issues.extend({"code": i.code, "severity": i.severity, "message": i.message, "source_validator": True, "row_kind": "source_material_coverage", "row_key": ""} for i in materials_validation.issues if i.severity == "error")
    except Exception as exc:
        issues.append(_issue("material_universe.materials_schema_invalid", f"materials parse failed: {exc}"))
    try:
        universes_parsed = parse_patch_content("universes", universes_env.content)
        universes_validation = validate_patch(universes_parsed)
        issues.extend({"code": i.code, "severity": i.severity, "message": i.message, "source_validator": True, "row_kind": "required_universe_material_structure", "row_key": ""} for i in universes_validation.issues if i.severity == "error")
    except Exception as exc:
        issues.append(_issue("material_universe.universes_schema_invalid", f"universes parse failed: {exc}"))
    # Cross-patch checks run whenever both patches parsed successfully, even
    # if the single-patch validator produced errors.  The two check sets are
    # complementary, not mutually exclusive.
    if materials_parsed is not None and universes_parsed is not None:
        issues.extend(_collect_materials_issues(materials_parsed, view, species_report or {}))
        issues.extend(_collect_universes_issues(universes_parsed, view))
        issues.extend(_collect_fuel_variant_issues(view))
        issues.extend(_collect_background_issues(view))
    # Deduplicate by (code, row_key).
    deduped: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for item in issues:
        key = (str(item.get("code", "")), str(item.get("row_key", "")))
        if key in seen:
            continue
        seen.add(key)
        deduped.append(item)
    input_hash = material_universe_gate_input_hash(state, species_report=species_report, policy=policy)
    matrix = build_material_universe_contract_matrix(view, deduped)
    summary = {
        "applicable": True,
        "ready": True,
        "material_count": len(view.material_records),
        "universe_count": len(view.universe_records),
        "cell_binding_count": len(view.cell_material_bindings),
        "fuel_variant_contract_count": len(view.fuel_variant_bindings),
        "issue_count": len(deduped),
        "blocking_issue_count": sum(1 for item in deduped if item.get("severity") == "error"),
        "matrix_row_count": len(matrix.rows),
    }
    ok = not any(item.get("severity") == "error" for item in deduped)
    return MaterialUniversePreflightResult(ok=ok, binding_view=view, issues=deduped, summary=summary, input_hash=input_hash)


from .material_universe_evidence import material_universe_gate_applicable  # noqa: E402

__all__ = ["MaterialUniversePreflightResult", "run_material_universe_preflight"]
