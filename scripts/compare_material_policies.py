#!/usr/bin/env python3
"""Compare two material composition policies (preserve_plan vs apply_alloy_library).

This script runs (or plans to run) the incremental plan-builder workflow under
two material policies, collects keff from OpenMC statepoints when OpenMC is
available, and writes a structured ``comparison_report.json``.

The default behaviour is ``--dry-run`` friendly: it never imports OpenMC unless
the user opts in, and it always writes a report describing what would run / did
run so it can be used in a base Python environment without OpenMC installed.

Example (dry-run, base environment, no OpenMC):

    python scripts/compare_material_policies.py \\
        --benchmark VERA3 \\
        --variant 3A \\
        --input Input/VERA3_problem.md \\
        --model fake \\
        --reference-patch-policy reference_only_for_structural \\
        --dry-run \\
        --out data/evals/material_policy/VERA3_3A_dry

Example (real OpenMC smoke, inside openmc-env):

    python scripts/compare_material_policies.py \\
        --benchmark VERA3 \\
        --variant 3A \\
        --input Input/VERA3_problem.md \\
        --model deepseek:deepseek-chat \\
        --reference-patch-policy reference_only_for_structural \\
        --batches 5 --inactive 1 --particles 1000 \\
        --out data/evals/material_policy/VERA3_3A_alloy
"""

from __future__ import annotations

import argparse
import json
import sys
import traceback
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


def _find_statepoint(run_dir: Path) -> Path | None:
    if not run_dir.exists():
        return None
    candidates = sorted(run_dir.glob("statepoint.*.h5"))
    return candidates[-1] if candidates else None


def _run_policy_case(
    *,
    benchmark: str,
    variant: str,
    input_path: Path,
    model: str,
    reference_patch_policy: str,
    policy_name: str,
    out_dir: Path,
    batches: int,
    inactive: int,
    particles: int,
    dry_run: bool,
    allow_real_llm: bool,
    use_incremental: bool,
) -> dict[str, Any]:
    """Run one material-policy case and return a result dict."""
    case_dir = out_dir / policy_name
    case_dir.mkdir(parents=True, exist_ok=True)
    result: dict[str, Any] = {
        "policy": policy_name,
        "ok": False,
        "keff": None,
        "std_dev": None,
        "material_composition_report_path": None,
        "statepoint_path": None,
        "requirement_resolution": {},
        "feature_summary": {},
        "task_order": [],
        "notes": [],
        "error": None,
    }

    if dry_run:
        result["notes"].append("dry-run: no OpenMC execution, no LLM call")
        result["ok"] = True
        # Even in dry-run, resolve the requirement references so callers can
        # verify feature detection would see the file content.
        try:
            from openmc_agent.requirement_resolver import (
                resolve_requirement_references,
                resolved_requirement_summary,
            )
            from openmc_agent.plan_builder.mode import should_use_incremental_planning

            requirement_text = (
                f"Build the {benchmark} {variant} benchmark model "
                f"described in {input_path}."
            )
            resolved = resolve_requirement_references(requirement_text)
            summary = resolved_requirement_summary(resolved)
            result["requirement_resolution"] = summary
            decision = should_use_incremental_planning(resolved.resolved_requirement)
            result["feature_summary"] = decision.feature_summary
            result["task_order"] = []
            if decision.mode == "incremental":
                result["task_order"] = [
                    "facts", "materials", "universes", "pin_map",
                    "axial_layers", "axial_overlays", "settings",
                ] if (
                    decision.feature_summary.get("has_special_pin_map")
                    or decision.feature_summary.get("has_spacer_grid")
                    or decision.feature_summary.get("large_lattice_dimension")
                ) else [
                    "facts", "materials", "universes", "axial_layers", "settings",
                ]
        except Exception as exc:
            result["notes"].append(f"dry-run requirement resolution failed: {exc}")
        result["material_composition_report_path"] = str(
            case_dir / "material_composition_report.json (would be written)"
        )
        return result

    if not _openmc_available():
        result["notes"].append(
            "OpenMC not available in this environment; skipping execution"
        )
        result["ok"] = True
        return result

    # Build the plan graph with the requested material policy. The graph
    # already renders model.py and (optionally) runs the smoke test.
    try:
        from openmc_agent.graph import build_plan_graph

        graph = build_plan_graph(
            enable_plots=False,
            enable_smoke_test=True,
            reference_patch_policy=reference_patch_policy,
            use_incremental_executor=use_incremental,
            material_policy=policy_name,
        )
        requirement_text = (
            f"Build the {benchmark} {variant} benchmark model "
            f"described in {input_path}."
        )
        records_path = case_dir / "simulation_runs.jsonl"
        state = graph.invoke(
            {
                "requirement": requirement_text,
                "model": model,
                "output_dir": str(case_dir),
                "records_path": str(records_path),
                "use_incremental_executor": use_incremental,
            }
        )

        # Record requirement resolution metadata so callers can verify that
        # the input file content was inlined into feature detection.
        req_resolution = state.get("requirement_resolution") or {}
        if req_resolution:
            result["requirement_resolution"] = req_resolution
        pmd = state.get("planning_mode_decision") or {}
        if pmd.get("feature_summary"):
            result["feature_summary"] = pmd["feature_summary"]
        # Record the required patch types so callers can verify pin_map and
        # axial_overlays entered the task order.
        pbs = state.get("plan_build_state") or {}
        tasks = pbs.get("component_tasks") or []
        result["task_order"] = [t.get("patch_type") for t in tasks if t.get("patch_type")]

        # Locate the material composition report written by the assembler.
        inc_dir = case_dir / "incremental"
        src_report = inc_dir / "material_composition_report.json"
        if src_report.exists():
            result["material_composition_report_path"] = str(src_report)
        else:
            result["notes"].append(
                "no material_composition_report.json found in incremental artifacts"
            )

        plan = state.get("simulation_plan")
        if plan is None:
            result["error"] = state.get("error") or "no simulation_plan produced"
            return result

        # The graph's smoke-test node writes the statepoint when OpenMC is
        # available. Read keff from it if present.
        sp = _find_statepoint(case_dir)
        if sp is not None:
            result["statepoint_path"] = str(sp)
            keff = _read_keff(sp)
            if keff is not None:
                result["keff"] = keff[0]
                result["std_dev"] = keff[1]
                result["ok"] = True
                result["notes"].append(
                    f"smoke keff={keff[0]:.5f} +/- {keff[1]:.5f}"
                )
            else:
                result["ok"] = True
                result["notes"].append("statepoint found but keff unreadable")
        else:
            result["ok"] = True
            result["notes"].append(
                "no statepoint found (graph may not have run smoke test)"
            )
    except Exception as exc:
        result["error"] = f"{type(exc).__name__}: {exc}"
        result["notes"].append(traceback.format_exc())

    return result


def run_comparison(args: argparse.Namespace) -> dict[str, Any]:
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    input_path = Path(args.input)

    cases: dict[str, Any] = {}
    for policy_name in ("preserve_plan", "apply_alloy_library"):
        cases[policy_name] = _run_policy_case(
            benchmark=args.benchmark,
            variant=args.variant,
            input_path=input_path,
            model=args.model,
            reference_patch_policy=args.reference_patch_policy,
            policy_name=policy_name,
            out_dir=out_dir,
            batches=args.batches,
            inactive=args.inactive,
            particles=args.particles,
            dry_run=args.dry_run,
            allow_real_llm=args.allow_real_llm,
            use_incremental=not args.no_incremental,
        )

    delta_pcm: int | None = None
    pp = cases.get("preserve_plan", {})
    al = cases.get("apply_alloy_library", {})
    if (
        pp.get("keff") is not None
        and al.get("keff") is not None
        and isinstance(pp["keff"], (int, float))
        and isinstance(al["keff"], (int, float))
    ):
        delta_pcm = int(round((al["keff"] - pp["keff"]) * 1e5))

    notes = [
        "Smoke-level run only; not benchmark agreement.",
        "Alloy compositions are nominal engineering approximations and replaceable.",
        "Geometry, source, and spacer overlay logic are unchanged between cases.",
    ]
    if args.dry_run:
        notes.append("This was a dry-run; no OpenMC execution or LLM call was made.")
    if not _openmc_available() and not args.dry_run:
        notes.append("OpenMC was not available; keff values are None.")

    report = {
        "benchmark": args.benchmark,
        "variant": args.variant,
        "input": str(input_path),
        "model": args.model,
        "reference_patch_policy": args.reference_patch_policy,
        "settings": {
            "batches": args.batches,
            "inactive": args.inactive,
            "particles": args.particles,
            "dry_run": args.dry_run,
        },
        "cases": cases,
        "delta_pcm": delta_pcm,
        "notes": notes,
    }
    report_path = out_dir / "comparison_report.json"
    report_path.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    report["comparison_report_path"] = str(report_path)
    return report


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--benchmark", default="VERA3", help="Benchmark family (e.g. VERA3)")
    parser.add_argument("--variant", default="3A", help="Benchmark variant (e.g. 3A, 3B)")
    parser.add_argument("--input", required=True, help="Path to benchmark problem description")
    parser.add_argument("--model", default="fake", help="Model name (e.g. fake, deepseek:deepseek-chat)")
    parser.add_argument(
        "--reference-patch-policy", default="off",
        help="Reference patch policy for the incremental executor",
    )
    parser.add_argument("--batches", type=int, default=5)
    parser.add_argument("--inactive", type=int, default=1)
    parser.add_argument("--particles", type=int, default=1000)
    parser.add_argument("--out", default="data/evals/material_policy/run", help="Output directory")
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Do not run OpenMC or call LLMs; only emit a planned-run report.",
    )
    parser.add_argument(
        "--require-openmc", action="store_true",
        help="Fail if OpenMC is not importable (otherwise skip execution gracefully).",
    )
    parser.add_argument(
        "--allow-real-llm", action="store_true",
        help="Explicitly allow non-fake LLM model names.",
    )
    parser.add_argument(
        "--no-incremental", action="store_true",
        help="Use the monolithic planner instead of the incremental executor.",
    )
    parser.add_argument("--log-level", default="INFO",
                        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
                        help="Logging level (default: INFO)")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    from openmc_agent.logging_setup import configure_logging
    configure_logging(args.log_level)

    if args.model != "fake" and not args.allow_real_llm:
        print(
            "Refusing to use non-fake model without --allow-real-llm.",
            file=sys.stderr,
        )
        return 2

    if args.require_openmc and not _openmc_available():
        print(
            "OpenMC is required (per --require-openmc) but not importable.",
            file=sys.stderr,
        )
        return 3

    report = run_comparison(args)
    print(f"Wrote comparison report: {report['comparison_report_path']}")
    pp = report["cases"]["preserve_plan"]
    al = report["cases"]["apply_alloy_library"]
    print(
        f"preserve_plan:         ok={pp['ok']} keff={pp.get('keff')} +/- {pp.get('std_dev')}"
    )
    print(
        f"apply_alloy_library:   ok={al['ok']} keff={al.get('keff')} +/- {al.get('std_dev')}"
    )
    if report.get("delta_pcm") is not None:
        print(f"delta_pcm (alloy - preserve): {report['delta_pcm']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
