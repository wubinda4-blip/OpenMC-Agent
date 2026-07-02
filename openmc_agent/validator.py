from openmc_agent.schemas import SimulationSpec, ValidationReport


def validate_simulation_spec(spec: SimulationSpec) -> ValidationReport:
    errors: list[str] = []
    warnings: list[str] = []
    geometry = spec.pin_cell.geometry

    if geometry.fuel_radius_cm <= 0 or geometry.fuel_radius_cm > 2.0:
        errors.append(
            f"fuel_radius_cm={geometry.fuel_radius_cm} is outside the supported "
            "pin-cell range (0, 2.0] cm"
        )

    if geometry.pitch_cm <= 0 or geometry.pitch_cm > 5.0:
        errors.append(
            f"pitch_cm={geometry.pitch_cm} is outside the supported range (0, 5.0] cm"
        )

    if geometry.fuel_radius_cm >= geometry.pitch_cm / 2:
        errors.append("fuel_radius_cm must be less than half of pitch_cm")

    has_clad_inner = geometry.clad_inner_radius_cm is not None
    has_clad_outer = geometry.clad_outer_radius_cm is not None
    if has_clad_inner != has_clad_outer:
        errors.append("clad_inner_radius_cm and clad_outer_radius_cm must both be set")

    if has_clad_inner and has_clad_outer:
        assert geometry.clad_inner_radius_cm is not None
        assert geometry.clad_outer_radius_cm is not None
        if geometry.clad_inner_radius_cm <= geometry.fuel_radius_cm:
            errors.append("clad_inner_radius_cm must exceed fuel_radius_cm")
        if geometry.clad_outer_radius_cm <= geometry.clad_inner_radius_cm:
            errors.append("clad_outer_radius_cm must exceed clad_inner_radius_cm")
        if geometry.clad_outer_radius_cm >= geometry.pitch_cm / 2:
            errors.append("clad_outer_radius_cm must be less than half of pitch_cm")

    if spec.pin_cell.cladding is not None and not has_clad_outer:
        errors.append("cladding material is present but cladding radii are missing")
    if spec.pin_cell.cladding is None and has_clad_outer:
        errors.append("cladding radii are present but cladding material is missing")

    if spec.settings.inactive >= spec.settings.batches:
        errors.append("inactive must be less than batches")

    return ValidationReport(is_valid=not errors, errors=errors, warnings=warnings)


def validate_openmc_script(
    script: str,
    spec: SimulationSpec | None = None,
) -> ValidationReport:
    errors: list[str] = []
    required_snippets = {
        "materials": "materials = openmc.Materials",
        "geometry": "geometry = openmc.Geometry",
        "settings": "settings = openmc.Settings()",
        "tallies": "tallies = openmc.Tallies",
        "model export": "model.export_to_xml()",
    }

    for label, snippet in required_snippets.items():
        if snippet not in script:
            errors.append(f"script missing required {label} structure")

    if spec is not None:
        expected_names = [
            spec.pin_cell.fuel.name,
            spec.pin_cell.moderator.name,
        ]
        if spec.pin_cell.cladding is not None:
            expected_names.append(spec.pin_cell.cladding.name)

        for material_name in expected_names:
            if material_name not in script:
                errors.append(f"material {material_name!r} is not referenced in script")

    return ValidationReport(is_valid=not errors, errors=errors)
