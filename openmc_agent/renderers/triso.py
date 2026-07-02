"""TRISO / pebble renderer wrapping the existing executor helpers."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from openmc_agent.executor import render_openmc_triso_script
from openmc_agent.renderers.base import BaseRenderer, RenderResult, low_cost_runnable
from openmc_agent.renderers.skeleton import (
    _write_capability_report,
    _write_todo,
    emit_skeleton,
)
from openmc_agent.schemas import (
    ComplexModelSpec,
    PebbleSpec,
    RenderCapabilityReport,
    SimulationPlan,
    TRISOSpec,
)
from openmc_agent.validator import validate_openmc_script


class TrisoRenderer(BaseRenderer):
    name = "triso"
    supported_kinds = ("triso_compact", "pebble")

    def can_render(self, plan: SimulationPlan) -> RenderCapabilityReport:
        model = plan.complex_model
        if model is None or model.kind not in {"triso_compact", "pebble"}:
            return RenderCapabilityReport(
                renderability="none",
                is_executable=False,
                supported_renderer="none",
                reasons=["triso renderer requires kind='triso_compact' or 'pebble'"],
            )
        errors = _triso_renderability_errors(model)
        if errors:
            return RenderCapabilityReport(
                renderability="skeleton",
                is_executable=False,
                supported_renderer="triso",
                unsupported_subsystems=_triso_subsystems(model),
                reasons=errors,
            )
        renderability = "runnable" if low_cost_runnable(plan) else "exportable"
        return RenderCapabilityReport(
            renderability=renderability,
            supported_renderer="triso",
            executable_subsystems=["materials", "triso_layers", "packing", "pebble"],
            reasons=["Current executor supports TRISO/pebble unit rendering."],
        )

    def render(self, plan: SimulationPlan, outdir: Path) -> RenderResult:
        capability = self.can_render(plan)
        model = plan.complex_model
        assert model is not None
        if capability.renderability in {"exportable", "runnable"}:
            try:
                script = render_openmc_triso_script(model, plot_specs=plan.plot_specs)
            except ValueError as exc:
                return emit_skeleton(self.name, plan, outdir, _skeleton_capability(model, str(exc)))
            script_report = validate_openmc_script(script)
            if not script_report.is_valid:
                return emit_skeleton(
                    self.name, plan, outdir, _skeleton_capability(model, "; ".join(script_report.errors))
                )
            return _write_full_model(self.name, outdir, script, capability)
        return emit_skeleton(self.name, plan, outdir, capability)


def _write_full_model(renderer_name, outdir, script, capability) -> RenderResult:
    outdir.mkdir(parents=True, exist_ok=True)
    model_path = outdir / "model.py"
    model_path.write_text(script, encoding="utf-8")
    files = [str(model_path), _write_capability_report(outdir, capability)]
    todo = _write_todo(outdir, renderer_name, capability)
    if todo is not None:
        files.append(todo)
    return RenderResult(
        renderer_name=renderer_name,
        renderability=capability.renderability,
        is_executable=capability.is_executable,
        script=script,
        output_files=files,
        warnings=capability.warnings,
        errors=[],
        capability=capability,
    )


def _skeleton_capability(model: ComplexModelSpec, extra_reason: str) -> RenderCapabilityReport:
    return RenderCapabilityReport(
        renderability="skeleton",
        is_executable=False,
        supported_renderer="triso",
        unsupported_subsystems=_triso_subsystems(model),
        reasons=[
            "TRISO/pebble IR could not be exported safely; generating a review-only skeleton.",
            extra_reason,
        ],
    )


def _triso_renderability_errors(model: Any) -> list[str]:
    errors: list[str] = []
    if not model.materials:
        errors.append("triso renderer requires materials")
    material_ids = {material.id for material in model.materials}
    for material in model.materials:
        if material.density_unit is None or material.density_value is None:
            errors.append(f"material {material.id!r} is missing density")
        if not material.composition and not material.chemical_formula:
            errors.append(f"material {material.id!r} is missing composition or chemical_formula")
    if not model.trisos:
        errors.append("triso renderer requires at least one TRISOSpec")
        return errors
    triso: TRISOSpec = model.trisos[0]
    for layer in triso.layers:
        if layer.material_id not in material_ids:
            errors.append(f"TRISO layer {layer.name!r} references missing material")
    pebble: PebbleSpec | None = model.pebbles[0] if model.pebbles else None
    matrix_material_id = (
        pebble.matrix_material_id
        if pebble is not None and pebble.matrix_material_id is not None
        else triso.matrix_material_id
    )
    if matrix_material_id is None or matrix_material_id not in material_ids:
        errors.append("triso renderer requires a matrix material present in materials")
    container_radius = (
        pebble.outer_radius_cm if pebble is not None else triso.layers[-1].outer_radius_cm * 5.0
    )
    fuel_zone_radius = (
        pebble.fuel_zone_radius_cm
        if pebble is not None and pebble.fuel_zone_radius_cm is not None
        else container_radius
    )
    if fuel_zone_radius > container_radius:
        errors.append("TRISO fuel zone radius must not exceed container radius")
    if triso.layers[-1].outer_radius_cm >= fuel_zone_radius:
        errors.append("TRISO outer radius must be less than fuel zone radius")
    return errors


def _triso_subsystems(model: ComplexModelSpec) -> list[str]:
    present = []
    for name, values in (
        ("materials", model.materials),
        ("trisos", model.trisos),
        ("packed_spheres", model.packed_spheres),
        ("pebbles", model.pebbles),
    ):
        if values:
            present.append(name)
    return present
