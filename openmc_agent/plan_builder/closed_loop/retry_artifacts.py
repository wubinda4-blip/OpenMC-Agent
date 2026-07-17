"""Phase-3B retry artifact writer.

Persists the typed retry protocol artifacts so every step can be audited:

  retry_request_000.json
  retry_execution_plan_000.json
  retry_candidate_000.json
  retry_acceptance_000.json
  retry_owner_commit_000.json
  retry_invalidation_000.json
  retry_gate_invalidation_000.json
  retry_gate_replay_000.json
  retry_reclassification_000.json
  retry_round_000.json
  retry_outcome.json
  retry_summary.json

No secrets, API keys, or hidden reasoning are ever written.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from openmc_agent.plan_builder.state import PlanBuildState


class RetryArtifactWriter:
    def __init__(self, base_dir: str | Path | None = None) -> None:
        self.base_dir = Path(base_dir) / "retry_artifacts" if base_dir else None
        if self.base_dir is not None:
            self.base_dir.mkdir(parents=True, exist_ok=True)
        self._counter = 0

    def _path(self, name: str) -> Path | None:
        if self.base_dir is None:
            return None
        return self.base_dir / name

    def write(self, name: str, payload: Any) -> Path | None:
        path = self._path(name)
        if path is None:
            return None
        if hasattr(payload, "model_dump"):
            data = payload.model_dump(mode="json")
        elif isinstance(payload, (dict, list)):
            data = payload
        else:
            data = {"value": str(payload)}
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        return path

    def write_text(self, name: str, text: str) -> Path | None:
        path = self._path(name)
        if path is None:
            return None
        path.write_text(text, encoding="utf-8")
        return path

    def write_round(self, round_index: int, state: PlanBuildState) -> None:
        """Write the complete artifact set for a single retry round."""
        suffix = f"{round_index:03d}"
        rounds = state.plan_retry_rounds
        if round_index >= len(rounds):
            return
        record = rounds[round_index]
        self.write(f"retry_round_{suffix}.json", record)
        self.write(f"retry_request_{suffix}.json", record.request)
        if record.execution_plan:
            self.write(f"retry_execution_plan_{suffix}.json", record.execution_plan)
        if record.candidate_hashes:
            self.write(f"retry_candidate_{suffix}.json", {"candidate_hashes": record.candidate_hashes, "owner_hashes_after": record.owner_hashes_after})
        if record.checks_executed:
            self.write(f"retry_acceptance_{suffix}.json", {"checks_executed": record.checks_executed, "checks_passed": record.checks_passed, "checks_failed": record.checks_failed})
        if record.owner_hashes_after:
            self.write(f"retry_owner_commit_{suffix}.json", {"owner_hashes_after": record.owner_hashes_after, "owners": list(record.owner_hashes_after.keys())})
        if record.invalidated_patch_types:
            self.write(f"retry_invalidation_{suffix}.json", {"invalidated_patch_types": record.invalidated_patch_types, "regenerated_patch_types": record.regenerated_patch_types})
        if record.gates_invalidated:
            self.write(f"retry_gate_invalidation_{suffix}.json", {"gates_invalidated": [g.value for g in record.gates_invalidated]})
        if record.gates_replayed:
            self.write(f"retry_gate_replay_{suffix}.json", {"gates_replayed": [g.value for g in record.gates_replayed]})
        if record.reclassification:
            self.write(f"retry_reclassification_{suffix}.json", {"classification": record.reclassification, "resolved": record.resolved_issue_codes, "remaining": record.remaining_issue_codes, "new": record.new_issue_codes})

    def write_summary(self, state: PlanBuildState) -> None:
        summary = {
            "total_rounds": len(state.plan_retry_rounds),
            "total_requests": len(state.plan_retry_requests),
            "pending_requests": list(state.plan_retry_pending_request_ids),
            "budget": state.plan_retry_budget,
            "owner_regenerations": state.plan_retry_owner_regenerations,
            "gate_invalidation_counts": state.plan_retry_gate_invalidation_counts,
            "gate_replay_attempt_counts": state.plan_retry_gate_replay_attempt_counts,
            "gate_replay_success_counts": state.plan_retry_gate_replay_success_counts,
            "cycle_trace": state.plan_retry_cycle_trace,
        }
        self.write("retry_summary.json", summary)
        if state.plan_retry_outcome:
            self.write("retry_outcome.json", state.plan_retry_outcome)

    def write_all(self, state: PlanBuildState) -> None:
        for i in range(len(state.plan_retry_rounds)):
            self.write_round(i, state)
        self.write_summary(state)


__all__ = ["RetryArtifactWriter"]
