"""Fail-closed checkpoints for accepted plan gates.

The checkpoint is deliberately a small data contract.  It stores fingerprints
and audit telemetry, not provider reasoning or raw prompts.  A gate may only be
reused when every fingerprint supplied at lookup time is identical.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

from pydantic import Field

from openmc_agent.schemas import AgentBaseModel
from openmc_agent.structured_output import canonical_payload_hash

__all__ = [
    "CampaignGateCheckpoint",
    "CampaignCheckpointStore",
    "FactsActionCheckpoint",
    "checkpoint_fingerprint",
]


def checkpoint_fingerprint(value: Any) -> str:
    """Hash a canonical JSON-compatible value for checkpoint comparison."""

    return canonical_payload_hash(value)


class CampaignGateCheckpoint(AgentBaseModel):
    campaign_id: str
    gate_id: str
    status: str = "accepted"
    input_hash: str
    evidence_hash: str
    inventory_hash: str
    structured_output_policy_hash: str
    canonical_hashes: dict[str, str] = Field(default_factory=dict)
    evidence: dict[str, Any] = Field(default_factory=dict)
    inventory: dict[str, Any] = Field(default_factory=dict)
    structured_output_policy: dict[str, Any] = Field(default_factory=dict)
    accepted_at: str = ""

    @classmethod
    def create(
        cls,
        *,
        campaign_id: str,
        gate_id: str,
        input_payload: Any,
        evidence: Any,
        inventory: Any,
        structured_output_policy: Any,
        canonical_hashes: Mapping[str, str] | None = None,
        accepted_at: str = "",
    ) -> "CampaignGateCheckpoint":
        return cls(
            campaign_id=campaign_id,
            gate_id=gate_id,
            input_hash=checkpoint_fingerprint(input_payload),
            evidence_hash=checkpoint_fingerprint(evidence),
            inventory_hash=checkpoint_fingerprint(inventory),
            structured_output_policy_hash=checkpoint_fingerprint(structured_output_policy),
            canonical_hashes=dict(canonical_hashes or {}),
            evidence=evidence if isinstance(evidence, dict) else {"value": evidence},
            inventory=inventory if isinstance(inventory, dict) else {"value": inventory},
            structured_output_policy=(
                structured_output_policy
                if isinstance(structured_output_policy, dict)
                else {"value": structured_output_policy}
            ),
            accepted_at=accepted_at,
        )

    def matches(
        self,
        *,
        input_payload: Any,
        evidence: Any,
        inventory: Any,
        structured_output_policy: Any,
        canonical_hashes: Mapping[str, str] | None = None,
    ) -> bool:
        return (
            self.status == "accepted"
            and self.input_hash == checkpoint_fingerprint(input_payload)
            and self.evidence_hash == checkpoint_fingerprint(evidence)
            and self.inventory_hash == checkpoint_fingerprint(inventory)
            and self.structured_output_policy_hash
            == checkpoint_fingerprint(structured_output_policy)
            and self.canonical_hashes == dict(canonical_hashes or {})
        )


class FactsActionCheckpoint(AgentBaseModel):
    """Durable action-level Facts progress used after provider interruption."""

    action_id: str
    tool_name: str
    arguments_hash: str
    status: str = "pending"  # pending | completed | provider_timeout | skipped_after_coverage_complete
    billed_call_count: int = 0
    provider_deadline: str = ""
    unfinished: bool = True


class CampaignCheckpointStore:
    """JSON-backed atomic store for gate and Facts action checkpoints."""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self._data: dict[str, Any] = {"gates": {}, "facts_actions": {}}
        self.load()

    def load(self) -> None:
        try:
            self._data = json.loads(self.path.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            self._data = {"gates": {}, "facts_actions": {}}

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp.write_text(json.dumps(self._data, indent=2, sort_keys=True), encoding="utf-8")
        tmp.replace(self.path)

    def accept_gate(self, checkpoint: CampaignGateCheckpoint) -> None:
        gates = self._data.setdefault("gates", {})
        gates[checkpoint.gate_id] = checkpoint.model_dump(mode="json")
        self.save()

    def lookup_gate(self, gate_id: str, **fingerprints: Any) -> CampaignGateCheckpoint | None:
        raw = self._data.get("gates", {}).get(gate_id)
        if not raw:
            return None
        checkpoint = CampaignGateCheckpoint.model_validate(raw)
        if checkpoint.matches(**fingerprints):
            return checkpoint
        # Mismatch is deliberately not treated as a cache miss: callers need a
        # deterministic signal to restart from the affected gate.
        raise ValueError(f"campaign checkpoint fingerprint mismatch for gate {gate_id}")

    def record_facts_action(self, checkpoint: FactsActionCheckpoint) -> None:
        actions = self._data.setdefault("facts_actions", {})
        actions[checkpoint.action_id] = checkpoint.model_dump(mode="json")
        self.save()

    def facts_action(self, action_id: str) -> FactsActionCheckpoint | None:
        raw = self._data.get("facts_actions", {}).get(action_id)
        return FactsActionCheckpoint.model_validate(raw) if raw else None
