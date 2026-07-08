"""Incremental executor + dependency-aware local retry router (Phase 5).

Reads a :class:`PlanBuildState`, generates patches one at a time in dependency
order (facts → materials → universes → pin_map → axial_layers → overlays →
settings → assembly), propagates context between patches, retries failures
locally, and finally assembles a complete SimulationPlan — all without
touching the graph workflow or OpenMC.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

from pydantic import Field

from openmc_agent.schemas import AgentBaseModel

from .assembler import assemble_simulation_plan_from_patches
from .patches import (
    AxialLayersPatch,
    AxialOverlaysPatch,
    FactsPatch,
    MaterialsPatch,
    PinMapPatch,
    SettingsPatch,
    UniversesPatch,
    parse_patch_content,
)
from .patch_generator import (
    PatchGenerationContext,
    generate_patch,
)
from .validators import validate_patch
from .reference_patches import (
    REFERENCE_PATCH_TYPES,
    build_reference_patch,
    load_benchmark_reference,
)
from .state import (
    EVENT_ASSEMBLY_COMPLETED,
    EVENT_ASSEMBLY_FAILED,
    EVENT_ASSEMBLY_STARTED,
    EVENT_PATCH_GENERATED,
    EVENT_PATCH_GENERATION_FAILED,
    PlanBuildState,
    PlanPatchEnvelope,
    add_validated_patch_to_state,
    assemble_state_if_ready,
)


# ---------------------------------------------------------------------------
# New event codes
# ---------------------------------------------------------------------------

EVENT_INCREMENTAL_EXECUTION_STARTED: str = "planning.incremental_execution_started"
EVENT_INCREMENTAL_EXECUTION_COMPLETED: str = "planning.incremental_execution_completed"
EVENT_INCREMENTAL_EXECUTION_FAILED: str = "planning.incremental_execution_failed"
EVENT_PATCH_SKIPPED_ALREADY_VALID: str = "planning.patch_skipped_already_valid"
EVENT_PATCH_DEPENDENCY_CONTEXT_BUILT: str = "planning.patch_dependency_context_built"
EVENT_PATCH_RETRY_ROUTED: str = "planning.patch_retry_routed"
EVENT_DETERMINISTIC_SETTINGS_CREATED: str = "planning.deterministic_settings_patch_created"
EVENT_INCREMENTAL_RESUME_STARTED: str = "planning.incremental_resume_started"
EVENT_INCREMENTAL_RESUME_COMPLETED: str = "planning.incremental_resume_completed"
EVENT_PATCH_SKIPPED_FROM_RESUME: str = "planning.patch_skipped_from_resume_state"
EVENT_REFERENCE_PATCH_LOADED: str = "reference_patch.loaded"
EVENT_REFERENCE_PATCH_GENERATED: str = "reference_patch.generated"
EVENT_REFERENCE_PATCH_FALLBACK: str = "reference_patch.fallback_after_llm_failure"
EVENT_REFERENCE_PATCH_VALIDATION_FAILED: str = "reference_patch.validation_failed"


# ---------------------------------------------------------------------------
# Result / issue models
# ---------------------------------------------------------------------------


class IncrementalExecutionIssue(AgentBaseModel):
    code: str
    severity: Literal["error", "warning", "info"] = "error"
    message: str
    patch_type: str | None = None
    patch_id: str | None = None
    path: str | None = None


class IncrementalExecutionResult(AgentBaseModel):
    ok: bool = False
    state: PlanBuildState
    assembled_plan: dict[str, Any] | None = None
    issues: list[IncrementalExecutionIssue] = Field(default_factory=list)
    summary: dict[str, Any] = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# Retry router
# ---------------------------------------------------------------------------


class RetryDecision(AgentBaseModel):
    action: Literal[
        "retry_same_patch",
        "retry_dependency_patch",
        "create_skeleton_patch",
        "fail",
    ]
    patch_type: str
    dependency_patch_type: str | None = None
    reason: str = ""


# Codes that signal an unresolved reference to a *different* patch type.
_REFERENCE_CODES: dict[str, str] = {
    "patch.axial_overlays.target_missing": "pin_map",
    "patch.axial_overlays.material_missing": "materials",
    "patch.pin_map.default_universe_missing": "universes",
    "assembly.unresolved_material_reference": "materials",
    "assembly.unresolved_universe_reference": "universes",
}


def route_retry(
    *,
    failed_patch_type: str,
    issues: list[dict[str, Any]],
    state: PlanBuildState,
) -> RetryDecision:
    """Decide what to do after a patch generation or validation failure."""
    error_codes = [i.get("code", "") for i in issues if i.get("severity") == "error"]
    if not error_codes:
        # Only warnings/info — treat as retry-same for completeness.
        return RetryDecision(
            action="retry_same_patch",
            patch_type=failed_patch_type,
            reason="non-error issues detected; retrying for completeness",
        )

    # Check for unresolved-reference codes that point to a dependency patch.
    for code in error_codes:
        dep = _REFERENCE_CODES.get(code)
        if dep is not None:
            dep_envelope = next(
                (e for e in state.patches.values()
                 if e.patch_type == dep and e.status == "valid"),
                None,
            )
            if dep_envelope is None:
                return RetryDecision(
                    action="retry_dependency_patch",
                    patch_type=failed_patch_type,
                    dependency_patch_type=dep,
                    reason=f"issue {code} references missing dependency patch {dep!r}",
                )
            # Dependency is valid but current patch still can't resolve it —
            # retry current patch with enriched context.
            return RetryDecision(
                action="retry_same_patch",
                patch_type=failed_patch_type,
                reason=f"issue {code}: dependency {dep!r} is valid; retry current patch",
            )

    # JSON parse / schema / local validation errors → retry same patch.
    local_prefixes = (
        "patch_generation.json_parse_error",
        "patch_generation.schema_error",
        "patch.pin_map.",
        "patch.axial_layers.",
        "patch.axial_overlays.",
        "patch.materials.",
        "patch.universes.",
        "patch.schema_invalid",
        "patch.duplicate_id",
    )
    if any(code.startswith(local_prefixes) for code in error_codes):
        return RetryDecision(
            action="retry_same_patch",
            patch_type=failed_patch_type,
            reason=f"local validation error(s): {error_codes[:3]}",
        )

    return RetryDecision(
        action="fail",
        patch_type=failed_patch_type,
        reason=f"unroutable error codes: {error_codes[:3]}",
    )


# ---------------------------------------------------------------------------
# Dependency graph
# ---------------------------------------------------------------------------

_DEFAULT_ORDER: tuple[str, ...] = (
    "facts",
    "materials",
    "universes",
    "pin_map",
    "axial_layers",
    "axial_overlays",
    "settings",
)

_DEPENDENCIES: dict[str, list[str]] = {
    "facts": [],
    "materials": ["facts"],
    "universes": ["facts", "materials"],
    "pin_map": ["facts", "universes"],
    "axial_layers": ["facts"],
    "axial_overlays": ["facts", "materials", "axial_layers"],
    "settings": [],
}


def default_patch_task_order(state: PlanBuildState) -> list[str]:
    """Return the default patch generation order based on state features."""
    order = list(_DEFAULT_ORDER)
    # Remove axial_overlays if spacer grids are not expected.
    has_spacer = _state_has_feature(state, "has_spacer_grid")
    if not has_spacer:
        order = [t for t in order if t != "axial_overlays"]
    # Remove pin_map if no special pin map.
    has_special = _state_has_feature(state, "has_special_pin_map")
    has_large = state.metadata.get("planning_mode_decision", {}).get(
        "feature_summary", {}
    ).get("large_lattice_dimension") is not None
    if not has_special and not has_large:
        order = [t for t in order if t != "pin_map"]
    return order


def required_patch_types_for_state(state: PlanBuildState) -> list[str]:
    """Return the minimal required patch types for this state."""
    required = ["facts", "materials", "universes", "axial_layers", "settings"]
    if _state_has_feature(state, "has_spacer_grid"):
        required.append("axial_overlays")
    if _state_has_feature(state, "has_special_pin_map"):
        required.append("pin_map")
    # Preserve canonical order.
    return [t for t in _DEFAULT_ORDER if t in required]


def _state_has_feature(state: PlanBuildState, feature: str) -> bool:
    pmd = state.metadata.get("planning_mode_decision", {})
    fs = pmd.get("feature_summary", {})
    if fs.get(feature):
        return True
    # Also check extracted_facts.
    return bool(state.extracted_facts.get(feature))


# ---------------------------------------------------------------------------
# Context propagation
# ---------------------------------------------------------------------------


def build_generation_context_from_state(
    state: PlanBuildState,
    patch_type: str,
    *,
    few_shot_case_ids: list[str] | None = None,
) -> PatchGenerationContext:
    """Build a :class:`PatchGenerationContext` enriched from all valid patches."""
    ctx = PatchGenerationContext(
        benchmark_id=state.benchmark_id,
        selected_variant=state.selected_variant,
        confirmed_facts=dict(state.confirmed_facts),
        extracted_facts=dict(state.extracted_facts),
        strict_benchmark=False,
        few_shot_case_ids=list(few_shot_case_ids or []),
    )

    known_material_ids: list[str] = []
    known_universe_ids: list[str] = []
    expected_counts: dict[str, int] = {}
    active_fuel: tuple[float, float] | None = None
    axial_domain: tuple[float, float] | None = None

    for env in state.patches.values():
        if env.status != "valid":
            continue
        ctx.validated_patch_summaries.setdefault(
            env.patch_type,
            {"status": "valid", "patch_id": env.patch_id},
        )
        content = env.content
        ptype = env.patch_type

        if ptype == "facts":
            ctx.benchmark_id = content.get("benchmark_id") or ctx.benchmark_id
            ctx.selected_variant = content.get("selected_variant") or ctx.selected_variant
            for key in (
                "expected_pin_count",
                "expected_guide_tube_count",
                "expected_instrument_tube_count",
                "expected_pyrex_count",
                "expected_thimble_plug_count",
                "expected_spacer_grid_count",
            ):
                val = content.get(key)
                if isinstance(val, int):
                    expected_counts[key] = val
            afr = content.get("active_fuel_region_cm")
            if isinstance(afr, list) and len(afr) == 2:
                active_fuel = (afr[0], afr[1])
            ad = content.get("axial_domain_cm")
            if isinstance(ad, list) and len(ad) == 2:
                axial_domain = (ad[0], ad[1])
            # Propagate feature flags.
            for flag in ("has_spacer_grids", "has_special_pin_map", "has_axial_geometry"):
                if content.get(flag):
                    ctx.extracted_facts[flag] = True
            ctx.strict_benchmark = bool(content.get("benchmark_id"))

        elif ptype == "materials":
            for mat in content.get("materials", []):
                mid = mat.get("material_id")
                if isinstance(mid, str):
                    known_material_ids.append(mid)

        elif ptype == "universes":
            for univ in content.get("universes", []):
                uid = univ.get("universe_id")
                if isinstance(uid, str):
                    known_universe_ids.append(uid)

        elif ptype == "pin_map":
            for group in (
                "guide_tube_coords",
                "instrument_tube_coords",
                "pyrex_rod_coords",
                "thimble_plug_coords",
            ):
                coords = content.get(group, [])
                if isinstance(coords, list):
                    label = group.replace("_coords", "_count")
                    expected_counts[f"expected_{label}"] = len(coords)

        elif ptype == "axial_layers":
            ad = content.get("axial_domain_cm")
            if isinstance(ad, list) and len(ad) == 2 and axial_domain is None:
                axial_domain = (ad[0], ad[1])
            for layer in content.get("layers", []):
                if layer.get("role") == "active_fuel":
                    z_min = layer.get("z_min_cm")
                    z_max = layer.get("z_max_cm")
                    if isinstance(z_min, (int, float)) and isinstance(z_max, (int, float)):
                        active_fuel = (z_min, z_max)

        elif ptype == "axial_overlays":
            for ov in content.get("overlays", []):
                tl = ov.get("target_lattice_id")
                if isinstance(tl, str) and tl not in ctx.known_lattice_ids:
                    ctx.known_lattice_ids.append(tl)

    ctx.expected_counts = expected_counts
    ctx.known_material_ids = list(dict.fromkeys(known_material_ids))
    ctx.known_universe_ids = list(dict.fromkeys(known_universe_ids))
    ctx.active_fuel_region_cm = active_fuel
    ctx.axial_domain_cm = axial_domain

    state.add_event(
        event_type=EVENT_PATCH_DEPENDENCY_CONTEXT_BUILT,
        message=f"context built for {patch_type} ({len(ctx.validated_patch_summaries)} valid patches)",
        data={
            "patch_type": patch_type,
            "known_material_count": len(ctx.known_material_ids),
            "known_universe_count": len(ctx.known_universe_ids),
            "expected_count_keys": list(ctx.expected_counts.keys()),
        },
    )
    return ctx


# ---------------------------------------------------------------------------
# Deterministic settings fallback
# ---------------------------------------------------------------------------


def build_deterministic_settings_patch(state: PlanBuildState) -> SettingsPatch:
    """Return a default SettingsPatch without calling the LLM."""
    return SettingsPatch(
        source_strategy="active_fuel_box",
        source_requires_fissionable_constraint=True,
        plot_strategy="full_assembly",
        cross_sections_runtime_required=True,
        tallies_required_for_smoke_test=False,
        assumptions=["cross sections resolved at runtime via OPENMC_CROSS_SECTIONS"],
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _has_valid_patch(state: PlanBuildState, patch_type: str) -> bool:
    return any(
        e.patch_type == patch_type and e.status == "valid"
        for e in state.patches.values()
    )


def _add_envelope(
    state: PlanBuildState,
    patch_type: str,
    content: dict[str, Any],
    source: str = "llm",
) -> PlanPatchEnvelope:
    import hashlib
    digest = hashlib.md5(
        str(content).encode("utf-8"), usedforsecurity=False
    ).hexdigest()[:8]
    envelope = PlanPatchEnvelope(
        patch_id=f"patch_{patch_type}_{digest}",
        patch_type=patch_type,
        content=content,
        source=source,  # type: ignore[arg-type]
        status="valid",
    )
    state.add_patch(envelope)
    return envelope


# ---------------------------------------------------------------------------
# Main executor
# ---------------------------------------------------------------------------


def run_incremental_planning(
    *,
    requirement: str,
    state: PlanBuildState,
    llm_client: Any,
    max_patch_attempts: int = 2,
    strict: bool = True,
    task_order: list[str] | None = None,
    reference_patch_policy: str = "fallback_after_llm_failure",
    reference_path: str | Path | None = None,
    few_shot_case_ids: list[str] | None = None,
) -> IncrementalExecutionResult:
    """Run the full incremental planning pipeline.

    Parameters
    ----------
    reference_patch_policy
        Controls when reference patches are used for structural patches:
        ``"off"`` (LLM only), ``"reference_only_for_structural"``
        (structural patches from reference, LLM for facts/materials/universes),
        ``"fallback_after_llm_failure"`` (try LLM, then reference),
        ``"prefer_reference_for_structural"`` (same as reference_only).
    reference_path
        Explicit path to reference file.  If None, tries benchmark lookup.
    """
    issues: list[IncrementalExecutionIssue] = []
    reference_data: dict[str, Any] | None = None
    reference_patches_used: list[str] = []

    # Note: reference loading is deferred until after facts patch is generated,
    # so benchmark_id can be extracted from FactsPatch content (LLM output).
    # This keeps the system benchmark-agnostic — no hardcoded text matching.

    state.add_event(
        event_type=EVENT_INCREMENTAL_EXECUTION_STARTED,
        message="incremental planning execution started",
        data={
            "max_patch_attempts": max_patch_attempts,
            "reference_patch_policy": reference_patch_policy,
            "reference_available": reference_data is not None,
        },
    )

    order = task_order or default_patch_task_order(state)
    required = required_patch_types_for_state(state)

    def _sync_benchmark_from_facts() -> None:
        """Extract benchmark_id/variant from the valid FactsPatch content.

        This is benchmark-agnostic: the identification comes from the
        LLM-generated FactsPatch (which extracts it from the requirement
        text), NOT from hardcoded text matching in production code.
        """
        facts_env = next(
            (e for e in state.patches.values()
             if e.patch_type == "facts" and e.status == "valid"),
            None,
        )
        if facts_env is None:
            return
        content = facts_env.content
        bid = content.get("benchmark_id")
        var = content.get("selected_variant")
        if bid and not state.benchmark_id:
            state.benchmark_id = bid
        if var and not state.selected_variant:
            state.selected_variant = var

    def _build_failure_summary(pt: str, error_codes: list[str], attempt_count: int) -> dict[str, Any]:
        valid_types = sorted({
            e.patch_type for e in state.patches.values() if e.status == "valid"
        })
        invalid_types = sorted({
            e.patch_type for e in state.patches.values()
            if e.status != "valid"
        })
        if pt not in invalid_types:
            invalid_types = sorted(set(invalid_types) | {pt})
        return {
            "failed_patch_type": pt,
            "failed_stage": "patch_generation",
            "attempt_count": attempt_count,
            "issue_codes": error_codes,
            "valid_patch_types": valid_types,
            "invalid_patch_types": invalid_types,
            "next_recommended_action": "resume_from_failed_patch",
            "monolithic_fallback_attempted": False,
            "reference_patches_used": reference_patches_used,
        }

    for patch_type in order:
        # Skip if already valid.
        if _has_valid_patch(state, patch_type):
            state.add_event(
                event_type=EVENT_PATCH_SKIPPED_ALREADY_VALID,
                message=f"{patch_type} already valid, skipping",
                data={"patch_type": patch_type},
            )
            continue

        # Deterministic settings fallback.
        if patch_type == "settings":
            settings_patch = build_deterministic_settings_patch(state)
            content = settings_patch.model_dump(mode="json")
            _add_envelope(state, "settings", content, source="deterministic")
            state.add_event(
                event_type=EVENT_DETERMINISTIC_SETTINGS_CREATED,
                message="deterministic settings patch created",
                data={"source_strategy": settings_patch.source_strategy},
            )
            continue

        # Reference-only for structural patches.
        is_structural = patch_type in REFERENCE_PATCH_TYPES

        # Lazy-load reference after facts patch has set benchmark_id.
        if (
            is_structural
            and reference_data is None
            and reference_patch_policy != "off"
            and state.benchmark_id is not None
        ):
            reference_data = load_benchmark_reference(
                benchmark_id=state.benchmark_id,
                variant=state.selected_variant,
                reference_path=reference_path,
                # Don't pass llm_client here — it would consume patch
                # generation responses. LLM matching should be done
                # separately if needed, not in the patch loop.
            )
            if reference_data is not None:
                state.add_event(
                    event_type=EVENT_REFERENCE_PATCH_LOADED,
                    message=f"benchmark reference loaded for {state.benchmark_id}/{state.selected_variant}",
                    data={"policy": reference_patch_policy},
                )

        use_reference_first = (
            is_structural
            and reference_data is not None
            and reference_patch_policy in (
                "reference_only_for_structural",
                "prefer_reference_for_structural",
            )
        )

        if use_reference_first:
            ref_patch = build_reference_patch(
                patch_type=patch_type,
                reference=reference_data,
                variant=state.selected_variant,
            )
            if ref_patch is not None:
                val_result = validate_patch(ref_patch)
                if val_result.ok:
                    content = ref_patch.model_dump(mode="json")
                    _add_envelope(state, patch_type, content, source="fixture")
                    reference_patches_used.append(patch_type)
                    state.add_event(
                        event_type=EVENT_REFERENCE_PATCH_GENERATED,
                        message=f"{patch_type} patch from reference (valid)",
                        data={"patch_type": patch_type},
                    )
                    continue
                else:
                    state.add_event(
                        event_type=EVENT_REFERENCE_PATCH_VALIDATION_FAILED,
                        message=f"{patch_type} reference patch failed validation",
                        data={
                            "patch_type": patch_type,
                            "issue_codes": [i.code for i in val_result.issues if i.severity == "error"],
                        },
                    )
            # Reference not available or failed → fall through to LLM.

        # Build context from valid patches.
        ctx = build_generation_context_from_state(
            state, patch_type, few_shot_case_ids=few_shot_case_ids
        )

        # Generate patch with retry.
        result = generate_patch(
            patch_type=patch_type,
            requirement=requirement,
            state=state,
            context=ctx,
            llm_client=llm_client,
            max_attempts=max_patch_attempts,
        )

        if result.ok and result.envelope is not None:
            state.add_patch(result.envelope)
            # Phase 7D: extract benchmark_id from FactsPatch for reference loading.
            _sync_benchmark_from_facts()
            state.add_event(
                event_type=EVENT_PATCH_GENERATED,
                message=f"{patch_type} generated and validated",
                data={
                    "patch_id": result.envelope.patch_id,
                    "attempts": len(result.attempts),
                },
            )
        else:
            # Try reference fallback if policy allows.
            error_codes = [
                i.get("code", "") for i in result.issues
                if i.get("severity") == "error"
            ]

            # Lazy-load reference for fallback (benchmark_id may have been
            # extracted from FactsPatch after initial load attempt).
            if (
                is_structural
                and reference_data is None
                and reference_patch_policy == "fallback_after_llm_failure"
                and state.benchmark_id is not None
            ):
                reference_data = load_benchmark_reference(
                    benchmark_id=state.benchmark_id,
                    variant=state.selected_variant,
                    reference_path=reference_path,
                )

            if (
                is_structural
                and reference_data is not None
                and reference_patch_policy == "fallback_after_llm_failure"
            ):
                ref_patch = build_reference_patch(
                    patch_type=patch_type,
                    reference=reference_data,
                    variant=state.selected_variant,
                )
                if ref_patch is not None:
                    val_result = validate_patch(ref_patch)
                    if val_result.ok:
                        content = ref_patch.model_dump(mode="json")
                        _add_envelope(state, patch_type, content, source="fixture")
                        reference_patches_used.append(patch_type)
                        state.add_event(
                            event_type=EVENT_REFERENCE_PATCH_FALLBACK,
                            message=f"{patch_type} reference fallback after LLM failure",
                            data={"patch_type": patch_type, "llm_error_codes": error_codes},
                        )
                        continue

            # All retries exhausted.
            attempt_count = len(result.attempts)
            decision = route_retry(
                failed_patch_type=patch_type,
                issues=result.issues,
                state=state,
            )
            state.add_event(
                event_type=EVENT_PATCH_RETRY_ROUTED,
                message=f"{patch_type} retry routed: {decision.action}",
                data={
                    "patch_type": patch_type,
                    "action": decision.action,
                    "reason": decision.reason,
                    "error_codes": error_codes,
                },
            )

            issues.append(IncrementalExecutionIssue(
                code="incremental.patch_generation_failed",
                severity="error",
                message=f"{patch_type} generation failed: {error_codes}",
                patch_type=patch_type,
            ))
            state.add_event(
                event_type=EVENT_INCREMENTAL_EXECUTION_FAILED,
                message=f"execution stopped: {patch_type} generation failed",
                data={"failed_patch_type": patch_type, "error_codes": error_codes},
            )
            return IncrementalExecutionResult(
                ok=False,
                state=state,
                issues=issues,
                summary=_build_failure_summary(patch_type, error_codes, attempt_count),
            )

    # Check required patches.
    missing = [pt for pt in required if not _has_valid_patch(state, pt)]
    if missing:
        for pt in missing:
            issues.append(IncrementalExecutionIssue(
                code="assembly.missing_patch",
                severity="error",
                message=f"required {pt} patch is missing",
                patch_type=pt,
            ))
        state.add_event(
            event_type=EVENT_INCREMENTAL_EXECUTION_FAILED,
            message=f"missing required patches: {missing}",
            data={"missing": missing},
        )
        return IncrementalExecutionResult(
            ok=False,
            state=state,
            issues=issues,
            summary={"missing_patches": missing},
        )

    # Assemble.
    state = assemble_state_if_ready(state, strict=strict)
    if state.assembled_plan is not None:
        state.add_event(
            event_type=EVENT_INCREMENTAL_EXECUTION_COMPLETED,
            message="incremental planning completed, plan assembled",
            data={
                "patch_count": len(state.patches),
                "valid_patch_count": len(state.get_valid_patches()),
            },
        )
        return IncrementalExecutionResult(
            ok=True,
            state=state,
            assembled_plan=state.assembled_plan,
            issues=issues,
            summary={
                "valid_patch_count": len(state.get_valid_patches()),
                "assembled": True,
                "reference_patches_used": reference_patches_used,
                "valid_patch_types": sorted({
                    e.patch_type for e in state.patches.values() if e.status == "valid"
                }),
            },
        )
    else:
        issues.append(IncrementalExecutionIssue(
            code="incremental.assembly_failed",
            severity="error",
            message="assembly failed after all patches generated",
        ))
        state.add_event(
            event_type=EVENT_INCREMENTAL_EXECUTION_FAILED,
            message="assembly failed",
            data={},
        )
        return IncrementalExecutionResult(
            ok=False,
            state=state,
            issues=issues,
            summary={"assembled": False},
        )


__all__ = [
    "IncrementalExecutionIssue",
    "IncrementalExecutionResult",
    "RetryDecision",
    "run_incremental_planning",
    "route_retry",
    "default_patch_task_order",
    "required_patch_types_for_state",
    "build_generation_context_from_state",
    "build_deterministic_settings_patch",
    "EVENT_INCREMENTAL_EXECUTION_STARTED",
    "EVENT_INCREMENTAL_EXECUTION_COMPLETED",
    "EVENT_INCREMENTAL_EXECUTION_FAILED",
    "EVENT_INCREMENTAL_RESUME_STARTED",
    "EVENT_INCREMENTAL_RESUME_COMPLETED",
    "EVENT_PATCH_SKIPPED_ALREADY_VALID",
    "EVENT_PATCH_SKIPPED_FROM_RESUME",
    "EVENT_PATCH_DEPENDENCY_CONTEXT_BUILT",
    "EVENT_PATCH_RETRY_ROUTED",
    "EVENT_DETERMINISTIC_SETTINGS_CREATED",
    "EVENT_REFERENCE_PATCH_LOADED",
    "EVENT_REFERENCE_PATCH_GENERATED",
    "EVENT_REFERENCE_PATCH_FALLBACK",
    "EVENT_REFERENCE_PATCH_VALIDATION_FAILED",
]
