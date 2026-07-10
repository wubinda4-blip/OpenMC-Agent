from openmc_agent.repair_proposal import RepairProposalMode, run_repair_proposal_flow, RepairValidationSnapshot

class BadClient:
    def __init__(self): self.calls=0
    def propose(self, repair_input, *, prompt, json_schema):
        self.calls += 1
        return "not json"

class CountingClient:
    def __init__(self): self.calls=0
    def propose(self, *a, **k):
        self.calls += 1
        return {}


def test_invalid_json_retry_failure_fallback(monkeypatch):
    monkeypatch.setattr("openmc_agent.repair_proposal.validate_plan_for_repair", lambda plan, *, context=None: RepairValidationSnapshot(schema_valid=True, issue_codes=[], blocking_issue_codes=[], warning_issue_codes=[]))
    client = BadClient()
    result = run_repair_proposal_flow(plan={}, validation_result={"issue_codes":["audit.fact_gap.unresolved_fact_hidden"]}, mode=RepairProposalMode.VALIDATE_ONLY, client=client)
    assert client.calls == 2
    assert result.fallback_used is True


def test_deterministic_repair_prevents_llm_call(monkeypatch):
    monkeypatch.setattr("openmc_agent.repair_proposal.validate_plan_for_repair", lambda plan, *, context=None: RepairValidationSnapshot(schema_valid=True, issue_codes=[], blocking_issue_codes=[], warning_issue_codes=[]))
    client = CountingClient()
    result = run_repair_proposal_flow(plan={}, validation_result={}, mode=RepairProposalMode.VALIDATE_ONLY, client=client, context={"deterministic_repair_attempted": True, "deterministic_repair_succeeded": True})
    assert client.calls == 0
    assert result.deterministic_repair_succeeded is True


def test_fact_gap_fallback_requires_human_confirmation(monkeypatch):
    monkeypatch.setattr("openmc_agent.repair_proposal.validate_plan_for_repair", lambda plan, *, context=None: RepairValidationSnapshot(schema_valid=True, issue_codes=[], blocking_issue_codes=[], warning_issue_codes=[]))
    result = run_repair_proposal_flow(plan={}, validation_result={"issue_codes":["audit.fact_gap.unresolved_fact_hidden"]}, mode=RepairProposalMode.VALIDATE_ONLY, client=BadClient())
    assert result.proposal.requires_human_confirmation is True
