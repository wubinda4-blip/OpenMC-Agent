#!/usr/bin/env python
"""Extract downstream GateReplayBundle JSONs from a real campaign checkpoint.

Reads ``campaign_checkpoint.json`` and produces one ``{gate}_live_bundle.json``
for each downstream gate whose accepted boundary snapshot exists.

Usage::

    python scripts/extract_downstream_replay_bundles.py \\
        --checkpoint data/runs/<run>/runs/run_001/campaign_checkpoint.json \\
        --out-dir data/runs/<run>/replay_bundles

The output bundles use ``upstream_chain_provenance="production_accepted"``
and carry no ``recorded_reviews`` (live-review does not need them).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from openmc_agent.plan_builder.closed_loop.campaign_checkpoint import (
    ACCEPTED_BOUNDARIES,
    CampaignCheckpointStore,
)
from openmc_agent.plan_builder.closed_loop.gate_replay import GateReplayBundle
from openmc_agent.plan_builder.closed_loop.models import PlanClosedLoopPolicy
from openmc_agent.plan_builder.state import PlanBuildState
from openmc_agent.structured_output import canonical_payload_hash

GATE_CONFIG = {
    "placement": {
        "boundary": "gate:placement",
        "review_mode": "placement_review_mode",
        "upstream": {"facts": True, "material_universe": True},
    },
    "axial_geometry": {
        "boundary": "gate:axial_geometry",
        "review_mode": "axial_geometry_review_mode",
        "upstream": {"facts": True, "material_universe": True, "placement": True},
    },
    "assembled_plan": {
        "boundary": "gate:assembled_plan",
        "review_mode": "assembled_plan_review_mode",
        "upstream": {"facts": True, "material_universe": True, "placement": True, "axial_geometry": True},
    },
}


def _input_hash_fn(gate_id: str):
    if gate_id == "placement":
        from openmc_agent.plan_builder.closed_loop.placement_evidence import placement_gate_input_hash
        return lambda state, policy: placement_gate_input_hash(state)
    if gate_id == "axial_geometry":
        from openmc_agent.plan_builder.closed_loop.axial_geometry_evidence import axial_geometry_gate_input_hash
        return lambda state, policy: axial_geometry_gate_input_hash(state, policy=policy)
    from openmc_agent.plan_builder.closed_loop.assembled_plan_evidence import assembled_plan_gate_input_hash
    return lambda state, policy: assembled_plan_gate_input_hash(state, policy=policy)


def extract_bundles(checkpoint_path: str | Path, out_dir: str | Path | None = None) -> dict[str, GateReplayBundle]:
    """Extract downstream bundles from a campaign checkpoint store."""
    store = CampaignCheckpointStore(Path(checkpoint_path))
    snapshots = {snap.boundary: snap for snap in store.state_snapshots()}
    bundles: dict[str, GateReplayBundle] = {}
    for gate_id, config in GATE_CONFIG.items():
        snap = snapshots.get(config["boundary"])
        if snap is None:
            print(f"skip {gate_id}: no snapshot at boundary {config['boundary']}", file=sys.stderr)
            continue
        state = PlanBuildState.model_validate(snap.plan_build_state)
        policy = PlanClosedLoopPolicy(mode="controlled", **{config["review_mode"]: "controlled"})
        input_hash_fn = _input_hash_fn(gate_id)
        bundle = GateReplayBundle.create(
            gate_id=gate_id,
            state=state,
            policy=policy,
            upstream_accepted=config["upstream"],
            canonical_hashes={
                "input": input_hash_fn(state, policy),
                "policy": canonical_payload_hash(policy.model_dump(mode="json")),
            },
            campaign_id=snap.campaign_id,
            upstream_chain_provenance="production_accepted",
        )
        bundles[gate_id] = bundle
        if out_dir is not None:
            out_path = Path(out_dir) / f"{gate_id}_live_bundle.json"
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_text(json.dumps(bundle.model_dump(mode="json"), indent=2, sort_keys=True), encoding="utf-8")
            print(f"wrote {out_path}", file=sys.stderr)
    return bundles


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Extract downstream replay bundles from a campaign checkpoint.")
    parser.add_argument("--checkpoint", required=True, help="Path to campaign_checkpoint.json")
    parser.add_argument("--out-dir", default=None, help="Output directory for bundle JSONs")
    args = parser.parse_args(argv)
    bundles = extract_bundles(args.checkpoint, args.out_dir)
    if not bundles:
        print("no downstream boundary snapshots found", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
