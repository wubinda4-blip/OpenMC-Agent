"""Stable error codes for the plan investigation package.

Each code is a dotted string in the ``plan_investigation.*`` namespace.  Codes
never include arbitrary free text; structured detail lives alongside the code
in a dict payload, mirroring the existing
:class:`openmc_agent.schemas.ValidationIssue` style.
"""

from __future__ import annotations

from typing import Any

__all__ = [
    "PlanInvestigationError",
    "PlanInvestigationIssue",
    "SOURCE_SPAN_INVALID",
    "SOURCE_HASH_MISMATCH",
    "SOURCE_REF_MISSING",
    "CLAIM_VALUE_NOT_JSON",
    "EXPLICIT_CLAIM_WITHOUT_SOURCE",
    "DERIVED_CLAIM_WITHOUT_INPUTS",
    "DERIVATION_INPUT_MISSING",
    "DERIVATION_CYCLE",
    "DERIVATION_RESULT_MISMATCH",
    "DERIVATION_OPERATION_NOT_ALLOWED",
    "EXTERNAL_EVIDENCE_DISABLED",
    "HUMAN_CONFIRMATION_MISSING",
    "CONFIRMED_CLAIM_MUTATION",
    "EVIDENCE_CONFLICT",
    "LEDGER_HASH_MISMATCH",
    "STALE_DERIVED_CLAIM",
    "ISSUE_CODES",
]


# ---------------------------------------------------------------------------
# Stable error code constants
# ---------------------------------------------------------------------------

SOURCE_SPAN_INVALID = "plan_investigation.source_span_invalid"
SOURCE_HASH_MISMATCH = "plan_investigation.source_hash_mismatch"
SOURCE_REF_MISSING = "plan_investigation.source_ref_missing"
CLAIM_VALUE_NOT_JSON = "plan_investigation.claim_value_not_json"
EXPLICIT_CLAIM_WITHOUT_SOURCE = "plan_investigation.explicit_claim_without_source"
DERIVED_CLAIM_WITHOUT_INPUTS = "plan_investigation.derived_claim_without_inputs"
DERIVATION_INPUT_MISSING = "plan_investigation.derivation_input_missing"
DERIVATION_CYCLE = "plan_investigation.derivation_cycle"
DERIVATION_RESULT_MISMATCH = "plan_investigation.derivation_result_mismatch"
DERIVATION_OPERATION_NOT_ALLOWED = "plan_investigation.derivation_operation_not_allowed"
EXTERNAL_EVIDENCE_DISABLED = "plan_investigation.external_evidence_disabled"
HUMAN_CONFIRMATION_MISSING = "plan_investigation.human_confirmation_missing"
CONFIRMED_CLAIM_MUTATION = "plan_investigation.confirmed_claim_mutation"
EVIDENCE_CONFLICT = "plan_investigation.evidence_conflict"
LEDGER_HASH_MISMATCH = "plan_investigation.ledger_hash_mismatch"
STALE_DERIVED_CLAIM = "plan_investigation.stale_derived_claim"


ISSUE_CODES: tuple[str, ...] = (
    SOURCE_SPAN_INVALID,
    SOURCE_HASH_MISMATCH,
    SOURCE_REF_MISSING,
    CLAIM_VALUE_NOT_JSON,
    EXPLICIT_CLAIM_WITHOUT_SOURCE,
    DERIVED_CLAIM_WITHOUT_INPUTS,
    DERIVATION_INPUT_MISSING,
    DERIVATION_CYCLE,
    DERIVATION_RESULT_MISMATCH,
    DERIVATION_OPERATION_NOT_ALLOWED,
    EXTERNAL_EVIDENCE_DISABLED,
    HUMAN_CONFIRMATION_MISSING,
    CONFIRMED_CLAIM_MUTATION,
    EVIDENCE_CONFLICT,
    LEDGER_HASH_MISMATCH,
    STALE_DERIVED_CLAIM,
)


# ---------------------------------------------------------------------------
# Issue / Error containers
# ---------------------------------------------------------------------------


class PlanInvestigationIssue(ValueError):
    """Structured issue raised (or reported) by the plan investigation layer.

    ``code`` is one of the :data:`ISSUE_CODES` constants.  ``details`` is a
    JSON-compatible dict carrying structured context; it never contains raw
    API keys, file system paths of the host, or full prompts.
    """

    __slots__ = ("code", "details", "message")

    def __init__(self, code: str, message: str, *, details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.details: dict[str, Any] = dict(details or {})

    def to_dict(self) -> dict[str, Any]:
        return {"code": self.code, "message": self.message, "details": self.details}

    def __str__(self) -> str:  # pragma: no cover - trivial
        return f"[{self.code}] {self.message}"


# Backwards-friendly alias.  Existing plan closed-loop code names errors with
# the ``...Error`` suffix; keeping both names available keeps callers natural.
PlanInvestigationError = PlanInvestigationIssue
