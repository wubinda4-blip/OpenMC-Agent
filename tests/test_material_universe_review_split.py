from __future__ import annotations

import json
from types import SimpleNamespace

from openmc_agent.plan_builder.closed_loop.material_universe_review_split import run_material_universe_review_split
from openmc_agent.plan_builder.closed_loop.models import PlanClosedLoopPolicy
from openmc_agent.plan_builder.state import PlanBuildState


class _Call:
    def __init__(self, payload):
        self.payload = payload

    def __call__(self, prompt, **kwargs):
        return self.payload


def _pack():
    record = lambda **kw: SimpleNamespace(**kw, model_dump=lambda mode="json": kw)
    view = SimpleNamespace(
        material_records=[record(material_id="m1")], universe_records=[record(universe_id="u1")],
        cell_material_bindings=[record(binding_id="u1:c1")],
    )
    row = lambda kind: SimpleNamespace(row_id=kind, row_kind=kind, model_dump=lambda mode="json": {"row_kind": kind, "row_id": kind})
    return SimpleNamespace(
        input_hash="pack-hash", binding_view=view,
        contract_matrix=SimpleNamespace(rows=[row("source_material_coverage"), row("required_universe_material_structure"), row("material_to_cell_binding")]),
        deterministic_issues=[], evidence_items=[SimpleNamespace(ref_id="M001")],
    )


def test_split_review_requires_three_scopes_and_returns_complete(monkeypatch):
    outputs = iter([
        {"review_status": "complete", "findings": [], "reviewed_ids": ["m1"], "reviewed_evidence_refs": ["M001"]},
        {"review_status": "complete", "findings": [], "reviewed_ids": ["u1"], "reviewed_evidence_refs": ["M001"]},
        {"review_status": "complete", "findings": [], "reviewed_ids": ["material_to_cell_binding"], "reviewed_evidence_refs": ["M001"]},
    ])

    def client(prompt: str):
        return json.dumps(next(outputs))

    state = PlanBuildState(state_id="split", requirement_text="r")
    result = run_material_universe_review_split(
        evidence_pack=_pack(), reviewer_client=client, state=state,
        policy=PlanClosedLoopPolicy(mode="advisory"),
    )
    assert result.ok
    assert result.coverage_complete
    assert result.reviewer_calls == 3
    assert [item["scope"] for item in result.outputs] == ["materials", "universes", "binding"]
