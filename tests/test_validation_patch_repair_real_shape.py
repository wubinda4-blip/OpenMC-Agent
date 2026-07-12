from __future__ import annotations

import json
from pathlib import Path

from openmc_agent.plan_builder.validation_repair import (
    evaluate_patch_repair_proposal,
    normalize_patch_repair_model_output,
)
from tests.test_validation_patch_repair_evaluation import _broken_state, _request


def test_deepseek_missing_metadata_shape_enters_clone_evaluation() -> None:
    state, report = _broken_state()
    # The live DeepSeek request contained this explicit optional key; retain
    # that real request shape rather than changing the observed operation.
    state.patches["pin_map"].content["water_cell_coords"] = []
    request = _request(state, report)
    raw = json.loads((
        Path(__file__).parent / "fixtures/regressions/deepseek_patch_repair_missing_metadata.json"
    ).read_text())
    normalized = normalize_patch_repair_model_output(raw, request=request)
    assert normalized.ok and normalized.proposal is not None

    evaluation = evaluate_patch_repair_proposal(
        state=state, request=request, proposal=normalized.proposal,
        requirement=state.requirement_text,
    )
    assert evaluation.candidate_hash is not None
    assert evaluation.repaired_patch is not None
    assert evaluation.repaired_plan is not None
    assert evaluation.validation_report_after is not None
