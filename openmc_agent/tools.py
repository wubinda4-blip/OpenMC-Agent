import subprocess
import sys
import re
import xml.etree.ElementTree as ET
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
    artifacts = _existing_xml_artifacts(path.parent)
    missing_required = _missing_required_xml_artifacts(path.parent)
    closure_error = ""
    if result.returncode == 0 and not missing_required:
        closure_error = _geometry_lattice_reference_error(path.parent / "geometry.xml")
    ok = result.returncode == 0 and not missing_required and not closure_error
    error = ""
    if result.returncode != 0:
        error = (result.stderr or result.stdout).strip()
    elif missing_required:
        error = "export_xml produced no required XML artifacts: " + ", ".join(missing_required)
    elif closure_error:
        error = closure_error
    return ToolResult(
        name="export_xml",
        ok=ok,
        command=command,
        returncode=result.returncode,
        stdout=result.stdout,
        stderr=result.stderr,
        artifacts=artifacts,
        error=error,
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

    if _has_cross_section_error(lowered):
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


def _missing_required_xml_artifacts(path: Path) -> list[str]:
    names = ("materials.xml", "geometry.xml", "settings.xml")
    return [name for name in names if not (path / name).exists()]


def _geometry_lattice_reference_error(geometry_path: Path) -> str:
    try:
        root = ET.parse(geometry_path).getroot()
    except ET.ParseError as exc:
        return f"geometry.xml is not valid XML: {exc}"

    exported_universe_numbers = {
        universe
        for cell in root.findall(".//cell")
        for universe in [cell.attrib.get("universe")]
        if universe is not None
    }
    missing_by_lattice: dict[str, list[str]] = {}
    for lattice in root.findall(".//lattice"):
        lattice_id = lattice.attrib.get("id", "<unknown>")
        referenced: set[str] = set()
        for universes in lattice.findall("universes"):
            if universes.text:
                referenced.update(re.findall(r"-?\d+", universes.text))
        missing = sorted(referenced - exported_universe_numbers, key=int)
        if missing:
            missing_by_lattice[lattice_id] = missing

    if not missing_by_lattice:
        return ""
    details = "; ".join(
        f"lattice {lattice_id} missing universe numbers {missing}"
        for lattice_id, missing in sorted(missing_by_lattice.items(), key=_xml_id_sort_key)
    )
    return f"geometry.xml has dangling lattice universe references: {details}"


def _xml_id_sort_key(item: tuple[str, object]) -> tuple[int, int | str]:
    xml_id = item[0]
    return (0, int(xml_id)) if xml_id.isdigit() else (1, xml_id)


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


def _has_cross_section_error(lowered_text: str) -> bool:
    patterns = (
        r"no\s+cross_sections\.xml\s+was\s+specified",
        r"cross_sections\.xml\s+(?:was\s+)?not\s+(?:specified|found)",
        r"could\s+not\s+(?:find|open|read).{0,120}cross",
        r"unable\s+to\s+(?:find|open|read).{0,120}cross",
        r"failed\s+to\s+(?:find|open|read).{0,120}cross",
        r"no\s+cross\s+section\s+data",
        r"cross\s+section\s+data\s+.*(?:missing|not\s+found|unavailable)",
        r"not\s+present\s+in\s+cross_sections\.xml",
    )
    return any(re.search(pattern, lowered_text) for pattern in patterns)
