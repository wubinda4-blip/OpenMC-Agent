"""Transport seed stability CLI for VERA3B qualification.

Usage:
    python scripts/evaluate_transport_seed_stability.py \
        --campaign-dir data/evals/vera3b_truth3_qualification \
        --output-dir data/evals/vera3b_truth3_qualification/transport_seed_stability
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run transport seed stability check on a qualification run."
    )
    parser.add_argument("--campaign-dir", type=Path, required=True,
                        help="Directory containing campaign_results.json and runs/")
    parser.add_argument("--output-dir", type=Path, required=True,
                        help="Where to write seed stability artifacts")
    parser.add_argument("--seeds", type=int, nargs="+", default=[10101, 20202, 30303])
    parser.add_argument("--batches", type=int, default=20)
    parser.add_argument("--particles", type=int, default=10000)
    args = parser.parse_args()

    # Load campaign results.
    results_path = args.campaign_dir / "campaign_results.json"
    if not results_path.exists():
        print(f"ERROR: {results_path} not found", file=sys.stderr)
        return 2

    results = json.loads(results_path.read_text())
    runs_dir = args.campaign_dir / "runs"

    from openmc_agent.transport_seed_stability import (
        evaluate_transport_seed_stability,
    )

    result = evaluate_transport_seed_stability(
        results,
        runs_dir,
        args.output_dir,
        seeds=args.seeds,
        batches=args.batches,
        particles=args.particles,
    )

    print(f"Status: {result.status}")
    print(f"Selected run: {result.selected_run_id}")
    print(f"Mean keff: {result.mean_keff:.5f}")
    print(f"Max pairwise z: {result.max_pairwise_z:.2f}")

    if "PASSED" in result.status:
        return 0
    if "ERROR" in result.status:
        return 2
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
