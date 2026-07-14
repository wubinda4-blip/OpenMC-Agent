"""Tests for real OpenMC evidence strengthening (S5)."""

from __future__ import annotations

from openmc_agent.real_campaign import RealCampaignRunResult, _verify_real_openmc


def _make_result(**kw):
    defaults = dict(
        run_id="t1", status="completed", final_disposition="",
        started_at="", completed_at="", duration_s=0,
        git_sha="", input_sha="", configuration_hash="",
        provider="deepseek", model="deepseek:deepseek-chat",
        real_llm_verified=True, real_openmc_verified=False, llm_call_count=1,
    )
    defaults.update(kw)
    return RealCampaignRunResult(**defaults)


def test_no_tool_results_not_verified():
    result = _make_result()
    _verify_real_openmc(result, {}, Path("/nonexistent"))
    assert not result.real_openmc_verified
    assert result.export_backend == "skipped"


def test_only_export_not_enough():
    result = _make_result()
    ws = {"tool_results": [
        {"name": "export_xml", "ok": True, "command": ["python", "model.py"]},
    ]}
    _verify_real_openmc(result, ws, Path("/nonexistent"))
    assert not result.real_openmc_verified
    assert result.export_backend == "real_python_export"


def test_mocked_export_blocks_verification():
    result = _make_result()
    ws = {"tool_results": [
        {"name": "export_xml", "ok": True, "mocked": True},
    ]}
    _verify_real_openmc(result, ws, Path("/nonexistent"))
    assert not result.real_openmc_verified


def test_all_three_stages_real():
    result = _make_result()
    ws = {"tool_results": [
        {"name": "export_xml", "ok": True, "command": ["python", "model.py"], "returncode": 0},
        {"name": "run_geometry_debug", "ok": True, "command": ["openmc"], "returncode": 0},
        {"name": "run_smoke_test", "ok": True, "command": ["openmc"], "returncode": 0, "issues": []},
    ]}
    # xml_exported is already True from the tool result but XML files don't exist.
    # _verify_real_openmc will check file existence and set xml_exported=False if missing.
    _verify_real_openmc(result, ws, Path("/nonexistent"))
    # Not verified because XML files don't exist at /nonexistent/workflow/
    assert not result.real_openmc_verified


def test_lost_particle_blocks_verification():
    result = _make_result()
    result.lost_particle_count = 1
    ws = {"tool_results": [
        {"name": "export_xml", "ok": True, "command": ["python", "model.py"], "returncode": 0},
        {"name": "run_geometry_debug", "ok": True, "command": ["openmc"], "returncode": 0},
        {"name": "run_smoke_test", "ok": True, "command": ["openmc"], "returncode": 0,
         "issues": [{"code": "lost_particle"}]},
    ]}
    _verify_real_openmc(result, ws, Path("/nonexistent"))
    assert not result.real_openmc_verified
    assert result.lost_particle_count == 1


def test_source_rejection_blocks_verification():
    result = _make_result()
    ws = {"tool_results": [
        {"name": "export_xml", "ok": True, "command": ["python", "model.py"], "returncode": 0},
        {"name": "run_geometry_debug", "ok": True, "command": ["openmc"], "returncode": 0},
        {"name": "run_smoke_test", "ok": True, "command": ["openmc"], "returncode": 0,
         "issues": [{"code": "source_rejection_critical"}]},
    ]}
    _verify_real_openmc(result, ws, Path("/nonexistent"))
    assert not result.real_openmc_verified
    assert result.source_rejection_count > 0


from pathlib import Path
