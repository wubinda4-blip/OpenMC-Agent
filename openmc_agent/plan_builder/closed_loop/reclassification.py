"""Post-replay root-cause reclassification.

After an owner commit + downstream resume + gate replay, the controller must
re-classify the original request against the new issue set.  Only when the
original issue fingerprint has actually disappeared from the after-set may
the request be marked ``resolved``.  New upstream issues trigger a new typed
request; lateral issues are recorded as ``remaining``.
"""

from __future__ import annotations

from typing import Any

from pydantic import Field

from openmc_agent.plan_builder.state import PlanBuildState
from openmc_agent.schemas import AgentBaseModel

from .retry_models import ExecutablePlanRetryRequest


class ReclassificationResult(AgentBaseModel):
    classification: str
    resolved_issue_codes: list[str] = Field(default_factory=list)
    remaining_issue_codes: list[str] = Field(default_factory=list)
    new_issue_codes: list[str] = Field(default_factory=list)
    new_request_reason_codes: list[str] = Field(default_factory=list)
    detail: str = ""


def reclassify_retry_outcome(
    *,
    request: ExecutablePlanRetryRequest,
    before_issues: list[dict[str, Any]],
    after_issues: list[dict[str, Any]],
    owner_hashes_after: dict[str, str] | None = None,
    task_plan_hash_after: str | None = None,
) -> ReclassificationResult:
    """Compare before/after issue sets and produce a typed classification.

    Classifications:
      - ``resolved``: the reason code (and all errors) are gone.
      - ``partially_resolved``: reason code gone but other errors remain.
      - ``next_request_required``: reason code gone but new errors appeared.
      - ``no_progress``: reason code still present.
      - ``awaiting_human``: new errors are human-required.
      - ``blocked``: new errors have no registered owner.
    """
    before_codes = {str(item.get("code")) for item in before_issues if item.get("severity") == "error"}
    after_codes = {str(item.get("code")) for item in after_issues if item.get("severity") == "error"}
    resolved = sorted(before_codes - after_codes)
    remaining = sorted(after_codes & before_codes)
    new = sorted(after_codes - before_codes)
    reason_still_present = request.reason_code in after_codes
    if reason_still_present:
        return ReclassificationResult(classification="no_progress", resolved_issue_codes=resolved, remaining_issue_codes=remaining, new_issue_codes=new, detail=f"reason code {request.reason_code} still present in after-set")
    if not after_codes:
        return ReclassificationResult(classification="resolved", resolved_issue_codes=resolved, remaining_issue_codes=remaining, new_issue_codes=new, detail="all errors cleared")
    new_human = any(item.get("requires_human") for item in after_issues if item.get("code") in new)
    if new_human:
        return ReclassificationResult(classification="awaiting_human", resolved_issue_codes=resolved, remaining_issue_codes=remaining, new_issue_codes=new, new_request_reason_codes=new, detail="new human-required issue appeared")
    if new:
        return ReclassificationResult(classification="next_request_required", resolved_issue_codes=resolved, remaining_issue_codes=remaining, new_issue_codes=new, new_request_reason_codes=new, detail=f"new errors appeared: {new}")
    return ReclassificationResult(classification="partially_resolved", resolved_issue_codes=resolved, remaining_issue_codes=remaining, new_issue_codes=new, detail="reason code resolved but pre-existing errors remain")


__all__ = ["ReclassificationResult", "reclassify_retry_outcome"]
