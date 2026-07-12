"""Freeze the VERA3B expert-feedback failure as a regression diagnosis.

Reads the real run artifacts under ``data/runs/VERA_3B/`` and writes
``expert_feedback_failure_diagnosis.json`` plus a sanitized regression fixture.
Run: ``conda run -n openmc-env python scripts/build_vera3b_diagnosis.py``.
"""
from __future__ import annotations

import json
from pathlib import Path

from openmc_agent.capability_blockers import classify_capability_blockers
from openmc_agent.expert_feedback import group_expert_questions
from openmc_agent.llm import normalize_capability_report
from openmc_agent.schemas import SimulationPlan

RUN_DIR = Path("data/runs/VERA_3B")
FIXTURE_DIR = Path("tests/fixtures/regressions")


def main() -> None:
    raw = json.loads((RUN_DIR / "simulation_plan.json").read_text())
    normalize_capability_report(raw)
    plan = SimulationPlan.model_validate(raw)
    cap = plan.capability_report
    # The on-disk capability_report.json is the authoritative skeleton report
    # (it keeps supported_renderer='assembly'; the plan-load normalizer forces
    # 'none' just so the non-executable plan validates).
    real_cap = json.loads((RUN_DIR / "capability_report.json").read_text())
    cap = cap.model_copy(update={"supported_renderer": real_cap.get("supported_renderer", cap.supported_renderer)})
    transcript = json.loads((RUN_DIR / "transcript.json").read_text())
    summary = classify_capability_blockers(plan)
    groups = group_expert_questions(plan, summary)

    sup = transcript.get("run_supervisor_result") or {}
    sup_decision = sup.get("proposed_decision") or sup.get("final_decision") or {}

    diagnosis = {
        "schema_version": "expert_feedback_failure_diagnosis.v1",
        "source_run": str(RUN_DIR),
        "capability": {
            "renderability": cap.renderability,
            "is_executable": cap.is_executable,
            "supported_renderer": cap.supported_renderer,
            "reasons": cap.reasons,
            "issues": [
                {
                    "code": i.code,
                    "severity": i.severity,
                    "schema_path": i.schema_path,
                    "route_hint": i.route_hint,
                    "requires_human_confirmation": i.requires_human_confirmation,
                    "message": i.message,
                }
                for i in cap.issues
            ],
            "required_human_confirmations": cap.required_human_confirmations,
        },
        "expert_assumptions": plan.expert_assumptions,
        "pending_expert_questions": transcript.get("pending_expert_questions", []),
        "run_supervisor": {
            "action": sup_decision.get("action"),
            "rationale": sup_decision.get("rationale"),
            "mode": sup.get("mode"),
            "fallback_used": sup.get("fallback_used"),
        },
        "empty_input_state": {
            "expert_feedback": transcript.get("expert_feedback", []),
            "expert_feedback_action": transcript.get("expert_feedback_action"),
            "pending_questions_retained": bool(transcript.get("pending_expert_questions")),
            "resolved_item_count": len(transcript.get("resolved_expert_items", [])),
            "capability_recomputed": False,
            "interpreted_as": (
                "vague_continue (legacy): pending questions retained AND "
                "workflow continued to render"
            ),
        },
        "final_status": {
            "status": "PASS" if transcript.get("ok") else "FAIL",
            "fail_reason": "renderability=skeleton -> _plan_state_ok returned False",
            "openmc_execution_attempted": False,
        },
        "real_blocking_issue_codes": summary.primary_blocker_codes,
        "blocker_classification": {
            "structural_agent_fixable_codes": [i.code for i in summary.structural_agent_fixable],
            "environment_required_codes": [i.code for i in summary.environment_required],
            "human_fact_required_count": len(summary.human_fact_required),
            "material_assumptions_count": len(summary.material_assumptions),
            "structural_issue_not_visible_to_validate_plan": (
                summary.structural_issue_not_visible_to_validate_plan
            ),
        },
        "expert_question_groups": [g.model_dump(mode="json") for g in groups],
        "primary_blocker_explanation": (
            "The skeleton was forced by axial lattice materialization failures: "
            "lattice-loading operations 'replace_water_with_grid' / "
            "'replace_water_with_top_grid' reference a replacement universe "
            "'grid_cell' that the plan does not declare. This is a structural "
            "plan defect, NOT a material-fact gap. It is not visible to "
            "validate_plan or can_render (only to render-time materialization), "
            "so the supervisor asked material questions that masked it."
        ),
    }

    out = RUN_DIR / "expert_feedback_failure_diagnosis.json"
    out.write_text(json.dumps(diagnosis, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"wrote {out}")

    # Sanitized regression fixture: stable structure, no verbose LLM assumption text.
    fixture = {
        "schema_version": "vera3b_empty_expert_feedback_skeleton.v1",
        "renderability": cap.renderability,
        "is_executable": cap.is_executable,
        "supported_renderer": cap.supported_renderer,
        "real_blocking_issue_codes": summary.primary_blocker_codes,
        "structural_issue_not_visible_to_validate_plan": (
            summary.structural_issue_not_visible_to_validate_plan
        ),
        "legacy_run_supervisor_action": sup_decision.get("action"),
        "legacy_empty_input_expert_feedback_action": transcript.get("expert_feedback_action"),
        "legacy_pending_questions_retained_after_empty_input": bool(
            transcript.get("pending_expert_questions")
        ),
        "legacy_final_status": "PASS" if transcript.get("ok") else "FAIL",
        "expected": {
            "new_empty_input_semantics": {
                "runnable_nonblocking": "defer_confirmations",
                "skeleton_or_blocked": "accept_review_only",
            },
            "final_status_label": "BLOCKED_REVIEW_ONLY",
            "internal_ok": False,
            "openmc_execution_attempted": False,
            "expert_question_group_count": len(groups),
            "material_assumption_count": len(summary.material_assumptions),
            "no_duplicate_question_wall": True,
        },
    }
    FIXTURE_DIR.mkdir(parents=True, exist_ok=True)
    fixture_path = FIXTURE_DIR / "vera3b_empty_expert_feedback_skeleton.json"
    fixture_path.write_text(json.dumps(fixture, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"wrote {fixture_path}")


if __name__ == "__main__":
    main()
