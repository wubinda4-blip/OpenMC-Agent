"""Validation for OpenMC-agent IR / SimulationPlan / generated scripts.

Validators return a :class:`~openmc_agent.schemas.ValidationReport` that carries
both the legacy free-text ``errors`` / ``warnings`` / ``suggestions`` lists (for
backward compatibility) and a structured ``issues`` list.  Each issue carries a
stable ``code``, related OpenMC ``concept_id``, knowledge references and repair
hints, so an LLM self-repair loop or a future retrieval layer can act on them.

Legacy message strings are intentionally preserved verbatim so existing callers
that match on ``report.errors`` text keep working.
"""

from __future__ import annotations

from openmc_agent.error_catalog import add_issue, issue_from_catalog
from openmc_agent.lattice_validation import lattice_pin_count_issues
from openmc_agent.schemas import (
    SimulationPlan,
    SimulationSpec,
    ValidationIssue,
    ValidationReport,
)


def validate_simulation_spec(spec: SimulationSpec) -> ValidationReport:
    """Validate a pin-cell :class:`SimulationSpec`.

    The schema's own model validators already reject most malformed specs, but a
    caller may bypass validation via ``model_construct`` (e.g. a repair loop
    inspecting a broken draft).  These checks catch that case and attach stable
    error codes + repair hints.
    """
    issues: list[ValidationIssue] = []
    geometry = spec.pin_cell.geometry

    if geometry.fuel_radius_cm <= 0 or geometry.fuel_radius_cm > 2.0:
        add_issue(
            issues,
            "geometry.fuel_radius.out_of_range",
            message=(
                f"fuel_radius_cm={geometry.fuel_radius_cm} is outside the supported "
                "pin-cell range (0, 2.0] cm"
            ),
        )

    if geometry.pitch_cm <= 0 or geometry.pitch_cm > 5.0:
        add_issue(
            issues,
            "geometry.pitch.out_of_range",
            message=(
                f"pitch_cm={geometry.pitch_cm} is outside the supported range (0, 5.0] cm"
            ),
        )

    if geometry.fuel_radius_cm >= geometry.pitch_cm / 2:
        add_issue(issues, "geometry.fuel_radius.too_large_for_pitch")

    has_clad_inner = geometry.clad_inner_radius_cm is not None
    has_clad_outer = geometry.clad_outer_radius_cm is not None
    if has_clad_inner != has_clad_outer:
        add_issue(issues, "geometry.cladding.radii_partial_missing")

    if has_clad_inner and has_clad_outer:
        assert geometry.clad_inner_radius_cm is not None
        assert geometry.clad_outer_radius_cm is not None
        if geometry.clad_inner_radius_cm <= geometry.fuel_radius_cm:
            add_issue(issues, "geometry.cladding.inner_not_greater_than_fuel")
        if geometry.clad_outer_radius_cm <= geometry.clad_inner_radius_cm:
            add_issue(issues, "geometry.cladding.outer_not_greater_than_inner")
        if geometry.clad_outer_radius_cm >= geometry.pitch_cm / 2:
            add_issue(issues, "geometry.cladding.outer_too_large_for_pitch")

    if spec.pin_cell.cladding is not None and not has_clad_outer:
        add_issue(issues, "geometry.cladding.material_missing_for_radii")
    if spec.pin_cell.cladding is None and has_clad_outer:
        add_issue(issues, "geometry.cladding.radii_missing_for_material")

    if spec.settings.inactive >= spec.settings.batches:
        add_issue(issues, "settings.inactive.not_less_than_batches")

    pin_cell_materials = [spec.pin_cell.fuel, spec.pin_cell.moderator]
    if spec.pin_cell.cladding is not None:
        pin_cell_materials.append(spec.pin_cell.cladding)
    for material in pin_cell_materials:
        percent_types = {component.percent_type for component in material.composition}
        if len(percent_types) <= 1:
            continue
        if material.chemical_formula is None:
            add_issue(
                issues,
                "material.pin_cell.mixed_percent_no_formula",
                message=(
                    f"material {material.name!r} mixes atom and weight percents "
                    "without chemical_formula fallback"
                ),
            )
        else:
            add_issue(
                issues,
                "material.pin_cell.mixed_percent_formula_fallback",
                message=(
                    f"material {material.name!r} mixes atom and weight percents; "
                    "renderer will use chemical_formula fallback"
                ),
            )

    return ValidationReport.from_issues(issues)


def validate_simulation_plan(plan: SimulationPlan) -> ValidationReport:
    """Validate a :class:`SimulationPlan`, merging any pin-cell spec issues."""
    issues: list[ValidationIssue] = []

    if plan.model_spec is not None:
        issues.extend(validate_simulation_spec(plan.model_spec).issues)

    if plan.model_spec is None and plan.complex_model is None:
        add_issue(issues, "plan.model.missing")

    if plan.complex_model is not None and not plan.capability_report.is_executable:
        # Warning + review suggestion. The exact strings are matched by graph.py
        # when summarising the transcript, so keep them verbatim.
        add_issue(issues, "plan.complex_model.non_executable")

    if (
        plan.capability_report.is_executable
        and plan.model_spec is None
        and plan.capability_report.supported_renderer not in {"assembly", "triso", "core"}
    ):
        add_issue(issues, "plan.executable.unsupported_renderer")

    if plan.complex_model is not None:
        issues.extend(lattice_pin_count_issues(plan.complex_model.lattices))

    return ValidationReport.from_issues(issues)


def validate_openmc_script(
    script: str,
    spec: SimulationSpec | None = None,
) -> ValidationReport:
    """Check that a rendered OpenMC script contains the required structures."""
    issues: list[ValidationIssue] = []
    required_snippets = {
        "materials": "materials = openmc.Materials",
        "geometry": "geometry = openmc.Geometry",
        "settings": "settings = openmc.Settings()",
        "tallies": "tallies = openmc.Tallies",
        "model export": "model.export_to_xml()",
    }

    for label, snippet in required_snippets.items():
        if snippet not in script:
            add_issue(
                issues,
                "script.missing_structure",
                message=f"script missing required {label} structure",
            )

    if spec is not None:
        expected_names = [
            spec.pin_cell.fuel.name,
            spec.pin_cell.moderator.name,
        ]
        if spec.pin_cell.cladding is not None:
            expected_names.append(spec.pin_cell.cladding.name)

        for material_name in expected_names:
            if material_name not in script:
                add_issue(
                    issues,
                    "script.material_not_referenced",
                    message=f"material {material_name!r} is not referenced in script",
                )

    return ValidationReport.from_issues(issues)


__all__ = [
    "validate_simulation_spec",
    "validate_simulation_plan",
    "validate_openmc_script",
    "issue_from_catalog",
]
