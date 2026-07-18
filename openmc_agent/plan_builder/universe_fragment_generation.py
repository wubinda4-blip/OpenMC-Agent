"""Large structured patch generation: universe fragment pipeline.

This module implements the fragmented universe generation pipeline:

    accepted Facts/Materials
    → requirement inventory
    → universe manifest (LLM)
    → one-universe fragments (LLM, one call each)
    → deterministic merge
    → standard UniversesPatch validation

The pipeline is designed to avoid truncation on large universe patches
(e.g. VERA4 with 11+ universes).  Each LLM call produces only one
universe, keeping the structured output small enough to complete within
provider token limits.

Key design principles:
- Thinking/reasoning mode is NOT disabled.  Reliability comes from
  shrinking each structured output, not from larger token budgets.
- Checkpoint/resume: completed fragments are never re-generated.
- Partial fragments are never exposed to downstream gates.
- The final output is a standard UniversesPatch that passes existing
  validators unchanged.
"""

from __future__ import annotations

import json
from typing import Any, Literal

from pydantic import Field

from openmc_agent.schemas import AgentBaseModel

from .llm_adapter import PatchLLMResponse, normalize_patch_llm_response
from .patches import parse_patch_content
from .validators import PatchValidationContext, validate_patch


# ---------------------------------------------------------------------------
# Requirement inventory
# ---------------------------------------------------------------------------


class UniverseGenerationRequirement(AgentBaseModel):
    """A single universe generation requirement extracted from accepted upstream context."""
    requirement_id: str
    universe_id: str = ""
    kind: str = ""
    required_cell_roles: list[str] = Field(default_factory=list)
    required_material_ids: list[str] = Field(default_factory=list)
    required_material_roles: list[str] = Field(default_factory=list)
    fuel_variant_id: str | None = None
    localized_insert_requirement_id: str | None = None
    base_path_component_profile_id: str | None = None
    protected_through_path_roles: list[str] = Field(default_factory=list)
    source_requirement_ids: list[str] = Field(default_factory=list)
    dependency_ids: list[str] = Field(default_factory=list)
    resolved: bool = False
    metadata: dict[str, Any] = Field(default_factory=dict)


class UniverseGenerationRequirementSet(AgentBaseModel):
    """The complete set of universe generation requirements."""
    requirements: list[UniverseGenerationRequirement] = Field(default_factory=list)
    unresolved_requirements: list[UniverseGenerationRequirement] = Field(default_factory=list)
    input_hash: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)


def extract_universe_requirements(
    *,
    facts: Any | None = None,
    materials: Any | None = None,
    canonical_task_plan: Any | None = None,
    confirmed_records: dict[str, Any] | None = None,
) -> UniverseGenerationRequirementSet:
    """Deterministically extract universe requirements from accepted upstream context.

    Sources (only these are allowed):
    - Facts: localized_insert_requirements, fuel_variant_requirements,
      planning_feature_contract.
    - Materials: material IDs and roles.
    - Canonical task plan: patch type ordering.
    - Confirmed human facts.

    Benchmark names, VERA3/VERA4 strings, and fixture names must NOT
    influence the requirement list.
    """
    requirements: list[UniverseGenerationRequirement] = []
    unresolved: list[UniverseGenerationRequirement] = []
    material_ids: set[str] = set()
    material_roles: set[str] = set()
    if materials is not None:
        for m in getattr(materials, "materials", []) or []:
            material_ids.add(m.material_id)
            if hasattr(m, "role") and m.role:
                material_roles.add(m.role)
    # Fuel variant requirements.
    if facts is not None:
        for variant in getattr(facts, "fuel_variant_requirements", []) or []:
            requirements.append(UniverseGenerationRequirement(
                requirement_id=f"fuel_variant:{variant.variant_id}",
                kind="fuel_pin",
                required_cell_roles=["fuel"],
                required_material_roles=["fuel"],
                fuel_variant_id=variant.variant_id,
                source_requirement_ids=[f"fuel_variant:{variant.variant_id}"],
                resolved=True,
            ))
        # Localized insert requirements.
        for req in getattr(facts, "localized_insert_requirements", []) or []:
            kind_map = {
                "control_rod": "control_rod",
                "absorber_insert": "control_rod",
                "pyrex_rod": "pyrex_rod",
                "thimble_plug": "thimble_plug",
            }
            mapped_kind = kind_map.get(getattr(req, "insert_kind", ""), "custom")
            requirements.append(UniverseGenerationRequirement(
                requirement_id=f"localized_insert:{req.requirement_id}",
                kind=mapped_kind,
                required_material_roles=["absorber"] if mapped_kind == "control_rod" else (["poison"] if mapped_kind == "pyrex_rod" else ["structural"]),
                localized_insert_requirement_id=req.requirement_id,
                source_requirement_ids=[f"localized_insert:{req.requirement_id}"],
                resolved=True,
            ))
    # Guide tube and instrument tube are standard requirements for assembly models.
    if facts is not None and getattr(facts, "planning_feature_contract", None):
        fc = facts.planning_feature_contract
        if getattr(fc, "expected_guide_tube_count", None):
            requirements.append(UniverseGenerationRequirement(
                requirement_id="implicit:guide_tube",
                kind="guide_tube",
                required_cell_roles=["wall", "coolant"],
                required_material_roles=["structural", "coolant"],
                source_requirement_ids=["implicit:guide_tube"],
                resolved=True,
            ))
        if getattr(fc, "expected_instrument_tube_count", None):
            requirements.append(UniverseGenerationRequirement(
                requirement_id="implicit:instrument_tube",
                kind="instrument_tube",
                required_cell_roles=["coolant"],
                required_material_roles=["coolant"],
                source_requirement_ids=["implicit:instrument_tube"],
                resolved=True,
            ))
    # Auxiliary universes needed for axial layer transitions.  When the
    # facts patch declares axial geometry, the downstream axial_layers
    # patch will reference these cross-sectional profiles for non-active-
    # fuel regions (end plugs, plenum, shoulder gaps).  Declaring them
    # as implicit requirements ensures the universes patch generates them
    # rather than leaving axial_layers to reference non-existent IDs.
    _has_axial = bool(getattr(facts, "has_axial_geometry", False)) if facts else False
    if _has_axial:
        _aux_specs = [
            ("implicit:end_plug_lower", "custom", ["structural"], ["end_plug"]),
            ("implicit:end_plug_upper", "custom", ["structural"], ["end_plug"]),
            ("implicit:gas_gap", "custom", ["coolant", "structural"], ["gas_gap"]),
            ("implicit:water_pin", "water_cell", ["coolant"], ["moderator"]),
        ]
        for req_id, kind, roles, cell_roles in _aux_specs:
            requirements.append(UniverseGenerationRequirement(
                requirement_id=req_id,
                kind=kind,
                required_cell_roles=cell_roles,
                required_material_roles=roles,
                source_requirement_ids=[req_id],
                resolved=True,
            ))
    import hashlib
    payload = json.dumps({
        "reqs": [r.model_dump(mode="json") for r in requirements],
        "material_ids": sorted(material_ids),
    }, sort_keys=True, default=str)
    input_hash = hashlib.sha256(payload.encode()).hexdigest()[:16]
    return UniverseGenerationRequirementSet(
        requirements=requirements,
        unresolved_requirements=unresolved,
        input_hash=input_hash,
        metadata={"material_ids": sorted(material_ids), "material_roles": sorted(material_roles)},
    )


# ---------------------------------------------------------------------------
# Manifest
# ---------------------------------------------------------------------------


class UniverseManifestItem(AgentBaseModel):
    """One entry in the universe manifest: describes a single universe to generate."""
    universe_id: str
    kind: str = "custom"
    required_cell_roles: list[str] = Field(default_factory=list)
    required_material_ids: list[str] = Field(default_factory=list)
    required_material_roles: list[str] = Field(default_factory=list)
    fuel_variant_id: str | None = None
    expected_cell_count: int | None = None
    protected_through_path_roles: list[str] = Field(default_factory=list)
    source_requirement_ids: list[str] = Field(default_factory=list)
    dependency_ids: list[str] = Field(default_factory=list)
    assumptions_allowed: bool = True
    metadata: dict[str, Any] = Field(default_factory=dict)


class UniverseManifest(AgentBaseModel):
    """The complete manifest of universes to generate."""
    manifest_id: str = ""
    input_hash: str = ""
    expected_universe_count: int = 0
    items: list[UniverseManifestItem] = Field(default_factory=list)
    unresolved_requirements: list[dict[str, Any]] = Field(default_factory=list)
    generation_order: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


def validate_manifest(
    manifest: UniverseManifest,
    requirement_set: UniverseGenerationRequirementSet,
    *,
    known_material_ids: set[str] | None = None,
) -> list[str]:
    """Validate that the manifest covers all requirements. Returns list of error codes."""
    errors: list[str] = []
    # Check for duplicate universe IDs.
    ids = [item.universe_id for item in manifest.items]
    if len(ids) != len(set(ids)):
        errors.append("manifest.duplicate_universe_id")
    # Check that all resolved requirements are covered.
    req_ids = {r.requirement_id for r in requirement_set.requirements if r.resolved}
    covered_ids = set()
    for item in manifest.items:
        covered_ids.update(item.source_requirement_ids)
    missing = req_ids - covered_ids
    if missing:
        errors.append("manifest.missing_required_universe")
    # Check that material IDs are known.
    if known_material_ids is not None:
        for item in manifest.items:
            for mid in item.required_material_ids:
                if mid not in known_material_ids:
                    errors.append(f"manifest.unknown_material_id:{mid}")
                    break
    # Check generation_order covers all items.
    order_set = set(manifest.generation_order)
    item_set = set(ids)
    if order_set != item_set:
        errors.append("manifest.generation_order_mismatch")
    return errors


def build_manifest_from_requirements(
    requirement_set: UniverseGenerationRequirementSet,
    *,
    known_material_ids: set[str] | None = None,
) -> UniverseManifest:
    """Build a manifest from the requirement set (deterministic, no LLM)."""
    items: list[UniverseManifestItem] = []
    for req in requirement_set.requirements:
        if not req.resolved:
            continue
        uid = req.universe_id or req.requirement_id.replace(":", "_")
        items.append(UniverseManifestItem(
            universe_id=uid,
            kind=req.kind,
            required_cell_roles=list(req.required_cell_roles),
            required_material_ids=list(req.required_material_ids),
            required_material_roles=list(req.required_material_roles),
            fuel_variant_id=req.fuel_variant_id,
            source_requirement_ids=[req.requirement_id],
            dependency_ids=list(req.dependency_ids),
        ))
    import hashlib
    manifest_hash = hashlib.sha256(json.dumps([i.model_dump(mode="json") for i in items], sort_keys=True, default=str).encode()).hexdigest()[:16]
    return UniverseManifest(
        manifest_id=f"manifest:{manifest_hash}",
        input_hash=requirement_set.input_hash,
        expected_universe_count=len(items),
        items=items,
        generation_order=[i.universe_id for i in items],
    )


# ---------------------------------------------------------------------------
# Fragment
# ---------------------------------------------------------------------------


class UniverseDefinitionFragment(AgentBaseModel):
    """A single universe definition fragment from one LLM call."""
    fragment_type: Literal["universe_definition"] = "universe_definition"
    universe_id: str
    universe: dict[str, Any] = Field(default_factory=dict)
    fragment_hash: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# Session / checkpoint
# ---------------------------------------------------------------------------


class FragmentStatus(AgentBaseModel):
    universe_id: str
    status: Literal["pending", "accepted", "failed"] = "pending"
    fragment_hash: str = ""
    issues: list[dict[str, Any]] = Field(default_factory=list)
    llm_calls: int = 0
    metadata: dict[str, Any] = Field(default_factory=dict)


class LargePatchGenerationSession(AgentBaseModel):
    """Checkpoint session for large patch generation (universes)."""
    session_id: str = ""
    patch_type: str = "universes"
    input_hash: str = ""
    mode: Literal["auto", "monolithic", "fragmented"] = "auto"
    requirement_set_hash: str = ""
    manifest: UniverseManifest | None = None
    manifest_status: Literal["pending", "accepted", "failed"] = "pending"
    fragment_statuses: list[FragmentStatus] = Field(default_factory=list)
    accepted_fragment_hashes: dict[str, str] = Field(default_factory=dict)
    failed_fragment_issues: dict[str, list[dict[str, Any]]] = Field(default_factory=dict)
    strategy_transitions: list[dict[str, Any]] = Field(default_factory=list)
    llm_call_count: int = 0
    completed: bool = False
    merged_patch_hash: str = ""
    provider_telemetry: list[dict[str, Any]] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# Strategy / admission
# ---------------------------------------------------------------------------

UniversesGenerationMode = Literal["auto", "monolithic", "fragmented"]

# Reactor-neutral thresholds (NOT hardcoded for VERA4 or any specific benchmark).
_DEFAULT_SAFE_FRAGMENT_TOKENS = 4000
_DEFAULT_MAX_MONOLITHIC_UNIVERSES = 6
_DEFAULT_LARGE_PATCH_SAFE_OUTPUT_RATIO = 0.6


def estimate_universes_output_size(
    *,
    universe_count: int,
    avg_cells_per_universe: int = 4,
) -> int:
    """Estimate output token size for a monolithic universes patch."""
    # Empirical: each universe with ~4 cells costs roughly 400-600 JSON tokens.
    # This is deliberately conservative.
    base_overhead = 100  # patch_type, outer braces, etc.
    per_universe = 350 + avg_cells_per_universe * 80
    return base_overhead + universe_count * per_universe


def should_fragment_universes(
    *,
    mode: UniversesGenerationMode,
    universe_count: int,
    provider_max_output_tokens: int | None = None,
    reasoning_enabled: bool = False,
    history_json_truncated: bool = False,
    history_context_exhausted: bool = False,
    history_monolithic_parse_failure: bool = False,
    avg_cells_per_universe: int = 4,
    safe_output_ratio: float = _DEFAULT_LARGE_PATCH_SAFE_OUTPUT_RATIO,
) -> tuple[bool, str]:
    """Decide whether to fragment universe generation.

    Returns (should_fragment, reason).
    """
    if mode == "fragmented":
        return True, "explicit_fragmented_mode"
    if mode == "monolithic":
        return False, "explicit_monolithic_mode"
    # auto mode.
    if history_json_truncated:
        return True, "history_json_truncated"
    if history_context_exhausted:
        return True, "history_context_exhausted"
    estimated = estimate_universes_output_size(universe_count=universe_count, avg_cells_per_universe=avg_cells_per_universe)
    if provider_max_output_tokens is not None:
        budget = int(provider_max_output_tokens * safe_output_ratio)
        if reasoning_enabled:
            budget = int(budget * 0.6)  # reasoning consumes part of the output budget
        if estimated > budget:
            return True, f"estimated_{estimated}_exceeds_budget_{budget}"
    if universe_count > _DEFAULT_MAX_MONOLITHIC_UNIVERSES:
        return True, f"universe_count_{universe_count}_exceeds_threshold_{_DEFAULT_MAX_MONOLITHIC_UNIVERSES}"
    if history_monolithic_parse_failure:
        return True, "history_monolithic_parse_failure"
    return False, "estimated_safe_for_monolithic"


# ---------------------------------------------------------------------------
# Merge
# ---------------------------------------------------------------------------


def merge_universe_fragments(
    *,
    manifest: UniverseManifest,
    fragments: list[UniverseDefinitionFragment],
    known_material_ids: set[str] | None = None,
) -> tuple[dict[str, Any] | None, list[str]]:
    """Deterministically merge fragments into a standard UniversesPatch.

    Returns (merged_patch_dict, error_codes).
    """
    errors: list[str] = []
    # Build fragment index by universe_id.
    frag_by_id: dict[str, UniverseDefinitionFragment] = {}
    for frag in fragments:
        uid = frag.universe_id
        if uid in frag_by_id:
            errors.append(f"merge.duplicate_fragment:{uid}")
            continue
        frag_by_id[uid] = frag
    # Check all manifest items are covered.
    merged_universes: list[dict[str, Any]] = []
    for item in manifest.generation_order:
        if item not in frag_by_id:
            errors.append(f"merge.missing_fragment:{item}")
            continue
        frag = frag_by_id[item]
        universe_data = frag.universe
        # Verify universe_id matches.
        if universe_data.get("universe_id") != item:
            errors.append(f"merge.universe_id_mismatch:{item}")
            continue
        # Verify material references.
        if known_material_ids is not None:
            for cell in universe_data.get("cells", []):
                mid = cell.get("material_id")
                if mid and mid not in known_material_ids:
                    errors.append(f"merge.unknown_material:{mid}")
        merged_universes.append(universe_data)
    # Check for extra undeclared fragments.
    manifest_ids = set(manifest.generation_order)
    extra = set(frag_by_id.keys()) - manifest_ids
    for eid in extra:
        errors.append(f"merge.extra_fragment:{eid}")
    if errors:
        return None, errors
    patch = {"patch_type": "universes", "universes": merged_universes}
    return patch, []


def validate_merged_patch(patch_dict: dict[str, Any], *, known_material_ids: set[str] | None = None, known_universe_ids: set[str] | None = None) -> tuple[bool, list[dict[str, Any]]]:
    """Validate merged patch using existing validators."""
    try:
        parsed = parse_patch_content("universes", patch_dict)
    except Exception as exc:
        return False, [{"code": "merge.schema_invalid", "severity": "error", "message": str(exc)}]
    ctx = PatchValidationContext(
        known_material_ids=list(known_material_ids) if known_material_ids else [],
        known_universe_ids=list(known_universe_ids) if known_universe_ids else [],
    )
    result = validate_patch(parsed, context=ctx)
    issues = [{"code": i.code, "severity": i.severity, "message": i.message} for i in result.issues]
    return result.ok, issues


# ---------------------------------------------------------------------------
# Token budget
# ---------------------------------------------------------------------------


def resolve_patch_output_budget(
    *,
    explicit: int | None = None,
    fragment_mode: bool = False,
    provider_max_output: int | None = None,
) -> int:
    """Resolve the output token budget for a patch or fragment call.

    Priority: explicit > fragment-specific > provider capability > conservative default.
    """
    if explicit is not None:
        return explicit
    if fragment_mode:
        return _DEFAULT_SAFE_FRAGMENT_TOKENS
    if provider_max_output is not None:
        return provider_max_output
    return 8000


__all__ = [
    "UniverseGenerationRequirement",
    "UniverseGenerationRequirementSet",
    "extract_universe_requirements",
    "UniverseManifestItem",
    "UniverseManifest",
    "validate_manifest",
    "build_manifest_from_requirements",
    "UniverseDefinitionFragment",
    "FragmentStatus",
    "LargePatchGenerationSession",
    "UniversesGenerationMode",
    "estimate_universes_output_size",
    "should_fragment_universes",
    "merge_universe_fragments",
    "validate_merged_patch",
    "resolve_patch_output_budget",
]
