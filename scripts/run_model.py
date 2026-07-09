#!/usr/bin/env python3
"""Run the OpenMC-Agent modeling pipeline on a single input file.

This is the simplest entry point for real LLM modeling:
resolves the input .md file, runs the incremental plan builder,
and saves all artifacts (plan, model.py, traces, material report).

Usage examples:

    # Fake model (no LLM call, no OpenMC) — quick smoke
    python scripts/run_model.py --input Input/VERA3_problem.md --model fake

    # Real LLM (DeepSeek)
    python scripts/run_model.py \\
        --input Input/VERA3_problem.md \\
        --model deepseek:deepseek-chat \\
        --allow-real-llm

    # Real LLM with OpenMC smoke test
    python scripts/run_model.py \\
        --input Input/VERA3_problem.md \\
        --model deepseek:deepseek-chat \\
        --allow-real-llm \\
        --smoke-test

    # Switch variant / benchmark
    python scripts/run_model.py \\
        --input Input/VERA3_problem.md --variant 3B \\
        --model deepseek:deepseek-chat --allow-real-llm

Via Makefile (recommended):

    make model INPUT=Input/VERA3_problem.md
    make model INPUT=Input/VERA3_problem.md VARIANT=3B
    make model INPUT=Input/VERA3_problem.md MODEL=glm:glm-4-plus ALLOW_REAL_LLM=1
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


def _openmc_available() -> bool:
    try:
        import openmc  # noqa: F401
    except Exception:
        return False
    return True


def _read_keff(statepoint_h5: Path) -> tuple[float, float] | None:
    try:
        import h5py
    except Exception:
        return None
    try:
        with h5py.File(statepoint_h5, "r") as f:
            kc = f["k_combined"][()]
            return float(kc[0]), float(kc[1])
    except Exception:
        return None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--input", required=True,
                        help="Path to input problem description (.md/.txt/.json)")
    parser.add_argument("--model", default="fake",
                        help="LLM model (e.g. fake, deepseek:deepseek-chat, glm:glm-4-plus)")
    parser.add_argument("--benchmark", default=None,
                        help="Benchmark family override (e.g. VERA3). Auto-detected from filename if omitted.")
    parser.add_argument("--variant", default=None,
                        help="Benchmark variant (e.g. 3A, 3B). Auto-detected from filename if omitted.")
    parser.add_argument("--out", default=None,
                        help="Output directory. Defaults to data/runs/<benchmark>_<variant>/")
    parser.add_argument("--reference-patch-policy", default="off",
                        help="Reference patch policy (off / reference_only_for_structural / fallback_after_llm_failure)")
    parser.add_argument("--material-policy", default="apply_alloy_library",
                        help="Material composition policy (preserve_plan / apply_alloy_library / strict_confirmed_only)")
    parser.add_argument("--allow-real-llm", action="store_true",
                        help="Explicitly allow non-fake LLM model names")
    parser.add_argument("--smoke-test", action="store_true",
                        help="Run OpenMC smoke test after rendering (requires OpenMC)")
    parser.add_argument("--no-incremental", action="store_true",
                        help="Use monolithic planner instead of incremental executor")
    parser.add_argument("--dry-run", action="store_true",
                        help="Resolve requirement and show feature detection without calling LLM or OpenMC")
    args = parser.parse_args(argv)

    input_path = Path(args.input)
    if not input_path.exists():
        print(f"ERROR: input file not found: {input_path}", file=sys.stderr)
        return 2

    if args.model != "fake" and not args.allow_real_llm:
        print("ERROR: non-fake model requires --allow-real-llm", file=sys.stderr)
        return 2

    # Auto-detect benchmark / variant from filename if not specified.
    benchmark = args.benchmark or _detect_benchmark(input_path)
    variant = args.variant or _detect_variant(input_path)

    out_dir = Path(args.out) if args.out else Path("data/runs") / f"{benchmark}_{variant}"
    out_dir.mkdir(parents=True, exist_ok=True)

    requirement_text = (
        f"Build the {benchmark} {variant} benchmark model "
        f"described in {input_path}."
    )

    print("=" * 70)
    print("OpenMC-Agent Model Run")
    print("=" * 70)
    print(f"  input:    {input_path}")
    print(f"  model:    {args.model}")
    print(f"  benchmark: {benchmark}  variant: {variant}")
    print(f"  out:      {out_dir}")
    print(f"  ref_policy: {args.reference_patch_policy}")
    print(f"  mat_policy: {args.material_policy}")
    print(f"  incremental: {not args.no_incremental}")
    print(f"  smoke_test: {args.smoke_test}")
    print()

    # --- Dry-run: resolve + feature detection only ---
    if args.dry_run:
        from openmc_agent.requirement_resolver import (
            resolve_requirement_references,
            resolved_requirement_summary,
        )
        from openmc_agent.plan_builder.mode import should_use_incremental_planning

        resolved = resolve_requirement_references(requirement_text)
        summary = resolved_requirement_summary(resolved)
        decision = should_use_incremental_planning(resolved.resolved_requirement)

        print("--- Dry-run (no LLM, no OpenMC) ---")
        print(f"  referenced_files: {summary['referenced_files']}")
        print(f"  resolved_chars:   {summary['resolved_requirement_chars']}")
        if summary["requirement_resolution_warnings"]:
            print(f"  warnings:         {summary['requirement_resolution_warnings']}")
        print(f"  mode:             {decision.mode}")
        print(f"  triggers:         {decision.triggers}")
        fs = decision.feature_summary
        print(f"  has_special_pin_map: {fs.get('has_special_pin_map')}")
        print(f"  has_spacer_grid:     {fs.get('has_spacer_grid')}")
        print(f"  has_axial_geometry:  {fs.get('has_axial_geometry')}")
        print(f"  large_lattice_dim:   {fs.get('large_lattice_dimension')}")
        return 0

    # --- Real run ---
    from openmc_agent.graph import build_plan_graph

    enable_smoke = args.smoke_test and _openmc_available()
    if args.smoke_test and not _openmc_available():
        print("WARNING: --smoke-test requested but OpenMC not available; skipping smoke test",
              file=sys.stderr)

    graph = build_plan_graph(
        enable_plots=False,
        enable_smoke_test=enable_smoke,
        reference_patch_policy=args.reference_patch_policy,
        use_incremental_executor=not args.no_incremental,
        material_policy=args.material_policy,
    )

    records_path = out_dir / "simulation_runs.jsonl"
    state = graph.invoke({
        "requirement": requirement_text,
        "model": args.model,
        "output_dir": str(out_dir),
        "records_path": str(records_path),
        "use_incremental_executor": not args.no_incremental,
    })

    # --- Print summary ---
    print()
    print("=" * 70)
    print("Run Summary")
    print("=" * 70)

    error = state.get("error")
    if error:
        print(f"  ERROR: {error}")

    plan = state.get("simulation_plan")
    if plan is not None:
        cap = plan.capability_report if hasattr(plan, "capability_report") else plan.get("capability_report", {})
        print(f"  renderability:      {getattr(cap, 'renderability', None) or cap.get('renderability')}")
        print(f"  supported_renderer: {getattr(cap, 'supported_renderer', None) or cap.get('supported_renderer')}")
        cm = plan.complex_model if hasattr(plan, "complex_model") else plan.get("complex_model", {})
        mats = cm.materials if hasattr(cm, "materials") else cm.get("materials", [])
        univs = cm.universes if hasattr(cm, "universes") else cm.get("universes", [])
        print(f"  materials:          {len(mats)}")
        print(f"  universes:          {len(univs)}")

    # Requirement resolution
    req_res = state.get("requirement_resolution") or {}
    if req_res:
        print(f"  referenced_files:   {req_res.get('referenced_files', [])}")
        print(f"  resolved_chars:     {req_res.get('resolved_requirement_chars', '?')}")

    # Planning mode + task order
    pmd = state.get("planning_mode_decision") or {}
    fs = pmd.get("feature_summary") or {}
    if fs:
        print(f"  planning_mode:      {pmd.get('mode')}")
        print(f"  has_special_pin_map: {fs.get('has_special_pin_map')}")
        print(f"  has_spacer_grid:     {fs.get('has_spacer_grid')}")

    pbs = state.get("plan_build_state") or {}
    tasks = pbs.get("component_tasks") or []
    task_order = [t.get("patch_type") for t in tasks if t.get("patch_type")]
    if task_order:
        print(f"  task_order:         {task_order}")

    # Incremental artifacts
    inc_dir = out_dir / "incremental"
    mat_report = inc_dir / "material_composition_report.json"
    if mat_report.exists():
        print(f"  material_report:    {mat_report}")

    # keff from smoke test
    if enable_smoke:
        sp_files = sorted(out_dir.glob("statepoint.*.h5"))
        if sp_files:
            keff = _read_keff(sp_files[-1])
            if keff:
                print(f"  keff:               {keff[0]:.5f} +/- {keff[1]:.5f}")
            else:
                print(f"  statepoint:         {sp_files[-1]} (keff unreadable)")

    print()
    print(f"Artifacts in: {out_dir}")
    return 0 if not error else 1


_FILENAME_BENCHMARK_MAP = {
    "vera1": "VERA1",
    "vera2": "VERA2",
    "vera3": "VERA3",
    "vera4": "VERA4",
    "vera5": "VERA5",
    "c5g7": "C5G7",
}

_FILENAME_VARIANT_MAP = {
    "1a": "1A", "1b": "1B",
    "2a": "2A", "2b": "2B",
    "3a": "3A", "3b": "3B",
    "4a": "4A", "4b": "4B",
    "5a": "5A", "5b": "5B",
}


def _detect_benchmark(path: Path) -> str:
    name = path.stem.lower()
    for key, val in _FILENAME_BENCHMARK_MAP.items():
        if key in name:
            return val
    return "CUSTOM"


def _detect_variant(path: Path) -> str:
    name = path.stem.lower()
    for key, val in _FILENAME_VARIANT_MAP.items():
        if key in name:
            return val
    return "default"


if __name__ == "__main__":
    raise SystemExit(main())
