"""Phase 8C Step 1 — Unit tests for the gate transaction kernel.

These tests exercise the kernel with stub hooks (no real state, no LLM).
The goal is to verify that the 20-step transaction order is respected and
that every short-circuit returns the right status.  Integration with the
real Facts gate is covered by ``test_facts_gate_transaction_retry_execution``
and the existing ``test_facts_gate_executor_integration``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pytest

from openmc_agent.plan_builder.closed_loop.gate_transaction import (
    GateFindingBundle,
    GateRepairExecutionResult,
    GateTransactionHooks,
    GateTransactionInput,
    GateTransactionResult,
    GateTransactionStatus,
    run_gate_transaction,
)
from openmc_agent.plan_builder.closed_loop.models import (
    PlanGateId,
    PlanReviewAction,
)


# ---------------------------------------------------------------------------
# A recording stub that lets tests script hook return values and assert
# the order in which hooks were called.
# ---------------------------------------------------------------------------


@dataclass
class StubHooks:
    """Minimal GateTransactionHooks implementation for tests.

    Each hook method appends its name to ``call_log`` and then returns
    the scripted value (default = the "continue" sentinel for that hook).
    """

    gate_id: PlanGateId = PlanGateId.FACTS
    applicability_result: GateTransactionResult | None = None
    ready_result: GateTransactionResult | None = None
    input_hash_value: str = "hash-v1"
    stale_hash_result: GateTransactionResult | None = None
    contract_result: GateTransactionResult | None = None
    preflight_findings: list[GateFindingBundle] = field(default_factory=list)
    preflight_result: GateTransactionResult | None = None
    evidence_pack_result: GateTransactionResult | None = None
    review_findings: list[GateFindingBundle] = field(default_factory=list)
    review_result: GateTransactionResult | None = None
    aggregate_findings_override: list[GateFindingBundle] | None = None
    route_action: PlanReviewAction = PlanReviewAction.APPROVE
    route_primary_finding: GateFindingBundle | None = None
    route_result: GateTransactionResult | None = None
    special_route_result: GateTransactionResult | None = None
    retry_request_result: GateTransactionResult | None = None
    produce_clone: Any = None
    produce_repair: GateRepairExecutionResult = field(
        default_factory=lambda: GateRepairExecutionResult(executed=True)
    )
    validate_result: GateTransactionResult | None = None
    commit_result: GateTransactionResult | None = None
    rebuild_result: GateTransactionResult | None = None
    replay_status: str = GateTransactionStatus.ACCEPTED
    replay_findings: list[GateFindingBundle] = field(default_factory=list)
    call_log: list[str] = field(default_factory=list)

    # Hook implementations
    def applicable(self, *, state, policy, inp):
        self.call_log.append("applicable")
        return self.applicability_result

    def ready(self, *, state, policy, ctx):
        self.call_log.append("ready")
        return self.ready_result

    def compute_input_hash(self, *, state, policy):
        self.call_log.append("compute_input_hash")
        return self.input_hash_value

    def stale_hash_check(self, *, state, policy, ctx):
        self.call_log.append("stale_hash_check")
        return self.stale_hash_result

    def compile_requirement_contract(self, *, state, policy, ctx):
        self.call_log.append("compile_requirement_contract")
        return self.contract_result

    def run_preflight(self, *, state, policy, ctx):
        self.call_log.append("run_preflight")
        return list(self.preflight_findings), self.preflight_result

    def build_evidence_pack(self, *, state, policy, ctx):
        self.call_log.append("build_evidence_pack")
        return self.evidence_pack_result

    def run_review(self, *, state, policy, ctx):
        self.call_log.append("run_review")
        return list(self.review_findings), self.review_result

    def aggregate_findings(self, *, state, policy, ctx, deterministic_findings, review_findings):
        self.call_log.append("aggregate_findings")
        if self.aggregate_findings_override is not None:
            return self.aggregate_findings_override
        return list(deterministic_findings) + list(review_findings)

    def route_finding(self, *, state, policy, ctx, findings):
        self.call_log.append("route_finding")
        return self.route_action, self.route_primary_finding, self.route_result

    def execute_special_route(self, *, state, policy, ctx, action, primary_finding):
        self.call_log.append("execute_special_route")
        return self.special_route_result

    def build_retry_request(self, *, state, policy, ctx, action, findings):
        self.call_log.append("build_retry_request")
        return self.retry_request_result

    def produce_candidate(self, *, state, policy, ctx, action, findings):
        self.call_log.append("produce_candidate")
        return self.produce_clone, self.produce_repair

    def validate_candidate(self, *, state, policy, ctx, clone_state, repair_result):
        self.call_log.append("validate_candidate")
        return self.validate_result

    def commit_candidate(self, *, state, policy, ctx, clone_state, repair_result):
        self.call_log.append("commit_candidate")
        return self.commit_result

    def rebuild_downstream(self, *, state, policy, ctx):
        self.call_log.append("rebuild_downstream")
        return self.rebuild_result

    def replay_gate(self, *, state, policy, ctx):
        self.call_log.append("replay_gate")
        return GateTransactionResult(
            status=self.replay_status,
            gate_id=self.gate_id,
            transaction_id=ctx.transaction_id,
            findings=list(self.replay_findings),
        )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_not_applicable_short_circuits_before_any_other_hook():
    hooks = StubHooks(
        applicability_result=GateTransactionResult(
            status=GateTransactionStatus.NOT_APPLICABLE,
            gate_id=PlanGateId.FACTS,
            transaction_id="",
        )
    )
    res = run_gate_transaction(
        gate_id=PlanGateId.FACTS, state=None, policy=None, hooks=hooks,
    )
    assert res.status == GateTransactionStatus.NOT_APPLICABLE
    assert hooks.call_log == ["applicable"]
    assert res.transaction_id.startswith("facts-")


def test_not_ready_short_circuits_after_readiness_check():
    hooks = StubHooks(
        ready_result=GateTransactionResult(
            status=GateTransactionStatus.NOT_READY,
            gate_id=PlanGateId.FACTS,
            transaction_id="",
        )
    )
    res = run_gate_transaction(
        gate_id=PlanGateId.FACTS, state=None, policy=None, hooks=hooks,
    )
    assert res.status == GateTransactionStatus.NOT_READY
    assert hooks.call_log == ["applicable", "ready"]


def test_stale_accepted_input_hash_short_circuits_to_accepted():
    """Step 4: if accepted_input_hash matches, gate is already done."""
    accepted = GateTransactionResult(
        status=GateTransactionStatus.ACCEPTED,
        gate_id=PlanGateId.FACTS,
        transaction_id="",
        accepted_input_hash="hash-v1",
    )
    hooks = StubHooks(stale_hash_result=accepted)
    res = run_gate_transaction(
        gate_id=PlanGateId.FACTS, state=None, policy=None, hooks=hooks,
    )
    assert res.status == GateTransactionStatus.ACCEPTED
    # Stale check happens after compute_input_hash, before preflight.
    assert hooks.call_log == ["applicable", "ready", "compute_input_hash", "stale_hash_check"]


def test_preflight_failed_short_circuits_after_preflight():
    """Step 6: deterministic preflight can block the transaction."""
    failed = GateTransactionResult(
        status=GateTransactionStatus.PREFLIGHT_FAILED,
        gate_id=PlanGateId.FACTS,
        transaction_id="",
        issue_code="facts.multi_assembly_contract_incomplete",
    )
    hooks = StubHooks(
        preflight_findings=[
            GateFindingBundle(
                finding_id="f1", code="facts.multi_assembly_contract_incomplete",
                severity="error", category="cross_patch_mismatch",
                message="missing assembly_count",
            )
        ],
        preflight_result=failed,
    )
    res = run_gate_transaction(
        gate_id=PlanGateId.FACTS, state=None, policy=None, hooks=hooks,
    )
    assert res.status == GateTransactionStatus.PREFLIGHT_FAILED
    assert res.issue_code == "facts.multi_assembly_contract_incomplete"
    # Reviewer must NOT run when preflight already failed.
    assert "run_review" not in hooks.call_log


def test_review_failed_short_circuits_after_reviewer():
    """Step 8: independent reviewer can block the transaction."""
    failed = GateTransactionResult(
        status=GateTransactionStatus.REVIEW_FAILED,
        gate_id=PlanGateId.FACTS,
        transaction_id="",
        issue_code="facts.review.coverage_incomplete",
    )
    hooks = StubHooks(review_result=failed)
    res = run_gate_transaction(
        gate_id=PlanGateId.FACTS, state=None, policy=None, hooks=hooks,
    )
    assert res.status == GateTransactionStatus.REVIEW_FAILED


def test_aggregate_findings_preserves_deterministic_metadata():
    """Step 9: deterministic findings reach aggregate without metadata loss."""
    det = GateFindingBundle(
        finding_id="d1", code="facts.assembly_count_inconsistent",
        severity="error", category="cross_patch_mismatch",
        message="expected 9 got 1",
        affected_json_paths=["/assembly_count"],
        expected_value=9, actual_value=1,
        slot_ids=["/assembly_count"],
        source_claim_ids=["claim_9"],
        derivation_codes=["count_from_lattice"],
    )
    rev = GateFindingBundle(
        finding_id="r1", code="facts.fuel_variant_contract_missing",
        severity="error", category="source_coverage",
        message="fuel variants missing",
        affected_json_paths=["/fuel_variant_requirements"],
    )
    hooks = StubHooks(preflight_findings=[det], review_findings=[rev])
    res = run_gate_transaction(
        gate_id=PlanGateId.FACTS, state=None, policy=None, hooks=hooks,
    )
    assert res.status == GateTransactionStatus.ACCEPTED  # default route is APPROVE
    # Both findings carried through to the final result.
    codes = {f.code for f in res.findings}
    assert "facts.assembly_count_inconsistent" in codes
    assert "facts.fuel_variant_contract_missing" in codes


def test_route_action_approve_skips_retry_and_clones():
    """Step 10: APPROVE action does not invoke candidate production."""
    hooks = StubHooks(route_action=PlanReviewAction.APPROVE)
    res = run_gate_transaction(
        gate_id=PlanGateId.FACTS, state=None, policy=None, hooks=hooks,
    )
    assert res.status == GateTransactionStatus.ACCEPTED
    assert "produce_candidate" not in hooks.call_log
    assert "validate_candidate" not in hooks.call_log
    assert "commit_candidate" not in hooks.call_log


def test_revise_action_runs_full_retry_pipeline():
    """Steps 13-18: REVISE_CURRENT_PATCH produces, validates, commits,
    rebuilds downstream, then replays the gate.
    """
    hooks = StubHooks(
        route_action=PlanReviewAction.REVISE_CURRENT_PATCH,
        produce_repair=GateRepairExecutionResult(
            executed=True,
            prior_candidate_hash="c-before",
            candidate_hash="c-after",
        ),
    )
    res = run_gate_transaction(
        gate_id=PlanGateId.FACTS, state=None, policy=None, hooks=hooks,
    )
    assert res.status == GateTransactionStatus.ACCEPTED  # replay default
    expected = [
        "produce_candidate", "validate_candidate", "commit_candidate",
        "rebuild_downstream", "replay_gate",
    ]
    for step in expected:
        assert step in hooks.call_log, f"missing step: {step}"


def test_clone_validation_failure_blocks_commit():
    """Step 14: failed clone validation prevents commit."""
    failed = GateTransactionResult(
        status=GateTransactionStatus.REVIEW_FAILED,
        gate_id=PlanGateId.FACTS,
        transaction_id="",
        issue_code="facts.clone_validation_failed",
    )
    hooks = StubHooks(
        route_action=PlanReviewAction.REVISE_CURRENT_PATCH,
        validate_result=failed,
    )
    res = run_gate_transaction(
        gate_id=PlanGateId.FACTS, state=None, policy=None, hooks=hooks,
    )
    assert res.status == GateTransactionStatus.REVIEW_FAILED
    assert "commit_candidate" not in hooks.call_log


def test_no_progress_short_circuit_when_hashes_match():
    """Step 17: identical candidate + input hash => SAFE_STOP_NO_PROGRESS."""
    hooks = StubHooks(
        route_action=PlanReviewAction.REVISE_CURRENT_PATCH,
        produce_repair=GateRepairExecutionResult(
            executed=True,
            prior_candidate_hash="same",
            candidate_hash="same",
        ),
        # Make input hash unchanged after the round
        input_hash_value="same-input",
    )
    res = run_gate_transaction(
        gate_id=PlanGateId.FACTS, state=None, policy=None, hooks=hooks,
    )
    assert res.status == GateTransactionStatus.SAFE_STOP_NO_PROGRESS


def test_special_route_ask_human_short_circuits_before_retry():
    """Step 11: ASK_HUMAN special route ends the transaction immediately."""
    awaited = GateTransactionResult(
        status=GateTransactionStatus.AWAITING_HUMAN,
        gate_id=PlanGateId.FACTS,
        transaction_id="",
        issue_code="planning.facts_awaiting_human",
    )
    hooks = StubHooks(
        route_action=PlanReviewAction.ASK_HUMAN,
        special_route_result=awaited,
    )
    res = run_gate_transaction(
        gate_id=PlanGateId.FACTS, state=None, policy=None, hooks=hooks,
    )
    assert res.status == GateTransactionStatus.AWAITING_HUMAN
    # build_retry_request must not run when special route handles the action.
    assert "build_retry_request" not in hooks.call_log


def test_transaction_id_propagates_to_short_circuit_result():
    hooks = StubHooks(
        applicability_result=GateTransactionResult(
            status=GateTransactionStatus.NOT_APPLICABLE,
            gate_id=PlanGateId.FACTS,
            transaction_id="",
        )
    )
    res = run_gate_transaction(
        gate_id=PlanGateId.FACTS, state=None, policy=None, hooks=hooks,
    )
    assert res.transaction_id and res.transaction_id.startswith("facts-")
    assert "ctx_input_hash_before" in res.metadata


def test_transaction_handles_unknown_action_as_blocked():
    """Defensive: an action that is neither APPROVE nor REVISE/RETRY and is
    not handled by a special route should land in BLOCKED.
    """
    # FAIL_CLOSED is not in the special routes set in this stub.
    hooks = StubHooks(
        route_action=PlanReviewAction.FAIL_CLOSED,
        # special_route_result stays None to simulate 'not a special route
        # for this stub', so the kernel should reach the unhandled-action
        # fallback.
    )
    res = run_gate_transaction(
        gate_id=PlanGateId.FACTS, state=None, policy=None, hooks=hooks,
    )
    assert res.status == GateTransactionStatus.BLOCKED
    assert "unhandled_action" in res.issue_code


def test_finding_bundle_to_dict_round_trip():
    bundle = GateFindingBundle(
        finding_id="x", code="c", severity="error",
        category="source_coverage", message="m",
        affected_json_paths=["/a"],
        expected_value=1, actual_value=2,
        slot_ids=["s"], source_claim_ids=["c1"], source_span_ids=["s1"],
        derivation_codes=["d"], repair_kind="replace",
        requires_human=True, confidence=0.9,
        metadata={"k": "v"},
    )
    d = bundle.to_dict()
    assert d["finding_id"] == "x"
    assert d["expected_value"] == 1
    assert d["actual_value"] == 2
    assert d["slot_ids"] == ["s"]
    assert d["source_claim_ids"] == ["c1"]
    assert d["requires_human"] is True
    assert d["metadata"] == {"k": "v"}
