"""Versioned GateReplayBundle + replay engine (Phase 8C Step 3B).

The replay engine allows re-evaluating a previously-accepted Facts or
Material-Universe gate from a **sanitized** bundle, without touching
production checkpoints.  Three modes are supported:

* ``preflight`` — pure deterministic validation, **no LLM**.  Checks the
  bundle schema version, upstream accepted status, canonical
  input/policy/bundle hashes, complete normalized state and rejects any
  sensitive / raw fields.
* ``recorded-review`` — replay the **normalized** recorded review outputs
  captured in the bundle against the current state.  No LLM call; the
  recorded outputs are re-normalized and compared.
* ``live-review`` — invoke the **target reviewer only** (the supplied
  provider/client) against the bundle's normalized state.  Production
  checkpoints are never mutated.

Safety rules (from AGENTS.md):

* Never mutate production checkpoints.
* Reject sensitive / raw fields (prompts, reasoning, raw responses).
* Validate schema version, upstream accepted status and canonical hashes.
* Output sanitized JSON only.
"""

from __future__ import annotations

import json
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Literal, Mapping

from pydantic import Field, field_validator, model_validator

from openmc_agent.schemas import AgentBaseModel
from openmc_agent.structured_output import canonical_payload_hash

from .campaign_checkpoint import (
    GATE_REPLAY_SNAPSHOT_SCHEMA_VERSION,
)
from .state_snapshot import sanitize_plan_build_state

__all__ = [
    "GATE_REPLAY_BUNDLE_SCHEMA_VERSION",
    "GateReplayMode",
    "GateReplayBundle",
    "GateReplayResult",
    "GateReplayIssue",
    "load_gate_replay_bundle",
    "run_gate_replay",
    "SENSITIVE_FIELD_NAMES",
]

# The bundle schema version is distinct from the snapshot schema version so
# the replay contract can evolve independently of checkpoint storage.
GATE_REPLAY_BUNDLE_SCHEMA_VERSION: str = "1.0"

# Default live-review timeout (seconds).  Documented in the CLI help.
DEFAULT_LIVE_REVIEW_TIMEOUT_SECONDS: int = 1800

# Field names that must never appear in a bundle.  The validator rejects
# any of these at any nesting depth.
SENSITIVE_FIELD_NAMES: frozenset[str] = frozenset(
    {
        "raw_text",
        "raw_output",
        "raw_response",
        "prompt_text",
        "prompt",
        "reasoning",
        "reasoning_content",
        "api_key",
        "token",
        "secret",
        "password",
        "credential",
        "authorization",
    }
)


class GateReplayMode(str, Enum):
    PREFLIGHT = "preflight"
    RECORDED_REVIEW = "recorded-review"
    LIVE_REVIEW = "live-review"


def _find_sensitive_keys(value: Any, prefix: str = "") -> list[str]:
    """Return dotted key paths for any sensitive field found at any depth."""
    found: list[str] = []
    if isinstance(value, Mapping):
        for key, item in value.items():
            path = f"{prefix}.{key}" if prefix else str(key)
            if str(key).lower() in SENSITIVE_FIELD_NAMES or any(
                frag in str(key).lower() for frag in SENSITIVE_FIELD_NAMES
            ):
                found.append(path)
            found.extend(_find_sensitive_keys(item, path))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            path = f"{prefix}[{index}]"
            found.extend(_find_sensitive_keys(item, path))
    return found


class GateReplayIssue(AgentBaseModel):
    """A single validation/replay issue."""

    code: str
    severity: Literal["error", "warning", "info"] = "error"
    message: str
    path: str = ""


class GateReplayBundle(AgentBaseModel):
    """A versioned, sanitized bundle for replaying a single gate.

    The bundle captures the *normalized* state at the boundary plus the
    canonical hashes required to validate it.  It never stores raw
    prompts, reasoning or un-normalized LLM outputs.
    """

    bundle_schema_version: str = GATE_REPLAY_BUNDLE_SCHEMA_VERSION
    snapshot_schema_version: str = GATE_REPLAY_SNAPSHOT_SCHEMA_VERSION
    gate_id: str
    campaign_id: str = ""
    upstream_accepted: dict[str, bool] = Field(default_factory=dict)
    canonical_hashes: dict[str, str] = Field(default_factory=dict)
    normalized_state: dict[str, Any] = Field(default_factory=dict)
    recorded_reviews: list[dict[str, Any]] = Field(default_factory=list)
    recorded_inventory: dict[str, Any] = Field(default_factory=dict)
    bundle_hash: str = ""
    created_at: str = ""

    @model_validator(mode="after")
    def _validate_contract(self) -> "GateReplayBundle":
        if self.bundle_schema_version != GATE_REPLAY_BUNDLE_SCHEMA_VERSION:
            raise ValueError(
                f"unsupported bundle schema version: {self.bundle_schema_version} "
                f"(expected {GATE_REPLAY_BUNDLE_SCHEMA_VERSION})"
            )
        if self.gate_id not in ("facts", "material_universe"):
            raise ValueError(
                f"gate replay only supports 'facts' and 'material_universe', "
                f"got {self.gate_id!r}"
            )
        # Reject sensitive fields anywhere in the bundle payload.
        sensitive = []
        sensitive.extend(_find_sensitive_keys(self.normalized_state, "normalized_state"))
        sensitive.extend(_find_sensitive_keys(self.recorded_reviews, "recorded_reviews"))
        sensitive.extend(_find_sensitive_keys(self.recorded_inventory, "recorded_inventory"))
        if sensitive:
            raise ValueError(
                "bundle contains sensitive/raw fields: " + ", ".join(sensitive[:10])
            )
        # Recompute and verify bundle hash if present.
        if self.bundle_hash:
            recomputed = self.compute_bundle_hash()
            if self.bundle_hash != recomputed:
                raise ValueError("bundle_hash does not match recomputed value")
        return self

    def compute_bundle_hash(self) -> str:
        """Compute the canonical hash of the bundle's semantic content."""
        payload = {
            "gate_id": self.gate_id,
            "campaign_id": self.campaign_id,
            "snapshot_schema_version": self.snapshot_schema_version,
            "upstream_accepted": self.upstream_accepted,
            "canonical_hashes": self.canonical_hashes,
            "normalized_state_hash": canonical_payload_hash(self.normalized_state),
            "recorded_reviews_hash": canonical_payload_hash(self.recorded_reviews),
            "recorded_inventory_hash": canonical_payload_hash(self.recorded_inventory),
        }
        return canonical_payload_hash(payload)


class GateReplayResult(AgentBaseModel):
    """Outcome of a gate replay run."""

    mode: str
    gate_id: str
    ok: bool = False
    issues: list[GateReplayIssue] = Field(default_factory=list)
    upstream_validated: bool = False
    hashes_validated: bool = False
    state_complete: bool = False
    sensitive_fields_rejected: bool = True
    recorded_review_replayed: bool = False
    live_review_invoked: bool = False
    review_output: dict[str, Any] | None = None
    bundle_hash: str = ""
    summary: str = ""

    def to_sanitized_dict(self) -> dict[str, Any]:
        """Return a sanitized JSON dict safe for CLI output."""
        data = self.model_dump(mode="json")
        # Defensive: strip any sensitive keys from the review output.
        from .state_snapshot import _sanitize_node

        if data.get("review_output") is not None:
            data["review_output"] = _sanitize_node(data["review_output"])
        return data


# ---------------------------------------------------------------------------
# Bundle loading
# ---------------------------------------------------------------------------


def load_gate_replay_bundle(path: str | Path) -> GateReplayBundle:
    """Load a GateReplayBundle from a JSON file.

    Raises ``ValueError`` on schema-version mismatch, hash drift or
    sensitive-field presence (the model validator handles this).
    """
    p = Path(path)
    raw = json.loads(p.read_text(encoding="utf-8"))
    return GateReplayBundle.model_validate(raw)


# ---------------------------------------------------------------------------
# Replay engine
# ---------------------------------------------------------------------------


def _validate_upstream(bundle: GateReplayBundle) -> tuple[bool, list[GateReplayIssue]]:
    issues: list[GateReplayIssue] = []
    if bundle.gate_id == "material_universe":
        facts_accepted = bundle.upstream_accepted.get("facts", False)
        if not facts_accepted:
            issues.append(
                GateReplayIssue(
                    code="gate_replay.upstream_not_accepted",
                    message="material_universe replay requires upstream 'facts' gate to be accepted",
                    path="upstream_accepted.facts",
                )
            )
    elif bundle.gate_id == "facts":
        # Facts is the root gate; no upstream dependency.
        pass
    return (not issues, issues)


def _validate_hashes(bundle: GateReplayBundle) -> tuple[bool, list[GateReplayIssue]]:
    issues: list[GateReplayIssue] = []
    required = {"input", "policy"}
    missing = [key for key in required if key not in bundle.canonical_hashes]
    if missing:
        issues.append(
            GateReplayIssue(
                code="gate_replay.missing_canonical_hash",
                message=f"canonical_hashes missing required keys: {missing}",
                path="canonical_hashes",
            )
        )
    # Verify bundle hash integrity.
    recomputed = bundle.compute_bundle_hash()
    if bundle.bundle_hash and bundle.bundle_hash != recomputed:
        issues.append(
            GateReplayIssue(
                code="gate_replay.bundle_hash_drift",
                message="bundle_hash does not match the recomputed canonical hash",
                path="bundle_hash",
            )
        )
    return (not issues, issues)


def _validate_state_complete(
    bundle: GateReplayBundle,
) -> tuple[bool, list[GateReplayIssue]]:
    issues: list[GateReplayIssue] = []
    state = bundle.normalized_state
    if not isinstance(state, Mapping):
        issues.append(
            GateReplayIssue(
                code="gate_replay.state_not_object",
                message="normalized_state must be a JSON object",
                path="normalized_state",
            )
        )
        return (False, issues)
    if not state:
        issues.append(
            GateReplayIssue(
                code="gate_replay.state_empty",
                message="normalized_state is empty",
                path="normalized_state",
            )
        )
    try:
        _reconstruct_plan_build_state_from_payload(state)
    except Exception as exc:
        issues.append(GateReplayIssue(
            code="gate_replay.state_reconstruction_failed",
            message=f"normalized_state is not a valid PlanBuildState: {type(exc).__name__}",
            path="normalized_state",
        ))
    return (not issues, issues)


def _reconstruct_plan_build_state_from_payload(payload: Mapping[str, Any]) -> Any:
    from openmc_agent.plan_builder.state import PlanBuildState

    return PlanBuildState.model_validate(payload.get("plan_build_state", payload))


def _reconstruct_plan_build_state(bundle: GateReplayBundle) -> Any:
    return _reconstruct_plan_build_state_from_payload(bundle.normalized_state)


def _replay_policy(bundle: GateReplayBundle) -> Any:
    from .models import PlanClosedLoopPolicy, PlanGateId, PlanLoopMode

    gate = PlanGateId.FACTS if bundle.gate_id == "facts" else PlanGateId.MATERIAL_UNIVERSE
    return PlanClosedLoopPolicy(
        mode=PlanLoopMode.CONTROLLED,
        plan_gates=[gate],
        gate_enabled={gate: True},
        material_universe_review_mode="controlled" if gate is PlanGateId.MATERIAL_UNIVERSE else "off",
        facts_review_stage_split=False,
    )


def _normalized_review_output(result: Any) -> dict[str, Any]:
    payload = result.model_dump(mode="json") if hasattr(result, "model_dump") else dict(result)
    payload.pop("raw_outputs", None)
    payload.pop("call_metadata", None)
    return sanitize_plan_build_state(payload)


def _run_production_review(
    bundle: GateReplayBundle,
    *,
    reviewer_client: Callable[[str], Any],
    recorded: bool,
) -> tuple[Any, list[GateReplayIssue]]:
    from .facts_evidence import build_facts_evidence_packs
    from .facts_reviewer import run_facts_review
    from .material_universe_evidence import build_material_universe_evidence_pack
    from .material_universe_review_split import run_material_universe_review_split

    state = _reconstruct_plan_build_state(bundle)
    policy = _replay_policy(bundle)
    if bundle.gate_id == "facts":
        facts = next(
            (env.content for env in state.patches.values() if env.patch_type == "facts"),
            None,
        )
        if facts is None:
            raise ValueError("Facts replay state has no facts patch")
        packs = build_facts_evidence_packs(
            requirement_text=state.requirement_text,
            facts_patch=facts,
            confirmed_facts=state.confirmed_facts,
            planning_metadata=state.metadata,
            policy=policy,
        )
        if recorded:
            recorded_review = bundle.recorded_reviews[0]
            calls = iter(packs)
            def recorded_reviewer(_prompt: str) -> Any:
                pack = next(calls)
                payload = dict(recorded_review)
                payload.setdefault("review_status", "complete")
                payload["reviewed_evidence_hashes"] = [
                    excerpt.evidence_hash for excerpt in pack.source_excerpts
                ]
                payload.setdefault("coverage_summary", {})
                return payload
            reviewer_client = recorded_reviewer
        return run_facts_review(
            evidence_packs=packs,
            reviewer_client=reviewer_client,
            state=state,
            policy=policy,
        ), []
    pack = build_material_universe_evidence_pack(state=state, policy=policy)
    if recorded:
        recorded_review = bundle.recorded_reviews[0]
        scope_ids = {
            "materials": [row.row_id for row in pack.contract_matrix.rows if row.row_kind in {"source_material_coverage", "fuel_variant_identity"}],
            "universes": [row.row_id for row in pack.contract_matrix.rows if row.row_kind == "required_universe_material_structure"],
            "binding": [row.row_id for row in pack.contract_matrix.rows if row.row_kind == "material_to_cell_binding"],
        }
        calls = iter(scope_ids.items())
        def recorded_reviewer(_prompt: str) -> Any:
            scope, ids = next(calls)
            payload = dict(recorded_review)
            payload.setdefault("review_status", "complete")
            payload["reviewed_ids"] = ids
            return payload
        reviewer_client = recorded_reviewer
    return run_material_universe_review_split(
        evidence_pack=pack,
        reviewer_client=reviewer_client,
        state=state,
        policy=policy,
    ), []


def _validate_no_sensitive(
    bundle: GateReplayBundle,
) -> tuple[bool, list[GateReplayIssue]]:
    issues: list[GateReplayIssue] = []
    sensitive = []
    sensitive.extend(_find_sensitive_keys(bundle.normalized_state, "normalized_state"))
    sensitive.extend(_find_sensitive_keys(bundle.recorded_reviews, "recorded_reviews"))
    sensitive.extend(_find_sensitive_keys(bundle.recorded_inventory, "recorded_inventory"))
    for path in sensitive:
        issues.append(
            GateReplayIssue(
                code="gate_replay.sensitive_field_present",
                severity="error",
                message=f"sensitive/raw field present in bundle: {path}",
                path=path,
            )
        )
    return (not sensitive, issues)


def run_gate_replay(
    bundle: GateReplayBundle,
    *,
    mode: GateReplayMode | str = GateReplayMode.PREFLIGHT,
    reviewer_client: Callable[[str], str] | None = None,
    live_review_timeout: int = DEFAULT_LIVE_REVIEW_TIMEOUT_SECONDS,
    current_input_hash: str | None = None,
    current_policy_hash: str | None = None,
) -> GateReplayResult:
    """Run a gate replay against ``bundle``.

    Parameters
    ----------
    bundle
        The sanitized :class:`GateReplayBundle` to replay.
    mode
        One of ``preflight``, ``recorded-review``, ``live-review``.
    reviewer_client
        Required for ``live-review``; ignored otherwise.  Must be a
        callable ``(prompt: str) -> str``.
    live_review_timeout
        Documented timeout for the live-review mode.  The caller is
        responsible for enforcing it.

    Returns
    -------
    GateReplayResult
        Sanitized result.  Production checkpoints are never mutated.
    """
    if isinstance(mode, str):
        mode = GateReplayMode(mode)
    issues: list[GateReplayIssue] = []
    state_complete = False

    # Common preflight checks (always run).
    upstream_ok, upstream_issues = _validate_upstream(bundle)
    issues.extend(upstream_issues)
    hashes_ok, hash_issues = _validate_hashes(bundle)
    issues.extend(hash_issues)
    state_ok, state_issues = _validate_state_complete(bundle)
    issues.extend(state_issues)
    state_complete = state_ok
    sensitive_ok, sensitive_issues = _validate_no_sensitive(bundle)
    issues.extend(sensitive_issues)
    if current_input_hash is not None and bundle.canonical_hashes.get("input") != current_input_hash:
        issues.append(GateReplayIssue(code="gate_replay.input_hash_drift", message="canonical input hash drift", path="canonical_hashes.input"))
        hashes_ok = False
    if current_policy_hash is not None and bundle.canonical_hashes.get("policy") != current_policy_hash:
        issues.append(GateReplayIssue(code="gate_replay.policy_hash_drift", message="canonical policy hash drift", path="canonical_hashes.policy"))
        hashes_ok = False

    result = GateReplayResult(
        mode=mode.value,
        gate_id=bundle.gate_id,
        issues=issues,
        upstream_validated=upstream_ok,
        hashes_validated=hashes_ok,
        state_complete=state_ok,
        sensitive_fields_rejected=sensitive_ok,
        bundle_hash=bundle.bundle_hash,
    )

    # Preflight stops here — no LLM, no recorded-output replay.
    if mode is GateReplayMode.PREFLIGHT:
        if state_ok:
            try:
                state = _reconstruct_plan_build_state(bundle)
                if bundle.gate_id == "facts":
                    from .facts_evidence import facts_review_preflight
                    env = next((env for env in state.patches.values() if env.patch_type == "facts"), None)
                    deterministic_issues = facts_review_preflight(env.content if env else {})
                else:
                    from .material_universe_preflight import run_material_universe_preflight
                    deterministic_issues = run_material_universe_preflight(
                        state=state, policy=_replay_policy(bundle)
                    ).issues
                for item in deterministic_issues:
                    if item.get("severity", "error") == "error":
                        issues.append(GateReplayIssue(code="gate_replay.deterministic_preflight", message=str(item.get("message", item.get("code", "preflight error")))))
                state_complete = state_complete and not any(item.get("severity", "error") == "error" for item in deterministic_issues)
                result.state_complete = state_complete
            except Exception as exc:
                state_complete = False
                result.state_complete = False
                issues.append(GateReplayIssue(code="gate_replay.state_reconstruction_failed", message=f"state reconstruction failed: {type(exc).__name__}"))
            result.issues = issues
        result.ok = upstream_ok and hashes_ok and state_complete and sensitive_ok
        result.summary = "preflight validation complete (no LLM)"
        return result

    # Both recorded-review and live-review require the preflight to pass.
    if not (upstream_ok and hashes_ok and state_ok and sensitive_ok):
        result.ok = False
        result.summary = "preflight failed; replay aborted"
        return result

    if mode is GateReplayMode.RECORDED_REVIEW:
        # Replay normalized recorded review outputs.  No LLM call.
        if not bundle.recorded_reviews:
            result.ok = False
            result.issues.append(
                GateReplayIssue(
                    code="gate_replay.no_recorded_reviews",
                    message="recorded-review mode requires non-empty recorded_reviews",
                    path="recorded_reviews",
                )
            )
            result.summary = "no recorded reviews to replay"
            return result
        if not all(isinstance(review, Mapping) for review in bundle.recorded_reviews):
            result.issues.append(GateReplayIssue(code="gate_replay.malformed_recorded_review", message="recorded review must be an object"))
            result.summary = "malformed recorded review"
            return result
        if not any(key in bundle.recorded_reviews[0] for key in ("review_status", "decision", "ok")):
            result.issues.append(GateReplayIssue(code="gate_replay.malformed_recorded_review", message="recorded review has no normalized decision"))
            result.summary = "malformed recorded review"
            return result
        recorded = iter(bundle.recorded_reviews)
        def fake_reviewer(_prompt: str) -> Any:
            try:
                return next(recorded)
            except StopIteration as exc:
                raise ValueError("recorded review count is insufficient") from exc
        try:
            review_result, _ = _run_production_review(bundle, reviewer_client=fake_reviewer, recorded=True)
        except Exception as exc:
            result.issues.append(GateReplayIssue(code="gate_replay.malformed_recorded_review", message=f"production normalization failed: {type(exc).__name__}"))
            result.summary = "malformed recorded review"
            return result
        result.recorded_review_replayed = True
        result.ok = bool(review_result.ok)
        result.review_output = _normalized_review_output(review_result)
        if not result.ok:
            result.issues.append(GateReplayIssue(
                code="gate_replay.recorded_review_failed",
                message=str(getattr(review_result, "failure_code", "review normalization failed")),
            ))
        result.summary = "recorded review normalized through production reviewer"
        return result

    if mode is GateReplayMode.LIVE_REVIEW:
        if reviewer_client is None:
            result.ok = False
            result.issues.append(
                GateReplayIssue(
                    code="gate_replay.live_reviewer_missing",
                    message="live-review mode requires a reviewer_client",
                )
            )
            result.summary = "live reviewer not supplied"
            return result
        # Invoke only the target production reviewer.  The reviewer receives
        # production-built evidence packs; replay persists its normalized
        # result, never raw provider output.
        try:
            review_result, _ = _run_production_review(
                bundle,
                reviewer_client=reviewer_client,
                recorded=False,
            )
            result.live_review_invoked = True
            result.ok = bool(review_result.ok)
            result.review_output = _normalized_review_output(review_result)
            if not result.ok:
                result.issues.append(GateReplayIssue(
                    code="gate_replay.live_review_failed",
                    message=str(getattr(review_result, "failure_code", "review failed")),
                ))
            result.summary = "live review invoked against target reviewer"
        except Exception as exc:
            result.live_review_invoked = True
            result.ok = False
            result.issues.append(
                GateReplayIssue(
                    code="gate_replay.live_reviewer_error",
                    message=f"reviewer_client raised: {type(exc).__name__}",
                )
            )
            result.summary = "live reviewer raised an error"
        return result

    result.ok = False
    result.summary = f"unknown replay mode: {mode}"
    return result
