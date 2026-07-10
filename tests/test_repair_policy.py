from openmc_agent.repair_policy import match_json_pointer_pattern, decode_json_pointer, is_protected_path
from openmc_agent.repair_proposal import RepairPatchOperation, evaluate_repair_operation


def test_path_pattern_matcher_and_json_pointer_decoding():
    assert decode_json_pointer("/materials/a~1b/~0key") == ["materials", "a/b", "~key"]
    assert match_json_pointer_pattern("/materials/0/composition_status", "/materials/*/composition_status")
    assert match_json_pointer_pattern("/core/axial_overlays/0/id", "/core/axial_overlays/**")


def test_protected_density_enrichment_and_nuclear_data_paths():
    assert is_protected_path("/materials/0/density")
    assert is_protected_path("/materials/0/enrichment_percent")
    assert is_protected_path("/settings/nuclear_data_path")


def test_operation_not_allowed_and_issue_allowlist():
    ev = evaluate_repair_operation(
        RepairPatchOperation(op="remove", path="/materials/0/composition_status"),
        source_issue_codes=["audit.material.nominal_reported_as_confirmed"],
        source_audit_finding_codes=[],
    )
    assert not ev.allowed
    assert "repair.operation_not_allowed" in ev.rejection_codes
