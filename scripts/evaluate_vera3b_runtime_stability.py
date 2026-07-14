"""VERA3B real-LLM runtime stability campaign CLI.

Lane B: real LLM generation through the full production graph.
Requires DEEPSEEK_API_KEY and OPENMC_CROSS_SECTIONS.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run real VERA3B LLM stability campaign (Lane B)."
    )
    parser.add_argument("--profile", choices=("pilot", "qualification", "extended"), default="pilot")
    parser.add_argument("--runs", type=int)
    parser.add_argument("--model", default="deepseek:deepseek-chat")
    parser.add_argument("--temperature", type=float, default=0.0)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--confirm-real-campaign", action="store_true")
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--runtime-supervisor", choices=("deterministic", "real"), default="deterministic")
    parser.add_argument("--max-runtime-iterations", type=int, default=4)
    parser.add_argument("--max-llm-calls", type=int, default=16)
    parser.add_argument("--run-timeout-seconds", type=float, default=900.0)
    parser.add_argument("--campaign-timeout-seconds", type=float, default=14400.0)
    parser.add_argument("--fail-fast", action="store_true")
    parser.add_argument("--verbose", action="store_true")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--full-acceptance", action="store_true",
                        help="Use full VERA3B acceptance check (auto-forced for qualification)")
    args = parser.parse_args()

    default_runs = {"pilot": 3, "qualification": 10, "extended": 30}[args.profile]
    runs = args.runs or default_runs

    # Full acceptance is mandatory for qualification profile.
    require_full_acceptance = args.profile == "qualification"
    use_full_acceptance = args.full_acceptance or require_full_acceptance

    vera3_callback = None
    if use_full_acceptance:
        try:
            from openmc_agent.campaign_eval.vera3_campaign_acceptance import (
                make_vera3b_acceptance_callback,
                FullAcceptanceLoadError,
            )
            vera3_callback = make_vera3b_acceptance_callback()
        except FullAcceptanceLoadError as exc:
            if require_full_acceptance:
                print(f"ERROR: Full acceptance is mandatory for qualification but failed to load: {exc}",
                      file=sys.stderr)
                return 2
        except Exception as exc:
            if require_full_acceptance:
                print(f"ERROR: Full acceptance load error: {exc}", file=sys.stderr)
                return 2

    if vera3_callback is None and not use_full_acceptance:
        def vera3_callback(plan):
            if plan is None:
                return False, ["no_plan"]
            from openmc_agent.schemas import SimulationPlan
            if not isinstance(plan, SimulationPlan):
                plan = SimulationPlan.model_validate(plan)
            model = plan.complex_model
            if model is None:
                return False, ["no_complex_model"]
            codes: list[str] = []
            for lat in model.lattices:
                if lat.kind == "rect" and lat.universe_pattern:
                    rows = len(lat.universe_pattern)
                    cols = len(lat.universe_pattern[0])
                    if rows != 17 or cols != 17:
                        codes.append(f"lattice_{rows}x{cols}_not_17x17")
            from openmc_agent.source_settings import fuel_material_ids
            fuel = fuel_material_ids(model)
            if not fuel:
                codes.append("no_fuel_material")
            return (len(codes) == 0), codes

    from openmc_agent.real_campaign import run_real_campaign

    manifest = run_real_campaign(
        args.output_dir,
        profile=args.profile,
        runs=runs,
        model=args.model,
        temperature=args.temperature,
        confirm_real_campaign=args.confirm_real_campaign,
        runtime_supervisor_mode=args.runtime_supervisor,
        max_runtime_iterations=args.max_runtime_iterations,
        max_llm_calls=args.max_llm_calls,
        run_timeout_s=args.run_timeout_seconds,
        campaign_timeout_s=args.campaign_timeout_seconds,
        fail_fast=args.fail_fast,
        resume=args.resume,
        vera3_acceptance_callback=vera3_callback,
    )

    status = manifest.get("aggregate_status", "UNKNOWN")

    if args.json:
        import json
        print(json.dumps(manifest, indent=2, default=str))
    else:
        print(f"Status: {status}")
        print(f"Completed: {manifest.get('completed_runs', 0)}/{manifest.get('requested_runs', 0)}")
        print(f"Successful: {manifest.get('successful_runs', 0)}")
        if manifest.get("resume_mismatches"):
            print(f"Resume mismatches: {manifest['resume_mismatches']}")

    # Exit codes.
    if "NOT_RUN_ENV" in status or "CONFIRMATION_REQUIRED" in status:
        return 2
    if "CONFIG_MISMATCH" in status:
        return 2
    if "PILOT_PASSED" in status or "STABILITY_ACCEPTED" in status:
        return 0
    if "PILOT_FAILED" in status or "STABILITY_FAILED" in status:
        return 1
    if "PENDING" in status or "TIMEOUT" in status:
        return 1
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
