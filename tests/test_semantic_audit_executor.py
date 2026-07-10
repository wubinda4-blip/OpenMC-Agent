from openmc_agent.semantic_audit import SemanticAuditInput, run_semantic_plan_audit, SemanticAuditMode

class BadThenGood:
    def __init__(self): self.n=0
    def audit(self, audit_input, *, prompt, json_schema):
        self.n += 1
        if self.n == 1: return "not json"
        return {"audit_id": audit_input.audit_id, "ok": True, "findings": [{"finding_code":"made.up", "title":"t", "severity":"warning", "summary":"s", "confidence":0.8}]}

class AlwaysBad:
    def audit(self, audit_input, *, prompt, json_schema):
        return "not json"


def test_unknown_code_normalized_after_retry():
    ai = SemanticAuditInput(audit_id="a", original_requirement="", resolved_requirement_summary="")
    r = run_semantic_plan_audit(ai, mode=SemanticAuditMode.WARNING_ONLY, client=BadThenGood())
    assert r.findings[0].finding_code == "audit.unknown_finding_code"
    assert any("unknown" in w for w in r.warnings)


def test_invalid_json_retry_then_fallback():
    ai = SemanticAuditInput(audit_id="a", original_requirement="3D axial", resolved_requirement_summary="")
    r = run_semantic_plan_audit(ai, client=AlwaysBad())
    assert r.fallback_used is True
