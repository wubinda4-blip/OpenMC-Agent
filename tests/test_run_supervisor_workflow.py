"""Tests for run supervisor graph integration: advisory vs controlled_route."""

from __future__ import annotations

import pytest
from unittest.mock import MagicMock

from openmc_agent.graph import build_plan_graph, GraphState


class TestSupervisorGraphNode:

    def test_supervisor_disabled_by_default(self):
        """When supervisor is disabled, graph builds and runs without error."""
        graph = build_plan_graph(enable_run_supervisor=False)
        assert graph is not None

    def test_supervisor_advisory_mode(self):
        """Advisory mode: supervisor runs but doesn't change route."""
        graph = build_plan_graph(enable_run_supervisor=True, run_supervisor_mode="advisory")
        assert graph is not None

    def test_supervisor_controlled_route_mode(self):
        """Controlled-route mode: supervisor can override routes."""
        graph = build_plan_graph(enable_run_supervisor=True, run_supervisor_mode="controlled_route")
        assert graph is not None

    def test_supervisor_node_is_registered(self):
        """The run_supervisor node exists in the graph."""
        graph = build_plan_graph(enable_run_supervisor=True)
        # LangGraph stores nodes in .nodes attribute.
        assert hasattr(graph, "nodes")
        node_names = graph.nodes.keys() if hasattr(graph, "nodes") else []
        # Check that either "run_supervisor" exists directly or in nested structure.
        nodes_str = str(node_names)
        assert "run_supervisor" in nodes_str or "supervisor" in nodes_str.lower()


class TestSupervisorRouteMap:

    def test_route_map_covers_all_actions(self):
        from openmc_agent.graph import SUPERVISOR_ROUTE_MAP
        from openmc_agent.run_supervisor import RunSupervisorAction

        for action in RunSupervisorAction:
            assert action.value in SUPERVISOR_ROUTE_MAP

    def test_render_maps_to_render(self):
        from openmc_agent.graph import SUPERVISOR_ROUTE_MAP
        assert SUPERVISOR_ROUTE_MAP["continue_to_render"] == "render"

    def test_stop_maps_to_stop(self):
        from openmc_agent.graph import SUPERVISOR_ROUTE_MAP
        assert SUPERVISOR_ROUTE_MAP["stop"] == "stop"

    def test_human_confirmation_maps_to_ask(self):
        from openmc_agent.graph import SUPERVISOR_ROUTE_MAP
        assert SUPERVISOR_ROUTE_MAP["request_human_confirmation"] == "ask"

    def test_patch_retry_maps_to_generate(self):
        from openmc_agent.graph import SUPERVISOR_ROUTE_MAP
        assert SUPERVISOR_ROUTE_MAP["retry_patch"] == "generate"

    def test_monolithic_fallback_not_in_map(self):
        """No supervisor action should map to a monolithic fallback."""
        from openmc_agent.graph import SUPERVISOR_ROUTE_MAP
        for action, route in SUPERVISOR_ROUTE_MAP.items():
            assert route != "reflect" or action in {
                "continue_patch_generation",
                "retry_patch",
            }, f"Unexpected route '{route}' for action '{action}'"


class TestSupervisorAwareRouter:

    def test_advisory_mode_uses_original_router(self):
        """In advisory mode, the router should use the original logic."""
        from openmc_agent.graph import _make_supervisor_aware_router

        router = _make_supervisor_aware_router(
            enable_supervisor=True,
            supervisor_mode="advisory",
        )
        # Advisory mode should NOT use override.
        state: GraphState = {
            "run_supervisor_route_override": "render",
            "validation_report": MagicMock(is_valid=True),
            "capability_repair_errors": [],
        }
        result = router(state)
        # Should not return "render" in advisory mode.
        assert result in {"reflect", "ask"}

    def test_controlled_route_uses_override(self):
        """In controlled_route mode, the router should use supervisor override."""
        from openmc_agent.graph import _make_supervisor_aware_router

        router = _make_supervisor_aware_router(
            enable_supervisor=True,
            supervisor_mode="controlled_route",
        )
        state: GraphState = {
            "run_supervisor_route_override": "render",
            "validation_report": MagicMock(is_valid=True),
            "capability_repair_errors": [],
        }
        result = router(state)
        assert result == "render"

    def test_controlled_route_falls_back_when_no_override(self):
        from openmc_agent.graph import _make_supervisor_aware_router

        router = _make_supervisor_aware_router(
            enable_supervisor=True,
            supervisor_mode="controlled_route",
        )
        state: GraphState = {
            "run_supervisor_route_override": "",
            "validation_report": MagicMock(is_valid=True),
            "capability_repair_errors": [],
        }
        result = router(state)
        assert result in {"reflect", "ask"}

    def test_controlled_route_unknown_override_ignored(self):
        from openmc_agent.graph import _make_supervisor_aware_router

        router = _make_supervisor_aware_router(
            enable_supervisor=True,
            supervisor_mode="controlled_route",
        )
        state: GraphState = {
            "run_supervisor_route_override": "invalid_route",
            "validation_report": MagicMock(is_valid=True),
            "capability_repair_errors": [],
        }
        result = router(state)
        assert result in {"reflect", "ask"}

    def test_disabled_supervisor_uses_original(self):
        from openmc_agent.graph import _make_supervisor_aware_router

        router = _make_supervisor_aware_router(
            enable_supervisor=False,
            supervisor_mode="advisory",
        )
        state: GraphState = {
            "run_supervisor_route_override": "render",
            "validation_report": MagicMock(is_valid=True),
            "capability_repair_errors": [],
        }
        result = router(state)
        assert result in {"reflect", "ask"}
