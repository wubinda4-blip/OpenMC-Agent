"""Evaluation harness for incremental planning with real or fake LLM (Phase 7).

Runs the full incremental pipeline (mode decision → patch generation →
assembly → guard check) and produces a structured evaluation report with
per-patch metrics.  Designed for opt-in real-LLM evaluation; CI tests use
FakePatchLLM.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pydantic import Field

from openmc_agent.schemas import AgentBaseModel, SimulationPlan
from openmc_agent.assembly3d_guard import validate_assembly3d_plan

from .executor import run_incremental_planning
from .mode import should_use_incremental_planning, PlanningModeDecision
from .patch_generator import PatchGenerationContext
from .state import PlanBuildState, initialize_plan_build_state


class PatchMetric(AgentBaseModel):
    patch_type: str
    attempts: int = 0
    raw_chars: int = 0
    parse_ok: bool = False
    validation_ok: bool = False
    issue_codes: list[str] = Field(default_factory=list)
    contains_full_plan_markers: bool = False
    contains_full_lattice_suspected: bool = False


class AssemblySummary(AgentBaseModel):
    ok: bool = False
    lattice_size: list[int] | None = None
    axial_layer_count: int = 0
    overlay_count: int = 0
    pyrex_count: int = 0
    thimble_plug_count: int = 0
    fuel_count: int = 0


class GuardSummary(AgentBaseModel):
    blocking_issue_count: int = 0
    issue_codes: list[str] = Field(default_factory=list)


class EvaluationReport(AgentBaseModel):
    benchmark: str | None = None
    variant: str | None = None
    planning_mode: str = "incremental"
    ok: bool = False
    model: str | None = None
    patch_metrics: dict[str, PatchMetric] = Field(default_factory=dict)
    assembly: AssemblySummary = Field(default_factory=AssemblySummary)
    guard: GuardSummary = Field(default_factory=GuardSummary)
    no_monolithic_plan_requested: bool = True
    error: str | None = None


def run_incremental_evaluation(
    *,
    requirement: str,
    benchmark_id: str | None = None,
    selected_variant: str | None = None,
    llm_client: Any,
    model: str | None = None,
    max_patch_attempts: int = 2,
    output_dir: str | Path | None = None,
) -> tuple[EvaluationReport, PlanBuildState]:
    """Run an incremental planning evaluation and produce a report.

    Parameters
    ----------
    requirement
        The benchmark requirement text.
    benchmark_id, selected_variant
        Optional benchmark / variant identifiers.
    llm_client
        A callable ``(prompt: str) -> str`` (real LLM adapter or FakePatchLLM).
    model
        Model name for the report (informational only).
    max_patch_attempts
        Max retry attempts per patch.
    output_dir
        If provided, write evaluation_report.json, plan_build_state.json,
        assembled_plan.json, and per-patch raw text files.

    Returns
    -------
    (EvaluationReport, PlanBuildState)
    """
    decision: PlanningModeDecision = should_use_incremental_planning(requirement)
    state = initialize_plan_build_state(
        requirement=requirement,
        decision=decision,
        benchmark_id=benchmark_id,
        selected_variant=selected_variant,
    )

    exec_result = run_incremental_planning(
        requirement=requirement,
        state=state,
        llm_client=llm_client,
        max_patch_attempts=max_patch_attempts,
    )

    report = EvaluationReport(
        benchmark=benchmark_id,
        variant=selected_variant,
        planning_mode=decision.mode,
        ok=exec_result.ok,
        model=model,
        no_monolithic_plan_requested=True,
    )

    # Extract per-patch metrics from build state.
    for env in state.patches.values():
        metric = PatchMetric(patch_type=env.patch_type)
        metric.validation_ok = env.status == "valid"
        metric.issue_codes = [i.get("code", "") for i in env.issues]
        report.patch_metrics[env.patch_type] = metric

    # The executor's internal attempts are in the build log; extract raw_chars
    # from patch generation events.  Since the executor uses generate_patch
    # internally, we approximate attempts from the build log.
    for event in state.build_log:
        if event.event_type == "planning.patch_generated":
            pt = event.data.get("patch_type", "")
            if pt in report.patch_metrics:
                report.patch_metrics[pt].attempts = event.data.get("attempts", 1)

    # Assembly summary.
    if exec_result.ok and exec_result.assembled_plan:
        plan_dict = exec_result.assembled_plan
        cm = plan_dict.get("complex_model", {})
        lattices = cm.get("lattices", [])
        if lattices:
            pattern = lattices[0].get("universe_pattern", [])
            flat = [uid for row in pattern for uid in row]
            report.assembly = AssemblySummary(
                ok=True,
                lattice_size=[len(pattern), len(pattern[0]) if pattern else 0],
                pyrex_count=sum(1 for u in flat if "pyrex" in u.lower()),
                thimble_plug_count=sum(1 for u in flat if "plug" in u.lower()),
                fuel_count=sum(1 for u in flat if "fuel" in u.lower()),
            )
        core = cm.get("core", {})
        report.assembly.axial_layer_count = len(core.get("axial_layers", []))
        report.assembly.overlay_count = len(core.get("axial_overlays", []))

        # Guard check.
        try:
            plan = SimulationPlan.model_validate(plan_dict)
            issues = validate_assembly3d_plan(plan, requirement=requirement)
            blocking = [i for i in issues if i.severity == "error"]
            report.guard = GuardSummary(
                blocking_issue_count=len(blocking),
                issue_codes=[i.code for i in issues],
            )
        except Exception:
            report.guard = GuardSummary(blocking_issue_count=-1)
    else:
        error_codes = [i.code for i in exec_result.issues if i.severity == "error"]
        report.error = "; ".join(error_codes[:3]) if error_codes else "assembly failed"

    # Write output if requested.
    if output_dir is not None:
        _write_evaluation_output(
            Path(output_dir), report, state, exec_result.assembled_plan,
        )

    return report, state


def _write_evaluation_output(
    out_dir: Path,
    report: EvaluationReport,
    state: PlanBuildState,
    assembled_plan: dict[str, Any] | None,
) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "evaluation_report.json").write_text(
        json.dumps(report.model_dump(mode="json"), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (out_dir / "plan_build_state.json").write_text(
        json.dumps(state.model_dump(mode="json"), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    if assembled_plan is not None:
        (out_dir / "assembled_plan.json").write_text(
            json.dumps(assembled_plan, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    # Write per-patch content.
    patch_dir = out_dir / "patches"
    patch_dir.mkdir(exist_ok=True)
    for env in state.patches.values():
        fname = f"{env.patch_type}.json"
        (patch_dir / fname).write_text(
            json.dumps(env.content, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )


__all__ = [
    "PatchMetric",
    "AssemblySummary",
    "GuardSummary",
    "EvaluationReport",
    "run_incremental_evaluation",
]
