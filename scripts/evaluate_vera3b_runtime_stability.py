"""Prepare an explicit-confirmation-gated real VERA3B stability campaign."""

from __future__ import annotations

import argparse
from pathlib import Path

from openmc_agent.runtime_campaign import RuntimeCampaignConfig, prepare_real_campaign


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--profile", choices=("pilot", "qualification", "extended"), default="qualification")
    parser.add_argument("--runs", type=int)
    parser.add_argument("--model", default="deepseek:deepseek-chat")
    parser.add_argument("--temperature", type=float, default=0.0)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--confirm-real-campaign", action="store_true")
    parser.add_argument("--resume", action="store_true")
    args = parser.parse_args()
    default_runs = {"pilot": 3, "qualification": 10, "extended": 30}[args.profile]
    manifest = prepare_real_campaign(
        RuntimeCampaignConfig(output_dir=args.output_dir, model=args.model, temperature=args.temperature, client="real", supervisor="real", diagnostician="real", proposer="real"),
        profile=args.profile, runs=args.runs or default_runs,
        confirm_real_campaign=args.confirm_real_campaign,
    )
    print(manifest["aggregate_status"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
