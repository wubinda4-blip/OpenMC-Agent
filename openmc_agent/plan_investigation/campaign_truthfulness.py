"""Phase 8A Step 4 truthfulness violation codes.

These codes are surfaced by the campaign truthfulness auditor when it
detects that the investigation layer misbehaved.  Each code maps to a
concrete check; the auditor never "guesses" intent — it reports exactly
the field that triggered the violation.

Mode-aware: when ``plan_investigation_mode == "off"``, the
investigator-related checks are skipped entirely (the campaign did not
ask for an investigation).
"""

from __future__ import annotations

from typing import Any, Mapping

__all__ = [
    "INVESTIGATION_TRUTH_VIOLATIONS",
    "investigation_truth_violations_for_run",
    "TV_ENABLED_WITHOUT_REAL_CLIENT_CALL",
    "TV_FAKE_CLIENT_USED",
    "TV_FALLBACK_USED",
    "TV_COMPLETED_WITHOUT_TOOL_CALL",
    "TV_CLAIM_WITHOUT_VALID_SOURCE_REF",
    "TV_LEDGER_HASH_MISMATCH",
    "TV_SESSION_ARTIFACT_MISSING",
    "TV_REASONING_CONTENT_PERSISTED",
    "TV_CONTROLLED_FAILURE_BYPASSED",
    "TV_FACTS_PATCH_BEFORE_INVESTIGATION",
    "TV_FACTS_PATCH_WITHOUT_REQUIRED_EVIDENCE",
    "TV_FACTS_PROMPT_EVIDENCE_NOT_IN_LEDGER",
    "TV_SESSION_REUSED_WITH_FINGERPRINT_MISMATCH",
    "TV_ARTIFACT_CONTAINS_HOST_PATH",
    "TV_ARTIFACT_CONTAINS_SECRET",
    # Phase 8A Step 6 additions.
    "TV_CONTROLLED_INVENTORY_FAILURE_BYPASSED",
    "TV_CONTROLLED_INVENTORY_LEGACY_FALLBACK_USED",
    "TV_INVENTORY_CONSTRAINT_WITHOUT_SOURCE_PROVENANCE",
    "TV_INVENTORY_CONSTRAINT_MISLABELED_EXPLICIT",
    "TV_INVENTORY_PREFLIGHT_EXCEPTION_HIDDEN",
    "TV_MATERIALS_INVESTIGATION_NOT_EXECUTED",
    "TV_UNIVERSES_INVESTIGATION_NOT_EXECUTED",
    "TV_MATERIALS_GENERATED_BEFORE_INVESTIGATION",
    "TV_UNIVERSES_GENERATED_BEFORE_INVESTIGATION",
    "TV_RESEARCH_ACTION_REPORTED_WITHOUT_REQUEST",
    "TV_RESEARCH_REQUEST_WITHOUT_FINDINGS",
    "TV_RESEARCH_RESULT_WITHOUT_TOOL_CALLS",
    "TV_RESEARCH_EVIDENCE_DELTA_INVALID",
    "TV_RESEARCH_LEDGER_HASH_UNCHANGED_REPORTED_AS_SUCCESS",
    "TV_RESEARCH_NO_PROGRESS_NOT_STOPPED",
    "TV_RESEARCH_BUDGET_BYPASSED",
    "TV_RESEARCH_OWNER_SELECTED_BY_LLM",
    "TV_RESEARCH_GATE_REOPENED_WITHOUT_INPUT_CHANGE",
    "TV_RESEARCH_INVALIDATED_UNRELATED_PATCH",
    "TV_RESEARCH_RESULT_REUSED_WITH_FINGERPRINT_MISMATCH",
    "TV_PLACEMENT_GENERATED_WITHOUT_REQUIREMENT_SET",
    "TV_PLACEMENT_GATE_ACCEPTED_WITH_PREFLIGHT_FAILURE",
    "TV_PLACEMENT_SOURCE_LESS_BINDING",
    "TV_PLACEMENT_INFERRED_COORDINATE",
    "TV_AXIAL_GENERATED_WITHOUT_REQUIREMENT_SET",
    "TV_AXIAL_GATE_ACCEPTED_WITH_PREFLIGHT_FAILURE",
    "TV_AXIAL_SOURCE_LESS_REGION",
    "TV_AXIAL_DEFAULT_HOMOGENIZATION_USED",
    "TV_AXIAL_FABRICATED_EXTENT",
    "TV_AXIAL_THROUGH_PATH_FAILURE_HIDDEN",
    "TV_REVIEW_COVERAGE_BASED_ONLY_ON_MODEL_SELF_REPORT",
    # Phase 8A Step 7 additions.
    "TV_CANDIDATE_SPANS_REPORTED_AS_COMMITTED_EVIDENCE",
    "TV_EVIDENCE_ADDED_WITHOUT_LEDGER_HASH_CHANGE",
    "TV_EVIDENCE_DELTA_CLAIM_MISSING_FROM_LEDGER",
    "TV_RESEARCH_PROPOSAL_WITHOUT_VALID_SOURCE_SPAN",
    "TV_RESEARCH_TARGET_NOT_COVERED",
    "TV_RESEARCH_GATE_REOPENED_WITHOUT_INPUT_HASH_CHANGE",
    "TV_RESEARCH_GATE_REOPENED_BEFORE_LEDGER_COMMIT",
    "TV_RESEARCH_REQUIREMENT_RECOMPILE_MISSING",
    "TV_RESEARCH_OWNER_REGENERATION_MISSING",
    "TV_RESEARCH_OWNER_CANDIDATE_NOT_CLONE_VALIDATED",
    "TV_RESEARCH_UNRELATED_PATCH_INVALIDATED",
    "TV_RESEARCH_REVIEWER_RESULT_REUSED",
    "TV_RESEARCH_GATE_ACCEPTED_WITHOUT_REPLAY",
    "TV_RESEARCH_NO_PROGRESS_LOOP_CONTINUED",
    "TV_RESEARCH_SOURCE_ABSENCE_DEFAULTED",
    "TV_FRAGMENT_CACHE_REUSED_ACROSS_REQUIREMENT_HASH_CHANGE",
]


# ---------------------------------------------------------------------------
# Individual violation codes
# ---------------------------------------------------------------------------


TV_ENABLED_WITHOUT_REAL_CLIENT_CALL = (
    "plan_investigation_enabled_without_real_client_call"
)
TV_FAKE_CLIENT_USED = "plan_investigation_fake_client_used"
TV_FALLBACK_USED = "plan_investigation_fallback_used"
TV_COMPLETED_WITHOUT_TOOL_CALL = (
    "plan_investigation_completed_without_tool_call"
)
TV_CLAIM_WITHOUT_VALID_SOURCE_REF = (
    "plan_investigation_claim_without_valid_source_ref"
)
TV_LEDGER_HASH_MISMATCH = "plan_investigation_ledger_hash_mismatch"
TV_SESSION_ARTIFACT_MISSING = (
    "plan_investigation_session_artifact_missing"
)
TV_REASONING_CONTENT_PERSISTED = (
    "plan_investigation_reasoning_content_persisted"
)
TV_CONTROLLED_FAILURE_BYPASSED = (
    "plan_investigation_controlled_failure_bypassed"
)
TV_FACTS_PATCH_BEFORE_INVESTIGATION = (
    "facts_patch_generated_before_investigation_completed"
)
TV_FACTS_PATCH_WITHOUT_REQUIRED_EVIDENCE = (
    "facts_patch_generated_without_required_evidence"
)
TV_FACTS_PROMPT_EVIDENCE_NOT_IN_LEDGER = (
    "facts_prompt_evidence_not_in_ledger"
)
TV_SESSION_REUSED_WITH_FINGERPRINT_MISMATCH = (
    "investigation_session_reused_with_fingerprint_mismatch"
)
TV_ARTIFACT_CONTAINS_HOST_PATH = (
    "investigation_artifact_contains_host_path"
)
TV_ARTIFACT_CONTAINS_SECRET = "investigation_artifact_contains_secret"

# Phase 8A Step 6 (Section 32) — additional truthfulness codes for
# inventory fail-closed, Materials/Universes investigation, and
# research-aware gate retry.
TV_CONTROLLED_INVENTORY_FAILURE_BYPASSED = (
    "controlled_inventory_failure_bypassed"
)
TV_CONTROLLED_INVENTORY_LEGACY_FALLBACK_USED = (
    "controlled_inventory_legacy_fallback_used"
)
TV_INVENTORY_CONSTRAINT_WITHOUT_SOURCE_PROVENANCE = (
    "inventory_constraint_without_source_provenance"
)
TV_INVENTORY_CONSTRAINT_MISLABELED_EXPLICIT = (
    "inventory_constraint_mislabeled_explicit"
)
TV_INVENTORY_PREFLIGHT_EXCEPTION_HIDDEN = (
    "inventory_preflight_exception_hidden"
)
TV_MATERIALS_INVESTIGATION_NOT_EXECUTED = (
    "materials_investigation_not_executed"
)
TV_UNIVERSES_INVESTIGATION_NOT_EXECUTED = (
    "universes_investigation_not_executed"
)
TV_MATERIALS_GENERATED_BEFORE_INVESTIGATION = (
    "materials_generated_before_investigation"
)
TV_UNIVERSES_GENERATED_BEFORE_INVESTIGATION = (
    "universes_generated_before_investigation"
)
TV_RESEARCH_ACTION_REPORTED_WITHOUT_REQUEST = (
    "research_action_reported_without_request"
)
TV_RESEARCH_REQUEST_WITHOUT_FINDINGS = (
    "research_request_without_findings"
)
TV_RESEARCH_RESULT_WITHOUT_TOOL_CALLS = (
    "research_result_without_tool_calls"
)
TV_RESEARCH_EVIDENCE_DELTA_INVALID = (
    "research_evidence_delta_invalid"
)
TV_RESEARCH_LEDGER_HASH_UNCHANGED_REPORTED_AS_SUCCESS = (
    "research_ledger_hash_unchanged_reported_as_success"
)
TV_RESEARCH_NO_PROGRESS_NOT_STOPPED = (
    "research_no_progress_not_stopped"
)
TV_RESEARCH_BUDGET_BYPASSED = "research_budget_bypassed"
TV_RESEARCH_OWNER_SELECTED_BY_LLM = (
    "research_owner_selected_by_llm"
)
TV_RESEARCH_GATE_REOPENED_WITHOUT_INPUT_CHANGE = (
    "research_gate_reopened_without_input_change"
)
TV_RESEARCH_INVALIDATED_UNRELATED_PATCH = (
    "research_invalidated_unrelated_patch"
)
TV_RESEARCH_RESULT_REUSED_WITH_FINGERPRINT_MISMATCH = (
    "research_result_reused_with_fingerprint_mismatch"
)
TV_PLACEMENT_GENERATED_WITHOUT_REQUIREMENT_SET = (
    "placement_generated_without_requirement_set"
)
TV_PLACEMENT_GATE_ACCEPTED_WITH_PREFLIGHT_FAILURE = (
    "placement_gate_accepted_with_preflight_failure"
)
TV_PLACEMENT_SOURCE_LESS_BINDING = (
    "placement_source_less_binding"
)
TV_PLACEMENT_INFERRED_COORDINATE = (
    "placement_inferred_coordinate"
)
TV_AXIAL_GENERATED_WITHOUT_REQUIREMENT_SET = (
    "axial_generated_without_requirement_set"
)
TV_AXIAL_GATE_ACCEPTED_WITH_PREFLIGHT_FAILURE = (
    "axial_gate_accepted_with_preflight_failure"
)
TV_AXIAL_SOURCE_LESS_REGION = (
    "axial_source_less_region"
)
TV_AXIAL_DEFAULT_HOMOGENIZATION_USED = (
    "axial_default_homogenization_used"
)
TV_AXIAL_FABRICATED_EXTENT = (
    "axial_fabricated_extent"
)
TV_AXIAL_THROUGH_PATH_FAILURE_HIDDEN = (
    "axial_through_path_failure_hidden"
)
TV_REVIEW_COVERAGE_BASED_ONLY_ON_MODEL_SELF_REPORT = (
    "review_coverage_based_only_on_model_self_report"
)
# Phase 8A Step 7 (Section 17) — research synthesis + replay violations.
TV_CANDIDATE_SPANS_REPORTED_AS_COMMITTED_EVIDENCE = (
    "candidate_spans_reported_as_committed_evidence"
)
TV_EVIDENCE_ADDED_WITHOUT_LEDGER_HASH_CHANGE = (
    "evidence_added_without_ledger_hash_change"
)
TV_EVIDENCE_DELTA_CLAIM_MISSING_FROM_LEDGER = (
    "evidence_delta_claim_missing_from_ledger"
)
TV_RESEARCH_PROPOSAL_WITHOUT_VALID_SOURCE_SPAN = (
    "research_proposal_without_valid_source_span"
)
TV_RESEARCH_TARGET_NOT_COVERED = (
    "research_target_not_covered"
)
TV_RESEARCH_GATE_REOPENED_WITHOUT_INPUT_HASH_CHANGE = (
    "research_gate_reopened_without_input_hash_change"
)
TV_RESEARCH_GATE_REOPENED_BEFORE_LEDGER_COMMIT = (
    "research_gate_reopened_before_ledger_commit"
)
TV_RESEARCH_REQUIREMENT_RECOMPILE_MISSING = (
    "research_requirement_recompile_missing"
)
TV_RESEARCH_OWNER_REGENERATION_MISSING = (
    "research_owner_regeneration_missing"
)
TV_RESEARCH_OWNER_CANDIDATE_NOT_CLONE_VALIDATED = (
    "research_owner_candidate_not_clone_validated"
)
TV_RESEARCH_UNRELATED_PATCH_INVALIDATED = (
    "research_unrelated_patch_invalidated"
)
TV_RESEARCH_REVIEWER_RESULT_REUSED = (
    "research_reviewer_result_reused"
)
TV_RESEARCH_GATE_ACCEPTED_WITHOUT_REPLAY = (
    "research_gate_accepted_without_replay"
)
TV_RESEARCH_NO_PROGRESS_LOOP_CONTINUED = (
    "research_no_progress_loop_continued"
)
TV_RESEARCH_SOURCE_ABSENCE_DEFAULTED = (
    "research_source_absence_defaulted"
)
TV_FRAGMENT_CACHE_REUSED_ACROSS_REQUIREMENT_HASH_CHANGE = (
    "fragment_cache_reused_across_requirement_hash_change"
)


INVESTIGATION_TRUTH_VIOLATIONS: tuple[str, ...] = (
    TV_ENABLED_WITHOUT_REAL_CLIENT_CALL,
    TV_FAKE_CLIENT_USED,
    TV_FALLBACK_USED,
    TV_COMPLETED_WITHOUT_TOOL_CALL,
    TV_CLAIM_WITHOUT_VALID_SOURCE_REF,
    TV_LEDGER_HASH_MISMATCH,
    TV_SESSION_ARTIFACT_MISSING,
    TV_REASONING_CONTENT_PERSISTED,
    TV_CONTROLLED_FAILURE_BYPASSED,
    TV_FACTS_PATCH_BEFORE_INVESTIGATION,
    TV_FACTS_PATCH_WITHOUT_REQUIRED_EVIDENCE,
    TV_FACTS_PROMPT_EVIDENCE_NOT_IN_LEDGER,
    TV_SESSION_REUSED_WITH_FINGERPRINT_MISMATCH,
    TV_ARTIFACT_CONTAINS_HOST_PATH,
    TV_ARTIFACT_CONTAINS_SECRET,
    # Phase 8A Step 6 additions.
    TV_CONTROLLED_INVENTORY_FAILURE_BYPASSED,
    TV_CONTROLLED_INVENTORY_LEGACY_FALLBACK_USED,
    TV_INVENTORY_CONSTRAINT_WITHOUT_SOURCE_PROVENANCE,
    TV_INVENTORY_CONSTRAINT_MISLABELED_EXPLICIT,
    TV_INVENTORY_PREFLIGHT_EXCEPTION_HIDDEN,
    TV_MATERIALS_INVESTIGATION_NOT_EXECUTED,
    TV_UNIVERSES_INVESTIGATION_NOT_EXECUTED,
    TV_MATERIALS_GENERATED_BEFORE_INVESTIGATION,
    TV_UNIVERSES_GENERATED_BEFORE_INVESTIGATION,
    TV_RESEARCH_ACTION_REPORTED_WITHOUT_REQUEST,
    TV_RESEARCH_REQUEST_WITHOUT_FINDINGS,
    TV_RESEARCH_RESULT_WITHOUT_TOOL_CALLS,
    TV_RESEARCH_EVIDENCE_DELTA_INVALID,
    TV_RESEARCH_LEDGER_HASH_UNCHANGED_REPORTED_AS_SUCCESS,
    TV_RESEARCH_NO_PROGRESS_NOT_STOPPED,
    TV_RESEARCH_BUDGET_BYPASSED,
    TV_RESEARCH_OWNER_SELECTED_BY_LLM,
    TV_RESEARCH_GATE_REOPENED_WITHOUT_INPUT_CHANGE,
    TV_RESEARCH_INVALIDATED_UNRELATED_PATCH,
    TV_RESEARCH_RESULT_REUSED_WITH_FINGERPRINT_MISMATCH,
    TV_PLACEMENT_GENERATED_WITHOUT_REQUIREMENT_SET,
    TV_PLACEMENT_GATE_ACCEPTED_WITH_PREFLIGHT_FAILURE,
    TV_PLACEMENT_SOURCE_LESS_BINDING,
    TV_PLACEMENT_INFERRED_COORDINATE,
    TV_AXIAL_GENERATED_WITHOUT_REQUIREMENT_SET,
    TV_AXIAL_GATE_ACCEPTED_WITH_PREFLIGHT_FAILURE,
    TV_AXIAL_SOURCE_LESS_REGION,
    TV_AXIAL_DEFAULT_HOMOGENIZATION_USED,
    TV_AXIAL_FABRICATED_EXTENT,
    TV_AXIAL_THROUGH_PATH_FAILURE_HIDDEN,
    TV_REVIEW_COVERAGE_BASED_ONLY_ON_MODEL_SELF_REPORT,
    # Phase 8A Step 7 additions.
    TV_CANDIDATE_SPANS_REPORTED_AS_COMMITTED_EVIDENCE,
    TV_EVIDENCE_ADDED_WITHOUT_LEDGER_HASH_CHANGE,
    TV_EVIDENCE_DELTA_CLAIM_MISSING_FROM_LEDGER,
    TV_RESEARCH_PROPOSAL_WITHOUT_VALID_SOURCE_SPAN,
    TV_RESEARCH_TARGET_NOT_COVERED,
    TV_RESEARCH_GATE_REOPENED_WITHOUT_INPUT_HASH_CHANGE,
    TV_RESEARCH_GATE_REOPENED_BEFORE_LEDGER_COMMIT,
    TV_RESEARCH_REQUIREMENT_RECOMPILE_MISSING,
    TV_RESEARCH_OWNER_REGENERATION_MISSING,
    TV_RESEARCH_OWNER_CANDIDATE_NOT_CLONE_VALIDATED,
    TV_RESEARCH_UNRELATED_PATCH_INVALIDATED,
    TV_RESEARCH_REVIEWER_RESULT_REUSED,
    TV_RESEARCH_GATE_ACCEPTED_WITHOUT_REPLAY,
    TV_RESEARCH_NO_PROGRESS_LOOP_CONTINUED,
    TV_RESEARCH_SOURCE_ABSENCE_DEFAULTED,
    TV_FRAGMENT_CACHE_REUSED_ACROSS_REQUIREMENT_HASH_CHANGE,
)


# ---------------------------------------------------------------------------
# Auditor
# ---------------------------------------------------------------------------


def investigation_truth_violations_for_run(
    *,
    run_summary: Mapping[str, Any],
    recorder_evidence: Mapping[str, Any] | None = None,
    investigation_outcome: Mapping[str, Any] | None = None,
    session_artifact_path: str | None = None,
    artifact_text_snapshot: str | None = None,
) -> list[str]:
    """Return the list of truth-violation codes for one campaign run.

    Parameters
    ----------
    run_summary
        The campaign run-summary dict.  Must contain at least
        ``plan_investigation_mode``; other fields are read defensively.
    recorder_evidence
        Optional :meth:`LLMCallRecorder.evidence_summary` dict.  When
        supplied, the auditor can attribute calls to the
        ``plan_investigator`` role via ``client_instance_ids``.
    investigation_outcome
        Optional :class:`InvestigationStageOutcome` model_dump.  Used to
        verify that the outcome actually contains the evidence the
        summary claims.
    session_artifact_path
        Optional path to ``investigation_session.json``.  When supplied,
        the auditor verifies the artifact exists.
    artifact_text_snapshot
        Optional text snapshot of the session artifact used to scan for
        host paths / secrets without re-reading the file.
    """

    mode = str(run_summary.get("plan_investigation_mode", "off")).lower()
    if mode == "off":
        # Mode is off → no investigation expected, no violations possible.
        return []

    violations: list[str] = []
    evidence = recorder_evidence or {}
    outcome = investigation_outcome or {}

    # 1. Real network call requirement.
    network_count = int(run_summary.get("plan_investigation_network_call_count", 0))
    if mode in {"controlled", "advisory"} and network_count < 1:
        # In advisory mode with a missing client, we expect 0 calls but
        # also that the run reports completed=False.  That is a separate
        # violation (TV_CONTROLLED_FAILURE_BYPASSED) checked below.
        if not (mode == "advisory" and outcome.get("completed") is False):
            violations.append(TV_ENABLED_WITHOUT_REAL_CLIENT_CALL)

    # 2. Fake / fallback client usage.
    if evidence.get("fake_client_used") or evidence.get("fake_fallback_used"):
        violations.append(TV_FAKE_CLIENT_USED)

    # 3. Completed without tool calls.
    completed = bool(outcome.get("completed"))
    tool_call_count = int(outcome.get("tool_call_count", 0))
    if completed and tool_call_count < 1:
        violations.append(TV_COMPLETED_WITHOUT_TOOL_CALL)

    # 4. Claim without source ref.
    source_backed = int(outcome.get("source_backed_claim_count", 0))
    claim_count = int(outcome.get("evidence_claim_count", 0))
    if completed and claim_count > 0 and source_backed < 1:
        violations.append(TV_CLAIM_WITHOUT_VALID_SOURCE_REF)

    # 5. Ledger hash mismatch.
    if outcome.get("ledger_hash_mismatch"):
        violations.append(TV_LEDGER_HASH_MISMATCH)

    # 6. Session artifact missing when expected.
    if (
        mode == "controlled"
        and completed
        and session_artifact_path is not None
        and not _path_exists(session_artifact_path)
    ):
        violations.append(TV_SESSION_ARTIFACT_MISSING)

    # 7. reasoning_content persisted into artifact.
    if artifact_text_snapshot and "reasoning_content" in artifact_text_snapshot:
        violations.append(TV_REASONING_CONTENT_PERSISTED)

    # 8. Controlled failure bypassed: mode=controlled + blocked=True +
    # a Facts patch was still generated.
    if (
        mode == "controlled"
        and outcome.get("blocked") is True
        and run_summary.get("facts_patch_generated_after_investigation") is True
    ):
        violations.append(TV_CONTROLLED_FAILURE_BYPASSED)

    # 9. Facts patch generated BEFORE investigation completed.
    if mode == "controlled" and run_summary.get("facts_patch_generated_after_investigation") is False:
        # If a facts patch exists at all but the flag is False, the patch
        # was generated before investigation finished.
        if run_summary.get("facts_patch_attempt_count", 0) > 0:
            violations.append(TV_FACTS_PATCH_BEFORE_INVESTIGATION)

    # 10. Facts patch without required evidence (controlled).
    if (
        mode == "controlled"
        and run_summary.get("facts_patch_generated_after_investigation") is True
        and not run_summary.get("facts_evidence_injected")
    ):
        violations.append(TV_FACTS_PATCH_WITHOUT_REQUIRED_EVIDENCE)

    # 11. Artifact contains host path / secret.
    if artifact_text_snapshot:
        if "/home/" in artifact_text_snapshot:
            violations.append(TV_ARTIFACT_CONTAINS_HOST_PATH)
        if any(
            secret in artifact_text_snapshot
            for secret in ("DEEPSEEK_API_KEY", "SENSENOVA_API_KEY", "ZHIPUAI_API_KEY")
        ):
            violations.append(TV_ARTIFACT_CONTAINS_SECRET)

    return violations


def _path_exists(path: str) -> bool:
    from pathlib import Path

    try:
        return Path(path).exists()
    except OSError:
        return False
