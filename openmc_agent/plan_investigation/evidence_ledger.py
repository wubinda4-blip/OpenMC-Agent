"""Typed :class:`PlanningEvidenceLedger` and its pure-Python API.

Design constraints
------------------
* Functions are pure with respect to global state; they operate on a
  locally-owned :class:`PlanningEvidenceLedger` instance and return it for
  chaining.  No singletons.
* The ledger hash is computed from sorted canonical payloads so insertion
  order and timestamps do not affect it.
* Derivations are recomputed by Python; the LLM-supplied ``result_hash`` is
  checked against the recomputed value and rejected on mismatch.
* Human-confirmed claims are immutable: a subsequent ``upsert`` that would
  change the value raises :class:`PlanInvestigationIssue` instead of
  silently overwriting.
* Conflicts are detected per semantic key.  Step 1 performs NO auto
  resolution by source precedence — all candidates are preserved.
"""

from __future__ import annotations

import math
from typing import Any, Iterable, Mapping

from pydantic import Field, PrivateAttr

from openmc_agent.schemas import AgentBaseModel

from .errors import (
    PlanInvestigationIssue,
    CONFIRMED_CLAIM_MUTATION,
    DERIVATION_CYCLE,
    DERIVATION_INPUT_MISSING,
    DERIVATION_RESULT_MISMATCH,
    EVIDENCE_CONFLICT,
    EXTERNAL_EVIDENCE_DISABLED,
    LEDGER_HASH_MISMATCH,
    SOURCE_REF_MISSING,
    STALE_DERIVED_CLAIM,
)
from .hashing import content_hash
from .models import (
    ALLOWED_DERIVATION_OPERATIONS,
    ConflictResolutionStatus,
    EvidenceClaim,
    EvidenceConflict,
    EvidenceCriticality,
    EvidenceDerivation,
    EvidenceLedgerSummary,
    EvidenceSourceRef,
    EvidenceStatus,
    SourceDocument,
    SourceSpan,
    compute_claim_id,
    semantic_key_for_claim,
)
from .source_index import SourceIndex

__all__ = [
    "LEDGER_VERSION",
    "PlanningEvidenceLedger",
    "create_empty_ledger",
    "add_claim",
    "upsert_claim",
    "add_derivation",
    "add_derived_claim",
    "detect_conflicts",
    "find_claims",
    "get_claim_by_id",
    "claims_for_json_path",
    "claims_for_patch_type",
    "unresolved_source_critical_claims",
    "find_stale_derived_claims",
    "recompute_ledger_hash",
    "finalize_ledger",
    "validate_ledger",
    "ledger_summary",
    "recompute_derivation",
]


LEDGER_VERSION: str = "0.1"


# ---------------------------------------------------------------------------
# PlanningEvidenceLedger
# ---------------------------------------------------------------------------


class PlanningEvidenceLedger(AgentBaseModel):
    """Typed container for planning evidence claims + conflicts.

    Build via :func:`create_empty_ledger`; never construct directly with
    hand-computed ``ledger_hash``.
    """

    ledger_version: str = LEDGER_VERSION
    requirement_hash: str
    source_index_hashes: tuple[str, ...] = Field(default_factory=tuple)
    claims: dict[str, EvidenceClaim] = Field(default_factory=dict)
    derivations: dict[str, EvidenceDerivation] = Field(default_factory=dict)
    conflicts: dict[str, EvidenceConflict] = Field(default_factory=dict)
    unresolved_claim_ids: tuple[str, ...] = Field(default_factory=tuple)
    source_critical_claim_ids: tuple[str, ...] = Field(default_factory=tuple)
    tool_call_ids: tuple[str, ...] = Field(default_factory=tuple)
    human_confirmation_ids: tuple[str, ...] = Field(default_factory=tuple)
    ledger_hash: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)

    # ------------------------------------------------------------------
    # Convenience accessors
    # ------------------------------------------------------------------

    def claim(self, claim_id: str) -> EvidenceClaim | None:
        return self.claims.get(claim_id)

    def all_claims(self) -> list[EvidenceClaim]:
        return list(self.claims.values())

    def all_conflicts(self) -> list[EvidenceConflict]:
        return list(self.conflicts.values())


# ---------------------------------------------------------------------------
# Empty ledger construction
# ---------------------------------------------------------------------------


def create_empty_ledger(
    *,
    requirement_hash: str,
    source_indexes: Iterable[SourceIndex] | None = None,
    metadata: Mapping[str, Any] | None = None,
) -> PlanningEvidenceLedger:
    """Return a fresh, unfinalized ledger bound to one requirement and zero
    or more source indexes.

    ``requirement_hash`` MUST be the canonical hash of the requirement text
    (computed by the caller); it is part of the ledger hash.
    """

    source_hashes = tuple(idx.index_hash for idx in (source_indexes or []))
    ledger = PlanningEvidenceLedger(
        requirement_hash=requirement_hash,
        source_index_hashes=source_hashes,
        metadata=dict(metadata or {}),
    )
    return ledger


# ---------------------------------------------------------------------------
# Claim validation against sources
# ---------------------------------------------------------------------------


def _resolve_source_index(
    source_indexes: Mapping[str, SourceIndex], source_id: str
) -> SourceIndex:
    idx = source_indexes.get(source_id)
    if idx is None:
        raise PlanInvestigationIssue(
            SOURCE_REF_MISSING,
            "source_id is not present in the provided source indexes",
            details={"source_id": source_id},
        )
    return idx


def _validate_claim_against_indexes(
    claim: EvidenceClaim, source_indexes: Mapping[str, SourceIndex]
) -> None:
    """Ensure every source_ref on ``claim`` resolves in ``source_indexes``."""

    for ref in claim.source_refs:
        idx = _resolve_source_index(source_indexes, ref.source_id)
        idx.validate_source_ref(ref)


# ---------------------------------------------------------------------------
# Add / upsert
# ---------------------------------------------------------------------------


def add_claim(
    ledger: PlanningEvidenceLedger,
    claim: EvidenceClaim,
    *,
    source_indexes: Mapping[str, SourceIndex] | None = None,
) -> PlanningEvidenceLedger:
    """Add ``claim`` to ``ledger``.

    Raises if a claim with the same ``claim_id`` already exists.  Two claims
    with the same semantic key but different values are both kept; the
    conflict between them is detected explicitly by :func:`detect_conflicts`.
    """

    if claim.status == EvidenceStatus.EXTERNAL_OFFICIAL:
        raise PlanInvestigationIssue(
            EXTERNAL_EVIDENCE_DISABLED,
            "external_official evidence is disabled in Phase 8A Step 1",
        )

    if source_indexes is not None:
        _validate_claim_against_indexes(claim, source_indexes)

    if claim.claim_id in ledger.claims:
        raise PlanInvestigationIssue(
            "plan_investigation.duplicate_claim",
            "claim_id already present; use upsert_claim to replace",
            details={"claim_id": claim.claim_id},
        )

    ledger.claims[claim.claim_id] = claim
    if claim.derivation is not None:
        # Adding the derivation here would be redundant: derivations are
        # added via add_derivation so the cycle check runs once.
        ledger.derivations[claim.derivation.derivation_id] = claim.derivation

    _refresh_bookkeeping(ledger)
    return ledger


def upsert_claim(
    ledger: PlanningEvidenceLedger,
    claim: EvidenceClaim,
    *,
    source_indexes: Mapping[str, SourceIndex] | None = None,
) -> PlanningEvidenceLedger:
    """Insert or replace ``claim``.

    Replacement of a human-confirmed claim with a different value is
    rejected.  Replacement with a semantically identical payload is a no-op.
    """

    if claim.status == EvidenceStatus.EXTERNAL_OFFICIAL:
        raise PlanInvestigationIssue(
            EXTERNAL_EVIDENCE_DISABLED,
            "external_official evidence is disabled in Phase 8A Step 1",
        )

    if source_indexes is not None:
        _validate_claim_against_indexes(claim, source_indexes)

    existing = ledger.claims.get(claim.claim_id)
    if existing is not None and existing.confirmed_by_human:
        # Immutability: allow only exact semantic match.
        if _claim_semantic_payload(existing) != _claim_semantic_payload(claim):
            raise PlanInvestigationIssue(
                CONFIRMED_CLAIM_MUTATION,
                "confirmed_by_human claims cannot be silently modified",
                details={"claim_id": claim.claim_id},
            )

    ledger.claims[claim.claim_id] = claim
    if claim.derivation is not None:
        ledger.derivations[claim.derivation.derivation_id] = claim.derivation

    _refresh_bookkeeping(ledger)
    return ledger


def _claim_semantic_payload(claim: EvidenceClaim) -> dict[str, Any]:
    return {
        "subject": claim.subject,
        "predicate": claim.predicate,
        "qualifiers": claim.qualifiers,
        "value": claim.value,
        "status": claim.status.value,
        "source_refs": [ref.model_dump(mode="json") for ref in claim.source_refs],
        "criticality": claim.criticality.value,
    }


# ---------------------------------------------------------------------------
# Derivations
# ---------------------------------------------------------------------------


def add_derivation(
    ledger: PlanningEvidenceLedger,
    derivation: EvidenceDerivation,
) -> PlanningEvidenceLedger:
    """Register ``derivation`` and validate it against its input claims.

    Performs:

    * Input claim existence check.
    * Cycle check (a claim cannot depend on itself transitively).
    * Result re-computation; ``derivation.result_hash`` must match.
    """

    for input_id in derivation.input_claim_ids:
        if input_id not in ledger.claims:
            raise PlanInvestigationIssue(
                DERIVATION_INPUT_MISSING,
                "derivation references an unknown input claim",
                details={"input_claim_id": input_id},
            )

    # Cycle check via DFS over derivation inputs.  The candidate derivation
    # has not been added to the ledger yet, so seed the traversal with its
    # own input claim ids and walk each input claim's derivation upstream.
    _assert_no_cycle(
        candidate_input_claim_ids=list(derivation.input_claim_ids),
        ledger=ledger,
        visiting={derivation.derivation_id},
    )

    # Result re-computation.
    input_values = [ledger.claims[cid].value for cid in derivation.input_claim_ids]
    recomputed = recompute_derivation(derivation, input_values)
    recomputed_hash = content_hash(recomputed)
    if recomputed_hash != derivation.result_hash:
        raise PlanInvestigationIssue(
            DERIVATION_RESULT_MISMATCH,
            "derivation result_hash does not match the recomputed value",
            details={
                "derivation_id": derivation.derivation_id,
                "operation": derivation.operation,
                "expected": recomputed_hash,
                "actual": derivation.result_hash,
            },
        )

    ledger.derivations[derivation.derivation_id] = derivation
    _refresh_bookkeeping(ledger)
    return ledger


def add_derived_claim(
    ledger: PlanningEvidenceLedger,
    *,
    claim: EvidenceClaim,
    derivation: EvidenceDerivation,
) -> PlanningEvidenceLedger:
    """Convenience: register a derived claim and its derivation together.

    The claim MUST have ``status = deterministically_derived`` and the same
    ``derivation`` attached inline (so the claim's ``claim_id`` already
    reflects ``derivation_present=True``).  The derivation is registered via
    :func:`add_derivation` so the cycle check runs exactly once, then the
    claim is added via :func:`add_claim`.
    """

    if claim.status != EvidenceStatus.DETERMINISTICALLY_DERIVED:
        raise PlanInvestigationIssue(
            "plan_investigation.derived_claim_without_inputs",
            "add_derived_claim requires status=deterministically_derived",
            details={"claim_id": claim.claim_id},
        )
    if claim.derivation is None or claim.derivation.derivation_id != derivation.derivation_id:
        raise PlanInvestigationIssue(
            "plan_investigation.derivation_input_missing",
            "add_derived_claim requires the derivation attached to the claim inline",
            details={"claim_id": claim.claim_id},
        )

    add_derivation(ledger, derivation)
    add_claim(ledger, claim)
    return ledger


def _assert_no_cycle(
    *,
    candidate_input_claim_ids: list[str],
    ledger: PlanningEvidenceLedger,
    visiting: set[str],
) -> None:
    """DFS over derivation inputs to detect cycles.

    ``visiting`` carries the set of derivation ids already on the current
    DFS path (seeded with the candidate derivation's id).  Walking upstream
    from each candidate input claim, if we encounter a derivation whose id
    is in ``visiting``, the candidate would close a cycle.
    """

    for input_id in candidate_input_claim_ids:
        input_claim = ledger.claims.get(input_id)
        if input_claim is None or input_claim.derivation is None:
            continue
        upstream_id = input_claim.derivation.derivation_id
        if upstream_id in visiting:
            raise PlanInvestigationIssue(
                DERIVATION_CYCLE,
                "derivation graph contains a cycle",
                details={"cycle_seed": upstream_id},
            )
        visiting.add(upstream_id)
        _assert_no_cycle(
            candidate_input_claim_ids=list(input_claim.derivation.input_claim_ids),
            ledger=ledger,
            visiting=visiting,
        )
        visiting.discard(upstream_id)


# ---------------------------------------------------------------------------
# Derivation operation executors
# ---------------------------------------------------------------------------


def recompute_derivation(
    derivation: EvidenceDerivation, input_values: list[Any]
) -> Any:
    """Recompute the result of ``derivation`` from its input values.

    Pure Python: no ``eval``, no ``exec``, no arbitrary code execution.
    Each operation has a dedicated branch; unknown operations are rejected
    at parse time (see :class:`EvidenceDerivation`).
    """

    op = derivation.operation
    if op == "integer_product":
        return _integer_product(input_values, derivation.parameters)
    if op == "integer_sum":
        return _integer_sum(input_values, derivation.parameters)
    if op == "matrix_shape":
        return _matrix_shape(input_values, derivation.parameters)
    if op == "count_by_label":
        return _count_by_label(input_values, derivation.parameters)
    if op == "equality_alias":
        return _equality_alias(input_values, derivation.parameters)
    if op == "interval_length":
        return _interval_length(input_values, derivation.parameters)
    raise PlanInvestigationIssue(
        "plan_investigation.derivation_operation_not_allowed",
        f"derivation operation '{op}' is not implemented",
        details={"allowed": sorted(ALLOWED_DERIVATION_OPERATIONS)},
    )


def _coerce_int(value: Any, *, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise PlanInvestigationIssue(
            "plan_investigation.derivation_result_mismatch",
            f"{label} must be an integer (got {type(value).__name__})",
        )
    return value


def _integer_product(input_values: list[Any], parameters: dict[str, Any]) -> int:
    operands = list(parameters.get("operands", []))
    values = list(input_values) + operands
    if not values:
        raise PlanInvestigationIssue(
            "plan_investigation.derivation_result_mismatch",
            "integer_product requires at least one operand",
        )
    result = 1
    for idx, val in enumerate(values):
        result *= _coerce_int(val, label=f"integer_product operand[{idx}]")
    return result


def _integer_sum(input_values: list[Any], parameters: dict[str, Any]) -> int:
    operands = list(parameters.get("operands", []))
    values = list(input_values) + operands
    if not values:
        raise PlanInvestigationIssue(
            "plan_investigation.derivation_result_mismatch",
            "integer_sum requires at least one operand",
        )
    return sum(_coerce_int(val, label=f"integer_sum operand[{idx}]") for idx, val in enumerate(values))


def _matrix_shape(input_values: list[Any], parameters: dict[str, Any]) -> list[int]:
    if not input_values:
        raise PlanInvestigationIssue(
            "plan_investigation.derivation_result_mismatch",
            "matrix_shape requires one input claim whose value is a list of lists",
        )
    value = input_values[0]
    if not isinstance(value, list) or not all(isinstance(row, list) for row in value):
        raise PlanInvestigationIssue(
            "plan_investigation.derivation_result_mismatch",
            "matrix_shape input must be a list of lists",
        )
    rows = len(value)
    cols = max((len(row) for row in value), default=0)
    return [rows, cols]


def _count_by_label(input_values: list[Any], parameters: dict[str, Any]) -> dict[str, int]:
    if not input_values:
        raise PlanInvestigationIssue(
            "plan_investigation.derivation_result_mismatch",
            "count_by_label requires one input claim whose value is a list of labels",
        )
    value = input_values[0]
    if not isinstance(value, list):
        raise PlanInvestigationIssue(
            "plan_investigation.derivation_result_mismatch",
            "count_by_label input must be a list",
        )
    # Flatten one level of nesting so a list-of-lists (e.g. a 2-D layout
    # grid) is counted as a flat label stream.  Deeper nesting is rejected
    # to keep the operation's semantics unambiguous.
    flat: list[Any] = []
    for item in value:
        if isinstance(item, list):
            for sub in item:
                if isinstance(sub, list):
                    raise PlanInvestigationIssue(
                        "plan_investigation.derivation_result_mismatch",
                        "count_by_label supports at most one level of nesting",
                    )
                flat.append(sub)
        else:
            flat.append(item)
    only: set[str] | None = None
    if "only" in parameters:
        only = set(str(label) for label in parameters["only"])
    counts: dict[str, int] = {}
    for item in flat:
        key = str(item)
        if only is not None and key not in only:
            continue
        counts[key] = counts.get(key, 0) + 1
    return counts


def _equality_alias(input_values: list[Any], parameters: dict[str, Any]) -> Any:
    if len(input_values) < 2:
        raise PlanInvestigationIssue(
            "plan_investigation.derivation_result_mismatch",
            "equality_alias requires at least two input claims",
        )
    first = input_values[0]
    for idx, val in enumerate(input_values[1:], start=1):
        if val != first:
            raise PlanInvestigationIssue(
                "plan_investigation.derivation_result_mismatch",
                f"equality_alias input claim [{idx}] disagrees with input [0]",
                details={"first": first, "differing": val},
            )
    return first


def _interval_length(input_values: list[Any], parameters: dict[str, Any]) -> int | float:
    if not input_values:
        raise PlanInvestigationIssue(
            "plan_investigation.derivation_result_mismatch",
            "interval_length requires one input claim",
        )
    value = input_values[0]
    lo: Any
    hi: Any
    if isinstance(value, (list, tuple)) and len(value) == 2:
        lo, hi = value
    elif isinstance(value, dict) and {"lo", "hi"} <= set(value):
        lo, hi = value["lo"], value["hi"]
    else:
        raise PlanInvestigationIssue(
            "plan_investigation.derivation_result_mismatch",
            "interval_length input must be a 2-tuple or {'lo','hi'} dict",
        )
    try:
        return _coerce_int(hi, label="interval hi") - _coerce_int(lo, label="interval lo")
    except PlanInvestigationIssue:
        # Float fallback for non-integer intervals.
        if isinstance(lo, (int, float)) and isinstance(hi, (int, float)) and not isinstance(lo, bool) and not isinstance(hi, bool):
            result = hi - lo
            if isinstance(result, float) and math.isfinite(result):
                return result
        raise


# ---------------------------------------------------------------------------
# Conflict detection
# ---------------------------------------------------------------------------


def detect_conflicts(ledger: PlanningEvidenceLedger) -> PlanningEvidenceLedger:
    """Group claims by semantic key; emit an :class:`EvidenceConflict` for
    each group whose candidate values disagree.

    Existing conflicts for unaffected semantic keys are preserved; conflicts
    whose key no longer has any disagreement are dropped.  Step 1 performs
    NO auto-resolution by source precedence — all candidates are kept.
    """

    grouped: dict[str, list[EvidenceClaim]] = {}
    for claim in ledger.claims.values():
        if claim.status in (EvidenceStatus.UNRESOLVED, EvidenceStatus.CONFLICT):
            continue
        key = semantic_key_for_claim(claim)
        grouped.setdefault(key, []).append(claim)

    new_conflicts: dict[str, EvidenceConflict] = {}
    for key, candidates in grouped.items():
        # A "distinct value" comparison via canonical hash.  Two claims
        # with structurally equal values produce the same canonical JSON.
        distinct_hashes: dict[str, Any] = {}
        for claim in candidates:
            value_hash = content_hash(claim.value)
            distinct_hashes.setdefault(value_hash, claim.value)
        if len(distinct_hashes) <= 1:
            continue

        ordered = sorted(candidates, key=lambda c: c.claim_id)
        claim_ids = tuple(c.claim_id for c in ordered)
        conflicting_values = tuple(
            distinct_hashes[h] for h in sorted(distinct_hashes.keys())
        )
        ref_set: dict[str, EvidenceSourceRef] = {}
        for claim in ordered:
            for ref in claim.source_refs:
                ref_key = ref.model_dump_json()
                ref_set.setdefault(ref_key, ref)
        source_refs = tuple(sorted(ref_set.values(), key=lambda r: r.model_dump_json()))

        conflict = EvidenceConflict(
            conflict_id="",  # filled by validator
            semantic_key=key,
            claim_ids=claim_ids,
            conflicting_values=conflicting_values,
            source_refs=source_refs,
            severity="warning",
            resolution_status=ConflictResolutionStatus.UNRESOLVED,
            resolved_claim_id=None,
            human_confirmation_required=True,
        )
        new_conflicts[conflict.conflict_id] = conflict

    ledger.conflicts = new_conflicts
    _refresh_bookkeeping(ledger)
    return ledger


# ---------------------------------------------------------------------------
# Query helpers
# ---------------------------------------------------------------------------


def find_claims(
    ledger: PlanningEvidenceLedger,
    *,
    subject: str | None = None,
    predicate: str | None = None,
    status: EvidenceStatus | None = None,
    criticality: EvidenceCriticality | None = None,
) -> list[EvidenceClaim]:
    """Filter claims by (subject, predicate, status, criticality).

    All filters are optional.  Filtering on subject/predicate is exact-string
    (after Pydantic's ``str_strip_whitespace``).
    """

    out: list[EvidenceClaim] = []
    for claim in ledger.claims.values():
        if subject is not None and claim.subject != subject:
            continue
        if predicate is not None and claim.predicate != predicate:
            continue
        if status is not None and claim.status != status:
            continue
        if criticality is not None and claim.criticality != criticality:
            continue
        out.append(claim)
    return sorted(out, key=lambda c: c.claim_id)


def get_claim_by_id(ledger: PlanningEvidenceLedger, claim_id: str) -> EvidenceClaim | None:
    return ledger.claims.get(claim_id)


def claims_for_json_path(ledger: PlanningEvidenceLedger, json_path: str) -> list[EvidenceClaim]:
    out = [c for c in ledger.claims.values() if json_path in c.required_by_json_paths]
    return sorted(out, key=lambda c: c.claim_id)


def claims_for_patch_type(ledger: PlanningEvidenceLedger, patch_type: str) -> list[EvidenceClaim]:
    out = [c for c in ledger.claims.values() if patch_type in c.required_by_patch_types]
    return sorted(out, key=lambda c: c.claim_id)


def unresolved_source_critical_claims(ledger: PlanningEvidenceLedger) -> list[EvidenceClaim]:
    """Return source-critical claims that are unresolved (no accepted value)."""

    out: list[EvidenceClaim] = []
    for claim in ledger.claims.values():
        if claim.criticality != EvidenceCriticality.SOURCE_CRITICAL:
            continue
        if claim.status == EvidenceStatus.UNRESOLVED:
            out.append(claim)
            continue
        # If this claim is part of an unresolved conflict, it cannot satisfy
        # source-critical requirements.
        for conflict in ledger.conflicts.values():
            if claim.claim_id in conflict.claim_ids and conflict.resolution_status == ConflictResolutionStatus.UNRESOLVED:
                out.append(claim)
                break
    return sorted(out, key=lambda c: c.claim_id)


def find_stale_derived_claims(ledger: PlanningEvidenceLedger) -> list[str]:
    """Return claim_ids of derived claims whose inputs no longer support them.

    A derived claim is stale when:

    * Its derivation's input_claim_ids are no longer all present in the
      ledger, OR
    * Recomputing its derivation yields a different ``result_hash``.
    """

    stale: list[str] = []
    for claim in ledger.claims.values():
        if claim.status != EvidenceStatus.DETERMINISTICALLY_DERIVED:
            continue
        derivation = claim.derivation
        if derivation is None:
            derivation = ledger.derivations.get(_derivation_id_for_claim(claim))
        if derivation is None:
            stale.append(claim.claim_id)
            continue
        if any(cid not in ledger.claims for cid in derivation.input_claim_ids):
            stale.append(claim.claim_id)
            continue
        try:
            input_values = [ledger.claims[cid].value for cid in derivation.input_claim_ids]
            recomputed = recompute_derivation(derivation, input_values)
        except PlanInvestigationIssue:
            stale.append(claim.claim_id)
            continue
        if content_hash(recomputed) != derivation.result_hash:
            stale.append(claim.claim_id)
    return sorted(stale)


def _derivation_id_for_claim(claim: EvidenceClaim) -> str:
    """Best-effort lookup key for a claim's derivation when it is unset."""
    # Claims whose derivation is set carry it inline; this helper exists only
    # for legacy ledgers that might separate the two.  We return "" so the
    # caller's .get("") returns None.
    return ""


# ---------------------------------------------------------------------------
# Hash + finalize + validate
# ---------------------------------------------------------------------------


def _hash_payload(ledger: PlanningEvidenceLedger) -> dict[str, Any]:
    """Return the canonical, order-independent hash payload for ``ledger``."""

    claims_sorted = sorted(ledger.claims.values(), key=lambda c: c.claim_id)
    derivations_sorted = sorted(ledger.derivations.values(), key=lambda d: d.derivation_id)
    conflicts_sorted = sorted(ledger.conflicts.values(), key=lambda c: c.conflict_id)

    return {
        "ledger_version": ledger.ledger_version,
        "requirement_hash": ledger.requirement_hash,
        "source_index_hashes": sorted(ledger.source_index_hashes),
        "claims": [c.model_dump(mode="json") for c in claims_sorted],
        "derivations": [d.model_dump(mode="json") for d in derivations_sorted],
        "conflicts": [c.model_dump(mode="json") for c in conflicts_sorted],
        "unresolved_claim_ids": sorted(ledger.unresolved_claim_ids),
        "source_critical_claim_ids": sorted(ledger.source_critical_claim_ids),
        "tool_call_ids": sorted(ledger.tool_call_ids),
        "human_confirmation_ids": sorted(ledger.human_confirmation_ids),
    }


def recompute_ledger_hash(ledger: PlanningEvidenceLedger) -> str:
    return content_hash(_hash_payload(ledger))


def finalize_ledger(ledger: PlanningEvidenceLedger) -> PlanningEvidenceLedger:
    """Refresh bookkeeping fields and stamp ``ledger_hash``.

    After finalize, ``ledger.ledger_hash`` equals
    :func:`recompute_ledger_hash`.  Reloading the finalized ledger from JSON
    and recomputing yields the same hash.
    """

    _refresh_bookkeeping(ledger)
    ledger.ledger_hash = recompute_ledger_hash(ledger)
    return ledger


def validate_ledger(ledger: PlanningEvidenceLedger) -> list[PlanInvestigationIssue]:
    """Return a list of issues (empty if the ledger is internally consistent)."""

    issues: list[PlanInvestigationIssue] = []
    # Hash check.
    if ledger.ledger_hash and ledger.ledger_hash != recompute_ledger_hash(ledger):
        issues.append(
            PlanInvestigationIssue(
                LEDGER_HASH_MISMATCH,
                "ledger_hash does not match the recomputed value",
                details={
                    "expected": recompute_ledger_hash(ledger),
                    "actual": ledger.ledger_hash,
                },
            )
        )
    # Stale derived claims.
    for stale_id in find_stale_derived_claims(ledger):
        issues.append(
            PlanInvestigationIssue(
                STALE_DERIVED_CLAIM,
                "derived claim is stale relative to its inputs",
                details={"claim_id": stale_id},
            )
        )
    return issues


def ledger_summary(ledger: PlanningEvidenceLedger) -> EvidenceLedgerSummary:
    """Compute an :class:`EvidenceLedgerSummary` for the ledger."""

    counts = {"explicit": 0, "derived": 0, "assumption": 0, "unresolved": 0}
    src_critical_total = 0
    for claim in ledger.claims.values():
        if claim.status == EvidenceStatus.EXPLICIT:
            counts["explicit"] += 1
        elif claim.status == EvidenceStatus.DETERMINISTICALLY_DERIVED:
            counts["derived"] += 1
        elif claim.status == EvidenceStatus.ASSUMPTION:
            counts["assumption"] += 1
        elif claim.status == EvidenceStatus.UNRESOLVED:
            counts["unresolved"] += 1
        if claim.criticality == EvidenceCriticality.SOURCE_CRITICAL:
            src_critical_total += 1

    unresolved_src_critical = set(c.claim_id for c in unresolved_source_critical_claims(ledger))
    src_critical_resolved = src_critical_total - len(unresolved_src_critical)

    return EvidenceLedgerSummary(
        total_claim_count=len(ledger.claims),
        explicit_count=counts["explicit"],
        derived_count=counts["derived"],
        assumption_count=counts["assumption"],
        unresolved_count=counts["unresolved"],
        conflict_count=len(ledger.conflicts),
        source_critical_count=src_critical_total,
        source_critical_resolved_count=src_critical_resolved,
        source_critical_unresolved_count=len(unresolved_src_critical),
    )


# ---------------------------------------------------------------------------
# Bookkeeping
# ---------------------------------------------------------------------------


def _refresh_bookkeeping(ledger: PlanningEvidenceLedger) -> None:
    """Refresh derived bookkeeping fields (unresolved, source-critical ids).

    Does NOT recompute ``ledger_hash`` — that's :func:`finalize_ledger`'s
    job.  Bookkeeping fields themselves ARE part of the hash, so this must
    run before any hash computation.
    """

    unresolved_ids: list[str] = []
    src_critical_ids: list[str] = []
    confirmation_ids: set[str] = set()

    for claim in ledger.claims.values():
        if claim.status == EvidenceStatus.UNRESOLVED:
            unresolved_ids.append(claim.claim_id)
        if claim.criticality == EvidenceCriticality.SOURCE_CRITICAL:
            src_critical_ids.append(claim.claim_id)
        if claim.confirmed_by_human and claim.human_confirmation_id:
            confirmation_ids.add(claim.human_confirmation_id)

    ledger.unresolved_claim_ids = tuple(sorted(unresolved_ids))
    ledger.source_critical_claim_ids = tuple(sorted(src_critical_ids))
    ledger.human_confirmation_ids = tuple(sorted(confirmation_ids))
