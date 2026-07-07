"""Fallback renderer that emits a review-only ``model.py`` skeleton.

The skeleton walks the structured IR (materials, surfaces, regions, cells,
universes, lattices, geometry, settings) and emits commented OpenMC Python with
explicit ``TODO`` markers wherever data is missing. It never calls
``model.export_to_xml()`` because the model is intentionally not executable.

Specialized renderers (e.g. :class:`~openmc_agent.renderers.assembly.RectAssemblyRenderer`)
reuse :func:`build_skeleton_script` for their own skeleton mode so the IR walker
stays in one place.
"""

from __future__ import annotations

import re
from pathlib import Path

from openmc_agent.error_catalog import issue_from_catalog
from openmc_agent.renderers.base import BaseRenderer, RenderResult
from openmc_agent.schemas import (
    ComplexMaterialSpec,
    ComplexModelSpec,
    RenderCapabilityReport,
    SimulationPlan,
    ValidationIssue,
)

_HEADER_STATUS = "NOT EXECUTABLE"


def build_skeleton_script(
    plan: SimulationPlan,
    *,
    renderer_name: str,
    reasons: list[str],
    warnings: list[str] | None = None,
) -> str:
    """Return a review-only model.py skeleton for ``plan``.

    The script is syntactically importable but deliberately does not export XML.
    """
    model = plan.complex_model
    name = _model_name(plan)
    reason_lines = reasons or ["No specialized renderer can produce an executable model for this IR."]
    warning_lines = list(warnings or [])

    sections: list[str] = []
    sections.append(_header(name, renderer_name, reason_lines, warning_lines))
    sections.append("import openmc\n")
    sections.append("materials = openmc.Materials()")

    if model is not None:
        sections.append(_materials_section(model))
        sections.append(_surfaces_section(model))
        sections.append(_regions_section(model))
        sections.append(_cells_section(model))
        sections.append(_universes_section(model))
        sections.append(_lattice_section(model))
        sections.append(_assembly_section(model))
        sections.append(_geometry_section(model))
        sections.append(_settings_section(model.settings))
    elif plan.model_spec is not None:
        sections.append(_pincell_skeleton_section(plan))
        sections.append(_settings_section(plan.model_spec.settings))

    sections.append(_tail())
    return "\n\n".join(section for section in sections if section) + "\n"


class SkeletonRenderer(BaseRenderer):
    """Last-resort renderer: always last in the registry, always 'skeleton' at best."""

    name = "skeleton"
    supported_kinds = (
        "pin_cell",
        "assembly",
        "core",
        "reflector",
        "control_rod",
        "triso_compact",
        "pebble",
        "pebble_bed",
        "mixed",
    )

    def can_render(self, plan: SimulationPlan) -> RenderCapabilityReport:
        if not _has_skeleton_source(plan):
            return RenderCapabilityReport(
                renderability="none",
                is_executable=False,
                supported_renderer="none",
                unsupported_subsystems=[],
                reasons=["No model_spec or complex_model is available to sketch."],
            )
        reasons = [
            "No specialized renderer can produce an executable model for this IR; "
            "generating a review-only skeleton.",
        ]
        subsystems = _skeleton_subsystems(plan)
        issues = _skeleton_issues(plan)
        if any(issue.code == "lattice.hex.renderer_unsupported" for issue in issues):
            reasons.append("Hex lattice currently has no HexAssemblyRenderer; maximum renderability is skeleton.")
        return RenderCapabilityReport(
            renderability="skeleton",
            is_executable=False,
            supported_renderer="skeleton",
            executable_subsystems=[],
            unsupported_subsystems=subsystems,
            reasons=reasons,
            required_human_confirmations=_skeleton_confirmations(plan),
            issues=issues,
        )

    def render(self, plan: SimulationPlan, outdir: Path) -> RenderResult:
        outdir.mkdir(parents=True, exist_ok=True)
        capability = self.can_render(plan)
        reasons = list(capability.reasons)
        script = build_skeleton_script(
            plan,
            renderer_name=self.name,
            reasons=reasons,
            warnings=capability.warnings,
        )
        files = _write_skeleton_outputs(outdir, script, self.name, capability)
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


# -- script section builders ----------------------------------------------


def _header(
    name: str,
    renderer_name: str,
    reasons: list[str],
    warnings: list[str],
) -> str:
    lines = [
        f'"""Auto-generated OpenMC model skeleton for {name}."""',
        "",
        "# Auto-generated OpenMC model skeleton",
        f"# Status: {_HEADER_STATUS}",
        "# Renderability: skeleton",
        f"# Renderer: {renderer_name}",
        "#",
        "# This file is for human review only. Do NOT run openmc on it directly:",
        "# missing nuclear data must be confirmed by a nuclear engineer, and",
        "# model.export_to_xml() is intentionally omitted.",
        "# Reasons:",
    ]
    for reason in reasons:
        lines.append(f"# - {_comment(reason)}")
    if warnings:
        lines.append("# Warnings:")
        for warning in warnings:
            lines.append(f"# - {_comment(warning)}")
    return "\n".join(lines)


def _skeleton_issues(plan: SimulationPlan) -> list[ValidationIssue]:
    model = plan.complex_model
    if model is None:
        return []
    issues: list[ValidationIssue] = []
    universe_ids = {universe.id for universe in model.universes}
    for lattice in model.lattices:
        if lattice.kind != "hex":
            continue
        schema_base = f"complex_model.lattices.{lattice.id}"
        issues.append(
            issue_from_catalog(
                "lattice.hex.renderer_unsupported",
                message=(
                    f"hex lattice {lattice.id!r} requires a HexAssemblyRenderer; "
                    "current renderer output remains skeleton."
                ),
                schema_path=schema_base,
            )
        )
        if not lattice.rings:
            issues.append(
                issue_from_catalog(
                    "lattice.hex.rings_missing",
                    message=f"hex lattice {lattice.id!r} rings are missing",
                    schema_path=f"{schema_base}.rings",
                )
            )
        else:
            invalid = _invalid_hex_ring_lengths(lattice.rings)
            if invalid:
                issues.append(
                    issue_from_catalog(
                        "lattice.hex.ring_shape_invalid",
                        message=f"hex lattice {lattice.id!r} has invalid ring lengths: {invalid}",
                        schema_path=f"{schema_base}.rings",
                    )
                )
            missing_universes = sorted(
                {
                    universe_id
                    for ring in lattice.rings
                    for universe_id in ring
                    if universe_id not in universe_ids
                }
            )
            if missing_universes:
                issues.append(
                    issue_from_catalog(
                        "lattice.universe_ref_missing",
                        message=(
                            f"hex lattice {lattice.id!r} rings reference missing universes: "
                            f"{missing_universes}"
                        ),
                        schema_path=f"{schema_base}.rings",
                        concept_id="openmc.geometry.hex_lattice",
                        grep_patterns=["HexLattice", "rings", *missing_universes],
                        route_hint="reflect_plan",
                    )
                )
        if lattice.outer_universe_id is None:
            issues.append(
                issue_from_catalog(
                    "lattice.hex.outer_universe_missing",
                    message=f"hex lattice {lattice.id!r} outer_universe_id is missing",
                    schema_path=f"{schema_base}.outer_universe_id",
                )
            )
        elif lattice.outer_universe_id not in universe_ids:
            issues.append(
                issue_from_catalog(
                    "lattice.universe_ref_missing",
                    message=(
                        f"hex lattice {lattice.id!r} outer_universe_id "
                        f"{lattice.outer_universe_id!r} is not defined"
                    ),
                    schema_path=f"{schema_base}.outer_universe_id",
                    concept_id="openmc.geometry.hex_lattice",
                    grep_patterns=["HexLattice", "outer_universe_id", lattice.outer_universe_id],
                    route_hint="reflect_plan",
                )
            )
        issues.append(
            issue_from_catalog(
                "lattice.hex.orientation_unverified",
                message=(
                    f"hex lattice {lattice.id!r} orientation, pitch convention, and "
                    "ring ordering require documentation verification before renderer work."
                ),
                schema_path=schema_base,
            )
        )
    return issues


def _invalid_hex_ring_lengths(rings: list[list[str]]) -> list[str]:
    invalid: list[str] = []
    for index, ring in enumerate(rings):
        expected = 1 if index == 0 else 6 * index
        if len(ring) != expected:
            invalid.append(f"ring {index}: expected {expected}, got {len(ring)}")
    return invalid


def _materials_section(model: ComplexModelSpec) -> str:
    if not model.materials:
        return "# --- Materials ---\n# TODO: no materials were provided in the IR."
    lines = ["# --- Materials ---"]
    for material in model.materials:
        lines.extend(_material_lines(material))
    return "\n".join(lines)


def _material_lines(material: ComplexMaterialSpec) -> list[str]:
    variable = _safe_name("material", material.id)
    lines = [f"# material {material.id!r} ({material.name})"]
    missing = _missing_material_fields(material)
    if missing:
        for item in missing:
            lines.append(f"# TODO: material {material.id!r} {item}")
    lines.append(f"{variable} = openmc.Material(name={material.name!r})  # TODO: incomplete")
    if material.density_unit is not None and material.density_value is not None:
        lines.append(
            f"{variable}.set_density({material.density_unit!r}, {material.density_value!r})"
        )
    elif material.macroscopic is not None:
        lines.append(
            f"# {variable}.add_macroscopic(...) will set macro density to 1.0 by default"
        )
    else:
        lines.append(f"# {variable}.set_density(<unit>, <value>)  # TODO: density missing")
    if material.temperature_k is not None:
        lines.append(f"{variable}.temperature = {material.temperature_k!r}")
    if material.depletable:
        lines.append(f"{variable}.depletable = {material.depletable!r}")
    if material.composition:
        for component in material.composition:
            action = "add_element" if component.kind == "element" else "add_nuclide"
            lines.append(
                f"# {variable}.{action}({component.name!r}, {component.percent!r}, "
                f"{component.percent_type!r})"
            )
    elif material.macroscopic is not None:
        lines.append(f"# {variable}.add_macroscopic({material.macroscopic!r})")
    elif material.chemical_formula is not None:
        lines.append(
            f"# {variable}.add_elements_from_formula({material.chemical_formula!r})"
        )
    else:
        lines.append(
            f"# {variable}.add_nuclide(...)  # TODO: composition or chemical_formula missing"
        )
    for sab in material.sab:
        lines.append(f"# {variable}.add_s_alpha_beta({sab!r})  # TODO: confirm thermal scattering name")
    lines.append(f"materials.append({variable})  # TODO: verify before export")
    return lines


def _surfaces_section(model: ComplexModelSpec) -> str:
    if not model.surfaces:
        return ""
    lines = ["# --- Surfaces ---"]
    for surface in model.surfaces:
        variable = _safe_name("surface", surface.id)
        params = dict(surface.parameters)
        if surface.boundary_type is not None:
            params["boundary_type"] = surface.boundary_type
        args = ", ".join(f"{key}={value!r}" for key, value in sorted(params.items()))
        ctor = _surface_ctor(surface.kind)
        lines.append(f"# surface {surface.id!r} ({surface.kind})")
        lines.append(f"# {variable} = {ctor}({args})  # TODO: review parameters")
    return "\n".join(lines)


def _regions_section(model: ComplexModelSpec) -> str:
    if not model.regions:
        return ""
    lines = ["# --- Regions ---"]
    for region in model.regions:
        variable = _safe_name("region", region.id)
        lines.append(f"# region {region.id!r}")
        lines.append(f"# {variable} = {_comment(region.expression)}  # TODO: translate to surface half-spaces")
    return "\n".join(lines)


def _cells_section(model: ComplexModelSpec) -> str:
    if not model.cells:
        return ""
    lines = ["# --- Cells ---"]
    material_ids = {material.id for material in model.materials}
    for cell in model.cells:
        variable = _safe_name("cell", cell.id)
        lines.append(f"# cell {cell.id!r} ({cell.name})")
        fill_note = _cell_fill_note(cell, material_ids)
        region_note = f"regions[{cell.region_id!r}]" if cell.region_id else "None"
        lines.append(
            f"# {variable} = openmc.Cell(name={cell.name!r}, fill=..., region={region_note})  # TODO: {fill_note}"
        )
    return "\n".join(lines)


def _universes_section(model: ComplexModelSpec) -> str:
    if not model.universes:
        return ""
    lines = ["# --- Universes ---"]
    for universe in model.universes:
        variable = _safe_name("universe", universe.id)
        cell_refs = ", ".join(f"cells[{cid!r}]" for cid in universe.cell_ids) or "TODO"
        lines.append(
            f"# {variable} = openmc.Universe(name={universe.name!r}, cells=[{cell_refs}])"
        )
    return "\n".join(lines)


def _lattice_section(model: ComplexModelSpec) -> str:
    if not model.lattices:
        return ""
    lines = ["# --- Lattice ---"]
    for lattice in model.lattices:
        variable = _safe_name("lattice", lattice.id)
        ctor = "openmc.RectLattice" if lattice.kind == "rect" else "openmc.HexLattice"
        lines.append(f"# lattice {lattice.id!r} ({lattice.kind})")
        lines.append(f"# {variable} = {ctor}(name={lattice.name!r})")
        lines.append(f"# {variable}.pitch = {tuple(lattice.pitch_cm)!r}")
        if lattice.kind == "rect":
            rows = len(lattice.universe_pattern)
            cols = len(lattice.universe_pattern[0]) if lattice.universe_pattern else 0
            if lattice.universe_pattern:
                lines.append(
                    f"# {variable}.universes = ...  # TODO: {rows}x{cols} universe_pattern; "
                    "verify dimensions and universe ids"
                )
            else:
                lines.append(
                    f"# {variable}.universes = ...  # TODO: universe_pattern missing"
                )
        else:
            lines.append(
                f"# {variable}.rings = ...  # TODO: {len(lattice.rings)} ring(s); verify universe ids"
            )
        if lattice.outer_universe_id is None:
            lines.append(f"# {variable}.outer = ...  # TODO: outer universe missing")
    return "\n".join(lines)


def _assembly_section(model: ComplexModelSpec) -> str:
    if not model.assemblies and model.core is None:
        return ""
    lines = ["# --- Root container ---"]
    if model.assemblies:
        assembly = model.assemblies[0]
        boundary = assembly.boundary or "TODO"
        lines.append(
            f"# root_cell = openmc.Cell(name={assembly.name!r}, fill=lattice, region=...)  "
            f"# boundary={boundary}"
        )
        if assembly.boundary is None:
            lines.append("# TODO: assembly boundary condition is not specified")
    if model.core is not None:
        boundary = model.core.boundary or "unknown"
        lines.append(
            f"# root_cell = openmc.Cell(name={model.core.name!r}, fill=lattice, region=...)  "
            f"# boundary={boundary}"
        )
    return "\n".join(lines)


def _geometry_section(model: ComplexModelSpec) -> str:
    return (
        "# --- Geometry ---\n"
        "# TODO: assemble root universe from the cells/universes above.\n"
        "# root_universe = openmc.Universe(cells=[...])\n"
        "geometry = openmc.Geometry()  # TODO: pass root_universe once cells are defined"
    )


def _settings_section(settings) -> str:
    lines = [
        "# --- Settings ---",
        "settings = openmc.Settings()",
        f"settings.run_mode = {settings.run_mode!r}",
        f"settings.batches = {settings.batches}",
        f"settings.inactive = {settings.inactive}",
        f"settings.particles = {settings.particles}",
    ]
    if getattr(settings, "seed", None) is not None:
        lines.append(f"settings.seed = {settings.seed}")
    lines.append(
        "# TODO: define settings.source once the fissionable bounding box is known."
    )
    return "\n".join(lines)


def _pincell_skeleton_section(plan: SimulationPlan) -> str:
    spec = plan.model_spec
    assert spec is not None
    geometry = spec.pin_cell.geometry
    lines = [
        "# --- Pin-cell skeleton (review only) ---",
        f"# fuel_radius_cm = {geometry.fuel_radius_cm!r}",
        f"# pitch_cm = {geometry.pitch_cm!r}",
        "# TODO: build openmc.Material / ZCylinder / Cell objects once materials are complete.",
    ]
    return "\n".join(lines)


def _tail() -> str:
    return (
        "# --- Model assembly ---\n"
        "# NOTE: model.export_to_xml() is intentionally omitted because this model\n"
        "# is NOT EXECUTABLE. Complete the TODO entries, confirm nuclear data, then\n"
        "# uncomment the model construction below in a real renderer.\n"
        "# model = openmc.Model(materials=materials, geometry=geometry, settings=settings)"
    )


# -- diagnostics ----------------------------------------------------------


def _missing_material_fields(material: ComplexMaterialSpec) -> list[str]:
    if material.macroscopic is not None:
        return []
    missing: list[str] = []
    if material.density_unit is None or material.density_value is None:
        missing.append("is missing density")
    if not material.composition and not material.chemical_formula:
        missing.append("is missing composition or chemical_formula")
    return missing


def _cell_fill_note(cell, material_ids: set[str]) -> str:
    if cell.fill_type == "material":
        if cell.fill_id and cell.fill_id in material_ids:
            return f"fill=materials[{cell.fill_id!r}]"
        return f"material {cell.fill_id!r} missing" if cell.fill_id else "fill material missing"
    if cell.fill_type == "universe":
        return f"fill=universes[{cell.fill_id!r}]"
    if cell.fill_type == "lattice":
        return f"fill=lattices[{cell.fill_id!r}]"
    return "void"


def _has_skeleton_source(plan: SimulationPlan) -> bool:
    return plan.model_spec is not None or plan.complex_model is not None


def _skeleton_subsystems(plan: SimulationPlan) -> list[str]:
    if plan.complex_model is None:
        return ["pin_cell"] if plan.model_spec is not None else []
    model = plan.complex_model
    present = []
    for name, values in (
        ("materials", model.materials),
        ("surfaces", model.surfaces),
        ("regions", model.regions),
        ("cells", model.cells),
        ("universes", model.universes),
        ("lattices", model.lattices),
        ("assemblies", model.assemblies),
        ("core", [model.core] if model.core is not None else []),
        ("reflectors", model.reflectors),
        ("control_rods", model.control_rods),
        ("trisos", model.trisos),
        ("pebbles", model.pebbles),
    ):
        if values:
            present.append(name)
    return present


def _skeleton_confirmations(plan: SimulationPlan) -> list[str]:
    if plan.complex_model is None:
        return []
    confirmations: list[str] = []
    for material in plan.complex_model.materials:
        for item in _missing_material_fields(material):
            confirmations.append(f"material {material.id}: {item}")
        confirmations.extend(
            f"material {material.id}: {item}" for item in material.requires_human_confirmation
        )
    for lattice in plan.complex_model.lattices:
        confirmations.extend(
            f"lattice {lattice.id}: {item}"
            for item in lattice.requires_human_confirmation
        )
    return list(dict.fromkeys(confirmations))


def emit_skeleton(
    renderer_name: str,
    plan: SimulationPlan,
    outdir: Path,
    capability: RenderCapabilityReport,
) -> RenderResult:
    """Write a review-only model.py skeleton + sidecars and return the RenderResult."""
    outdir.mkdir(parents=True, exist_ok=True)
    script = build_skeleton_script(
        plan,
        renderer_name=renderer_name,
        reasons=capability.reasons,
        warnings=capability.warnings,
    )
    model_path = outdir / "model.py"
    model_path.write_text(script, encoding="utf-8")
    files = [str(model_path), _write_capability_report(outdir, capability)]
    todo = _write_todo(outdir, renderer_name, capability)
    if todo is not None:
        files.append(todo)
    return RenderResult(
        renderer_name=renderer_name,
        renderability="skeleton",
        is_executable=False,
        script=script,
        output_files=files,
        warnings=capability.warnings,
        errors=[],
        capability=capability,
    )


def _write_skeleton_outputs(
    outdir: Path,
    script: str,
    renderer_name: str,
    capability: RenderCapabilityReport,
) -> list[str]:
    model_path = outdir / "model.py"
    model_path.write_text(script, encoding="utf-8")
    files = [str(model_path)]
    files.append(_write_capability_report(outdir, capability))
    todo_path = _write_todo(outdir, renderer_name, capability)
    if todo_path is not None:
        files.append(todo_path)
    return files


def _write_capability_report(outdir: Path, capability: RenderCapabilityReport) -> str:
    import json

    path = outdir / "capability_report.json"
    path.write_text(
        json.dumps(capability.model_dump(mode="json"), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return str(path)


def _write_todo(
    outdir: Path,
    renderer_name: str,
    capability: RenderCapabilityReport,
) -> str | None:
    # Runnable / exportable models have no TODO gaps -- the TODO file is only
    # for skeleton / non-executable models that need human review. Writing a
    # TODO for a runnable model (just because it has a 'reasons' summary and
    # warnings) is misleading: it says "NOT EXECUTABLE" next to "runnable".
    if capability.renderability in {"exportable", "runnable"}:
        return None
    if not capability.reasons and not capability.warnings:
        return None
    lines = [
        "# TODO — OpenMC model skeleton gaps",
        "",
        f"Renderer: {renderer_name}",
        f"Renderability: {capability.renderability}",
        "Status: NOT EXECUTABLE",
        "",
        "## Blocking reasons",
    ]
    for reason in capability.reasons:
        lines.append(f"- {reason}")
    if capability.warnings:
        lines.append("")
        lines.append("## Warnings")
        for warning in capability.warnings:
            lines.append(f"- {warning}")
    if capability.required_human_confirmations:
        lines.append("")
        lines.append("## Requires human confirmation")
        for item in capability.required_human_confirmations:
            lines.append(f"- {item}")
    path = outdir / "TODO.md"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return str(path)


def _surface_ctor(kind: str) -> str:
    mapping = {
        "xplane": "openmc.XPlane",
        "yplane": "openmc.YPlane",
        "zplane": "openmc.ZPlane",
        "plane": "openmc.Plane",
        "zcylinder": "openmc.ZCylinder",
        "ycylinder": "openmc.YCylinder",
        "xcylinder": "openmc.XCylinder",
        "sphere": "openmc.Sphere",
        "rectangular_prism": "openmc.model.rectangular_prism",
        "hexagonal_prism": "openmc.model.hexagonal_prism",
    }
    return mapping.get(kind, f"openmc.Surface  # unsupported kind {kind!r}")


def _model_name(plan: SimulationPlan) -> str:
    if plan.complex_model is not None:
        return plan.complex_model.name
    if plan.model_spec is not None:
        return plan.model_spec.name
    return "unknown"


def _safe_name(prefix: str, identifier: str) -> str:
    safe = re.sub(r"\W+", "_", identifier).strip("_")
    if not safe or safe[0].isdigit():
        safe = f"{prefix}_{safe}"
    return f"{prefix}_{safe}"


def _comment(text: str) -> str:
    return text.replace("\n", " ")
