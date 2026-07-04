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
    material_ids = {material.id for material in model.materials}
    surface_ids = {surface.id for surface in model.surfaces}
    region_ids = {region.id for region in model.regions}
    composite_region_ids = {
        surface.id
        for surface in model.surfaces
        if surface.kind in {"rectangular_prism", "hexagonal_prism"}
    }
    region_like_ids = region_ids | composite_region_ids
    cell_ids = {cell.id for cell in model.cells}
    universe_ids = {universe.id for universe in model.universes}
    lattice_ids = {lattice.id for lattice in model.lattices}
    for material in model.materials:
        percent_types = {component.percent_type for component in material.composition}
        if (
            material.macroscopic is None
            and len(percent_types) > 1
            and material.chemical_formula is None
        ):
            errors.append(
                f"material {material.id!r} mixes atom and weight percents without "
                "chemical_formula fallback"
            )
    for region in model.regions:
        missing = [surface_id for surface_id in region.surface_ids if surface_id not in surface_ids]
        if missing:
            errors.append(f"region {region.id!r} references missing surfaces: {missing}")
    for cell in model.cells:
        if cell.region_id is not None and cell.region_id not in region_like_ids:
            errors.append(f"cell {cell.id!r} references missing region {cell.region_id!r}")
        if cell.fill_type == "material" and cell.fill_id not in material_ids:
            errors.append(f"cell {cell.id!r} references missing material {cell.fill_id!r}")
        if cell.fill_type == "universe" and cell.fill_id not in universe_ids:
            errors.append(f"cell {cell.id!r} references missing universe {cell.fill_id!r}")
        if cell.fill_type == "lattice" and cell.fill_id not in lattice_ids:
            errors.append(f"cell {cell.id!r} references missing lattice {cell.fill_id!r}")
    for universe in model.universes:
        missing = [cell_id for cell_id in universe.cell_ids if cell_id not in cell_ids]
        if missing:
            errors.append(f"universe {universe.id!r} references missing cells: {missing}")
    empty_universe_ids = {universe.id for universe in model.universes if not universe.cell_ids}
    auto_wrappable_universe_ids = _core_auto_wrappable_universe_ids(model)
    for lattice in model.lattices:
        pattern = lattice.universe_pattern
        if not pattern:
            errors.append(f"lattice {lattice.id!r} requires universe_pattern before export")
            continue
        row_lengths = {len(row) for row in pattern}
        if len(row_lengths) > 1:
            errors.append(f"lattice {lattice.id!r} universe_pattern rows have unequal lengths")
        missing = sorted({universe_id for row in pattern for universe_id in row if universe_id not in universe_ids})
        if missing:
            errors.append(f"lattice {lattice.id!r} references missing universes: {missing}")
        empty_refs = sorted(
            {
                universe_id
                for row in pattern
                for universe_id in row
                if universe_id in empty_universe_ids
                and universe_id not in auto_wrappable_universe_ids
            }
        )
        if empty_refs:
            errors.append(f"lattice {lattice.id!r} references empty universes: {empty_refs}")
        if lattice.outer_universe_id is not None and lattice.outer_universe_id not in universe_ids:
            errors.append(
                f"lattice {lattice.id!r} references missing outer_universe_id "
                f"{lattice.outer_universe_id!r}"
            )
    if model.core is not None:
        for layer in model.core.axial_layers:
            if layer.fill_type == "material" and layer.fill_id not in material_ids:
                errors.append(
                    f"axial layer {layer.id!r} references missing material {layer.fill_id!r}"
                )
            if layer.fill_type == "universe" and layer.fill_id not in universe_ids:
                errors.append(
                    f"axial layer {layer.id!r} references missing universe {layer.fill_id!r}"
                )
            if layer.fill_type == "lattice" and layer.fill_id not in lattice_ids:
                errors.append(
                    f"axial layer {layer.id!r} references missing lattice {layer.fill_id!r}"
                )
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
    if model.core is not None and model.core.axial_layers:
        present.append("axial_layers")
    return present


def _core_auto_wrappable_universe_ids(model: Any) -> set[str]:
    if model.core is None or model.core.lattice_id is None:
        return set()
    universe_by_id = {universe.id: universe for universe in model.universes}
    core_universe_ids = _core_reachable_universe_ids(model)
    direct_core_refs = _core_direct_lattice_universe_ids(model)
    return {
        universe_id
        for universe_id in core_universe_ids
        if universe_id in universe_by_id
        and _core_universe_wrapper_fill_exists(
            model,
            universe_id,
            direct_core_refs=direct_core_refs,
        )
    }


def _core_universe_wrapper_fill_exists(
    model: Any,
    universe_id: str,
    *,
    direct_core_refs: set[str],
) -> bool:
    lattice_ids = {lattice.id for lattice in model.lattices}
    assembly_by_id = {assembly.id: assembly for assembly in model.assemblies}
    assembly = assembly_by_id.get(universe_id)
    if assembly is not None and assembly.lattice_id in lattice_ids:
        return True
    universe_by_id = {universe.id: universe for universe in model.universes}
    universe = universe_by_id.get(universe_id)
    return (
        _core_material_id_for_empty_universe(model, universe_id) is not None
        and (universe is None or not universe.cell_ids or universe_id in direct_core_refs)
    )


def _core_direct_lattice_universe_ids(model: Any) -> set[str]:
    if model.core is None or model.core.lattice_id is None:
        return set()
    core_lattice = next(
        (lattice for lattice in model.lattices if lattice.id == model.core.lattice_id),
        None,
    )
    if core_lattice is None:
        return set()
    return {
        universe_id
        for row in core_lattice.universe_pattern
        for universe_id in row
    }


def _core_reachable_universe_ids(model: Any) -> set[str]:
    if model.core is None or model.core.lattice_id is None:
        return set()
    lattice_by_id = {lattice.id: lattice for lattice in model.lattices}
    cell_by_id = {cell.id: cell for cell in model.cells}
    universe_by_id = {universe.id: universe for universe in model.universes}
    assembly_by_id = {assembly.id: assembly for assembly in model.assemblies}
    pending_lattice_ids = [model.core.lattice_id]
    visited_lattice_ids: set[str] = set()
    reachable_universe_ids: set[str] = set()

    while pending_lattice_ids:
        lattice_id = pending_lattice_ids.pop()
        if lattice_id in visited_lattice_ids:
            continue
        visited_lattice_ids.add(lattice_id)
        lattice = lattice_by_id.get(lattice_id)
        if lattice is None:
            continue
        lattice_universe_ids = {
            universe_id
            for row in lattice.universe_pattern
            for universe_id in row
        }
        if lattice.outer_universe_id is not None:
            lattice_universe_ids.add(lattice.outer_universe_id)
        for universe_id in lattice_universe_ids:
            if universe_id in reachable_universe_ids:
                continue
            reachable_universe_ids.add(universe_id)
            assembly = assembly_by_id.get(universe_id)
            if assembly is not None and assembly.lattice_id is not None:
                pending_lattice_ids.append(assembly.lattice_id)
            universe = universe_by_id.get(universe_id)
            if universe is None:
                continue
            for cell_id in universe.cell_ids:
                cell = cell_by_id.get(cell_id)
                if cell is not None and cell.fill_type == "lattice" and cell.fill_id is not None:
                    pending_lattice_ids.append(cell.fill_id)
    return reachable_universe_ids


def _core_material_id_for_empty_universe(model: Any, universe_id: str) -> str | None:
    material_ids = [material.id for material in model.materials]
    tokens = set(universe_id.split("_"))
    candidates = [
        material_id
        for material_id in material_ids
        if universe_id == material_id
        or universe_id.startswith(f"{material_id}_")
        or universe_id.endswith(f"_{material_id}")
        or material_id in tokens
    ]
    if len(candidates) == 1:
        return candidates[0]
    return None
