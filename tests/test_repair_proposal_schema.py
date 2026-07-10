from openmc_agent.repair_proposal import LLMRepairProposal, RepairPatchOperation, RepairProposalMode


def test_repair_proposal_schema_accepts_json_patch_subset():
    proposal = LLMRepairProposal(
        proposal_id="p1",
        source_issue_codes=["audit.material.nominal_reported_as_confirmed"],
        rationale="fix status only",
        expected_effect="nominal no longer marked confirmed",
        operations=[RepairPatchOperation(op="replace", path="/materials/0/composition_status", value="nominal")],
        confidence=0.8,
    )
    assert proposal.operations[0].op == "replace"
    assert RepairProposalMode.PROPOSAL_ONLY.value == "proposal_only"
