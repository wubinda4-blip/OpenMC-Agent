from __future__ import annotations

import subprocess
import sys


SCRIPT = "scripts/check_environment.py"


def test_check_environment_allows_missing_openmc() -> None:
    result = subprocess.run(
        [sys.executable, SCRIPT],
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0
    assert "Python version:" in result.stdout
    assert "OpenMC import:" in result.stdout


def test_check_environment_require_openmc_fails_when_missing() -> None:
    probe = subprocess.run(
        [sys.executable, "-c", "import importlib.util; raise SystemExit(0 if importlib.util.find_spec('openmc') is None else 1)"],
        check=False,
    )
    if probe.returncode != 0:
        # This environment has OpenMC, so strict mode should be allowed to pass.
        result = subprocess.run(
            [sys.executable, SCRIPT, "--require-openmc"],
            text=True,
            capture_output=True,
            check=False,
        )
        assert result.returncode == 0
        assert "OpenMC import: ok" in result.stdout
        return

    result = subprocess.run(
        [sys.executable, SCRIPT, "--require-openmc"],
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode != 0
    assert "OpenMC import: fail" in result.stdout
