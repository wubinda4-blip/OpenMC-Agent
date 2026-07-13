"""Tests for the structured runtime feedback layer (runtime_feedback.py).

Covers: source-rejection precedence over crash, geometry overlap, lost
particle, cross-section environment, missing nuclide, timeout, unknown
error, fingerprint stability, and absolute-path normalization.
"""

import pytest

from openmc_agent.runtime_feedback import (
    RuntimeFailure,
    RuntimeFailureClass,
    classify_runtime_tool_results,
    compute_runtime_error_fingerprint,
    normalize_runtime_error,
)
from openmc_agent.tools import ToolResult


def _make_result(
    name: str = "run_smoke_test",
    *,
    ok: bool = False,
    error: str = "ERROR",
    issues=None,
) -> ToolResult:
    from openmc_agent.schemas import ValidationIssue
    return ToolResult(
        name=name,
        ok=ok,
        returncode=1,
        stdout="",
        stderr=error,
        error=error,
        issues=issues or [],
    )


def _issue(code: str, severity: str = "error"):
    from openmc_agent.schemas import ValidationIssue
    return ValidationIssue(severity=severity, code=code, message=f"test {code}")


# ---- 1. Source rejection + segfault → source rejection is primary ----

def test_source_rejection_dominates_crash_noise():
    result = _make_result(
        error="Too few source sites satisfied minimum source rejection fraction\n"
              "double free or corruption\nMPI_ABORT was invoked",
        issues=[
            _issue("runtime.openmc_source_rejection_failure"),
            _issue("runtime.openmc_process_crash"),
        ],
    )
    failures = classify_runtime_tool_results([result])
    assert len(failures) == 1
    f = failures[0]
    assert f.primary_issue_code == "runtime.openmc_source_rejection_failure"
    assert "runtime.openmc_process_crash" in f.secondary_issue_codes
    assert f.classification is RuntimeFailureClass.PLAN_FIXABLE
    assert f.environment_only is False


# ---- 2. Geometry overlap classification ----

def test_geometry_overlap_classified_as_plan_fixable():
    result = _make_result(
        error="Overlap detected between cells 10 and 11",
        issues=[_issue("runtime.geometry_overlap")],
    )
    failures = classify_runtime_tool_results([result])
    assert len(failures) == 1
    f = failures[0]
    assert f.primary_issue_code == "runtime.geometry_overlap"
    assert f.classification is RuntimeFailureClass.PLAN_FIXABLE
    assert "cells" in f.owner_patch_types


# ---- 3. Lost particle classification ----

def test_lost_particle_classified_as_plan_fixable():
    result = _make_result(
        error="Lost particle could not be located in any cell",
        issues=[_issue("runtime.lost_particle")],
    )
    failures = classify_runtime_tool_results([result])
    assert len(failures) == 1
    f = failures[0]
    assert f.primary_issue_code == "runtime.lost_particle"
    assert f.classification is RuntimeFailureClass.PLAN_FIXABLE


# ---- 4. Cross-section missing → environment ----

def test_cross_section_missing_classified_as_environment():
    result = _make_result(
        error="No cross_sections.xml was specified",
        issues=[_issue("runtime.cross_sections_missing")],
    )
    failures = classify_runtime_tool_results([result])
    assert len(failures) == 1
    f = failures[0]
    assert f.classification is RuntimeFailureClass.ENVIRONMENT
    assert f.environment_only is True
    assert f.requires_human_confirmation is True


def test_cross_section_environment_dominates_geometry():
    result = _make_result(
        error="cross_sections.xml not found and overlap detected",
        issues=[
            _issue("runtime.geometry_overlap"),
            _issue("runtime.cross_sections_missing"),
        ],
    )
    failures = classify_runtime_tool_results([result])
    assert len(failures) == 1
    f = failures[0]
    assert f.primary_issue_code == "runtime.cross_sections_missing"
    assert f.classification is RuntimeFailureClass.ENVIRONMENT


# ---- 5. Missing nuclide → human_fact ----

def test_missing_nuclide_classified_as_human_fact():
    result = _make_result(
        error="Nuclide Pu239 not present in cross_sections.xml",
        issues=[_issue("runtime.material_missing_nuclide_data")],
    )
    failures = classify_runtime_tool_results([result])
    assert len(failures) == 1
    f = failures[0]
    assert f.classification is RuntimeFailureClass.HUMAN_FACT
    assert f.requires_human_confirmation is True


# ---- 6. Timeout classification ----

def test_timeout_classified_as_transient():
    result = _make_result(
        name="run_geometry_debug",
        error="OpenMC geometry debug timed out after 120s",
        issues=[_issue("runtime.openmc_timeout")],
    )
    failures = classify_runtime_tool_results([result])
    assert len(failures) == 1
    f = failures[0]
    assert f.classification is RuntimeFailureClass.TRANSIENT


# ---- 7. Unknown error classification ----

def test_unknown_error_classified_as_unknown():
    result = _make_result(
        error="Unexpected internal error",
        issues=[_issue("runtime.openmc_unknown_error")],
    )
    failures = classify_runtime_tool_results([result])
    assert len(failures) == 1
    f = failures[0]
    assert f.classification is RuntimeFailureClass.UNKNOWN
    assert f.requires_human_confirmation is True


def test_no_issues_with_error_falls_back_to_unknown():
    result = _make_result(error="something broke", issues=[])
    failures = classify_runtime_tool_results([result])
    assert len(failures) == 1
    f = failures[0]
    assert f.primary_issue_code == "runtime.openmc_unknown_error"
    assert f.classification is RuntimeFailureClass.UNKNOWN


# ---- 8. Fingerprint stability ----

def test_fingerprint_is_stable_for_same_normalized_text():
    text = "Overlap detected between cells 10 and 11"
    fp1 = compute_runtime_error_fingerprint(normalize_runtime_error(text))
    fp2 = compute_runtime_error_fingerprint(normalize_runtime_error(text))
    assert fp1 == fp2
    assert fp1.startswith("rt_")


def test_fingerprint_empty_for_empty_text():
    assert compute_runtime_error_fingerprint("") == "rt_empty"


# ---- 9. Absolute path does not affect fingerprint ----

def test_absolute_path_does_not_affect_fingerprint():
    text_a = "Error reading /home/wbd/runs/VERA_3B/geometry.xml at line 42"
    text_b = "Error reading /tmp/opencode/runs/VERA_3B/geometry.xml at line 42"
    fp_a = compute_runtime_error_fingerprint(normalize_runtime_error(text_a))
    fp_b = compute_runtime_error_fingerprint(normalize_runtime_error(text_b))
    assert fp_a == fp_b


def test_timestamp_does_not_affect_fingerprint():
    text_a = "2024-01-15T10:30:00 ERROR overlap cell 10"
    text_b = "2025-06-20T14:00:00 ERROR overlap cell 10"
    fp_a = compute_runtime_error_fingerprint(normalize_runtime_error(text_a))
    fp_b = compute_runtime_error_fingerprint(normalize_runtime_error(text_b))
    assert fp_a == fp_b


def test_pid_does_not_affect_fingerprint():
    text_a = "PID 12345: segmentation fault in cell 10"
    text_b = "PID 99999: segmentation fault in cell 10"
    fp_a = compute_runtime_error_fingerprint(normalize_runtime_error(text_a))
    fp_b = compute_runtime_error_fingerprint(normalize_runtime_error(text_b))
    assert fp_a == fp_b


def test_hex_address_does_not_affect_fingerprint():
    text_a = "Error at 0x7fff12345678 in geometry"
    text_b = "Error at 0x7fffabcdef0000 in geometry"
    fp_a = compute_runtime_error_fingerprint(normalize_runtime_error(text_a))
    fp_b = compute_runtime_error_fingerprint(normalize_runtime_error(text_b))
    assert fp_a == fp_b


# ---- 10. Successful results produce no failures ----

def test_successful_results_produce_no_failures():
    result = ToolResult(name="export_xml", ok=True, returncode=0)
    failures = classify_runtime_tool_results([result])
    assert failures == []


# ---- 11. Multiple failures classified independently ----

def test_multiple_failed_tools_classified_separately():
    r1 = _make_result(
        name="export_xml",
        error="dangling reference",
        issues=[_issue("export_xml.dangling_lattice_universe")],
    )
    r2 = _make_result(
        name="run_smoke_test",
        error="overlap detected",
        issues=[_issue("runtime.geometry_overlap")],
    )
    failures = classify_runtime_tool_results([r1, r2])
    assert len(failures) == 2
    # Export issue doesn't appear in precedence map → unknown
    assert failures[0].tool_name == "export_xml"
    assert failures[1].primary_issue_code == "runtime.geometry_overlap"


# ---- 12. Process crash without source rejection ----

def test_process_crash_without_source_rejection_is_transient():
    result = _make_result(
        error="segmentation fault (core dumped)",
        issues=[_issue("runtime.openmc_process_crash")],
    )
    failures = classify_runtime_tool_results([result])
    assert len(failures) == 1
    f = failures[0]
    assert f.classification is RuntimeFailureClass.TRANSIENT


# ---- 13. RuntimeFailure to_dict round-trip ----

def test_runtime_failure_to_dict_has_all_fields():
    result = _make_result(
        error="overlap detected",
        issues=[_issue("runtime.geometry_overlap")],
    )
    failures = classify_runtime_tool_results([result], plan_hash="abc123")
    d = failures[0].to_dict()
    assert d["primary_issue_code"] == "runtime.geometry_overlap"
    assert d["classification"] == "plan_fixable"
    assert d["plan_hash"] == "abc123"
    assert d["error_fingerprint"].startswith("rt_")
    assert d["stage"] == "execute_tools"
