import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

from openmc_agent.executor import render_openmc_smoke_test_script
from openmc_agent.schemas import SimulationPlan, ValidationReport


@dataclass(frozen=True)
class ToolResult:
    name: str
    ok: bool
    command: list[str] = field(default_factory=list)
    returncode: int | None = None
    stdout: str = ""
    stderr: str = ""
    artifacts: list[str] = field(default_factory=list)
    error: str = ""

    def model_dump(self) -> dict:
        return {
            "name": self.name,
            "ok": self.ok,
            "command": self.command,
            "returncode": self.returncode,
            "stdout": self.stdout,
            "stderr": self.stderr,
            "artifacts": self.artifacts,
            "error": self.error,
        }


def export_xml(model_path: str | Path, *, timeout: float = 60.0) -> ToolResult:
    path = Path(model_path)
    command = [sys.executable, path.name]
    result = subprocess.run(
        command,
        cwd=path.parent,
        text=True,
        capture_output=True,
        timeout=timeout,
        check=False,
    )
    return ToolResult(
        name="export_xml",
        ok=result.returncode == 0,
        command=command,
        returncode=result.returncode,
        stdout=result.stdout,
        stderr=result.stderr,
        artifacts=_existing_xml_artifacts(path.parent),
        error="" if result.returncode == 0 else (result.stderr or result.stdout).strip(),
    )


def run_geometry_plots(run_dir: str | Path, *, timeout: float = 60.0) -> ToolResult:
    path = Path(run_dir)
    command = ["openmc", "-p"]
    result = subprocess.run(
        command,
        cwd=path,
        text=True,
        capture_output=True,
        timeout=timeout,
        check=False,
    )
    return ToolResult(
        name="run_geometry_plots",
        ok=result.returncode == 0,
        command=command,
        returncode=result.returncode,
        stdout=result.stdout,
        stderr=result.stderr,
        artifacts=_plot_artifacts(path),
        error="" if result.returncode == 0 else (result.stderr or result.stdout).strip(),
    )


def run_smoke_test(
    run_dir: str | Path,
    plan: SimulationPlan,
    *,
    max_particles: int = 1000,
    max_batches: int = 20,
    timeout: float = 120.0,
) -> ToolResult:
    settings = plan.execution_check.settings
    if settings.particles > max_particles or settings.batches > max_batches:
        return ToolResult(
            name="run_smoke_test",
            ok=False,
            error=(
                "Smoke test settings exceed safety limits: "
                f"particles={settings.particles} max_particles={max_particles}, "
                f"batches={settings.batches} max_batches={max_batches}"
            ),
        )

    path = Path(run_dir)
    path.mkdir(parents=True, exist_ok=True)
    smoke_model_path = path / "smoke_model.py"
    smoke_model_path.write_text(render_openmc_smoke_test_script(plan), encoding="utf-8")

    export_command = [sys.executable, smoke_model_path.name]
    export_result = subprocess.run(
        export_command,
        cwd=path,
        text=True,
        capture_output=True,
        timeout=timeout,
        check=False,
    )
    if export_result.returncode != 0:
        return ToolResult(
            name="run_smoke_test",
            ok=False,
            command=export_command,
            returncode=export_result.returncode,
            stdout=export_result.stdout,
            stderr=export_result.stderr,
            artifacts=[str(smoke_model_path), *_existing_xml_artifacts(path)],
            error=(export_result.stderr or export_result.stdout).strip(),
        )

    run_command = ["openmc"]
    run_result = subprocess.run(
        run_command,
        cwd=path,
        text=True,
        capture_output=True,
        timeout=timeout,
        check=False,
    )
    return ToolResult(
        name="run_smoke_test",
        ok=run_result.returncode == 0,
        command=run_command,
        returncode=run_result.returncode,
        stdout=run_result.stdout,
        stderr=run_result.stderr,
        artifacts=[str(smoke_model_path), *_existing_xml_artifacts(path), *_statepoint_artifacts(path)],
        error="" if run_result.returncode == 0 else (run_result.stderr or run_result.stdout).strip(),
    )


def parse_openmc_output(stdout: str, stderr: str) -> ValidationReport:
    combined = f"{stdout}\n{stderr}"
    lowered = combined.lower()
    errors: list[str] = []
    warnings: list[str] = []

    if "cross_sections.xml" in lowered or "cross section" in lowered:
        errors.append("OpenMC cross section data is missing or not configured.")
    if "could not be located in any cell" in lowered or "undefined" in lowered:
        errors.append("OpenMC reported an undefined region or geometry containment issue.")
    if "overlap" in lowered:
        errors.append("OpenMC reported a possible geometry overlap.")
    if "lost particle" in lowered or "lost particles" in lowered:
        errors.append("OpenMC reported lost particles.")
    if "traceback (most recent call last)" in lowered:
        errors.append("Python traceback occurred while preparing or running OpenMC.")
    if "warning" in lowered:
        warnings.append(_first_matching_line(combined, "warning"))

    return ValidationReport(is_valid=not errors, errors=errors, warnings=warnings)


def _existing_xml_artifacts(path: Path) -> list[str]:
    names = ("materials.xml", "geometry.xml", "settings.xml", "tallies.xml", "plots.xml")
    return [str(path / name) for name in names if (path / name).exists()]


def _plot_artifacts(path: Path) -> list[str]:
    artifacts = [*path.glob("*.png"), *path.glob("*.ppm")]
    return [str(artifact) for artifact in sorted(artifacts)]


def _statepoint_artifacts(path: Path) -> list[str]:
    return [str(artifact) for artifact in sorted(path.glob("statepoint.*.h5"))]


def _first_matching_line(text: str, pattern: str) -> str:
    lowered_pattern = pattern.lower()
    for line in text.splitlines():
        if lowered_pattern in line.lower():
            return line.strip()
    return ""
