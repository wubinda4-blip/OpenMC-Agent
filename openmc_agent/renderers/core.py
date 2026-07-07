"""Rectangular-core renderer wrapping the existing executor helpers."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from openmc_agent.error_catalog import issue_from_catalog
from openmc_agent.executor import render_openmc_core_script
from openmc_agent.lattice_validation import lattice_pin_count_issues
from openmc_agent.renderers.base import BaseRenderer, RenderResult, low_cost_runnable
from openmc_agent.renderers.skeleton import emit_skeleton
from openmc_agent.renderers.triso import _write_full_model
from openmc_agent.schemas import (
    ComplexModelSpec,
    RenderCapabilityReport,
    SimulationPlan,
    ValidationIssue,
)
from openmc_agent.validator import validate_openmc_script


def _iss(code: str, message: str, schema_path: str = "") -> ValidationIssue:
    """Build a structured diagnostic issue with a stable code."""
    return issue_from_catalog(code, message=message, schema_path=schema_path or None)


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
                reasons=[issue.message for issue in errors],
                issues=errors,
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


def _core_renderability_errors(model: Any) -> list[ValidationIssue]:
    errors: list[ValidationIssue] = []
    if model.core is None or model.core.lattice_id is None:
        errors.append(_iss("core.requires_lattice_id", "core renderer requires CoreSpec.lattice_id", "complex_model.core.lattice_id"))
    if not model.materials:
        errors.append(_iss("core.requires_materials", "core renderer requires materials", "complex_model.materials"))
    for material in model.materials:
        if material.macroscopic is not None:
            continue
        schema_path = f"complex_model.materials.{material.id}"
        if material.density_unit is None or material.density_value is None:
            errors.append(_iss("material.missing_density", f"material {material.id!r} is missing density", schema_path))
        if not material.composition and not material.chemical_formula:
            errors.append(_iss("material.missing_composition", f"material {material.id!r} is missing composition or chemical_formula", schema_path))
    if not model.cells and not _core_has_auto_materializable_missing_cells(model):
        errors.append(_iss("core.requires_cells", "core renderer requires cells", "complex_model.cells"))
    if not model.universes:
        errors.append(_iss("core.requires_universes", "core renderer requires universes", "complex_model.universes"))
    if not model.lattices:
        errors.append(_iss("core.requires_lattice", "core renderer requires a RectLattice", "complex_model.lattices"))
    if model.lattices and any(lattice.kind != "rect" for lattice in model.lattices):
        errors.append(_iss("core.requires_rect_lattice", "core renderer currently supports RectLattice only", "complex_model.lattices"))
    if model.core is not None and model.core.lattice_id is not None:
        if all(lattice.id != model.core.lattice_id for lattice in model.lattices):
            errors.append(_iss("core.lattice_ref_missing", f"core references missing lattice_id={model.core.lattice_id!r}", "complex_model.core.lattice_id"))
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
            errors.append(_iss(
                "material.mixed_percent_type",
                f"material {material.id!r} mixes atom and weight percents without "
                "chemical_formula fallback",
                f"complex_model.materials.{material.id}",
            ))
    for region in model.regions:
        missing = [surface_id for surface_id in region.surface_ids if surface_id not in surface_ids]
        if missing:
            errors.append(_iss(
                "region.surface_ref_missing",
                f"region {region.id!r} references missing surfaces: {missing}",
                f"complex_model.regions.{region.id}.surface_ids",
            ))
    for cell in model.cells:
        if cell.region_id is not None and cell.region_id not in region_like_ids:
            errors.append(_iss(
                "cell.region_ref_missing",
                f"cell {cell.id!r} references missing region {cell.region_id!r}",
                f"complex_model.cells.{cell.id}.region_id",
            ))
        if cell.fill_type == "material" and cell.fill_id not in material_ids:
            errors.append(_iss(
                "cell.material_ref_missing",
                f"cell {cell.id!r} references missing material {cell.fill_id!r}",
                f"complex_model.cells.{cell.id}.fill_id",
            ))
        if cell.fill_type == "universe" and cell.fill_id not in universe_ids:
            errors.append(_iss(
                "cell.universe_ref_missing",
                f"cell {cell.id!r} references missing universe {cell.fill_id!r}",
                f"complex_model.cells.{cell.id}.fill_id",
            ))
        if cell.fill_type == "lattice" and cell.fill_id not in lattice_ids:
            errors.append(_iss(
                "cell.lattice_ref_missing",
                f"cell {cell.id!r} references missing lattice {cell.fill_id!r}",
                f"complex_model.cells.{cell.id}.fill_id",
            ))
    for universe in model.universes:
        missing = [cell_id for cell_id in universe.cell_ids if cell_id not in cell_ids]
        if missing and not _core_missing_cells_auto_repairable(model, universe.id, missing):
            errors.append(_iss(
                "universe.cell_ref_missing",
                f"universe {universe.id!r} references missing cells: {missing}",
                f"complex_model.universes.{universe.id}.cell_ids",
            ))
    empty_universe_ids = {universe.id for universe in model.universes if not universe.cell_ids}
    auto_wrappable_universe_ids = _core_auto_wrappable_universe_ids(model)
    for lattice in model.lattices:
        pattern = lattice.universe_pattern
        schema_base = f"complex_model.lattices.{lattice.id}.universe_pattern"
        if not pattern:
            errors.append(_iss("lattice.pattern_missing", f"lattice {lattice.id!r} requires universe_pattern before export", schema_base))
            continue
        row_lengths = {len(row) for row in pattern}
        if len(row_lengths) > 1:
            errors.append(_iss("lattice.pattern_ragged_rows", f"lattice {lattice.id!r} universe_pattern rows have unequal lengths", schema_base))
        missing = sorted({universe_id for row in pattern for universe_id in row if universe_id not in universe_ids})
        if missing:
            errors.append(_iss("lattice.universe_ref_missing", f"lattice {lattice.id!r} references missing universes: {missing}", schema_base))
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
            errors.append(_iss("lattice.empty_universe_ref", f"lattice {lattice.id!r} references empty universes: {empty_refs}", schema_base))
        if lattice.outer_universe_id is not None and lattice.outer_universe_id not in universe_ids:
            errors.append(_iss(
                "lattice.outer_universe_ref_missing",
                f"lattice {lattice.id!r} references missing outer_universe_id "
                f"{lattice.outer_universe_id!r}",
                f"complex_model.lattices.{lattice.id}.outer_universe_id",
            ))
        errors.extend(lattice_pin_count_issues([lattice], message_style="renderer"))
    if model.core is not None:
        loading_by_id = {loading.id: loading for loading in model.lattice_loadings}
        for layer in model.core.axial_layers:
            layer_fill_schema = f"complex_model.core.axial_layers.{layer.id}.fill.id"
            fill = layer.fill
            if fill.type == "material" and fill.id not in material_ids:
                errors.append(_iss("axial_layer.fill_ref_missing", f"axial layer {layer.id!r} references missing material {fill.id!r}", layer_fill_schema))
            if fill.type == "universe" and fill.id not in universe_ids:
                errors.append(_iss("axial_layer.fill_ref_missing", f"axial layer {layer.id!r} references missing universe {fill.id!r}", layer_fill_schema))
            if fill.type == "lattice":
                loading = loading_by_id.get(layer.loading_id) if layer.loading_id else None
                derived_id = None if loading is None else loading.derived_lattice_id or f"{loading.id}_lattice"
                if fill.id not in lattice_ids and fill.id != derived_id:
                    errors.append(_iss("axial_layer.fill_ref_missing", f"axial layer {layer.id!r} references missing lattice {fill.id!r}", layer_fill_schema))
            if layer.loading_id is not None:
                if fill.type != "lattice":
                    errors.append(_iss(
                        "axial_layer.loading_ref_missing",
                        f"axial layer {layer.id!r} uses loading_id with non-lattice fill",
                        f"complex_model.core.axial_layers.{layer.id}.loading_id",
                    ))
                loading = loading_by_id.get(layer.loading_id)
                if loading is None:
                    errors.append(_iss(
                        "axial_layer.loading_ref_missing",
                        f"axial layer {layer.id!r} references missing loading_id {layer.loading_id!r}",
                        f"complex_model.core.axial_layers.{layer.id}.loading_id",
                    ))
                    continue
                base_lattice = next((lat for lat in model.lattices if lat.id == loading.base_lattice_id), None)
                if base_lattice is None:
                    errors.append(_iss(
                        "lattice_loading.base_ref_missing",
                        f"lattice loading {loading.id!r} references missing base_lattice_id {loading.base_lattice_id!r}",
                        f"complex_model.lattice_loadings.{loading.id}.base_lattice_id",
                    ))
                    continue
                override_schema = f"complex_model.lattice_loadings.{loading.id}.overrides"
                for universe_id, positions in loading.overrides.items():
                    if universe_id not in universe_ids:
                        errors.append(_iss(
                            "lattice_loading.override_universe_ref_missing",
                            f"lattice loading {loading.id!r} override references missing universe {universe_id!r}",
                            override_schema,
                        ))
                    pattern_rows = len(base_lattice.universe_pattern)
                    for row, col in positions:
                        in_rows = 0 <= row < pattern_rows
                        in_cols = in_rows and 0 <= col < len(base_lattice.universe_pattern[row])
                        if not (in_rows and in_cols):
                            errors.append(_iss(
                                "lattice_loading.override_position_oob",
                                f"lattice loading {loading.id!r} override position {(row, col)} for universe {universe_id!r} is out of bounds",
                                override_schema,
                            ))
    for reflector in model.reflectors:
        if reflector.material_id not in material_ids:
            errors.append(_iss(
                "core.reflector_material_ref_missing",
                f"core reflector {reflector.id!r} references missing material {reflector.material_id!r}",
                "complex_model.reflectors.material_id",
            ))
        if reflector.region_id is None or reflector.region_id not in region_like_ids:
            errors.append(_iss(
                "core.reflector_region_ref_missing",
                f"core reflector {reflector.id!r} requires a valid region_id",
                "complex_model.reflectors.region_id",
            ))
    if model.core is not None and model.core.lattice_id is not None:
        core_lattice = next((lat for lat in model.lattices if lat.id == model.core.lattice_id), None)
        if core_lattice is not None and core_lattice.universe_pattern:
            has_ir_boundary = any(
                "core" in (surface.id or "").lower()
                and any(tag in (surface.id or "").lower() for tag in ("xmin", "xmax", "ymin", "ymax"))
                for surface in model.surfaces
            )
            pattern_universes = {
                uid for row in core_lattice.universe_pattern for uid in row
            }
            radial_reflectors = [r for r in model.reflectors if r.location == "radial"]
            if (
                not has_ir_boundary
                and core_lattice.outer_universe_id
                and core_lattice.outer_universe_id not in pattern_universes
            ):
                errors.append(_iss(
                    "core.lattice_outer_unreachable",
                    "lattice.outer_universe_id is set but the root cell equals the active lattice footprint; outer is dead geometry",
                    f"complex_model.lattices.{core_lattice.id}.outer_universe_id",
                ))
            if not has_ir_boundary and radial_reflectors:
                errors.append(_iss(
                    "core.radial_reflector_unreachable",
                    "radial reflector declared but no core boundary surfaces extend the root cell beyond the lattice",
                    "complex_model.reflectors",
                ))
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
        _core_material_id_for_wrapper_universe(model, universe_id) is not None
        and (universe is None or not universe.cell_ids or universe_id in direct_core_refs)
    )


def _core_missing_cells_auto_repairable(
    model: Any,
    universe_id: str,
    missing_cell_ids: list[str],
) -> bool:
    if _core_universe_wrapper_fill_exists(
        model,
        universe_id,
        direct_core_refs=_core_direct_lattice_universe_ids(model),
    ):
        return True
    return all(
        _core_material_id_for_missing_cell(model, universe_id, cell_id) is not None
        for cell_id in missing_cell_ids
    )


def _core_has_auto_materializable_missing_cells(model: Any) -> bool:
    existing_cell_ids = {cell.id for cell in model.cells}
    for universe in model.universes:
        missing = [
            cell_id for cell_id in universe.cell_ids if cell_id not in existing_cell_ids
        ]
        if missing and _core_missing_cells_auto_repairable(model, universe.id, missing):
            return True
    return False


def _core_material_id_for_missing_cell(
    model: Any,
    universe_id: str,
    cell_id: str,
) -> str | None:
    material_ids = {material.id for material in model.materials}
    cell_tokens = set(cell_id.split("_"))
    universe_tokens = set(universe_id.split("_"))
    if cell_tokens & {"mod", "moderator", "water"} and "water" in material_ids:
        return "water"
    if cell_tokens & {"fiss", "chamber"}:
        if "fiss_chamber" in material_ids:
            return "fiss_chamber"
        if "guide_tube" in material_ids:
            return "guide_tube"
    if "guide" in cell_tokens or "guide" in universe_tokens:
        if "guide_tube" in material_ids:
            return "guide_tube"
        if "guide" in material_ids:
            return "guide"
    for token in [*cell_id.split("_"), *universe_id.split("_")]:
        if token in material_ids:
            return token
    if "fuel" in cell_tokens:
        for token in universe_id.split("_"):
            if token in material_ids:
                return token
        if "fuel" in material_ids:
            return "fuel"
    return _core_material_id_for_empty_universe(model, universe_id)


def _core_material_id_for_wrapper_universe(model: Any, universe_id: str) -> str | None:
    if universe_id.startswith("pin_"):
        return None
    tokens = set(universe_id.split("_"))
    material_id = _core_material_id_for_empty_universe(model, universe_id)
    if material_id is None:
        if "water" in {material.id for material in model.materials} and tokens & {"water", "reflector"}:
            return "water"
        return None
    if material_id == "water" or tokens & {"water", "reflector", "moderator", "mod"}:
        return material_id
    return None


def _core_direct_lattice_universe_ids(model: Any) -> set[str]:
    if model.core is None or model.core.lattice_id is None:
        return set()
    core_lattice = next(
        (lattice for lattice in model.lattices if lattice.id == model.core.lattice_id),
        None,
    )
    if core_lattice is None:
        return set()
    universe_ids = {
        universe_id
        for row in core_lattice.universe_pattern
        for universe_id in row
    }
    universe_ids.update(
        layer.fill.id
        for layer in model.core.axial_layers
        if layer.fill.type == "universe" and layer.fill.id is not None
    )
    return universe_ids


def _core_reachable_universe_ids(model: Any) -> set[str]:
    if model.core is None or model.core.lattice_id is None:
        return set()
    lattice_by_id = {lattice.id: lattice for lattice in model.lattices}
    cell_by_id = {cell.id: cell for cell in model.cells}
    universe_by_id = {universe.id: universe for universe in model.universes}
    assembly_by_id = {assembly.id: assembly for assembly in model.assemblies}
    pending_lattice_ids = [model.core.lattice_id]
    pending_universe_ids = [
        layer.fill.id
        for layer in model.core.axial_layers
        if layer.fill.type == "universe" and layer.fill.id is not None
    ]
    visited_lattice_ids: set[str] = set()
    reachable_universe_ids: set[str] = set()

    while pending_lattice_ids or pending_universe_ids:
        if pending_universe_ids:
            lattice_universe_ids = {pending_universe_ids.pop()}
        else:
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
                if cell is not None and cell.fill_type == "universe" and cell.fill_id is not None:
                    pending_universe_ids.append(cell.fill_id)
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
