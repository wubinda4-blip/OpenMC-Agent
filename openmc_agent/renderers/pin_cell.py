"""Pin-cell renderer wrapping the existing executor helpers."""

from __future__ import annotations

from pathlib import Path

from openmc_agent.executor import render_openmc_script
from openmc_agent.renderers.base import BaseRenderer, RenderResult, low_cost_runnable
from openmc_agent.renderers.skeleton import _write_capability_report, _write_todo
from openmc_agent.schemas import (
    RenderCapabilityReport,
    SimulationPlan,
)
from openmc_agent.validator import validate_openmc_script, validate_simulation_spec


class PinCellRenderer(BaseRenderer):
    """Executable renderer for simple pin-cell ``model_spec`` plans."""

    name = "pin_cell"
    supported_kinds = ("pin_cell",)

    def can_render(self, plan: SimulationPlan) -> RenderCapabilityReport:
        spec = plan.model_spec
        if spec is None:
            return RenderCapabilityReport(
                renderability="none",
                is_executable=False,
                supported_renderer="none",
                reasons=["pin_cell renderer requires model_spec"],
            )
        report = validate_simulation_spec(spec)
        if not report.is_valid:
            return RenderCapabilityReport(
                renderability="none",
                is_executable=False,
                supported_renderer="none",
                reasons=[f"pin_cell model_spec failed validation: {report.errors}"],
            )
        renderability = "runnable" if low_cost_runnable(plan) else "exportable"
        return RenderCapabilityReport(
            renderability=renderability,
            supported_renderer="pin_cell",
            executable_subsystems=["pin_cell"],
            unsupported_subsystems=[],
            reasons=["Current executor supports pin-cell rendering."],
        )

    def render(self, plan: SimulationPlan, outdir: Path) -> RenderResult:
        capability = self.can_render(plan)
        spec = plan.model_spec
        assert spec is not None
        script = render_openmc_script(spec, plot_specs=plan.plot_specs)
        script_report = validate_openmc_script(script, spec)
        if not script_report.is_valid:
            return RenderResult(
                renderer_name=self.name,
                renderability="none",
                is_executable=False,
                errors=script_report.errors,
                capability=capability,
            )
        outdir.mkdir(parents=True, exist_ok=True)
        model_path = outdir / "model.py"
        model_path.write_text(script, encoding="utf-8")
        files = [str(model_path), _write_capability_report(outdir, capability)]
        todo = _write_todo(outdir, self.name, capability)
        if todo is not None:
            files.append(todo)
        return RenderResult(
            renderer_name=self.name,
            renderability=capability.renderability,
            is_executable=capability.is_executable,
            script=script,
            output_files=files,
            warnings=capability.warnings,
            errors=[],
            capability=capability,
        )
