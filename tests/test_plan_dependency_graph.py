from openmc_agent.plan_builder.dependency_graph import DEFAULT_PLAN_PATCH_DEPENDENCY_GRAPH, validate_dependency_graph


def test_dependency_graph_is_acyclic_and_stably_orders_profiles_before_catalog() -> None:
    validate_dependency_graph()
    ordered = DEFAULT_PLAN_PATCH_DEPENDENCY_GRAPH.topological_order(["facts", "universes", "localized_insert_profiles", "assembly_catalog", "core_layout"])
    assert ordered.index("localized_insert_profiles") < ordered.index("assembly_catalog") < ordered.index("core_layout")


def test_facts_and_universes_have_expected_transitive_dependents() -> None:
    assert "core_layout" in DEFAULT_PLAN_PATCH_DEPENDENCY_GRAPH.transitive_dependents(["facts"])
    universe_dependents = DEFAULT_PLAN_PATCH_DEPENDENCY_GRAPH.transitive_dependents(["universes"])
    assert "localized_insert_profiles" in universe_dependents
    assert "assembly_catalog" in universe_dependents
