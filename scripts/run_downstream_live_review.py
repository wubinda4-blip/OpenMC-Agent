#!/usr/bin/env python
"""Run Placement → Axial Geometry → Assembled Plan live-review in sequence.

Loads bundles from a directory (offline or extracted from a real campaign),
runs preflight then live-review for each gate in dependency order, and writes
a sanitized JSON summary.  The caller is responsible for enforcing the
per-gate timeout (default 1800 s).

Usage::

    python scripts/run_downstream_live_review.py \\
        --bundle-dir data/runs/<run>/replay_bundles \\
        --model zhipu:glm-5.2 \\
        --out data/runs/<run>/live_review_result.json

To validate the pipeline without a real LLM, use ``--bundle-dir
tests/fixtures/gate_replay`` with ``--mode recorded-review``.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from openmc_agent.plan_builder.closed_loop.gate_replay import (
    DEFAULT_LIVE_REVIEW_TIMEOUT_SECONDS,
    GateReplayMode,
    load_gate_replay_bundle,
    run_gate_replay,
)

GATES = ("placement", "axial_geometry", "assembled_plan")


def _bundle_path(bundle_dir: str | Path, gate: str, suffix: str = "live_bundle") -> Path:
    return Path(bundle_dir) / f"{gate}_{suffix}.json"


def _resolve_bundle(bundle_dir: str | Path, gate: str) -> Path:
    live = _bundle_path(bundle_dir, gate, "live_bundle")
    if live.exists():
        return live
    offline = _bundle_path(bundle_dir, gate, "offline_bundle")
    if offline.exists():
        return offline
    raise FileNotFoundError(f"no bundle for gate {gate} in {bundle_dir}")


def run_sequential_live_review(
    bundle_dir: str | Path,
    model: str,
    *,
    gates: tuple[str, ...] = GATES,
    live_timeout: int = DEFAULT_LIVE_REVIEW_TIMEOUT_SECONDS,
    mode: str = "live-review",
    continue_on_fail: bool = False,
) -> dict[str, Any]:
    """Run sequential gate review and return a sanitized JSON-serialisable result."""
    reviewer_client = None
    if mode == "live-review":
        from openmc_agent.plan_builder.llm_adapter import make_patch_llm_client
        reviewer_client = make_patch_llm_client(model_name=model)

    replay_mode = GateReplayMode(mode)
    results: list[dict[str, Any]] = []
    all_ok = True

    for gate in gates:
        try:
            bundle = load_gate_replay_bundle(_resolve_bundle(bundle_dir, gate))
        except FileNotFoundError as exc:
            results.append({"gate_id": gate, "ok": False, "error": str(exc), "skipped": True})
            all_ok = False
            if not continue_on_fail:
                break
            continue

        preflight = run_gate_replay(bundle, mode=GateReplayMode.PREFLIGHT)
        if not preflight.ok:
            results.append({
                "gate_id": gate,
                "ok": False,
                "preflight": preflight.to_sanitized_dict(),
                "review": None,
                "skipped": "preflight_failed",
            })
            all_ok = False
            if not continue_on_fail:
                break
            continue

        review = run_gate_replay(
            bundle, mode=replay_mode,
            reviewer_client=reviewer_client,
            live_review_timeout=live_timeout,
        )
        results.append({
            "gate_id": gate,
            "ok": review.ok,
            "preflight": preflight.to_sanitized_dict(),
            "review": review.to_sanitized_dict(),
            "terminal_status": review.terminal_status,
            "coverage": review.coverage,
            "blocking_finding_count": review.blocking_finding_count,
            "rejected_finding_count": review.rejected_finding_count,
        })
        if not review.ok:
            all_ok = False
            if not continue_on_fail:
                break

    return {
        "ok": all_ok,
        "mode": mode,
        "model": model if mode == "live-review" else None,
        "gates": results,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run downstream gate live-review in sequence.")
    parser.add_argument("--bundle-dir", required=True, help="Directory containing gate bundle JSONs")
    parser.add_argument("--model", default=None, help="Model name for live-review (e.g. zhipu:glm-5.2)")
    parser.add_argument("--mode", default="live-review", choices=["live-review", "recorded-review", "preflight"])
    parser.add_argument("--gates", default="placement,axial_geometry,assembled_plan")
    parser.add_argument("--out", default=None)
    parser.add_argument("--live-timeout", type=int, default=DEFAULT_LIVE_REVIEW_TIMEOUT_SECONDS)
    parser.add_argument("--continue-on-fail", action="store_true")
    args = parser.parse_args(argv)

    if args.mode == "live-review" and args.model is None:
        print("live-review mode requires --model", file=sys.stderr)
        return 2

    payload = run_sequential_live_review(
        args.bundle_dir,
        args.model or "offline",
        gates=tuple(args.gates.split(",")),
        live_timeout=args.live_timeout,
        mode=args.mode,
        continue_on_fail=args.continue_on_fail,
    )
    text = json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True)
    if args.out:
        out_path = Path(args.out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(text, encoding="utf-8")
        print(f"wrote {out_path}", file=sys.stderr)
    else:
        print(text)
    return 0 if payload["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
