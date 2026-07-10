#!/usr/bin/env python3
"""Run the P0 workflow benchmark and write report artifacts."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from openmc_agent.workflow_benchmark import (  # noqa: E402
    WorkflowBenchmarkConfig,
    run_workflow_benchmark,
)


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    mode = args.mode.replace("-", "_")
    if args.model != "fake" and not args.allow_real_llm:
        print(
            "Refusing to run real LLM benchmark without --allow-real-llm.",
            file=sys.stderr,
        )
        return 2
    config = WorkflowBenchmarkConfig(
        cases_path=args.cases,
        output_dir=args.out,
        model=args.model,
        mode=mode,
        max_cases=args.max_cases,
        categories=args.categories or [],
        reference_patch_policy=args.reference_patch_policy,
        use_incremental_executor=args.use_incremental_executor,
        enable_retrieval=args.enable_retrieval,
        enable_graph_retrieval=args.enable_graph_retrieval,
        allow_real_llm=args.allow_real_llm,
        enable_semantic_audit=args.enable_semantic_audit,
        semantic_audit_mode=args.semantic_audit_mode.replace("-", "_"),
        semantic_audit_model=args.semantic_audit_model,
        semantic_audit_allow_fallback=args.semantic_audit_allow_fallback,
        enable_llm_repair=args.enable_llm_repair,
        llm_repair_mode=args.llm_repair_mode.replace("-", "_"),
        llm_repair_model=args.llm_repair_model,
        llm_repair_allow_fallback=args.llm_repair_allow_fallback,
        llm_repair_max_proposals=args.llm_repair_max_proposals,
    )
    try:
        result = run_workflow_benchmark(config)
    except Exception as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print(f"Wrote evaluation report: {result.report_path}")
    print(f"Wrote benchmark summary: {result.summary_path}")
    print(f"Cases: {result.case_count} pass_rate={result.metrics.pass_rate:.1%}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", required=True, help="Path to evaluation_cases.json")
    parser.add_argument("--model", default="fake", help="Model name; non-fake requires --allow-real-llm")
    parser.add_argument(
        "--mode",
        default="plan-only",
        choices=["plan-only", "render-only", "smoke-test"],
        help="Workflow benchmark mode",
    )
    parser.add_argument("--out", default="data/evals/workflow", help="Output directory")
    parser.add_argument("--max-cases", type=int, default=None)
    parser.add_argument(
        "--category",
        "--categories",
        action="append",
        dest="categories",
        default=[],
        help="Case category filter; can be passed multiple times",
    )
    parser.add_argument("--reference-patch-policy", default="off")
    parser.add_argument(
        "--use-incremental-executor",
        dest="use_incremental_executor",
        action="store_true",
        default=True,
    )
    parser.add_argument(
        "--no-incremental-executor",
        dest="use_incremental_executor",
        action="store_false",
    )
    parser.add_argument("--enable-retrieval", dest="enable_retrieval", action="store_true", default=True)
    parser.add_argument("--disable-retrieval", dest="enable_retrieval", action="store_false")
    parser.add_argument(
        "--enable-graph-retrieval",
        dest="enable_graph_retrieval",
        action="store_true",
        default=True,
    )
    parser.add_argument(
        "--disable-graph-retrieval",
        dest="enable_graph_retrieval",
        action="store_false",
    )
    parser.add_argument("--allow-real-llm", action="store_true")
    parser.add_argument("--enable-semantic-audit", action="store_true")
    parser.add_argument("--semantic-audit-mode", default="warning-only", choices=["warning-only", "strict-evaluation"])
    parser.add_argument("--semantic-audit-model", default=None)
    parser.add_argument("--allow-semantic-audit-fallback", dest="semantic_audit_allow_fallback", action="store_true", default=True)
    parser.add_argument("--disable-semantic-audit-fallback", dest="semantic_audit_allow_fallback", action="store_false")
    parser.add_argument("--enable-llm-repair", action="store_true")
    parser.add_argument("--llm-repair-mode", default="proposal-only", choices=["proposal-only", "validate-only", "apply-if-safe"])
    parser.add_argument("--llm-repair-model", default=None)
    parser.add_argument("--allow-llm-repair-fallback", dest="llm_repair_allow_fallback", action="store_true", default=True)
    parser.add_argument("--disable-llm-repair-fallback", dest="llm_repair_allow_fallback", action="store_false")
    parser.add_argument("--llm-repair-max-proposals", type=int, default=1)
    return parser


if __name__ == "__main__":
    raise SystemExit(main())
