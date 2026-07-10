from openmc_agent.workflow_trace import TraceRecorder


def test_llm_repair_trace_events_available():
    r=TraceRecorder()
    r.add_event("llm_repair_proposal_started", metadata={"proposal_id":"p"})
    r.add_event("llm_repair_proposal_generated", metadata={"proposal_id":"p", "status":"proposed", "operation_count":0})
    assert [e.event_type for e in r.trace.events] == ["llm_repair_proposal_started", "llm_repair_proposal_generated"]
