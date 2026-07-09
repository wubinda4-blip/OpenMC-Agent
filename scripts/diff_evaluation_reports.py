#!/usr/bin/env python3
"""Diff two workflow benchmark ``evaluation_report.json`` files.

Produces a markdown report comparing metric deltas, case status changes, new
failures, and fixed cases. Optionally fails (non-zero exit) when configured
regression thresholds are breached, so it can be used as a PR gate.

Example:

    python scripts/diff_evaluation_reports.py \\
        --base data/evals/workflow/baseline/evaluation_report.json \\
        --head data/evals/workflow/current/evaluation_report.json \\
        --out data/evals/workflow/current/report_diff.md

With regression gating:

    python scripts/diff_evaluation_reports.py \\
        --base $BASE_REPORT --head $HEAD_REPORT \\
        --fail-on-regression \\
        --min-pass-rate-delta 0.0
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


_METRIC_KEYS: tuple[str, ...] = (
    "pass_rate",
    "plan_schema_success_rate",
    "incremental_patch_success_rate",
    "artifact_completeness_rate",
    "planning_mode_accuracy",
    "issue_code_precision",
    "issue_code_recall",
    "retrieval_trigger_rate",
    "human_confirmation_rate",
)


def _load_report(path: str | Path) -> dict[str, Any]:
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"report not found: {p}")
    return json.loads(p.read_text(encoding="utf-8"))


def _metrics(report: dict[str, Any]) -> dict[str, Any]:
    return report.get("metrics") or {}


def _cases(report: dict[str, Any]) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for case in report.get("cases") or []:
        cid = case.get("case_id")
        if cid:
            out[cid] = case
    return out


def _fmt_pct(value: Any) -> str:
    if value is None:
        return "n/a"
    try:
        return f"{float(value) * 100:.1f}%"
    except (TypeError, ValueError):
        return str(value)


def _fmt_delta(base: Any, head: Any) -> str:
    if base is None or head is None:
        return "n/a"
    try:
        delta = float(head) - float(base)
        sign = "+" if delta >= 0 else ""
        return f"{sign}{delta * 100:.1f}pp"
    except (TypeError, ValueError):
        return "n/a"


def _case_status(case: dict[str, Any]) -> str:
    if case.get("passed"):
        return "pass"
    return "fail"


def build_diff(
    base: dict[str, Any],
    head: dict[str, Any],
) -> dict[str, Any]:
    """Build a structured diff between two evaluation reports."""
    base_metrics = _metrics(base)
    head_metrics = _metrics(head)
    metric_changes: list[dict[str, Any]] = []
    for key in _METRIC_KEYS:
        b = base_metrics.get(key)
        h = head_metrics.get(key)
        if b is None and h is None:
            continue
        delta: float | None = None
        if b is not None and h is not None:
            try:
                delta = float(h) - float(b)
            except (TypeError, ValueError):
                delta = None
        metric_changes.append({
            "metric": key,
            "base": b,
            "head": h,
            "delta": delta,
        })

    base_cases = _cases(base)
    head_cases = _cases(head)
    all_case_ids = sorted(set(base_cases) | set(head_cases))

    case_status_changes: list[dict[str, Any]] = []
    new_failures: list[dict[str, Any]] = []
    fixed_cases: list[dict[str, Any]] = []
    for cid in all_case_ids:
        b_case = base_cases.get(cid)
        h_case = head_cases.get(cid)
        b_status = _case_status(b_case) if b_case else "absent"
        h_status = _case_status(h_case) if h_case else "absent"
        if b_status == h_status:
            continue
        change = {
            "case_id": cid,
            "base": b_status,
            "head": h_status,
        }
        case_status_changes.append(change)
        if b_status == "pass" and h_status == "fail":
            new_failures.append({
                "case_id": cid,
                "failed_stage": (h_case or {}).get("failed_stage"),
                "failed_patch_type": (h_case or {}).get("failed_patch_type"),
                "issue_codes": (h_case or {}).get("issue_codes", []),
                "failure_reasons": (h_case or {}).get("failure_reasons", []),
            })
        elif b_status == "fail" and h_status == "pass":
            fixed_cases.append({
                "case_id": cid,
                "previous_failure": {
                    "failed_stage": (b_case or {}).get("failed_stage"),
                    "failed_patch_type": (b_case or {}).get("failed_patch_type"),
                },
                "current_status": "pass",
            })

    return {
        "metric_changes": metric_changes,
        "case_status_changes": case_status_changes,
        "new_failures": new_failures,
        "fixed_cases": fixed_cases,
    }


def render_diff_markdown(diff: dict[str, Any]) -> str:
    """Render a structured diff as a markdown report."""
    lines: list[str] = [
        "# Evaluation Report Diff",
        "",
        "## Metric changes",
        "",
        "| metric | base | head | delta |",
        "| --- | --- | --- | --- |",
    ]
    for m in diff["metric_changes"]:
        lines.append(
            f"| {m['metric']} | {_fmt_pct(m['base'])} | {_fmt_pct(m['head'])} "
            f"| {_fmt_delta(m['base'], m['head'])} |"
        )

    lines += [
        "",
        "## Case status changes",
        "",
        "| case_id | base | head | change |",
        "| --- | --- | --- | --- |",
    ]
    if diff["case_status_changes"]:
        for c in diff["case_status_changes"]:
            lines.append(
                f"| {c['case_id']} | {c['base']} | {c['head']} | "
                f"{c['base']} -> {c['head']} |"
            )
    else:
        lines.append("| _none_ |  |  |  |")

    lines += [
        "",
        "## New failures",
        "",
        "| case_id | failed_stage | failed_patch_type | issue_codes |",
        "| --- | --- | --- | --- |",
    ]
    if diff["new_failures"]:
        for f in diff["new_failures"]:
            lines.append(
                f"| {f['case_id']} | {f.get('failed_stage') or ''} | "
                f"{f.get('failed_patch_type') or ''} | "
                f"{', '.join(f.get('issue_codes', []))} |"
            )
    else:
        lines.append("| _none_ |  |  |  |")

    lines += [
        "",
        "## Fixed cases",
        "",
        "| case_id | previous_failure | current_status |",
        "| --- | --- | --- |",
    ]
    if diff["fixed_cases"]:
        for fx in diff["fixed_cases"]:
            prev = fx.get("previous_failure") or {}
            lines.append(
                f"| {fx['case_id']} | "
                f"stage={prev.get('failed_stage') or ''} "
                f"patch={prev.get('failed_patch_type') or ''} | "
                f"{fx.get('current_status', '')} |"
            )
    else:
        lines.append("| _none_ |  |  |")

    return "\n".join(lines) + "\n"


def check_regression(
    diff: dict[str, Any],
    *,
    min_pass_rate_delta: float = 0.0,
    min_plan_schema_delta: float = 0.0,
    min_artifact_completeness_delta: float = 0.0,
    allow_new_failures: bool = False,
) -> list[str]:
    """Return a list of regression violations (empty = no regression)."""
    violations: list[str] = []
    by_metric = {m["metric"]: m for m in diff["metric_changes"]}

    def _check(metric_name: str, threshold: float, label: str) -> None:
        m = by_metric.get(metric_name)
        if m is None:
            return
        delta = m.get("delta")
        if delta is None:
            return
        if delta < -abs(threshold):
            violations.append(
                f"{label} regression: delta={delta * 100:.1f}pp "
                f"(threshold={-abs(threshold) * 100:.1f}pp)"
            )

    _check("pass_rate", min_pass_rate_delta, "pass_rate")
    _check("plan_schema_success_rate", min_plan_schema_delta, "plan_schema_success_rate")
    _check(
        "artifact_completeness_rate",
        min_artifact_completeness_delta,
        "artifact_completeness_rate",
    )
    if not allow_new_failures and diff["new_failures"]:
        ids = [f["case_id"] for f in diff["new_failures"]]
        violations.append(f"new failed cases: {', '.join(ids)}")
    return violations


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", required=True, help="Path to baseline evaluation_report.json")
    parser.add_argument("--head", required=True, help="Path to head evaluation_report.json")
    parser.add_argument("--out", default=None, help="Path to write markdown diff report")
    parser.add_argument("--fail-on-regression", action="store_true",
                        help="Exit non-zero when a regression threshold is breached")
    parser.add_argument("--min-pass-rate-delta", type=float, default=0.0,
                        help="Allowed pass_rate decrease (positive number; default 0.0)")
    parser.add_argument("--min-plan-schema-delta", type=float, default=0.0,
                        help="Allowed plan_schema_success_rate decrease")
    parser.add_argument("--min-artifact-completeness-delta", type=float, default=0.0,
                        help="Allowed artifact_completeness_rate decrease")
    parser.add_argument("--allow-new-failures", action="store_true",
                        help="Do not fail when new cases fail (only metric deltas gate)")
    args = parser.parse_args(argv)

    base = _load_report(args.base)
    head = _load_report(args.head)
    diff = build_diff(base, head)

    md = render_diff_markdown(diff)
    if args.out:
        out_path = Path(args.out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(md, encoding="utf-8")
        print(f"Wrote diff report: {out_path}")

    # Print a compact summary to stdout.
    bm = _metrics(base)
    hm = _metrics(head)
    print(f"base pass_rate={_fmt_pct(bm.get('pass_rate'))} "
          f"plan_schema={_fmt_pct(bm.get('plan_schema_success_rate'))} "
          f"artifact={_fmt_pct(bm.get('artifact_completeness_rate'))}")
    print(f"head pass_rate={_fmt_pct(hm.get('pass_rate'))} "
          f"plan_schema={_fmt_pct(hm.get('plan_schema_success_rate'))} "
          f"artifact={_fmt_pct(hm.get('artifact_completeness_rate'))}")
    print(f"new_failures={len(diff['new_failures'])} "
          f"fixed_cases={len(diff['fixed_cases'])} "
          f"status_changes={len(diff['case_status_changes'])}")

    if args.fail_on_regression:
        violations = check_regression(
            diff,
            min_pass_rate_delta=args.min_pass_rate_delta,
            min_plan_schema_delta=args.min_plan_schema_delta,
            min_artifact_completeness_delta=args.min_artifact_completeness_delta,
            allow_new_failures=args.allow_new_failures,
        )
        if violations:
            print("\nREGRESSION DETECTED:", file=sys.stderr)
            for v in violations:
                print(f"  - {v}", file=sys.stderr)
            return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
