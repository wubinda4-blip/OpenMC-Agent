#!/usr/bin/env python3
"""Report the local OpenMC-Agent development environment status."""

from __future__ import annotations

import argparse
import importlib.util
import os
import shutil
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--require-openmc",
        action="store_true",
        help="return a non-zero exit code when the OpenMC Python package is missing",
    )
    args = parser.parse_args()

    project_ok, project_detail = _check_project_import()
    openmc_ok, openmc_detail = _check_import("openmc")
    cross_sections = os.environ.get("OPENMC_CROSS_SECTIONS")
    conda_env = os.environ.get("CONDA_DEFAULT_ENV") or os.environ.get("ENV_NAME") or "not set"
    micromamba_path = shutil.which("micromamba")

    print(f"Python executable: {sys.executable}")
    print(f"Python version: {sys.version.split()[0]}")
    print(f"Project import: {'ok' if project_ok else 'fail'} ({project_detail})")
    print(f"OpenMC import: {'ok' if openmc_ok else 'fail'} ({openmc_detail})")
    print(f"OPENMC_CROSS_SECTIONS: {'set' if cross_sections else 'not set'}")
    print(f"Conda env: {conda_env}")
    print(
        "Micromamba: "
        + (f"available ({micromamba_path})" if micromamba_path else "not available")
    )

    if args.require_openmc and not openmc_ok:
        return 1
    return 0


def _check_project_import() -> tuple[bool, str]:
    try:
        import openmc_agent  # noqa: F401
    except Exception as exc:  # pragma: no cover - diagnostic detail
        return False, f"{type(exc).__name__}: {exc}"
    return True, "openmc_agent"


def _check_import(module_name: str) -> tuple[bool, str]:
    if importlib.util.find_spec(module_name) is None:
        return False, "module not found"
    try:
        completed = subprocess.run(
            [sys.executable, "-c", f"import {module_name}; print(getattr({module_name}, '__version__', 'unknown'))"],
            text=True,
            capture_output=True,
            check=False,
            timeout=10,
            cwd=str(Path.cwd()),
        )
    except Exception as exc:  # pragma: no cover - diagnostic detail
        return False, f"{type(exc).__name__}: {exc}"
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout).strip() or "import failed"
        return False, detail
    return True, f"version {completed.stdout.strip() or 'unknown'}"


if __name__ == "__main__":
    raise SystemExit(main())
