from __future__ import annotations

import json
from pathlib import Path

import pytest

from openmc_agent.plan_builder.validation_repair import (
    PatchRepairRequest,
    normalize_patch_repair_model_output,
)


def _request() -> PatchRepairRequest:
    return PatchRepairRequest(
        repair_id="ppr_request",
        issue_fingerprint="fingerprint",
        target_patch_type="pin_map",
        issues=[{"code": "lattice.pin_count_mismatch"}],
        previous_patch_content={"default_universe_id": "fuel_pin_endplug"},
        previous_patch_hash="hash",
        relevant_plan_fragment={},
        valid_upstream_patch_summaries={},
        allowed_path_patterns=["/default_universe_id"],
        forbidden_path_patterns=[],
    )


def test_complete_proposal_normalizes_without_defaults() -> None:
    result = normalize_patch_repair_model_output({
        "repair_id": "ppr_request", "target_patch_type": "pin_map",
        "operations": [{"op": "replace", "path": "/default_universe_id", "value": "fuel_pin"}],
        "rationale": "Fix base universe.", "confidence": 0.5,
    }, request=_request())
    assert result.ok and result.proposal is not None
    assert result.proposal.confidence == 0.5
    assert result.warnings == []


def test_missing_system_and_advisory_fields_are_deterministically_bound() -> None:
    result = normalize_patch_repair_model_output({
        "operations": [{"op": "replace", "path": "/default_universe_id", "value": "fuel_pin"}],
    }, request=_request())
    assert result.ok and result.proposal is not None
    assert result.proposal.repair_id == "ppr_request"
    assert result.proposal.target_patch_type == "pin_map"
    assert result.proposal.rationale == "Model did not provide a rationale."
    assert result.proposal.confidence == 0.0
    assert result.warnings == [
        "repair_proposal.rationale_defaulted",
        "repair_proposal.confidence_defaulted",
    ]


@pytest.mark.parametrize("field,value", [("repair_id", "other"), ("target_patch_type", "materials")])
def test_conflicting_system_fields_are_rejected(field: str, value: str) -> None:
    payload = {
        "operations": [{"op": "replace", "path": "/default_universe_id", "value": "fuel_pin"}],
        field: value,
    }
    result = normalize_patch_repair_model_output(payload, request=_request())
    assert not result.ok
    assert "conflicts" in result.errors[0]


@pytest.mark.parametrize("payload", [
    {},
    {"operations": [{"op": "replace", "value": "fuel_pin"}]},
    {"operations": [{"op": "move", "path": "/default_universe_id", "value": "fuel_pin"}]},
    {"operations": [{"op": "replace", "path": "/default_universe_id"}]},
])
def test_missing_or_malformed_operations_are_rejected(payload: dict[str, object]) -> None:
    assert not normalize_patch_repair_model_output(payload, request=_request()).ok


@pytest.mark.parametrize("confidence", [85, "high"])
def test_invalid_confidence_is_rejected(confidence: object) -> None:
    result = normalize_patch_repair_model_output({
        "operations": [{"op": "replace", "path": "/default_universe_id", "value": "fuel_pin"}],
        "confidence": confidence,
    }, request=_request())
    assert not result.ok


def test_real_deepseek_missing_metadata_fixture_normalizes() -> None:
    path = Path(__file__).parent / "fixtures/regressions/deepseek_patch_repair_missing_metadata.json"
    result = normalize_patch_repair_model_output(json.loads(path.read_text()), request=_request())
    assert result.ok and result.proposal is not None
    assert result.proposal.repair_id == "ppr_request"
    assert result.proposal.target_patch_type == "pin_map"
    assert result.proposal.confidence == 0.0
