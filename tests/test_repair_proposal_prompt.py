from openmc_agent.repair_proposal import RepairProposalInput
from openmc_agent.repair_prompts import build_repair_proposal_prompt


def test_repair_prompt_redacts_secret_metadata_and_lists_contract():
    ri = RepairProposalInput(
        repair_id="r", requirement_summary="", plan_summary={"pin_map": [["fuel"] * 17] * 17},
        issue_codes=[], issue_summaries=[], allowed_operations=["replace"], allowed_paths=["/metadata/x"],
        protected_path_summary=["/materials/*/density*"], validation_summary_before={}, metadata={"api_key":"SECRET"},
    )
    prompt = build_repair_proposal_prompt(ri)
    assert "You do not generate Python" in prompt
    assert "SECRET" not in prompt
    assert prompt.count("fuel") < 50
