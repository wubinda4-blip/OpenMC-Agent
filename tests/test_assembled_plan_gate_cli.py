"""Tests for CLI --assembled-plan-review-mode flag."""

import subprocess
import sys


def test_cli_flag_appears_in_help():
    result = subprocess.run(
        [sys.executable, "-m", "openmc_agent.inspect", "--help"],
        capture_output=True, text=True, timeout=30,
    )
    assert "--assembled-plan-review-mode" in result.stdout


def test_cli_flag_choices():
    result = subprocess.run(
        [sys.executable, "-m", "openmc_agent.inspect", "--help"],
        capture_output=True, text=True, timeout=30,
    )
    assert "off" in result.stdout
    assert "controlled" in result.stdout
