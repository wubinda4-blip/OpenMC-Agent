"""Maintained lightweight graph registry for OpenMC-agent diagnostics."""

from __future__ import annotations

from typing import Any

from openmc_agent.knowledge_graph import GraphEdge, GraphNode


def _node(
    node_id: str,
    node_type: str,
    title: str,
    description: str = "",
    *,
    aliases: list[str] | None = None,
    **metadata: Any,
) -> GraphNode:
    return GraphNode(
        id=node_id,
        type=node_type,  # type: ignore[arg-type]
        title=title,
        description=description,
        aliases=aliases or [],
        metadata=metadata,
    )


def _edge(
    source: str,
    target: str,
    relation: str,
    *,
    weight: float = 1.0,
    **metadata: Any,
) -> GraphEdge:
    return GraphEdge(
        source=source,
        target=target,
        relation=relation,  # type: ignore[arg-type]
        weight=weight,
        metadata=metadata,
    )


def _schema(
    model: str,
    field: str,
    concept: str,
    description: str,
    *,
    aliases: list[str] | None = None,
    docs: list[str] | None = None,
    apis: list[str] | None = None,
    hints: list[str] | None = None,
) -> tuple[list[GraphNode], list[GraphEdge]]:
    schema_id = f"schema.{model}.{field}"
    concept_id = f"concept.{concept}"
    nodes = [
        _node(
            schema_id,
            "schema_field",
            f"{model}.{field}",
            description,
            aliases=[field, f"{model}.{field}", *(aliases or [])],
            schema_path=f"{model}.{field}",
            concept_id=concept,
            doc_refs=docs or [],
            api_refs=apis or [],
            retrieval_hints=hints or [],
        )
    ]
    edges = [
        _edge(schema_id, concept_id, "represents"),
    ]
    for doc in docs or []:
        edges.append(_edge(schema_id, f"doc.{doc}", "documented_in"))
        edges.append(_edge(concept_id, f"doc.{doc}", "documented_in"))
    for api in apis or []:
        edges.append(_edge(concept_id, f"api.{api}", "implemented_by"))
    return nodes, edges


CONCEPTS: list[GraphNode] = [
    _node(
        "concept.openmc.material.nuclide_name",
        "openmc_concept",
        "OpenMC nuclide names",
        "Nuclide or element identifiers accepted by OpenMC material APIs.",
        aliases=["nuclide_name", "add_nuclide", "U235", "O16"],
        concept_id="openmc.material.nuclide_name",
        retrieval_hints=["OpenMC nuclide naming convention U235 O16"],
    ),
    _node("concept.openmc.material.composition_fraction", "openmc_concept", "Material composition fraction", aliases=["composition_fraction", "percent"], concept_id="openmc.material.composition_fraction"),
    _node("concept.openmc.material.percent_type", "openmc_concept", "Material percent type", aliases=["percent_type", "ao", "wo"], concept_id="openmc.material.percent_type"),
    _node(
        "concept.openmc.material.density_unit",
        "openmc_concept",
        "Material density unit",
        "Units passed to openmc.Material.set_density.",
        aliases=["density_unit", "set_density"],
        concept_id="openmc.material.density_unit",
        retrieval_hints=["OpenMC Material.set_density density units"],
    ),
    _node("concept.openmc.material.density_value", "openmc_concept", "Material density value", aliases=["density_value"], concept_id="openmc.material.density_value"),
    _node("concept.openmc.material.composition", "openmc_concept", "Material composition", aliases=["composition", "add_element", "add_nuclide"], concept_id="openmc.material.composition"),
    _node("concept.openmc.material.thermal_scattering", "openmc_concept", "Thermal scattering", aliases=["thermal_scattering", "sab", "add_s_alpha_beta"], concept_id="openmc.material.thermal_scattering"),
    _node("concept.openmc.material.chemical_formula", "openmc_concept", "Chemical formula", aliases=["chemical_formula"], concept_id="openmc.material.chemical_formula"),
    _node("concept.openmc.material.macroscopic", "openmc_concept", "Macroscopic material", aliases=["macroscopic", "MGXS"], concept_id="openmc.material.macroscopic"),
    _node("concept.openmc.data.cross_sections", "openmc_concept", "Cross section data", aliases=["cross_sections", "cross_sections.xml", "OPENMC_CROSS_SECTIONS"], concept_id="openmc.data.cross_sections", retrieval_hints=["OpenMC cross_sections.xml OPENMC_CROSS_SECTIONS environment variable"]),
    _node("concept.openmc.geometry.pin_cell_radius", "openmc_concept", "Pin-cell radius", aliases=["fuel_radius_cm", "pin_cell_radius"], concept_id="openmc.geometry.pin_cell_radius"),
    _node("concept.openmc.geometry.pin_cell_pitch", "openmc_concept", "Pin-cell pitch", aliases=["pitch_cm", "pin_cell_pitch"], concept_id="openmc.geometry.pin_cell_pitch"),
    _node("concept.openmc.geometry.cladding_radii", "openmc_concept", "Cladding radii", aliases=["clad_inner_radius_cm", "clad_outer_radius_cm"], concept_id="openmc.geometry.cladding_radii"),
    _node("concept.openmc.geometry.surface", "openmc_concept", "OpenMC surface", aliases=["Surface", "SurfaceSpec", "surface_ids"], concept_id="openmc.geometry.surface"),
    _node("concept.openmc.geometry.boundary_type", "openmc_concept", "Boundary type", aliases=["boundary_type", "vacuum", "reflective"], concept_id="openmc.geometry.boundary_type"),
    _node("concept.openmc.geometry.region_boolean_expression", "openmc_concept", "Region boolean expression", aliases=["Region", "RegionSpec", "region", "surface_ids", "overlap"], concept_id="openmc.geometry.region_boolean_expression", retrieval_hints=["OpenMC geometry overlap region surface boundary lost particle"]),
    _node("concept.openmc.geometry.cell", "openmc_concept", "Cell", aliases=["Cell", "CellSpec"], concept_id="openmc.geometry.cell"),
    _node("concept.openmc.geometry.cell_fill", "openmc_concept", "Cell fill", aliases=["fill_id", "fill_type", "Cell.fill"], concept_id="openmc.geometry.cell_fill"),
    _node("concept.openmc.geometry.universe", "openmc_concept", "Universe", aliases=["Universe", "UniverseSpec", "cell_ids"], concept_id="openmc.geometry.universe"),
    _node("concept.openmc.geometry.lattice", "openmc_concept", "Lattice", aliases=["Lattice", "LatticeSpec", "universe_pattern", "outer_universe_id"], concept_id="openmc.geometry.lattice"),
    _node("concept.openmc.geometry.rect_lattice", "openmc_concept", "RectLattice", aliases=["RectLattice", "rect", "universe_pattern"], concept_id="openmc.geometry.rect_lattice"),
    _node(
        "concept.openmc.geometry.hex_lattice",
        "openmc_concept",
        "HexLattice",
        "OpenMC hexagonal lattice using rings and outer universe.",
        aliases=["HexLattice", "hex_lattice", "rings", "hexagonal_prism", "orientation", "outer_universe_id"],
        concept_id="openmc.geometry.hex_lattice",
        retrieval_hints=["OpenMC HexLattice rings outer universe orientation"],
    ),
    _node("concept.openmc.settings.batches", "openmc_concept", "Settings batches", aliases=["batches"], concept_id="openmc.settings.batches"),
    _node("concept.openmc.settings.inactive", "openmc_concept", "Settings inactive", aliases=["inactive"], concept_id="openmc.settings.inactive"),
    _node("concept.openmc.settings.particles", "openmc_concept", "Settings particles", aliases=["particles"], concept_id="openmc.settings.particles"),
    _node("concept.openmc.settings.energy_mode", "openmc_concept", "Settings energy mode", aliases=["energy_mode"], concept_id="openmc.settings.energy_mode"),
    _node("concept.openmc.settings.seed", "openmc_concept", "Settings seed", aliases=["seed"], concept_id="openmc.settings.seed"),
    _node("concept.openmc.execution.smoke_test", "openmc_concept", "OpenMC smoke test", aliases=["smoke_test", "ExecutionCheckSpec"], concept_id="openmc.execution.smoke_test"),
    _node("concept.openmc_agent.renderability", "renderer_capability", "Renderability boundary", "Safe boundary: none, skeleton, exportable, runnable.", aliases=["renderability", "skeleton", "runnable"], concept_id="openmc_agent.renderability"),
    _node("concept.openmc_agent.renderer_selection", "renderer_capability", "Renderer selection", aliases=["supported_renderer", "choose_renderer"], concept_id="openmc_agent.renderer_selection"),
    _node("concept.openmc_agent.unsupported_subsystem", "renderer_capability", "Unsupported subsystem", aliases=["unsupported_subsystems", "renderer_unsupported"], concept_id="openmc_agent.unsupported_subsystem"),
    _node("concept.openmc_agent.human_confirmation", "openmc_concept", "Human confirmation", aliases=["required_human_confirmations", "ask_expert", "manual_review"], concept_id="openmc_agent.human_confirmation"),
]


DOCS_APIS: list[GraphNode] = [
    _node("doc.openmc.usersguide.materials", "doc_ref", "OpenMC materials user guide", ref_id="openmc.usersguide.materials"),
    _node("doc.openmc.usersguide.geometry", "doc_ref", "OpenMC geometry user guide", ref_id="openmc.usersguide.geometry"),
    _node("doc.openmc.usersguide.settings", "doc_ref", "OpenMC settings user guide", ref_id="openmc.usersguide.settings"),
    _node("doc.openmc.usersguide.cross_sections", "doc_ref", "OpenMC cross section configuration", ref_id="openmc.usersguide.cross_sections"),
    _node("doc.openmc.usersguide.troubleshoot", "doc_ref", "OpenMC troubleshooting guide", ref_id="openmc.usersguide.troubleshoot"),
    _node("api.openmc.Material.set_density", "openmc_api", "openmc.Material.set_density", api_ref="openmc.Material.set_density"),
    _node("api.openmc.Material.add_nuclide", "openmc_api", "openmc.Material.add_nuclide", api_ref="openmc.Material.add_nuclide"),
    _node("api.openmc.Material.add_s_alpha_beta", "openmc_api", "openmc.Material.add_s_alpha_beta", api_ref="openmc.Material.add_s_alpha_beta"),
    _node("api.openmc.Cell.fill", "openmc_api", "openmc.Cell.fill", api_ref="openmc.Cell.fill"),
    _node("api.openmc.Universe", "openmc_api", "openmc.Universe", api_ref="openmc.Universe"),
    _node("api.openmc.RectLattice", "openmc_api", "openmc.RectLattice", api_ref="openmc.RectLattice"),
    _node("api.openmc.HexLattice", "openmc_api", "openmc.HexLattice", api_ref="openmc.HexLattice"),
    _node("api.openmc.Surface", "openmc_api", "openmc.Surface", api_ref="openmc.Surface"),
    _node("api.openmc.Settings", "openmc_api", "openmc.Settings", api_ref="openmc.Settings"),
    _node("example.openmc.examples.pin_cell", "example_ref", "OpenMC pin-cell example", ref_id="openmc.examples.pin_cell"),
    _node("example.openmc.examples.lattice", "example_ref", "OpenMC lattice examples", ref_id="openmc.examples.lattice"),
]


REPAIR_POLICIES: list[GraphNode] = [
    _node("repair.ask_expert", "repair_policy", "ask_expert", "Requires a human-provided fact or environmental setup.", aliases=["ask_expert"], policy="ask_expert"),
    _node("repair.manual_review", "repair_policy", "manual_review", "Needs manual inspection; do not auto-patch.", aliases=["manual_review"], policy="manual_review"),
    _node("repair.reflect_plan", "repair_policy", "reflect_plan", "LLM may repair local structural plan fields using issue context.", aliases=["reflect_plan"], policy="reflect_plan"),
    _node("repair.retrieval", "repair_policy", "retrieval", "Use retrieved docs/evidence as context before repair.", aliases=["retrieval"], policy="retrieval"),
    _node("repair.auto_repair", "repair_policy", "auto_repair", "Deterministic patch is preferred before LLM reflection.", aliases=["auto_repair"], policy="auto_repair"),
    _node("repair.capability_downgrade", "repair_policy", "capability_downgrade", "Keep or downgrade renderability to skeleton/none; do not export or smoke-test unsupported models.", aliases=["capability_downgrade", "skeleton"], policy="capability_downgrade"),
]


_schema_entries: list[tuple[list[GraphNode], list[GraphEdge]]] = [
    _schema("NuclideSpec", "name", "openmc.material.nuclide_name", "OpenMC nuclide or element name.", docs=["openmc.usersguide.materials"], apis=["openmc.Material.add_nuclide"]),
    _schema("NuclideSpec", "percent", "openmc.material.composition_fraction", "Material component amount.", docs=["openmc.usersguide.materials"]),
    _schema("NuclideSpec", "percent_type", "openmc.material.percent_type", "Composition basis, atom or weight percent.", docs=["openmc.usersguide.materials"]),
    _schema("MaterialSpec", "density_unit", "openmc.material.density_unit", "Density unit for simple materials.", docs=["openmc.usersguide.materials"], apis=["openmc.Material.set_density"], hints=["OpenMC Material.set_density density units"]),
    _schema("MaterialSpec", "density_value", "openmc.material.density_value", "Positive material density value.", docs=["openmc.usersguide.materials"], apis=["openmc.Material.set_density"]),
    _schema("MaterialSpec", "composition", "openmc.material.composition", "Simple material nuclide composition.", docs=["openmc.usersguide.materials"], apis=["openmc.Material.add_nuclide"]),
    _schema("MaterialSpec", "sab", "openmc.material.thermal_scattering", "Thermal scattering library names.", docs=["openmc.usersguide.materials"], apis=["openmc.Material.add_s_alpha_beta"]),
    _schema("MaterialSpec", "chemical_formula", "openmc.material.chemical_formula", "Optional material chemical formula.", docs=["openmc.usersguide.materials"]),
    _schema("ComplexMaterialSpec", "macroscopic", "openmc.material.macroscopic", "Multi-group macroscopic dataset id.", docs=["openmc.usersguide.materials"]),
    _schema("ComplexMaterialSpec", "composition", "openmc.material.composition", "Complex IR material composition.", docs=["openmc.usersguide.materials"], apis=["openmc.Material.add_nuclide"]),
    _schema("ComplexMaterialSpec", "density_unit", "openmc.material.density_unit", "Density unit for complex materials.", docs=["openmc.usersguide.materials"], apis=["openmc.Material.set_density"], hints=["OpenMC Material.set_density density units"]),
    _schema("GeometrySpec", "fuel_radius_cm", "openmc.geometry.pin_cell_radius", "Fuel radius for pin-cell renderer.", aliases=["model_spec.pin_cell.geometry.fuel_radius_cm"], docs=["openmc.usersguide.geometry"], hints=["OpenMC pin cell fuel radius pitch"]),
    _schema("GeometrySpec", "pitch_cm", "openmc.geometry.pin_cell_pitch", "Pin pitch.", aliases=["model_spec.pin_cell.geometry.pitch_cm"], docs=["openmc.usersguide.geometry"]),
    _schema("GeometrySpec", "clad_inner_radius_cm", "openmc.geometry.cladding_radii", "Inner cladding radius.", docs=["openmc.usersguide.geometry"]),
    _schema("GeometrySpec", "clad_outer_radius_cm", "openmc.geometry.cladding_radii", "Outer cladding radius.", docs=["openmc.usersguide.geometry"]),
    _schema("SurfaceSpec", "kind", "openmc.geometry.surface", "Surface primitive kind.", docs=["openmc.usersguide.geometry"], apis=["openmc.Surface"]),
    _schema("SurfaceSpec", "parameters", "openmc.geometry.surface", "Surface parameters.", docs=["openmc.usersguide.geometry"], apis=["openmc.Surface"]),
    _schema("SurfaceSpec", "boundary_type", "openmc.geometry.boundary_type", "Surface boundary condition.", docs=["openmc.usersguide.geometry"], apis=["openmc.Surface"]),
    _schema("RegionSpec", "expression", "openmc.geometry.region_boolean_expression", "Boolean surface expression.", docs=["openmc.usersguide.geometry"]),
    _schema("RegionSpec", "surface_ids", "openmc.geometry.region_boolean_expression", "Surfaces referenced by a region.", docs=["openmc.usersguide.geometry"]),
    _schema("CellSpec", "fill_type", "openmc.geometry.cell_fill", "Cell fill kind.", docs=["openmc.usersguide.geometry"], apis=["openmc.Cell.fill"]),
    _schema("CellSpec", "fill_id", "openmc.geometry.cell_fill", "Referenced material, universe, or lattice id.", docs=["openmc.usersguide.geometry"], apis=["openmc.Cell.fill"]),
    _schema("UniverseSpec", "cell_ids", "openmc.geometry.universe", "Cells grouped into a universe.", docs=["openmc.usersguide.geometry"], apis=["openmc.Universe"]),
    _schema("LatticeSpec", "kind", "openmc.geometry.lattice", "Rect or hex lattice discriminator.", docs=["openmc.usersguide.geometry"], apis=["openmc.RectLattice", "openmc.HexLattice"]),
    _schema("LatticeSpec", "pitch_cm", "openmc.geometry.lattice", "Lattice pitch.", docs=["openmc.usersguide.geometry"], apis=["openmc.RectLattice", "openmc.HexLattice"]),
    _schema("LatticeSpec", "universe_pattern", "openmc.geometry.rect_lattice", "Rect lattice universe map.", docs=["openmc.usersguide.geometry"], apis=["openmc.RectLattice"]),
    _schema("LatticeSpec", "rings", "openmc.geometry.hex_lattice", "Hex lattice rings, center outward.", docs=["openmc.usersguide.geometry"], apis=["openmc.HexLattice"], hints=["OpenMC HexLattice rings outer universe orientation"]),
    _schema("LatticeSpec", "outer_universe_id", "openmc.geometry.hex_lattice", "Universe outside active lattice.", docs=["openmc.usersguide.geometry"], apis=["openmc.HexLattice"], hints=["OpenMC HexLattice outer universe"]),
    _schema("RunSettingsSpec", "batches", "openmc.settings.batches", "OpenMC batches setting.", docs=["openmc.usersguide.settings"], apis=["openmc.Settings"]),
    _schema("RunSettingsSpec", "inactive", "openmc.settings.inactive", "Inactive batches.", docs=["openmc.usersguide.settings"], apis=["openmc.Settings"]),
    _schema("RunSettingsSpec", "particles", "openmc.settings.particles", "Particles per batch.", docs=["openmc.usersguide.settings"], apis=["openmc.Settings"]),
    _schema("RunSettingsSpec", "energy_mode", "openmc.settings.energy_mode", "Continuous-energy or multi-group mode.", docs=["openmc.usersguide.settings"], apis=["openmc.Settings"]),
    _schema("RunSettingsSpec", "seed", "openmc.settings.seed", "Random seed.", docs=["openmc.usersguide.settings"], apis=["openmc.Settings"]),
    _schema("ExecutionCheckSpec", "settings", "openmc.execution.smoke_test", "Execution check settings.", docs=["openmc.usersguide.settings"]),
    _schema("RenderCapabilityReport", "renderability", "openmc_agent.renderability", "Renderer capability boundary.", aliases=["skeleton", "runnable"], hints=["OpenMC Agent renderability skeleton exportable runnable"]),
    _schema("RenderCapabilityReport", "supported_renderer", "openmc_agent.renderer_selection", "Selected renderer name."),
    _schema("RenderCapabilityReport", "unsupported_subsystems", "openmc_agent.unsupported_subsystem", "Unsupported plan subsystems."),
    _schema("RenderCapabilityReport", "required_human_confirmations", "openmc_agent.human_confirmation", "Human confirmations required before execution."),
]


SCHEMA_NODES: list[GraphNode] = []
SCHEMA_EDGES: list[GraphEdge] = []
for _nodes, _edges in _schema_entries:
    SCHEMA_NODES.extend(_nodes)
    SCHEMA_EDGES.extend(_edges)


ISSUE_NODES: list[GraphNode] = [
    _node("issue.runtime.cross_sections_missing", "runtime_error", "runtime.cross_sections_missing", "Cross section path or library is missing.", aliases=["runtime.cross_sections_missing", "cross_sections_missing"], error_code="runtime.cross_sections_missing", retrieval_hints=["OpenMC cross_sections.xml OPENMC_CROSS_SECTIONS environment variable"]),
    _node("issue.runtime.cross_sections_invalid", "runtime_error", "runtime.cross_sections_invalid", "Cross section path exists but is invalid.", aliases=["runtime.cross_sections_invalid", "cross_sections_invalid"], error_code="runtime.cross_sections_invalid", retrieval_hints=["OpenMC cross_sections.xml validation"]),
    _node("issue.runtime.geometry_overlap", "runtime_error", "runtime.geometry_overlap", "OpenMC detected overlapping geometry.", aliases=["runtime.geometry_overlap", "geometry_overlap", "overlap"], error_code="runtime.geometry_overlap", retrieval_hints=["OpenMC geometry overlap region surface boundary lost particle"]),
    _node("issue.runtime.lost_particle", "runtime_error", "runtime.lost_particle", "OpenMC lost particle during tracking.", aliases=["runtime.lost_particle", "lost_particle"], error_code="runtime.lost_particle", retrieval_hints=["OpenMC lost particle geometry boundary troubleshooting"]),
    _node("issue.runtime.material_missing_nuclide_data", "runtime_error", "runtime.material_missing_nuclide_data", "Material references nuclide data missing from library.", aliases=["runtime.material_missing_nuclide_data", "missing_nuclide_data"], error_code="runtime.material_missing_nuclide_data"),
    _node("issue.runtime.dagmc_or_geometry_load_failed", "runtime_error", "runtime.dagmc_or_geometry_load_failed", "Geometry or DAGMC load failed.", aliases=["dagmc_or_geometry_load_failed"], error_code="runtime.dagmc_or_geometry_load_failed"),
    _node("issue.runtime.openmc_unknown_error", "runtime_error", "runtime.openmc_unknown_error", "Unknown OpenMC runtime error.", aliases=["openmc_unknown_error"], error_code="runtime.openmc_unknown_error"),
    _node("issue.export_xml.dangling_cell_fill", "validation_issue", "export_xml.dangling_cell_fill", "Cell fill references a missing id.", aliases=["export_xml.dangling_cell_fill", "dangling_cell_fill"], error_code="export_xml.dangling_cell_fill"),
    _node("issue.export_xml.dangling_lattice_universe", "validation_issue", "export_xml.dangling_lattice_universe", "Lattice universe pattern references a missing universe.", aliases=["export_xml.dangling_lattice_universe", "dangling_lattice_universe"], error_code="export_xml.dangling_lattice_universe"),
    _node("issue.export_xml.dangling_lattice_outer_universe", "validation_issue", "export_xml.dangling_lattice_outer_universe", "Lattice outer universe references a missing universe.", aliases=["export_xml.dangling_lattice_outer_universe"], error_code="export_xml.dangling_lattice_outer_universe"),
    _node("issue.export_xml.dangling_region_surface", "validation_issue", "export_xml.dangling_region_surface", "Region references a missing surface.", aliases=["dangling_region_surface"], error_code="export_xml.dangling_region_surface"),
    _node("issue.export_xml.dangling_material_ref", "validation_issue", "export_xml.dangling_material_ref", "Material reference is missing.", aliases=["dangling_material_ref"], error_code="export_xml.dangling_material_ref"),
    _node("issue.export_xml.dangling_universe_cell", "validation_issue", "export_xml.dangling_universe_cell", "Universe references a missing cell.", aliases=["dangling_universe_cell"], error_code="export_xml.dangling_universe_cell"),
    _node("issue.export_xml.geometry_reference_unknown", "validation_issue", "export_xml.geometry_reference_unknown", "Unknown geometry reference.", aliases=["geometry_reference_unknown"], error_code="export_xml.geometry_reference_unknown"),
    _node("issue.lattice.hex.renderer_unsupported", "validation_issue", "lattice.hex.renderer_unsupported", "Hex lattice is currently diagnostic/skeleton only; HexAssemblyRenderer is not implemented.", aliases=["lattice.hex.renderer_unsupported", "renderer_unsupported", "HexAssemblyRenderer"], error_code="lattice.hex.renderer_unsupported", retrieval_hints=["OpenMC HexLattice rings outer universe orientation"]),
    _node("issue.lattice.hex.rings_missing", "validation_issue", "lattice.hex.rings_missing", "Hex lattice rings are missing.", aliases=["lattice.hex.rings_missing", "rings_missing"], error_code="lattice.hex.rings_missing", retrieval_hints=["OpenMC HexLattice rings outer universe orientation"]),
    _node("issue.lattice.hex.ring_shape_invalid", "validation_issue", "lattice.hex.ring_shape_invalid", "Hex lattice ring lengths are invalid.", aliases=["ring_shape_invalid"], error_code="lattice.hex.ring_shape_invalid", retrieval_hints=["OpenMC HexLattice ring counts"]),
    _node("issue.lattice.hex.outer_universe_missing", "validation_issue", "lattice.hex.outer_universe_missing", "Hex lattice outer universe is missing.", aliases=["outer_universe_missing"], error_code="lattice.hex.outer_universe_missing", retrieval_hints=["OpenMC HexLattice outer universe"]),
    _node("issue.lattice.hex.orientation_unverified", "validation_issue", "lattice.hex.orientation_unverified", "Hex lattice orientation/pitch/ring ordering is unverified.", aliases=["orientation_unverified"], error_code="lattice.hex.orientation_unverified", retrieval_hints=["OpenMC HexLattice orientation pitch rings"]),
]


ISSUE_EDGES: list[GraphEdge] = [
    _edge("issue.runtime.cross_sections_missing", "concept.openmc.data.cross_sections", "related_to"),
    _edge("issue.runtime.cross_sections_missing", "concept.openmc_agent.human_confirmation", "routes_to"),
    _edge("issue.runtime.cross_sections_missing", "repair.ask_expert", "routes_to"),
    _edge("issue.runtime.cross_sections_missing", "repair.manual_review", "routes_to"),
    _edge("issue.runtime.cross_sections_invalid", "concept.openmc.data.cross_sections", "related_to"),
    _edge("issue.runtime.cross_sections_invalid", "repair.ask_expert", "routes_to"),
    _edge("issue.runtime.geometry_overlap", "concept.openmc.geometry.region_boolean_expression", "related_to"),
    _edge("issue.runtime.geometry_overlap", "concept.openmc.geometry.surface", "related_to"),
    _edge("issue.runtime.geometry_overlap", "repair.reflect_plan", "routes_to"),
    _edge("issue.runtime.geometry_overlap", "repair.retrieval", "routes_to"),
    _edge("issue.runtime.lost_particle", "concept.openmc.geometry.boundary_type", "related_to"),
    _edge("issue.runtime.lost_particle", "concept.openmc.geometry.region_boolean_expression", "related_to"),
    _edge("issue.runtime.lost_particle", "repair.reflect_plan", "routes_to"),
    _edge("issue.runtime.lost_particle", "repair.retrieval", "routes_to"),
    _edge("issue.runtime.material_missing_nuclide_data", "concept.openmc.material.nuclide_name", "related_to"),
    _edge("issue.runtime.material_missing_nuclide_data", "concept.openmc.data.cross_sections", "related_to"),
    _edge("issue.runtime.material_missing_nuclide_data", "repair.ask_expert", "routes_to"),
    _edge("issue.runtime.openmc_unknown_error", "repair.manual_review", "routes_to"),
    _edge("issue.export_xml.dangling_cell_fill", "schema.CellSpec.fill_id", "raises"),
    _edge("issue.export_xml.dangling_cell_fill", "concept.openmc.geometry.cell_fill", "related_to"),
    _edge("issue.export_xml.dangling_cell_fill", "repair.auto_repair", "routes_to"),
    _edge("issue.export_xml.dangling_cell_fill", "repair.reflect_plan", "routes_to"),
    _edge("issue.export_xml.dangling_lattice_universe", "schema.LatticeSpec.universe_pattern", "raises"),
    _edge("issue.export_xml.dangling_lattice_universe", "concept.openmc.geometry.lattice", "related_to"),
    _edge("issue.export_xml.dangling_lattice_universe", "concept.openmc.geometry.universe", "related_to"),
    _edge("issue.export_xml.dangling_lattice_universe", "repair.auto_repair", "routes_to"),
    _edge("issue.export_xml.dangling_lattice_universe", "repair.reflect_plan", "routes_to"),
    _edge("issue.export_xml.dangling_lattice_outer_universe", "schema.LatticeSpec.outer_universe_id", "raises"),
    _edge("issue.export_xml.dangling_lattice_outer_universe", "concept.openmc.geometry.lattice", "related_to"),
    _edge("issue.export_xml.dangling_lattice_outer_universe", "concept.openmc.geometry.universe", "related_to"),
    _edge("issue.export_xml.dangling_lattice_outer_universe", "repair.auto_repair", "routes_to"),
    _edge("issue.export_xml.dangling_region_surface", "schema.RegionSpec.surface_ids", "raises"),
    _edge("issue.export_xml.dangling_region_surface", "concept.openmc.geometry.surface", "related_to"),
    _edge("issue.export_xml.dangling_material_ref", "schema.CellSpec.fill_id", "raises"),
    _edge("issue.export_xml.dangling_universe_cell", "schema.UniverseSpec.cell_ids", "raises"),
    _edge("issue.export_xml.geometry_reference_unknown", "repair.manual_review", "routes_to"),
    _edge("issue.lattice.hex.renderer_unsupported", "schema.LatticeSpec.kind", "raises"),
    _edge("issue.lattice.hex.renderer_unsupported", "schema.LatticeSpec.rings", "related_to"),
    _edge("issue.lattice.hex.renderer_unsupported", "concept.openmc.geometry.hex_lattice", "related_to"),
    _edge("issue.lattice.hex.renderer_unsupported", "concept.openmc_agent.renderability", "downgrades_to"),
    _edge("issue.lattice.hex.renderer_unsupported", "repair.capability_downgrade", "routes_to"),
    _edge("issue.lattice.hex.rings_missing", "schema.LatticeSpec.rings", "raises"),
    _edge("issue.lattice.hex.rings_missing", "concept.openmc.geometry.hex_lattice", "related_to"),
    _edge("issue.lattice.hex.ring_shape_invalid", "schema.LatticeSpec.rings", "raises"),
    _edge("issue.lattice.hex.ring_shape_invalid", "concept.openmc.geometry.hex_lattice", "related_to"),
    _edge("issue.lattice.hex.outer_universe_missing", "schema.LatticeSpec.outer_universe_id", "raises"),
    _edge("issue.lattice.hex.outer_universe_missing", "concept.openmc.geometry.hex_lattice", "related_to"),
    _edge("issue.lattice.hex.orientation_unverified", "concept.openmc.geometry.hex_lattice", "related_to"),
    _edge("issue.lattice.hex.orientation_unverified", "repair.manual_review", "routes_to"),
]


SUPPORT_EDGES: list[GraphEdge] = [
    _edge("concept.openmc.material.density_unit", "doc.openmc.usersguide.materials", "documented_in"),
    _edge("concept.openmc.material.density_unit", "api.openmc.Material.set_density", "implemented_by"),
    _edge("concept.openmc.material.nuclide_name", "api.openmc.Material.add_nuclide", "implemented_by"),
    _edge("concept.openmc.material.composition", "doc.openmc.usersguide.materials", "documented_in"),
    _edge("concept.openmc.data.cross_sections", "doc.openmc.usersguide.cross_sections", "documented_in"),
    _edge("concept.openmc.geometry.surface", "api.openmc.Surface", "implemented_by"),
    _edge("concept.openmc.geometry.region_boolean_expression", "doc.openmc.usersguide.geometry", "documented_in"),
    _edge("concept.openmc.geometry.cell_fill", "api.openmc.Cell.fill", "implemented_by"),
    _edge("concept.openmc.geometry.universe", "api.openmc.Universe", "implemented_by"),
    _edge("concept.openmc.geometry.lattice", "doc.openmc.usersguide.geometry", "documented_in"),
    _edge("concept.openmc.geometry.lattice", "example.openmc.examples.lattice", "demonstrated_by"),
    _edge("concept.openmc.geometry.rect_lattice", "api.openmc.RectLattice", "implemented_by"),
    _edge("concept.openmc.geometry.hex_lattice", "api.openmc.HexLattice", "implemented_by"),
    _edge("concept.openmc.geometry.hex_lattice", "doc.openmc.usersguide.geometry", "documented_in"),
    _edge("concept.openmc.geometry.hex_lattice", "concept.openmc.geometry.lattice", "related_to"),
    _edge("concept.openmc.geometry.hex_lattice", "concept.openmc_agent.renderability", "related_to"),
    _edge("concept.openmc.settings.batches", "api.openmc.Settings", "implemented_by"),
    _edge("concept.openmc.execution.smoke_test", "doc.openmc.usersguide.settings", "documented_in"),
]


_all_nodes = [*CONCEPTS, *DOCS_APIS, *REPAIR_POLICIES, *SCHEMA_NODES, *ISSUE_NODES]
GRAPH_NODES: dict[str, GraphNode] = {node.id: node for node in _all_nodes}
GRAPH_EDGES: list[GraphEdge] = [*SCHEMA_EDGES, *ISSUE_EDGES, *SUPPORT_EDGES]

