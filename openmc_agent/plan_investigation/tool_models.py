"""Tool protocol models for the read-only planning investigation layer.

Every Step 2 tool follows the same shape:

* Caller builds an :class:`InvestigationToolRequest`.
* Registry dispatches to the tool's executor.
* Executor returns an :class:`InvestigationToolResult` whose payload is a
  structured dict (never free-text prose) and whose ``evidence_claim_ids``
  list the claims that were added to the supplied ledger (when any).

The models are intentionally strict about:

* ``ok`` MUST reflect deterministic execution success (validation or
  security failures flip it to ``False``).
* ``result`` MUST be a JSON-compatible dict; never a string of natural
  language prose like "based on the document, ...".
* ``evidence_claim_ids`` MUST be empty when the tool is read-only with
  respect to the ledger (e.g. :func:`query_evidence_ledger`); otherwise
  it lists the new claim ids produced by this call.
* ``execution_hash`` is a deterministic SHA-256 over the canonical JSON
  of ``(tool_name, arguments, result_payload)``, excluding timestamps
  and run ids.
"""

from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import ConfigDict, Field, field_validator, model_validator

from openmc_agent.schemas import AgentBaseModel

from .errors import PlanInvestigationIssue
from .hashing import content_hash, short_id
from .models import EvidenceSourceRef, SourceKind

__all__ = [
    "ToolCapability",
    "ToolSideEffect",
    "InvestigationToolRequest",
    "InvestigationToolResult",
    "InvestigationToolSpec",
    "STEP2_ENABLED_CAPABILITIES",
]


class ToolCapability(str, Enum):
    """Capability surface a tool exposes.

    Step 2 enables :attr:`SOURCE_SEARCH` and :attr:`SCHEMA_INSPECTION`.
    :attr:`STRUCTURE_INSPECTION` is implemented for the requirement
    structure extractor (a thin, reactor-neutral keyword scan).  The
    remaining values are reserved so the registry can reject tools that
    claim capabilities Step 2 does not grant.
    """

    SOURCE_SEARCH = "source_search"
    STRUCTURE_INSPECTION = "structure_inspection"
    SCHEMA_INSPECTION = "schema_inspection"
    REPOSITORY_INSPECTION = "repository_inspection"  # reserved


class ToolSideEffect(str, Enum):
    """Side-effect class a tool may declare.

    All Step 2 tools declare :attr:`NONE`; the enum exists so a later
    step cannot silently introduce a side-effecting tool without
    updating its spec.
    """

    NONE = "none"


#: Capabilities Step 2 actually grants.  ``REPOSITORY_INSPECTION`` is
#: reserved and rejected at registry time.
STEP2_ENABLED_CAPABILITIES: frozenset[ToolCapability] = frozenset(
    {
        ToolCapability.SOURCE_SEARCH,
        ToolCapability.STRUCTURE_INSPECTION,
        ToolCapability.SCHEMA_INSPECTION,
    }
)


class InvestigationToolRequest(AgentBaseModel):
    """One tool invocation requested by an orchestrator (LLM or Python).

    Step 2 has no LLM dispatch; requests are built by Python callers
    (tests, future orchestration).  The shape is forward-compatible
    with a Step 3 LLM tool-call surface.
    """

    tool_name: str
    arguments: dict[str, Any] = Field(default_factory=dict)
    evidence_required: bool = True
    max_results: int = Field(default=50, ge=1, le=500)
    caller_stage: str = Field(default="investigation", max_length=64)

    @field_validator("tool_name")
    @classmethod
    def _nonempty_tool_name(cls, value: str) -> str:
        if not value or not value.strip():
            raise PlanInvestigationIssue(
                "plan_investigation.tool_request_invalid",
                "tool_name must be non-empty",
            )
        return value


class InvestigationToolResult(AgentBaseModel):
    """Deterministic, structured result of one tool invocation.

    The ``result`` payload is always a JSON-compatible dict.  Tools that
    discover source evidence populate ``source_refs`` (pointers to
    verified spans) and ``evidence_claim_ids`` (the new ledger entries).
    Read-only tools (e.g. ``query_evidence_ledger``) leave both empty.
    """

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    ok: bool
    tool_name: str
    result: dict[str, Any] = Field(default_factory=dict)
    evidence_claim_ids: tuple[str, ...] = Field(default_factory=tuple)
    source_refs: tuple[EvidenceSourceRef, ...] = Field(default_factory=tuple)
    warnings: tuple[str, ...] = Field(default_factory=tuple)
    error_codes: tuple[str, ...] = Field(default_factory=tuple)
    execution_hash: str = ""

    @model_validator(mode="after")
    def _compute_execution_hash(self) -> "InvestigationToolResult":
        expected = content_hash(
            {
                "tool": self.tool_name,
                "ok": self.ok,
                "result": self.result,
                # source_refs / evidence_claim_ids are derived from result
                # but we include them explicitly so tampering with the
                # claim linkage is detectable.
                "evidence_claim_ids": list(self.evidence_claim_ids),
                "source_refs": [ref.model_dump(mode="json") for ref in self.source_refs],
            }
        )
        if not self.execution_hash:
            object.__setattr__(self, "execution_hash", expected)
        elif self.execution_hash != expected:
            raise PlanInvestigationIssue(
                "plan_investigation.tool_execution_hash_mismatch",
                "execution_hash does not match the recomputed value",
                details={"expected": expected, "actual": self.execution_hash},
            )
        return self


class InvestigationToolSpec(AgentBaseModel):
    """Declarative description of one investigation tool.

    Specs live in the registry and are surfaced to (future) LLM
    orchestrators via :meth:`InvestigationToolRegistry.list_tools`.
    """

    name: str
    description: str
    capability: ToolCapability
    input_schema: dict[str, Any] = Field(default_factory=dict)
    output_schema: dict[str, Any] = Field(default_factory=dict)
    allowed_source_kinds: tuple[SourceKind, ...] = Field(default_factory=tuple)
    side_effect: ToolSideEffect = ToolSideEffect.NONE
    produces_evidence: bool = True

    @field_validator("name")
    @classmethod
    def _name_pattern(cls, value: str) -> str:
        if not value or not value.replace("_", "").isalnum():
            raise PlanInvestigationIssue(
                "plan_investigation.tool_spec_invalid",
                "tool name must match [a-z_][a-z0-9_]*",
                details={"name": value},
            )
        return value

    @model_validator(mode="after")
    def _step2_capability_gate(self) -> "InvestigationToolSpec":
        if self.capability not in STEP2_ENABLED_CAPABILITIES:
            raise PlanInvestigationIssue(
                "plan_investigation.tool_capability_not_enabled",
                "capability is not enabled in Step 2",
                details={
                    "capability": self.capability.value,
                    "allowed": sorted(c.value for c in STEP2_ENABLED_CAPABILITIES),
                },
            )
        return self
