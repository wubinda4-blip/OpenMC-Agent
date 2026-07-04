from openmc_agent.error_catalog import issue_from_catalog
from openmc_agent.grep_search import RetrievedEvidence
from openmc_agent.knowledge_graph import (
    GraphLookupRequest,
    gather_graph_context_for_issues,
    graph_context_to_evidence,
    graph_lookup,
    graph_request_from_issues,
)
from openmc_agent.knowledge_graph_registry import GRAPH_EDGES, GRAPH_NODES


def test_graph_registry_contains_hex_lattice_relationships() -> None:
    assert "schema.LatticeSpec.rings" in GRAPH_NODES
    assert "concept.openmc.geometry.hex_lattice" in GRAPH_NODES
    assert "issue.lattice.hex.renderer_unsupported" in GRAPH_NODES

    assert any(
        edge.source == "schema.LatticeSpec.rings"
        and edge.target == "concept.openmc.geometry.hex_lattice"
        for edge in GRAPH_EDGES
    )
    assert any(
        edge.source == "issue.lattice.hex.renderer_unsupported"
        and edge.target == "concept.openmc.geometry.hex_lattice"
        for edge in GRAPH_EDGES
    )
    assert any(
        edge.source == "issue.lattice.hex.renderer_unsupported"
        and edge.target == "repair.capability_downgrade"
        for edge in GRAPH_EDGES
    )


def test_graph_lookup_from_hex_renderer_issue() -> None:
    context = graph_lookup(
        GraphLookupRequest(issue_codes=["lattice.hex.renderer_unsupported"])
    )

    assert "openmc.geometry.hex_lattice" in context.related_concept_ids
    assert "LatticeSpec.rings" in context.related_schema_paths
    assert "capability_downgrade" in context.repair_policies
    assert any("HexLattice rings outer universe" in hint for hint in context.retrieval_hints)


def test_graph_lookup_maps_pin_cell_schema_path() -> None:
    context = graph_lookup(
        GraphLookupRequest(schema_paths=["model_spec.pin_cell.geometry.fuel_radius_cm"])
    )

    assert "schema.GeometrySpec.fuel_radius_cm" in {node.id for node in context.nodes}
    assert "openmc.geometry.pin_cell_radius" in context.related_concept_ids


def test_graph_lookup_from_density_unit_concept() -> None:
    context = graph_lookup(
        GraphLookupRequest(concept_ids=["openmc.material.density_unit"])
    )

    assert "MaterialSpec.density_unit" in context.related_schema_paths
    assert "ComplexMaterialSpec.density_unit" in context.related_schema_paths
    assert "openmc.Material.set_density" in context.related_api_refs
    assert "openmc.usersguide.materials" in context.related_doc_refs


def test_graph_lookup_from_grep_pattern_outer_universe() -> None:
    context = graph_lookup(GraphLookupRequest(grep_patterns=["outer_universe_id"]))

    assert "LatticeSpec.outer_universe_id" in context.related_schema_paths
    assert "openmc.geometry.lattice" in context.related_concept_ids
    assert "openmc.geometry.hex_lattice" in context.related_concept_ids


def test_graph_request_uses_issues_and_grep_evidence() -> None:
    issue = issue_from_catalog(
        "export_xml.dangling_lattice_universe",
        schema_path="complex_model.lattices[0].universe_pattern",
        grep_patterns=["missing_universe", "LatticeSpec"],
    )
    evidence = [
        RetrievedEvidence(
            source_type="grep",
            locator="openmc_agent/schemas.py:430-450",
            text="class LatticeSpec: universe_pattern",
            issue_code=issue.code,
            schema_path=issue.schema_path,
            metadata={"matched_pattern": "universe_pattern", "symbol_hint": "LatticeSpec"},
        )
    ]

    request = graph_request_from_issues([issue], evidence)

    assert request.issue_codes == ["export_xml.dangling_lattice_universe"]
    assert "complex_model.lattices[0].universe_pattern" in request.schema_paths
    assert "universe_pattern" in request.grep_patterns
    assert request.evidence_locators == ["openmc_agent/schemas.py:430-450"]


def test_graph_context_to_evidence_is_compact() -> None:
    context = gather_graph_context_for_issues(
        [issue_from_catalog("lattice.hex.renderer_unsupported")]
    )

    evidence = graph_context_to_evidence(context)

    assert evidence
    assert all(item.source_type == "graph" for item in evidence)
    assert any(item.metadata.get("node_type") == "validation_issue" for item in evidence)
    assert any(
        "openmc.geometry.hex_lattice" in item.metadata.get("related_concept_ids", [])
        for item in evidence
    )
    assert all(len(item.text) < 500 for item in evidence)

