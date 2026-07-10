from __future__ import annotations

import json
from openmc_agent.semantic_audit import SemanticAuditInput, SEMANTIC_AUDIT_FINDING_CODES


def build_semantic_audit_prompt(audit_input: SemanticAuditInput) -> str:
    """Build the read-only semantic audit prompt."""
    allowed = sorted(SEMANTIC_AUDIT_FINDING_CODES)
    compact = audit_input.model_dump(mode="json")
    return (
        "You are a read-only semantic plan auditor.\n"
        "You do not modify the plan. You do not generate OpenMC code. "
        "You do not execute tools or run OpenMC. You do not invent benchmark facts. "
        "You do not infer missing material densities as confirmed facts. "
        "You only identify semantic inconsistencies supported by provided evidence.\n\n"
        "Audit order:\n"
        "1. requirement vs patches; 2. patches vs assembled plan; "
        "3. assembled plan vs axial semantics; 4. plan vs material policy; "
        "5. plan vs capability / renderer claim; 6. reference policy vs usage; "
        "7. unresolved fact gaps.\n\n"
        "Rules: findings must cite evidence; no evidence means no finding; "
        "omit findings with confidence below 0.55; set requires_human_confirmation=true "
        "only for missing/conflicting input facts; do not report optimization suggestions; "
        "do not report nominal alloy composition unless it is incorrectly marked confirmed; "
        "do not fault 2D cases for lacking axial layers; do not require spacer overlays when no spacer grid exists; "
        "do not report unexpected reference usage when policy explicitly allows it.\n\n"
        "Allowed finding_code values only:\n"
        + json.dumps(allowed, ensure_ascii=False)
        + "\n\nReturn JSON with keys: audit_id, ok, findings, warnings.\n"
        "Audit input JSON:\n"
        + json.dumps(compact, ensure_ascii=False, indent=2)
    )
