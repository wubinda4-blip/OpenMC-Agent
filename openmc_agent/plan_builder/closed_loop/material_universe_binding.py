"""Material-Universe static binding view.

Builds a deterministic, reactor-neutral view of the Materials → Universes
edge: which materials the source requires, which materials exist, which
universes reference them, and which fuel variants keep an independent
identity.  This is *static* reachability — it does not claim that a universe
is actually placed in any lattice.
"""

from __future__ import annotations

from typing import Any

from openmc_agent.plan_builder.patches import parse_patch_content

from .fingerprints import compute_candidate_hash
from .models import (
    CellMaterialBinding,
    FuelVariantBinding,
    MaterialRecord,
    MaterialUniverseBindingView,
    UniverseRecord,
)


_MATERIAL_UNIVERSE_PATCH_TYPES = ("facts", "materials", "universes")


def _valid(state: Any, patch_type: str) -> Any | None:
    matches = [item for item in state.patches.values() if item.patch_type == patch_type and item.status == "valid"]
    if len(matches) > 1:
        raise ValueError(f"material_universe.multiple_valid_envelopes:{patch_type}")
    return matches[0] if matches else None


def _facts_content(state: Any) -> dict[str, Any]:
    env = _valid(state, "facts")
    return env.content if env is not None else {}


def _hash(state: Any, patch_type: str) -> str:
    env = _valid(state, patch_type)
    return compute_candidate_hash(target_patch_type=patch_type, candidate_patch=env.content) if env else ""


def _required_material_contracts(facts: Any, facts_dict: dict[str, Any]) -> list[dict[str, Any]]:
    """Extract source-driven material role requirements from accepted Facts.

    Never invents material IDs — when the source only declares a role, the
    ``material_id`` stays ``None`` so the contract matrix can report the
    coverage gap deterministically.
    """
    contracts: list[dict[str, Any]] = []
    for variant in (facts.fuel_variant_requirements if facts else []):
        contracts.append({
            "requirement_id": f"fuel_variant:{variant.variant_id}",
            "requirement_kind": "fuel",
            "expected_role": "fuel",
            "expected_variant_id": variant.variant_id,
            "expected_enrichment_wt_percent": variant.enrichment_wt_percent,
            "expected_density_g_cm3": variant.density_g_cm3,
            "assembly_type_ids": list(variant.assembly_type_ids),
            "material_id": None,
        })
    for req in (facts.localized_insert_requirements if facts else []):
        # Each localized insert implies at least one absorber/poison/structural
        # material role.  We do not invent the ID; we only record the role.
        kind = req.insert_kind
        if kind in {"control_rod", "absorber_insert"}:
            contracts.append({
                "requirement_id": f"localized_insert:{req.requirement_id}",
                "requirement_kind": "absorber",
                "expected_role": "absorber",
                "expected_variant_id": None,
                "material_id": None,
                "host_kind": req.host_kind,
            })
        elif kind == "pyrex_rod":
            contracts.append({
                "requirement_id": f"localized_insert:{req.requirement_id}",
                "requirement_kind": "poison",
                "expected_role": "poison",
                "expected_variant_id": None,
                "material_id": None,
                "host_kind": req.host_kind,
            })
        elif kind == "thimble_plug":
            contracts.append({
                "requirement_id": f"localized_insert:{req.requirement_id}",
                "requirement_kind": "structural",
                "expected_role": "structural",
                "expected_variant_id": None,
                "material_id": None,
                "host_kind": req.host_kind,
            })
    # Standard structural/coolant roles that every reactor model needs.
    # These are recorded as low-priority contracts; if the source does not
    # name them they remain info-level (the preflight will only flag a
    # missing fuel variant as an error, not a missing coolant).
    contracts.append({"requirement_id": "implicit:coolant", "requirement_kind": "coolant", "expected_role": "coolant", "expected_variant_id": None, "material_id": None, "implicit": True})
    contracts.append({"requirement_id": "implicit:cladding", "requirement_kind": "cladding", "expected_role": "cladding", "expected_variant_id": None, "material_id": None, "implicit": True})
    return contracts


def _material_record(material: Any, species_report: dict[str, Any], required_by: dict[str, list[str]]) -> MaterialRecord:
    mid = material.material_id
    resolver_entry = species_report.get("materials", {}).get(mid, {}) if isinstance(species_report, dict) else {}
    return MaterialRecord(
        material_id=mid,
        name=material.name,
        role=material.role,
        source_variant_id=material.source_variant_id,
        density_g_cm3=material.density_g_cm3,
        density_status=material.density_status,
        density_source=material.density_source,
        temperature_K=material.temperature_K,
        composition_status=material.composition_status,
        composition=dict(material.composition),
        composition_basis=material.composition_basis,
        compound_component_count=len(material.compound_components),
        resolver_status=str(resolver_entry.get("status", "unknown")),
        resolver_normalized_species=dict(resolver_entry.get("normalized_species", {})),
        resolver_warnings=list(resolver_entry.get("warnings", [])),
        warnings=list(material.warnings),
        required_by_source=list(required_by.get(mid, [])),
        static_consumers=[],
    )


def _universe_record(universe: Any, required_by: dict[str, list[str]]) -> UniverseRecord:
    cells = list(universe.cells)
    material_ids = sorted({cell.material_id for cell in cells if cell.material_id})
    roles = [cell.role for cell in cells]
    background = next((cell.id for cell in cells if cell.region_kind == "background"), None)
    return UniverseRecord(
        universe_id=universe.universe_id,
        kind=universe.kind,
        fuel_variant_id=getattr(universe, "fuel_variant_id", None),
        cell_count=len(cells),
        material_ids=material_ids,
        cell_roles=roles,
        background_cell_id=background,
        required_by_source=list(required_by.get(universe.universe_id, [])),
    )


# Reactor-neutral role compatibility.  A material role is compatible with a
# cell role if the cell role appears in the material role's allowed list.
# This is intentionally permissive for ``custom`` roles — Python cannot
# decide semantic compatibility for unknown roles, so those are flagged
# ``unresolved`` for the Critic / human.
_ROLE_COMPATIBILITY: dict[str, set[str]] = {
    "fuel": {"fuel"},
    "cladding": {"clad", "cladding", "wall", "tube", "endplug", "internal"},
    "coolant": {"coolant", "moderator", "background", "water", "gas", "inner_flow"},
    "moderator": {"coolant", "moderator", "background", "water"},
    "structural": {"clad", "cladding", "wall", "tube", "structural", "frame", "can", "endplug", "internal"},
    "absorber": {"absorber", "poison", "control"},
    "poison": {"absorber", "poison", "control"},
    "gas": {"gap", "plenum", "coolant", "gas", "gas_gap"},
}


def _role_compatible(cell_role: str, material_role: str) -> str:
    """Return 'pass', 'fail', or 'unresolved' for role compatibility.

    Per Phase-4 spec: unknown custom roles do not directly report error;
    they output 'unresolved' for the Critic / human to decide.  Only clearly
    incompatible standard roles produce 'fail'.
    """
    cell_role_l = cell_role.lower()
    material_role_l = material_role.lower()
    allowed = _ROLE_COMPATIBILITY.get(material_role_l)
    if allowed is None:
        # Material role not in registry → unresolved, not fail.
        return "unresolved"
    for token in allowed:
        if token in cell_role_l or cell_role_l in token:
            return "pass"
    # Before declaring fail, check if the cell role is a known custom role
    # (not in any standard compatibility set).  If so, it is unresolved.
    all_standard_cell_roles = {token for tokens in _ROLE_COMPATIBILITY.values() for token in tokens}
    if not any(token in cell_role_l for token in all_standard_cell_roles):
        return "unresolved"
    return "fail"


def build_material_universe_binding_view(*, state: Any, species_report: dict[str, Any] | None = None) -> MaterialUniverseBindingView:
    """Construct the static binding view from valid patches.

    The view never invokes an LLM and never claims root reachability.
    """
    facts_env = _valid(state, "facts")
    materials_env = _valid(state, "materials")
    universes_env = _valid(state, "universes")
    facts = parse_patch_content("facts", facts_env.content) if facts_env else None
    materials = parse_patch_content("materials", materials_env.content) if materials_env else None
    universes = parse_patch_content("universes", universes_env.content) if universes_env else None
    species = species_report or {}
    contracts = _required_material_contracts(facts, facts_env.content if facts_env else {})
    required_by_material: dict[str, list[str]] = {}
    for contract in contracts:
        mid = contract.get("material_id")
        if mid:
            required_by_material.setdefault(mid, []).append(contract["requirement_id"])
    material_records = [_material_record(m, species, required_by_material) for m in (materials.materials if materials else [])]
    # Map source variant_id → material_id for fuel variant binding.
    variant_to_material: dict[str, str] = {}
    for m in (materials.materials if materials else []):
        if m.source_variant_id:
            variant_to_material.setdefault(m.source_variant_id, m.material_id)
    required_by_universe: dict[str, list[str]] = {}
    if facts:
        for req in facts.localized_insert_requirements:
            for uid in req.expected_insert_universe_ids:
                required_by_universe.setdefault(uid, []).append(req.requirement_id)
    universe_records = [_universe_record(u, required_by_universe) for u in (universes.universes if universes else [])]
    # Cell-material bindings.
    material_by_id = {m.material_id: m for m in (materials.materials if materials else [])}
    universe_by_id = {u.universe_id: u for u in (universes.universes if universes else [])}
    cell_bindings: list[CellMaterialBinding] = []
    unresolved: list[dict[str, Any]] = []
    if universes:
        for universe in universes.universes:
            for cell in universe.cells:
                binding_id = f"{universe.universe_id}:{cell.id}"
                material = material_by_id.get(cell.material_id) if cell.material_id else None
                status = "pass"
                issue_codes: list[str] = []
                if cell.material_id and material is None:
                    status = "fail"
                    issue_codes.append("material_universe.material_reference_missing")
                    unresolved.append({"universe_id": universe.universe_id, "cell_id": cell.id, "material_id": cell.material_id, "kind": "unknown_material_reference"})
                elif material is not None:
                    compat = _role_compatible(cell.role, material.role)
                    if compat == "fail":
                        status = "fail"
                        issue_codes.append("material_universe.material_role_mismatch")
                    elif compat == "unresolved":
                        status = "unresolved"
                cell_bindings.append(CellMaterialBinding(
                    binding_id=binding_id,
                    universe_id=universe.universe_id,
                    universe_kind=universe.kind,
                    cell_id=cell.id,
                    cell_role=cell.role,
                    region_kind=cell.region_kind,
                    r_min_cm=cell.r_min_cm,
                    r_max_cm=cell.r_max_cm,
                    material_id=cell.material_id,
                    material_role=material.role if material else None,
                    material_source_variant_id=material.source_variant_id if material else None,
                    status=status,  # type: ignore[arg-type]
                    issue_codes=issue_codes,
                ))
    # Fuel variant bindings.
    variant_bindings: list[FuelVariantBinding] = []
    if facts:
        for variant in facts.fuel_variant_requirements:
            material_id = variant_to_material.get(variant.variant_id)
            active_fuel_universe_ids: list[str] = []
            active_fuel_cell_ids: list[str] = []
            actual_variant_ids: list[str] = []
            for u in universe_records:
                if u.kind == "fuel_pin":
                    active_fuel_universe_ids.append(u.universe_id)
                    active_fuel_cell_ids.extend(f"{u.universe_id}:{cid}" for cid in _fuel_cell_ids(universe_by_id.get(u.universe_id)))
                    if u.fuel_variant_id:
                        actual_variant_ids.append(u.fuel_variant_id)
            collapsed = sorted({v for v in actual_variant_ids if v != variant.variant_id})
            status_val = "pass"
            issue_codes: list[str] = []
            if not active_fuel_universe_ids:
                status_val = "fail"
                issue_codes.append("material_universe.fuel_variant_material_unreachable")
            if material_id is None:
                status_val = "fail"
                issue_codes.append("material_universe.required_fuel_variant_material_missing")
            if len(set(actual_variant_ids)) > 1:
                status_val = "fail"
                issue_codes.append("material_universe.multiple_variants_in_one_universe")
            if collapsed:
                status_val = "fail"
                issue_codes.append("material_universe.fuel_variant_collapsed")
            enrichment = None
            if material_id and material_id in material_by_id:
                enrichment = material_by_id[material_id].composition.get("U235") if material_by_id[material_id].composition else None
            variant_bindings.append(FuelVariantBinding(
                variant_id=variant.variant_id,
                source_enrichment_wt_percent=variant.enrichment_wt_percent,
                material_id=material_id,
                material_source_variant_id=variant.variant_id if material_id else None,
                material_enrichment_wt_percent=enrichment,
                active_fuel_universe_ids=active_fuel_universe_ids,
                active_fuel_cell_ids=active_fuel_cell_ids,
                variant_count=len(set(actual_variant_ids)),
                collapsed_with_variants=collapsed,
                status=status_val,  # type: ignore[arg-type]
                issue_codes=issue_codes,
            ))
    # Static reachability edges: material → universe → cell.
    edges: list[dict[str, Any]] = []
    for binding in cell_bindings:
        if binding.material_id:
            edges.append({"material_id": binding.material_id, "universe_id": binding.universe_id, "cell_id": binding.cell_id})
    view = MaterialUniverseBindingView(
        planning_scope=str(facts.model_scope) if facts else "unknown",
        facts_patch_hash=_hash(state, "facts"),
        materials_patch_hash=_hash(state, "materials"),
        universes_patch_hash=_hash(state, "universes"),
        feature_contract_hash=str(state.planning_feature_contract.contract_hash if state.planning_feature_contract else ""),
        canonical_task_plan_hash=str(state.canonical_task_plan.plan_hash if state.canonical_task_plan else ""),
        required_material_contracts=contracts,
        material_records=material_records,
        universe_records=universe_records,
        cell_material_bindings=cell_bindings,
        fuel_variant_bindings=variant_bindings,
        unresolved_references=unresolved,
        static_reachability_edges=edges,
    )
    return view


def _fuel_cell_ids(universe: Any) -> list[str]:
    if universe is None:
        return []
    return [cell.id for cell in universe.cells if cell.region_kind != "background"]


__all__ = ["build_material_universe_binding_view"]
