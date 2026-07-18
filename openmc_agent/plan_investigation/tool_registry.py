"""Registry for read-only planning investigation tools.

The registry is the single dispatch surface for Step 2 tools.  It:

* Holds :class:`InvestigationToolSpec` + executor pairs.
* Validates that the declared capability is enabled in Step 2.
* Validates request arguments against the spec's ``input_schema``.
* Executes the tool with an explicit (source_indexes, ledger) context
  and returns an :class:`InvestigationToolResult`.

The registry has NO global state: each instance is locally owned by its
caller.  Step 2 never registers the registry with any graph node.
"""

from __future__ import annotations

from typing import Any, Callable, Mapping

from pydantic import Field, PrivateAttr

from openmc_agent.schemas import AgentBaseModel

from .errors import PlanInvestigationIssue
from .evidence_ledger import PlanningEvidenceLedger
from .models import SourceKind
from .source_index import SourceIndex
from .tool_models import (
    STEP2_ENABLED_CAPABILITIES,
    InvestigationToolRequest,
    InvestigationToolResult,
    InvestigationToolSpec,
    ToolCapability,
)

__all__ = [
    "ToolExecutor",
    "ToolExecutionContext",
    "InvestigationToolRegistry",
    "build_default_step2_registry",
    "TOOL_NAME_SEARCH_SOURCE_INDEX",
    "TOOL_NAME_INSPECT_REQUIREMENT_STRUCTURE",
    "TOOL_NAME_INSPECT_PATCH_SCHEMA",
    "TOOL_NAME_QUERY_EVIDENCE_LEDGER",
]


# ---------------------------------------------------------------------------
# Type aliases
# ---------------------------------------------------------------------------


#: A tool executor is a pure function: it consumes a request + execution
#: context and returns a structured result.  It MUST NOT close over any
#: mutable global state, perform I/O outside the supplied context, or
#: mutate the ledger in place beyond what :class:`ToolExecutionContext`
#: explicitly allows (adding new evidence claims via ``ledger``).
ToolExecutor = Callable[
    ["ToolExecutionContext", InvestigationToolRequest],
    InvestigationToolResult,
]


class ToolExecutionContext(AgentBaseModel):
    """Explicit, immutable-by-convention inputs to a tool executor.

    ``source_indexes`` is keyed by ``source_id`` so a tool can resolve
    spans without knowing the host file system.  ``ledger`` is the
    evidence ledger claims get added to.  ``patch_schemas`` is an
    optional callable returning a patch-type schema dict (used by the
    schema-inspection tool); when omitted the tool uses the canonical
    :mod:`openmc_agent.plan_builder.patches` introspection.
    """

    model_config = {"arbitrary_types_allowed": True}

    source_indexes: dict[str, SourceIndex] = Field(default_factory=dict)
    ledger: PlanningEvidenceLedger
    patch_schema_provider: Any = None  # callable[[str], dict[str, Any]] | None

    def resolve_index(self, source_id: str | None) -> SourceIndex:
        """Return the named source index, or the only one if ``source_id``
        is ``None``.  Raises if the request is ambiguous or unresolvable.
        """

        if not self.source_indexes:
            raise PlanInvestigationIssue(
                "plan_investigation.tool_context_missing",
                "execution context has no source indexes",
            )
        if source_id is None:
            if len(self.source_indexes) == 1:
                return next(iter(self.source_indexes.values()))
            raise PlanInvestigationIssue(
                "plan_investigation.tool_context_missing",
                "source_id is required when multiple source indexes are present",
                details={"available": sorted(self.source_indexes.keys())},
            )
        idx = self.source_indexes.get(source_id)
        if idx is None:
            raise PlanInvestigationIssue(
                "plan_investigation.tool_context_missing",
                "source_id is not present in the execution context",
                details={
                    "source_id": source_id,
                    "available": sorted(self.source_indexes.keys()),
                },
            )
        return idx


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------


class InvestigationToolRegistry(AgentBaseModel):
    """Registry of investigation tools available in Step 2.

    Construction is via :func:`build_default_step2_registry`; callers
    can also instantiate an empty registry and register tools one by
    one for testing.
    """

    tools: dict[str, InvestigationToolSpec] = Field(default_factory=dict)
    _executors: dict[str, ToolExecutor] = PrivateAttr(default_factory=dict)

    # ------------------------------------------------------------------
    # Registration
    # ------------------------------------------------------------------

    def register(self, spec: InvestigationToolSpec, executor: ToolExecutor) -> None:
        if spec.capability not in STEP2_ENABLED_CAPABILITIES:
            raise PlanInvestigationIssue(
                "plan_investigation.tool_capability_not_enabled",
                "cannot register a tool whose capability is not enabled in Step 2",
                details={"tool": spec.name, "capability": spec.capability.value},
            )
        if spec.name in self.tools:
            raise PlanInvestigationIssue(
                "plan_investigation.tool_already_registered",
                "tool name is already registered",
                details={"tool": spec.name},
            )
        self.tools[spec.name] = spec
        self._executors[spec.name] = executor

    # ------------------------------------------------------------------
    # Lookup
    # ------------------------------------------------------------------

    def get(self, tool_name: str) -> InvestigationToolSpec:
        spec = self.tools.get(tool_name)
        if spec is None:
            raise PlanInvestigationIssue(
                "plan_investigation.tool_unknown",
                "tool is not registered",
                details={"tool": tool_name, "available": sorted(self.tools.keys())},
            )
        return spec

    def list_tools(
        self, *, capability: ToolCapability | None = None
    ) -> list[InvestigationToolSpec]:
        out = [spec for spec in self.tools.values()]
        if capability is not None:
            out = [spec for spec in out if spec.capability == capability]
        return sorted(out, key=lambda s: s.name)

    # ------------------------------------------------------------------
    # Validation + execution
    # ------------------------------------------------------------------

    def validate_arguments(
        self, tool_name: str, arguments: Mapping[str, Any]
    ) -> list[PlanInvestigationIssue]:
        """Return a list of validation issues (empty == valid).

        Validates keys and basic JSON-schema-style type tags declared on
        the spec's ``input_schema``.  Deliberately small: complex schema
        enforcement is deferred to the tool executor.
        """

        spec = self.get(tool_name)
        issues: list[PlanInvestigationIssue] = []
        schema = spec.input_schema
        if not schema:
            return issues
        properties = schema.get("properties", {})
        required = set(schema.get("required", []))
        arg_keys = set(arguments.keys())
        # Unknown keys (when additionalProperties is explicitly False).
        if schema.get("additionalProperties") is False:
            for key in arg_keys - set(properties.keys()):
                issues.append(
                    PlanInvestigationIssue(
                        "plan_investigation.tool_argument_unknown",
                        "argument is not declared in the tool's input schema",
                        details={"tool": tool_name, "argument": key},
                    )
                )
        # Missing required keys.
        for key in required:
            if key not in arguments:
                issues.append(
                    PlanInvestigationIssue(
                        "plan_investigation.tool_argument_missing",
                        "required argument is missing",
                        details={"tool": tool_name, "argument": key},
                    )
                )
        return issues

    def execute(
        self,
        tool_name: str,
        request: InvestigationToolRequest,
        *,
        context: ToolExecutionContext,
    ) -> InvestigationToolResult:
        """Validate + execute a tool.  Never raises for in-band tool
        failures (those are reflected in ``result.ok=False`` and
        ``result.error_codes``); raises only for protocol-level mistakes
        (unknown tool, unresolvable context, security gate trip).
        """

        spec = self.get(tool_name)
        if tool_name != request.tool_name:
            raise PlanInvestigationIssue(
                "plan_investigation.tool_request_mismatch",
                "request.tool_name does not match the dispatched tool_name",
                details={"expected": tool_name, "actual": request.tool_name},
            )
        validation = self.validate_arguments(tool_name, request.arguments)
        if validation:
            return InvestigationToolResult(
                ok=False,
                tool_name=tool_name,
                result={"validated": False},
                error_codes=tuple(issue.code for issue in validation),
                warnings=tuple(issue.message for issue in validation),
            )
        executor = self._executors[tool_name]
        return executor(context, request)


# ---------------------------------------------------------------------------
# Tool name constants
# ---------------------------------------------------------------------------


TOOL_NAME_SEARCH_SOURCE_INDEX = "search_source_index"
TOOL_NAME_INSPECT_REQUIREMENT_STRUCTURE = "inspect_requirement_structure"
TOOL_NAME_INSPECT_PATCH_SCHEMA = "inspect_patch_schema"
TOOL_NAME_QUERY_EVIDENCE_LEDGER = "query_evidence_ledger"


# ---------------------------------------------------------------------------
# Default registry builder
# ---------------------------------------------------------------------------


def build_default_step2_registry() -> InvestigationToolRegistry:
    """Return a registry populated with the four Step 2 tools.

    Order matters only for the ``list_tools`` output.  The executors are
    imported lazily so importing this module does not pull in Pydantic
    patch models unless the schema tool is actually called.
    """

    from .tools import (
        execute_inspect_patch_schema,
        execute_inspect_requirement_structure,
        execute_query_evidence_ledger,
        execute_search_source_index,
        spec_inspect_patch_schema,
        spec_inspect_requirement_structure,
        spec_query_evidence_ledger,
        spec_search_source_index,
    )

    registry = InvestigationToolRegistry()
    registry.register(spec_search_source_index(), execute_search_source_index)
    registry.register(
        spec_inspect_requirement_structure(), execute_inspect_requirement_structure
    )
    registry.register(spec_inspect_patch_schema(), execute_inspect_patch_schema)
    registry.register(spec_query_evidence_ledger(), execute_query_evidence_ledger)
    return registry
