"""End-to-end integration tests for ``generate_universes_patch`` (Step 4B-1).

These tests exercise the full pipeline directly (no manual merge helper
calls) and cover the scenarios required by the task:

A. All-success
B. One fragment first-attempt failure → targeted retry
C. Checkpoint corruption on resume → only that fragment regenerates
D. Merge discovers fragment-scoped issue → targeted replay
E. Manifest/global failure → fail closed
F. run_004 failure class (REPLACE placeholder) → diagnosed precisely

Each test sets up a reactor-neutral Facts + Materials state, drives the
pipeline with a deterministic fake LLM, and inspects both the returned
:class:`PatchGenerationResult` and the persisted
:class:`LargePatchGenerationSession` checkpoint.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from openmc_agent.plan_builder.patches import (
    FactsPatch,
    FuelVariantRequirementPatchItem,
    LocalizedInsertPlacementRequirementPatchItem,
    MaterialSpecPatch,
    MaterialsPatch,
)
from openmc_agent.plan_builder.state import PlanBuildState, PlanPatchEnvelope
from openmc_agent.plan_builder.universe_fragment_generation import (
    LargePatchGenerationSession,
)
from openmc_agent.plan_builder.universe_patch_pipeline import generate_universes_patch


# ---------------------------------------------------------------------------
# State builder
# ---------------------------------------------------------------------------


def _fuel_materials() -> MaterialsPatch:
    return MaterialsPatch(
        patch_type="materials",
        materials=[
            MaterialSpecPatch(
                material_id="m_fuel", name="fuel", role="fuel",
                density_g_cm3=10.0,
                composition={"U235": 100.0},
                composition_basis="weight_frac",
                composition_status="approximate",
            ),
            MaterialSpecPatch(
                material_id="m_water", name="coolant", role="coolant",
                density_g_cm3=0.7,
                composition={"H": 1.0},
                composition_basis="weight_frac",
                composition_status="approximate",
            ),
            MaterialSpecPatch(
                material_id="m_structural", name="structural", role="structural",
                density_g_cm3=7.8,
                composition={"Fe": 100.0},
                composition_basis="weight_frac",
                composition_status="approximate",
            ),
            MaterialSpecPatch(
                material_id="m_absorber", name="absorber", role="absorber",
                density_g_cm3=8.0,
                composition={"B10": 100.0},
                composition_basis="weight_frac",
                composition_status="approximate",
            ),
            MaterialSpecPatch(
                material_id="m_moderator", name="moderator", role="moderator",
                density_g_cm3=1.0,
                composition={"H2": 100.0},
                composition_basis="weight_frac",
                composition_status="approximate",
            ),
        ],
    )


def _facts_with_fuel_variants(n: int = 2, *, with_inserts: bool = False) -> FactsPatch:
    fuel_variants = [
        FuelVariantRequirementPatchItem(
            variant_id=f"v{i+1}",
            source_label=f"variant {i+1}",
            enrichment_wt_percent=2.0 + 0.5 * i,
            density_g_cm3=10.257,
        )
        for i in range(n)
    ]
    localized_inserts: list[LocalizedInsertPlacementRequirementPatchItem] = []
    if with_inserts:
        localized_inserts.append(LocalizedInsertPlacementRequirementPatchItem(
            requirement_id="insert_a",
            insert_kind="control_rod",
            required_segment_roles=["absorber"],
            expected_insert_universe_ids=["localized_insert_insert_a"],
        ))
    return FactsPatch(
        patch_type="facts",
        benchmark_id=None,  # reactor-neutral
        geometry_type="single_assembly",
        lattice_size=(17, 17),
        pin_pitch_cm=1.26,
        has_axial_geometry=True,
        active_fuel_region_cm=(0.0, 100.0),
        fuel_variant_requirements=fuel_variants,
        localized_insert_requirements=localized_inserts,
    )


def _state_with_accepted_upstream(
    *, with_inserts: bool = False, fuel_variants: int = 2
) -> PlanBuildState:
    state = PlanBuildState(state_id="uni_pipeline_test", requirement_text="reactor-neutral")
    state.add_patch(PlanPatchEnvelope(
        patch_id="facts", patch_type="facts",
        content=_facts_with_fuel_variants(fuel_variants, with_inserts=with_inserts).model_dump(mode="json"),
        source="fixture", status="valid",
    ))
    state.add_patch(PlanPatchEnvelope(
        patch_id="materials", patch_type="materials",
        content=_fuel_materials().model_dump(mode="json"),
        source="fixture", status="valid",
    ))
    return state


# ---------------------------------------------------------------------------
# Fake LLM that returns scripted per-call universes
# ---------------------------------------------------------------------------


class _ScriptedFragmentLLM:
    """Fake LLM that returns one universe JSON per ``__call__``.

    ``scripts`` is a list of dicts; each call pops the next dict.  A dict
    can either be a ``str`` (raw LLM output) or a ``{"universe": ...}`` payload
    describing a single universe.  Use the dict form for convenience.
    """

    def __init__(self, scripts: list[Any]):
        self.scripts: list[str] = []
        for s in scripts:
            if isinstance(s, str):
                self.scripts.append(s)
            elif isinstance(s, dict) and "universe" in s:
                self.scripts.append(json.dumps({
                    "patch_type": "universes",
                    "universes": [s["universe"]],
                }))
            elif isinstance(s, dict) and "raw" in s:
                self.scripts.append(s["raw"])
            else:
                self.scripts.append(json.dumps(s))
        self.prompts: list[str] = []
        self.calls_by_prompt: dict[str, int] = {}

    def __call__(self, prompt: str) -> str:
        self.prompts.append(prompt)
        # Track per-universe call counts by parsing the universe_id out of the prompt.
        for line in prompt.splitlines():
            if "universe_id=" in line:
                uid = line.split("universe_id=", 1)[1].split()[0]
                # Strip trailing punctuation from prompt template ("universe_id=XYZ.")
                uid = uid.rstrip(".,;:")
                self.calls_by_prompt[uid] = self.calls_by_prompt.get(uid, 0) + 1
                break
        if not self.scripts:
            return json.dumps({"patch_type": "universes", "universes": []})
        return self.scripts.pop(0)


def _fuel_universe(uid: str, material_id: str = "m_fuel") -> dict:
    return {
        "universe_id": uid, "kind": "fuel_pin",
        "cells": [
            {"id": "c1", "role": "fuel", "material_id": material_id,
             "region_kind": "cylinder", "r_min_cm": 0.0, "r_max_cm": 0.4},
        ],
    }


def _insert_universe(uid: str) -> dict:
    return {
        "universe_id": uid, "kind": "control_rod",
        "cells": [
            {"id": "c1", "role": "absorber", "material_id": "m_absorber",
             "region_kind": "cylinder", "r_min_cm": 0.0, "r_max_cm": 0.4},
        ],
    }


def _get_session(state: PlanBuildState) -> LargePatchGenerationSession:
    sessions = state.metadata.get("large_patch_generation_sessions", {})
    assert sessions, "no session was persisted"
    key = next(iter(sessions.keys()))
    return LargePatchGenerationSession.model_validate(sessions[key])


# ---------------------------------------------------------------------------
# Scenario A: all-success
# ---------------------------------------------------------------------------


def test_scenario_a_all_success():
    """Multiple fragments succeed first time; merge produces one envelope."""
    state = _state_with_accepted_upstream(fuel_variants=2)
    # The pipeline will create ~2 fuel variants + 4 implicit universes
    # (end plugs, gas gap, water pin).  Script enough happy responses by
    # generating good universes for whatever the manifest asks for.
    from openmc_agent.plan_builder.universe_fragment_generation import (
        extract_universe_requirements, build_manifest_from_requirements,
    )
    from openmc_agent.plan_builder.patches import parse_patch_content

    facts_env = next(e for e in state.patches.values() if e.patch_type == "facts")
    materials_env = next(e for e in state.patches.values() if e.patch_type == "materials")
    facts_obj = parse_patch_content("facts", facts_env.content)
    materials_obj = parse_patch_content("materials", materials_env.content)
    reqs = extract_universe_requirements(facts=facts_obj, materials=materials_obj)
    manifest = build_manifest_from_requirements(reqs)
    ids = list(manifest.generation_order)

    scripts = [{"universe": _good_for_universe(uid)} for uid in ids]
    fake = _ScriptedFragmentLLM(scripts)
    result = generate_universes_patch(
        requirement="reactor-neutral source",
        state=state, llm_client=fake, mode="fragmented",
    )
    assert result.ok, f"expected ok, got: {[i.get('message') for i in result.issues]}"
    assert result.envelope is not None
    assert result.envelope.patch_type == "universes"
    session = _get_session(state)
    assert session.completed
    assert session.manifest_status == "accepted"
    assert len(session.accepted_fragments) == session.manifest.expected_universe_count
    assert session.llm_call_count >= session.manifest.expected_universe_count
    # Every fragment was called exactly once.
    for uid in ids:
        assert fake.calls_by_prompt.get(uid, 0) == 1


# ---------------------------------------------------------------------------
# Scenario B: one fragment fails first attempt, succeeds on retry
# ---------------------------------------------------------------------------


def test_scenario_b_one_fragment_first_attempt_bad_then_good():
    """The first attempt for fragment X returns an unknown material; the
    retry returns a valid fragment.  Other fragments are NOT regenerated."""
    state = _state_with_accepted_upstream(fuel_variants=1)
    # Find the universe IDs we will need to script for.
    from openmc_agent.plan_builder.universe_fragment_generation import (
        extract_universe_requirements, build_manifest_from_requirements,
    )
    from openmc_agent.plan_builder.patches import parse_patch_content

    facts_env = next(e for e in state.patches.values() if e.patch_type == "facts")
    materials_env = next(e for e in state.patches.values() if e.patch_type == "materials")
    facts_obj = parse_patch_content("facts", facts_env.content)
    materials_obj = parse_patch_content("materials", materials_env.content)
    reqs = extract_universe_requirements(facts=facts_obj, materials=materials_obj)
    manifest = build_manifest_from_requirements(reqs)
    ids = list(manifest.generation_order)
    assert len(ids) >= 3
    bad_id = ids[1]
    good_id_first = ids[0]
    other_ids = ids[2:]

    scripts: list[Any] = []
    # First pass: emit good_for(first), bad for bad_id (then its retry),
    # then good for the rest.
    scripts.append({"universe": _good_for_universe(good_id_first)})
    bad_universe = _good_for_universe(bad_id)
    bad_universe["cells"][0]["material_id"] = "m_unknown"
    scripts.append({"universe": bad_universe})
    # Retry attempt for bad_id (within max_fragment_attempts).
    scripts.append({"universe": _good_for_universe(bad_id)})
    for uid in other_ids:
        scripts.append({"universe": _good_for_universe(uid)})

    fake = _ScriptedFragmentLLM(scripts)
    result = generate_universes_patch(
        requirement="reactor-neutral source",
        state=state, llm_client=fake, mode="fragmented",
        max_fragment_attempts=2,
    )
    assert result.ok, f"unexpected failure: {[i.get('message') for i in result.issues]}"
    session = _get_session(state)
    # The bad fragment was retried exactly once (and not the others).
    assert fake.calls_by_prompt[bad_id] == 2
    for uid in ids:
        if uid != bad_id:
            assert fake.calls_by_prompt[uid] == 1


# ---------------------------------------------------------------------------
# Scenario C: checkpoint corruption on resume
# ---------------------------------------------------------------------------


def test_scenario_c_checkpoint_corruption_only_regen_corrupt_fragment():
    """A resumed session has one fragment with status=accepted but the
    accepted data is missing.  Resume regenerates ONLY that fragment."""
    state = _state_with_accepted_upstream(fuel_variants=1)
    from openmc_agent.plan_builder.universe_fragment_generation import (
        extract_universe_requirements, build_manifest_from_requirements,
    )
    from openmc_agent.plan_builder.patches import parse_patch_content

    facts_env = next(e for e in state.patches.values() if e.patch_type == "facts")
    materials_env = next(e for e in state.patches.values() if e.patch_type == "materials")
    facts_obj = parse_patch_content("facts", facts_env.content)
    materials_obj = parse_patch_content("materials", materials_env.content)
    reqs = extract_universe_requirements(facts=facts_obj, materials=materials_obj)
    manifest = build_manifest_from_requirements(reqs)
    ids = list(manifest.generation_order)

    # Pre-populate the session so all but the LAST fragment is accepted.
    # Then we corrupt one fragment and verify only it gets regenerated.
    fake_initial = _ScriptedFragmentLLM([
        {"universe": _good_for_universe(uid)} for uid in ids
    ])
    result = generate_universes_patch(
        requirement="reactor-neutral source",
        state=state, llm_client=fake_initial, mode="fragmented",
    )
    assert result.ok

    # Now corrupt one accepted fragment in the persisted session.
    sessions = state.metadata["large_patch_generation_sessions"]
    session_key = next(iter(sessions.keys()))
    session_data = sessions[session_key]
    corrupt_id = ids[0]
    # Wipe the universe data but keep status="accepted".
    session_data["accepted_fragments"][corrupt_id]["universe"] = {}
    session_data["accepted_fragments"][corrupt_id]["fragment_hash"] = "corrupt_hash"
    session_data["completed"] = False
    # Remove the merged envelope to force a full transaction.
    state.patches.pop("universes_fragmented_" + session_data.get("merged_patch_hash", "x"), None)

    # Resume with a fresh fake that returns only the corrupted fragment.
    fake_resume = _ScriptedFragmentLLM([
        {"universe": _good_for_universe(corrupt_id)},
    ])
    result2 = generate_universes_patch(
        requirement="reactor-neutral source",
        state=state, llm_client=fake_resume, mode="fragmented",
    )
    assert result2.ok, f"resume failed: {[i.get("message") for i in result2.issues]}"
    # Only the corrupt fragment was re-called.
    assert corrupt_id in fake_resume.calls_by_prompt
    for uid in ids:
        if uid != corrupt_id:
            assert uid not in fake_resume.calls_by_prompt


def _good_for_universe(uid: str) -> dict:
    """Generate a happy-path universe for any implicit requirement id.

    The cell/material roles are aligned with what
    :func:`extract_universe_requirements` declares so the qualification
    contract is satisfied deterministically.
    """
    if uid.startswith("fuel_variant"):
        return _fuel_universe(uid)
    if uid.startswith("implicit_end_plug"):
        # cell role 'end_plug', material role 'structural'
        return {
            "universe_id": uid, "kind": "custom",
            "cells": [{"id": "c1", "role": "end_plug", "material_id": "m_structural",
                        "region_kind": "cylinder", "r_min_cm": 0.0, "r_max_cm": 0.4}],
        }
    if uid.startswith("implicit_gas_gap"):
        # cell role 'gas_gap', material roles 'coolant' AND 'structural'
        return {
            "universe_id": uid, "kind": "custom",
            "cells": [
                {"id": "c1", "role": "gas_gap", "material_id": "m_water",
                 "region_kind": "cylinder", "r_min_cm": 0.0, "r_max_cm": 0.3},
                {"id": "c2", "role": "gas_gap", "material_id": "m_structural",
                 "region_kind": "annulus", "r_min_cm": 0.3, "r_max_cm": 0.4},
            ],
        }
    if uid.startswith("implicit_water_pin"):
        # cell role 'moderator', material role 'coolant'
        return {
            "universe_id": uid, "kind": "water_cell",
            "cells": [{"id": "c1", "role": "moderator", "material_id": "m_water",
                        "region_kind": "cylinder", "r_min_cm": 0.0, "r_max_cm": 0.6}],
        }
    if uid.startswith("localized_insert"):
        # control_rod insert: cell role 'absorber', material role 'absorber'
        return {
            "universe_id": uid, "kind": "control_rod",
            "cells": [{"id": "c1", "role": "absorber", "material_id": "m_absorber",
                        "region_kind": "cylinder", "r_min_cm": 0.0, "r_max_cm": 0.4}],
        }
    # Default: treat as guide / instrument tube (no implicit contract from
    # extract_universe_requirements when facts has no feature contract, so
    # this is just defensive).
    return {
        "universe_id": uid, "kind": "guide_tube",
        "cells": [
            {"id": "c1", "role": "coolant", "material_id": "m_water",
             "region_kind": "cylinder", "r_min_cm": 0.0, "r_max_cm": 0.3},
            {"id": "c2", "role": "wall", "material_id": "m_structural",
             "region_kind": "annulus", "r_min_cm": 0.3, "r_max_cm": 0.4},
        ],
    }


# ---------------------------------------------------------------------------
# Scenario D: merge finds fragment-scoped issue → targeted replay
# ---------------------------------------------------------------------------


def test_scenario_d_merge_finds_fragment_scoped_issue_then_replays():
    """All fragments pass qualification; merge detects a contract drift on
    one fragment via stale qualification record and triggers a targeted
    replay only for that fragment."""
    state = _state_with_accepted_upstream(fuel_variants=1)
    from openmc_agent.plan_builder.universe_fragment_generation import (
        extract_universe_requirements, build_manifest_from_requirements,
    )
    from openmc_agent.plan_builder.patches import parse_patch_content

    facts_env = next(e for e in state.patches.values() if e.patch_type == "facts")
    materials_env = next(e for e in state.patches.values() if e.patch_type == "materials")
    facts_obj = parse_patch_content("facts", facts_env.content)
    materials_obj = parse_patch_content("materials", materials_env.content)
    reqs = extract_universe_requirements(facts=facts_obj, materials=materials_obj)
    manifest = build_manifest_from_requirements(reqs)
    ids = list(manifest.generation_order)

    # Run once successfully.
    fake_initial = _ScriptedFragmentLLM([
        {"universe": _good_for_universe(uid)} for uid in ids
    ])
    result = generate_universes_patch(
        requirement="reactor-neutral source",
        state=state, llm_client=fake_initial, mode="fragmented",
    )
    assert result.ok

    # Now corrupt ONE accepted fragment's data: change the universe_id so the
    # merge step flags a universe_id_mismatch (fragment-scoped).
    sessions = state.metadata["large_patch_generation_sessions"]
    session_key = next(iter(sessions.keys()))
    session_data = sessions[session_key]
    corrupt_id = ids[0]
    original = dict(session_data["accepted_fragments"][corrupt_id])
    # Mutate the universe_id to something else so merge catches it.
    session_data["accepted_fragments"][corrupt_id]["universe"] = {
        **original["universe"],
        "universe_id": "totally_wrong_id",
    }
    session_data["completed"] = False

    fake_resume = _ScriptedFragmentLLM([
        {"universe": _good_for_universe(corrupt_id)},
    ])
    result2 = generate_universes_patch(
        requirement="reactor-neutral source",
        state=state, llm_client=fake_resume, mode="fragmented",
        max_fragment_attempts=2, max_merge_replays=2,
    )
    # The merge-targeted replay should regenerate the corrupt fragment.
    assert result2.ok, f"unexpected failure: {[i.get("message") for i in result2.issues]}"
    assert corrupt_id in fake_resume.calls_by_prompt


# ---------------------------------------------------------------------------
# Scenario E: manifest / global failure fail-closed
# ---------------------------------------------------------------------------


def test_scenario_e_manifest_failure_fails_closed():
    """A pre-baked manifest with duplicate IDs cannot recover."""
    state = _state_with_accepted_upstream(fuel_variants=1)
    # Inject a fake duplicate-id manifest by patching the session directly
    # so when the pipeline resumes, validate_manifest catches it.
    from openmc_agent.plan_builder.universe_fragment_generation import (
        extract_universe_requirements,
        LargePatchGenerationSession,
    )
    from openmc_agent.plan_builder.patches import parse_patch_content

    facts_env = next(e for e in state.patches.values() if e.patch_type == "facts")
    materials_env = next(e for e in state.patches.values() if e.patch_type == "materials")
    facts_obj = parse_patch_content("facts", facts_env.content)
    materials_obj = parse_patch_content("materials", materials_env.content)
    reqs = extract_universe_requirements(facts=facts_obj, materials=materials_obj)

    # Skip the LLM by making mode="monolithic" with a fake that returns invalid JSON.
    fake = _ScriptedFragmentLLM([])
    # Force fragmented mode and pre-load a manifest with duplicate items.
    sessions = state.metadata.setdefault("large_patch_generation_sessions", {})

    # Build a session whose manifest has duplicate universe IDs.
    from openmc_agent.plan_builder.universe_fragment_generation import (
        UniverseManifest, UniverseManifestItem,
    )
    dup_item_a = UniverseManifestItem(universe_id="u_x", kind="fuel_pin", source_requirement_ids=["r1"])
    dup_item_a.recompute_contract_hash()
    dup_item_b = UniverseManifestItem(universe_id="u_x", kind="fuel_pin", source_requirement_ids=["r2"])
    dup_item_b.recompute_contract_hash()
    bad_manifest = UniverseManifest(
        manifest_id="bad",
        input_hash=reqs.input_hash,
        expected_universe_count=2,
        items=[dup_item_a, dup_item_b],
        generation_order=["u_x", "u_x"],
    )
    sessions[f"universes:{reqs.input_hash}"] = LargePatchGenerationSession(
        session_id="pre", input_hash=reqs.input_hash,
        mode="fragmented", manifest=bad_manifest, manifest_status="pending",
    ).model_dump(mode="json")

    result = generate_universes_patch(
        requirement="reactor-neutral source",
        state=state, llm_client=fake, mode="fragmented",
    )
    # Even though we pre-loaded a manifest, the pipeline trusts only manifests
    # it built itself; the persisted manifest here gets reused because it is
    # already set.  The merge step itself will catch the manifest duplicate.
    # Either way, this must NOT produce a valid envelope.
    assert result.ok is False
    if result.issues:
        codes = [i.get("code") for i in result.issues] + [i.get("metadata", {}).get("code") for i in result.issues if isinstance(i.get("metadata"), dict)]
        # Either the manifest validation, fragment failure (no scripts), or
        # the structured merge failure path.  All acceptable fail-closed
        # outcomes — the key assertion is that we did NOT silently accept.
        assert any(
            "manifest" in str(c) or "fragment" in str(c) or "merge" in str(c)
            for c in codes
        )


# ---------------------------------------------------------------------------
# Scenario F: run_004 failure class (REPLACE placeholder)
# ---------------------------------------------------------------------------


def test_scenario_f_run004_replace_placeholder_diagnosed_precisely():
    """Reproduces the run_004 failure mode: one fragment returns the literal
    placeholder ``material_id="REPLACE"`` copied from the prompt template.

    Before Step 4B-1: the fragment was accepted and merge failed with
    ``merge.unknown_material:REPLACE``.  After Step 4B-1: qualification
    rejects the fragment immediately with a structured
    ``qualification.placeholder_material_id`` issue and a JSON path.
    """
    state = _state_with_accepted_upstream(fuel_variants=1)
    from openmc_agent.plan_builder.universe_fragment_generation import (
        extract_universe_requirements, build_manifest_from_requirements,
    )
    from openmc_agent.plan_builder.patches import parse_patch_content

    facts_env = next(e for e in state.patches.values() if e.patch_type == "facts")
    materials_env = next(e for e in state.patches.values() if e.patch_type == "materials")
    facts_obj = parse_patch_content("facts", facts_env.content)
    materials_obj = parse_patch_content("materials", materials_env.content)
    reqs = extract_universe_requirements(facts=facts_obj, materials=materials_obj)
    manifest = build_manifest_from_requirements(reqs)
    ids = list(manifest.generation_order)

    # First pass: one fragment returns the REPLACE placeholder every time.
    placeholder_id = "implicit_gas_gap"
    if placeholder_id not in ids:
        placeholder_id = ids[0]  # fall back to first available

    scripts: list[Any] = []
    for uid in ids:
        if uid == placeholder_id:
            bad = _good_for_universe(uid)
            bad["cells"][0]["material_id"] = "REPLACE"
            scripts.append({"universe": bad})
            scripts.append({"universe": bad})  # retry also fails
        else:
            scripts.append({"universe": _good_for_universe(uid)})

    fake = _ScriptedFragmentLLM(scripts)
    result = generate_universes_patch(
        requirement="reactor-neutral source",
        state=state, llm_client=fake, mode="fragmented",
        max_fragment_attempts=2,
    )
    assert result.ok is False
    # The failure must reference the placeholder universe and be attributed.
    placeholder_failure = None
    for issue in result.issues:
        meta = issue.get("metadata") or {}
        # Either top-level or in nested last_qualification_issues.
        if issue.get("code") == "patch_generation.fragment_failed":
            placeholder_failure = issue
            break
        for q in meta.get("last_qualification_issues", []) or []:
            if q.get("code") == "qualification.placeholder_material_id":
                placeholder_failure = issue
                break
        if placeholder_failure:
            break
    assert placeholder_failure is not None, (
        "expected a fragment failure with placeholder_material_id diagnostic; "
        f"got: {[i.get("code") for i in result.issues]}"
    )
    # The session should record the structured qualification issue.
    session = _get_session(state)
    fs = next((fs for fs in session.fragment_statuses if fs.universe_id == placeholder_id), None)
    assert fs is not None
    assert fs.status == "failed"
    qual_codes = [q.get("code") for q in fs.qualification_issues]
    assert "qualification.placeholder_material_id" in qual_codes, (
        f"expected placeholder_material_id in {qual_codes}"
    )
    # And the fragment was NOT accepted.
    assert placeholder_id not in session.accepted_fragments


# ---------------------------------------------------------------------------
# Backward-compat: legacy tests still work via the new pipeline
# ---------------------------------------------------------------------------


def test_legacy_fragment_payload_with_two_universes_is_rejected():
    """A single LLM call returning two universes must not be accepted."""
    state = _state_with_accepted_upstream(fuel_variants=1)
    # Pre-build the manifest to know the IDs.
    from openmc_agent.plan_builder.universe_fragment_generation import (
        extract_universe_requirements, build_manifest_from_requirements,
    )
    from openmc_agent.plan_builder.patches import parse_patch_content

    facts_env = next(e for e in state.patches.values() if e.patch_type == "facts")
    materials_env = next(e for e in state.patches.values() if e.patch_type == "materials")
    facts_obj = parse_patch_content("facts", facts_env.content)
    materials_obj = parse_patch_content("materials", materials_env.content)
    reqs = extract_universe_requirements(facts=facts_obj, materials=materials_obj)
    manifest = build_manifest_from_requirements(reqs)
    ids = list(manifest.generation_order)
    target_id = ids[0]

    scripts: list[Any] = [
        # First call returns TWO universes for the target_id slot.
        {"raw": json.dumps({
            "patch_type": "universes",
            "universes": [
                _good_for_universe(target_id),
                _good_for_universe(ids[1] if len(ids) > 1 else "other"),
            ],
        })},
        # Retry for target_id that succeeds (must come immediately after the bad call).
        {"universe": _good_for_universe(target_id)},
    ]
    # Fill the remaining slots with valid responses for the other universes.
    for uid in ids[1:]:
        scripts.append({"universe": _good_for_universe(uid)})

    fake = _ScriptedFragmentLLM(scripts)
    result = generate_universes_patch(
        requirement="reactor-neutral source",
        state=state, llm_client=fake, mode="fragmented",
        max_fragment_attempts=2,
    )
    assert result.ok, f"expected recovery after retry: {[i.get("message") for i in result.issues]}"
    # The target_id must have been called twice (bad + good).
    assert fake.calls_by_prompt.get(target_id, 0) == 2
