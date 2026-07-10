from openmc_agent.workflow_trace import TraceRecorder


def test_semantic_audit_trace_events_available():
    r = TraceRecorder()
    r.add_event("semantic_audit_started", metadata={"audit_id":"a"})
    r.add_event("semantic_audit_completed", metadata={"audit_id":"a", "finding_count":0, "finding_codes":[]})
    assert [e.event_type for e in r.trace.events] == ["semantic_audit_started", "semantic_audit_completed"]
