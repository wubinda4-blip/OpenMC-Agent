"""Tests for the LLM runtime diagnostician and constrained patch proposer (R4).

Covers: diagnosis validation, evidence grading, proposal static validation,
safety enforcement (protected paths, operation budget, allowlist), fake client
behavior, and graph routing.
"""

import pytest
from copy import deepcopy

from openmc_agent.runtime_feedback import RuntimeFailure, RuntimeFailureClass
from openmc_agent.runtime_diagnostician import (
    FakeRuntimeDiagnosticianClient,
    RuntimeDiagnosis,
    ValidatedRuntimeDiagnosis,
    build_runtime_diagnosis_input,
    validate_runtime_diagnosis,
)
from openmc_agent.runtime_patch_proposer import (
    FakeRuntimePatchProposerClient,
    LLMRuntimeRepairProposal,
    ProposalValidationResult,
    validate_llm_runtime_proposal,
    apply_proposal_to_clone,
)
from openmc_agent.runtime_repair_policy import get_repair_policy


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #

def _make_failure(
    code: str = "runtime.geometry_overlap",
    classification: RuntimeFailureClass = RuntimeFailureClass.PLAN_FIXABLE,
    fid: str = "rf_test_001",
) -> RuntimeFailure:
    return RuntimeFailure(
        failure_id=fid,
        stage="execute_tools",
        tool_name="run_geometry_debug",
        returncode=1,
        primary_issue_code=code,
        secondary_issue_codes=[],
        normalized_message="overlap detected",
        raw_error_excerpt="Overlap detected between cells 10 and 11",
        error_fingerprint="rt_overlap_001",
        classification=classification,
    )


def _make_diagnosis(
    failure: RuntimeFailure,
    disposition: str = "safe_to_propose",
    repair_kind: str = "reference_correction",
    target_patch_type: str = "universes",
    target_patch_id: str = "universes_0",
    confidence: float = 0.8,
    target_patch_paths: list[str] | None = None,
    contradictions: list[str] | None = None,
) -> RuntimeDiagnosis:
    return RuntimeDiagnosis(
        diagnosis_id="diag_test_001",
        failure_id=failure.failure_id,
        primary_issue_code=failure.primary_issue_code,
        classification=failure.classification.value,
        root_cause_summary="test diagnosis",
        disposition=disposition,
        target_patch_type=target_patch_type,
        target_patch_id=target_patch_id,
        target_patch_paths=target_patch_paths or ["/universes/0/cell_ids/0"],
        repair_kind=repair_kind,
        evidence_refs=["ev_failure", "ev_obj_map"],
        contradictions=contradictions or [],
        confidence=confidence,
    )


def _make_validated_diagnosis(
    target_patch_type: str = "universes",
    target_patch_id: str = "universes_0",
    allowed_paths: list[str] | None = None,
    proposal_allowed: bool = True,
    repair_kind: str = "reference_correction",
) -> ValidatedRuntimeDiagnosis:
    return ValidatedRuntimeDiagnosis(
        accepted=True,
        failure_id="rf_test_001",
        primary_issue_code="runtime.geometry_overlap",
        target_patch_type=target_patch_type,
        target_patch_id=target_patch_id,
        deterministically_allowed_paths=allowed_paths or ["/universes/*/cell_ids/*"],
        deterministically_forbidden_paths=["/composition*", "/density*"],
        repair_kind=repair_kind,
        risk_level="low",
        proposal_allowed=proposal_allowed,
    )


# --------------------------------------------------------------------------- #
# 1. Diagnosis validation
# --------------------------------------------------------------------------- #

class TestDiagnosisValidation:

    def test_valid_diagnosis_accepted(self):
        failure = _make_failure()
        diagnosis = _make_diagnosis(failure)
        result = validate_runtime_diagnosis(diagnosis, failure, {})
        assert result.accepted is True
        assert result.proposal_allowed is True

    def test_failure_id_mismatch_rejected(self):
        failure = _make_failure()
        diagnosis = _make_diagnosis(failure)
        diagnosis.failure_id = "wrong_id"
        result = validate_runtime_diagnosis(diagnosis, failure, {})
        assert "failure_id_mismatch" in result.rejection_codes

    def test_classification_change_rejected(self):
        failure = _make_failure()
        diagnosis = _make_diagnosis(failure)
        diagnosis.classification = "environment"
        result = validate_runtime_diagnosis(diagnosis, failure, {})
        assert "classification_changed" in result.rejection_codes

    def test_environment_blocked(self):
        failure = _make_failure(
            code="runtime.cross_sections_missing",
            classification=RuntimeFailureClass.ENVIRONMENT,
        )
        diagnosis = _make_diagnosis(failure)
        result = validate_runtime_diagnosis(diagnosis, failure, {})
        assert result.accepted is False
        assert "blocked_classification" in result.rejection_codes

    def test_low_confidence_rejected(self):
        failure = _make_failure()
        diagnosis = _make_diagnosis(failure, confidence=0.1)
        result = validate_runtime_diagnosis(diagnosis, failure, {})
        assert "low_confidence" in result.rejection_codes

    def test_contradictions_block(self):
        failure = _make_failure()
        diagnosis = _make_diagnosis(failure, contradictions=["conflicting evidence"])
        result = validate_runtime_diagnosis(diagnosis, failure, {})
        assert "unresolved_contradictions" in result.rejection_codes

    def test_disposition_no_safe_repair(self):
        failure = _make_failure()
        diagnosis = _make_diagnosis(failure, disposition="no_safe_repair")
        result = validate_runtime_diagnosis(diagnosis, failure, {})
        assert result.accepted is False

    def test_disposition_ambiguous_owner(self):
        failure = _make_failure()
        diagnosis = _make_diagnosis(failure, disposition="ambiguous_owner")
        result = validate_runtime_diagnosis(diagnosis, failure, {})
        assert result.accepted is False

    def test_repair_kind_not_in_policy(self):
        failure = _make_failure()
        diagnosis = _make_diagnosis(
            failure, repair_kind="environment_fix_required",
        )
        result = validate_runtime_diagnosis(diagnosis, failure, {})
        assert result.proposal_allowed is False

    def test_hallucinated_patch_type_rejected(self):
        failure = _make_failure()
        diagnosis = _make_diagnosis(failure, target_patch_type="materials")
        result = validate_runtime_diagnosis(diagnosis, failure, {})
        assert "target_patch_type_not_in_policy" in result.rejection_codes


# --------------------------------------------------------------------------- #
# 2. Evidence and diagnosis input
# --------------------------------------------------------------------------- #

class TestDiagnosisInput:

    def test_input_has_evidence(self):
        failure = _make_failure()
        result = build_runtime_diagnosis_input(failure, None, {}, [])
        assert "failure" in result
        assert "evidence" in result
        assert len(result["evidence"]) >= 2
        assert any(e["evidence_id"] == "ev_failure" for e in result["evidence"])

    def test_input_does_not_include_full_plan(self):
        failure = _make_failure()
        result = build_runtime_diagnosis_input(failure, None, {}, [])
        serialized = str(result)
        # Should not contain full SimulationPlan dump
        assert "model_spec" not in serialized or len(serialized) < 10000


# --------------------------------------------------------------------------- #
# 3. Proposal static validation
# --------------------------------------------------------------------------- #

class TestProposalValidation:

    def test_valid_reference_correction_accepted(self):
        validated = _make_validated_diagnosis(
            allowed_paths=["/universes/*/cell_ids/*"],
        )
        current_patch = {"universes": [{"id": "u1", "cell_ids": ["c1", "c99"]}]}
        raw = {
            "proposal_id": "rp_001",
            "diagnosis_id": "rf_test_001",
            "failure_id": "rf_test_001",
            "target_patch_type": "universes",
            "repair_kind": "reference_correction",
            "operations": [
                {"op": "test", "path": "/universes/0/cell_ids/1", "value": "c99"},
                {"op": "replace", "path": "/universes/0/cell_ids/1", "value": "c1"},
            ],
            "rationale": "Fix dangling reference",
            "confidence": 0.9,
        }
        result = validate_llm_runtime_proposal(raw, validated, current_patch)
        assert result.accepted is True

    def test_empty_operations_rejected(self):
        validated = _make_validated_diagnosis()
        raw = {
            "proposal_id": "rp_002",
            "diagnosis_id": "rf_test_001",
            "failure_id": "rf_test_001",
            "target_patch_type": "universes",
            "operations": [],
        }
        result = validate_llm_runtime_proposal(raw, validated, {})
        assert result.accepted is False
        assert "empty_operations" in result.rejection_codes

    def test_protected_path_rejected(self):
        validated = _make_validated_diagnosis(
            allowed_paths=["/composition/*/name"],
        )
        raw = {
            "proposal_id": "rp_003",
            "diagnosis_id": "rf_test_001",
            "failure_id": "rf_test_001",
            "target_patch_type": "universes",
            "operations": [
                {"op": "replace", "path": "/density_value", "value": 5.0},
            ],
        }
        result = validate_llm_runtime_proposal(raw, validated, {})
        assert result.accepted is False
        # Density is protected/forbidden
        assert any("protected" in c or "forbidden" in c for c in result.rejection_codes)

    def test_too_many_mutating_ops_rejected(self):
        validated = _make_validated_diagnosis(
            allowed_paths=["/x", "/y", "/z", "/w", "/v"],
        )
        raw = {
            "proposal_id": "rp_004",
            "diagnosis_id": "rf_test_001",
            "failure_id": "rf_test_001",
            "target_patch_type": "universes",
            "operations": [
                {"op": "replace", "path": "/x", "value": 1},
                {"op": "replace", "path": "/y", "value": 2},
                {"op": "replace", "path": "/z", "value": 3},
                {"op": "replace", "path": "/w", "value": 4},
                {"op": "replace", "path": "/v", "value": 5},
            ],
        }
        result = validate_llm_runtime_proposal(raw, validated, {}, max_mutating_operations=4)
        assert result.accepted is False
        assert "too_many_mutating_ops" in result.rejection_codes

    def test_root_replacement_rejected(self):
        validated = _make_validated_diagnosis()
        raw = {
            "proposal_id": "rp_005",
            "diagnosis_id": "rf_test_001",
            "failure_id": "rf_test_001",
            "target_patch_type": "universes",
            "operations": [
                {"op": "replace", "path": "", "value": {}},
            ],
        }
        result = validate_llm_runtime_proposal(raw, validated, {})
        assert result.accepted is False
        assert any("root" in c for c in result.rejection_codes)

    def test_test_value_mismatch_rejected(self):
        validated = _make_validated_diagnosis(
            allowed_paths=["/name"],
        )
        current_patch = {"name": "old_name"}
        raw = {
            "proposal_id": "rp_006",
            "diagnosis_id": "rf_test_001",
            "failure_id": "rf_test_001",
            "target_patch_type": "universes",
            "operations": [
                {"op": "test", "path": "/name", "value": "wrong_value"},
                {"op": "replace", "path": "/name", "value": "new_name"},
            ],
        }
        result = validate_llm_runtime_proposal(raw, validated, current_patch)
        assert result.accepted is False
        assert any("test_mismatch" in c for c in result.rejection_codes)

    def test_path_not_in_allowlist_rejected(self):
        validated = _make_validated_diagnosis(
            allowed_paths=["/universes/*/cell_ids/*"],
        )
        raw = {
            "proposal_id": "rp_007",
            "diagnosis_id": "rf_test_001",
            "failure_id": "rf_test_001",
            "target_patch_type": "universes",
            "operations": [
                {"op": "replace", "path": "/surfaces/0/r", "value": 0.5},
            ],
        }
        result = validate_llm_runtime_proposal(raw, validated, {})
        assert result.accepted is False
        assert any("not_in_allowlist" in c for c in result.rejection_codes)


# --------------------------------------------------------------------------- #
# 4. Fake client behavior
# --------------------------------------------------------------------------- #

class TestFakeClients:

    def test_fake_diagnostician_returns_no_safe_repair(self):
        client = FakeRuntimeDiagnosticianClient()
        result = client.diagnose(
            {"failure": {"failure_id": "test", "primary_issue_code": "x"}},
            prompt="",
            json_schema={},
        )
        assert result["disposition"] == "no_safe_repair"

    def test_fake_proposer_returns_empty_operations(self):
        client = FakeRuntimePatchProposerClient()
        result = client.propose(
            {"diagnosis_id": "test"},
            prompt="",
            json_schema={},
        )
        assert result["operations"] == []

    def test_fake_diagnostician_not_used_when_real_unavailable(self):
        """When allow_fallback=False and client=None, should not use fake."""
        from openmc_agent.runtime_diagnostician import make_runtime_diagnostician_client
        client = make_runtime_diagnostician_client(llm=None)
        assert isinstance(client, FakeRuntimeDiagnosticianClient)


# --------------------------------------------------------------------------- #
# 5. Policy LLM fields
# --------------------------------------------------------------------------- #

class TestPolicyLLMFields:

    def test_geometry_has_llm_diagnosis(self):
        policy = get_repair_policy("runtime.geometry_overlap")
        assert policy.llm_diagnosis_supported is True
        assert policy.llm_proposal_supported is True
        assert "reference_correction" in policy.allowed_repair_kinds

    def test_source_rejection_has_llm_diagnosis(self):
        policy = get_repair_policy("runtime.openmc_source_rejection_failure")
        assert policy.llm_diagnosis_supported is True
        assert policy.llm_proposal_supported is False  # deterministic only

    def test_environment_no_llm(self):
        policy = get_repair_policy("runtime.cross_sections_missing")
        assert policy.llm_diagnosis_supported is False
        assert policy.llm_proposal_supported is False


# --------------------------------------------------------------------------- #
# 6. Graph routing for LLM diagnosis
# --------------------------------------------------------------------------- #

class TestGraphRouting:

    def test_llm_diagnose_router_propose(self):
        from openmc_agent.graph import _make_llm_diagnosis_router
        state = {
            "runtime_validated_diagnosis": {
                "proposal_allowed": True,
            },
            "runtime_llm_proposal_count": 0,
        }
        router = _make_llm_diagnosis_router()
        assert router(state) == "propose"

    def test_llm_diagnose_router_save_no_proposal(self):
        from openmc_agent.graph import _make_llm_diagnosis_router
        state = {
            "runtime_validated_diagnosis": {
                "proposal_allowed": False,
            },
        }
        router = _make_llm_diagnosis_router()
        assert router(state) == "save"

    def test_runtime_feedback_router_llm_diagnose(self):
        from openmc_agent.graph import _make_runtime_feedback_router
        state = {
            "runtime_primary_failure": {
                "primary_issue_code": "runtime.geometry_overlap",
            },
            "runtime_repair_count": 0,
            "runtime_llm_diagnosis_count": 0,
        }
        router = _make_runtime_feedback_router(enable_llm_runtime_repair=True)
        # Geometry overlap has no deterministic repair, but has LLM diagnosis
        assert router(state) == "llm_diagnose"

    def test_runtime_feedback_router_no_llm(self):
        from openmc_agent.graph import _make_runtime_feedback_router
        state = {
            "runtime_primary_failure": {
                "primary_issue_code": "runtime.geometry_overlap",
            },
            "runtime_repair_count": 0,
            "runtime_llm_diagnosis_count": 0,
        }
        router = _make_runtime_feedback_router(enable_llm_runtime_repair=False)
        assert router(state) == "save"
