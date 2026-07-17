import json

from openmc_agent.plan_builder.closed_loop.facts_evidence import build_facts_evidence_packs
from openmc_agent.plan_builder.closed_loop.facts_reviewer import run_facts_review
from openmc_agent.plan_builder.closed_loop.models import PlanClosedLoopPolicy
from openmc_agent.plan_builder.state import PlanBuildState


def test_reviewer_normalizes_only_evidence_backed_facts_findings() -> None:
    policy = PlanClosedLoopPolicy()
    packs = build_facts_evidence_packs(requirement_text="variant A\n", facts_patch={"patch_type": "facts"}, confirmed_facts={}, planning_metadata={}, policy=policy)
    evidence = packs[0].source_excerpts[0].evidence_hash
    payload = {"review_status": "complete", "reviewed_evidence_hashes": [evidence], "coverage_summary": {}, "findings": [{"code": "facts.variant_missing", "severity": "error", "category": "source_coverage", "message": "missing", "evidence_hashes": [evidence], "affected_json_paths": ["/fuel_variant_requirements"], "repairable_by_llm": True, "requires_human": False, "confidence": 0.9}]}
    result = run_facts_review(evidence_packs=packs, reviewer_client=lambda _: json.dumps(payload), state=PlanBuildState(state_id="s", requirement_text="r"), policy=policy)
    assert result.ok and result.coverage_complete and result.findings[0].affected_patch_types == ["facts"]
