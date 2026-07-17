from openmc_agent.plan_builder.assembler import _check_required_patches
from openmc_agent.plan_builder.patches import FactsPatch


def test_catalog_family_with_single_facts_reports_scope_conflict_before_pin_map():
    issues = _check_required_patches({"facts": object(), "materials": object(), "universes": object(), "assembly_catalog": object(), "core_layout": object()}, FactsPatch(model_scope="single_assembly"))
    assert [item.code for item in issues] == ["assembly.model_scope_patch_family_conflict"]
