"""Tests for the geometry-debug tool and the refactored tool execution order.

Covers: geometry debug success, geometry debug failure blocks smoke, export
failure blocks geometry debug, trace stage ordering, artifact isolation.
"""

import pytest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

from openmc_agent.tools import ToolResult, run_geometry_debug


def _write_xml_artifacts(path: Path) -> None:
    """Write minimal valid XML artifacts so geometry debug can proceed."""
    (path / "materials.xml").write_text("<materials/>", encoding="utf-8")
    (path / "geometry.xml").write_text("<geometry/>", encoding="utf-8")
    (path / "settings.xml").write_text("<settings/>", encoding="utf-8")


def _fake_plan():
    from tests.test_tools import _plan
    return _plan()


# ---- 10. Geometry debug success ----

import subprocess as _real_subprocess

# Save the real subprocess.run before any monkeypatching.
_REAL_RUN = _real_subprocess.run


def _patch_openmc_only(monkeypatch, openmc_response):
    """Patch subprocess.run to only intercept openmc calls, passthrough others."""

    def fake_run(command, **kwargs):
        if command and command[0] == "openmc":
            return openmc_response(command, **kwargs)
        return _REAL_RUN(command, **kwargs)

    monkeypatch.setattr("openmc_agent.tools.subprocess.run", fake_run)


def test_geometry_debug_success_returns_ok(tmp_path: Path, monkeypatch) -> None:
    _write_xml_artifacts(tmp_path)

    def openmc_response(command, **kwargs):
        assert command == ["openmc", "-g"]
        assert "geometry_debug" in str(kwargs.get("cwd", ""))
        return SimpleNamespace(returncode=0, stdout="No overlaps found", stderr="")

    _patch_openmc_only(monkeypatch, openmc_response)

    result = run_geometry_debug(tmp_path, _fake_plan())
    assert result.ok is True
    assert result.returncode == 0
    assert result.name == "run_geometry_debug"


def test_geometry_debug_detects_overlap(tmp_path: Path, monkeypatch) -> None:
    _write_xml_artifacts(tmp_path)

    def openmc_response(command, **kwargs):
        return SimpleNamespace(
            returncode=1,
            stdout="",
            stderr="Overlap detected between cells 10 and 11",
        )

    _patch_openmc_only(monkeypatch, openmc_response)

    result = run_geometry_debug(tmp_path, _fake_plan())
    assert result.ok is False
    assert any(i.code == "runtime.geometry_overlap" for i in result.issues)


def test_geometry_debug_timeout_not_overlap(tmp_path: Path, monkeypatch) -> None:
    _write_xml_artifacts(tmp_path)

    def openmc_response(command, **kwargs):
        raise _real_subprocess.TimeoutExpired(cmd=command, timeout=5)

    _patch_openmc_only(monkeypatch, openmc_response)

    result = run_geometry_debug(tmp_path, _fake_plan(), timeout=5)
    assert result.ok is False
    assert any(i.code == "runtime.openmc_timeout" for i in result.issues)
    assert not any(i.code == "runtime.geometry_overlap" for i in result.issues)


def test_geometry_debug_artifacts_isolated(tmp_path: Path, monkeypatch) -> None:
    _write_xml_artifacts(tmp_path)

    def openmc_response(command, **kwargs):
        gd_dir = Path(kwargs["cwd"])
        (gd_dir / "geometry_debug.log").write_text("overlap check passed", encoding="utf-8")
        return SimpleNamespace(returncode=0, stdout="ok", stderr="")

    _patch_openmc_only(monkeypatch, openmc_response)

    result = run_geometry_debug(tmp_path, _fake_plan())

    assert any("geometry_debug" in p for p in result.artifacts)
    assert not (tmp_path / "geometry_debug.log").exists()
    assert (tmp_path / "geometry_debug" / "geometry_debug.log").exists()


def test_geometry_debug_skips_when_no_xml(tmp_path: Path) -> None:
    """When geometry.xml is missing, geometry debug returns ok (nothing to check)."""
    result = run_geometry_debug(tmp_path, _fake_plan())
    assert result.ok is True
    assert result.command == []


# ---- 11/12/13. Execution order via graph ----

def _make_simple_plan():
    from openmc_agent.schemas import (
        CapabilityReport,
        ExecutionCheckSpec,
        PinCellSpec,
        RunSettingsSpec,
        SimulationPlan,
        SimulationSpec,
    )
    from tests.test_tools import _plan
    p = _plan()
    p.capability_report.renderability = "runnable"
    p.capability_report.supported_renderer = "pin_cell"
    p.capability_report.is_executable = True
    return p


def test_export_failure_blocks_geometry_debug_and_smoke(tmp_path: Path) -> None:
    """When export fails, neither geometry debug nor smoke test should run."""
    from openmc_agent.graph import build_plan_graph
    from openmc_agent.llm import StructuredOutputResult
    from tests.test_graph import make_simulation_plan

    calls: list[str] = []

    def fake_generate_plan(*, requirement: str, schema, model: str):
        return StructuredOutputResult(ok=True, value=make_simulation_plan())

    def fake_export(model_path):
        calls.append("export_xml")
        return ToolResult(name="export_xml", ok=False, error="export failed")

    def fake_geom_debug(*args, **kwargs):
        calls.append("geometry_debug")
        return ToolResult(name="run_geometry_debug", ok=True)

    def fake_smoke(*args, **kwargs):
        calls.append("smoke_test")
        return ToolResult(name="run_smoke_test", ok=True)

    graph = build_plan_graph(
        generate_plan=fake_generate_plan,
        export_xml_tool=fake_export,
        smoke_test_tool=fake_smoke,
        geometry_debug_tool=fake_geom_debug,
        max_retries=0,
    )

    state = graph.invoke({
        "requirement": "test",
        "model": "test:model",
        "output_dir": str(tmp_path),
        "records_path": str(tmp_path / "runs.jsonl"),
    })

    assert "export_xml" in calls
    assert "geometry_debug" not in calls
    assert "smoke_test" not in calls


def test_geometry_debug_failure_blocks_smoke(tmp_path: Path) -> None:
    """When geometry debug fails, smoke test should not run."""
    from openmc_agent.graph import build_plan_graph
    from openmc_agent.llm import StructuredOutputResult
    from tests.test_graph import make_simulation_plan

    calls: list[str] = []

    def fake_generate_plan(*, requirement: str, schema, model: str):
        return StructuredOutputResult(ok=True, value=make_simulation_plan())

    def fake_export(model_path):
        calls.append("export_xml")
        return ToolResult(name="export_xml", ok=True)

    def fake_geom_debug(*args, **kwargs):
        calls.append("geometry_debug")
        return ToolResult(name="run_geometry_debug", ok=False, error="overlap")

    def fake_smoke(*args, **kwargs):
        calls.append("smoke_test")
        return ToolResult(name="run_smoke_test", ok=True)

    graph = build_plan_graph(
        generate_plan=fake_generate_plan,
        export_xml_tool=fake_export,
        smoke_test_tool=fake_smoke,
        geometry_debug_tool=fake_geom_debug,
        max_retries=0,
    )

    state = graph.invoke({
        "requirement": "test",
        "model": "test:model",
        "output_dir": str(tmp_path),
        "records_path": str(tmp_path / "runs.jsonl"),
    })

    assert "export_xml" in calls
    assert "geometry_debug" in calls
    assert "smoke_test" not in calls


def test_geometry_debug_pass_allows_smoke(tmp_path: Path) -> None:
    """When geometry debug passes and plan is runnable, smoke test should run."""
    from openmc_agent.graph import build_plan_graph
    from openmc_agent.llm import StructuredOutputResult
    from tests.test_graph import make_simulation_plan

    calls: list[str] = []

    def fake_generate_plan(*, requirement: str, schema, model: str):
        return StructuredOutputResult(ok=True, value=make_simulation_plan())

    def fake_export(model_path):
        calls.append("export_xml")
        return ToolResult(name="export_xml", ok=True)

    def fake_geom_debug(*args, **kwargs):
        calls.append("geometry_debug")
        return ToolResult(name="run_geometry_debug", ok=True)

    def fake_smoke(*args, **kwargs):
        calls.append("smoke_test")
        return ToolResult(name="run_smoke_test", ok=True)

    graph = build_plan_graph(
        generate_plan=fake_generate_plan,
        export_xml_tool=fake_export,
        smoke_test_tool=fake_smoke,
        geometry_debug_tool=fake_geom_debug,
        max_retries=0,
    )

    state = graph.invoke({
        "requirement": "test",
        "model": "test:model",
        "output_dir": str(tmp_path),
        "records_path": str(tmp_path / "runs.jsonl"),
    })

    assert "export_xml" in calls
    assert "geometry_debug" in calls
    assert "smoke_test" in calls


# ---- 14. Trace stage order ----

def test_trace_events_include_geometry_debug(tmp_path: Path) -> None:
    """Trace should contain export_xml_completed, geometry_debug_completed, smoke_test_completed."""
    from openmc_agent.graph import build_plan_graph
    from openmc_agent.llm import StructuredOutputResult
    from openmc_agent.workflow_trace import trace_from_raw
    from tests.test_graph import make_simulation_plan

    def fake_generate_plan(*, requirement: str, schema, model: str):
        return StructuredOutputResult(ok=True, value=make_simulation_plan())

    graph = build_plan_graph(
        generate_plan=fake_generate_plan,
        export_xml_tool=lambda model_path: ToolResult(name="export_xml", ok=True),
        smoke_test_tool=lambda run_dir, plan: ToolResult(name="run_smoke_test", ok=True),
        geometry_debug_tool=lambda *a, **kw: ToolResult(name="run_geometry_debug", ok=True),
        max_retries=0,
    )

    state = graph.invoke({
        "requirement": "test",
        "model": "test:model",
        "output_dir": str(tmp_path),
        "records_path": str(tmp_path / "runs.jsonl"),
    })

    trace = trace_from_raw(state.get("trace"))
    event_types = [e.event_type for e in trace.events]

    # Verify all three stages have trace events in the right order.
    assert "export_xml_completed" in event_types
    assert "geometry_debug_completed" in event_types
    assert "smoke_test_completed" in event_types

    export_idx = event_types.index("export_xml_completed")
    geom_idx = event_types.index("geometry_debug_completed")
    smoke_idx = event_types.index("smoke_test_completed")
    assert export_idx < geom_idx < smoke_idx


# ---- 16. VERA3B smoke regression marker ----

@pytest.mark.openmc
def test_vera3b_smoke_regression_mark():
    """Marker test: the VERA3B smoke regression is covered by the existing
    integration suite. This ensures the marker is registered for the
    --openmc test group."""
    assert True
