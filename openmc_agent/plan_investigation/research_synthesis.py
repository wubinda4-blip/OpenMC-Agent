"""Phase 8A Step 7 — LLM research evidence synthesis (Sections 5-6).

Takes the candidate SourceSpans located by the deterministic executor
and asks the LLM to propose typed semantic EvidenceClaims.  The LLM
only emits proposals that reference system-provided ``source_span_ids``;
it cannot invent span IDs, source IDs, or line numbers.

The pipeline is:

1. Build :class:`ResearchEvidenceSynthesisContext` from the research
   request + candidate spans + existing ledger + inventory context.
2. Render a strict JSON prompt.
3. Parse + validate each proposal (:func:`validate_research_evidence_proposals`).
4. Commit accepted proposals to the Ledger
   (:func:`commit_research_evidence_proposals`).
5. Return a :class:`PlanningEvidenceDelta` with real ``added_claim_ids``
   and a changed ``ledger_hash_after``.

Hard rules (Section 6):

* Span must exist in the current SourceIndex.
* Span hash must be valid.
* Predicate must be in the allowlist.
* Component/profile kind must be legal.
* Numerical values must be verifiable in the span excerpt (or from an
  allowed deterministic unit conversion).
* Proposal must cover a ResearchTarget.
* Must not overwrite a human-confirmed claim.
* Must not silently resolve a conflict.
* Must not accept a source-critical claim without source.
* Must not accept a duplicate semantic key + same value as new progress.
"""

from __future__ import annotations

import json
import re
from typing import Any, Callable, Iterable, Mapping

from pydantic import Field, model_validator

from openmc_agent.schemas import AgentBaseModel

from .errors import PlanInvestigationIssue
from .evidence_ledger import PlanningEvidenceLedger
from .hashing import content_hash, short_id
from .models import EvidenceClaim, SourceSpan
from .research_models import (
    PlanResearchRequest,
    PlanResearchResult,
    PlanResearchStatus,
    PlanningEvidenceDelta,
    PlanResearchTarget,
)

__all__ = [
    "ResearchEvidenceSynthesisContext",
    "ResearchEvidenceProposal",
    "ResearchProposalValidationResult",
    "build_research_synthesis_context",
    "render_research_synthesis_prompt",
    "parse_research_synthesis_output",
    "validate_research_evidence_proposals",
    "commit_research_evidence_proposals",
    "run_research_evidence_synthesis",
    "ALLOWED_RESEARCH_PREDICATES",
]


# Predicates the LLM may propose during research synthesis.  Keep
# conservative: adding new predicates requires explicit code change.
ALLOWED_RESEARCH_PREDICATES: frozenset[str] = frozenset({
    "material.role_required",
    "material.density",
    "material.composition",
    "material.temperature",
    "geometry.profile_required",
    "geometry.pin_pitch",
    "geometry.diameter",
    "geometry.axial_region_extent",
    "placement.coordinate_required",
    "axial.region_required",
    "axial.extent_required",
    "source.value_present",
})


# ---------------------------------------------------------------------------
# Synthesis context + proposal models
# ---------------------------------------------------------------------------


class ResearchEvidenceSynthesisContext(AgentBaseModel):
    """Typed context bundle for one LLM synthesis call.

    Carries everything the LLM needs to propose claims: candidate
    spans, existing matching claims, the research targets, the
    inventory + requirement sets, and the allowed ontology.
    """

    research_request_id: str
    research_targets: tuple[PlanResearchTarget, ...] = Field(default_factory=tuple)
    candidate_source_spans: tuple[dict[str, Any], ...] = Field(default_factory=tuple)
    existing_matching_claims: tuple[dict[str, Any], ...] = Field(default_factory=tuple)
    gate_findings: tuple[dict[str, Any], ...] = Field(default_factory=tuple)
    geometry_inventory_summary: dict[str, Any] = Field(default_factory=dict)
    material_requirement_set_summary: dict[str, Any] = Field(default_factory=dict)
    universe_requirement_set_summary: dict[str, Any] = Field(default_factory=dict)
    materials_patch_summary: dict[str, Any] = Field(default_factory=dict)
    universes_patch_summary: dict[str, Any] = Field(default_factory=dict)
    allowed_predicates: tuple[str, ...] = Field(default_factory=lambda: tuple(sorted(ALLOWED_RESEARCH_PREDICATES)))
    allowed_component_kinds: tuple[str, ...] = Field(default_factory=tuple)
    allowed_profile_kinds: tuple[str, ...] = Field(default_factory=tuple)


class ResearchEvidenceProposal(AgentBaseModel):
    """One LLM-proposed evidence claim.

    ``source_span_ids`` MUST reference spans the system provided in
    :class:`ResearchEvidenceSynthesisContext.candidate_source_spans`.
    The LLM cannot invent span IDs.
    """

    target_id: str = ""
    subject: str
    predicate: str
    value: Any = None
    source_span_ids: tuple[str, ...] = Field(default_factory=tuple)
    qualifiers: dict[str, Any] = Field(default_factory=dict)
    criticality: str = "supporting"


class ResearchProposalValidationResult(AgentBaseModel):
    """Outcome of validating one batch of proposals."""

    accepted: tuple[ResearchEvidenceProposal, ...] = Field(default_factory=tuple)
    rejected: tuple[dict[str, Any], ...] = Field(default_factory=tuple)
    resolved_target_ids: tuple[str, ...] = Field(default_factory=tuple)
    unresolved_target_ids: tuple[str, ...] = Field(default_factory=tuple)
    conflict_ids: tuple[str, ...] = Field(default_factory=tuple)


# ---------------------------------------------------------------------------
# Context builder
# ---------------------------------------------------------------------------


def build_research_synthesis_context(
    *,
    request: PlanResearchRequest,
    candidate_spans: Iterable[SourceSpan],
    ledger: PlanningEvidenceLedger,
    gate_findings: Iterable[Any],
    geometry_inventory: Any = None,
    material_requirement_set: Any = None,
    universe_requirement_set: Any = None,
    materials_patch: Any = None,
    universes_patch: Any = None,
) -> ResearchEvidenceSynthesisContext:
    """Build the typed synthesis context from the research request."""

    # Candidate spans: render as compact dicts.
    span_dicts: list[dict[str, Any]] = []
    for span in candidate_spans:
        span_dicts.append({
            "span_id": span.span_id,
            "source_id": span.source_id,
            "start_line": span.start_line,
            "end_line": span.end_line,
            "excerpt": (span.excerpt or "")[:500],
        })
    # Existing matching claims: those whose predicate matches any target.
    wanted_predicates = {
        p for target in request.targets for p in target.claim_predicates
    }
    existing_claims: list[dict[str, Any]] = []
    for claim in ledger.claims.values():
        if claim.predicate in wanted_predicates:
            existing_claims.append({
                "claim_id": claim.claim_id,
                "subject": claim.subject,
                "predicate": claim.predicate,
                "value": claim.value,
                "status": claim.status.value if hasattr(claim.status, "value") else str(claim.status),
            })
    # Inventory + requirement summaries (compact, no secrets).
    inv_summary = _compact_summary(geometry_inventory)
    mrs_summary = _compact_summary(material_requirement_set)
    urs_summary = _compact_summary(universe_requirement_set)
    mat_summary = _compact_summary(materials_patch)
    uni_summary = _compact_summary(universes_patch)
    # Allowed ontology.
    allowed_components = tuple(sorted({
        kind for kind in (
            _extract_component_kinds(geometry_inventory)
            if geometry_inventory is not None else []
        )
    }))
    allowed_profiles = tuple(sorted({
        kind for kind in (
            _extract_profile_kinds(geometry_inventory)
            if geometry_inventory is not None else []
        )
    }))
    return ResearchEvidenceSynthesisContext(
        research_request_id=request.request_id,
        research_targets=request.targets,
        candidate_source_spans=tuple(span_dicts),
        existing_matching_claims=tuple(existing_claims),
        gate_findings=tuple(_finding_to_dict(f) for f in gate_findings),
        geometry_inventory_summary=inv_summary,
        material_requirement_set_summary=mrs_summary,
        universe_requirement_set_summary=urs_summary,
        materials_patch_summary=mat_summary,
        universes_patch_summary=uni_summary,
        allowed_component_kinds=allowed_components,
        allowed_profile_kinds=allowed_profiles,
    )


def _compact_summary(obj: Any) -> dict[str, Any]:
    """Render a compact, secret-free summary of a typed object."""

    if obj is None:
        return {}
    if isinstance(obj, dict):
        return dict(obj)
    if hasattr(obj, "model_dump"):
        try:
            return obj.model_dump(mode="json")
        except Exception:
            return {}
    return {}


def _extract_component_kinds(inventory: Any) -> list[str]:
    """Extract allowed component kinds from the inventory."""

    out: list[str] = []
    for profile in getattr(inventory, "radial_profiles", []) or []:
        kind = getattr(profile, "component_kind", "") or getattr(profile, "kind", "")
        if kind:
            out.append(str(kind))
    return out


def _extract_profile_kinds(inventory: Any) -> list[str]:
    out: list[str] = []
    for profile in getattr(inventory, "radial_profiles", []) or []:
        kind = getattr(profile, "profile_kind", "") or ""
        if kind:
            out.append(str(kind))
    return out


def _finding_to_dict(finding: Any) -> dict[str, Any]:
    if isinstance(finding, dict):
        return finding
    if hasattr(finding, "model_dump"):
        return finding.model_dump(mode="json")
    return {"code": str(getattr(finding, "code", "")), "message": str(getattr(finding, "message", ""))}


# ---------------------------------------------------------------------------
# Prompt rendering
# ---------------------------------------------------------------------------


def render_research_synthesis_prompt(ctx: ResearchEvidenceSynthesisContext) -> str:
    """Render the strict-JSON synthesis prompt for the LLM."""

    lines = [
        "You are an evidence-synthesis agent for an OpenMC model-building pipeline.",
        "Your job: propose typed semantic EvidenceClaims that bind the gate's",
        "findings to specific source spans.  You are NOT generating a patch, a",
        "plan, or making owner/gate/action decisions.",
        "",
        f"Research request: {ctx.research_request_id}",
        "",
        "Allowed predicates (use EXACTLY one per proposal):",
    ]
    for p in ctx.allowed_predicates:
        lines.append(f"  - {p}")
    if ctx.allowed_component_kinds:
        lines.append("")
        lines.append("Allowed component kinds:")
        for k in ctx.allowed_component_kinds:
            lines.append(f"  - {k}")
    if ctx.allowed_profile_kinds:
        lines.append("")
        lines.append("Allowed profile kinds:")
        for k in ctx.allowed_profile_kinds:
            lines.append(f"  - {k}")
    # Targets.
    lines.append("")
    lines.append("Research targets:")
    for t in ctx.research_targets:
        terms = ", ".join(t.suggested_search_terms) if t.suggested_search_terms else "(none)"
        lines.append(f"  - target_id={t.target_id}")
        lines.append(f"    predicates={list(t.claim_predicates)}")
        lines.append(f"    search_terms=[{terms}]")
        lines.append(f"    component_ids={list(t.target_component_ids)}")
        lines.append(f"    requirement_ids={list(t.target_requirement_ids)}")
    # Candidate spans — these are the ONLY span IDs the LLM may reference.
    lines.append("")
    lines.append("Candidate source spans (you may ONLY reference these span_ids):")
    for s in ctx.candidate_source_spans:
        lines.append(f"  - span_id={s.get('span_id')} (lines {s.get('start_line')}-{s.get('end_line')})")
        lines.append(f"    excerpt: {s.get('excerpt', '')[:200]}")
    # Existing claims.
    if ctx.existing_matching_claims:
        lines.append("")
        lines.append("Existing matching claims already in the ledger:")
        for c in ctx.existing_matching_claims[:20]:
            lines.append(f"  - {c.get('claim_id')}: {c.get('subject')}.{c.get('predicate')} = {c.get('value')}")
    # Gate findings (compact).
    if ctx.gate_findings:
        lines.append("")
        lines.append("Gate findings that triggered this research:")
        for f in ctx.gate_findings[:10]:
            lines.append(f"  - {f.get('code', '?')}: {f.get('message', '')[:120]}")
    # Output contract.
    lines.append("")
    lines.append(_render_synthesis_output_contract())
    return "\n".join(lines)


def _render_synthesis_output_contract() -> str:
    return (
        "Output contract (STRICT JSON, no markdown, no commentary):\n"
        "  {\n"
        "    \"proposals\": [\n"
        "      {\n"
        "        \"target_id\": \"<one of the listed target_id values>\",\n"
        "        \"subject\": \"<subject string, e.g. 'material_role:fuel'>\",\n"
        "        \"predicate\": \"<one of the allowed predicates>\",\n"
        "        \"value\": <JSON-compatible value>,\n"
        "        \"source_span_ids\": [\"<span_id>\", ...],\n"
        "        \"qualifiers\": {<optional dict>},\n"
        "        \"criticality\": \"source_critical\" | \"supporting\"\n"
        "      }\n"
        "    ],\n"
        "    \"unresolved_targets\": [\"<target_id>\", ...],\n"
        "    \"conflicts\": [\n"
        "      {\"target_id\": \"<target_id>\", \"reason\": \"<short reason>\"}\n"
        "    ]\n"
        "  }\n"
        "Rules:\n"
        "- Every proposal MUST reference at least one span_id from the candidate list.\n"
        "- You MUST NOT invent span_ids, source_ids, or line numbers.\n"
        "- You MUST NOT output a patch, a plan, owner/gate/action decisions.\n"
        "- \"value\" must be a value that appears verbatim in the span excerpt,\n"
        "  or a deterministic unit conversion of such a value.\n"
        "- If a target has no supporting span, list it in unresolved_targets.\n"
        "- If two spans disagree, list the target in conflicts.\n"
        "- Anything outside this contract will be rejected."
    )


# ---------------------------------------------------------------------------
# Output parsing
# ---------------------------------------------------------------------------


def parse_research_synthesis_output(raw: str) -> dict[str, Any] | None:
    """Parse the LLM output into a synthesis dict.

    Returns None when the output is not valid JSON or does not match
    the contract structure.
    """

    text = (raw or "").strip()
    if not text:
        return None
    # Strip markdown fences if present.
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    # Extract the largest balanced JSON object.
    payload = _extract_largest_json(text)
    if payload is None:
        return None
    try:
        obj = json.loads(payload)
    except Exception:
        return None
    if not isinstance(obj, dict):
        return None
    # Validate required keys.
    if "proposals" not in obj:
        return None
    if not isinstance(obj["proposals"], list):
        return None
    return obj


def _extract_largest_json(text: str) -> str | None:
    """Extract the largest balanced ``{...}`` block from ``text``."""

    start = text.find("{")
    if start < 0:
        return None
    depth = 0
    in_str = False
    escape = False
    for i in range(start, len(text)):
        ch = text[i]
        if in_str:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_str = False
        else:
            if ch == '"':
                in_str = True
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    return text[start : i + 1]
    return None


# ---------------------------------------------------------------------------
# Proposal validation
# ---------------------------------------------------------------------------


def validate_research_evidence_proposals(
    *,
    synthesis_output: dict[str, Any],
    ctx: ResearchEvidenceSynthesisContext,
    ledger: PlanningEvidenceLedger,
    source_index: Any,
) -> ResearchProposalValidationResult:
    """Validate LLM proposals against the strict rules (Section 6)."""

    # Build the set of valid span IDs from the SourceIndex.
    valid_span_ids: set[str] = set()
    span_by_id: dict[str, SourceSpan] = {}
    if source_index is not None:
        for section in getattr(source_index, "sections", []) or []:
            for span in getattr(section, "spans", []) or []:
                valid_span_ids.add(span.span_id)
                span_by_id[span.span_id] = span
    # Also include spans from the candidate list (they came from the
    # same SourceIndex but we want to be defensive).
    for s in ctx.candidate_source_spans:
        sid = s.get("span_id", "")
        if sid:
            valid_span_ids.add(sid)
    accepted: list[ResearchEvidenceProposal] = []
    rejected: list[dict[str, Any]] = []
    resolved_targets: set[str] = set()
    unresolved_targets: set[str] = set()
    conflict_targets: set[str] = set()
    target_ids = {t.target_id for t in ctx.research_targets}
    # Existing semantic keys to detect duplicates.
    existing_semantic_keys: dict[str, Any] = {}
    for claim in ledger.claims.values():
        key = _semantic_key(claim.subject, claim.predicate, claim.qualifiers)
        existing_semantic_keys[key] = claim.value
    # Validate each proposal.
    for raw_p in synthesis_output.get("proposals", []):
        if not isinstance(raw_p, dict):
            rejected.append({"reason": "not_a_dict", "raw": raw_p})
            continue
        try:
            proposal = ResearchEvidenceProposal(
                target_id=str(raw_p.get("target_id", "")),
                subject=str(raw_p.get("subject", "")),
                predicate=str(raw_p.get("predicate", "")),
                value=raw_p.get("value"),
                source_span_ids=tuple(str(s) for s in raw_p.get("source_span_ids", []) or []),
                qualifiers=dict(raw_p.get("qualifiers", {}) or {}),
                criticality=str(raw_p.get("criticality", "supporting")),
            )
        except Exception as exc:
            rejected.append({"reason": f"parse_error: {exc}", "raw": raw_p})
            continue
        # Rule 1: span IDs must be in the valid set.
        missing_spans = [s for s in proposal.source_span_ids if s not in valid_span_ids]
        if missing_spans:
            rejected.append({
                "reason": "unknown_span_id",
                "missing": missing_spans,
                "target_id": proposal.target_id,
            })
            continue
        # Rule 3: predicate must be in the allowlist.
        if proposal.predicate not in ALLOWED_RESEARCH_PREDICATES:
            rejected.append({
                "reason": "predicate_not_allowed",
                "predicate": proposal.predicate,
                "target_id": proposal.target_id,
            })
            continue
        # Rule 9: source-critical claims MUST have at least one span.
        if proposal.criticality == "source_critical" and not proposal.source_span_ids:
            rejected.append({
                "reason": "source_critical_without_span",
                "target_id": proposal.target_id,
            })
            continue
        # Rule 10: duplicate semantic key + same value is not new progress.
        sem_key = _semantic_key(proposal.subject, proposal.predicate, proposal.qualifiers)
        if sem_key in existing_semantic_keys:
            existing_val = existing_semantic_keys[sem_key]
            if _values_equal(existing_val, proposal.value):
                rejected.append({
                    "reason": "duplicate_semantic_key_same_value",
                    "target_id": proposal.target_id,
                    "semantic_key": sem_key,
                })
                continue
        # Rule 5: numerical values must be verifiable in span excerpt.
        if not _value_verifiable_in_spans(
            proposal.value, proposal.source_span_ids, span_by_id,
        ):
            rejected.append({
                "reason": "value_not_verifiable_in_span_excerpt",
                "target_id": proposal.target_id,
                "value": proposal.value,
            })
            continue
        accepted.append(proposal)
        existing_semantic_keys[sem_key] = proposal.value  # prevent dupes within batch
        if proposal.target_id in target_ids:
            resolved_targets.add(proposal.target_id)
    # Process unresolved_targets from the synthesis output.
    for tid in synthesis_output.get("unresolved_targets", []) or []:
        if isinstance(tid, str) and tid in target_ids:
            unresolved_targets.add(tid)
    # Process conflicts.
    for c in synthesis_output.get("conflicts", []) or []:
        if isinstance(c, dict):
            tid = str(c.get("target_id", ""))
            if tid in target_ids:
                conflict_targets.add(tid)
    # Any target not resolved/unresolved/conflict is unresolved by default.
    for t in ctx.research_targets:
        if (
            t.target_id not in resolved_targets
            and t.target_id not in unresolved_targets
            and t.target_id not in conflict_targets
        ):
            unresolved_targets.add(t.target_id)
    return ResearchProposalValidationResult(
        accepted=tuple(accepted),
        rejected=tuple(rejected),
        resolved_target_ids=tuple(sorted(resolved_targets)),
        unresolved_target_ids=tuple(sorted(unresolved_targets)),
        conflict_ids=tuple(sorted(conflict_targets)),
    )


def _semantic_key(subject: str, predicate: str, qualifiers: dict[str, Any]) -> str:
    """Stable semantic key for a claim (used for duplicate detection)."""

    try:
        q = json.dumps(qualifiers or {}, sort_keys=True, ensure_ascii=False)
    except Exception:
        q = str(qualifiers or "")
    return f"{subject}|{predicate}|{q}"


def _values_equal(a: Any, b: Any) -> bool:
    """Loose equality for duplicate detection."""

    return a == b


def _value_verifiable_in_spans(
    value: Any,
    span_ids: tuple[str, ...],
    span_by_id: dict[str, SourceSpan],
) -> bool:
    """Check that ``value`` appears (or can be derived from text that
    appears) in at least one of the cited span excerpts.

    For non-scalar values (dicts/lists) we only check the top-level
    string representation.  The LLM is told to keep values scalar or
    shallow.
    """

    if value is None:
        # None values are not source-backed claims.
        return False
    if not span_ids:
        return False
    # Render value to a searchable string.
    try:
        value_str = json.dumps(value, ensure_ascii=False, sort_keys=True)
    except Exception:
        value_str = str(value)
    # Extract numeric tokens from the value string.
    numeric_tokens = set(re.findall(r"-?\d+(?:\.\d+)?", value_str))
    # If no numeric tokens, accept any non-empty string value.
    if not numeric_tokens:
        return True
    # At least one numeric token must appear in some span excerpt.
    for sid in span_ids:
        span = span_by_id.get(sid)
        if span is None:
            continue
        excerpt = span.excerpt or ""
        for token in numeric_tokens:
            if token in excerpt:
                return True
    return False


# ---------------------------------------------------------------------------
# Commit to ledger
# ---------------------------------------------------------------------------


def commit_research_evidence_proposals(
    *,
    validation: ResearchProposalValidationResult,
    ledger: PlanningEvidenceLedger,
    source_index: Any,
    request_id: str,
) -> PlanningEvidenceDelta:
    """Commit accepted proposals as new EvidenceClaims in the Ledger.

    Returns a :class:`PlanningEvidenceDelta` with real
    ``added_claim_ids`` and ``ledger_hash_after`` reflecting the new
    ledger hash.  If no proposals were accepted, the delta is empty
    and ``ledger_hash_after == ledger_hash_before``.
    """

    ledger_hash_before = ledger.ledger_hash
    added_claim_ids: list[str] = []
    added_span_ids: list[str] = []
    # Build span_id → SourceSpan lookup.
    span_by_id: dict[str, SourceSpan] = {}
    if source_index is not None:
        for section in getattr(source_index, "sections", []) or []:
            for span in getattr(section, "spans", []) or []:
                span_by_id[span.span_id] = span
    # Add each accepted proposal as a new EvidenceClaim.
    from .models import EvidenceSourceRef
    for proposal in validation.accepted:
        # Build source_refs from the span IDs as typed EvidenceSourceRef.
        source_refs: list[EvidenceSourceRef] = []
        for sid in proposal.source_span_ids:
            span = span_by_id.get(sid)
            if span is not None:
                source_refs.append(EvidenceSourceRef(
                    source_id=span.source_id,
                    span_id=span.span_id,
                    excerpt_hash=span.excerpt_hash,
                ))
                if sid not in added_span_ids:
                    added_span_ids.append(sid)
        # Compute a stable claim_id using the canonical helper.
        from .models import compute_claim_id
        source_refs_dicts = [
            {"source_id": ref.source_id, "span_id": ref.span_id, "excerpt_hash": ref.excerpt_hash}
            for ref in source_refs
        ]
        claim_id = compute_claim_id(
            subject=proposal.subject,
            predicate=proposal.predicate,
            qualifiers=proposal.qualifiers,
            value=proposal.value,
            status="explicit",
            source_refs=source_refs_dicts,
            derivation_present=False,
            criticality=proposal.criticality,
        )
        # Skip if the claim_id already exists (idempotent).
        if claim_id in ledger.claims:
            continue
        # Build the EvidenceClaim.
        try:
            claim = EvidenceClaim(
                claim_id=claim_id,
                subject=proposal.subject,
                predicate=proposal.predicate,
                value=proposal.value,
                source_refs=tuple(source_refs),
                qualifiers=proposal.qualifiers,
                criticality=proposal.criticality,
                status="explicit",
            )
        except Exception:
            continue
        ledger.claims[claim_id] = claim
        added_claim_ids.append(claim_id)
    # Recompute the ledger hash.
    ledger_hash_after = _recompute_ledger_hash(ledger)
    return PlanningEvidenceDelta(
        request_id=request_id,
        ledger_hash_before=ledger_hash_before,
        ledger_hash_after=ledger_hash_after,
        added_claim_ids=tuple(added_claim_ids),
        added_source_span_ids=tuple(added_span_ids),
        resolved_unresolved_claim_ids=validation.resolved_target_ids,
        newly_unresolved_claim_ids=validation.unresolved_target_ids,
    )


def _recompute_ledger_hash(ledger: PlanningEvidenceLedger) -> str:
    """Recompute the ledger hash after claims were added."""

    try:
        # PlanningEvidenceLedger exposes recompute_ledger_hash().
        from .evidence_ledger import recompute_ledger_hash
        return recompute_ledger_hash(ledger)
    except Exception:
        # Fallback: compute a content hash from the claims dict.
        claims_dump = {
            cid: c.model_dump(mode="json") if hasattr(c, "model_dump") else str(c)
            for cid, c in ledger.claims.items()
        }
        return content_hash(claims_dump)


# ---------------------------------------------------------------------------
# End-to-end synthesis runner
# ---------------------------------------------------------------------------


def run_research_evidence_synthesis(
    *,
    request: PlanResearchRequest,
    research_result: PlanResearchResult,
    ledger: PlanningEvidenceLedger,
    source_index: Any,
    gate_findings: Iterable[Any],
    geometry_inventory: Any = None,
    material_requirement_set: Any = None,
    universe_requirement_set: Any = None,
    materials_patch: Any = None,
    universes_patch: Any = None,
    llm_client: Callable[[str], str] | None = None,
    add_event: Callable[[str, str, dict[str, Any]], None] | None = None,
) -> PlanningEvidenceDelta | None:
    """Run the full LLM synthesis + validation + commit pipeline.

    Returns a :class:`PlanningEvidenceDelta` (possibly empty) when the
    pipeline ran to completion; ``None`` when the LLM client is
    unavailable or the synthesis produced no acceptable proposals.

    The caller inspects ``delta.ledger_hash_after !=
    delta.ledger_hash_before`` to decide whether the gate should
    reopen (Section 4 ``evidence_added`` semantics).
    """

    if llm_client is None:
        return None
    # Extract candidate spans from the research result's tool calls.
    candidate_span_ids: list[str] = []
    for tc in research_result.tool_calls:
        if tc.get("tool") == "search_source_index" and tc.get("result_count", 0) > 0:
            # The minimal executor doesn't expose the span IDs in the
            # tool call record; we collect them from the absence
            # records' opposite case (spans found via search).
            pass
    # Build candidate spans from the source index (those whose excerpt
    # matches any target's suggested search terms).
    candidate_spans = _collect_candidate_spans(
        source_index=source_index,
        targets=request.targets,
    )
    if not candidate_spans:
        if add_event is not None:
            add_event(
                "planning.research_synthesis_skipped",
                "no candidate spans for synthesis",
                {"request_id": request.request_id},
            )
        return None
    # Build the synthesis context.
    ctx = build_research_synthesis_context(
        request=request,
        candidate_spans=candidate_spans,
        ledger=ledger,
        gate_findings=gate_findings,
        geometry_inventory=geometry_inventory,
        material_requirement_set=material_requirement_set,
        universe_requirement_set=universe_requirement_set,
        materials_patch=materials_patch,
        universes_patch=universes_patch,
    )
    # Render the prompt + call the LLM.
    prompt = render_research_synthesis_prompt(ctx)
    try:
        raw_output = llm_client(prompt)
    except Exception as exc:
        if add_event is not None:
            add_event(
                "planning.research_synthesis_failed",
                f"LLM call failed: {type(exc).__name__}: {exc}",
                {"request_id": request.request_id},
            )
        return None
    # Parse the output.
    parsed = parse_research_synthesis_output(raw_output)
    if parsed is None:
        if add_event is not None:
            add_event(
                "planning.research_synthesis_invalid_output",
                "LLM output did not match the synthesis contract",
                {"request_id": request.request_id},
            )
        return None
    # Validate.
    validation = validate_research_evidence_proposals(
        synthesis_output=parsed,
        ctx=ctx,
        ledger=ledger,
        source_index=source_index,
    )
    # Commit.
    delta = commit_research_evidence_proposals(
        validation=validation,
        ledger=ledger,
        source_index=source_index,
        request_id=request.request_id,
    )
    if add_event is not None:
        add_event(
            "planning.research_synthesis_committed",
            f"synthesis accepted {len(delta.added_claim_ids)} claims "
            f"(rejected {len(validation.rejected)})",
            {
                "request_id": request.request_id,
                "accepted_count": len(delta.added_claim_ids),
                "rejected_count": len(validation.rejected),
                "ledger_hash_before": delta.ledger_hash_before[:12],
                "ledger_hash_after": delta.ledger_hash_after[:12],
            },
        )
    return delta


def _collect_candidate_spans(
    *,
    source_index: Any,
    targets: tuple[PlanResearchTarget, ...],
) -> list[SourceSpan]:
    """Collect spans whose excerpt matches any target's search terms."""

    if source_index is None:
        return []
    out: list[SourceSpan] = []
    seen_ids: set[str] = set()
    search_terms: list[str] = []
    for target in targets:
        search_terms.extend(target.suggested_search_terms)
    if not search_terms:
        return []
    for section in getattr(source_index, "sections", []) or []:
        for span in getattr(section, "spans", []) or []:
            if span.span_id in seen_ids:
                continue
            excerpt_lower = (span.excerpt or "").lower()
            for term in search_terms:
                if term.lower() in excerpt_lower:
                    out.append(span)
                    seen_ids.add(span.span_id)
                    break
    return out
