"""Tests for AxialGeometryEvidencePack."""

from tests._axial_geometry_fixtures import state_with_axial_patches
from openmc_agent.plan_builder.closed_loop.axial_geometry_evidence import (
    axial_geometry_gate_applicable,
    axial_geometry_gate_ready,
    axial_geometry_gate_input_hash,
    build_axial_geometry_evidence_pack,
)
from openmc_agent.plan_builder.closed_loop.models import PlanClosedLoopPolicy


def _policy():
    return PlanClosedLoopPolicy(mode="controlled", axial_geometry_review_mode="controlled")


def test_applicable_with_axial_domain():
    state = state_with_axial_patches()
    assert axial_geometry_gate_applicable(state) is True


def test_ready_with_valid_axial_patches():
    state = state_with_axial_patches()
    assert axial_geometry_gate_ready(state) is True


def test_input_hash_stable():
    state = state_with_axial_patches()
    h1 = axial_geometry_gate_input_hash(state, policy=_policy())
    h2 = axial_geometry_gate_input_hash(state, policy=_policy())
    assert h1 == h2


def test_evidence_pack_has_items():
    state = state_with_axial_patches()
    pack = build_axial_geometry_evidence_pack(state=state, policy=_policy())
    assert pack.gate_id.value == "axial_geometry"
    assert len(pack.evidence_items) > 0
    assert pack.binding_view is not None
    assert pack.input_hash
