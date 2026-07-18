"""Tests for universe fragment checkpoint resume."""

from openmc_agent.plan_builder.universe_fragment_generation import LargePatchGenerationSession, FragmentStatus
from openmc_agent.plan_builder.state import PlanBuildState


def test_session_serializable():
    session = LargePatchGenerationSession(
        session_id="test",
        input_hash="abc",
        mode="fragmented",
        fragment_statuses=[FragmentStatus(universe_id="u1", status="accepted")],
    )
    data = session.model_dump(mode="json")
    restored = LargePatchGenerationSession.model_validate(data)
    assert restored.session_id == "test"
    assert restored.mode == "fragmented"
    assert len(restored.fragment_statuses) == 1


def test_session_stored_in_state_metadata():
    state = PlanBuildState(state_id="test", requirement_text="r")
    sessions = state.metadata.setdefault("large_patch_generation_sessions", {})
    session = LargePatchGenerationSession(session_id="s1", input_hash="h1")
    sessions["universes:h1"] = session.model_dump(mode="json")
    assert "universes:h1" in state.metadata["large_patch_generation_sessions"]


def test_accepted_fragment_not_re_generated():
    """A completed fragment in the session should be reused, not re-called."""
    session = LargePatchGenerationSession(
        session_id="test",
        input_hash="abc",
        mode="fragmented",
        fragment_statuses=[
            FragmentStatus(universe_id="u1", status="accepted", fragment_hash="hash1"),
        ],
        accepted_fragment_hashes={"u1": "hash1"},
    )
    accepted = [fs for fs in session.fragment_statuses if fs.status == "accepted"]
    assert len(accepted) == 1
    assert accepted[0].universe_id == "u1"


def test_stale_session_on_input_hash_change():
    """When input hash changes, old sessions should be marked stale."""
    state = PlanBuildState(state_id="test", requirement_text="r")
    sessions = state.metadata.setdefault("large_patch_generation_sessions", {})
    sessions["universes:old_hash"] = LargePatchGenerationSession(
        session_id="s1", input_hash="old_hash", completed=True,
    ).model_dump(mode="json")
    # Mark stale.
    for key in list(sessions.keys()):
        if key != "universes:new_hash":
            sessions[key]["completed"] = False
            sessions[key].setdefault("metadata", {})["stale"] = True
    assert sessions["universes:old_hash"]["metadata"]["stale"] is True
    assert sessions["universes:old_hash"]["completed"] is False
