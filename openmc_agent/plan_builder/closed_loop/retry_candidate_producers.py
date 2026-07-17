"""Owner-specific candidate producer registry.

Phase 3A had a single nested closure inside the executor that produced every
owner candidate through the same vanilla patch-generation prompt.  Phase 3B
introduces a registry where each owner (facts / materials / universes /
placement / task-plan) has a dedicated producer.  Producers receive a typed
:class:`RetryCandidateContext` with required IDs, protected invariants, prior
failure codes and retry-aware prompt text so the LLM never generates a
free-form full-plan when a targeted revision was requested.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from openmc_agent.plan_builder.state import PlanBuildState

from .models import PlanClosedLoopPolicy
from .retry_models import ExecutablePlanRetryRequest, RetryExecutionPlan


@dataclass
class RetryCandidateContext:
    """All deterministic inputs a producer needs to generate an owner patch."""

    request: ExecutablePlanRetryRequest
    execution_plan: RetryExecutionPlan
    clone_state: PlanBuildState
    policy: PlanClosedLoopPolicy
    requirement: str = ""
    proposer_client: Any = None
    reviewer_client: Any = None
    repair_client: Any = None
    few_shot_case_ids: list[str] = field(default_factory=list)
    patch_output_mode: str | None = None
    patch_max_tokens: int | None = None
    patch_reasoning_effort: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass
class RetryCandidateResult:
    """Output of a producer invocation."""

    candidates: dict[str, dict[str, Any]]
    llm_calls: int = 0
    producer: str = ""
    prompt_paths: dict[str, str] = field(default_factory=dict)
    raw_paths: dict[str, str] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)


ProducerFn = Callable[[RetryCandidateContext], RetryCandidateResult]


def _valid_envelope(state: PlanBuildState, patch_type: str):
    matches = [item for item in state.patches.values() if item.patch_type == patch_type and item.status == "valid"]
    return matches[0] if len(matches) == 1 else None


def _build_retry_prompt_suffix(request: ExecutablePlanRetryRequest) -> str:
    """Construct the typed retry-aware prompt suffix shared by every producer."""
    required_ids = sorted({item for target in request.targets for item in target.required_ids})
    required_props = sorted({item for target in request.targets for item in target.required_properties})
    affected = sorted({item for target in request.targets for item in target.affected_json_paths})
    protected = sorted({item for target in request.targets for item in target.protected_json_paths})
    lines = [
        "=== Executable retry context ===",
        f"Retry request: {request.request_id} (origin={request.origin.value})",
        f"Reason code: {request.reason_code}",
        f"Source issue codes: {', '.join(request.source_issue_codes)}",
        f"Owner patch type(s): {', '.join(request.owner_patch_types)}",
        f"Required IDs: {', '.join(required_ids) or '(none)'}",
        f"Required properties: {', '.join(required_props) or '(none)'}",
        f"Affected JSON paths: {', '.join(affected) or '(none)'}",
        f"Protected invariants: {', '.join(protected) or '(none)'}",
    ]
    lines.extend(
        [
            "Constraints:",
            "- Generate ONLY the requested owner patch; do NOT generate a full plan.",
            "- Include every required ID exactly as listed; do NOT substitute similar IDs.",
            "- Preserve every protected invariant and every already-valid unrelated object.",
            "- Do NOT modify patches outside the declared owner patch type(s).",
        ]
    )
    if request.metadata.get("prior_failure_codes"):
        lines.append(f"- Prior failure codes: {', '.join(request.metadata['prior_failure_codes'])}")
    return "\n".join(lines)


def _materialize_via_patch_generator(
    ctx: RetryCandidateContext,
    owner_patch_type: str,
    *,
    generate_patch_fn: Callable[..., Any],
    build_context_fn: Callable[..., Any],
) -> tuple[dict[str, Any], int]:
    """Run the existing patch generator with a retry-aware requirement."""
    suffix = _build_retry_prompt_suffix(ctx.request)
    enriched_requirement = f"{ctx.requirement}\n\n{suffix}"
    from openmc_agent.plan_builder.patch_generator import RetryPatchGenerationContext

    base_context = build_context_fn(ctx.clone_state, owner_patch_type, few_shot_case_ids=ctx.few_shot_case_ids)
    retry_context = RetryPatchGenerationContext(
        base_context=base_context,
        retry_request_id=ctx.request.request_id,
        reason_code=ctx.request.reason_code,
        source_issue_codes=ctx.request.source_issue_codes,
        required_ids=sorted({item for target in ctx.request.targets for item in target.required_ids}),
        required_properties=sorted({item for target in ctx.request.targets for item in target.required_properties}),
        affected_json_paths=sorted({item for target in ctx.request.targets for item in target.affected_json_paths}),
        protected_invariants=sorted({item for target in ctx.request.targets for item in target.protected_json_paths}),
        prior_candidate_hashes=ctx.extra.get("prior_candidate_hashes", []),
        prior_failure_codes=ctx.extra.get("prior_failure_codes", []),
    )
    generated = generate_patch_fn(
        patch_type=owner_patch_type,
        requirement=enriched_requirement,
        state=ctx.clone_state,
        context=retry_context,
        llm_client=ctx.proposer_client,
        max_attempts=ctx.policy.max_attempts_per_retry_request,
    )
    if not generated.ok or generated.parsed_patch is None:
        raise ValueError(f"retry owner generation failed: {owner_patch_type}")
    return generated.parsed_patch, len(generated.attempts)


def make_facts_producer(
    *,
    generate_patch_fn: Callable[..., Any],
    build_context_fn: Callable[..., Any],
    facts_revision_fn: Callable[..., Any] | None = None,
) -> ProducerFn:
    """Facts owner producer.

    Prefers the deterministic Facts revision path when the request is
    expressible as RFC6902 operations; falls back to full FactsPatch
    regeneration with the retry-aware prompt suffix.
    """

    def _produce(ctx: RetryCandidateContext) -> RetryCandidateResult:
        llm_calls = 0
        # Try targeted revision first if a revision function and repair client
        # are available.
        if facts_revision_fn is not None and ctx.repair_client is not None:
            try:
                revision_outcome = facts_revision_fn(ctx)
                if revision_outcome is not None:
                    return RetryCandidateResult(
                        candidates=revision_outcome.get("candidates", {}),
                        llm_calls=revision_outcome.get("llm_calls", 1),
                        producer="facts_revision",
                        metadata=revision_outcome.get("metadata", {}),
                    )
            except Exception:
                pass
            llm_calls += 1
        # Fall back to full regeneration with retry-aware prompt.
        candidates: dict[str, dict[str, Any]] = {}
        for owner in ctx.request.owner_patch_types:
            if owner == "planning_task_plan":
                continue
            patch, calls = _materialize_via_patch_generator(ctx, owner, generate_patch_fn=generate_patch_fn, build_context_fn=build_context_fn)
            candidates[owner] = patch
            llm_calls += calls
        return RetryCandidateResult(candidates=candidates, llm_calls=llm_calls, producer="facts_regeneration")

    return _produce


def make_materials_producer(
    *,
    generate_patch_fn: Callable[..., Any],
    build_context_fn: Callable[..., Any],
    targeted_density_fn: Callable[[RetryCandidateContext], dict[str, Any]] | None = None,
) -> ProducerFn:
    """Materials owner producer.

    For density issues, prefers a targeted revision that only touches
    ``density_g_cm3`` / ``density_status`` / ``density_source`` /
    ``warnings`` / ``assumptions`` on the target material.  Other material
    species problems fall back to full regeneration preserving fuel-variant
    identity.
    """

    def _produce(ctx: RetryCandidateContext) -> RetryCandidateResult:
        llm_calls = 0
        if ctx.request.reason_code == "materials.execution_density_required" and targeted_density_fn is not None:
            try:
                outcome = targeted_density_fn(ctx)
                if outcome.get("candidates"):
                    return RetryCandidateResult(
                        candidates=outcome["candidates"],
                        llm_calls=outcome.get("llm_calls", 1),
                        producer="materials_targeted_density",
                        metadata=outcome.get("metadata", {}),
                    )
            except Exception:
                pass
            llm_calls += 1
        candidates: dict[str, dict[str, Any]] = {}
        for owner in ctx.request.owner_patch_types:
            patch, calls = _materialize_via_patch_generator(ctx, owner, generate_patch_fn=generate_patch_fn, build_context_fn=build_context_fn)
            candidates[owner] = patch
            llm_calls += calls
        return RetryCandidateResult(candidates=candidates, llm_calls=llm_calls, producer="materials_regeneration")

    return _produce


def make_universes_producer(
    *,
    generate_patch_fn: Callable[..., Any],
    build_context_fn: Callable[..., Any],
) -> ProducerFn:
    """Universes owner producer.

    Always regenerates the full UniversesPatch but the retry-aware prompt
    carries the exact required IDs, fuel-variant requirements and through-path
    requirements.  Acceptance rejects near-miss ID substitutions.
    """

    def _produce(ctx: RetryCandidateContext) -> RetryCandidateResult:
        candidates: dict[str, dict[str, Any]] = {}
        llm_calls = 0
        for owner in ctx.request.owner_patch_types:
            patch, calls = _materialize_via_patch_generator(ctx, owner, generate_patch_fn=generate_patch_fn, build_context_fn=build_context_fn)
            candidates[owner] = patch
            llm_calls += calls
        return RetryCandidateResult(candidates=candidates, llm_calls=llm_calls, producer="universes_regeneration")

    return _produce


def make_placement_owner_producer(
    *,
    generate_patch_fn: Callable[..., Any],
    build_context_fn: Callable[..., Any],
) -> ProducerFn:
    """Placement-owned (pin_map / assembly_catalog / core_layout / profiles) producer.

    Reuses the existing patch generator with the retry-aware prompt suffix.
    The Placement revision (RFC6902) path remains available for issue-scoped
    edits via the placement_revision module; this producer is the regeneration
    fallback for cases that cannot be expressed as JSON Patch operations.
    """

    def _produce(ctx: RetryCandidateContext) -> RetryCandidateResult:
        candidates: dict[str, dict[str, Any]] = {}
        llm_calls = 0
        for owner in ctx.request.owner_patch_types:
            patch, calls = _materialize_via_patch_generator(ctx, owner, generate_patch_fn=generate_patch_fn, build_context_fn=build_context_fn)
            candidates[owner] = patch
            llm_calls += calls
        return RetryCandidateResult(candidates=candidates, llm_calls=llm_calls, producer="placement_owner_regeneration")

    return _produce


def make_task_plan_producer() -> ProducerFn:
    """Deterministic task-plan recompute producer (no LLM)."""

    def _produce(ctx: RetryCandidateContext) -> RetryCandidateResult:
        from openmc_agent.plan_builder.planning_scope import build_canonical_task_plan

        if ctx.clone_state.resolved_planning_scope is None or ctx.clone_state.planning_feature_contract is None:
            raise ValueError("task-plan producer requires resolved scope and feature contract")
        facts_env = _valid_envelope(ctx.clone_state, "facts")
        new_plan = build_canonical_task_plan(
            scope=ctx.clone_state.resolved_planning_scope,
            contract=ctx.clone_state.planning_feature_contract,
            facts_patch=facts_env.content if facts_env else {},
            feature_order=list(__import__("openmc_agent.plan_builder.dependency_graph", fromlist=["DEFAULT_PLAN_PATCH_DEPENDENCY_GRAPH"]).DEFAULT_PLAN_PATCH_DEPENDENCY_GRAPH._ORDER),
        )
        ctx.clone_state.metadata["acceptance_candidate_task_plan_hash"] = new_plan.plan_hash
        return RetryCandidateResult(candidates={}, llm_calls=0, producer="task_plan_recompute", metadata={"candidate_task_plan_hash": new_plan.plan_hash})

    return _produce


def make_axial_producer(
    *,
    generate_patch_fn: Callable[..., Any],
    build_context_fn: Callable[..., Any],
) -> ProducerFn:
    def _produce(ctx: RetryCandidateContext) -> RetryCandidateResult:
        candidates: dict[str, dict[str, Any]] = {}
        llm_calls = 0
        for owner in ctx.request.owner_patch_types:
            patch, calls = _materialize_via_patch_generator(ctx, owner, generate_patch_fn=generate_patch_fn, build_context_fn=build_context_fn)
            candidates[owner] = patch
            llm_calls += calls
        return RetryCandidateResult(candidates=candidates, llm_calls=llm_calls, producer="axial_regeneration")

    return _produce


class RetryCandidateProducerRegistry:
    """Maps owner patch types to dedicated producers."""

    def __init__(self) -> None:
        self._producers: dict[str, ProducerFn] = {}

    def register(self, owner_patch_type: str, producer: ProducerFn) -> None:
        self._producers[owner_patch_type] = producer

    def register_facts(self, **kwargs: Any) -> None:
        self.register("facts", make_facts_producer(**kwargs))

    def register_materials(self, **kwargs: Any) -> None:
        self.register("materials", make_materials_producer(**kwargs))

    def register_universes(self, **kwargs: Any) -> None:
        self.register("universes", make_universes_producer(**kwargs))

    def register_placement_owner(self, **kwargs: Any) -> None:
        for owner in ("localized_insert_profiles", "pin_map", "assembly_catalog", "core_layout"):
            self.register(owner, make_placement_owner_producer(**kwargs))

    def register_task_plan(self) -> None:
        self.register("planning_task_plan", make_task_plan_producer())

    def register_axial(self, **kwargs: Any) -> None:
        for owner in ("axial_layers", "axial_overlays", "base_path_axial_profiles"):
            self.register(owner, make_axial_producer(**kwargs))

    def produce(self, ctx: RetryCandidateContext) -> RetryCandidateResult:
        owner_types = [item for item in ctx.request.owner_patch_types]
        if not owner_types:
            raise ValueError("retry request has no owner patch types")
        # Pick the producer for the first owner.  If multiple owners are
        # declared, each must be registered; we run them sequentially.
        result_candidates: dict[str, dict[str, Any]] = {}
        total_calls = 0
        producer_names: list[str] = []
        for owner in owner_types:
            producer = self._producers.get(owner)
            if producer is None:
                raise ValueError(f"no producer registered for owner {owner}")
            sub_ctx = RetryCandidateContext(
                request=ctx.request,
                execution_plan=ctx.execution_plan,
                clone_state=ctx.clone_state,
                policy=ctx.policy,
                requirement=ctx.requirement,
                proposer_client=ctx.proposer_client,
                reviewer_client=ctx.reviewer_client,
                repair_client=ctx.repair_client,
                few_shot_case_ids=ctx.few_shot_case_ids,
                patch_output_mode=ctx.patch_output_mode,
                patch_max_tokens=ctx.patch_max_tokens,
                patch_reasoning_effort=ctx.patch_reasoning_effort,
                extra=ctx.extra,
            )
            # Override the request's owner_patch_types so the sub-producer
            # only generates one patch.
            sub_ctx.request = ctx.request.model_copy(update={"owner_patch_types": [owner]})
            sub_result = producer(sub_ctx)
            result_candidates.update(sub_result.candidates)
            total_calls += sub_result.llm_calls
            producer_names.append(sub_result.producer)
        return RetryCandidateResult(
            candidates=result_candidates,
            llm_calls=total_calls,
            producer="+".join(producer_names),
            metadata={"owner_count": len(owner_types)},
        )


def default_producer_registry(
    *,
    generate_patch_fn: Callable[..., Any],
    build_context_fn: Callable[..., Any],
    facts_revision_fn: Callable[..., Any] | None = None,
    targeted_density_fn: Callable[[RetryCandidateContext], dict[str, Any]] | None = None,
) -> RetryCandidateProducerRegistry:
    registry = RetryCandidateProducerRegistry()
    registry.register_facts(generate_patch_fn=generate_patch_fn, build_context_fn=build_context_fn, facts_revision_fn=facts_revision_fn)
    registry.register_materials(generate_patch_fn=generate_patch_fn, build_context_fn=build_context_fn, targeted_density_fn=targeted_density_fn)
    registry.register_universes(generate_patch_fn=generate_patch_fn, build_context_fn=build_context_fn)
    registry.register_placement_owner(generate_patch_fn=generate_patch_fn, build_context_fn=build_context_fn)
    registry.register_task_plan()
    registry.register_axial(generate_patch_fn=generate_patch_fn, build_context_fn=build_context_fn)
    return registry


__all__ = [
    "RetryCandidateContext",
    "RetryCandidateResult",
    "RetryCandidateProducerRegistry",
    "default_producer_registry",
    "make_facts_producer",
    "make_materials_producer",
    "make_universes_producer",
    "make_placement_owner_producer",
    "make_task_plan_producer",
    "make_axial_producer",
]
