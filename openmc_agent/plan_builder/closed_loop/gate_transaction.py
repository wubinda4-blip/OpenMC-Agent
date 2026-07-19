"""Phase 8C Step 1 — Gate Transaction Kernel.

A thin orchestrator that runs one plan gate (Facts / Material-Universe /
Placement / Axial-Geometry / Assembled-Plan) as a single deterministic
transaction with a fixed 20-step order.  The kernel is gate-agnostic;
each gate supplies its own :class:`GateTransactionHooks` adapter.

The kernel exists for two reasons:

1. **One transaction, one place.**  Today every gate has its own inline
   implementation of applicability / readiness / preflight / reviewer /
   action routing / retry / commit / replay.  The kernel gives all gates
   the same skeleton so truth-instrumentation, accepted-input-hash
   replay, finding-metadata preservation, clone validation, and atomic
   commit have exactly one implementation each.

2. **No silent bypasses.**  Phase 8C Step 0 audit
   (``docs/phase8c_step0_facts_truth_audit.md``) found that the Facts
   gate skipped ``accepted_input_hash`` replay, dropped finding metadata
   between preflight and retry request, and had four overlapping repair
   entry points.  Routing every gate through the same 20-step skeleton
   eliminates those structural defects.

Phase 8C Step 1 wires only the Facts gate through the kernel.  Other
gates continue to use their inline paths.  The kernel's contract is
designed so those gates can migrate later without changing the kernel.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any, Protocol

from openmc_agent.plan_builder.closed_loop.models import (
    PlanGateId,
    PlanStageStatus,
    PlanReviewAction,
)


# ---------------------------------------------------------------------------
# Status + result types
# ---------------------------------------------------------------------------


class GateTransactionStatus(str):
    """Outcome of a gate transaction.

    Values:
    - ``NOT_APPLICABLE`` — gate skipped (off mode or non-controlled).
    - ``NOT_READY`` — upstream gate has not accepted yet; come back later.
    - ``PREFLIGHT_FAILED`` — deterministic preflight raised blocking issues.
    - ``REVIEW_FAILED`` — independent reviewer raised blocking issues.
    - ``ACCEPTED`` — gate reached ACCEPTED.
    - ``RETRY_SCHEDULED`` — typed retry request registered, not yet executed.
    - ``RETRY_EXECUTED`` — retry candidate committed; gate will be replayed.
    - ``AWAITING_HUMAN`` — typed question emitted; gate paused.
    - ``BLOCKED`` — gate cannot proceed; caller decides next move.
    - ``SAFE_STOP_NO_PROGRESS`` — same input produced same output; loop stop.
    - ``BUDGET_EXHAUSTED`` — retry / llm / repair budget exhausted.
    """

    NOT_APPLICABLE = "not_applicable"
    NOT_READY = "not_ready"
    PREFLIGHT_FAILED = "preflight_failed"
    REVIEW_FAILED = "review_failed"
    ACCEPTED = "accepted"
    RETRY_SCHEDULED = "retry_scheduled"
    RETRY_EXECUTED = "retry_executed"
    AWAITING_HUMAN = "awaiting_human"
    BLOCKED = "blocked"
    SAFE_STOP_NO_PROGRESS = "safe_stop_no_progress"
    BUDGET_EXHAUSTED = "budget_exhausted"


@dataclass
class GateFindingBundle:
    """Lossless finding carrier.

    Carries every deterministic preflight finding and every reviewer
    finding from production to consumption without dropping metadata.
    Phase 8C Step 0 audit found that the inline Facts gate kept only
    ``code + message`` after the lift into ``PlanReviewFinding``; this
    bundle is the structural fix.

    Fields mirror :class:`PlanReviewFinding` and add the deterministic
    metadata that the Facts consistency preflight produces.
    """

    finding_id: str
    code: str
    severity: str
    category: str
    message: str
    affected_patch_types: list[str] = field(default_factory=list)
    affected_json_paths: list[str] = field(default_factory=list)
    expected_value: Any | None = None
    actual_value: Any | None = None
    slot_ids: list[str] = field(default_factory=list)
    source_claim_ids: list[str] = field(default_factory=list)
    source_span_ids: list[str] = field(default_factory=list)
    derivation_codes: list[str] = field(default_factory=list)
    repair_kind: str = ""
    requires_human: bool = False
    confidence: float = 1.0
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "finding_id": self.finding_id,
            "code": self.code,
            "severity": self.severity,
            "category": self.category,
            "message": self.message,
            "affected_patch_types": list(self.affected_patch_types),
            "affected_json_paths": list(self.affected_json_paths),
            "expected_value": self.expected_value,
            "actual_value": self.actual_value,
            "slot_ids": list(self.slot_ids),
            "source_claim_ids": list(self.source_claim_ids),
            "source_span_ids": list(self.source_span_ids),
            "derivation_codes": list(self.derivation_codes),
            "repair_kind": self.repair_kind,
            "requires_human": self.requires_human,
            "confidence": self.confidence,
            "metadata": dict(self.metadata),
        }


@dataclass
class GateRepairExecutionResult:
    """Outcome of executing a retry / repair within the transaction."""

    executed: bool
    candidate_hash: str = ""
    prior_candidate_hash: str = ""
    candidate_committed: bool = False
    replay_required: bool = False
    no_progress: bool = False
    llm_calls: int = 0
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class GateTransactionInput:
    """What the gate consumes — supplied by the caller, not the hooks."""

    requirement_text: str = ""
    confirmed_facts: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class GateTransactionContext:
    """Run-time context shared across all hooks in one transaction."""

    gate_id: PlanGateId
    transaction_id: str
    input_hash_before: str = ""
    input_hash_after: str = ""
    candidate_hash_before: str = ""
    candidate_hash_after: str = ""
    reviewer_rerun: bool = False
    downstream_recompiled: bool = False
    stages_reopened: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class GateTransactionResult:
    """What :func:`run_gate_transaction` returns to the caller."""

    status: str
    gate_id: PlanGateId
    transaction_id: str
    issue_code: str = ""
    issue_message: str = ""
    issue_patch_type: str | None = None
    issue_patch_id: str | None = None
    issue_path: str | None = None
    findings: list[GateFindingBundle] = field(default_factory=list)
    accepted_input_hash: str = ""
    accepted_candidate_hash: str = ""
    repair: GateRepairExecutionResult | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def is_terminal_accepted(self) -> bool:
        return self.status == GateTransactionStatus.ACCEPTED

    @property
    def is_blocking(self) -> bool:
        """A result that should make the caller stop the patch loop."""
        return self.status in (
            GateTransactionStatus.PREFLIGHT_FAILED,
            GateTransactionStatus.REVIEW_FAILED,
            GateTransactionStatus.AWAITING_HUMAN,
            GateTransactionStatus.BLOCKED,
            GateTransactionStatus.SAFE_STOP_NO_PROGRESS,
            GateTransactionStatus.BUDGET_EXHAUSTED,
        )


# ---------------------------------------------------------------------------
# Hooks protocol
# ---------------------------------------------------------------------------


class GateTransactionHooks(Protocol):
    """Per-gate adapter.

    The kernel calls these methods in a fixed order.  Each method returns
    either a short-circuit :class:`GateTransactionResult` (which ends the
    transaction immediately) or ``None`` (which lets the kernel continue).

    Implementations may mutate ``state`` and ``ctx`` in place — the
    kernel does not clone between hooks.  Clone validation happens inside
    :meth:`validate_candidate` on a state copy produced by
    :meth:`produce_candidate`.
    """

    gate_id: PlanGateId

    def applicable(self, *, state: Any, policy: Any, inp: GateTransactionInput) -> GateTransactionResult | None:
        """Return NOT_APPLICABLE if the gate should be skipped."""

    def ready(self, *, state: Any, policy: Any, ctx: GateTransactionContext) -> GateTransactionResult | None:
        """Return NOT_READY if upstream gates have not accepted yet."""

    def compute_input_hash(self, *, state: Any, policy: Any) -> str:
        """Deterministic hash of all inputs the gate consumes."""

    def stale_hash_check(
        self,
        *,
        state: Any,
        policy: Any,
        ctx: GateTransactionContext,
    ) -> GateTransactionResult | None:
        """Return ACCEPTED short-circuit if accepted_input_hash matches."""

    def compile_requirement_contract(
        self,
        *,
        state: Any,
        policy: Any,
        ctx: GateTransactionContext,
    ) -> GateTransactionResult | None:
        """Compile the requirement contract (e.g. planning_feature_contract)."""

    def run_preflight(
        self,
        *,
        state: Any,
        policy: Any,
        ctx: GateTransactionContext,
    ) -> tuple[list[GateFindingBundle], GateTransactionResult | None]:
        """Deterministic preflight.  Returns findings + optional short-circuit."""

    def build_evidence_pack(
        self,
        *,
        state: Any,
        policy: Any,
        ctx: GateTransactionContext,
    ) -> GateTransactionResult | None:
        """Build the reviewer evidence pack."""

    def run_review(
        self,
        *,
        state: Any,
        policy: Any,
        ctx: GateTransactionContext,
    ) -> tuple[list[GateFindingBundle], GateTransactionResult | None]:
        """Independent reviewer.  Returns findings + optional short-circuit."""

    def aggregate_findings(
        self,
        *,
        state: Any,
        policy: Any,
        ctx: GateTransactionContext,
        deterministic_findings: list[GateFindingBundle],
        review_findings: list[GateFindingBundle],
    ) -> list[GateFindingBundle]:
        """Merge deterministic + reviewer findings without losing metadata."""

    def route_finding(
        self,
        *,
        state: Any,
        policy: Any,
        ctx: GateTransactionContext,
        findings: list[GateFindingBundle],
    ) -> tuple[PlanReviewAction, GateFindingBundle | None, GateTransactionResult | None]:
        """Decide the next action (APPROVE / REVISE / RETRY / ASK_HUMAN / FAIL_CLOSED)."""

    def execute_special_route(
        self,
        *,
        state: Any,
        policy: Any,
        ctx: GateTransactionContext,
        action: PlanReviewAction,
        primary_finding: GateFindingBundle | None,
    ) -> GateTransactionResult | None:
        """Execute RETRIEVE_EVIDENCE / ASK_HUMAN / FAIL_CLOSED routes.

        Returns None if the action is not a special route.
        """

    def build_retry_request(
        self,
        *,
        state: Any,
        policy: Any,
        ctx: GateTransactionContext,
        action: PlanReviewAction,
        findings: list[GateFindingBundle],
    ) -> GateTransactionResult | None:
        """Build (and register) a typed retry request for REVISE/RETRY actions."""

    def produce_candidate(
        self,
        *,
        state: Any,
        policy: Any,
        ctx: GateTransactionContext,
        action: PlanReviewAction,
        findings: list[GateFindingBundle],
    ) -> tuple[Any, GateRepairExecutionResult]:
        """Produce a repair candidate on a state clone.

        Returns (clone_state, repair_result).  The clone is later passed
        to :meth:`validate_candidate` and :meth:`commit_candidate`.
        """

    def validate_candidate(
        self,
        *,
        state: Any,
        policy: Any,
        ctx: GateTransactionContext,
        clone_state: Any,
        repair_result: GateRepairExecutionResult,
    ) -> GateTransactionResult | None:
        """Run schema + preflight + reviewer + inventory on the clone.

        Returns None if the candidate passes; otherwise a short-circuit
        result (typically RETRY_SCHEDULED for a new attempt, or
        SAFE_STOP_NO_PROGRESS for a repeated candidate).
        """

    def commit_candidate(
        self,
        *,
        state: Any,
        policy: Any,
        ctx: GateTransactionContext,
        clone_state: Any,
        repair_result: GateRepairExecutionResult,
    ) -> GateTransactionResult | None:
        """Atomic owner commit.  Returns None on success."""

    def rebuild_downstream(
        self,
        *,
        state: Any,
        policy: Any,
        ctx: GateTransactionContext,
    ) -> GateTransactionResult | None:
        """Recompile canonical task plan + Inventory + requirement sets."""

    def replay_gate(
        self,
        *,
        state: Any,
        policy: Any,
        ctx: GateTransactionContext,
    ) -> GateTransactionResult:
        """Re-run preflight + reviewer after commit; reclassify the outcome."""


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------


def run_gate_transaction(
    *,
    gate_id: PlanGateId,
    state: Any,
    policy: Any,
    hooks: GateTransactionHooks,
    inp: GateTransactionInput | None = None,
) -> GateTransactionResult:
    """Run one gate as a fixed 20-step transaction.

    The 20 steps correspond exactly to the Phase 8C Step 1 charter
    section 4 transaction order.  Each step either advances the
    transaction or short-circuits with a terminal status.
    """
    inp = inp or GateTransactionInput()
    ctx = GateTransactionContext(
        gate_id=gate_id,
        transaction_id=f"{gate_id.value}-{uuid.uuid4().hex[:12]}",
    )

    # Step 1: applicability
    res = hooks.applicable(state=state, policy=policy, inp=inp)
    if res is not None:
        return _finalize(res, ctx)

    # Step 2: readiness
    res = hooks.ready(state=state, policy=policy, ctx=ctx)
    if res is not None:
        return _finalize(res, ctx)

    # Step 3: input hash
    ctx.input_hash_before = hooks.compute_input_hash(state=state, policy=policy)
    ctx.input_hash_after = ctx.input_hash_before

    # Step 4: stale accepted/reviewed hash detection
    res = hooks.stale_hash_check(state=state, policy=policy, ctx=ctx)
    if res is not None:
        return _finalize(res, ctx)

    # Step 5: requirement contract compilation
    res = hooks.compile_requirement_contract(state=state, policy=policy, ctx=ctx)
    if res is not None:
        return _finalize(res, ctx)

    # Step 6: deterministic preflight
    deterministic_findings, res = hooks.run_preflight(state=state, policy=policy, ctx=ctx)
    if res is not None:
        return _finalize(res, ctx)
    ctx.metadata = {**ctx.metadata, "deterministic_finding_count": len(deterministic_findings)}

    # Step 7: evidence pack
    res = hooks.build_evidence_pack(state=state, policy=policy, ctx=ctx)
    if res is not None:
        return _finalize(res, ctx)

    # Step 8: independent reviewer
    review_findings, res = hooks.run_review(state=state, policy=policy, ctx=ctx)
    if res is not None:
        return _finalize(res, ctx)

    # Step 9: finding aggregation
    all_findings = hooks.aggregate_findings(
        state=state, policy=policy, ctx=ctx,
        deterministic_findings=deterministic_findings,
        review_findings=review_findings,
    )

    # Step 10: deterministic action routing
    action, primary_finding, res = hooks.route_finding(
        state=state, policy=policy, ctx=ctx, findings=all_findings,
    )
    if res is not None:
        return _finalize(res, ctx)

    # Step 11: special route execution (RETRIEVE_EVIDENCE / ASK_HUMAN / FAIL_CLOSED)
    res = hooks.execute_special_route(
        state=state, policy=policy, ctx=ctx,
        action=action, primary_finding=primary_finding,
    )
    if res is not None:
        return _finalize(res, ctx)

    # Step 12: typed retry request (for REVISE / RETRY actions)
    res = hooks.build_retry_request(
        state=state, policy=policy, ctx=ctx,
        action=action, findings=all_findings,
    )
    if res is not None:
        return _finalize(res, ctx)

    # Steps 13–16: candidate production + validation + commit + downstream rebuild
    if action in (PlanReviewAction.REVISE_CURRENT_PATCH, PlanReviewAction.RETRY_DEPENDENCY):
        clone_state, repair_result = hooks.produce_candidate(
            state=state, policy=policy, ctx=ctx,
            action=action, findings=all_findings,
        )
        ctx.candidate_hash_before = repair_result.prior_candidate_hash
        ctx.candidate_hash_after = repair_result.candidate_hash

        # Step 14: clone validation
        res = hooks.validate_candidate(
            state=state, policy=policy, ctx=ctx,
            clone_state=clone_state, repair_result=repair_result,
        )
        if res is not None:
            return _finalize(res, ctx)

        # Step 15: atomic owner commit
        res = hooks.commit_candidate(
            state=state, policy=policy, ctx=ctx,
            clone_state=clone_state, repair_result=repair_result,
        )
        if res is not None:
            return _finalize(res, ctx)
        ctx.reviewer_rerun = True

        # Step 16: downstream invalidation + rebuild
        res = hooks.rebuild_downstream(state=state, policy=policy, ctx=ctx)
        if res is not None:
            return _finalize(res, ctx)

        # Step 17: input hash recompute
        ctx.input_hash_after = hooks.compute_input_hash(state=state, policy=policy)
        if ctx.input_hash_after == ctx.input_hash_before and ctx.candidate_hash_after == ctx.candidate_hash_before:
            return _finalize(
                GateTransactionResult(
                    status=GateTransactionStatus.SAFE_STOP_NO_PROGRESS,
                    gate_id=gate_id,
                    transaction_id=ctx.transaction_id,
                    issue_code=f"planning.{gate_id.value}_no_progress",
                    issue_message=(
                        f"{gate_id.value} gate produced identical candidate hash "
                        f"and input hash; stopping to avoid infinite loop."
                    ),
                    findings=all_findings,
                ),
                ctx,
            )

        # Step 18: authoritative gate replay
        replay_res = hooks.replay_gate(state=state, policy=policy, ctx=ctx)
        replay_res.findings = list(replay_res.findings) + list(all_findings)
        return _finalize(replay_res, ctx)

    # Step 17–18: for APPROVE / no-action routes, finalize here.
    if action == PlanReviewAction.APPROVE:
        # Step 16: downstream rebuild on accept.
        res = hooks.rebuild_downstream(state=state, policy=policy, ctx=ctx)
        if res is not None:
            return _finalize(res, ctx)
        return _finalize(
            GateTransactionResult(
                status=GateTransactionStatus.ACCEPTED,
                gate_id=gate_id,
                transaction_id=ctx.transaction_id,
                accepted_input_hash=ctx.input_hash_before,
                findings=all_findings,
            ),
            ctx,
        )

    # Action routed to something we did not handle (should not happen —
    # special routes cover the other cases).
    return _finalize(
        GateTransactionResult(
            status=GateTransactionStatus.BLOCKED,
            gate_id=gate_id,
            transaction_id=ctx.transaction_id,
            issue_code=f"planning.{gate_id.value}_unhandled_action",
            issue_message=f"Gate transaction cannot handle action={action}",
            findings=all_findings,
        ),
        ctx,
    )


def _finalize(res: GateTransactionResult, ctx: GateTransactionContext) -> GateTransactionResult:
    """Attach the transaction id and any non-final metadata."""
    if not res.transaction_id:
        res.transaction_id = ctx.transaction_id
    res.metadata.setdefault("ctx_input_hash_before", ctx.input_hash_before)
    res.metadata.setdefault("ctx_input_hash_after", ctx.input_hash_after)
    res.metadata.setdefault("ctx_candidate_hash_before", ctx.candidate_hash_before)
    res.metadata.setdefault("ctx_candidate_hash_after", ctx.candidate_hash_after)
    res.metadata.setdefault("ctx_reviewer_rerun", ctx.reviewer_rerun)
    res.metadata.setdefault("ctx_downstream_recompiled", ctx.downstream_recompiled)
    res.metadata.setdefault("ctx_stages_reopened", list(ctx.stages_reopened))
    return res


__all__ = [
    "GateTransactionStatus",
    "GateFindingBundle",
    "GateRepairExecutionResult",
    "GateTransactionInput",
    "GateTransactionContext",
    "GateTransactionResult",
    "GateTransactionHooks",
    "run_gate_transaction",
]
