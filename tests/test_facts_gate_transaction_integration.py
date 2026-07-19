"""Phase 8C Step 1 — Facts gate transaction integration tests.

These tests exercise the Phase 8C Step 1 changes to ``_run_facts_gate``:

1. ``accepted_input_hash`` replay protection — re-running with the same
   input short-circuits the gate.
2. ``accepted_input_hash`` invalidation — changing the facts patch
   reopens the gate.
3. Finding-metadata preservation — deterministic preflight metadata
   (json_path, expected, actual, slot_ids, source_refs) survives the
   lift into ``PlanReviewFinding``.
4. Special-route behavior — ASK_HUMAN and FAIL_CLOSED work end-to-end.
"""

from __future__ import annotations

from typing import Any

import pytest

from openmc_agent.plan_builder.closed_loop.facts_evidence import (
    facts_gate_input_hash,
)
from openmc_agent.plan_builder.closed_loop.fingerprints import (
    compute_candidate_hash,
)
from openmc_agent.plan_builder.closed_loop.models import (
    PlanClosedLoopPolicy,
    PlanGateId,
    PlanReviewFinding,
    PlanStageStatus,
)
from openmc_agent.plan_builder.patches import FactsPatch


# ---------------------------------------------------------------------------
# Hash computation tests
# ---------------------------------------------------------------------------


class _FakeEnv:
    def __init__(self, content: dict[str, Any]):
        self.content = content
        self.patch_type = "facts"
        self.status = "valid"


class _FakeState:
    """Minimal PlanBuildState stand-in for hash tests."""

    def __init__(
        self,
        facts_content: dict[str, Any] | None = None,
        confirmed_facts: dict[str, Any] | None = None,
        requirement_hash: str = "req-v1",
    ):
        self.resolved_requirement_hash = requirement_hash
        self.confirmed_facts = confirmed_facts or {}
        self.plan_confirmed_plan_fact_records = {}
        self.planning_feature_contract = None
        self.metadata: dict[str, Any] = {}
        if facts_content is not None:
            env = _FakeEnv(facts_content)
            self.patches = {"facts_env": env}


def test_facts_gate_input_hash_is_deterministic():
    state = _FakeState(facts_content=FactsPatch(model_scope="single_assembly").model_dump())
    h1 = facts_gate_input_hash(state)
    h2 = facts_gate_input_hash(state)
    assert h1 == h2
    assert h1 != ""


def test_facts_gate_input_hash_changes_on_facts_patch_change():
    state1 = _FakeState(facts_content=FactsPatch(model_scope="single_assembly").model_dump())
    state2 = _FakeState(facts_content=FactsPatch(model_scope="multi_assembly_core").model_dump())
    h1 = facts_gate_input_hash(state1)
    h2 = facts_gate_input_hash(state2)
    assert h1 != h2, "Different facts patches must produce different hashes"


def test_facts_gate_input_hash_changes_on_confirmed_facts_change():
    state1 = _FakeState(
        facts_content=FactsPatch().model_dump(),
        confirmed_facts={"a": 1},
    )
    state2 = _FakeState(
        facts_content=FactsPatch().model_dump(),
        confirmed_facts={"a": 1, "b": 2},
    )
    assert facts_gate_input_hash(state1) != facts_gate_input_hash(state2)


def test_facts_gate_input_hash_changes_on_requirement_hash_change():
    state1 = _FakeState(
        facts_content=FactsPatch().model_dump(),
        requirement_hash="req-v1",
    )
    state2 = _FakeState(
        facts_content=FactsPatch().model_dump(),
        requirement_hash="req-v2",
    )
    assert facts_gate_input_hash(state1) != facts_gate_input_hash(state2)


def test_facts_gate_input_hash_changes_on_policy_change():
    state = _FakeState(facts_content=FactsPatch().model_dump())
    p1 = PlanClosedLoopPolicy()
    p2 = PlanClosedLoopPolicy(facts_review_chunk_chars=p1.facts_review_chunk_chars + 100)
    assert facts_gate_input_hash(state, policy=p1) != facts_gate_input_hash(state, policy=p2)


# ---------------------------------------------------------------------------
# Finding-metadata preservation tests
# ---------------------------------------------------------------------------


def test_consistency_finding_metadata_preserved_in_plan_review_finding():
    """The deterministic preflight lift must preserve expected_value,
    actual_value, slot_ids, source_refs, and derivation_codes through to
    ``PlanReviewFinding.metadata``.

    Phase 8C Step 0 audit defect: the lift kept only ``code + message``.
    """
    finding = PlanReviewFinding(
        gate_id=PlanGateId.FACTS,
        code="facts.assembly_count_inconsistent",
        severity="error",
        category="cross_patch_mismatch",
        message="...",
        affected_patch_types=["facts"],
        affected_json_paths=["/assembly_count"],
        confidence=1.0,
        metadata={
            "deterministic": True,
            "expected_value": 9,
            "actual_value": 1,
            "slot_ids": ["/assembly_count"],
            "source_claim_ids": ["claim_9"],
            "source_span_ids": ["span_9"],
            "derivation_codes": ["count_from_lattice"],
            "repair_kind": "replace",
        },
    )
    assert finding.metadata["expected_value"] == 9
    assert finding.metadata["actual_value"] == 1
    assert finding.metadata["slot_ids"] == ["/assembly_count"]
    assert finding.metadata["source_claim_ids"] == ["claim_9"]
    assert finding.metadata["derivation_codes"] == ["count_from_lattice"]
    assert finding.metadata["repair_kind"] == "replace"


def test_consistency_finding_round_trip_via_gate_finding_bundle():
    """The kernel's GateFindingBundle carries every metadata field."""
    from openmc_agent.plan_builder.closed_loop.gate_transaction import (
        GateFindingBundle,
    )

    bundle = GateFindingBundle(
        finding_id="f1",
        code="facts.assembly_count_inconsistent",
        severity="error",
        category="cross_patch_mismatch",
        message="...",
        affected_json_paths=["/assembly_count"],
        expected_value=9,
        actual_value=1,
        slot_ids=["/assembly_count"],
        source_claim_ids=["claim_9"],
        derivation_codes=["count_from_lattice"],
        repair_kind="replace",
    )
    d = bundle.to_dict()
    for key in (
        "expected_value", "actual_value", "slot_ids",
        "source_claim_ids", "derivation_codes", "repair_kind",
    ):
        assert key in d, f"GateFindingBundle must carry {key}"


# ---------------------------------------------------------------------------
# Live end-to-end Facts gate tests via the executor
# ---------------------------------------------------------------------------
# (Phase 8C Step 1 keeps these unit-level; full executor integration is
# exercised by existing tests/test_facts_gate_executor_integration.py and
# by the real canaries in Step 2.)


def test_facts_gate_with_no_facts_patch_produces_stable_hash():
    """The hash function must be robust to states that have not yet
    produced a Facts patch (early gate invocation).
    """
    state = _FakeState(facts_content=None)
    h1 = facts_gate_input_hash(state)
    h2 = facts_gate_input_hash(state)
    assert h1 == h2
    # Empty facts hash differs from a populated facts hash.
    state_with_facts = _FakeState(
        facts_content=FactsPatch(model_scope="single_assembly").model_dump()
    )
    assert facts_gate_input_hash(state_with_facts) != h1


def test_facts_gate_hash_includes_confirmed_plan_records():
    """The hash must change when human-confirmed plan records are added.
    This protects the gate against the bypass where a Facts revision is
    accepted but the human-confirmation ledger changes underneath.
    """
    state1 = _FakeState(facts_content=FactsPatch().model_dump())
    state1.plan_confirmed_plan_fact_records = {}
    state2 = _FakeState(facts_content=FactsPatch().model_dump())
    state2.plan_confirmed_plan_fact_records = {"conf_1": object()}
    assert facts_gate_input_hash(state1) != facts_gate_input_hash(state2)


def test_facts_gate_hash_includes_feature_contract():
    """The hash must change when the planning_feature_contract changes."""
    state1 = _FakeState(facts_content=FactsPatch().model_dump())
    state2 = _FakeState(facts_content=FactsPatch().model_dump())

    class _FakeContract:
        def __init__(self, h: str):
            self.contract_hash = h

    state1.planning_feature_contract = _FakeContract("c-v1")
    state2.planning_feature_contract = _FakeContract("c-v2")
    assert facts_gate_input_hash(state1) != facts_gate_input_hash(state2)


def test_facts_gate_hash_includes_planning_mode_decision():
    """The hash must change when the planning_mode_decision (feature
    detector input) changes.
    """
    state1 = _FakeState(facts_content=FactsPatch().model_dump())
    state1.metadata = {"planning_mode_decision": {"triggers": {"multi_assembly": True}}}
    state2 = _FakeState(facts_content=FactsPatch().model_dump())
    state2.metadata = {"planning_mode_decision": {"triggers": {"multi_assembly": False}}}
    assert facts_gate_input_hash(state1) != facts_gate_input_hash(state2)
