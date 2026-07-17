"""Best-effort JSON-only artifact writing for the Phase-0 plan loop."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .models import PLAN_CLOSED_LOOP_CONTRACT_VERSION
from .policy import canonical_gate_order, gate_definition


def _payload(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    return value


class PlanLoopArtifactWriter:
    """Write stable current-state artifacts; failures are reported, never raised."""

    def __init__(self, output_dir: str | Path | None, artifact_subdir: str = "plan_closed_loop"):
        self.root = Path(output_dir) / "incremental" / artifact_subdir if output_dir is not None else None

    def _write(self, filename: str, value: Any) -> str | None:
        if self.root is None:
            return None
        try:
            self.root.mkdir(parents=True, exist_ok=True)
            path = self.root / filename
            path.write_text(json.dumps(_payload(value), ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
            return str(path)
        except Exception:
            return None

    def write_plan_loop_policy(self, policy: Any) -> str | None:
        return self._write("plan_loop_policy.json", _payload(policy))

    def write_plan_loop_state(self, state: Any) -> str | None:
        return self._write("plan_loop_state.json", _payload(state))

    def write_gate_stage(self, stage: Any) -> str | None:
        return self._write(f"gate_{stage.gate_id.value}.json", _payload(stage))

    def write_evidence_pack(self, pack: Any) -> str | None:
        return self._write(f"evidence_{pack.gate_id.value}.json", _payload(pack))

    def write_findings(self, findings: Any) -> str | None:
        return self._write("findings.json", _payload(findings))

    def write_decision(self, decision: Any) -> str | None:
        return self._write("decision.json", _payload(decision))

    def write_human_questions(self, questions: Any) -> str | None:
        return self._write("human_questions.json", _payload(questions))

    def write_gate_registry(self) -> str | None:
        return self._write("gate_registry.json", {
            "contract_version": PLAN_CLOSED_LOOP_CONTRACT_VERSION,
            "gates": [gate_definition(gate) for gate in canonical_gate_order()],
        })

    def write_plan_loop_summary(self, summary: dict[str, Any]) -> str | None:
        return self._write("plan_loop_summary.json", {
            "contract_version": PLAN_CLOSED_LOOP_CONTRACT_VERSION, **summary,
        })


def write_plan_loop_policy(writer: PlanLoopArtifactWriter, policy: Any) -> str | None:
    return writer.write_plan_loop_policy(policy)


def write_plan_loop_state(writer: PlanLoopArtifactWriter, state: Any) -> str | None:
    return writer.write_plan_loop_state(state)


def write_gate_stage(writer: PlanLoopArtifactWriter, stage: Any) -> str | None:
    return writer.write_gate_stage(stage)


def write_evidence_pack(writer: PlanLoopArtifactWriter, pack: Any) -> str | None:
    return writer.write_evidence_pack(pack)


def write_findings(writer: PlanLoopArtifactWriter, findings: Any) -> str | None:
    return writer.write_findings(findings)


def write_decision(writer: PlanLoopArtifactWriter, decision: Any) -> str | None:
    return writer.write_decision(decision)


def write_human_questions(writer: PlanLoopArtifactWriter, questions: Any) -> str | None:
    return writer.write_human_questions(questions)


def write_plan_loop_summary(writer: PlanLoopArtifactWriter, summary: dict[str, Any]) -> str | None:
    return writer.write_plan_loop_summary(summary)
