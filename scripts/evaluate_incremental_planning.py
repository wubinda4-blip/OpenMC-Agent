#!/usr/bin/env python
"""CLI for incremental planning evaluation (Phase 7).

Runs the full incremental pipeline with a real LLM and produces a structured
evaluation report.  CI does NOT call this — it is opt-in only.

Usage::

    python scripts/evaluate_incremental_planning.py \\
        --benchmark VERA3 --variant 3B \\
        --input Input/VERA3_problem.md \\
        --model zhipu:glm-5.2 \\
        --max-patch-attempts 2 \\
        --out data/evals/incremental/VERA3_3B

    # Dry-run (no LLM call, just shows planned patch order):
    python scripts/evaluate_incremental_planning.py --dry-run \\
        --benchmark VERA3 --variant 3B
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Evaluate incremental planning with a real LLM",
    )
    parser.add_argument("--benchmark", default="VERA3", help="Benchmark ID")
    parser.add_argument("--variant", default="3B", help="Variant (3A/3B)")
    parser.add_argument("--input", help="Path to benchmark requirement file")
    parser.add_argument("--model", default=None, help="LLM model (provider:name)")
    parser.add_argument(
        "--max-patch-attempts", type=int, default=2,
        help="Max retry attempts per patch",
    )
    parser.add_argument("--out", default=None, help="Output directory")
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Print planned patch order without calling LLM",
    )
    args = parser.parse_args()

    # Load requirement text.
    requirement = ""
    if args.input:
        requirement = Path(args.input).read_text(encoding="utf-8")
    else:
        requirement = (
            f"{args.benchmark} {args.variant} benchmark: 3D assembly with "
            "axial layers, spacer grids, special pin map"
        )

    # Mode decision.
    from openmc_agent.plan_builder import should_use_incremental_planning
    from openmc_agent.plan_builder.executor import default_patch_task_order
    from openmc_agent.plan_builder.state import initialize_plan_build_state

    decision = should_use_incremental_planning(requirement)
    print(f"Planning mode: {decision.mode}")
    print(f"Triggers: {decision.triggers}")

    state = initialize_plan_build_state(
        requirement=requirement,
        decision=decision,
        benchmark_id=args.benchmark,
        selected_variant=args.variant,
    )

    order = default_patch_task_order(state)
    print(f"Patch order: {order}")

    if args.dry_run:
        print("\n[Dry-run] No LLM call. Planned patch types:")
        for pt in order:
            print(f"  - {pt}")
        return 0

    # Real LLM evaluation.
    if args.model is None:
        print("Error: --model is required for real evaluation (or use --dry-run)")
        return 1

    from openmc_agent.plan_builder.llm_adapter import make_patch_llm_client
    from openmc_agent.plan_builder.evaluation import run_incremental_evaluation

    print(f"\nCreating patch LLM client for model={args.model}...")
    llm_client = make_patch_llm_client(model_name=args.model)

    output_dir = args.out or f"data/evals/incremental/{args.benchmark}_{args.variant}"

    print(f"Running incremental evaluation (output: {output_dir})...")
    report, build_state = run_incremental_evaluation(
        requirement=requirement,
        benchmark_id=args.benchmark,
        selected_variant=args.variant,
        llm_client=llm_client,
        model=args.model,
        max_patch_attempts=args.max_patch_attempts,
        output_dir=output_dir,
    )

    # Print summary.
    print(f"\n{'='*60}")
    print(f"Evaluation result: ok={report.ok}")
    print(f"Planning mode: {report.planning_mode}")
    print(f"No monolithic plan requested: {report.no_monolithic_plan_requested}")
    print(f"\nPatch metrics:")
    for pt, metric in report.patch_metrics.items():
        status = "valid" if metric.validation_ok else "INVALID"
        print(f"  {pt}: {status} (attempts={metric.attempts}, raw_chars={metric.raw_chars})")
        if metric.issue_codes:
            print(f"    issues: {metric.issue_codes}")
    print(f"\nAssembly: ok={report.assembly.ok}")
    if report.assembly.ok:
        print(f"  lattice: {report.assembly.lattice_size}")
        print(f"  axial_layers: {report.assembly.axial_layer_count}")
        print(f"  overlays: {report.assembly.overlay_count}")
        print(f"  pyrex: {report.assembly.pyrex_count}")
        print(f"  plugs: {report.assembly.thimble_plug_count}")
    print(f"\nGuard: blocking={report.guard.blocking_issue_count}")
    if report.error:
        print(f"Error: {report.error}")
    print(f"{'='*60}")

    return 0 if report.ok else 1


if __name__ == "__main__":
    sys.exit(main())
