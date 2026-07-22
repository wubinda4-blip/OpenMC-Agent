#!/usr/bin/env python
"""Offline pre-qualification for Placement, Axial, and Assembled gates."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from openmc_agent.plan_builder.closed_loop.gate_replay import (
    GateReplayMode,
    load_gate_replay_bundle,
    run_gate_replay,
)


GATES = ("placement", "axial_geometry", "assembled_plan")


def qualify(bundle_dir: str | Path) -> dict[str, Any]:
    root = Path(bundle_dir)
    results: list[dict[str, Any]] = []
    for gate in GATES:
        bundle = load_gate_replay_bundle(root / f"{gate}_offline_bundle.json")
        preflight = run_gate_replay(bundle, mode=GateReplayMode.PREFLIGHT)
        recorded = run_gate_replay(bundle, mode=GateReplayMode.RECORDED_REVIEW)
        results.append({
            "gate_id": gate,
            "fixture_fingerprint": bundle.fixture_fingerprint,
            "upstream_chain_provenance": bundle.upstream_chain_provenance,
            "preflight": preflight.to_sanitized_dict(),
            "recorded_review": recorded.to_sanitized_dict(),
            "terminal_status": recorded.terminal_status,
            "coverage": recorded.coverage,
            "blocking_finding_count": recorded.blocking_finding_count,
            "rejected_finding_count": recorded.rejected_finding_count,
        })
    return {"ok": all(item["preflight"]["ok"] and item["recorded_review"]["ok"] for item in results), "mode": "offline_deterministic", "gates": results}


def main() -> int:
    parser = argparse.ArgumentParser(description="Run downstream gate offline preflight and recorded-review qualification.")
    parser.add_argument("--bundle-dir", default="tests/fixtures/gate_replay")
    parser.add_argument("--out", default=None)
    args = parser.parse_args()
    payload = qualify(args.bundle_dir)
    text = json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True)
    if args.out:
        path = Path(args.out)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    else:
        print(text)
    return 0 if payload["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
