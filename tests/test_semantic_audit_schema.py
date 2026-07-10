from openmc_agent.semantic_audit import SemanticAuditFinding, SemanticAuditSeverity


def test_semantic_audit_finding_schema_validates():
    f = SemanticAuditFinding(
        finding_code="audit.geometry.dimension_mismatch",
        title="x", severity=SemanticAuditSeverity.WARNING, summary="s", confidence=0.7,
    )
    assert f.suggested_patch_target == "none"
