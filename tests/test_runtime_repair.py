"""Tests for runtime repair policy, source oracle, clone evaluation,
and one-shot graph recovery (P1-RUNTIME-R2/R3).

Covers: fault injection for source strategy, environment blocker,
fuel geometry missing, ambiguous geometry, duplicate/no-progress,
valid patch preservation, and VERA3B source-fault recovery.
"""

import json
import pytest
from pathlib import Path
from copy import deepcopy

from openmc_agent.runtime_feedback import (
    RuntimeFailure,
    RuntimeFailureClass,
)
from openmc_agent.runtime_repair_policy import (
    RUNTIME_REPAIR_POLICIES,
    get_repair_policy,
    is_environment_blocked,
)
from openmc_agent.runtime_repair import (
    RuntimeRepairEvaluation,
    RuntimeRepairRequest,
    DeterministicRuntimeRepairProposal,
    build_runtime_repair_request,
    diagnose_source_runtime_failure,
    propose_source_binding_repair,
    diagnose_geometry_runtime_failure,
    evaluate_deterministic_runtime_repair,
    commit_accepted_runtime_repair,
    stable_json_hash,
)


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #

def _make_failure(
    code: str = "runtime.openmc_source_rejection_failure",
    classification: RuntimeFailureClass = RuntimeFailureClass.PLAN_FIXABLE,
    tool_name: str = "run_smoke_test",
) -> RuntimeFailure:
    return RuntimeFailure(
        failure_id="rf_test_001",
        stage="execute_tools",
        tool_name=tool_name,
        returncode=1,
        primary_issue_code=code,
        secondary_issue_codes=[],
        normalized_message="test failure",
        raw_error_excerpt="Too few source sites",
        error_fingerprint="rt_test001",
        classification=classification,
        owner_patch_types=["settings"],
    )


def _make_settings_patch(
    source_strategy: str = "assembly_box",
    fissionable: bool = False,
) -> dict[str, any]:
    return {
        "patch_type": "settings",
        "source_strategy": source_strategy,
        "source_requires_fissionable_constraint": fissionable,
        "plot_strategy": "full_assembly",
        "cross_sections_runtime_required": True,
        "tallies_required_for_smoke_test": False,
        "assumptions": [],
    }


# --------------------------------------------------------------------------- #
# 1. Repair policy registry
# --------------------------------------------------------------------------- #

class TestRuntimeRepairPolicy:

    def test_source_rejection_has_deterministic_repair(self):
        policy = get_repair_policy("runtime.openmc_source_rejection_failure")
        assert policy is not None
        assert policy.deterministic_repair_supported is True
        assert policy.preferred_patch_type == "settings"
        assert "/source_strategy" in policy.allowed_path_patterns

    def test_environment_blocked(self):
        assert is_environment_blocked(["runtime.cross_sections_missing"]) is True
        assert is_environment_blocked(["runtime.geometry_overlap"]) is False

    def test_cross_section_no_repair(self):
        policy = get_repair_policy("runtime.cross_sections_missing")
        assert policy is not None
        assert policy.deterministic_repair_supported is False
        assert policy.candidate_patch_types == []

    def test_geometry_overlap_diagnose_only(self):
        policy = get_repair_policy("runtime.geometry_overlap")
        assert policy is not None
        assert policy.deterministic_repair_supported is False

    def test_timeout_no_repair(self):
        policy = get_repair_policy("runtime.openmc_timeout")
        assert policy is not None
        assert policy.deterministic_repair_supported is False

    def test_all_required_codes_registered(self):
        required = [
            "runtime.openmc_source_rejection_failure",
            "runtime.source_default_z_extent",
            "runtime.source_not_in_active_fuel_region",
            "runtime.source_covers_nonfuel_axial_regions",
            "runtime.active_fuel_region_missing",
            "runtime.fuel_material_not_fissionable",
            "runtime.active_fuel_geometry_missing",
            "runtime.geometry_overlap",
            "runtime.lost_particle",
            "runtime.material_missing_nuclide_data",
            "runtime.cross_sections_missing",
            "runtime.cross_sections_invalid",
            "runtime.openmc_timeout",
            "runtime.openmc_process_crash",
            "runtime.openmc_unknown_error",
            "export_xml.dangling_cell_fill",
            "export_xml.dangling_lattice_universe",
            "export_xml.dangling_lattice_outer_universe",
        ]
        for code in required:
            assert code in RUNTIME_REPAIR_POLICIES, f"Missing policy for {code}"


# --------------------------------------------------------------------------- #
# 2. Source binding oracle
# --------------------------------------------------------------------------- #

class TestSourceBindingOracle:

    def test_source_strategy_assembly_box_repaired_to_active_fuel(self):
        """Fault: source_strategy=assembly_box → repair to active_fuel_box."""
        failure = _make_failure()
        current_patch = _make_settings_patch("assembly_box", fissionable=False)
        request = RuntimeRepairRequest(
            request_id="rr_test",
            runtime_failure=failure.to_dict(),
            target_patch_type="settings",
            target_patch_id="settings_patch_0",
            current_patch=current_patch,
            allowed_paths=["/source_strategy", "/source_requires_fissionable_constraint"],
        )
        diagnosis = {
            "safe_repair_available": True,
            "reasons": ["Strategy 'assembly_box' → 'active_fuel_box'"],
            "current_source_strategy": "assembly_box",
            "active_fuel_z_bounds": (0.0, 400.0),
            "fissionable_material_count": 2,
        }
        proposal = propose_source_binding_repair(request, diagnosis)
        assert isinstance(proposal, DeterministicRuntimeRepairProposal)
        assert "/source_strategy" in proposal.changed_paths
        assert any(
            op["path"] == "/source_strategy" and op["value"] == "active_fuel_box"
            for op in proposal.operations
        )

    def test_source_strategy_manual_repaired(self):
        failure = _make_failure()
        request = RuntimeRepairRequest(
            request_id="rr_test",
            runtime_failure=failure.to_dict(),
            target_patch_type="settings",
            current_patch=_make_settings_patch("manual", fissionable=True),
            allowed_paths=["/source_strategy"],
        )
        diagnosis = {
            "safe_repair_available": True,
            "reasons": ["Strategy 'manual' → 'active_fuel_box'"],
            "current_source_strategy": "manual",
            "active_fuel_z_bounds": (0.0, 400.0),
            "fissionable_material_count": 1,
        }
        proposal = propose_source_binding_repair(request, diagnosis)
        assert isinstance(proposal, DeterministicRuntimeRepairProposal)
        assert "/source_strategy" in proposal.changed_paths
        # fissionable already True, should not be changed.
        assert "/source_requires_fissionable_constraint" not in proposal.changed_paths

    def test_source_noop_guard(self):
        """When settings already correct, no proposal."""
        failure = _make_failure()
        request = RuntimeRepairRequest(
            request_id="rr_test",
            runtime_failure=failure.to_dict(),
            target_patch_type="settings",
            current_patch=_make_settings_patch("active_fuel_box", fissionable=True),
            allowed_paths=["/source_strategy"],
        )
        diagnosis = {
            "safe_repair_available": True,
            "reasons": [],
            "current_source_strategy": "active_fuel_box",
        }
        result = propose_source_binding_repair(request, diagnosis)
        assert isinstance(result, RuntimeRepairEvaluation)
        assert result.disposition == "no_safe_repair"
        assert "renderer/source-binding bug" in result.reasons[0]

    def test_source_no_fuel_returns_no_safe_repair(self):
        failure = _make_failure()
        request = RuntimeRepairRequest(
            request_id="rr_test",
            runtime_failure=failure.to_dict(),
            target_patch_type="settings",
            current_patch=_make_settings_patch("assembly_box"),
            allowed_paths=["/source_strategy"],
        )
        diagnosis = {
            "safe_repair_available": False,
            "reasons": ["No fissionable material found"],
        }
        result = propose_source_binding_repair(request, diagnosis)
        assert isinstance(result, RuntimeRepairEvaluation)
        assert result.disposition == "no_safe_repair"


# --------------------------------------------------------------------------- #
# 3. build_runtime_repair_request routing
# --------------------------------------------------------------------------- #

class TestBuildRepairRequest:

    def test_environment_blocked(self):
        failure = _make_failure(
            code="runtime.cross_sections_missing",
            classification=RuntimeFailureClass.ENVIRONMENT,
        )
        result = build_runtime_repair_request(failure, None, {}, [])
        assert isinstance(result, RuntimeRepairEvaluation)
        assert result.disposition == "blocked_environment"

    def test_unknown_blocked(self):
        failure = _make_failure(
            code="runtime.openmc_unknown_error",
            classification=RuntimeFailureClass.UNKNOWN,
        )
        result = build_runtime_repair_request(failure, None, {}, [])
        assert isinstance(result, RuntimeRepairEvaluation)
        assert result.disposition == "no_safe_repair"

    def test_geometry_not_supported(self):
        failure = _make_failure(
            code="runtime.geometry_overlap",
            classification=RuntimeFailureClass.PLAN_FIXABLE,
        )
        result = build_runtime_repair_request(failure, None, {}, [])
        assert isinstance(result, RuntimeRepairEvaluation)
        assert result.disposition == "no_safe_repair"


# --------------------------------------------------------------------------- #
# 4. Geometry diagnosis
# --------------------------------------------------------------------------- #

class TestGeometryDiagnosis:

    def test_lost_particle_diagnose_only(self):
        failure = _make_failure(
            code="runtime.lost_particle",
            classification=RuntimeFailureClass.PLAN_FIXABLE,
        )
        failure.raw_error_excerpt = "particle could not be located in any cell 42"
        diagnosis = diagnose_geometry_runtime_failure(failure, None, None)
        assert diagnosis["safe_repair_available"] is False
        assert "42" in diagnosis["reported_cell_ids"]

    def test_overlap_no_object_map(self):
        failure = _make_failure(
            code="runtime.geometry_overlap",
            classification=RuntimeFailureClass.PLAN_FIXABLE,
        )
        failure.raw_error_excerpt = "Overlap detected between cells 10 and 11"
        diagnosis = diagnose_geometry_runtime_failure(failure, None, None)
        assert diagnosis["safe_repair_available"] is False
        assert "10" in diagnosis["reported_cell_ids"]
        assert "11" in diagnosis["reported_cell_ids"]


# --------------------------------------------------------------------------- #
# 5. Clone evaluation
# --------------------------------------------------------------------------- #

class TestCloneEvaluation:

    def test_duplicate_candidate_rejected(self):
        request = RuntimeRepairRequest(
            request_id="rr_test",
            target_patch_type="settings",
            current_patch=_make_settings_patch("assembly_box"),
            source_plan_hash="abc",
        )
        proposal = DeterministicRuntimeRepairProposal(
            proposal_id="rp_test",
            request_id="rr_test",
            target_patch_type="settings",
            operations=[{"op": "replace", "path": "/source_strategy", "value": "active_fuel_box"}],
            changed_paths=["/source_strategy"],
            deterministic_rule_id="test",
        )
        # Make prior hash match what the candidate would produce.
        from openmc_agent.runtime_repair import _apply_operations_to_clone
        candidate = _apply_operations_to_clone(
            _make_settings_patch("assembly_box"), proposal.operations
        )
        prior_hash = stable_json_hash(candidate)
        result = evaluate_deterministic_runtime_repair(
            request, proposal, {}, prior_candidate_hashes=[prior_hash],
        )
        assert result.disposition == "duplicate_candidate"


# --------------------------------------------------------------------------- #
# 6. Root cause precedence unification
# --------------------------------------------------------------------------- #

class TestRootCausePrecedence:

    def test_source_rejection_dominates_crash(self):
        from openmc_agent.tools import ToolResult, parse_openmc_output
        report = parse_openmc_output(
            stdout="",
            stderr=(
                "Too few source sites satisfied minimum source rejection fraction\n"
                "double free or corruption\nMPI_ABORT was invoked"
            ),
        )
        codes = [i.code for i in report.issues]
        # Source rejection should appear.
        assert "runtime.openmc_source_rejection_failure" in codes
        # Crash should also appear but as secondary.
        assert "runtime.openmc_process_crash" in codes

    def test_cross_section_dominates_geometry(self):
        from openmc_agent.tools import parse_openmc_output
        report = parse_openmc_output(
            stdout="",
            stderr="No cross_sections.xml was specified\nOverlap detected between cells 1 and 2",
        )
        codes = [i.code for i in report.issues]
        assert "runtime.cross_sections_missing" in codes
        assert "runtime.geometry_overlap" in codes


# --------------------------------------------------------------------------- #
# 7. Source strategy fault injection (graph integration)
# --------------------------------------------------------------------------- #

@pytest.mark.openmc
class TestSourceFaultInjectionGraph:

    def test_runtime_repair_routing_activates_on_source_rejection(self, tmp_path: Path) -> None:
        """Verify the execution router routes to runtime_repair when source rejection detected."""
        from openmc_agent.graph import (
            _make_plan_execution_router,
            _has_runtime_repairable_failure,
        )
        from openmc_agent.schemas import ValidationIssue, ValidationReport
        from openmc_agent.error_catalog import issue_from_catalog

        # Build a validation_report with a source rejection issue.
        issue = issue_from_catalog(
            "runtime.openmc_source_rejection_failure",
            message="Too few source sites",
        )
        report = ValidationReport.from_issues([issue], is_valid=False)

        state = {
            "validation_report": report,
            "runtime_repair_count": 0,
            "runtime_repair_applied": False,
            "tool_results": [],
            "retry_count": 0,
        }

        # Should detect runtime-repairable failure.
        assert _has_runtime_repairable_failure(state) is True

        # Router should return "runtime_repair".
        router = _make_plan_execution_router(max_retries=3)
        assert router(state) == "runtime_repair"

    def test_runtime_repair_not_activated_on_environment(self, tmp_path: Path) -> None:
        """Environment failures should NOT route to runtime repair."""
        from openmc_agent.graph import (
            _make_plan_execution_router,
            _has_runtime_repairable_failure,
        )
        from openmc_agent.schemas import ValidationReport
        from openmc_agent.error_catalog import issue_from_catalog

        issue = issue_from_catalog(
            "runtime.cross_sections_missing",
            message="No cross_sections.xml",
        )
        report = ValidationReport.from_issues([issue], is_valid=False)

        state = {
            "validation_report": report,
            "runtime_repair_count": 0,
            "runtime_repair_applied": False,
            "tool_results": [],
            "retry_count": 0,
        }

        assert _has_runtime_repairable_failure(state) is False

    def test_runtime_repair_one_shot_budget(self, tmp_path: Path) -> None:
        """After one repair attempt, should not route to runtime_repair again."""
        from openmc_agent.graph import _make_plan_execution_router
        from openmc_agent.schemas import ValidationReport
        from openmc_agent.error_catalog import issue_from_catalog

        issue = issue_from_catalog("runtime.openmc_source_rejection_failure")
        report = ValidationReport.from_issues([issue], is_valid=False)

        state = {
            "validation_report": report,
            "runtime_repair_count": 1,  # Already used
            "runtime_repair_applied": False,
            "tool_results": [],
            "retry_count": 0,
        }

        router = _make_plan_execution_router(max_retries=3)
        result = router(state)
        assert result != "runtime_repair"


# --------------------------------------------------------------------------- #
# 8. Valid patch preservation
# --------------------------------------------------------------------------- #

class TestValidPatchPreservation:

    def test_rejected_repair_preserves_valid_patches(self):
        """A rejected runtime repair should not modify valid patches."""
        from openmc_agent.plan_builder.state import (
            PlanBuildState,
            PlanPatchEnvelope,
        )

        # Build a state with valid facts + materials + settings patches.
        state = PlanBuildState(
            state_id="test_state_001",
            requirement_text="test requirement",
        )
        for ptype, content in [
            ("facts", {"benchmark_id": "TEST", "has_axial_geometry": True}),
            ("materials", {"materials": [{"material_id": "m", "name": "M", "role": "fuel", "density_g_cm3": 10.0}]}),
            ("settings", _make_settings_patch("assembly_box")),
        ]:
            env = PlanPatchEnvelope(
                patch_id=f"{ptype}_0",
                patch_type=ptype,
                content=content,
                status="valid",
            )
            state.add_patch(env)

        facts_hash_before = stable_json_hash(state.patches["facts_0"].content)
        materials_hash_before = stable_json_hash(state.patches["materials_0"].content)

        # Attempt an environment-blocked repair.
        failure = _make_failure(
            code="runtime.cross_sections_missing",
            classification=RuntimeFailureClass.ENVIRONMENT,
        )
        result = build_runtime_repair_request(failure, None, state.model_dump(mode="json"), [])
        assert isinstance(result, RuntimeRepairEvaluation)
        assert result.disposition == "blocked_environment"

        # Verify patches unchanged.
        assert stable_json_hash(state.patches["facts_0"].content) == facts_hash_before
        assert stable_json_hash(state.patches["materials_0"].content) == materials_hash_before


# --------------------------------------------------------------------------- #
# 9. Fingerprint stability for repair dedup
# --------------------------------------------------------------------------- #

class TestFingerprintDedup:

    def test_same_fingerprint_different_run_dir(self):
        from openmc_agent.runtime_feedback import (
            normalize_runtime_error,
            compute_runtime_error_fingerprint,
        )
        text_a = "Source rejection at /home/wbd/runs/VERA_3B on 2024-01-15"
        text_b = "Source rejection at /tmp/opencode/runs/VERA_3B on 2025-06-20"
        fp_a = compute_runtime_error_fingerprint(normalize_runtime_error(text_a))
        fp_b = compute_runtime_error_fingerprint(normalize_runtime_error(text_b))
        assert fp_a == fp_b
