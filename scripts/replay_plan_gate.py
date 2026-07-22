#!/usr/bin/env python
"""CLI for replaying a GateReplayBundle (Phase 8C Step 3B).

Re-evaluates a previously-accepted Facts or Material-Universe gate from a
**sanitized** bundle without touching production checkpoints.

Modes
-----
* ``preflight`` — pure deterministic validation, **no LLM**.  Checks
  bundle schema version, upstream accepted status, canonical hashes,
  complete normalized state and rejects sensitive/raw fields.
* ``recorded-review`` — replay normalized recorded review outputs.
* ``live-review`` — invoke the target reviewer only.  Requires ``--model``.

Usage
-----
    python scripts/replay_plan_gate.py \\
        --bundle path/to/bundle.json \\
        --mode preflight \\
        --out path/to/result.json

The default live-review timeout is 1800 seconds (30 minutes).  The caller
is responsible for enforcing it (e.g. via ``timeout`` or signal handlers).

Output is sanitized JSON only — never prompts, reasoning or raw responses.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Replay a GateReplayBundle for Facts or Material-Universe gate.",
    )
    parser.add_argument(
        "--bundle",
        required=True,
        help="Path to the GateReplayBundle JSON file.",
    )
    parser.add_argument(
        "--mode",
        required=True,
        choices=["preflight", "recorded-review", "live-review"],
        default="preflight",
        help="Replay mode. 'preflight' uses no LLM.",
    )
    parser.add_argument(
        "--model",
        default=None,
        help="Model name for live-review mode only (e.g. openai:gpt-4o).",
    )
    parser.add_argument(
        "--out",
        default=None,
        help="Output path for the sanitized JSON result. Defaults to stdout.",
    )
    parser.add_argument(
        "--live-timeout",
        type=int,
        default=1800,
        help="Live-review timeout in seconds (default: 1800 = 30 min). "
        "The caller is responsible for enforcing it.",
    )
    return parser


def _load_bundle(path: str):
    from openmc_agent.plan_builder.closed_loop.gate_replay import (
        load_gate_replay_bundle,
    )

    return load_gate_replay_bundle(path)


def _run(args: argparse.Namespace) -> dict[str, Any]:
    from openmc_agent.plan_builder.closed_loop.gate_replay import (
        DEFAULT_LIVE_REVIEW_TIMEOUT_SECONDS,
        GateReplayMode,
        run_gate_replay,
    )

    bundle = _load_bundle(args.bundle)
    reviewer_client = None
    if args.mode == "live-review":
        if args.model is None:
            raise SystemExit(
                "live-review mode requires --model (target reviewer model name)"
            )
        from openmc_agent.plan_builder.llm_adapter import make_patch_llm_client

        reviewer_client = make_patch_llm_client(model_name=args.model)
    timeout = args.live_timeout or DEFAULT_LIVE_REVIEW_TIMEOUT_SECONDS
    result = run_gate_replay(
        bundle,
        mode=GateReplayMode(args.mode),
        reviewer_client=reviewer_client,
        live_review_timeout=timeout,
    )
    return result.to_sanitized_dict()


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    try:
        result = _run(args)
    except SystemExit:
        raise
    except Exception as exc:
        print(
            json.dumps(
                {"ok": False, "error": f"{type(exc).__name__}: {exc}"},
                indent=2,
            ),
            file=sys.stderr,
        )
        return 2
    payload = json.dumps(result, indent=2, ensure_ascii=False, sort_keys=True)
    if args.out:
        out_path = Path(args.out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(payload, encoding="utf-8")
        print(f"wrote {out_path}", file=sys.stderr)
    else:
        print(payload)
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
