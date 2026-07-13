"""Contracts and isolated source-state injectors for runtime evaluation.

The module intentionally contains no reactor-specific repair policy. A case may
describe VERA3B fixture setup, but all mutations are applied to a copied source
``PlanBuildState`` and are verified before the production graph is invoked.
"""

from __future__ import annotations

import copy
import hashlib
import json
from enum import Enum
from pathlib import Path
from typing import Any, Callable

from pydantic import Field

from openmc_agent.plan_builder.state import PlanBuildState, PlanPatchEnvelope
from openmc_agent.schemas import AgentBaseModel


class FaultInjectionLayer(str, Enum):
    SOURCE_PATCH = "source_patch"
    PLAN_PATCH = "plan_patch"
    RENDERED_ARTIFACT = "rendered_artifact"
    TOOL_RESULT = "tool_result"
    ENVIRONMENT = "environment"
    LLM_RESPONSE = "llm_response"
    WORKFLOW_CONTROL = "workflow_control"


class FaultExpectedDisposition(str, Enum):
    RECOVERED = "recovered"
    SAFE_STOP = "safe_stop"
    BLOCKED_ENVIRONMENT = "blocked_environment"
    BLOCKED_HUMAN_FACT = "blocked_human_fact"
    DIAGNOSE_ONLY = "diagnose_only"
    TRANSIENT_RETRY_THEN_SUCCESS = "transient_retry_then_success"
    TRANSIENT_RETRY_EXHAUSTED = "transient_retry_exhausted"
    NO_PROGRESS = "no_progress"
    PROPOSAL_REJECTED = "proposal_rejected"
    USER_CANCELLED = "user_cancelled"


class FaultInjectionCase(AgentBaseModel):
    case_id: str
    title: str
    description: str
    lane: str = "fixture"
    injection_layer: FaultInjectionLayer
    target_stage: str
    applicable_variant: str = "3B"
    preconditions: list[str] = Field(default_factory=list)
    injection_operations: list[dict[str, Any]] = Field(default_factory=list)
    expected_primary_issue_code: str | None = None
    expected_classification: str | None = None
    expected_repair_channel: str = "none"
    expected_target_patch_type: str | None = None
    expected_changed_paths: list[str] = Field(default_factory=list)
    forbidden_changed_paths: list[str] = Field(default_factory=list)
    expected_final_disposition: FaultExpectedDisposition
    expected_max_iterations: int = 4
    requires_real_openmc: bool = False
    requires_llm: bool = False
    cleanup_requirements: list[str] = Field(default_factory=lambda: ["isolated_run_directory"])
    metadata: dict[str, Any] = Field(default_factory=dict)

    def prepare(self, state: PlanBuildState, output_dir: Path) -> PlanBuildState:
        output_dir.mkdir(parents=True, exist_ok=True)
        return state.model_copy(deep=True)

    def inject(self, state: PlanBuildState) -> PlanBuildState:
        """Apply only source-patch operations owned by this contract."""
        injected = state.model_copy(deep=True)
        for operation in self.injection_operations:
            if operation.get("patch_type") != "settings":
                continue
            target = _single_valid_patch(injected, "settings")
            for key, value in (operation.get("set") or {}).items():
                target.content[key] = copy.deepcopy(value)
        return injected

    def verify_injection(self, before: PlanBuildState, after: PlanBuildState) -> dict[str, Any]:
        return {
            "valid": state_hash(before) != state_hash(after) if self.injection_operations else True,
            "before_hash": state_hash(before),
            "after_hash": state_hash(after),
            "case_id": self.case_id,
        }

    def verify_outcome(self, outcome: dict[str, Any]) -> dict[str, Any]:
        actual = outcome.get("final_disposition")
        return {
            "passed": actual == self.expected_final_disposition.value,
            "expected_final_disposition": self.expected_final_disposition.value,
            "actual_final_disposition": actual,
        }

    def cleanup(self, output_dir: Path) -> None:
        # Artifacts are intentionally retained. No source fixture or parent env
        # is touched, and statepoints are removed by the runner if encountered.
        for statepoint in output_dir.rglob("statepoint.*.h5"):
            statepoint.unlink(missing_ok=True)


def state_hash(state: PlanBuildState | dict[str, Any]) -> str:
    value = state.model_dump(mode="json") if hasattr(state, "model_dump") else state
    raw = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def load_vera3b_accepted_state() -> PlanBuildState:
    """Load fixture patches as a self-contained accepted incremental state."""
    path = Path("tests/fixtures/vera3_patches/vera3_3b_patches.json")
    raw = json.loads(path.read_text(encoding="utf-8"))
    state = PlanBuildState(
        state_id="vera3b_accepted_fixture",
        requirement_text="VERA3 3B assembly from accepted incremental source patches",
        benchmark_id="VERA3",
        selected_variant="3B",
    )
    for index, entry in enumerate(raw["patches"]):
        patch_id = f"fixture_{index:02d}_{entry['patch_type']}"
        state.add_patch(PlanPatchEnvelope(
            patch_id=patch_id,
            patch_type=entry["patch_type"],
            content=copy.deepcopy(entry),
            source="fixture",
            status="valid",
        ))
    from openmc_agent.plan_builder.state import assemble_state_if_ready
    state = assemble_state_if_ready(state, strict=True)

    # Run capability assessment so the production graph's execute_tools node
    # does not skip export/debug/smoke due to renderability="none".
    if state.assembled_plan is not None:
        from openmc_agent.renderers import choose_renderer
        from openmc_agent.schemas import SimulationPlan
        plan = SimulationPlan.model_validate(state.assembled_plan)
        renderer, capability = choose_renderer(plan)
        if renderer is not None:
            state.assembled_plan["capability_report"] = capability.model_dump(mode="json")
            state.assembled_plan["model_spec"] = plan.model_spec

    return state


def _single_valid_patch(state: PlanBuildState, patch_type: str) -> PlanPatchEnvelope:
    patches = state.get_valid_patches(patch_type)
    if len(patches) != 1:
        raise ValueError(f"expected exactly one valid {patch_type} patch")
    return patches[0]


def default_fault_matrix() -> list[FaultInjectionCase]:
    """The R7/R8 immutable case registry; no case is silently omitted."""
    specs = [
        ("F00", "baseline_no_fault", FaultInjectionLayer.TOOL_RESULT, None, None, "none", FaultExpectedDisposition.RECOVERED, True),
        ("F01", "source_strategy_fault", FaultInjectionLayer.SOURCE_PATCH, "runtime.openmc_source_rejection_failure", "plan_fixable", "deterministic", FaultExpectedDisposition.RECOVERED, True),
        ("F02", "source_noop_renderer_bug", FaultInjectionLayer.TOOL_RESULT, "runtime.openmc_source_rejection_failure", "plan_fixable", "llm_diagnose", FaultExpectedDisposition.SAFE_STOP, False),
        ("F03", "timeout_then_success", FaultInjectionLayer.TOOL_RESULT, "runtime.openmc_timeout", "transient", "retry", FaultExpectedDisposition.TRANSIENT_RETRY_THEN_SUCCESS, False),
        ("F04", "timeout_twice", FaultInjectionLayer.TOOL_RESULT, "runtime.openmc_timeout", "transient", "retry", FaultExpectedDisposition.TRANSIENT_RETRY_EXHAUSTED, False),
        ("F05", "process_crash_after_source_rejection", FaultInjectionLayer.TOOL_RESULT, "runtime.openmc_source_rejection_failure", "plan_fixable", "deterministic", FaultExpectedDisposition.RECOVERED, False),
        ("F06", "cross_sections_missing", FaultInjectionLayer.ENVIRONMENT, "runtime.cross_sections_missing", "environment", "none", FaultExpectedDisposition.BLOCKED_ENVIRONMENT, False),
        ("F07", "missing_nuclide_data", FaultInjectionLayer.TOOL_RESULT, "runtime.material_missing_nuclide_data", "human_fact", "none", FaultExpectedDisposition.BLOCKED_HUMAN_FACT, False),
        ("F08", "ambiguous_geometry_overlap", FaultInjectionLayer.TOOL_RESULT, "runtime.geometry_overlap", "plan_fixable", "diagnose", FaultExpectedDisposition.DIAGNOSE_ONLY, True),
        ("F09", "lost_particle_without_provenance", FaultInjectionLayer.TOOL_RESULT, "runtime.lost_particle", "plan_fixable", "diagnose", FaultExpectedDisposition.DIAGNOSE_ONLY, False),
        ("F10", "protected_geometry_fault", FaultInjectionLayer.LLM_RESPONSE, "runtime.geometry_overlap", "plan_fixable", "llm_propose", FaultExpectedDisposition.PROPOSAL_REJECTED, False),
        ("F11", "unsafe_material_proposal", FaultInjectionLayer.LLM_RESPONSE, "runtime.geometry_overlap", "plan_fixable", "llm_propose", FaultExpectedDisposition.PROPOSAL_REJECTED, False),
        ("F12", "missing_prior_test_operation", FaultInjectionLayer.LLM_RESPONSE, "runtime.geometry_overlap", "plan_fixable", "llm_propose", FaultExpectedDisposition.PROPOSAL_REJECTED, False),
        ("F13", "duplicate_candidate", FaultInjectionLayer.LLM_RESPONSE, "runtime.geometry_overlap", "plan_fixable", "llm_propose", FaultExpectedDisposition.PROPOSAL_REJECTED, False),
        ("F14", "same_failure_after_commit", FaultInjectionLayer.TOOL_RESULT, "runtime.openmc_source_rejection_failure", "plan_fixable", "deterministic", FaultExpectedDisposition.NO_PROGRESS, False),
        ("F15", "new_failure_after_repair", FaultInjectionLayer.TOOL_RESULT, "runtime.openmc_source_rejection_failure", "plan_fixable", "deterministic", FaultExpectedDisposition.SAFE_STOP, False),
        ("F16", "environment_after_repair", FaultInjectionLayer.ENVIRONMENT, "runtime.cross_sections_missing", "environment", "none", FaultExpectedDisposition.BLOCKED_ENVIRONMENT, False),
        ("F17", "malformed_diagnosis_response", FaultInjectionLayer.LLM_RESPONSE, "runtime.geometry_overlap", "plan_fixable", "llm_diagnose", FaultExpectedDisposition.PROPOSAL_REJECTED, False),
        ("F18", "supervisor_unsafe_action", FaultInjectionLayer.LLM_RESPONSE, "runtime.cross_sections_missing", "environment", "veto", FaultExpectedDisposition.BLOCKED_ENVIRONMENT, False),
        ("F19", "user_cancel", FaultInjectionLayer.WORKFLOW_CONTROL, None, None, "none", FaultExpectedDisposition.USER_CANCELLED, False),
    ]
    cases: list[FaultInjectionCase] = []
    for prefix, name, layer, code, classification, channel, disposition, real_openmc in specs:
        operations = []
        if name in ("source_strategy_fault", "process_crash_after_source_rejection"):
            operations = [{"patch_type": "settings", "set": {"source_strategy": "assembly_box", "source_requires_fissionable_constraint": False}}]
        cases.append(FaultInjectionCase(
            case_id=f"{prefix}_{name}", title=name.replace("_", " "), description=name,
            injection_layer=layer, target_stage="execute_tools", injection_operations=operations,
            expected_primary_issue_code=code, expected_classification=classification,
            expected_repair_channel=channel, expected_target_patch_type="settings" if channel == "deterministic" else None,
            expected_changed_paths=["/source_strategy", "/source_requires_fissionable_constraint"] if name == "source_strategy_fault" else [],
            forbidden_changed_paths=["/density", "/composition", "/temperature", "/r", "/z_min_cm", "/z_max_cm"],
            expected_final_disposition=disposition, requires_real_openmc=real_openmc,
        ))
    return cases


def fault_case_by_name(name: str) -> FaultInjectionCase:
    for case in default_fault_matrix():
        if case.case_id == name or case.case_id.split("_", 1)[0] == name or case.case_id.endswith(name):
            return case
    raise KeyError(name)
