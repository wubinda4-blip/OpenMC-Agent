from openmc_agent.plan_builder.validation_repair_policy import policy_for_issue_code


def test_vera_issue_has_pin_map_ownership_policy() -> None:
    policy = policy_for_issue_code("lattice.pin_count_mismatch")
    assert policy is not None
    assert policy.owner_patch_type == "pin_map"
    assert "/default_universe_id" in policy.allowed_path_patterns


def test_known_axial_loading_policy_is_registered() -> None:
    assert policy_for_issue_code("lattice_loading.override_universe_ref_missing").owner_patch_type == "axial_layers"
