from openmc_agent.semantic_audit import SemanticAuditInput
from openmc_agent.semantic_audit_prompts import build_semantic_audit_prompt


def test_prompt_states_read_only_boundaries():
    p = build_semantic_audit_prompt(SemanticAuditInput(audit_id="a", original_requirement="", resolved_requirement_summary=""))
    assert "read-only semantic plan auditor" in p
    assert "do not invent benchmark facts" in p.lower()
