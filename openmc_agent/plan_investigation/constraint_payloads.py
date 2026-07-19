"""Phase 8A Step 6 — typed planning-constraint payloads.

A :class:`EvidenceConstraintPayload` represents a *derived planning
constraint* extracted from the :class:`GeometryComponentInventory` and
its requirement sets (Material / Universe / Placement / Axial).  It is
deliberately distinct from an :class:`EvidenceClaim`:

* An ``EvidenceClaim`` is *source-backed*: its ``source_spans`` MUST
  cite a ``SourceSpan`` in a :class:`SourceIndex`.
* An ``EvidenceConstraintPayload`` is *derived*: its provenance is the
  inventory/requirement compiler, not a direct source quotation.  Its
  ``derivation_status`` is one of ``deterministically_derived``,
  ``explicit_source``, ``human_confirmed``, ``unresolved`` or
  ``conflict``.  ``source_claim_ids`` references real EvidenceClaims
  in the shared Ledger (when the underlying source value was
  retrieved) but the constraint itself is never labelled ``status=
  explicit`` with empty spans — that misrepresentation was the P0-4
  bug fixed in Step 6A.

The patch prompt renders evidence claims and derived constraints in
separate sections so reviewers / auditors can distinguish "what the
source said" from "what the inventory inferred".
"""

from __future__ import annotations

from typing import Any

from pydantic import Field, model_validator

from openmc_agent.schemas import AgentBaseModel

from .hashing import content_hash, short_id

__all__ = [
    "EvidenceConstraintPayload",
    "ConstraintDerivationStatus",
    "CONSTRAINT_SCHEMA_VERSION",
]


CONSTRAINT_SCHEMA_VERSION = "1.0"


class ConstraintDerivationStatus:
    """Allowed derivation-status values for a planning constraint."""

    DETERMINISTICALLY_DERIVED = "deterministically_derived"
    EXPLICIT_SOURCE = "explicit_source"
    HUMAN_CONFIRMED = "human_confirmed"
    UNRESOLVED = "unresolved"
    CONFLICT = "conflict"


_ALLOWED_DERIVATION_STATUSES = {
    ConstraintDerivationStatus.DETERMINISTICALLY_DERIVED,
    ConstraintDerivationStatus.EXPLICIT_SOURCE,
    ConstraintDerivationStatus.HUMAN_CONFIRMED,
    ConstraintDerivationStatus.UNRESOLVED,
    ConstraintDerivationStatus.CONFLICT,
}


class EvidenceConstraintPayload(AgentBaseModel):
    """One derived planning constraint fed into a patch prompt.

    Fields follow the Step 6 contract (Section 6).  ``constraint_hash``
    and ``constraint_id`` are computed deterministically from the
    payload body so resume / artifact deduplication is stable.
    """

    model_config = {"arbitrary_types_allowed": True}

    constraint_id: str = ""
    constraint_kind: str = ""
    subject: str = ""
    predicate: str = ""
    value: Any = None
    derivation_status: str = ConstraintDerivationStatus.DETERMINISTICALLY_DERIVED
    source_claim_ids: tuple[str, ...] = Field(default_factory=tuple)
    source_spans: tuple[dict[str, Any], ...] = Field(default_factory=tuple)
    inventory_requirement_ids: tuple[str, ...] = Field(default_factory=tuple)
    inventory_hash: str = ""
    ledger_hash: str = ""
    criticality: str = "supporting"
    unresolved_fields: tuple[str, ...] = Field(default_factory=tuple)
    constraint_hash: str = ""

    @model_validator(mode="after")
    def _compute_hash_and_id(self) -> "EvidenceConstraintPayload":
        # Derivation-status validation: fail fast on unknown values so
        # callers cannot invent new statuses that bypass the auditor.
        if self.derivation_status not in _ALLOWED_DERIVATION_STATUSES:
            raise ValueError(
                f"unknown derivation_status={self.derivation_status!r}; "
                f"allowed: {sorted(s for s in _ALLOWED_DERIVATION_STATUSES)}"
            )
        # explicit_source / human_confirmed constraints MUST cite at
        # least one real claim id (otherwise they are misrepresenting
        # their provenance — the P0-4 bug).
        if (
            self.derivation_status
            in (ConstraintDerivationStatus.EXPLICIT_SOURCE, ConstraintDerivationStatus.HUMAN_CONFIRMED)
            and not self.source_claim_ids
        ):
            raise ValueError(
                f"derivation_status={self.derivation_status} requires "
                f"at least one source_claim_id (got none)"
            )
        body = {
            "kind": self.constraint_kind,
            "subject": self.subject,
            "predicate": self.predicate,
            "value": self.value,
            "derivation_status": self.derivation_status,
            "source_claim_ids": list(self.source_claim_ids),
            "inventory_requirement_ids": list(self.inventory_requirement_ids),
            "inventory_hash": self.inventory_hash,
            "ledger_hash": self.ledger_hash,
            "criticality": self.criticality,
            "unresolved_fields": list(self.unresolved_fields),
        }
        h = content_hash(body)
        object.__setattr__(self, "constraint_hash", h)
        if not self.constraint_id:
            object.__setattr__(self, "constraint_id", short_id("constraint", h))
        return self

    def to_prompt_dict(self) -> dict[str, Any]:
        """Compact dict representation used by the prompt renderer."""

        return {
            "constraint_id": self.constraint_id,
            "constraint_kind": self.constraint_kind,
            "subject": self.subject,
            "predicate": self.predicate,
            "value": self.value,
            "derivation_status": self.derivation_status,
            "source_claim_ids": list(self.source_claim_ids),
            "source_spans": list(self.source_spans),
            "inventory_requirement_ids": list(self.inventory_requirement_ids),
            "criticality": self.criticality,
            "unresolved_fields": list(self.unresolved_fields),
        }
