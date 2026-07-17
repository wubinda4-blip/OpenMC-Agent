"""Material-Universe evidence pack, contract matrix, and gate applicability.

Mirrors the Placement evidence module but for the Materials → Universes
static edge.  The contract matrix has four row kinds:
- source_material_coverage
- material_to_cell_binding
- fuel_variant_identity
- required_universe_material_structure
"""

from __future__ import annotations

from typing import Any

from openmc_agent.plan_builder.patches import parse_patch_content

from .fingerprints import compute_candidate_hash, compute_evidence_pack_hash
from .material_universe_binding import _valid, build_material_universe_binding_view
from .models import (
    MaterialUniverseContractMatrix,
    MaterialUniverseContractRow,
    MaterialUniverseEvidencePack,
    MaterialUniverseBindingView,
    PlanClosedLoopPolicy,
    PlanEvidenceItem,
    PlanGateId,
    PlanLoopMode,
    PlanReviewAction,
)


def material_universe_gate_applicable(state: Any) -> bool:
    """Gate applies when the canonical task plan requires materials or universes."""
    if state.canonical_task_plan is None:
        # Fall back to patch-presence heuristic until the task plan is built.
        return _valid(state, "materials") is not None or _valid(state, "universes") is not None
    ordered = set(state.canonical_task_plan.ordered_patch_types)
    return "materials" in ordered or "universes" in ordered


def material_universe_gate_ready(state: Any) -> bool:
    """Controlled/advisory review requires valid Facts+Materials+Universes."""
    if not material_universe_gate_applicable(state):
        return False
    for patch_type in ("facts", "materials", "universes"):
        if _valid(state, patch_type) is None:
            return False
    return True


def material_universe_gate_input_hash(state: Any, *, species_report: dict[str, Any] | None = None, policy: PlanClosedLoopPolicy | None = None) -> str:
    """Bind the gate input to every input that should invalidate the accepted hash."""
    view = build_material_universe_binding_view(state=state, species_report=species_report)
    matrix = build_material_universe_contract_matrix(view)
    payload: dict[str, Any] = {
        "facts_patch_hash": view.facts_patch_hash,
        "materials_patch_hash": view.materials_patch_hash,
        "universes_patch_hash": view.universes_patch_hash,
        "feature_contract_hash": view.feature_contract_hash,
        "canonical_task_plan_hash": view.canonical_task_plan_hash,
        "matrix_hash": matrix.input_hash,
        "material_species_report_hash": compute_evidence_pack_hash(species_report or {}),
        "confirmed_records": [str(item) for item in (state.plan_confirmed_plan_fact_records.keys() if hasattr(state, "plan_confirmed_plan_fact_records") else [])],
    }
    if policy is not None:
        payload["material_policy"] = str(policy.metadata.get("material_policy", "default"))
        payload["structural_density_policy"] = str(getattr(state, "metadata", {}).get("structural_density_policy", "source_only"))
        payload["material_universe_review_mode"] = policy.material_universe_review_mode
        payload["review_schema_version"] = "1"
    return compute_evidence_pack_hash(payload)


def build_material_universe_contract_matrix(view: MaterialUniverseBindingView, issues: list[dict[str, Any]] | None = None) -> MaterialUniverseContractMatrix:
    """Construct the four-kind contract matrix from the binding view.

    Issues from the deterministic preflight are attached to the matching row
    via ``requirement_id``/``material_id``/``universe_id``/``variant_id``.
    """
    by_key: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for issue in issues or []:
        kind = str(issue.get("row_kind", "source_material_coverage"))
        key = str(issue.get("row_key", ""))
        by_key.setdefault((kind, key), []).append(issue)
    rows: list[MaterialUniverseContractRow] = []

    def _codes(kind: str, key: str) -> list[str]:
        return sorted({str(item.get("code")) for item in by_key.get((kind, key), [])})

    # 1. Source material coverage rows.
    for contract in view.required_material_contracts:
        rid = contract["requirement_id"]
        role = contract.get("expected_role", "")
        actual_material_ids = [m.material_id for m in view.material_records if m.role == role and not contract.get("implicit")]
        if not actual_material_ids and contract.get("implicit"):
            actual_material_ids = [m.material_id for m in view.material_records if m.role == role]
        coverage = "pass" if actual_material_ids else ("ambiguous" if contract.get("implicit") else "fail")
        rows.append(MaterialUniverseContractRow(
            row_id=f"smc:{rid}",
            row_kind="source_material_coverage",
            requirement_id=rid,
            material_id=actual_material_ids[0] if actual_material_ids else None,
            material_role=role,
            expected_roles=[role] if role else [],
            actual_roles=sorted({m.role for m in view.material_records if m.material_id in actual_material_ids}),
            coverage_status=coverage,  # type: ignore[arg-type]
            issue_codes=_codes("source_material_coverage", rid),
            metadata={"expected_variant_id": contract.get("expected_variant_id"), "implicit": bool(contract.get("implicit"))},
        ))
    # 2. Material-to-cell binding rows.
    for binding in view.cell_material_bindings:
        # Map binding status to contract matrix coverage status vocabulary.
        coverage = binding.status
        if coverage == "unresolved":
            coverage = "ambiguous"
        rows.append(MaterialUniverseContractRow(
            row_id=f"m2c:{binding.binding_id}",
            row_kind="material_to_cell_binding",
            material_id=binding.material_id,
            material_role=binding.material_role,
            universe_id=binding.universe_id,
            cell_id=binding.cell_id,
            cell_role=binding.cell_role,
            expected_roles=binding.expected_roles,
            actual_roles=[binding.cell_role] if binding.cell_role else [],
            expected_material_roles=[binding.material_role] if binding.material_role else [],
            actual_material_roles=[binding.material_role] if binding.material_role else [],
            coverage_status=coverage,  # type: ignore[arg-type]
            issue_codes=list(binding.issue_codes),
        ))
    # 3. Fuel variant identity rows.
    for variant in view.fuel_variant_bindings:
        rows.append(MaterialUniverseContractRow(
            row_id=f"fvi:{variant.variant_id}",
            row_kind="fuel_variant_identity",
            variant_id=variant.variant_id,
            material_id=variant.material_id,
            expected_variant_id=variant.variant_id,
            actual_variant_ids=[variant.variant_id] if variant.material_id else [],
            coverage_status=variant.status,  # type: ignore[arg-type]
            issue_codes=list(variant.issue_codes),
            metadata={"source_enrichment_wt_percent": variant.source_enrichment_wt_percent, "material_enrichment_wt_percent": variant.material_enrichment_wt_percent},
        ))
    # 4. Required universe material structure rows (reactor-neutral defaults).
    _UNIVERSE_STRUCTURE: dict[str, dict[str, Any]] = {
        "fuel_pin": {"required_roles": ["fuel"], "required_material_roles": ["fuel"]},
        "guide_tube": {"required_roles": ["wall", "coolant"], "required_material_roles": ["structural", "coolant"]},
        "instrument_tube": {"required_roles": ["coolant"], "required_material_roles": ["coolant"]},
        "control_rod": {"required_roles": ["absorber"], "required_material_roles": ["absorber"]},
        "pyrex_rod": {"required_roles": ["poison"], "required_material_roles": ["poison"]},
        "thimble_plug": {"required_roles": ["structural"], "required_material_roles": ["structural"]},
    }
    for universe in view.universe_records:
        spec = _UNIVERSE_STRUCTURE.get(universe.kind)
        if spec is None:
            continue
        actual_roles = sorted(set(universe.cell_roles))
        actual_material_roles = sorted({m.role for m in view.material_records if m.material_id in universe.material_ids})
        missing_roles = [r for r in spec["required_roles"] if not any(r in ar for ar in actual_roles)]
        missing_material_roles = [r for r in spec["required_material_roles"] if not any(r in amr for amr in actual_material_roles)]
        coverage = "pass" if not missing_roles and not missing_material_roles else "fail"
        rows.append(MaterialUniverseContractRow(
            row_id=f"rums:{universe.universe_id}",
            row_kind="required_universe_material_structure",
            universe_id=universe.universe_id,
            expected_roles=spec["required_roles"],
            actual_roles=actual_roles,
            expected_material_roles=spec["required_material_roles"],
            actual_material_roles=actual_material_roles,
            coverage_status=coverage,  # type: ignore[arg-type]
            issue_codes=_codes("required_universe_material_structure", universe.universe_id),
            metadata={"universe_kind": universe.kind},
        ))
    matrix = MaterialUniverseContractMatrix(rows=rows)
    matrix.input_hash = compute_evidence_pack_hash(matrix)
    return matrix


def build_material_universe_evidence_pack(*, state: Any, policy: PlanClosedLoopPolicy, species_report: dict[str, Any] | None = None, deterministic_issues: list[dict[str, Any]] | None = None) -> MaterialUniverseEvidencePack:
    view = build_material_universe_binding_view(state=state, species_report=species_report)
    matrix = build_material_universe_contract_matrix(view, deterministic_issues)
    patch_hashes: dict[str, str] = {}
    for ptype in ("facts", "materials", "universes"):
        env = _valid(state, ptype)
        if env is not None:
            patch_hashes[ptype] = compute_candidate_hash(target_patch_type=ptype, candidate_patch=env.content)
    items: list[PlanEvidenceItem] = []
    index = 1

    def _add(kind: str, prefix: str, patch_type: str | None, path: str | None, label: str, value: Any, metadata: dict[str, Any] | None = None) -> None:
        nonlocal index
        canonical_hash = compute_evidence_pack_hash({"kind": kind, "patch_type": patch_type, "path": path, "value": value})
        items.append(PlanEvidenceItem(ref_id=f"{prefix}{index:03d}", evidence_kind=kind, patch_type=patch_type, json_path=path, label=label, value=value, canonical_hash=canonical_hash, metadata=metadata or {}))
        index += 1

    for contract in view.required_material_contracts:
        _add("accepted_fact_contract", "F", "facts", "/required_material_contracts", f"source material contract {contract['requirement_id']}", contract)
    for material in view.material_records:
        _add("patch_fragment", "M", "materials", f"/materials/{material.material_id}", f"material {material.material_id}", material.model_dump(mode="json"))
    for universe in view.universe_records:
        _add("patch_fragment", "U", "universes", f"/universes/{universe.universe_id}", f"universe {universe.universe_id}", universe.model_dump(mode="json"))
    for binding in view.cell_material_bindings:
        _add("patch_fragment", "C", "universes", f"/universes/{binding.universe_id}/cells/{binding.cell_id}", f"cell binding {binding.binding_id}", binding.model_dump(mode="json"))
    for variant in view.fuel_variant_bindings:
        _add("accepted_fact_contract", "V", "facts", f"/fuel_variant_requirements/{variant.variant_id}", f"fuel variant {variant.variant_id}", variant.model_dump(mode="json"))
    for issue in deterministic_issues or []:
        _add("deterministic_issue", "D", None, None, f"deterministic issue {issue.get('code', '')}", issue)
    pack = MaterialUniverseEvidencePack(
        binding_view=view,
        contract_matrix=matrix,
        material_species_report=species_report or {},
        deterministic_issues=list(deterministic_issues or []),
        relevant_patch_hashes=patch_hashes,
        accepted_facts_hash=patch_hashes.get("facts", ""),
        evidence_items=items,
        confirmed_records=[item.model_dump(mode="json") for item in getattr(state, "plan_confirmed_plan_fact_records", {}).values()],
        allowed_actions=list(_allowed_review_actions(policy)),
    )
    pack.input_hash = material_universe_gate_input_hash(state, species_report=species_report, policy=policy)
    pack.evidence_pack_id = pack.input_hash
    return pack


def _allowed_review_actions(policy: PlanClosedLoopPolicy) -> list[PlanReviewAction]:
    if policy.mode is PlanLoopMode.OFF:
        return []
    return [PlanReviewAction.APPROVE, PlanReviewAction.REVISE_CURRENT_PATCH, PlanReviewAction.RETRY_DEPENDENCY, PlanReviewAction.ASK_HUMAN, PlanReviewAction.FAIL_CLOSED]


__all__ = [
    "material_universe_gate_applicable",
    "material_universe_gate_ready",
    "material_universe_gate_input_hash",
    "build_material_universe_contract_matrix",
    "build_material_universe_evidence_pack",
]
