"""R7/R8 fault contract schema and injection tests."""

from __future__ import annotations

import pytest
from pathlib import Path

from openmc_agent.runtime_faults import (
    FaultExpectedDisposition,
    FaultInjectionCase,
    FaultInjectionLayer,
    default_fault_matrix,
    fault_case_by_name,
    load_vera3b_accepted_state,
    state_hash,
)


def test_fault_matrix_has_20_cases():
    cases = default_fault_matrix()
    assert len(cases) == 20


def test_all_case_ids_unique():
    cases = default_fault_matrix()
    ids = [c.case_id for c in cases]
    assert len(ids) == len(set(ids))


def test_fault_case_lookup_by_short_name():
    case = fault_case_by_name("F00")
    assert case.case_id == "F00_baseline_no_fault"


@pytest.mark.parametrize("case", default_fault_matrix())
def test_each_case_has_required_fields(case: FaultInjectionCase):
    assert case.case_id
    assert case.title
    assert case.injection_layer in FaultInjectionLayer
    assert case.expected_final_disposition in FaultExpectedDisposition
    assert case.expected_max_iterations > 0


def test_source_patch_injection_modifies_settings():
    case = fault_case_by_name("F01")
    state = load_vera3b_accepted_state()
    before = state_hash(state)
    injected = case.inject(state.model_copy(deep=True))
    after = state_hash(injected)
    assert before != after


def test_baseline_no_fault_injection_is_noop():
    case = fault_case_by_name("F00")
    state = load_vera3b_accepted_state()
    injected = case.inject(state.model_copy(deep=True))
    assert state_hash(state) == state_hash(injected)


def test_forbidden_paths_include_density_and_composition():
    cases = default_fault_matrix()
    for case in cases:
        if case.expected_repair_channel not in ("deterministic", "retry"):
            continue
        assert "/density" in case.forbidden_changed_paths
        assert "/composition" in case.forbidden_changed_paths


def test_cleanup_removes_statepoints(tmp_path: Path):
    case = fault_case_by_name("F00")
    (tmp_path / "statepoint.1.h5").write_bytes(b"fake")
    case.cleanup(tmp_path)
    assert not (tmp_path / "statepoint.1.h5").exists()
