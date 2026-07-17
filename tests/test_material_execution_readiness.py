from openmc_agent.plan_builder.material_execution_readiness import validate_material_execution_readiness


def test_mass_grid_density_errors_are_grouped_by_material():
    materials = {"materials": [{"material_id":"struct", "density_g_cm3":None, "composition_status":"needs_confirmation"}]}
    overlays = {"overlays": [{"overlay_id":f"g{i}", "geometry_mode":"mass_conserving_outer_frame", "material_id":"struct"} for i in range(8)]}
    result = validate_material_execution_readiness(materials_patch=materials, axial_overlays_patch=overlays)
    assert len(result.issues) == 1
    assert result.issues[0].code == "materials.execution_density_required"
    assert len(result.issues[0].affected_consumer_ids) == 8


def test_composition_confirmation_does_not_hide_known_density():
    result = validate_material_execution_readiness(materials_patch={"materials":[{"material_id":"struct", "density_g_cm3":7.5, "composition_status":"needs_confirmation"}]}, axial_overlays_patch={"overlays":[{"geometry_mode":"mass_conserving_outer_frame", "material_id":"struct"}]})
    assert result.ok
