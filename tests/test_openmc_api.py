from openmc_agent.openmc_api import (
    build_openmc_knowledge_graph,
    explain_openmc_interface,
    inspect_openmc_api,
    retrieve_openmc_context,
    search_openmc_api,
)


def test_inspect_openmc_api_returns_local_signature_and_doc_summary() -> None:
    doc = inspect_openmc_api("openmc.RectLattice")

    assert doc.symbol == "openmc.RectLattice"
    assert "lattice" in doc.doc_summary.lower()
    assert doc.official_url.endswith("openmc.RectLattice.html")


def test_search_openmc_api_finds_triso_and_packing_interfaces() -> None:
    docs = search_openmc_api("TRISO fuel particle packing in pebble", limit=4)
    symbols = {doc.symbol for doc in docs}

    assert "openmc.model.TRISO" in symbols
    assert "openmc.model.pack_spheres" in symbols


def test_retrieve_openmc_context_returns_serializable_dicts() -> None:
    docs = retrieve_openmc_context("六角组件 HexLattice", limit=3)

    assert docs
    assert all("symbol" in item for item in docs)
    assert any(item["symbol"] == "openmc.HexLattice" for item in docs)
    assert any(item["symbol"] == "openmc_api_knowledge_graph" for item in docs)


def test_explain_openmc_interface_adds_usage_note() -> None:
    explanation = explain_openmc_interface("openmc.model.pack_spheres")

    assert explanation["symbol"] == "openmc.model.pack_spheres"
    assert "sphere" in explanation["usage_note"].lower()


def test_build_openmc_knowledge_graph_contains_lattice_relationships() -> None:
    graph = build_openmc_knowledge_graph()
    edges = graph["edges"]

    assert any(
        edge["source"] == "openmc.RectLattice"
        and edge["relation"] == "contains"
        and edge["target"] == "openmc.Universe"
        for edge in edges
    )
