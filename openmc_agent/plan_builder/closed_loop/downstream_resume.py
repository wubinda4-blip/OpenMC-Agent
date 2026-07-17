"""Downstream resume helper for Phase-3B executable retry.

The retry controller commits an owner patch and invalidates its dependents,
but it does not actually regenerate them.  This module provides a bounded
``resume_incremental_from_patch`` helper that re-enters the incremental
executor starting from the earliest invalidated patch type, skipping every
patch that is still valid.

The helper is deliberately *not* recursive: it uses an explicit resume depth
counter stored on ``state.metadata`` to prevent infinite re-entry.
"""

from __future__ import annotations

from typing import Any

from pydantic import Field

from openmc_agent.plan_builder.dependency_graph import DEFAULT_PLAN_PATCH_DEPENDENCY_GRAPH, PlanPatchDependencyGraph
from openmc_agent.plan_builder.state import PlanBuildState
from openmc_agent.schemas import AgentBaseModel

from .retry_models import RetryExecutionPlan


class DownstreamResumeResult(AgentBaseModel):
    ok: bool = False
    earliest_resume_patch_type: str | None = None
    skipped_valid_patch_types: list[str] = Field(default_factory=list)
    regenerated_patch_types: list[str] = Field(default_factory=list)
    failure_location: str | None = None
    detail: str = ""
    resume_depth: int = 0


def resume_incremental_from_patch(
    *,
    state: PlanBuildState,
    earliest_patch_type: str | None,
    canonical_task_plan: Any | None = None,
    run_incremental_fn: Any = None,
    requirement: str = "",
    llm_client: Any = None,
    max_patch_attempts: int = 2,
    strict: bool = True,
    task_order: list[str] | None = None,
    reference_patch_policy: str = "off",
    reference_path: str | None = None,
    few_shot_case_ids: list[str] | None = None,
    material_policy: Any = None,
    plan_loop_policy: Any = None,
    plan_loop_output_dir: str | None = None,
    plan_reviewer_client: Any = None,
    plan_repair_client: Any = None,
    dependency_graph: PlanPatchDependencyGraph = DEFAULT_PLAN_PATCH_DEPENDENCY_GRAPH,
    max_depth: int = 6,
) -> DownstreamResumeResult:
    """Resume incremental planning from the earliest invalidated patch.

    This is a non-recursive wrapper around ``run_incremental_planning`` that:
      - skips patches that are still valid (owner commit preserved them),
      - starts generation from the earliest invalidated patch type,
      - uses an explicit depth counter to prevent unbounded re-entry,
      - never clears ``PlanBuildState`` or modifies the original requirement,
      - never enters monolithic fallback or ``reflect_plan``.

    The caller is expected to pass the executor's ``run_incremental_planning``
    as ``run_incremental_fn`` so this module stays provider-neutral.
    """
    if earliest_patch_type is None:
        return DownstreamResumeResult(ok=True, detail="no invalidated patches to resume")
    depth = int(state.metadata.get("phase3_retry_resume_depth", 0))
    if depth >= max_depth:
        return DownstreamResumeResult(ok=False, failure_location="depth_guard", detail=f"resume depth exhausted ({depth}/{max_depth})", resume_depth=depth)
    state.metadata["phase3_retry_resume_depth"] = depth + 1
    try:
        valid_types = {item.patch_type for item in state.patches.values() if item.status == "valid"}
        ordered = dependency_graph.topological_order(list(valid_types | {earliest_patch_type}))
        skipped = [item for item in ordered if item in valid_types and item != earliest_patch_type]
        if task_order is None:
            # Build a task order that starts from the earliest invalidated
            # patch and continues through every dependent.
            full_order = list(dependency_graph._ORDER)
            start_index = full_order.index(earliest_patch_type) if earliest_patch_type in full_order else 0
            resume_order = [item for item in full_order[start_index:] if item not in skipped]
        else:
            resume_order = list(task_order)
        if run_incremental_fn is None:
            return DownstreamResumeResult(ok=False, failure_location="no_runner", detail="run_incremental_fn not provided", resume_depth=depth, earliest_resume_patch_type=earliest_patch_type, skipped_valid_patch_types=skipped)
        result = run_incremental_fn(
            requirement=requirement or state.requirement_text,
            state=state,
            llm_client=llm_client,
            max_patch_attempts=max_patch_attempts,
            strict=strict,
            task_order=resume_order,
            reference_patch_policy=reference_patch_policy,
            reference_path=reference_path,
            few_shot_case_ids=few_shot_case_ids or [],
            material_policy=material_policy,
            plan_loop_policy=plan_loop_policy,
            plan_loop_output_dir=plan_loop_output_dir,
            plan_reviewer_client=plan_reviewer_client,
            plan_repair_client=plan_repair_client,
        )
        state.metadata["phase3_retry_resume_depth"] = depth
        regenerated = [item.patch_type for item in state.patches.values() if item.status == "valid" and item.patch_type not in valid_types]
        if getattr(result, "ok", False):
            return DownstreamResumeResult(ok=True, earliest_resume_patch_type=earliest_patch_type, skipped_valid_patch_types=skipped, regenerated_patch_types=regenerated, resume_depth=depth, detail="downstream resume completed")
        failure_code = ", ".join(issue.code for issue in getattr(result, "issues", []) if getattr(issue, "severity", None) == "error")
        return DownstreamResumeResult(ok=False, earliest_resume_patch_type=earliest_patch_type, skipped_valid_patch_types=skipped, regenerated_patch_types=regenerated, failure_location="incremental_executor", detail=failure_code or "incremental executor returned not-ok", resume_depth=depth)
    except Exception as exc:
        state.metadata["phase3_retry_resume_depth"] = depth
        return DownstreamResumeResult(ok=False, failure_location="exception", detail=str(exc), resume_depth=depth)


def make_downstream_resumer(
    *,
    requirement: str,
    llm_client: Any,
    run_incremental_fn: Any,
    max_patch_attempts: int = 2,
    strict: bool = True,
    reference_patch_policy: str = "off",
    reference_path: str | None = None,
    few_shot_case_ids: list[str] | None = None,
    material_policy: Any = None,
    plan_loop_policy: Any = None,
    plan_loop_output_dir: str | None = None,
    plan_reviewer_client: Any = None,
    plan_repair_client: Any = None,
    max_depth: int = 6,
) -> Any:
    """Return a closure compatible with ``execute_plan_retry_loop(downstream_resumer=...)``."""

    def _resumer(state: PlanBuildState, plan: RetryExecutionPlan) -> list[str]:
        result = resume_incremental_from_patch(
            state=state,
            earliest_patch_type=plan.earliest_resume_patch_type,
            run_incremental_fn=run_incremental_fn,
            requirement=requirement,
            llm_client=llm_client,
            max_patch_attempts=max_patch_attempts,
            strict=strict,
            reference_patch_policy=reference_patch_policy,
            reference_path=reference_path,
            few_shot_case_ids=few_shot_case_ids,
            material_policy=material_policy,
            plan_loop_policy=plan_loop_policy,
            plan_loop_output_dir=plan_loop_output_dir,
            plan_reviewer_client=plan_reviewer_client,
            plan_repair_client=plan_repair_client,
            max_depth=max_depth,
        )
        return result.regenerated_patch_types

    return _resumer


__all__ = ["DownstreamResumeResult", "resume_incremental_from_patch", "make_downstream_resumer"]
