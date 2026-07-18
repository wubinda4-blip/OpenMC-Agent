"""Typed data models for plan investigation: sources and evidence claims.

Conventions
-----------
* Every model derives from :class:`openmc_agent.schemas.AgentBaseModel` for
  consistency with the rest of the plan closed-loop protocol.
* Models that MUST preserve exact textual whitespace (source excerpts, span
  text) override ``str_strip_whitespace`` to ``False`` so the LLM cannot
  silently trim evidence.
* IDs (``source_id``, ``span_id``, ``claim_id``, ``derivation_id``,
  ``conflict_id``) are always Python-computed from semantic content.  An LLM
  may not supply its own ID; if it does, the model rejects it.
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Literal

from pydantic import ConfigDict, Field, field_validator, model_validator

from openmc_agent.schemas import AgentBaseModel

from .errors import (
    PlanInvestigationIssue,
    SOURCE_HASH_MISMATCH,
    SOURCE_SPAN_INVALID,
)
from .hashing import content_hash, short_id

__all__ = [
    "SourceKind",
    "ALLOWED_STEP1_SOURCE_KINDS",
    "SourceDocument",
    "SourceSection",
    "SourceSpan",
    "EvidenceStatus",
    "EvidenceCriticality",
    "EvidenceSourceRef",
    "EvidenceDerivation",
    "ALLOWED_DERIVATION_OPERATIONS",
    "EvidenceClaim",
    "EvidenceConflict",
    "ConflictResolutionStatus",
    "EvidenceLedgerSummary",
    "semantic_key_for_claim",
]


# ---------------------------------------------------------------------------
# Source kind enum
# ---------------------------------------------------------------------------


class SourceKind(str, Enum):
    """Kind of source a document came from.

    Step 1 only constructs :attr:`USER_REQUIREMENT` and
    :attr:`ATTACHED_DOCUMENT`.  The other values are reserved so the schema is
    forward-compatible with Step 2+ (repository / OpenMC docs / official web).
    """

    USER_REQUIREMENT = "user_requirement"
    ATTACHED_DOCUMENT = "attached_document"
    REPOSITORY = "repository"
    OPENMC_DOCS = "openmc_docs"
    OFFICIAL_WEB = "official_web"


#: Source kinds that Step 1 is permitted to actually construct.  Other kinds
#: are reserved for later steps that add the corresponding tool surface.
ALLOWED_STEP1_SOURCE_KINDS: frozenset[str] = frozenset(
    {SourceKind.USER_REQUIREMENT.value, SourceKind.ATTACHED_DOCUMENT.value}
)


# ---------------------------------------------------------------------------
# Source documents, sections, spans
# ---------------------------------------------------------------------------


def _no_strip_model_config() -> ConfigDict:
    return ConfigDict(extra="forbid", str_strip_whitespace=False)


class SourceDocument(AgentBaseModel):
    """A single user-supplied document that has been canonicalized and indexed.

    The ``content_hash`` and ``normalized_content_hash`` fields are computed
    by the source indexer over the canonical normalized text; callers MUST NOT
    set them manually.  ``source_id`` is derived deterministically and must
    match the recomputed value.
    """

    model_config = _no_strip_model_config()

    source_id: str
    source_kind: SourceKind
    title: str
    origin_label: str = ""
    content_hash: str
    normalized_content_hash: str
    line_count: int
    char_count: int
    section_count: int
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: str | None = None

    @model_validator(mode="after")
    def _validate_source_id(self) -> "SourceDocument":
        expected = _compute_source_id(
            source_kind=self.source_kind.value,
            normalized_title=_normalize_title(self.title),
            normalized_content_hash=self.normalized_content_hash,
        )
        # Empty source_id is the "auto-fill" sentinel: callers may pass ""
        # and let Python compute the deterministic value.  Any non-empty
        # value MUST match the recomputed id.
        if not self.source_id:
            object.__setattr__(self, "source_id", expected)
        elif self.source_id != expected:
            raise PlanInvestigationIssue(
                "plan_investigation.source_id_mismatch",
                "source_id must match the deterministic Python-computed id",
                details={"expected": expected, "actual": self.source_id},
            )
        if self.line_count < 0 or self.char_count < 0 or self.section_count < 0:
            raise PlanInvestigationIssue(
                SOURCE_SPAN_INVALID,
                "line_count / char_count / section_count must be non-negative",
            )
        return self


class SourceSection(AgentBaseModel):
    """A heading-delimited region of a :class:`SourceDocument`.

    Synthetic root sections (heading == "" and level == 0) cover the whole
    document and always exist, even when the document has no Markdown
    headings.
    """

    model_config = _no_strip_model_config()

    section_id: str
    source_id: str
    heading: str
    level: int = Field(ge=0, le=6)
    section_path: tuple[str, ...] = Field(default_factory=tuple)
    start_line: int = Field(ge=1)
    end_line: int = Field(ge=1)
    parent_section_id: str | None = None
    content_hash: str

    @model_validator(mode="after")
    def _validate_range(self) -> "SourceSection":
        if self.end_line < self.start_line:
            raise PlanInvestigationIssue(
                SOURCE_SPAN_INVALID,
                "section end_line must be >= start_line",
                details={
                    "source_id": self.source_id,
                    "start_line": self.start_line,
                    "end_line": self.end_line,
                },
            )
        if self.section_path and self.section_path[0] != "":
            # Non-root paths always begin with the synthetic-root sentinel.
            raise PlanInvestigationIssue(
                SOURCE_SPAN_INVALID,
                "section_path must start with the synthetic root sentinel ''",
                details={"section_path": list(self.section_path)},
            )
        return self


class SourceSpan(AgentBaseModel):
    """An immutable, hash-verified excerpt of a :class:`SourceDocument`.

    ``excerpt`` MUST equal the exact bytes of the source document between
    ``start_line`` and ``end_line`` (inclusive).  ``excerpt_hash`` is computed
    by Python; an LLM-supplied value that does not match is rejected.

    Spans are constructed by :meth:`SourceIndex.make_span`; constructing one
    by hand requires passing the verified ``excerpt`` and the
    Python-computed ``excerpt_hash``.
    """

    model_config = _no_strip_model_config()

    span_id: str
    source_id: str
    start_line: int = Field(ge=1)
    end_line: int = Field(ge=1)
    section_id: str
    section_path: tuple[str, ...] = Field(default_factory=tuple)
    excerpt: str
    excerpt_hash: str

    @model_validator(mode="after")
    def _validate_span(self) -> "SourceSpan":
        if self.end_line < self.start_line:
            raise PlanInvestigationIssue(
                SOURCE_SPAN_INVALID,
                "span end_line must be >= start_line",
                details={
                    "source_id": self.source_id,
                    "start_line": self.start_line,
                    "end_line": self.end_line,
                },
            )
        expected_excerpt_hash = content_hash(self.excerpt)
        if self.excerpt_hash != expected_excerpt_hash:
            raise PlanInvestigationIssue(
                SOURCE_HASH_MISMATCH,
                "excerpt_hash does not match the excerpt content",
                details={
                    "source_id": self.source_id,
                    "expected": expected_excerpt_hash,
                    "actual": self.excerpt_hash,
                },
            )
        expected_span_id = _compute_span_id(
            source_id=self.source_id,
            start_line=self.start_line,
            end_line=self.end_line,
            excerpt_hash=self.excerpt_hash,
        )
        if not self.span_id:
            object.__setattr__(self, "span_id", expected_span_id)
        elif self.span_id != expected_span_id:
            raise PlanInvestigationIssue(
                "plan_investigation.span_id_mismatch",
                "span_id must match the deterministic Python-computed id",
                details={"expected": expected_span_id, "actual": self.span_id},
            )
        return self


# ---------------------------------------------------------------------------
# ID computation helpers
# ---------------------------------------------------------------------------


def _normalize_title(title: str) -> str:
    """Normalize a source title for ID derivation.

    Uses NFC and collapses runs of ASCII whitespace to a single space.  This
    is intentionally narrower than the source-text normalizer: a title is a
    short identifier and reasonable to normalize aggressively, while body
    text must be preserved verbatim.
    """

    import unicodedata

    normalized = unicodedata.normalize("NFC", title).strip()
    return " ".join(normalized.split())


def _compute_source_id(*, source_kind: str, normalized_title: str, normalized_content_hash: str) -> str:
    payload = {"k": source_kind, "t": normalized_title, "h": normalized_content_hash}
    return short_id("src", payload)


def _compute_span_id(*, source_id: str, start_line: int, end_line: int, excerpt_hash: str) -> str:
    payload = {
        "s": source_id,
        "r": [int(start_line), int(end_line)],
        "h": excerpt_hash,
    }
    return short_id("span", payload)


# ---------------------------------------------------------------------------
# Evidence models
# ---------------------------------------------------------------------------


class EvidenceStatus(str, Enum):
    """Lifecycle status of an :class:`EvidenceClaim`."""

    EXPLICIT = "explicit"
    DETERMINISTICALLY_DERIVED = "deterministically_derived"
    EXTERNAL_OFFICIAL = "external_official"
    ASSUMPTION = "assumption"
    UNRESOLVED = "unresolved"
    CONFLICT = "conflict"


class EvidenceCriticality(str, Enum):
    """How central a claim is to satisfying a downstream requirement."""

    INFORMATIONAL = "informational"
    SUPPORTING = "supporting"
    SOURCE_CRITICAL = "source_critical"


class EvidenceSourceRef(AgentBaseModel):
    """Pointer from a claim to a verified span in a :class:`SourceDocument`."""

    source_id: str
    span_id: str
    excerpt_hash: str


class EvidenceDerivation(AgentBaseModel):
    """Deterministic derivation that produced a claim value.

    The ``operation`` MUST be in :data:`ALLOWED_DERIVATION_OPERATIONS`; the
    Python re-computation in :mod:`openmc_agent.plan_investigation.evidence_ledger`
    is the source of truth — ``result_hash`` is checked against the recomputed
    value when the derivation is added.

    ``description`` is audit-only prose and is NOT part of any hash.
    """

    derivation_id: str
    operation: str
    input_claim_ids: tuple[str, ...] = Field(default_factory=tuple)
    parameters: dict[str, Any] = Field(default_factory=dict)
    result_hash: str
    description: str = ""

    @field_validator("operation")
    @classmethod
    def _operation_in_allowlist(cls, value: str) -> str:
        if value not in ALLOWED_DERIVATION_OPERATIONS:
            raise PlanInvestigationIssue(
                "plan_investigation.derivation_operation_not_allowed",
                f"derivation operation '{value}' is not in the allow-list",
                details={"allowed": sorted(ALLOWED_DERIVATION_OPERATIONS)},
            )
        return value

    @field_validator("input_claim_ids")
    @classmethod
    def _inputs_nonempty(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if len(value) == 0:
            raise PlanInvestigationIssue(
                "plan_investigation.derived_claim_without_inputs",
                "derivation must declare at least one input_claim_id",
            )
        return value

    @model_validator(mode="after")
    def _validate_derivation_id(self) -> "EvidenceDerivation":
        expected = _compute_derivation_id(
            operation=self.operation,
            input_claim_ids=list(self.input_claim_ids),
            parameters=self.parameters,
            result_hash=self.result_hash,
        )
        if not self.derivation_id:
            object.__setattr__(self, "derivation_id", expected)
        elif self.derivation_id != expected:
            raise PlanInvestigationIssue(
                "plan_investigation.derivation_id_mismatch",
                "derivation_id must match the deterministic Python-computed id",
                details={"expected": expected, "actual": self.derivation_id},
            )
        if any(cid == self.derivation_id for cid in self.input_claim_ids):
            raise PlanInvestigationIssue(
                "plan_investigation.derivation_cycle",
                "derivation cannot reference itself as an input",
            )
        return self


#: Allow-list of deterministic operations a derivation may declare.  Anything
#: outside this set is rejected at parse time.
ALLOWED_DERIVATION_OPERATIONS: frozenset[str] = frozenset(
    {
        "integer_product",
        "integer_sum",
        "matrix_shape",
        "count_by_label",
        "equality_alias",
        "interval_length",
    }
)


def _compute_derivation_id(
    *,
    operation: str,
    input_claim_ids: list[str],
    parameters: dict[str, Any],
    result_hash: str,
) -> str:
    payload = {
        "op": operation,
        "in": list(input_claim_ids),
        "p": parameters,
        "r": result_hash,
    }
    return short_id("der", payload)


class EvidenceClaim(AgentBaseModel):
    """A single piece of evidence about the modeling problem.

    ``claim_id`` is Python-computed from the semantic payload
    (subject/predicate/qualifiers/value/status); it never includes timestamps,
    artifact paths or run ids.  Two claims with the same semantic key but
    different values are *different claims* and (when both explicit) become
    candidates for an :class:`EvidenceConflict`.
    """

    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
        validate_assignment=True,
    )

    claim_id: str
    subject: str
    predicate: str
    qualifiers: dict[str, Any] = Field(default_factory=dict)
    value: Any = None
    status: EvidenceStatus
    criticality: EvidenceCriticality = EvidenceCriticality.INFORMATIONAL
    source_refs: tuple[EvidenceSourceRef, ...] = Field(default_factory=tuple)
    derivation: EvidenceDerivation | None = None
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    required_by_patch_types: tuple[str, ...] = Field(default_factory=tuple)
    required_by_json_paths: tuple[str, ...] = Field(default_factory=tuple)
    confirmed_by_human: bool = False
    human_confirmation_id: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    # ------------------------------------------------------------------
    # Field-level validators
    # ------------------------------------------------------------------

    @field_validator("subject", "predicate")
    @classmethod
    def _nonempty(cls, value: str) -> str:
        if not value or not value.strip():
            raise PlanInvestigationIssue(
                "plan_investigation.claim_value_not_json",
                "subject and predicate must be non-empty",
            )
        return value

    @field_validator("source_refs", "required_by_patch_types", "required_by_json_paths")
    @classmethod
    def _dedupe(cls, value: tuple[Any, ...]) -> tuple[Any, ...]:
        # Preserve order while removing duplicates.
        seen: set[Any] = set()
        out: list[Any] = []
        for item in value:
            key = item.model_dump(mode="json") if hasattr(item, "model_dump") else item
            key_repr = repr(sorted(key.items())) if isinstance(key, dict) else repr(key)
            if key_repr in seen:
                continue
            seen.add(key_repr)
            out.append(item)
        return tuple(out)

    # ------------------------------------------------------------------
    # Cross-field validators
    # ------------------------------------------------------------------

    @model_validator(mode="after")
    def _validate_consistency(self) -> "EvidenceClaim":
        # Phase 8A Step 1 policy: external_official is reserved for later
        # steps (web/docs retrieval).  Construction-time rejection enforces
        # the policy even if the LLM tries to bypass it.
        if self.status == EvidenceStatus.EXTERNAL_OFFICIAL:
            raise PlanInvestigationIssue(
                "plan_investigation.external_evidence_disabled",
                "external_official evidence is disabled in Phase 8A Step 1",
            )

        if self.status == EvidenceStatus.EXPLICIT:
            if not self.source_refs:
                raise PlanInvestigationIssue(
                    "plan_investigation.explicit_claim_without_source",
                    "explicit claims must cite at least one verified source span",
                    details={"subject": self.subject, "predicate": self.predicate},
                )
            if self.derivation is not None:
                raise PlanInvestigationIssue(
                    "plan_investigation.explicit_claim_without_source",
                    "explicit claims must not carry a derivation",
                )

        if self.status == EvidenceStatus.DETERMINISTICALLY_DERIVED:
            if self.derivation is None:
                raise PlanInvestigationIssue(
                    "plan_investigation.derived_claim_without_inputs",
                    "deterministically_derived claims must carry a derivation",
                    details={"claim_id": self.claim_id or "<unset>"},
                )

        if self.status == EvidenceStatus.CONFLICT:
            # Conflicts are produced only by the Python conflict detector.
            raise PlanInvestigationIssue(
                "plan_investigation.evidence_conflict",
                "claims with status=conflict are produced only by detect_conflicts()",
            )

        if self.confirmed_by_human and not self.human_confirmation_id:
            raise PlanInvestigationIssue(
                "plan_investigation.human_confirmation_missing",
                "confirmed_by_human claims must carry a human_confirmation_id",
            )

        # Validate JSON-compatibility of value/qualifiers BEFORE computing
        # the claim_id.  Pydantic accepts Path/datetime silently; we reject
        # them so the ledger hash stays stable across Python versions and
        # the canonical-JSON serializer cannot crash mid-construction.
        _assert_jsonable(self.value, label="claim.value")
        for key, val in self.qualifiers.items():
            _assert_jsonable(val, label=f"claim.qualifiers[{key!r}]")

        # claim_id must match the deterministic value computed from the
        # *semantic* payload.  Timestamps and paths are excluded by
        # construction (they're not part of the canonical payload below).
        expected_id = compute_claim_id(
            subject=self.subject,
            predicate=self.predicate,
            qualifiers=self.qualifiers,
            value=self.value,
            status=self.status.value,
            source_refs=[ref.model_dump(mode="json") for ref in self.source_refs],
            derivation_present=self.derivation is not None,
            criticality=self.criticality.value,
        )
        if not self.claim_id:
            object.__setattr__(self, "claim_id", expected_id)
        elif self.claim_id != expected_id:
            raise PlanInvestigationIssue(
                "plan_investigation.claim_id_mismatch",
                "claim_id must match the deterministic Python-computed id",
                details={"expected": expected_id, "actual": self.claim_id},
            )

        return self


def compute_claim_id(
    *,
    subject: str,
    predicate: str,
    qualifiers: dict[str, Any],
    value: Any,
    status: str,
    source_refs: list[dict[str, Any]],
    derivation_present: bool,
    criticality: str,
) -> str:
    """Deterministically compute ``claim_id`` from a claim's semantic payload."""
    payload = {
        "s": subject,
        "p": predicate,
        "q": _canonical_qualifiers(qualifiers),
        "v": _canonical_jsonable(value),
        "st": status,
        "sr": [_canonical_jsonable(ref) for ref in source_refs],
        "d": bool(derivation_present),
        "c": criticality,
    }
    return short_id("claim", payload)


def semantic_key_for_claim(claim: EvidenceClaim) -> str:
    """Return the deterministic semantic key for a claim.

    Two claims with the same subject/predicate/qualifiers (regardless of
    value) share the same semantic key.  Conflicts are detected per semantic
    key.
    """

    return _semantic_key(
        subject=claim.subject,
        predicate=claim.predicate,
        qualifiers=claim.qualifiers,
    )


def _semantic_key(*, subject: str, predicate: str, qualifiers: dict[str, Any]) -> str:
    payload = {
        "s": subject,
        "p": predicate,
        "q": _canonical_qualifiers(qualifiers),
    }
    return content_hash(payload)


def _canonical_qualifiers(qualifiers: dict[str, Any]) -> dict[str, Any]:
    return {str(k): _canonical_jsonable(v) for k, v in qualifiers.items()}


def _canonical_jsonable(value: Any) -> Any:
    # Replace tuples with lists so Python's tuple-vs-list distinction does
    # not introduce nondeterminism in the canonical JSON form.
    if isinstance(value, tuple):
        return [_canonical_jsonable(item) for item in value]
    if isinstance(value, list):
        return [_canonical_jsonable(item) for item in value]
    if isinstance(value, dict):
        return {str(k): _canonical_jsonable(v) for k, v in value.items()}
    return value


def _assert_jsonable(value: Any, *, label: str) -> None:
    import json
    import pathlib
    import datetime as _dt

    if isinstance(value, (pathlib.Path, _dt.datetime, _dt.date, _dt.timedelta, complex)):
        raise PlanInvestigationIssue(
            "plan_investigation.claim_value_not_json",
            f"{label} must be JSON-compatible (got {type(value).__name__})",
        )
    try:
        json.dumps(value, ensure_ascii=False, allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise PlanInvestigationIssue(
            "plan_investigation.claim_value_not_json",
            f"{label} must be JSON-compatible",
            details={"error": str(exc)},
        ) from exc


# ---------------------------------------------------------------------------
# Conflict + summary models
# ---------------------------------------------------------------------------


class ConflictResolutionStatus(str, Enum):
    UNRESOLVED = "unresolved"
    RESOLVED_BY_SOURCE_PRECEDENCE = "resolved_by_source_precedence"
    RESOLVED_BY_HUMAN = "resolved_by_human"


class EvidenceConflict(AgentBaseModel):
    """A set of mutually-incompatible :class:`EvidenceClaim` candidates.

    Conflicts are emitted only by :func:`detect_conflicts`.  ``resolution_status``
    starts at :attr:`ConflictResolutionStatus.UNRESOLVED`; Step 1 deliberately
    performs NO auto-resolution by source precedence — all candidates are
    preserved for explicit policy or human confirmation in a later step.
    """

    conflict_id: str
    semantic_key: str
    claim_ids: tuple[str, ...] = Field(default_factory=tuple)
    conflicting_values: tuple[Any, ...] = Field(default_factory=tuple)
    source_refs: tuple[EvidenceSourceRef, ...] = Field(default_factory=tuple)
    severity: Literal["warning", "error"] = "warning"
    resolution_status: ConflictResolutionStatus = ConflictResolutionStatus.UNRESOLVED
    resolved_claim_id: str | None = None
    human_confirmation_required: bool = True

    @model_validator(mode="after")
    def _validate_conflict_id(self) -> "EvidenceConflict":
        expected = short_id(
            "conflict",
            {
                "k": self.semantic_key,
                "c": list(self.claim_ids),
            },
        )
        if not self.conflict_id:
            object.__setattr__(self, "conflict_id", expected)
        elif self.conflict_id != expected:
            raise PlanInvestigationIssue(
                "plan_investigation.conflict_id_mismatch",
                "conflict_id must match the deterministic Python-computed id",
                details={"expected": expected, "actual": self.conflict_id},
            )
        return self


class EvidenceLedgerSummary(AgentBaseModel):
    """Roll-up of an evidence ledger for human/audit consumption."""

    total_claim_count: int = 0
    explicit_count: int = 0
    derived_count: int = 0
    assumption_count: int = 0
    unresolved_count: int = 0
    conflict_count: int = 0
    source_critical_count: int = 0
    source_critical_resolved_count: int = 0
    source_critical_unresolved_count: int = 0
