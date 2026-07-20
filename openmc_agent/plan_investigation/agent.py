"""LLM-orchestrated investigation agent (Phase 8A Step 3).

Controlled, opt-in investigation stage that runs BEFORE patch generation
when ``PlanInvestigationConfig.enabled`` is True.  The agent asks an LLM
which read-only tools to invoke, executes those tools via the existing
:class:`InvestigationToolRegistry`, and records new evidence in the
ledger.

Hard rules enforced here:

* The LLM NEVER calls tools directly.  It emits strict JSON
  ``{"actions": [{"tool": ..., "arguments": {...}}]}``; Python parses
  and dispatches.
* Budget violations are blocking (``InvestigationResult.blocked=True``).
* Tool failures are non-blocking (a warning is recorded, the agent
  continues with the remaining actions).
* The agent returns evidence claim ids and a structured summary; it
  does NOT modify the supplied :class:`PlanBuildState` and does NOT
  build patches.
"""

from __future__ import annotations

import json
import re
from typing import Any, Callable

from pydantic import ConfigDict, Field, PrivateAttr, model_validator

from openmc_agent.schemas import AgentBaseModel
from openmc_agent.structured_output import (
    StructuredOutputRepairPrompt,
    canonical_payload_hash,
    StructuredOutputResult,
    run_structured_output_transaction,
)

from .semantic_coverage import compile_semantic_coverage

from .errors import PlanInvestigationIssue
from .evidence_ledger import (
    PlanningEvidenceLedger,
    get_claim_by_id,
)
from .hashing import content_hash, short_id
from .models import EvidenceClaim, SourceKind
from .source_index import SourceIndex
from .tool_artifacts import (
    ToolCallLedger,
    ToolCallRecord,
    record_tool_call,
)
from .tool_models import (
    InvestigationToolRequest,
    InvestigationToolResult,
    InvestigationToolSpec,
)
from .tool_registry import (
    InvestigationToolRegistry,
    ToolExecutionContext,
)

# Forward-declared type for the baseline policy.  Imported lazily inside
# _resolve_baseline_policy to avoid a circular import.
InvestigationBaselinePolicy = Any

__all__ = [
    "InvestigationBudget",
    "InvestigationBudgetUsage",
    "InvestigationContext",
    "InvestigationAction",
    "InvestigationPlan",
    "InvestigationResult",
    "InvestigationAgent",
    "BLOCK_CODE_BUDGET_EXCEEDED",
    "BLOCK_CODE_INVALID_LLM_OUTPUT",
    "BLOCK_CODE_UNKNOWN_TOOL",
    "BLOCK_CODE_ARGUMENT_INVALID",
]


# ---------------------------------------------------------------------------
# Stable block codes
# ---------------------------------------------------------------------------


BLOCK_CODE_BUDGET_EXCEEDED = "planning.investigation_budget_exceeded"
BLOCK_CODE_INVALID_LLM_OUTPUT = "planning.investigation_invalid_llm_output"
BLOCK_CODE_UNKNOWN_TOOL = "planning.investigation_unknown_tool"
BLOCK_CODE_ARGUMENT_INVALID = "planning.investigation_argument_invalid"
MAX_PLANNER_CALLS = 2


# ---------------------------------------------------------------------------
# Budget
# ---------------------------------------------------------------------------


class InvestigationBudget(AgentBaseModel):
    """Per-session investigation budget.

    Defaults align with the Step 3 spec: 5 tool calls, 50 results per
    tool, 100 evidence claims.  Budget violations are blocking.
    """

    max_tool_calls: int = Field(default=5, ge=0, le=50)
    max_results_per_tool: int = Field(default=50, ge=1, le=500)
    max_evidence_claims: int = Field(default=100, ge=0, le=1000)


class InvestigationBudgetUsage(AgentBaseModel):
    """Snapshot of budget consumed so far in one investigation session."""

    tool_calls: int = 0
    evidence_claims: int = 0

    def exceeds(self, budget: InvestigationBudget) -> bool:
        return (
            self.tool_calls > budget.max_tool_calls
            or self.evidence_claims > budget.max_evidence_claims
        )


# ---------------------------------------------------------------------------
# Context / plan / result
# ---------------------------------------------------------------------------


class InvestigationContext(AgentBaseModel):
    """Inputs to one :meth:`InvestigationAgent.run` call.

    ``source_indexes`` and ``ledger`` are passed by reference; the agent
    adds new evidence claims to the ledger but does NOT mutate any
    :class:`SourceIndex` (tools may register spans inside an index, but
    that is idempotent).

    Phase 8A Step 6 (P0-5 fix): typed optional fields ``accepted_facts``,
    ``geometry_inventory``, ``material_requirement_set``,
    ``universe_requirement_set`` give the Materials/Universes baseline
    resolver access to the inventory context that the previous
    implementation hardcoded to ``None``.  These fields are populated
    only by :func:`run_patch_investigation_stage`; legacy callers that
    build an ``InvestigationContext`` directly (tests, the Facts-only
    path) are unaffected.
    """

    model_config = ConfigDict(arbitrary_types_allowed=True)

    requirement_text: str
    patch_type: str
    available_tools: tuple[InvestigationToolSpec, ...] = Field(default_factory=tuple)
    existing_evidence: tuple[EvidenceClaim, ...] = Field(default_factory=tuple)
    budget: InvestigationBudget = Field(default_factory=InvestigationBudget)
    source_indexes: dict[str, SourceIndex] = Field(default_factory=dict)
    ledger: PlanningEvidenceLedger
    policy_suggestions: tuple[str, ...] = Field(default_factory=tuple)
    caller_stage: str = "investigation"
    # Phase 8A Step 6 (P0-5): typed inventory context.  ``Any`` typing
    # avoids a circular import with the inventory / requirement-set
    # modules; the baseline resolver duck-types these objects.
    accepted_facts: Any = None
    geometry_inventory: Any = None
    material_requirement_set: Any = None
    universe_requirement_set: Any = None
    feature_contract: Any = None

    @property
    def requirement_excerpt(self) -> str:
        """Return up to 2 KB of the requirement text for the LLM prompt.

        The full requirement is preserved in the source index; the LLM
        only needs an excerpt plus the evidence context to decide which
        tools to call.
        """

        if len(self.requirement_text) <= 2048:
            return self.requirement_text
        return self.requirement_text[:2048] + "\n... [truncated]"


class InvestigationAction(AgentBaseModel):
    """One LLM-requested tool invocation."""

    tool: str
    arguments: dict[str, Any] = Field(default_factory=dict)


class InvestigationPlan(AgentBaseModel):
    """Parsed LLM output: a list of actions + optional summary.

    The summary is audit text only; it does not affect deterministic
    execution.
    """

    actions: tuple[InvestigationAction, ...] = Field(default_factory=tuple)
    summary: str = ""

    @model_validator(mode="after")
    def _validate_action_contract(self) -> "InvestigationPlan":
        for action in self.actions:
            if not action.tool.strip():
                raise ValueError("investigation action tool must be non-empty")
            if not isinstance(action.arguments, dict):
                raise ValueError("investigation action arguments must be an object")
        return self


class InvestigationResult(AgentBaseModel):
    """Outcome of one :meth:`InvestigationAgent.run` call."""

    session_id: str
    patch_type: str
    tool_calls: tuple[ToolCallRecord, ...] = Field(default_factory=tuple)
    tool_results: tuple[InvestigationToolResult, ...] = Field(default_factory=tuple)
    evidence_claim_ids: tuple[str, ...] = Field(default_factory=tuple)
    summary: str = ""
    completed: bool = False
    blocked: bool = False
    block_code: str | None = None
    block_message: str | None = None
    budget: InvestigationBudget = Field(default_factory=InvestigationBudget)
    budget_used: InvestigationBudgetUsage = Field(default_factory=InvestigationBudgetUsage)
    warnings: tuple[str, ...] = Field(default_factory=tuple)
    planner_calls: int = 0
    schema_retries: int = 0
    planner_input_payload_hash: str = ""
    semantic_coverage: dict[str, Any] = Field(default_factory=dict)
    skipped_actions: tuple[str, ...] = Field(default_factory=tuple)
    skipped_action_reason: str | None = None
    structured_output_payload_hash_drift: bool = False
    structured_output_unbudgeted_retry: bool = False
    structured_output_stale_output_reused: bool = False
    result_hash: str = ""

    @model_validator(mode="after")
    def _compute_result_hash(self) -> "InvestigationResult":
        payload = {
            "session_id": self.session_id,
            "patch_type": self.patch_type,
            "tool_calls": [tc.model_dump(mode="json") for tc in self.tool_calls],
            "evidence_claim_ids": list(self.evidence_claim_ids),
            "blocked": self.blocked,
            "block_code": self.block_code,
            "completed": self.completed,
            "budget_used": self.budget_used.model_dump(mode="json"),
        }
        if self.planner_calls or self.schema_retries or self.planner_input_payload_hash or self.semantic_coverage or self.skipped_actions or self.skipped_action_reason or self.structured_output_payload_hash_drift or self.structured_output_unbudgeted_retry or self.structured_output_stale_output_reused:
            payload.update(
                {
                    "planner_calls": self.planner_calls,
                    "schema_retries": self.schema_retries,
                    "planner_input_payload_hash": self.planner_input_payload_hash,
                    "semantic_coverage": self.semantic_coverage,
                    "skipped_actions": list(self.skipped_actions),
                    "skipped_action_reason": self.skipped_action_reason,
                    "structured_output_payload_hash_drift": self.structured_output_payload_hash_drift,
                    "structured_output_unbudgeted_retry": self.structured_output_unbudgeted_retry,
                    "structured_output_stale_output_reused": self.structured_output_stale_output_reused,
                }
            )
        expected = content_hash(payload)
        if not self.result_hash:
            object.__setattr__(self, "result_hash", expected)
        elif self.result_hash != expected:
            raise PlanInvestigationIssue(
                "plan_investigation.result_hash_mismatch",
                "result_hash does not match the recomputed value",
                details={"expected": expected, "actual": self.result_hash},
            )
        return self


# ---------------------------------------------------------------------------
# Agent
# ---------------------------------------------------------------------------


# Type alias for the LLM callable.  Same shape as patch_generator's
# ``llm_client``: takes a prompt string, returns a string (which must be
# strict JSON for this agent).
InvestigationLLMClient = Callable[[str], str]


class InvestigationAgent:
    """ Orchestrates LLM-driven tool calls against the investigation
    registry.

    The agent is stateless across runs: every ``run()`` builds a fresh
    :class:`ToolCallLedger` and a fresh ``session_id``.  Callers must
    not cache agent instances across plans.
    """

    def __init__(
        self,
        *,
        registry: InvestigationToolRegistry,
        llm_client: InvestigationLLMClient,
    ) -> None:
        self.registry = registry
        self.llm_client = llm_client
        self.last_plan_transaction: StructuredOutputResult | None = None

    # ------------------------------------------------------------------
    # Public surface
    # ------------------------------------------------------------------

    def run(self, context: InvestigationContext) -> InvestigationResult:
        session_id = short_id(
            "inv",
            {
                "patch_type": context.patch_type,
                "requirement_hash": content_hash(context.requirement_text),
                "ledger_hash": content_hash(context.ledger.ledger_hash),
            },
        )

        tool_ledger = ToolCallLedger()
        tool_results: list[InvestigationToolResult] = []
        evidence_claim_ids: list[str] = []
        warnings: list[str] = []
        skipped_actions: list[str] = []
        skipped_action_reason: str | None = None
        usage = InvestigationBudgetUsage()

        # Phase 8A Step 5: execute the mandatory baseline BEFORE the LLM.
        baseline_policy = _resolve_baseline_policy(context)
        if baseline_policy is not None:
            for action in baseline_policy.actions:
                if usage.tool_calls >= context.budget.max_tool_calls:
                    return self._block(
                        session_id=session_id,
                        context=context,
                        code=BLOCK_CODE_BUDGET_EXCEEDED,
                        message=f"max_tool_calls={context.budget.max_tool_calls} reached during mandatory baseline",
                        tool_ledger=tool_ledger,
                        tool_results=tool_results,
                        evidence_claim_ids=evidence_claim_ids,
                        usage=usage,
                        warnings=warnings,
                    )
                mandatory_result = self._execute_action(
                    action.tool_name,
                    action.arguments,
                    context,
                    tool_ledger,
                    tool_results,
                    evidence_claim_ids,
                    warnings,
                    usage,
                    session_id,
                )
                if mandatory_result is not None and not mandatory_result.ok:
                    warnings.append(f"mandatory action {action.tool_name} returned ok=False")

        coverage = compile_semantic_coverage(
            context=context, ledger=context.ledger, evidence_claim_ids=evidence_claim_ids
        )
        try:
            plan = self.plan(context)
        except PlanInvestigationIssue as issue:
            return self._block(
                session_id=session_id,
                context=context,
                code=issue.code,
                message=issue.message,
                tool_ledger=tool_ledger,
                tool_results=tool_results,
                evidence_claim_ids=evidence_claim_ids,
                usage=usage,
                warnings=warnings,
                semantic_coverage=coverage.to_dict(),
            )

        blocked_code: str | None = None
        blocked_message: str | None = None

        for index, action in enumerate(plan.actions):
            coverage = compile_semantic_coverage(
                context=context, ledger=context.ledger, evidence_claim_ids=evidence_claim_ids
            )
            if coverage.coverage_complete:
                skipped_action_reason = "skipped_after_coverage_complete"
                skipped_actions.extend(item.tool for item in plan.actions[index:])
                break
            if usage.tool_calls >= context.budget.max_tool_calls:
                blocked_code = BLOCK_CODE_BUDGET_EXCEEDED
                blocked_message = f"max_tool_calls={context.budget.max_tool_calls} reached"
                break

            request = InvestigationToolRequest(
                tool_name=action.tool,
                arguments=dict(action.arguments),
                max_results=context.budget.max_results_per_tool,
                caller_stage=context.caller_stage,
            )
            try:
                self.registry.get(action.tool)
            except PlanInvestigationIssue as issue:
                blocked_code = BLOCK_CODE_UNKNOWN_TOOL
                blocked_message = issue.message
                break
            validation = self.registry.validate_arguments(action.tool, action.arguments)
            if validation:
                blocked_code = BLOCK_CODE_ARGUMENT_INVALID
                blocked_message = "; ".join(issue.message for issue in validation)
                break

            usage.tool_calls += 1
            exec_context = ToolExecutionContext(
                source_indexes=context.source_indexes,
                ledger=context.ledger,
            )
            try:
                tool_result = self.registry.execute(
                    action.tool, request, context=exec_context
                )
            except PlanInvestigationIssue as issue:
                warnings.append(f"tool {action.tool} raised {issue.code}: {issue.message}")
                tool_result = InvestigationToolResult(
                    ok=False,
                    tool_name=action.tool,
                    result={"error_code": issue.code},
                    error_codes=(issue.code,),
                    warnings=(issue.message,),
                )

            tool_results.append(tool_result)
            record_tool_call(
                tool_ledger,
                tool_name=action.tool,
                arguments=action.arguments,
                result=tool_result,
                caller_stage=context.caller_stage,
            )
            for claim_id in tool_result.evidence_claim_ids:
                if claim_id not in evidence_claim_ids:
                    evidence_claim_ids.append(claim_id)
            usage.evidence_claims = len(evidence_claim_ids)
            coverage = compile_semantic_coverage(
                context=context, ledger=context.ledger, evidence_claim_ids=evidence_claim_ids
            )
            if coverage.coverage_complete:
                skipped_action_reason = "skipped_after_coverage_complete"
                skipped_actions.extend(item.tool for item in plan.actions[index + 1 :])
                break
            if usage.exceeds(context.budget):
                blocked_code = BLOCK_CODE_BUDGET_EXCEEDED
                blocked_message = (
                    f"max_evidence_claims={context.budget.max_evidence_claims} exceeded"
                )
                break

        transaction = self.last_plan_transaction
        structured_payload_hash_drift = bool(
            transaction and transaction.error_code == "structured_output.payload_hash_mismatch"
        )
        structured_unbudgeted_retry = bool(
            transaction
            and any(not attempt.budget_charged for attempt in transaction.attempts)
        )
        structured_stale_output_reused = bool(
            transaction
            and any(
                "stale_output_reused" in attempt.parse_errors
                for attempt in transaction.attempts
            )
        )
        completed = blocked_code is None
        return InvestigationResult(
            session_id=session_id,
            patch_type=context.patch_type,
            tool_calls=tuple(tool_ledger.records),
            tool_results=tuple(tool_results),
            evidence_claim_ids=tuple(evidence_claim_ids),
            summary=plan.summary if blocked_code is None else "",
            completed=completed,
            blocked=blocked_code is not None,
            block_code=blocked_code,
            block_message=blocked_message,
            budget=context.budget,
            budget_used=usage,
            warnings=tuple(warnings),
            planner_calls=transaction.call_count if transaction else 0,
            schema_retries=transaction.schema_retry_count if transaction else 0,
            planner_input_payload_hash=transaction.input_payload_hash if transaction else "",
            semantic_coverage=coverage.to_dict(),
            skipped_actions=tuple(skipped_actions),
            skipped_action_reason=skipped_action_reason,
            structured_output_payload_hash_drift=structured_payload_hash_drift,
            structured_output_unbudgeted_retry=structured_unbudgeted_retry,
            structured_output_stale_output_reused=structured_stale_output_reused,
        )

    # ------------------------------------------------------------------
    # LLM interaction
    # ------------------------------------------------------------------

    def plan(self, context: InvestigationContext) -> InvestigationPlan:
        """Build and validate the plan through the shared output transaction."""

        from .prompt import build_investigation_prompt

        prompt = build_investigation_prompt(context)
        payload = {
            "requirement_hash": content_hash(context.requirement_text),
            "patch_type": context.patch_type,
            "tool_names": [tool.name for tool in context.available_tools],
            "existing_claim_ids": [claim.claim_id for claim in context.existing_evidence],
            "policy_suggestions": list(context.policy_suggestions),
            "budget": context.budget.model_dump(mode="json"),
        }

        def _repair(raw: str, error: str) -> StructuredOutputRepairPrompt:
            repair_prompt = (
                f"{prompt}\n\n"
                "The previous response failed the investigation action schema. "
                "Return one JSON object with an actions array and optional summary. "
                "Do not add prose or unknown top-level fields.\n"
                f"Validation error: {error}\n"
                f"Previous output: {raw[:4000]}"
            )
            return StructuredOutputRepairPrompt(
                prompt=repair_prompt,
                input_payload_hash=canonical_payload_hash(payload),
            )

        def _normalize(candidate: dict[str, Any]) -> dict[str, Any]:
            if isinstance(candidate, list):
                return {"actions": candidate}
            return candidate

        planner_budget_used = [0]

        def _planner_budget_available() -> bool:
            return planner_budget_used[0] < MAX_PLANNER_CALLS

        def _charge_planner_budget() -> None:
            planner_budget_used[0] += 1

        def _planner_call(client: Any, current_prompt: str) -> Any:
            if hasattr(client, "generate_patch_json"):
                return client.generate_patch_json(
                    prompt=current_prompt,
                    patch_type="investigation_plan",
                    json_schema=InvestigationPlan.model_json_schema(),
                )
            return client(current_prompt)

        transaction = run_structured_output_transaction(
            client=self.llm_client,
            initial_prompt=prompt,
            retry_prompt_builder=_repair,
            output_model=InvestigationPlan,
            call=_planner_call,
            payload=payload,
            normalize_candidate=_normalize,
            max_attempts=2,
            allow_embedded_json=True,
            allow_top_level_array=True,
            budget_available=_planner_budget_available,
            charge_budget=_charge_planner_budget,
        )
        self.last_plan_transaction = transaction
        if not transaction.ok or transaction.parsed_output is None:
            details = {
                "error_code": transaction.error_code,
                "input_payload_hash": transaction.input_payload_hash,
            }
            if transaction.attempts:
                latest_attempt = transaction.attempts[-1]
                details["raw_hash"] = latest_attempt.raw_hash
                details["parse_errors"] = list(latest_attempt.parse_errors)
                details["schema_errors"] = list(latest_attempt.schema_errors)
            raise PlanInvestigationIssue(
                BLOCK_CODE_INVALID_LLM_OUTPUT,
                "investigation LLM output was not valid JSON matching the action schema",
                details=details,
            )
        return InvestigationPlan.model_validate(transaction.parsed_output)

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _block(
        self,
        *,
        session_id: str,
        context: InvestigationContext,
        code: str,
        message: str,
        tool_ledger: ToolCallLedger | None = None,
        tool_results: list[InvestigationToolResult] | None = None,
        evidence_claim_ids: list[str] | None = None,
        usage: InvestigationBudgetUsage | None = None,
        warnings: list[str] | None = None,
        semantic_coverage: dict[str, Any] | None = None,
    ) -> InvestigationResult:
        transaction = self.last_plan_transaction
        structured_payload_hash_drift = bool(
            transaction and transaction.error_code == "structured_output.payload_hash_mismatch"
        )
        structured_unbudgeted_retry = bool(
            transaction
            and any(not attempt.budget_charged for attempt in transaction.attempts)
        )
        structured_stale_output_reused = bool(
            transaction
            and any(
                "stale_output_reused" in attempt.parse_errors
                for attempt in transaction.attempts
            )
        )
        return InvestigationResult(
            session_id=session_id,
            patch_type=context.patch_type,
            tool_calls=tuple(tool_ledger.records) if tool_ledger else (),
            tool_results=tuple(tool_results) if tool_results else (),
            evidence_claim_ids=tuple(evidence_claim_ids) if evidence_claim_ids else (),
            blocked=True,
            block_code=code,
            block_message=message,
            budget=context.budget,
            budget_used=usage or InvestigationBudgetUsage(),
            warnings=tuple(warnings or [message]),
            planner_calls=transaction.call_count if transaction else 0,
            schema_retries=transaction.schema_retry_count if transaction else 0,
            planner_input_payload_hash=transaction.input_payload_hash if transaction else "",
            semantic_coverage=semantic_coverage or {},
            structured_output_payload_hash_drift=structured_payload_hash_drift,
            structured_output_unbudgeted_retry=structured_unbudgeted_retry,
            structured_output_stale_output_reused=structured_stale_output_reused,
        )

    def _execute_action(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        context: InvestigationContext,
        tool_ledger: ToolCallLedger,
        tool_results: list[InvestigationToolResult],
        evidence_claim_ids: list[str],
        warnings: list[str],
        usage: InvestigationBudgetUsage,
        session_id: str,
    ) -> InvestigationToolResult | None:
        """Execute one tool action (mandatory or supplemental) and record it."""

        try:
            self.registry.get(tool_name)
        except PlanInvestigationIssue as issue:
            warnings.append(f"mandatory tool {tool_name} unknown: {issue.message}")
            return None
        validation = self.registry.validate_arguments(tool_name, arguments)
        if validation:
            warnings.append(
                f"mandatory tool {tool_name} invalid args: "
                + "; ".join(i.message for i in validation)
            )
            return None
        usage.tool_calls += 1
        request = InvestigationToolRequest(
            tool_name=tool_name,
            arguments=dict(arguments),
            max_results=context.budget.max_results_per_tool,
            caller_stage=context.caller_stage,
        )
        exec_context = ToolExecutionContext(
            source_indexes=context.source_indexes,
            ledger=context.ledger,
        )
        try:
            result = self.registry.execute(tool_name, request, context=exec_context)
        except PlanInvestigationIssue as issue:
            warnings.append(f"mandatory tool {tool_name} raised {issue.code}: {issue.message}")
            result = InvestigationToolResult(
                ok=False,
                tool_name=tool_name,
                result={"error_code": issue.code},
                error_codes=(issue.code,),
                warnings=(issue.message,),
            )
        tool_results.append(result)
        record_tool_call(
            tool_ledger,
            tool_name=tool_name,
            arguments=arguments,
            result=result,
            caller_stage=context.caller_stage,
        )
        for claim_id in result.evidence_claim_ids:
            if claim_id not in evidence_claim_ids:
                evidence_claim_ids.append(claim_id)
        usage.evidence_claims = len(evidence_claim_ids)
        return result


def _resolve_baseline_policy(
    context: InvestigationContext,
) -> InvestigationBaselinePolicy | None:
    """Return the mandatory baseline policy for ``context.patch_type``.

    Returns ``None`` when the baseline module is unavailable or when no
    policy exists for the patch type (e.g. axial_layers, settings).

    Phase 8A Step 6 (P0-5 fix): the previous implementation hardcoded
    ``accepted_facts=None, inventory=None``.  Now we forward the typed
    optional fields from the context so the Materials/Universes
    baseline resolver can actually read fuel variants and inventory
    roles (its dedicated code path was previously dead).
    """

    try:
        from .baseline import baseline_policy_for_patch_type
    except ImportError:
        return None
    try:
        return baseline_policy_for_patch_type(
            context.patch_type,
            accepted_facts=context.accepted_facts,
            inventory=context.geometry_inventory,
        )
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Strict JSON parsing
# ---------------------------------------------------------------------------


def _parse_investigation_plan(raw: str) -> InvestigationPlan | None:
    """Parse the LLM output into an :class:`InvestigationPlan`.

    Strict contract: the output must reduce to a single JSON object with
    an ``actions`` array (possibly empty) and an optional ``summary``
    string.  Any deviation returns ``None`` (the caller decides how to
    surface the block).

    Tolerated wrappers (the spec explicitly allows "complete JSON object
    extraction" as long as no business semantics are invented):

    * Markdown fences `````json ... `````.
    * Leading / trailing prose as long as a single JSON object is
      embedded (we extract the largest balanced ``{...}`` block).
    * A bare JSON array ``[{tool, arguments}, ...]`` is interpreted as
      ``{"actions": [...]}`` for resilience.

    Anything else — prose-only responses, multi-document outputs, YAML,
    etc. — is rejected.
    """

    text = raw.strip()
    if not text:
        return None
    payload = _extract_json_payload(text)
    if payload is None:
        return None
    if not isinstance(payload, dict):
        return None
    # Accept a bare list wrapper: {"actions": [...]} is the canonical
    # form, but a top-level list is also valid (interpreted as the
    # actions directly).
    if "actions" not in payload:
        return None
    actions_raw = payload["actions"]
    if not isinstance(actions_raw, list):
        return None
    actions: list[InvestigationAction] = []
    for item in actions_raw:
        if not isinstance(item, dict):
            return None
        if "tool" not in item or not isinstance(item["tool"], str):
            return None
        if not item["tool"]:
            return None
        args = item.get("arguments", {})
        if args is None:
            args = {}
        if not isinstance(args, dict):
            return None
        actions.append(InvestigationAction(tool=item["tool"], arguments=args))
    summary = payload.get("summary", "")
    if summary is None:
        summary = ""
    if not isinstance(summary, str):
        return None
    # Reject unknown top-level keys.  The contract is strict: only
    # ``actions`` and ``summary`` are allowed.
    extra_keys = set(payload.keys()) - {"actions", "summary"}
    if extra_keys:
        return None
    return InvestigationPlan(actions=tuple(actions), summary=summary)


def _extract_json_payload(text: str) -> Any:
    """Extract a single JSON value from ``text``.

    Handles three forms:
    1. Pure JSON (the happy path).
    2. JSON wrapped in markdown fences.
    3. JSON embedded in prose (largest balanced ``{...}`` block).

    Returns the parsed JSON value (usually a dict) or ``None``.
    """

    # 1. Try strict json.loads first.
    try:
        return json.loads(text)
    except (ValueError, TypeError):
        pass

    # 2. Strip markdown fences if present.
    fence_match = re.search(r"```(?:json)?\s*\n?(.*?)\n?```", text, re.DOTALL)
    if fence_match:
        candidate = fence_match.group(1).strip()
        try:
            return json.loads(candidate)
        except (ValueError, TypeError):
            pass

    # 3. Extract the largest balanced {...} block.
    obj = _extract_largest_balanced(text, "{", "}")
    if obj is not None:
        try:
            return json.loads(obj)
        except (ValueError, TypeError):
            pass
    # 3b. Try a bare JSON array as a fallback (interpret as actions).
    arr = _extract_largest_balanced(text, "[", "]")
    if arr is not None:
        try:
            parsed = json.loads(arr)
            if isinstance(parsed, list):
                return {"actions": parsed}
        except (ValueError, TypeError):
            pass

    return None


def _extract_largest_balanced(text: str, open_ch: str, close_ch: str) -> str | None:
    """Return the largest balanced ``open_ch ... close_ch`` substring."""

    best = ""
    depth = 0
    start = -1
    for i, ch in enumerate(text):
        if ch == open_ch:
            if depth == 0:
                start = i
            depth += 1
        elif ch == close_ch:
            if depth > 0:
                depth -= 1
                if depth == 0 and start >= 0:
                    candidate = text[start : i + 1]
                    if len(candidate) > len(best):
                        best = candidate
                    start = -1
    return best if best else None


# ---------------------------------------------------------------------------
# Helper: collect claim payloads for prompt injection
# ---------------------------------------------------------------------------


def collect_evidence_for_patch_prompt(
    ledger: PlanningEvidenceLedger,
    claim_ids: tuple[str, ...] | list[str],
) -> list[dict[str, Any]]:
    """Return a JSON-compatible list of claim payloads for the patch
    prompt's evidence section.

    Each entry carries: ``claim_id``, ``subject``, ``predicate``,
    ``value``, ``status``, ``criticality``, and one ``source_span``
    pointer per source_ref (``source_id`` + ``line_range`` only — never
    the excerpt body, never the API key, never the host path).
    """

    out: list[dict[str, Any]] = []
    for claim_id in claim_ids:
        claim = get_claim_by_id(ledger, claim_id)
        if claim is None:
            continue
        spans: list[dict[str, Any]] = []
        for ref in claim.source_refs:
            spans.append({"source_id": ref.source_id, "span_id": ref.span_id})
        out.append(
            {
                "claim_id": claim.claim_id,
                "subject": claim.subject,
                "predicate": claim.predicate,
                "value": claim.value,
                "status": claim.status.value,
                "criticality": claim.criticality.value,
                "source_spans": spans,
            }
        )
    return out
