"""Rectangular-assembly renderer.

Handles ``complex_model.kind == "assembly"`` with at least one ``rect`` lattice.
It reports four capability levels:

- ``runnable``: structure and materials complete and a low-cost smoke test is configured.
- ``exportable``: structure and materials complete enough to export XML.
- ``skeleton``: recognizable assembly IR but missing materials or structural issues;
  emits a review-only skeleton instead.
- ``none``: this renderer does not apply (no complex_model or kind != "assembly").
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from openmc_agent.executor import render_openmc_assembly_script
from openmc_agent.renderers.base import BaseRenderer, RenderResult, low_cost_runnable
from openmc_agent.reachability import (
    ActiveDependencies,
    collect_active_dependencies,
)
from openmc_agent.renderers.skeleton import (
    _write_capability_report,
    _write_todo,
    emit_skeleton,
)
from openmc_agent.schemas import (
    ComplexMaterialSpec,
    ComplexModelSpec,
    LatticeSpec,
    RenderCapabilityReport,
    SimulationPlan,
)
from openmc_agent.validator import validate_openmc_script


_COMPOSITE_REGION_SURFACE_KINDS = {"rectangular_prism", "hexagonal_prism"}


class RectAssemblyRenderer(BaseRenderer):
    name = "assembly"
    supported_kinds = ("assembly",)

    def can_render(self, plan: SimulationPlan) -> RenderCapabilityReport:
        model = plan.complex_model
        if model is None or model.kind != "assembly":
            return RenderCapabilityReport(
                renderability="none",
                is_executable=False,
                supported_renderer="none",
                reasons=["assembly renderer requires complex_model.kind='assembly'"],
            )

        deps = collect_active_dependencies(plan)
        errors, warnings = _assembly_diagnostics(model, deps)
        if errors:
            return RenderCapabilityReport(
                renderability="skeleton",
                is_executable=False,
                supported_renderer="assembly",
                executable_subsystems=[],
                unsupported_subsystems=_assembly_subsystems(model),
                reasons=errors,
                warnings=warnings,
                required_human_confirmations=_material_confirmations(model, deps),
            )
        renderability = "runnable" if low_cost_runnable(plan) else "exportable"
        return RenderCapabilityReport(
            renderability=renderability,
            supported_renderer="assembly",
            executable_subsystems=[
                "materials",
                "cells",
                "universes",
                "rect_lattice",
                "assembly",
            ],
            unsupported_subsystems=[],
            reasons=["Current executor supports rectangular assembly lattice rendering."],
            warnings=warnings,
            required_human_confirmations=_material_confirmations(model, deps),
        )

    def render(self, plan: SimulationPlan, outdir: Path) -> RenderResult:
        outdir.mkdir(parents=True, exist_ok=True)
        capability = self.can_render(plan)
        model = plan.complex_model
        assert model is not None

        if capability.renderability in {"exportable", "runnable"}:
            try:
                script = render_openmc_assembly_script(model, plot_specs=plan.plot_specs)
            except ValueError as exc:
                # Defensive: if the executor rejects what can_render accepted,
                # fall back to a skeleton so the user still gets reviewable output.
                return emit_skeleton(self.name, plan, outdir, _skeleton_capability(plan, str(exc)))
            script_report = validate_openmc_script(script)
            if not script_report.is_valid:
                fallback = _skeleton_capability(plan, "; ".join(script_report.errors))
                return emit_skeleton(self.name, plan, outdir, fallback)
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

        # skeleton mode
        return emit_skeleton(self.name, plan, outdir, capability)


# -- diagnostics ----------------------------------------------------------


def _assembly_diagnostics(
    model: ComplexModelSpec,
    deps: ActiveDependencies,
) -> tuple[list[str], list[str]]:
    """Return (blocking_errors, warnings). Empty error list means exportable.

    ``deps`` partitions declared objects by reachability from the default
    lattice. Only *active* materials can block the default model; gaps in
    candidate / inactive materials become warnings instead.
    """
    errors: list[str] = []
    warnings: list[str] = []

    # Check 2: at least one rect lattice.
    rect_lattices = [lat for lat in model.lattices if lat.kind == "rect"]
    if not model.lattices:
        errors.append("assembly renderer requires a RectLattice")
    elif not rect_lattices:
        errors.append("assembly renderer currently supports RectLattice only")

    if not model.assemblies or model.assemblies[0].lattice_id is None:
        errors.append("assembly renderer requires an AssemblySpec with lattice_id")

    material_ids = {material.id for material in model.materials}
    region_ids = {region.id for region in model.regions}
    composite_region_ids = {
        surface.id
        for surface in model.surfaces
        if surface.kind in _COMPOSITE_REGION_SURFACE_KINDS
    }
    region_like_ids = region_ids | composite_region_ids
    surface_ids = {surface.id for surface in model.surfaces}
    universe_ids = {universe.id for universe in model.universes}
    cell_ids = {cell.id for cell in model.cells}

    if not model.cells:
        errors.append("assembly renderer requires cells")
    if not model.universes:
        errors.append("assembly renderer requires universes")
    if not model.materials:
        errors.append("assembly renderer requires materials")

    # Check 8: material completeness (density + composition/chemical_formula).
    # Only materials reachable from the default lattice (active) can block;
    # candidate / inactive material gaps only warn, so an un-inserted
    # burnable-poison universe with an incomplete borosilicate glass does not
    # downgrade the default F/G assembly.
    warnings.extend(_material_completeness_messages(model, deps, errors))

    # Candidate universes defined but not inserted into the default lattice.
    for universe_id in sorted(deps.inactive_universe_ids):
        warnings.append(
            f"universe {universe_id!r} is not inserted in the default lattice "
            "(candidate only)"
        )

    # Check 4 + 3: universe_pattern references and shape consistency.
    for lattice in rect_lattices:
        errors.extend(_lattice_pattern_errors(lattice, universe_ids))

    # Check 5: cell.region_id references an existing region (when set).
    for cell in model.cells:
        if cell.region_id is not None and cell.region_id not in region_like_ids:
            errors.append(
                f"cell {cell.id!r} references missing region {cell.region_id!r}"
            )

    # Check 6: region.surface_ids reference existing surfaces.
    for region in model.regions:
        missing = [sid for sid in region.surface_ids if sid not in surface_ids]
        if missing:
            errors.append(
                f"region {region.id!r} references missing surfaces: {missing}"
            )

    # Check 7: material cells reference existing materials.
    for cell in model.cells:
        if cell.fill_type == "material" and cell.fill_id and cell.fill_id not in material_ids:
            errors.append(
                f"cell {cell.id!r} references missing material {cell.fill_id!r}"
            )

    # Universe -> cell references.
    for universe in model.universes:
        missing = [cid for cid in universe.cell_ids if cid not in cell_ids]
        if not missing:
            continue
        if universe.id in deps.universe_ids:
            errors.append(
                f"universe {universe.id!r} references missing cells: {missing}"
            )
        else:
            warnings.append(
                f"inactive candidate universe {universe.id!r} references missing "
                f"cells: {missing}; this only blocks models that use that universe"
            )

    # Existing reflector / control-rod reference checks.
    for reflector in model.reflectors:
        if reflector.material_id not in material_ids:
            errors.append(f"reflector {reflector.id!r} references missing material")
        if reflector.region_id is None or reflector.region_id not in region_like_ids:
            errors.append(f"reflector {reflector.id!r} requires a valid region_id")

    lattice_universe_ids = {
        uid
        for lattice in rect_lattices
        for row in lattice.universe_pattern
        for uid in row
    }
    for control_rod in model.control_rods:
        if control_rod.absorber_material_id not in material_ids:
            errors.append(
                f"control rod {control_rod.id!r} references missing absorber material"
            )
        if (
            control_rod.guide_tube_region_id is not None
            and control_rod.guide_tube_region_id not in region_like_ids
        ):
            errors.append(
                f"control rod {control_rod.id!r} references missing guide_tube_region_id"
            )
        if control_rod.guide_tube_region_id is None and not any(
            position_id in lattice_universe_ids for position_id in control_rod.position_ids
        ):
            errors.append(
                f"control rod {control_rod.id!r} must reference a lattice universe "
                "position or a guide_tube_region_id"
            )

    # Check 9 + 10: cylinder radii positive and within pitch/2.
    errors.extend(_cylinder_geometry_errors(model, warnings))

    # Check 11: assembly boundary explicitly specified.
    if model.assemblies:
        boundary = model.assemblies[0].boundary
        if boundary is None:
            warnings.append(
                "assembly boundary is not specified; export will default to vacuum"
            )

    return errors, warnings


def _lattice_pattern_errors(
    lattice: LatticeSpec,
    universe_ids: set[str],
) -> list[str]:
    errors: list[str] = []
    pattern = lattice.universe_pattern
    if lattice.shape is not None:
        expected = tuple(lattice.shape)
        # OpenMC RectLattice shape is (num_x, num_y); pattern is rows of columns.
        shape_rows_cols = _shape_to_rows_cols(expected)
        if shape_rows_cols is not None:
            expected_rows, expected_cols = shape_rows_cols
            actual_rows = len(pattern)
            actual_cols = len(pattern[0]) if pattern else 0
            if expected_rows != actual_rows or expected_cols != actual_cols:
                errors.append(
                    f"lattice {lattice.id!r} shape {list(expected)} does not match "
                    f"universe_pattern dimensions {actual_rows}x{actual_cols}"
                )
    else:
        # Without an explicit shape, just confirm the pattern is rectangular.
        if pattern:
            col_counts = {len(row) for row in pattern}
            if len(col_counts) > 1:
                errors.append(
                    f"lattice {lattice.id!r} universe_pattern rows have unequal lengths"
                )

    missing = [
        uid
        for row in pattern
        for uid in row
        if uid not in universe_ids
    ]
    if missing:
        errors.append(
            f"lattice {lattice.id!r} universe_pattern references missing universes: "
            f"{_dedupe(missing)}"
        )
    return errors


def _shape_to_rows_cols(shape: tuple[int, ...]) -> tuple[int, int] | None:
    if len(shape) == 2:
        nx, ny = int(shape[0]), int(shape[1])
        return ny, nx  # pattern is [ny rows][nx cols]
    if len(shape) == 1:
        n = int(shape[0])
        return n, n
    return None


def _cylinder_geometry_errors(
    model: ComplexModelSpec,
    warnings: list[str],
) -> list[str]:
    errors: list[str] = []
    radii: list[float] = []
    for surface in model.surfaces:
        if surface.kind in {"zcylinder", "ycylinder", "xcylinder"}:
            r = surface.parameters.get("r")
            if r is None:
                warnings.append(
                    f"{surface.kind} surface {surface.id!r} has no 'r' parameter"
                )
                continue
            try:
                radius = float(r)
            except (TypeError, ValueError):
                errors.append(f"{surface.kind} surface {surface.id!r} has non-numeric radius {r!r}")
                continue
            if radius <= 0:
                errors.append(
                    f"{surface.kind} surface {surface.id!r} radius must be positive, got {radius}"
                )
            else:
                radii.append(radius)

    if radii:
        pitch = _assembly_pitch_cm(model)
        if pitch is None:
            warnings.append("cannot verify cylinder radius < pitch/2: assembly pitch unknown")
        else:
            max_radius = max(radii)
            if max_radius >= pitch / 2.0:
                errors.append(
                    f"maximum cylinder outer radius {max_radius} must be less than "
                    f"pitch/2 ({pitch / 2.0})"
                )
    return errors


def _assembly_pitch_cm(model: ComplexModelSpec) -> float | None:
    for lattice in model.lattices:
        if lattice.kind == "rect" and lattice.pitch_cm:
            return float(lattice.pitch_cm[0])
    if model.assemblies and model.assemblies[0].pitch_cm is not None:
        return float(model.assemblies[0].pitch_cm)
    return None


def _assembly_subsystems(model: ComplexModelSpec) -> list[str]:
    present = []
    for name, values in (
        ("materials", model.materials),
        ("surfaces", model.surfaces),
        ("regions", model.regions),
        ("cells", model.cells),
        ("universes", model.universes),
        ("lattices", model.lattices),
        ("assemblies", model.assemblies),
        ("reflectors", model.reflectors),
        ("control_rods", model.control_rods),
    ):
        if values:
            present.append(name)
    return present


def _material_completeness_messages(
    model: ComplexModelSpec,
    deps: ActiveDependencies,
    errors: list[str],
) -> list[str]:
    """Split material gaps into blocking errors (active) and warnings (inactive).

    Mutates ``errors`` in place with active-material gaps and returns the
    inactive-material warnings. Active = reachable from the default lattice.
    """
    warnings: list[str] = []
    for material in model.materials:
        missing_density = material.density_unit is None or material.density_value is None
        missing_composition = not material.composition and not material.chemical_formula
        mixed_percent_types = _material_has_mixed_percent_types(material)
        if material.id in deps.material_ids:
            if missing_density:
                errors.append(f"material {material.id!r} is missing density")
            if missing_composition:
                errors.append(
                    f"material {material.id!r} is missing composition or chemical_formula"
                )
            if mixed_percent_types and material.chemical_formula is None:
                errors.append(
                    f"material {material.id!r} mixes atom and weight percents "
                    "without chemical_formula fallback"
                )
            elif mixed_percent_types:
                warnings.append(
                    f"material {material.id!r} mixes atom and weight percents; "
                    "using chemical_formula fallback"
                )
        elif missing_density or missing_composition or mixed_percent_types:
            # Candidate / orphan material: gaps only warn, never block.
            suffix = "; this only blocks models that use its universe"
            if missing_density:
                warnings.append(
                    f"inactive candidate material {material.id!r} is missing density{suffix}"
                )
            if missing_composition:
                warnings.append(
                    f"inactive candidate material {material.id!r} is missing "
                    f"composition or chemical_formula{suffix}"
                )
            if mixed_percent_types:
                warnings.append(
                    f"inactive candidate material {material.id!r} mixes atom and "
                    f"weight percents{suffix}"
                )
    return warnings


def _material_has_mixed_percent_types(material: ComplexMaterialSpec) -> bool:
    percent_types = {component.percent_type for component in material.composition}
    return len(percent_types) > 1


def _material_confirmations(
    model: ComplexModelSpec,
    deps: ActiveDependencies | None = None,
) -> list[str]:
    """Human-confirmation entries for every material gap.

    ``deps`` tags candidate / inactive materials so reviewers can see that a gap
    only matters if the owning universe is later inserted. Accepts ``None`` so
    callers without a reachability analysis (e.g. defensive skeleton fallbacks)
    still work, treating every material as potentially active.
    """
    active_material_ids = deps.material_ids if deps is not None else None
    confirmations: list[str] = []
    for material in model.materials:
        is_active = (
            material.id in active_material_ids if active_material_ids is not None else True
        )
        prefix = "material" if is_active else "inactive candidate material"
        if material.density_unit is None or material.density_value is None:
            confirmations.append(f"{prefix} {material.id}: is missing density")
        if not material.composition and not material.chemical_formula:
            confirmations.append(
                f"{prefix} {material.id}: is missing composition or chemical_formula"
            )
        confirmations.extend(
            f"{prefix} {material.id}: {item}"
            for item in material.requires_human_confirmation
        )
    return list(dict.fromkeys(confirmations))


def _dedupe(items: list[str]) -> list[str]:
    return list(dict.fromkeys(items))


# -- skeleton emission ----------------------------------------------------


def _skeleton_capability(plan: SimulationPlan, extra_reason: str) -> RenderCapabilityReport:
    model = plan.complex_model
    assert model is not None
    # Preserve active/inactive labelling even in the defensive skeleton fallback.
    deps = collect_active_dependencies(plan)
    base_reasons = [
        "Assembly IR could not be exported safely; generating a review-only skeleton.",
        extra_reason,
    ]
    return RenderCapabilityReport(
        renderability="skeleton",
        is_executable=False,
        supported_renderer="assembly",
        executable_subsystems=[],
        unsupported_subsystems=_assembly_subsystems(model),
        reasons=base_reasons,
        required_human_confirmations=_material_confirmations(model, deps),
    )
