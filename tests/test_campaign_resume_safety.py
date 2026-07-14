"""Tests for safe campaign resume (S9)."""

from __future__ import annotations

import json
from pathlib import Path

from openmc_agent.real_campaign import _check_resume_config_match


def test_matching_configs_safe_to_resume():
    old = {
        "git_sha": "abc123", "input_sha": "def456", "model": "deepseek:deepseek-chat",
        "temperature": 0.0, "profile": "qualification", "requested_runs": 10,
        "configuration": {"runtime_supervisor_mode": "deterministic",
                          "max_runtime_iterations": 4, "max_llm_calls": 16},
    }
    new = dict(old)
    mismatches = _check_resume_config_match(old, new)
    assert mismatches == []


def test_different_model_blocks_resume():
    old = {"model": "deepseek:deepseek-chat"}
    new = {"model": "openai:gpt-4"}
    mismatches = _check_resume_config_match(old, new)
    assert "model" in mismatches


def test_different_temperature_blocks_resume():
    old = {"temperature": 0.0}
    new = {"temperature": 0.7}
    mismatches = _check_resume_config_match(old, new)
    assert "temperature" in mismatches


def test_different_requested_runs_blocks_resume():
    old = {"requested_runs": 10}
    new = {"requested_runs": 5}
    mismatches = _check_resume_config_match(old, new)
    assert "requested_runs" in mismatches


def test_different_runtime_supervisor_blocks_resume():
    old = {"configuration": {"runtime_supervisor_mode": "deterministic"}}
    new = {"configuration": {"runtime_supervisor_mode": "real"}}
    mismatches = _check_resume_config_match(old, new)
    assert "runtime_supervisor_mode" in mismatches


def test_different_max_llm_calls_blocks_resume():
    old = {"configuration": {"max_llm_calls": 16}}
    new = {"configuration": {"max_llm_calls": 32}}
    mismatches = _check_resume_config_match(old, new)
    assert "max_llm_calls" in mismatches


def test_different_git_sha_blocks_resume():
    old = {"git_sha": "abc123"}
    new = {"git_sha": "def456"}
    mismatches = _check_resume_config_match(old, new)
    assert "git_sha" in mismatches


def test_different_input_sha_blocks_resume():
    old = {"input_sha": "aaa"}
    new = {"input_sha": "bbb"}
    mismatches = _check_resume_config_match(old, new)
    assert "input_sha" in mismatches
