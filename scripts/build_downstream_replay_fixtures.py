#!/usr/bin/env python
"""Build sanitized offline replay fixtures for the three downstream gates.

The fixtures use deterministic test plans and an explicitly synthetic accepted
upstream chain.  They are qualification inputs only and never represent a
real MU or VERA4 provider acceptance.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from openmc_agent.plan_builder.closed_loop.assembled_plan_evidence import assembled_plan_gate_input_hash
from openmc_agent.plan_builder.closed_loop.axial_geometry_evidence import axial_geometry_gate_input_hash
from openmc_agent.plan_builder.closed_loop.gate_replay import GateReplayBundle
from openmc_agent.plan_builder.closed_loop.models import PlanClosedLoopPolicy
from openmc_agent.plan_builder.closed_loop.placement_evidence import placement_gate_input_hash
from openmc_agent.plan_builder.closed_loop.state_snapshot import sanitize_plan_build_state
from openmc_agent.structured_output import canonical_payload_hash


def _placement_state():
    from tests.test_placement_phase2_protocol import _state

    return _state()


def _axial_state():
    from tests._axial_geometry_fixtures import state_with_axial_patches

    return state_with_axial_patches()


def _assembled_state():
    from tests._assembled_plan_fixtures import make_assembled_plan, state_with_assembled_plan

    # The shared fixture's extra unused materials intentionally tests a
    # reachability warning.  Keep the offline clean fixture deterministic and
    # free of that unrelated blocker.
    plan = make_assembled_plan()
    model = plan.complex_model.model_copy(update={"materials": [plan.complex_model.materials[0]]})
    return state_with_assembled_plan(plan=plan.model_copy(update={"complex_model": model}))


def _review(gate: str) -> dict:
    return {
        "review_status": "complete",
        "findings": [],
        "reviewed_evidence_refs": [],
        "reviewed_contract_row_ids": [],
        "coverage_summary": {},
        "metadata": {"fixture_mode": "offline_deterministic", "gate": gate},
    }


def build_bundles() -> dict[str, GateReplayBundle]:
    placement = _placement_state()
    axial = _axial_state()
    assembled = _assembled_state()
    common = {"facts": True, "material_universe": True}
    bundles = {
        "placement": GateReplayBundle.create(
            gate_id="placement",
            state=placement,
            policy=PlanClosedLoopPolicy(mode="controlled", placement_review_mode="controlled"),
            upstream_accepted={**common},
            canonical_hashes={"input": placement_gate_input_hash(placement), "policy": canonical_payload_hash(PlanClosedLoopPolicy(mode="controlled", placement_review_mode="controlled").model_dump(mode="json"))},
            recorded_reviews=[_review("placement")],
            upstream_chain_provenance="offline_deterministic",
        ),
        "axial_geometry": GateReplayBundle.create(
            gate_id="axial_geometry",
            state=axial,
            policy=PlanClosedLoopPolicy(mode="controlled", axial_geometry_review_mode="controlled"),
            upstream_accepted={**common, "placement": True},
            canonical_hashes={"input": axial_geometry_gate_input_hash(axial, policy=PlanClosedLoopPolicy(mode="controlled")), "policy": canonical_payload_hash(PlanClosedLoopPolicy(mode="controlled", axial_geometry_review_mode="controlled").model_dump(mode="json"))},
            recorded_reviews=[_review("axial_geometry")],
            upstream_chain_provenance="offline_deterministic",
        ),
        "assembled_plan": GateReplayBundle.create(
            gate_id="assembled_plan",
            state=assembled,
            policy=PlanClosedLoopPolicy(mode="controlled", assembled_plan_review_mode="controlled"),
            upstream_accepted={**common, "placement": True, "axial_geometry": True},
            canonical_hashes={"input": assembled_plan_gate_input_hash(assembled, policy=PlanClosedLoopPolicy(mode="controlled")), "policy": canonical_payload_hash(PlanClosedLoopPolicy(mode="controlled", assembled_plan_review_mode="controlled").model_dump(mode="json"))},
            recorded_reviews=[_review("assembled_plan")],
            upstream_chain_provenance="offline_deterministic",
        ),
    }
    return bundles


def main() -> int:
    parser = argparse.ArgumentParser(description="Build sanitized downstream offline replay fixtures.")
    parser.add_argument("--out-dir", default="tests/fixtures/gate_replay")
    args = parser.parse_args()
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    for gate, bundle in build_bundles().items():
        path = out_dir / f"{gate}_offline_bundle.json"
        path.write_text(json.dumps(bundle.model_dump(mode="json"), indent=2, sort_keys=True), encoding="utf-8")
        print(f"wrote {path} fingerprint={bundle.fixture_fingerprint}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
