from openmc_agent.semantic_audit import SemanticAuditInput, run_deterministic_semantic_audit


def test_fallback_detects_vera3_3b_axial_conflict():
    ai = SemanticAuditInput(
        audit_id="a", original_requirement="3D finite axial poison rod", resolved_requirement_summary="",
        assembled_plan_summary={"special_pin_roles":["pyrex_rod"], "axial_layers":[], "lattice_loadings":[], "axial_overlays":[]},
    )
    result = run_deterministic_semantic_audit(ai)
    assert "audit.axial.partial_insert_in_base_lattice" in [f.finding_code for f in result.findings]


def test_2d_clean_no_axial_false_positive():
    ai = SemanticAuditInput(audit_id="a", original_requirement="2D assembly", resolved_requirement_summary="", assembled_plan_summary={"special_pin_roles":[], "axial_layers":[], "axial_overlays":[]})
    result = run_deterministic_semantic_audit(ai)
    assert not [f for f in result.findings if f.finding_code.startswith("audit.axial")]
