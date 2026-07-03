"""Rectangular-core renderer wrapping the existing executor helpers."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from openmc_agent.executor import render_openmc_core_script
from openmc_agent.renderers.base import BaseRenderer, RenderResult, low_cost_runnable
from openmc_agent.renderers.skeleton import emit_skeleton
from openmc_agent.renderers.triso import _write_full_model
from openmc_agent.schemas import (
    ComplexModelSpec,
    RenderCapabilityReport,
    SimulationPlan,
)
from openmc_agent.validator import validate_openmc_script


class CoreRenderer(BaseRenderer):
    name = "core"
    supported_kinds = ("core",)

    def can_render(self, plan: SimulationPlan) -> RenderCapabilityReport:
        model = plan.complex_model
        if model is None or model.kind != "core":
            return RenderCapabilityReport(
                renderability="none",
                is_executable=False,
                supported_renderer="none",
                reasons=["core renderer requires kind='core'"],
            )
        errors = _core_renderability_errors(model)
        if errors:
            return RenderCapabilityReport(
                renderability="skeleton",
                is_executable=False,
                supported_renderer="core",
                unsupported_subsystems=_core_subsystems(model),
                reasons=errors,
            )
        renderability = "runnable" if low_cost_runnable(plan) else "exportable"
        return RenderCapabilityReport(
            renderability=renderability,
            supported_renderer="core",
            executable_subsystems=["materials", "cells", "universes", "rect_lattice", "core"],
            reasons=["Current executor supports rectangular core lattice rendering."],
        )

    def render(self, plan: SimulationPlan, outdir: Path) -> RenderResult:
        capability = self.can_render(plan)
        model = plan.complex_model
        assert model is not None
        if capability.renderability in {"exportable", "runnable"}:
            try:
                script = render_openmc_core_script(model, plot_specs=plan.plot_specs)
            except ValueError as exc:
                return emit_skeleton(self.name, plan, outdir, _skeleton_capability(model, str(exc)))
            script_report = validate_openmc_script(script)
            if not script_report.is_valid:
                return emit_skeleton(
                    self.name, plan, outdir, _skeleton_capability(model, "; ".join(script_report.errors))
                )
            return _write_full_model(self.name, outdir, script, capability)
        return emit_skeleton(self.name, plan, outdir, capability)


def _skeleton_capability(model: ComplexModelSpec, extra_reason: str) -> RenderCapabilityReport:
    return RenderCapabilityReport(
        renderability="skeleton",
        is_executable=False,
        supported_renderer="core",
        unsupported_subsystems=_core_subsystems(model),
        reasons=[
            "Core IR could not be exported safely; generating a review-only skeleton.",
            extra_reason,
        ],
    )


def _core_renderability_errors(model: Any) -> list[str]:
    errors: list[str] = []
    if model.core is None or model.core.lattice_id is None:
        errors.append("core renderer requires CoreSpec.lattice_id")
    if not model.materials:
        errors.append("core renderer requires materials")
    for material in model.materials:
        if material.macroscopic is not None:
            continue
        if material.density_unit is None or material.density_value is None:
            errors.append(f"material {material.id!r} is missing density")
        if not material.composition and not material.chemical_formula:
            errors.append(f"material {material.id!r} is missing composition or chemical_formula")
    if not model.cells:
        errors.append("core renderer requires cells")
    if not model.universes:
        errors.append("core renderer requires universes")
    if not model.lattices:
        errors.append("core renderer requires a RectLattice")
    if model.lattices and any(lattice.kind != "rect" for lattice in model.lattices):
        errors.append("core renderer currently supports RectLattice only")
    if model.core is not None and model.core.lattice_id is not None:
        if all(lattice.id != model.core.lattice_id for lattice in model.lattices):
            errors.append(f"core references missing lattice_id={model.core.lattice_id!r}")
    cell_ids = {cell.id for cell in model.cells}
    for universe in model.universes:
        missing = [cell_id for cell_id in universe.cell_ids if cell_id not in cell_ids]
        if missing:
            errors.append(f"universe {universe.id!r} references missing cells: {missing}")
    return errors


def _core_subsystems(model: ComplexModelSpec) -> list[str]:
    present = []
    for name, values in (
        ("materials", model.materials),
        ("cells", model.cells),
        ("universes", model.universes),
        ("lattices", model.lattices),
        ("core", [model.core] if model.core is not None else []),
    ):
        if values:
            present.append(name)
    return present
