#!/usr/bin/env python
"""Run the deterministic Phase 8C Step 3F recovery qualification matrix."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from openmc_agent.plan_builder.closed_loop.campaign_recovery import run_campaign_recovery_matrix


def main() -> int:
    parser = argparse.ArgumentParser(description="Qualify campaign checkpoint and replay recovery offline.")
    parser.add_argument("--bundle-dir", default="tests/fixtures/gate_replay")
    parser.add_argument("--out", default=None)
    args = parser.parse_args()
    payload = run_campaign_recovery_matrix(args.bundle_dir).model_dump(mode="json")
    text = json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True)
    if args.out:
        path = Path(args.out)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    else:
        print(text)
    return 0 if payload["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
