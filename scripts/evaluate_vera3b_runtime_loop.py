"""Bounded VERA3B runtime-loop harness.

This script records the supported runtime-loop fault modes. It deliberately
does not invent geometry repairs: ambiguous/protected geometry remains
diagnose-only and environment failures remain blocked.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path


SUPPORTED_FAULT_SEQUENCES = {
    "baseline",
    "source_then_success",
    "source_then_unique_reference",
    "source_then_same_source",
    "timeout_then_success",
    "timeout_twice",
    "source_then_environment_blocker",
    "ambiguous_geometry",
    "protected_geometry",
    "rejected_candidate",
}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--client", choices=("fake", "real"), default="fake")
    parser.add_argument("--supervisor", choices=("deterministic", "fake", "real"), default="deterministic")
    parser.add_argument("--mode", choices=("diagnose_only", "validate_only", "apply_if_safe"), default="diagnose_only")
    parser.add_argument("--fault-sequence", choices=sorted(SUPPORTED_FAULT_SEQUENCES), default="baseline")
    parser.add_argument("--max-iterations", type=int, default=4)
    parser.add_argument("--runs", type=int, default=1)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()

    args.output_dir.mkdir(parents=True, exist_ok=True)
    real_available = bool(os.environ.get("DEEPSEEK_API_KEY"))
    status = "READY"
    if args.client == "real" or args.supervisor == "real":
        status = "READY" if real_available else "REAL_LLM_SKIPPED_ENV"

    report = {
        "client": args.client,
        "supervisor": args.supervisor,
        "mode": args.mode,
        "fault_sequence": args.fault_sequence,
        "max_iterations": args.max_iterations,
        "runs": args.runs,
        "status": status,
        "safety_boundary": (
            "ambiguous/protected geometry remains diagnose_only; no material, "
            "radius, axial-boundary, pin-map, or nuclear-data repair is attempted"
        ),
    }
    (args.output_dir / "runtime_loop_harness_report.json").write_text(
        json.dumps(report, indent=2), encoding="utf-8"
    )
    print(json.dumps(report, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
