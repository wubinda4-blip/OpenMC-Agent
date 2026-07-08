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
import json
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
        "--patch-output-mode", default="auto",
        choices=["auto", "plain_prompt", "json_object", "json_schema", "tool_call"],
        help="How to request JSON output from the LLM provider",
    )
    parser.add_argument(
        "--allow-monolithic-fallback", action="store_true", default=False,
        help="Allow monolithic fallback if incremental fails (default: False)",
    )
    parser.add_argument(
        "--reference-patch-policy", default="off",
        choices=["off", "prefer_reference_for_structural", "fallback_after_llm_failure", "reference_only_for_structural"],
        help="When to use benchmark reference patches for structural patches",
    )
    parser.add_argument(
        "--reference-path", default=None,
        help="Explicit path to benchmark reference file",
    )
    parser.add_argument(
        "--resume-from", default=None,
        help="Resume from saved incremental directory (e.g. data/runs/VERA3/3B/incremental)",
    )
    parser.add_argument(
        "--start-at-patch", default=None,
        help="Start execution at this patch type (for resume)",
    )
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

    # Resume from saved state if requested.
    if args.resume_from:
        from openmc_agent.plan_builder.state import load_plan_build_state
        resume_dir = Path(args.resume_from)
        state_path = resume_dir / "plan_build_state.json"
        if state_path.is_file():
            print(f"Resuming from {state_path}")
            state = load_plan_build_state(state_path)
        else:
            print(f"Warning: {state_path} not found, starting fresh")
            state = initialize_plan_build_state(
                requirement=requirement, decision=decision,
                benchmark_id=args.benchmark, selected_variant=args.variant,
            )
    else:
        state = initialize_plan_build_state(
            requirement=requirement, decision=decision,
            benchmark_id=args.benchmark, selected_variant=args.variant,
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
    llm_client = make_patch_llm_client(model_name=args.model, output_mode=args.patch_output_mode)

    if args.reference_patch_policy != "off":
        print(f"Reference patch policy: {args.reference_patch_policy}")

    output_dir = args.out or f"data/evals/incremental/{args.benchmark}_{args.variant}"

    print(f"Running incremental evaluation (output: {output_dir})...")

    # Use executor directly for reference patch policy support.
    from openmc_agent.plan_builder.executor import run_incremental_planning

    exec_result = run_incremental_planning(
        requirement=requirement,
        state=state,
        llm_client=llm_client,
        max_patch_attempts=args.max_patch_attempts,
        reference_patch_policy=args.reference_patch_policy,
        reference_path=args.reference_path,
    )

    # Write output.
    out_path = Path(output_dir)
    out_path.mkdir(parents=True, exist_ok=True)
    if exec_result.assembled_plan:
        (out_path / "assembled_plan.json").write_text(
            json.dumps(exec_result.assembled_plan, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    (out_path / "plan_build_state.json").write_text(
        json.dumps(exec_result.state.model_dump(mode="json"), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    report_ok = exec_result.ok
    report_summary = exec_result.summary

    # Print summary.
    print(f"\n{'='*60}")
    print(f"Evaluation result: ok={report_ok}")
    print(f"Reference policy: {args.reference_patch_policy}")
    if not report_ok:
        failed = report_summary.get("failed_patch_type", "unknown")
        valid_types = report_summary.get("valid_patch_types", [])
        issue_codes = report_summary.get("issue_codes", [])
        print(f"Failed patch: {failed}")
        print(f"Valid patches: {', '.join(valid_types) if valid_types else '(none)'}")
        print(f"Issue codes: {issue_codes}")
        print(f"Monolithic fallback: {report_summary.get('monolithic_fallback_attempted', False)}")
        if report_summary.get("reference_patches_used"):
            print(f"Reference patches used: {report_summary['reference_patches_used']}")
    else:
        valid_types = sorted({
            e.patch_type for e in exec_result.state.patches.values()
            if e.status == "valid"
        })
        print(f"Valid patches: {', '.join(valid_types)}")
        ref_used = report_summary.get("reference_patches_used", [])
        if ref_used:
            print(f"Reference patches used: {ref_used}")
        if report_summary.get("actual_pin_counts"):
            print(f"Actual pin counts: {report_summary['actual_pin_counts']}")
        if report_summary.get("material_aliases_applied"):
            print(f"Material aliases applied: {report_summary['material_aliases_applied']}")
    print(f"Output: {output_dir}")
    print(f"{'='*60}")

    return 0 if report_ok else 1


if __name__ == "__main__":
    sys.exit(main())
