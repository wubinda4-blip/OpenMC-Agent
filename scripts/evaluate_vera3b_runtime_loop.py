"""Run the R7 Lane A VERA3B runtime fault matrix."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from openmc_agent.runtime_campaign import RuntimeCampaignConfig, run_fault_matrix
from openmc_agent.runtime_faults import default_fault_matrix, fault_case_by_name


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--lane", choices=("fixture", "real-generation"), default="fixture")
    parser.add_argument("--client", choices=("fake", "real"), default="fake")
    parser.add_argument("--supervisor", choices=("deterministic", "fake", "real"), default="deterministic")
    parser.add_argument("--mode", choices=("diagnose_only", "validate_only", "apply_if_safe"), default="diagnose_only")
    parser.add_argument("--fault-case")
    parser.add_argument("--fault-matrix", action="store_true")
    parser.add_argument("--max-iterations", type=int, default=4)
    parser.add_argument("--runs", type=int, default=1)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--markdown-report", action="store_true")
    args = parser.parse_args()

    if (
        args.client == "real" or args.supervisor == "real"
    ) and not os.environ.get("DEEPSEEK_API_KEY"):
        report = {
            "status": "REAL_LLM_SKIPPED_ENV",
            "lane": args.lane,
            "reason": "DEEPSEEK_API_KEY is not configured",
        }
        args.output_dir.mkdir(parents=True, exist_ok=True)
        (args.output_dir / "runtime_loop_harness_report.json").write_text(
            json.dumps(report, indent=2), encoding="utf-8"
        )
        print(report)
        return 0

    config = RuntimeCampaignConfig(
        output_dir=args.output_dir, max_iterations=args.max_iterations,
        client=args.client, supervisor=args.supervisor, mode=args.mode,
    )
    cases = default_fault_matrix() if args.fault_matrix or not args.fault_case else [fault_case_by_name(args.fault_case)]
    report = run_fault_matrix(cases, config)
    print(report["metrics"])
    return 0 if report["metrics"]["status"] == "VERA3B_RUNTIME_FAULT_MATRIX_PASSED" else 1


if __name__ == "__main__":
    raise SystemExit(main())
