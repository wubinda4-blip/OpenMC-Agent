"""Fail-closed checkpoints for accepted plan gates.

The checkpoint is deliberately a small data contract.  It stores fingerprints
and audit telemetry, not provider reasoning or raw prompts.  A gate may only be
reused when every fingerprint supplied at lookup time is identical.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping, Literal

from pydantic import Field, model_validator

from openmc_agent.schemas import AgentBaseModel
from openmc_agent.structured_output import canonical_payload_hash

__all__ = [
    "CampaignGateCheckpoint",
    "CampaignCheckpointStore",
    "CampaignStateSnapshot",
    "FactsActionCheckpoint",
    "GATE_REPLAY_SNAPSHOT_SCHEMA_VERSION",
    "BOUNDARY_PATCH_MATERIALS",
    "BOUNDARY_PATCH_UNIVERSES",
    "BOUNDARY_GATE_FACTS",
    "BOUNDARY_GATE_MATERIAL_UNIVERSE",
    "ACCEPTED_BOUNDARIES",
    "checkpoint_fingerprint",
]


def checkpoint_fingerprint(value: Any) -> str:
    """Hash a canonical JSON-compatible value for checkpoint comparison."""

    return canonical_payload_hash(value)


_ACTION_PROGRESS_SENSITIVE_KEY_FRAGMENTS: tuple[str, ...] = (
    "raw_text",
    "raw_output",
    "raw_response",
    "prompt",
    "reasoning",
    "api_key",
    "token",
    "secret",
    "password",
    "credential",
    "authorization",
)


def _progress_has_sensitive_key(value: Any) -> bool:
    if isinstance(value, Mapping):
        for key, item in value.items():
            if any(fragment in str(key).lower() for fragment in _ACTION_PROGRESS_SENSITIVE_KEY_FRAGMENTS):
                return True
            if _progress_has_sensitive_key(item):
                return True
    elif isinstance(value, list):
        return any(_progress_has_sensitive_key(item) for item in value)
    return False


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
    patch_type: str = ""
    tool_name: str
    arguments_hash: str
    status: Literal["pending", "completed", "provider_timeout", "skipped_after_coverage_complete"] = "pending"
    billed_call_count: int = 0
    provider_deadline: str = ""
    unfinished: bool = True

    context_hash: str = ""
    campaign_fingerprints: dict[str, str] = Field(default_factory=dict)
    normalized_progress: dict[str, Any] = Field(default_factory=dict)
    progress_hash: str = ""

    @model_validator(mode="after")
    def _validate_progress_integrity(self) -> "FactsActionCheckpoint":
        if _progress_has_sensitive_key(self.normalized_progress):
            raise ValueError("Facts action progress contains sensitive/raw fields")
        expected = checkpoint_fingerprint(self.normalized_progress)
        if self.progress_hash and self.progress_hash != expected:
            raise ValueError("Facts action progress_hash mismatch")
        if self.normalized_progress and not self.progress_hash:
            object.__setattr__(self, "progress_hash", expected)
        if self.status == "completed" and not self.normalized_progress:
            raise ValueError("completed Facts action requires normalized_progress")
        return self


# ---------------------------------------------------------------------------
# Phase 8C Step 3B: accepted-boundary state snapshots
# ---------------------------------------------------------------------------

# Schema version for the sanitized snapshot contract.  Bumped whenever the
# stored shape changes in a backwards-incompatible way.  Resume validation
# rejects a snapshot whose version does not match.
GATE_REPLAY_SNAPSHOT_SCHEMA_VERSION: str = "1.0"

BOUNDARY_PATCH_MATERIALS: str = "patch:materials"
BOUNDARY_PATCH_UNIVERSES: str = "patch:universes"
BOUNDARY_GATE_FACTS: str = "gate:facts"
BOUNDARY_GATE_MATERIAL_UNIVERSE: str = "gate:material_universe"

# Ordered from earliest to latest accepted boundary.  Hydration selects the
# latest *valid* snapshot, i.e. the one closest to the end of this list.
ACCEPTED_BOUNDARIES: tuple[str, ...] = (
    BOUNDARY_GATE_FACTS,
    BOUNDARY_PATCH_MATERIALS,
    BOUNDARY_PATCH_UNIVERSES,
    BOUNDARY_GATE_MATERIAL_UNIVERSE,
)


class CampaignStateSnapshot(AgentBaseModel):
    """A full sanitized ``PlanBuildState`` snapshot persisted at an accepted
    boundary.

    The snapshot stores only the sanitized plan build state plus the
    fingerprints required to validate it on resume.  It never stores raw
    prompts, provider reasoning text, or un-normalized LLM outputs.
    """

    campaign_id: str
    boundary: str
    schema_version: str = GATE_REPLAY_SNAPSHOT_SCHEMA_VERSION
    sequence: int = 0
    state_hash: str
    plan_build_state: dict[str, Any]
    requirement_hash: str = ""
    input_hash: str = ""
    policy_hash: str = ""
    git_sha: str = ""
    structured_output_policy_hash: str = ""
    accepted_at: str = ""

    @property
    def boundary_index(self) -> int:
        try:
            return ACCEPTED_BOUNDARIES.index(self.boundary)
        except ValueError:
            return -1


class CampaignCheckpointStore:
    """JSON-backed atomic store for gate and Facts action checkpoints."""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self._data: dict[str, Any] = {"gates": {}, "facts_actions": {}, "state_snapshots": []}
        self.load()

    def load(self) -> None:
        try:
            self._data = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            self._data = {"gates": {}, "facts_actions": {}, "state_snapshots": []}
        except (json.JSONDecodeError, OSError) as exc:
            raise ValueError(f"invalid campaign checkpoint store: {self.path}") from exc
        if not isinstance(self._data, dict):
            raise ValueError(f"campaign checkpoint store must be an object: {self.path}")
        self._data.setdefault("state_snapshots", [])

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

    def restore_facts_action(
        self,
        action_id: str,
        *,
        patch_type: str,
        tool_name: str,
        arguments_hash: str,
        context_hash: str,
        campaign_fingerprints: Mapping[str, str],
    ) -> FactsActionCheckpoint | None:
        """Return a completed, hash-valid action or None for rerunnable work."""

        checkpoint = self.facts_action(action_id)
        if checkpoint is None or checkpoint.status != "completed" or checkpoint.unfinished:
            return None
        expected = {
            "patch_type": patch_type,
            "tool_name": tool_name,
            "arguments_hash": arguments_hash,
            "context_hash": context_hash,
            "campaign_fingerprints": dict(campaign_fingerprints),
        }
        actual = {
            "patch_type": checkpoint.patch_type,
            "tool_name": checkpoint.tool_name,
            "arguments_hash": checkpoint.arguments_hash,
            "context_hash": checkpoint.context_hash,
            "campaign_fingerprints": checkpoint.campaign_fingerprints,
        }
        if actual != expected:
            raise ValueError(f"Facts action checkpoint fingerprint drift: {action_id}")
        return checkpoint

    # ------------------------------------------------------------------
    # Phase 8C Step 3B: accepted-boundary state snapshots
    # ------------------------------------------------------------------

    def accept_state_snapshot(self, snapshot: CampaignStateSnapshot) -> None:
        """Persist a sanitized state snapshot atomically.

        Snapshots are append-only and ordered by ``sequence``.  Resume
        consumers hydrate from the latest *valid* entry.
        """
        snapshots = self._data.setdefault("state_snapshots", [])
        snapshots.append(snapshot.model_dump(mode="json"))
        self.save()

    def state_snapshots(self) -> list[CampaignStateSnapshot]:
        raw_list = self._data.get("state_snapshots", [])
        if not isinstance(raw_list, list):
            raise ValueError("campaign checkpoint state_snapshots must be a list")
        out: list[CampaignStateSnapshot] = []
        for raw in raw_list:
            if not isinstance(raw, dict):
                raise ValueError("campaign checkpoint contains a malformed snapshot")
            try:
                out.append(CampaignStateSnapshot.model_validate(raw))
            except Exception as exc:
                raise ValueError("campaign checkpoint contains an invalid snapshot") from exc
        return out

    def latest_state_snapshot(self) -> CampaignStateSnapshot | None:
        snaps = self.state_snapshots()
        if not snaps:
            return None
        snaps.sort(key=lambda s: (s.boundary_index, s.sequence, s.accepted_at))
        return snaps[-1]

    def hydrate_accepted_state(
        self,
        *,
        requirement_hash: str,
        input_hash: str,
        policy_hash: str,
        git_sha: str,
        structured_output_policy_hash: str,
    ) -> CampaignStateSnapshot | None:
        """Return the latest *valid* accepted boundary snapshot.

        Validates schema version, state integrity, and all campaign
        fingerprints.  ``None`` means that no snapshot exists.  A present
        but invalid or drifted snapshot raises so callers cannot mistake
        corruption for a cache miss.
        """
        snaps = self.state_snapshots()
        if not snaps:
            return None
        snaps.sort(key=lambda s: (s.boundary_index, s.sequence, s.accepted_at))
        for snap in snaps:
            if snap.boundary_index < 0:
                raise ValueError(f"unknown accepted boundary: {snap.boundary}")
            if snap.schema_version != GATE_REPLAY_SNAPSHOT_SCHEMA_VERSION:
                raise ValueError(f"unsupported snapshot schema version: {snap.schema_version}")
            expected_hash = checkpoint_fingerprint(snap.plan_build_state)
            if snap.state_hash != expected_hash:
                raise ValueError(
                    f"state_hash mismatch for snapshot {snap.campaign_id}:{snap.boundary}"
                )
            try:
                from openmc_agent.plan_builder.state import PlanBuildState

                PlanBuildState.model_validate(snap.plan_build_state)
            except Exception as exc:
                raise ValueError(
                    f"invalid PlanBuildState for snapshot {snap.campaign_id}:{snap.boundary}"
                ) from exc
            if any(
                actual != expected
                for actual, expected in (
                    (snap.requirement_hash, requirement_hash),
                    (snap.input_hash, input_hash),
                    (snap.policy_hash, policy_hash),
                    (snap.git_sha, git_sha),
                    (snap.structured_output_policy_hash, structured_output_policy_hash),
                )
            ):
                raise ValueError(
                    f"campaign resume fingerprint drift for snapshot "
                    f"{snap.campaign_id}:{snap.boundary}"
                )
        return snaps[-1]
