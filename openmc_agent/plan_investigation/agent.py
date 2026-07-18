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
    result_hash: str = ""

    @model_validator(mode="after")
    def _compute_result_hash(self) -> "InvestigationResult":
        expected = content_hash(
            {
                "session_id": self.session_id,
                "patch_type": self.patch_type,
                "tool_calls": [tc.model_dump(mode="json") for tc in self.tool_calls],
                "evidence_claim_ids": list(self.evidence_claim_ids),
                "blocked": self.blocked,
                "block_code": self.block_code,
                "completed": self.completed,
                "budget_used": self.budget_used.model_dump(mode="json"),
            }
        )
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
        # 1. Ask the LLM for an action plan.
        try:
            plan = self.plan(context)
        except PlanInvestigationIssue as issue:
            return self._block(
                session_id=session_id,
                context=context,
                code=issue.code,
                message=issue.message,
            )

        # 2. Execute each action against the registry, respecting budget.
        tool_ledger = ToolCallLedger()
        tool_results: list[InvestigationToolResult] = []
        evidence_claim_ids: list[str] = []
        warnings: list[str] = []
        usage = InvestigationBudgetUsage()
        blocked_code: str | None = None
        blocked_message: str | None = None

        for action in plan.actions:
            if usage.tool_calls >= context.budget.max_tool_calls:
                blocked_code = BLOCK_CODE_BUDGET_EXCEEDED
                blocked_message = (
                    f"max_tool_calls={context.budget.max_tool_calls} reached"
                )
                break

            request = InvestigationToolRequest(
                tool_name=action.tool,
                arguments=dict(action.arguments),
                max_results=context.budget.max_results_per_tool,
                caller_stage=context.caller_stage,
            )

            # Validate the tool exists and arguments are well-formed BEFORE
            # we count it against the budget.  An unknown tool blocks the
            # whole session (it indicates the LLM is off-policy).
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
                result = self.registry.execute(
                    action.tool, request, context=exec_context
                )
            except PlanInvestigationIssue as issue:
                # Tool raised a protocol-level exception: record a warning
                # and continue.  Tool failures are non-blocking.
                warnings.append(
                    f"tool {action.tool} raised {issue.code}: {issue.message}"
                )
                result = InvestigationToolResult(
                    ok=False,
                    tool_name=action.tool,
                    result={"error_code": issue.code},
                    error_codes=(issue.code,),
                    warnings=(issue.message,),
                )

            tool_results.append(result)
            record_tool_call(
                tool_ledger,
                tool_name=action.tool,
                arguments=action.arguments,
                result=result,
                caller_stage=context.caller_stage,
            )
            for claim_id in result.evidence_claim_ids:
                if claim_id not in evidence_claim_ids:
                    evidence_claim_ids.append(claim_id)
            usage.evidence_claims = len(evidence_claim_ids)
            if usage.exceeds(context.budget):
                blocked_code = BLOCK_CODE_BUDGET_EXCEEDED
                blocked_message = (
                    f"max_evidence_claims={context.budget.max_evidence_claims} exceeded"
                )
                break

        completed = blocked_code is None
        result = InvestigationResult(
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
        )
        return result

    # ------------------------------------------------------------------
    # LLM interaction
    # ------------------------------------------------------------------

    def plan(self, context: InvestigationContext) -> InvestigationPlan:
        """Build the LLM prompt, call the LLM, and parse strict JSON."""

        # Imported lazily so importing the agent module does not pull in
        # prompt-rendering helpers (and therefore the patch schema) until
        # the agent is actually used.
        from .prompt import build_investigation_prompt

        prompt = build_investigation_prompt(context)
        raw = self.llm_client(prompt)
        if not isinstance(raw, str):
            raise PlanInvestigationIssue(
                BLOCK_CODE_INVALID_LLM_OUTPUT,
                "investigation LLM must return a string",
                details={"return_type": type(raw).__name__},
            )
        plan = _parse_investigation_plan(raw)
        if plan is None:
            raise PlanInvestigationIssue(
                BLOCK_CODE_INVALID_LLM_OUTPUT,
                "investigation LLM output was not valid strict JSON matching the action schema",
                details={"raw_excerpt": raw[:200]},
            )
        return plan

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
    ) -> InvestigationResult:
        return InvestigationResult(
            session_id=session_id,
            patch_type=context.patch_type,
            blocked=True,
            block_code=code,
            block_message=message,
            budget=context.budget,
            warnings=(message,),
        )


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
