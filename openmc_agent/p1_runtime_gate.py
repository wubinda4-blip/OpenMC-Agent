"""P1-RUNTIME final stage gate checker.

Only when ALL of the following are satisfied can the stage be marked complete:

1. RUNTIME_FEEDBACK_REPAIR_INFRASTRUCTURE_READY
2. VERA3B_RUNTIME_FAULT_MATRIX_PASSED
3. RUNTIME_TRUTHFULNESS_T5_PASSED
4. VERA3B_REAL_LLM_STABILITY_ACCEPTED
5. RUNTIME_TRUTHFULNESS_T6_QUALIFICATION_PASSED
6. VERA3B_TRANSPORT_SEED_STABILITY_PASSED
7. Full non-OpenMC tests passed
8. Full OpenMC tests passed
9. Benchmark 21/21
10. No uncommitted worktree changes (for task-related files)
11. Final report and artifact manifest complete
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class GateCheck:
    name: str
    passed: bool
    evidence: str = ""
    required: bool = True


@dataclass
class P1RuntimeFinalGateResult:
    status: str  # P1_RUNTIME_STAGE_COMPLETE | P1_RUNTIME_STAGE_NOT_COMPLETE
    gates: list[GateCheck] = field(default_factory=list)
    failed_gates: list[str] = field(default_factory=list)

    @property
    def all_passed(self) -> bool:
        return all(g.passed for g in self.gates if g.required)


def evaluate_p1_runtime_final_gate(
    *,
    fault_matrix_status: str = "",
    pilot_status: str = "",
    qualification_metrics: dict[str, Any] | None = None,
    qualification_status: str = "",
    seed_stability_status: str = "",
    non_openmc_tests_passed: bool = False,
    openmc_tests_passed: bool = False,
    benchmark_pass_rate: float = 0.0,
    benchmark_total: int = 0,
    worktree_clean: bool = False,
    artifact_manifest_complete: bool = False,
) -> P1RuntimeFinalGateResult:
    """Evaluate all P1-RUNTIME final gate criteria."""
    result = P1RuntimeFinalGateResult(status="P1_RUNTIME_STAGE_NOT_COMPLETE")
    metrics = qualification_metrics or {}

    # Gate 1: Fault matrix passed.
    g1 = GateCheck(
        name="VERA3B_RUNTIME_FAULT_MATRIX_PASSED",
        passed="FAULT_MATRIX_PASSED" in fault_matrix_status,
        evidence=f"status={fault_matrix_status}",
    )
    result.gates.append(g1)

    # Gate 2: T5 pilot passed.
    g2 = GateCheck(
        name="RUNTIME_TRUTHFULNESS_T5_PASSED",
        passed="PILOT_PASSED" in pilot_status or "STABILITY_ACCEPTED" in pilot_status,
        evidence=f"status={pilot_status}",
    )
    result.gates.append(g2)

    # Gate 3: T6 qualification stability accepted.
    g3 = GateCheck(
        name="VERA3B_REAL_LLM_STABILITY_ACCEPTED",
        passed="STABILITY_ACCEPTED" in qualification_status,
        evidence=f"status={qualification_status}",
    )
    result.gates.append(g3)

    # Gate 4: T6 qualification detailed metrics pass.
    final_rate = metrics.get("final_success_rate", 0.0)
    successful = metrics.get("successful_runs", 0)
    autonomous = metrics.get("autonomous_terminal_rate", 0.0)
    bounded = metrics.get("bounded_outcome_rate", 0.0)
    unsafe = metrics.get("unsafe_acceptance_rate", 0.0)
    protected = metrics.get("protected_field_change_count", 0)
    env_repair = metrics.get("environment_plan_repair_attempts", 0)
    human_repair = metrics.get("human_fact_plan_repair_attempts", 0)
    fake_count = metrics.get("fake_client_count", 0)
    ref_count = metrics.get("reference_patch_count", 0)
    bench_fs = metrics.get("benchmark_few_shot_count", 0)
    gold_fs = metrics.get("gold_few_shot_count", 0)
    monolithic = metrics.get("monolithic_fallback_count", 0)
    unverif = metrics.get("unverified_provenance_count", 0)
    lost_particle = metrics.get("lost_particle_runs", 0)
    source_rej = metrics.get("source_rejection_final_runs", 0)
    artifact_rate = metrics.get("successful_artifact_completeness_rate", metrics.get("artifact_completeness_rate", 0.0))
    real_llm_rate = metrics.get("successful_real_llm_rate", metrics.get("real_llm_verification_rate", 0.0))
    real_openmc_rate = metrics.get("successful_real_openmc_rate", metrics.get("real_openmc_verification_rate", 0.0))
    vera3_rate = metrics.get("successful_vera3_rate", metrics.get("vera3_acceptance_rate", 0.0))
    completed = metrics.get("completed_runs", 0)

    t6_details_ok = (
        completed >= 10
        and successful >= 7
        and autonomous >= 0.9
        and bounded >= 0.8
        and unsafe == 0
        and protected == 0
        and env_repair == 0
        and human_repair == 0
        and fake_count == 0
        and ref_count == 0
        and bench_fs == 0
        and gold_fs == 0
        and monolithic == 0
        and unverif == 0
        and lost_particle == 0
        and source_rej == 0
        and artifact_rate == 1.0
        and real_llm_rate == 1.0
        and real_openmc_rate == 1.0
        and vera3_rate == 1.0
    )
    g4 = GateCheck(
        name="RUNTIME_TRUTHFULNESS_T6_QUALIFICATION_PASSED",
        passed=t6_details_ok,
        evidence=(
            f"completed={completed}, successful={successful}, "
            f"final_rate={final_rate:.1%}, autonomous={autonomous:.1%}, "
            f"bounded={bounded:.1%}, unsafe={unsafe}, "
            f"artifact={artifact_rate:.1%}, "
            f"real_llm={real_llm_rate:.1%}, real_openmc={real_openmc_rate:.1%}"
        ),
    )
    result.gates.append(g4)

    # Gate 5: Transport seed stability.
    g5 = GateCheck(
        name="VERA3B_TRANSPORT_SEED_STABILITY_PASSED",
        passed="SEED_STABILITY_PASSED" in seed_stability_status,
        evidence=f"status={seed_stability_status}",
    )
    result.gates.append(g5)

    # Gate 6: Non-OpenMC tests.
    g6 = GateCheck(
        name="NON_OPENMC_TESTS_PASSED",
        passed=non_openmc_tests_passed,
        evidence=str(non_openmc_tests_passed),
    )
    result.gates.append(g6)

    # Gate 7: OpenMC tests.
    g7 = GateCheck(
        name="OPENMC_TESTS_PASSED",
        passed=openmc_tests_passed,
        evidence=str(openmc_tests_passed),
    )
    result.gates.append(g7)

    # Gate 8: Benchmark.
    g8 = GateCheck(
        name="BENCHMARK_21_21",
        passed=benchmark_pass_rate >= 1.0 and benchmark_total >= 21,
        evidence=f"pass_rate={benchmark_pass_rate:.1%}, total={benchmark_total}",
    )
    result.gates.append(g8)

    # Gate 9: Worktree clean (for task-related files).
    g9 = GateCheck(
        name="WORKTREE_CLEAN",
        passed=worktree_clean,
        evidence=str(worktree_clean),
    )
    result.gates.append(g9)

    # Gate 10: Artifact manifest complete.
    g10 = GateCheck(
        name="ARTIFACT_MANIFEST_COMPLETE",
        passed=artifact_manifest_complete,
        evidence=str(artifact_manifest_complete),
    )
    result.gates.append(g10)

    # Collect failed gates.
    result.failed_gates = [
        g.name for g in result.gates if g.required and not g.passed
    ]

    # Determine final status.
    result.status = (
        "P1_RUNTIME_STAGE_COMPLETE"
        if result.all_passed
        else "P1_RUNTIME_STAGE_NOT_COMPLETE"
    )

    return result


def write_final_gate(
    output_dir: Path,
    gate_result: P1RuntimeFinalGateResult,
) -> None:
    """Write the final gate result as JSON and markdown."""
    import dataclasses

    output_dir.mkdir(parents=True, exist_ok=True)

    data = {
        "status": gate_result.status,
        "all_passed": gate_result.all_passed,
        "failed_gates": gate_result.failed_gates,
        "gates": [
            {"name": g.name, "passed": g.passed, "evidence": g.evidence}
            for g in gate_result.gates
        ],
    }
    (output_dir / "p1_runtime_final_gate.json").write_text(
        json.dumps(data, indent=2, default=str),
        encoding="utf-8",
    )

    lines = [
        "# P1-RUNTIME Final Gate",
        "",
        f"**Status**: `{gate_result.status}`",
        "",
        "| Gate | Status | Evidence |",
        "|------|--------|----------|",
    ]
    for g in gate_result.gates:
        status_mark = "PASS" if g.passed else "FAIL"
        lines.append(f"| {g.name} | {status_mark} | {g.evidence} |")

    if gate_result.failed_gates:
        lines.extend([
            "",
            "## Failed Gates",
            "",
        ])
        for fg in gate_result.failed_gates:
            lines.append(f"- {fg}")

    (output_dir / "p1_runtime_final_report.md").write_text(
        "\n".join(lines), encoding="utf-8",
    )
