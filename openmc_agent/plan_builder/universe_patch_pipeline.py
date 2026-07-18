"""Top-level entry point for fragmented universe patch generation.

Provides ``generate_universes_patch`` which orchestrates the full pipeline:
requirement extraction → manifest → fragment generation → merge → validation.

The executor calls this instead of ``generate_patch`` when the universes
patch is large enough to risk truncation.
"""

from __future__ import annotations

import json
from typing import Any

from .llm_adapter import PatchLLMResponse, normalize_patch_llm_response
from .patch_generator import PatchGenerationResult, PatchGenerationAttempt
from .patches import get_patch_json_schema
from .state import PlanBuildState, PlanPatchEnvelope
from .universe_fragment_generation import (
    FragmentStatus,
    LargePatchGenerationSession,
    UniverseDefinitionFragment,
    UniverseManifest,
    UniverseManifestItem,
    UniverseGenerationRequirementSet,
    build_manifest_from_requirements,
    estimate_universes_output_size,
    extract_universe_requirements,
    merge_universe_fragments,
    resolve_patch_output_budget,
    should_fragment_universes,
    validate_manifest,
    validate_merged_patch,
)


def _get_session(state: PlanBuildState, input_hash: str) -> LargePatchGenerationSession | None:
    """Retrieve a checkpoint session matching the input hash."""
    sessions = state.metadata.get("large_patch_generation_sessions", {})
    if not isinstance(sessions, dict):
        return None
    session_data = sessions.get(f"universes:{input_hash}")
    if session_data is None:
        return None
    if isinstance(session_data, dict):
        return LargePatchGenerationSession.model_validate(session_data)
    return session_data


def _save_session(state: PlanBuildState, session: LargePatchGenerationSession) -> None:
    """Persist a checkpoint session."""
    sessions = state.metadata.setdefault("large_patch_generation_sessions", {})
    sessions[f"universes:{session.input_hash}"] = session.model_dump(mode="json")


def _stale_session(state: PlanBuildState, input_hash: str) -> None:
    """Mark a session as stale (input hash changed)."""
    sessions = state.metadata.get("large_patch_generation_sessions", {})
    if isinstance(sessions, dict):
        for key in list(sessions.keys()):
            if key.startswith("universes:") and key != f"universes:{input_hash}":
                old = sessions[key]
                if isinstance(old, dict):
                    old["completed"] = False
                    old["metadata"]["stale"] = True


def _call_llm_fragment(
    llm_client: Any,
    *,
    prompt: str,
    max_tokens: int | None = None,
) -> PatchLLMResponse:
    """Call the LLM for a single universe fragment."""
    if hasattr(llm_client, "generate_patch_json_with_meta"):
        try:
            schema = get_patch_json_schema("universes")
            return llm_client.generate_patch_json_with_meta(
                prompt=prompt, patch_type="universes",
                json_schema=schema, max_tokens=max_tokens,
            )
        except Exception:
            pass
    if hasattr(llm_client, "generate_patch_json"):
        try:
            schema = get_patch_json_schema("universes")
            raw = llm_client.generate_patch_json(
                prompt=prompt, patch_type="universes",
                json_schema=schema, max_tokens=max_tokens,
            )
            from .llm_adapter import PatchLLMResponse
            return PatchLLMResponse(content=raw, output_mode_used=str(getattr(llm_client, "last_output_mode_used", "structured")))
        except Exception:
            pass
    raw = llm_client(prompt)
    return normalize_patch_llm_response(raw)


def _build_fragment_prompt(
    item: UniverseManifestItem,
    *,
    requirement: str,
    material_summary: str,
    prior_failures: list[str] | None = None,
) -> str:
    """Build a focused prompt for generating one universe."""
    lines = [
        f"Generate ONLY the universe definition for universe_id={item.universe_id}.",
        f"Kind: {item.kind}",
    ]
    if item.required_cell_roles:
        lines.append(f"Required cell roles: {', '.join(item.required_cell_roles)}")
    if item.required_material_ids:
        lines.append(f"Required material IDs: {', '.join(item.required_material_ids)}")
    if item.required_material_roles:
        lines.append(f"Required material roles: {', '.join(item.required_material_roles)}")
    if item.fuel_variant_id:
        lines.append(f"Fuel variant: {item.fuel_variant_id}")
    if item.protected_through_path_roles:
        lines.append(f"Protected through-path roles: {', '.join(item.protected_through_path_roles)}")
    lines.append(f"\nAvailable materials:\n{material_summary}")
    lines.append(f"\nSource context:\n{requirement[:2000]}")
    lines.append("\nOutput a single JSON object with keys: patch_type, universes (list with ONE universe).")
    lines.append("The universe must have: universe_id, kind, cells (list of cell layers).")
    lines.append("Each cell layer needs: id, role, material_id, region_kind, r_min_cm, r_max_cm.")
    if prior_failures:
        lines.append(f"\nPrior failures to avoid:\n{chr(10).join(prior_failures[-3:])}")
    import json
    lines.append("\n```json")
    lines.append(json.dumps({
        "patch_type": "universes",
        "universes": [{
            "universe_id": item.universe_id,
            "kind": item.kind,
            "cells": [{"id": "c1", "role": "fuel", "material_id": "REPLACE", "region_kind": "cylinder", "r_min_cm": 0.0, "r_max_cm": 0.4}]
        }]
    }, indent=2))
    lines.append("```")
    return "\n".join(lines)


def generate_universes_patch(
    *,
    requirement: str,
    state: PlanBuildState,
    llm_client: Any,
    mode: str = "auto",
    max_tokens: int | None = None,
    max_fragment_attempts: int = 2,
    max_total_llm_calls: int = 40,
    safe_output_ratio: float = 0.6,
    strict_structured: bool = False,
) -> PatchGenerationResult:
    """Generate a universes patch, using fragmentation when necessary.

    This function replaces ``generate_patch`` for the ``universes`` patch type.
    Other patch types continue to use ``generate_patch``.

    Parameters
    ----------
    mode
        ``"auto"`` (default), ``"monolithic"``, or ``"fragmented"``.
    max_fragment_attempts
        Maximum retry attempts per individual universe fragment.
    max_total_llm_calls
        Global LLM call budget for this generation (prevents infinite fragmentation).
    """
    from .patch_generator import generate_patch, _looks_truncated, parse_llm_patch_json
    from .llm_adapter import LARGE_PATCH_MAX_TOKENS
    from .closed_loop.fingerprints import compute_candidate_hash
    from .patches import parse_patch_content, PatchParseError

    patch_type = "universes"
    effective_max = max_tokens or LARGE_PATCH_MAX_TOKENS.get(patch_type)

    # Extract requirements from accepted upstream context.
    facts_env = next((e for e in state.patches.values() if e.patch_type == "facts" and e.status == "valid"), None)
    materials_env = next((e for e in state.patches.values() if e.patch_type == "materials" and e.status == "valid"), None)
    facts_obj = None
    materials_obj = None
    known_material_ids: set[str] = set()
    if facts_env is not None:
        try:
            facts_obj = parse_patch_content("facts", facts_env.content)
        except Exception:
            pass
    if materials_env is not None:
        try:
            materials_obj = parse_patch_content("materials", materials_env.content)
            for m in getattr(materials_obj, "materials", []):
                known_material_ids.add(m.material_id)
        except Exception:
            pass

    requirement_set = extract_universe_requirements(
        facts=facts_obj, materials=materials_obj,
        canonical_task_plan=getattr(state, "canonical_task_plan", None),
        confirmed_records=getattr(state, "plan_confirmed_plan_fact_records", None),
    )

    # Check for existing checkpoint session.
    session = _get_session(state, requirement_set.input_hash)
    if session is None:
        _stale_session(state, requirement_set.input_hash)
        session = LargePatchGenerationSession(
            session_id=f"session:{requirement_set.input_hash}",
            patch_type=patch_type,
            input_hash=requirement_set.input_hash,
            mode=mode,  # type: ignore[arg-type]
            requirement_set_hash=requirement_set.input_hash,
        )

    material_summary = ""
    if materials_obj is not None:
        parts = []
        for m in getattr(materials_obj, "materials", []):
            parts.append(f"  - {m.material_id} (role={getattr(m, 'role', 'unknown')}, density={getattr(m, 'density_g_cm3', '?')})")
        material_summary = "\n".join(parts)

    # Decide strategy.
    history_truncated = any(
        "json_truncated" in str(a.get("issues", []))
        for a in getattr(state, "metadata", {}).get("patch_attempt_artifacts", {}).values()
        if isinstance(a, dict) and a.get("patch_type") == patch_type
    )

    do_fragment, fragment_reason = should_fragment_universes(
        mode=mode,  # type: ignore[arg-type]
        universe_count=max(len(requirement_set.requirements), 1),
        provider_max_output_tokens=effective_max,
        reasoning_enabled=False,
        history_json_truncated=history_truncated,
        safe_output_ratio=safe_output_ratio,
    )

    if not do_fragment:
        # Monolithic path: delegate to existing generate_patch.
        session.strategy_transitions.append({"from": "none", "to": "monolithic", "reason": fragment_reason})
        _save_session(state, session)
        result = generate_patch(
            patch_type=patch_type, requirement=requirement, state=state,
            llm_client=llm_client, max_attempts=2, max_tokens=effective_max,
        )
        # Check if monolithic was truncated → switch to fragmented.
        for attempt in result.attempts:
            if any(i.get("code") == "patch_generation.json_truncated" for i in attempt.issues):
                session.strategy_transitions.append({"from": "monolithic", "to": "fragmented", "reason": "json_truncated"})
                session.llm_call_count += len(result.attempts)
                do_fragment = True
                break
            if attempt.finish_reason and attempt.finish_reason.lower() in ("length", "max_tokens"):
                session.strategy_transitions.append({"from": "monolithic", "to": "fragmented", "reason": f"finish_reason={attempt.finish_reason}"})
                session.llm_call_count += len(result.attempts)
                do_fragment = True
                break
        if not do_fragment:
            session.completed = True
            _save_session(state, session)
            return result

    # Fragmented path.
    session.mode = "fragmented"
    session.strategy_transitions.append({"from": "monolithic" if session.strategy_transitions else "none", "to": "fragmented", "reason": fragment_reason if not session.strategy_transitions else session.strategy_transitions[-1].get("reason", "")})

    # Build or reuse manifest.
    if session.manifest is None:
        manifest = build_manifest_from_requirements(requirement_set, known_material_ids=known_material_ids)
        manifest_errors = validate_manifest(manifest, requirement_set, known_material_ids=known_material_ids)
        if manifest_errors:
            session.manifest_status = "failed"
            _save_session(state, session)
            return PatchGenerationResult(
                ok=False, patch_type=patch_type,
                issues=[{"code": "patch_generation.manifest_validation_failed", "severity": "error", "message": f"manifest errors: {manifest_errors}"}],
            )
        session.manifest = manifest
        session.manifest_status = "accepted"
    else:
        manifest = session.manifest

    # Generate or resume fragments.
    fragment_budget = max_total_llm_calls - session.llm_call_count
    accepted_fragments: list[UniverseDefinitionFragment] = []
    for item_id in manifest.generation_order:
        # Resume: skip already-accepted fragments.
        existing = next((fs for fs in session.fragment_statuses if fs.universe_id == item_id), None)
        if existing and existing.status == "accepted":
            frag_data = session.accepted_fragment_hashes.get(item_id, "")
            # Reconstruct fragment from stored data.
            for key, val in session.metadata.get("_accepted_fragments", {}).items():
                if key == item_id and isinstance(val, dict):
                    accepted_fragments.append(UniverseDefinitionFragment(
                        universe_id=item_id, universe=val.get("universe", {}),
                        fragment_hash=frag_data,
                    ))
                    break
            continue

        item = next((i for i in manifest.items if i.universe_id == item_id), None)
        if item is None:
            continue

        prior_failures: list[str] = []
        frag_result: UniverseDefinitionFragment | None = None
        for frag_attempt in range(max_fragment_attempts):
            if session.llm_call_count >= max_total_llm_calls:
                break
            if fragment_budget <= 0:
                break
            fragment_budget -= 1
            session.llm_call_count += 1
            prompt = _build_fragment_prompt(
                item, requirement=requirement, material_summary=material_summary,
                prior_failures=prior_failures,
            )
            frag_max = resolve_patch_output_budget(explicit=max_tokens, fragment_mode=True, provider_max_output=effective_max)
            resp = _call_llm_fragment(llm_client, prompt=prompt, max_tokens=frag_max)
            session.provider_telemetry.append({
                "universe_id": item_id, "attempt": frag_attempt,
                "finish_reason": resp.finish_reason, "output_mode_used": resp.output_mode_used,
                "completion_tokens": resp.completion_tokens, "reasoning_tokens": resp.reasoning_tokens,
            })
            # Parse the fragment response.
            try:
                parsed = parse_llm_patch_json(resp.content, patch_type)
                universes_list = parsed.get("universes", [])
                if not universes_list:
                    raise ValueError("empty universes list in fragment response")
                universe_data = universes_list[0]
                if universe_data.get("universe_id") != item_id:
                    raise ValueError(f"universe_id mismatch: expected {item_id}, got {universe_data.get('universe_id')}")
                frag_hash = compute_candidate_hash(target_patch_type=patch_type, candidate_patch=universe_data)
                frag_result = UniverseDefinitionFragment(
                    universe_id=item_id, universe=universe_data, fragment_hash=frag_hash,
                )
                break  # success
            except Exception as exc:
                prior_failures.append(str(exc))
                if resp.finish_reason and resp.finish_reason.lower() in ("length", "max_tokens"):
                    prior_failures.append(f"finish_reason={resp.finish_reason} (output truncated)")

        if frag_result is None:
            # Fragment failed.
            fs = next((fs for fs in session.fragment_statuses if fs.universe_id == item_id), None)
            if fs is None:
                fs = FragmentStatus(universe_id=item_id, status="failed")
                session.fragment_statuses.append(fs)
            fs.status = "failed"
            fs.issues = [{"code": "patch_generation.fragment_failed", "severity": "error", "message": f"fragment {item_id} failed after {max_fragment_attempts} attempts"}]
            session.failed_fragment_issues[item_id] = fs.issues
            _save_session(state, session)
            return PatchGenerationResult(
                ok=False, patch_type=patch_type,
                issues=[{"code": "patch_generation.fragment_failed", "severity": "error", "message": f"universe fragment {item_id} failed"}],
            )
        # Fragment accepted.
        fs = next((fs for fs in session.fragment_statuses if fs.universe_id == item_id), None)
        if fs is None:
            fs = FragmentStatus(universe_id=item_id, status="accepted")
            session.fragment_statuses.append(fs)
        fs.status = "accepted"
        fs.fragment_hash = frag_result.fragment_hash
        session.accepted_fragment_hashes[item_id] = frag_result.fragment_hash
        # Store fragment data for resume.
        session.metadata.setdefault("_accepted_fragments", {})[item_id] = {"universe": frag_result.universe}
        accepted_fragments.append(frag_result)
        _save_session(state, session)

    # Merge fragments.
    merged_patch, merge_errors = merge_universe_fragments(
        manifest=manifest, fragments=accepted_fragments,
        known_material_ids=known_material_ids,
    )
    if merge_errors:
        _save_session(state, session)
        return PatchGenerationResult(
            ok=False, patch_type=patch_type,
            issues=[{"code": "patch_generation.merge_failed", "severity": "error", "message": f"merge errors: {merge_errors}"}],
        )

    # Validate merged patch.
    ok, val_issues = validate_merged_patch(merged_patch, known_material_ids=known_material_ids)
    if not ok:
        _save_session(state, session)
        return PatchGenerationResult(
            ok=False, patch_type=patch_type,
            issues=val_issues,
        )

    # Create standard PlanPatchEnvelope.
    import hashlib
    patch_hash = hashlib.sha256(json.dumps(merged_patch, sort_keys=True).encode()).hexdigest()[:16] if merged_patch else ""
    envelope = PlanPatchEnvelope(
        patch_id=f"universes_fragmented_{patch_hash}",
        patch_type=patch_type,
        content=merged_patch,
        source="llm",
        status="valid",
    )
    session.completed = True
    session.merged_patch_hash = patch_hash
    _save_session(state, session)

    return PatchGenerationResult(
        ok=True, patch_type=patch_type, envelope=envelope,
        parsed_patch=merged_patch,
        attempts=[],
        issues=[],
    )


__all__ = ["generate_universes_patch"]
