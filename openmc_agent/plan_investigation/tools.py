"""Built-in Step 2 investigation tools.

Four tools, all pure-Python and side-effect-free with respect to the
host (no shell, no fs walk, no network, no LLM).  Tools that discover
evidence add it to the supplied :class:`PlanningEvidenceLedger` via
the public :func:`add_claim` API and report the new claim ids.

Reactor-neutrality
------------------
None of these tools hard-code a reactor type, fuel name, lattice
orientation, or material composition.  The keyword scan in
:func:`execute_inspect_requirement_structure` uses generic descriptors
(``"full core"``, ``"assembly"``, ``"lattice"``, ...) that span PWR,
BWR, VVER, HTGR, SFR, CANDU and MOX problem statements.
"""

from __future__ import annotations

import re
from typing import Any

from .errors import PlanInvestigationIssue
from .evidence_ledger import (
    PlanningEvidenceLedger,
    add_claim,
    find_claims,
)
from .hashing import content_hash
from .models import (
    EvidenceClaim,
    EvidenceCriticality,
    EvidenceSourceRef,
    EvidenceStatus,
    SourceKind,
)
from .source_index import SourceIndex
from .tool_models import (
    InvestigationToolRequest,
    InvestigationToolResult,
    InvestigationToolSpec,
    ToolCapability,
    ToolSideEffect,
)
from .tool_registry import (
    TOOL_NAME_INSPECT_PATCH_SCHEMA,
    TOOL_NAME_INSPECT_REQUIREMENT_STRUCTURE,
    TOOL_NAME_QUERY_EVIDENCE_LEDGER,
    TOOL_NAME_SEARCH_SOURCE_INDEX,
    ToolExecutionContext,
)

__all__ = [
    # Tool 1: source_search
    "spec_search_source_index",
    "execute_search_source_index",
    # Tool 2: requirement structure
    "spec_inspect_requirement_structure",
    "execute_inspect_requirement_structure",
    # Tool 3: patch schema
    "spec_inspect_patch_schema",
    "execute_inspect_patch_schema",
    # Tool 4: evidence query
    "spec_query_evidence_ledger",
    "execute_query_evidence_ledger",
    # Keyword map used by Tool 2 (exported for tests + future tools)
    "REQUIREMENT_KEYWORD_GROUPS",
    "GRID_PATTERN_RE",
]


# ---------------------------------------------------------------------------
# Tool 1: search_source_index
# ---------------------------------------------------------------------------


def spec_search_source_index() -> InvestigationToolSpec:
    return InvestigationToolSpec(
        name=TOOL_NAME_SEARCH_SOURCE_INDEX,
        description=(
            "Deterministic case-sensitive substring / keyword-AND search "
            "over indexed source documents.  Returns verbatim SourceSpan "
            "records (no prose).  Each hit becomes an explicit EvidenceClaim."
        ),
        capability=ToolCapability.SOURCE_SEARCH,
        input_schema={
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "query": {"type": "string", "description": "Substring to find."},
                "source_id": {
                    "type": "string",
                    "description": "Optional source_id to restrict the search.",
                },
                "keywords": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Optional list of keywords for an AND search.",
                },
                "max_results": {"type": "integer", "minimum": 1, "maximum": 500},
            },
            "required": ["query"],
        },
        output_schema={
            "type": "object",
            "properties": {
                "spans": {"type": "array"},
                "total_hits": {"type": "integer"},
                "truncated": {"type": "boolean"},
            },
        },
        allowed_source_kinds=(
            SourceKind.USER_REQUIREMENT,
            SourceKind.ATTACHED_DOCUMENT,
        ),
        side_effect=ToolSideEffect.NONE,
        produces_evidence=True,
    )


def execute_search_source_index(
    context: ToolExecutionContext,
    request: InvestigationToolRequest,
) -> InvestigationToolResult:
    args = request.arguments
    query = args.get("query")
    if not isinstance(query, str) or not query:
        return _failure(
            TOOL_NAME_SEARCH_SOURCE_INDEX,
            "plan_investigation.tool_argument_invalid",
            "query must be a non-empty string",
        )

    keywords = args.get("keywords") or []
    if not isinstance(keywords, list) or not all(isinstance(k, str) for k in keywords):
        return _failure(
            TOOL_NAME_SEARCH_SOURCE_INDEX,
            "plan_investigation.tool_argument_invalid",
            "keywords must be a list of strings",
        )

    source_id = args.get("source_id")
    max_results = min(int(args.get("max_results") or request.max_results), request.max_results)

    try:
        index = context.resolve_index(source_id)
    except PlanInvestigationIssue as issue:
        return _failure(
            TOOL_NAME_SEARCH_SOURCE_INDEX,
            issue.code,
            issue.message,
            details=issue.details,
        )

    # Use keyword-AND search when keywords are supplied AND there is no
    # explicit query other than the keyword gating; otherwise do a
    # literal substring search.
    spans = index.find_literal(query, max_hits=max_results) if query else []
    # Apply optional keyword filtering on top of literal hits: keep only
    # spans whose excerpt contains every keyword.
    if keywords:
        spans = [s for s in spans if all(k in s.excerpt for k in keywords)]

    spans.sort(key=lambda s: (s.source_id, s.start_line, s.end_line, s.span_id))

    # Build evidence claims.  Each hit becomes an ``explicit`` claim
    # recording what was searched and where it was found.  The subject is
    # generic (``"source"``) so this stays reactor-neutral; downstream
    # tools / Facts Gate interpret the value.
    produced_refs: list[EvidenceSourceRef] = []
    produced_claim_ids: list[str] = []
    warnings: list[str] = []
    for span in spans:
        index.register_span(span)
        ref = EvidenceSourceRef(
            source_id=span.source_id,
            span_id=span.span_id,
            excerpt_hash=span.excerpt_hash,
        )
        produced_refs.append(ref)
        claim = EvidenceClaim(
            claim_id="",
            subject="source",
            predicate="search_hit",
            value={
                "query": query,
                "keywords": list(keywords),
                "source_id": span.source_id,
                "line_range": [span.start_line, span.end_line],
                "section_path": list(span.section_path),
            },
            status=EvidenceStatus.EXPLICIT,
            criticality=EvidenceCriticality.INFORMATIONAL,
            source_refs=(ref,),
            metadata={"tool": TOOL_NAME_SEARCH_SOURCE_INDEX},
        )
        try:
            add_claim(
                context.ledger,
                claim,
                source_indexes=context.source_indexes,
            )
            produced_claim_ids.append(claim.claim_id)
        except PlanInvestigationIssue as issue:
            # Dedup on identical claim_id is the common case (the same
            # search run twice produces the same claim).  Other rejections
            # are surfaced as warnings.
            if issue.code != "plan_investigation.duplicate_claim":
                warnings.append(
                    f"could not add search_hit claim for span {span.span_id}: {issue.message}"
                )
            else:
                produced_claim_ids.append(claim.claim_id)

    payload_spans = [
        {
            "source_id": span.source_id,
            "span_id": span.span_id,
            "start_line": span.start_line,
            "end_line": span.end_line,
            "section_path": list(span.section_path),
            "excerpt": span.excerpt,
        }
        for span in spans
    ]
    return InvestigationToolResult(
        ok=True,
        tool_name=TOOL_NAME_SEARCH_SOURCE_INDEX,
        result={
            "spans": payload_spans,
            "total_hits": len(spans),
            "truncated": len(spans) >= max_results,
            "query": query,
            "keywords": list(keywords),
        },
        evidence_claim_ids=tuple(produced_claim_ids),
        source_refs=tuple(produced_refs),
        warnings=tuple(warnings),
    )


# ---------------------------------------------------------------------------
# Tool 2: inspect_requirement_structure
# ---------------------------------------------------------------------------


#: Reactor-neutral keyword groups the requirement-structure tool scans
#: for.  Each entry maps a canonical indicator id to a list of literal
#: phrases that legitimately indicate that indicator in a problem
#: statement.  None of these are reactor-specific; they describe generic
#: modeling scope signals any reactor type might use.
REQUIREMENT_KEYWORD_GROUPS: dict[str, tuple[str, ...]] = {
    "full_core": ("full core", "full-core", "whole core"),
    "assembly": ("assembly", "assemblies", "fuel assembly"),
    "lattice": ("lattice", "lattices", "rectangular lattice", "hexagonal lattice"),
    "loading_map": ("loading map", "loading pattern", "core loading"),
    "control_rod": ("control rod", "control rods", "control blade", "crd"),
    "burnable_poison": ("burnable poison", "burnable absorber", "ba", "ifba", "waba"),
    "fuel_enrichment": (
        "enrichment",
        "fuel enrichment",
        "wt%",
        "weight percent",
        "atom percent",
    ),
    "axial": ("axial", "axially", "active fuel", "fuel height", "stack length"),
    "spacer_grid": ("spacer grid", "spacer grids", "grid span"),
    "universe": ("universe", "universe id", "cell universe"),
    "material": ("material", "composition", "density", "atom density"),
}


#: Pattern that recognises "N x N" / "N by N" / "NxN" grid-size notation.
#: Useful for spotting lattice-size statements without imposing a fixed
#: grid shape.  Captures the two dimensions.
GRID_PATTERN_RE = re.compile(
    r"\b(\d{1,3})\s*(?:x|×|by)\s*(\d{1,3})\b",
    re.IGNORECASE,
)


def spec_inspect_requirement_structure() -> InvestigationToolSpec:
    return InvestigationToolSpec(
        name=TOOL_NAME_INSPECT_REQUIREMENT_STRUCTURE,
        description=(
            "Reactor-neutral scan of the requirement source for scope "
            "indicator phrases (full core / assembly / lattice / control "
            "rod / spacer grid / ...) and grid-size notation (N x N).  "
            "Emits one ``scope_indicator_present`` claim per detected "
            "indicator and one ``grid_size_text`` claim per parsed grid "
            "size.  Does NOT decide model_scope; that is the Facts Gate's "
            "responsibility."
        ),
        capability=ToolCapability.STRUCTURE_INSPECTION,
        input_schema={
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "source_id": {
                    "type": "string",
                    "description": "Optional source_id; defaults to the only/first source.",
                },
                "keyword_groups": {
                    "type": "object",
                    "description": "Optional override of keyword groups to scan for.",
                },
            },
            "required": [],
        },
        output_schema={
            "type": "object",
            "properties": {
                "scope_indicators": {"type": "array"},
                "grid_sizes": {"type": "array"},
            },
        },
        allowed_source_kinds=(SourceKind.USER_REQUIREMENT, SourceKind.ATTACHED_DOCUMENT),
        side_effect=ToolSideEffect.NONE,
        produces_evidence=True,
    )


def execute_inspect_requirement_structure(
    context: ToolExecutionContext,
    request: InvestigationToolRequest,
) -> InvestigationToolResult:
    args = request.arguments
    source_id = args.get("source_id")
    keyword_groups = args.get("keyword_groups") or REQUIREMENT_KEYWORD_GROUPS
    if not isinstance(keyword_groups, dict):
        return _failure(
            TOOL_NAME_INSPECT_REQUIREMENT_STRUCTURE,
            "plan_investigation.tool_argument_invalid",
            "keyword_groups must be a dict[str, list[str]]",
        )

    try:
        index = context.resolve_index(source_id)
    except PlanInvestigationIssue as issue:
        return _failure(
            TOOL_NAME_INSPECT_REQUIREMENT_STRUCTURE,
            issue.code,
            issue.message,
            details=issue.details,
        )

    produced_claim_ids: list[str] = []
    produced_refs: list[EvidenceSourceRef] = []
    warnings: list[str] = []

    # Build a lowercase line index once for case-insensitive indicator
    # matching.  The original line text is still used for span excerpts.
    lowercase_lines: list[str] = [
        index.get_line(n).lower() for n in range(1, index.document.line_count + 1)
    ]

    scope_indicators: list[dict[str, Any]] = []
    for indicator, phrases in keyword_groups.items():
        phrase_hits: list[dict[str, Any]] = []
        for phrase in phrases:
            phrase_lower = phrase.lower()
            for line_no, lower_line in enumerate(lowercase_lines, start=1):
                if phrase_lower not in lower_line:
                    continue
                span = index.make_span(line_no, line_no)
                index.register_span(span)
                ref = EvidenceSourceRef(
                    source_id=span.source_id,
                    span_id=span.span_id,
                    excerpt_hash=span.excerpt_hash,
                )
                produced_refs.append(ref)
                phrase_hits.append(
                    {
                        "phrase": phrase,
                        "line_range": [span.start_line, span.end_line],
                    }
                )
                if len(phrase_hits) >= request.max_results:
                    break
            if len(phrase_hits) >= request.max_results:
                break
        if phrase_hits:
            scope_indicators.append(
                {"indicator": indicator, "hits": phrase_hits}
            )
            claim = EvidenceClaim(
                claim_id="",
                subject="model",
                predicate="scope_indicator_present",
                value=indicator,
                status=EvidenceStatus.EXPLICIT,
                criticality=EvidenceCriticality.SUPPORTING,
                source_refs=tuple(produced_refs[-len(phrase_hits):]),
                metadata={"tool": TOOL_NAME_INSPECT_REQUIREMENT_STRUCTURE},
            )
            try:
                add_claim(context.ledger, claim, source_indexes=context.source_indexes)
                produced_claim_ids.append(claim.claim_id)
            except PlanInvestigationIssue as issue:
                if issue.code != "plan_investigation.duplicate_claim":
                    warnings.append(
                        f"could not add scope_indicator claim for {indicator}: {issue.message}"
                    )
                else:
                    produced_claim_ids.append(claim.claim_id)

    # Grid-size pattern scan: crawl the whole document once per unique
    # line, applying GRID_PATTERN_RE.
    grid_sizes: list[dict[str, Any]] = []
    seen_grid: set[tuple[int, int]] = set()
    for line_no in range(1, index.document.line_count + 1):
        line = index.get_line(line_no)
        for match in GRID_PATTERN_RE.finditer(line):
            try:
                rows = int(match.group(1))
                cols = int(match.group(2))
            except ValueError:
                continue
            if (rows, cols) in seen_grid:
                continue
            if rows < 1 or cols < 1 or rows > 1000 or cols > 1000:
                continue
            seen_grid.add((rows, cols))
            span = index.make_span(line_no, line_no)
            index.register_span(span)
            ref = EvidenceSourceRef(
                source_id=span.source_id,
                span_id=span.span_id,
                excerpt_hash=span.excerpt_hash,
            )
            produced_refs.append(ref)
            grid_sizes.append(
                {
                    "rows": rows,
                    "cols": cols,
                    "line": line_no,
                    "match_text": match.group(0),
                }
            )
            claim = EvidenceClaim(
                claim_id="",
                subject="model",
                predicate="grid_size_text",
                value={"rows": rows, "cols": cols, "match_text": match.group(0)},
                status=EvidenceStatus.EXPLICIT,
                criticality=EvidenceCriticality.SUPPORTING,
                source_refs=(ref,),
                metadata={
                    "tool": TOOL_NAME_INSPECT_REQUIREMENT_STRUCTURE,
                    "line": line_no,
                },
            )
            try:
                add_claim(context.ledger, claim, source_indexes=context.source_indexes)
                produced_claim_ids.append(claim.claim_id)
            except PlanInvestigationIssue as issue:
                if issue.code != "plan_investigation.duplicate_claim":
                    warnings.append(
                        f"could not add grid_size_text claim at line {line_no}: {issue.message}"
                    )
                else:
                    produced_claim_ids.append(claim.claim_id)

    return InvestigationToolResult(
        ok=True,
        tool_name=TOOL_NAME_INSPECT_REQUIREMENT_STRUCTURE,
        result={
            "scope_indicators": scope_indicators,
            "grid_sizes": grid_sizes,
            "source_id": index.document.source_id,
        },
        evidence_claim_ids=tuple(produced_claim_ids),
        source_refs=tuple(produced_refs),
        warnings=tuple(warnings),
    )


# ---------------------------------------------------------------------------
# Tool 3: inspect_patch_schema
# ---------------------------------------------------------------------------


def spec_inspect_patch_schema() -> InvestigationToolSpec:
    return InvestigationToolSpec(
        name=TOOL_NAME_INSPECT_PATCH_SCHEMA,
        description=(
            "Return the public Pydantic schema for a patch type: required "
            "fields, optional fields, enum values, nested model pointers.  "
            "Reads ONLY the public patch schema (no implementation code, "
            "no private helpers).  Does not produce source evidence; "
            "output is reference data the LLM uses to shape a patch."
        ),
        capability=ToolCapability.SCHEMA_INSPECTION,
        input_schema={
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "patch_type": {
                    "type": "string",
                    "description": "One of the PatchType literal values.",
                },
            },
            "required": ["patch_type"],
        },
        output_schema={
            "type": "object",
            "properties": {
                "patch_type": {"type": "string"},
                "required_fields": {"type": "array"},
                "optional_fields": {"type": "array"},
                "enum_values": {"type": "object"},
                "nested_models": {"type": "object"},
                "allowed_top_level_keys": {"type": "array"},
                "forbidden_top_level_keys": {"type": "array"},
            },
        },
        allowed_source_kinds=(),  # no source requirement
        side_effect=ToolSideEffect.NONE,
        produces_evidence=False,
    )


def execute_inspect_patch_schema(
    context: ToolExecutionContext,
    request: InvestigationToolRequest,
) -> InvestigationToolResult:
    args = request.arguments
    patch_type = args.get("patch_type")
    if not isinstance(patch_type, str) or not patch_type:
        return _failure(
            TOOL_NAME_INSPECT_PATCH_SCHEMA,
            "plan_investigation.tool_argument_invalid",
            "patch_type must be a non-empty string",
        )

    try:
        schema_payload = _introspect_patch_schema(patch_type)
    except PlanInvestigationIssue as issue:
        return _failure(
            TOOL_NAME_INSPECT_PATCH_SCHEMA,
            issue.code,
            issue.message,
            details=issue.details,
        )

    return InvestigationToolResult(
        ok=True,
        tool_name=TOOL_NAME_INSPECT_PATCH_SCHEMA,
        result=schema_payload,
        evidence_claim_ids=(),
        source_refs=(),
    )


def _introspect_patch_schema(patch_type: str) -> dict[str, Any]:
    """Return the public schema dict for ``patch_type``.

    Imports the patch-model registry lazily so the tool only pays for
    Pydantic patch models when actually called.  Raises
    :class:`PlanInvestigationIssue` for unknown patch types.
    """

    from openmc_agent.plan_builder.patches import (
        _PATCH_MODELS,
        get_patch_allowed_top_level_keys,
        get_patch_forbidden_top_level_keys,
        get_patch_json_schema,
    )

    model_cls = _PATCH_MODELS.get(patch_type)
    if model_cls is None:
        raise PlanInvestigationIssue(
            "plan_investigation.tool_argument_invalid",
            "unknown patch_type",
            details={
                "patch_type": patch_type,
                "allowed": sorted(_PATCH_MODELS.keys()),
            },
        )

    import typing

    required: list[str] = []
    optional: list[str] = []
    enum_values: dict[str, list[Any]] = {}
    nested_models: dict[str, str] = {}

    for field_name, info in model_cls.model_fields.items():
        if info.is_required():
            required.append(field_name)
        else:
            optional.append(field_name)
        enumish = _extract_enum_values(info.annotation)
        if enumish is not None:
            enum_values[field_name] = enumish
        nested_name = _extract_nested_model_name(info.annotation)
        if nested_name is not None:
            nested_models[field_name] = nested_name

    return {
        "patch_type": patch_type,
        "model_class": model_cls.__name__,
        "required_fields": sorted(required),
        "optional_fields": sorted(optional),
        "enum_values": {k: sorted(set(v)) for k, v in enum_values.items()},
        "nested_models": dict(sorted(nested_models.items())),
        "allowed_top_level_keys": sorted(get_patch_allowed_top_level_keys(patch_type)),
        "forbidden_top_level_keys": sorted(get_patch_forbidden_top_level_keys(patch_type)),
        "json_schema_digest": _digest_json_schema(get_patch_json_schema(patch_type)),
    }


def _extract_enum_values(annotation: Any) -> list[Any] | None:
    """Return enum/literal values for ``annotation``, or None."""

    import enum
    import typing

    origin = typing.get_origin(annotation)
    args = typing.get_args(annotation)

    # Literal[...] -> return the literal args.
    if origin is typing.Literal:
        out: list[Any] = []
        for arg in args:
            if isinstance(arg, enum.Enum):
                out.append(arg.value)
            else:
                out.append(arg)
        return out

    # Optional[Literal[...]] / Union[Literal[...], None]
    if origin is typing.Union:
        literal_args = [a for a in args if typing.get_origin(a) is typing.Literal]
        if literal_args:
            vals: list[Any] = []
            for literal in literal_args:
                for v in typing.get_args(literal):
                    if isinstance(v, enum.Enum):
                        vals.append(v.value)
                    else:
                        vals.append(v)
            return vals

    # Enum subclass directly.
    if isinstance(annotation, type) and issubclass(annotation, enum.Enum):
        return [member.value for member in annotation]

    return None


def _extract_nested_model_name(annotation: Any) -> str | None:
    """Return the class name of a nested pydantic BaseModel field, if any."""

    import typing
    from pydantic import BaseModel

    origin = typing.get_origin(annotation)
    args = typing.get_args(annotation)

    candidates: list[Any] = []
    if origin is None:
        candidates.append(annotation)
    else:
        candidates.extend(args)

    for cand in candidates:
        if isinstance(cand, type) and issubclass(cand, BaseModel):
            return cand.__name__
    return None


def _digest_json_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """Reduce a JSON schema to its public-interest digest.

    The full JSON schema can be large; we surface only the keys an LLM
    needs to shape a patch: ``required`` and the per-property
    ``type`` / ``anyOf`` summary.
    """

    if not isinstance(schema, dict):
        return {}
    properties = schema.get("properties", {}) or {}
    digest_props: dict[str, Any] = {}
    for name, prop in properties.items():
        if not isinstance(prop, dict):
            continue
        # Keep only structural keys; drop title/description/$defs to
        # avoid leaking large reference trees.
        slim = {k: prop[k] for k in ("type", "anyOf", "oneOf", "enum", "items") if k in prop}
        digest_props[name] = slim
    return {
        "required": sorted(schema.get("required", []) or []),
        "properties": dict(sorted(digest_props.items())),
    }


# ---------------------------------------------------------------------------
# Tool 4: query_evidence_ledger
# ---------------------------------------------------------------------------


def spec_query_evidence_ledger() -> InvestigationToolSpec:
    return InvestigationToolSpec(
        name=TOOL_NAME_QUERY_EVIDENCE_LEDGER,
        description=(
            "Read-only query of the evidence ledger.  Returns matching "
            "claims (subject / predicate / status / criticality filters).  "
            "Does not create new claims; useful for avoiding duplicate "
            "investigation."
        ),
        capability=ToolCapability.SCHEMA_INSPECTION,
        input_schema={
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "subject": {"type": "string"},
                "predicate": {"type": "string"},
                "status": {"type": "string"},
                "criticality": {"type": "string"},
            },
            "required": [],
        },
        output_schema={
            "type": "object",
            "properties": {
                "claims": {"type": "array"},
                "total": {"type": "integer"},
            },
        },
        allowed_source_kinds=(),
        side_effect=ToolSideEffect.NONE,
        produces_evidence=False,
    )


def execute_query_evidence_ledger(
    context: ToolExecutionContext,
    request: InvestigationToolRequest,
) -> InvestigationToolResult:
    args = request.arguments

    status_arg = args.get("status")
    status = None
    if status_arg:
        try:
            status = EvidenceStatus(status_arg)
        except ValueError:
            return _failure(
                TOOL_NAME_QUERY_EVIDENCE_LEDGER,
                "plan_investigation.tool_argument_invalid",
                "unknown status value",
                details={"status": status_arg},
            )

    crit_arg = args.get("criticality")
    criticality = None
    if crit_arg:
        try:
            criticality = EvidenceCriticality(crit_arg)
        except ValueError:
            return _failure(
                TOOL_NAME_QUERY_EVIDENCE_LEDGER,
                "plan_investigation.tool_argument_invalid",
                "unknown criticality value",
                details={"criticality": crit_arg},
            )

    matches = find_claims(
        context.ledger,
        subject=args.get("subject"),
        predicate=args.get("predicate"),
        status=status,
        criticality=criticality,
    )
    payload = [claim.model_dump(mode="json") for claim in matches[: request.max_results]]
    return InvestigationToolResult(
        ok=True,
        tool_name=TOOL_NAME_QUERY_EVIDENCE_LEDGER,
        result={
            "claims": payload,
            "total": len(matches),
            "truncated": len(matches) > request.max_results,
        },
        evidence_claim_ids=(),
        source_refs=(),
    )


# ---------------------------------------------------------------------------
# Internal helper
# ---------------------------------------------------------------------------


def _failure(
    tool_name: str, code: str, message: str, *, details: dict[str, Any] | None = None
) -> InvestigationToolResult:
    return InvestigationToolResult(
        ok=False,
        tool_name=tool_name,
        result={"error_code": code, "error_message": message},
        error_codes=(code,),
        warnings=(message,),
    )
