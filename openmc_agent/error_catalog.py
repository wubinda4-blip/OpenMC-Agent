"""Stable error-code catalog for OpenMC-agent validation.

Each entry maps a stable ``error_code`` to a :class:`~openmc_agent.schemas.ValidationIssue`
blueprint: severity, human-readable message, schema path, rule id, related OpenMC
``concept_id``, knowledge references (manual / API / example pointers), and
structured repair hints.

Design notes
------------
* **Short knowledge lives here; long knowledge is referenced.**  Entries carry
  pointers (``KnowledgeRef``) to the OpenMC user guide / API rather than embedding
  manual text.  This keeps the catalog small while giving future retrieval,
  knowledge-graph, and GraphRAG layers stable hooks to grab onto.
* **Backward compatible.**  ``message`` strings mirror the legacy free-text errors
  emitted by :mod:`openmc_agent.validator`, so legacy callers that read
  ``report.errors`` see identical text.  The catalog only *adds* structure around
  those messages.
* **Extensible.**  Codes not present here fall back to a minimal issue via
  :func:`issue_from_catalog`, so new validators can emit issues before their
  catalog entry is written.
"""

from __future__ import annotations

from typing import Any

from openmc_agent.schemas import KnowledgeRef, RepairHint, ValidationIssue


def _ref(
    ref_id: str,
    title: str,
    source_type: str,
    *,
    locator: str | None = None,
    retrieval_query: str | None = None,
    concept_id: str | None = None,
) -> KnowledgeRef:
    return KnowledgeRef(
        ref_id=ref_id,
        title=title,
        source_type=source_type,  # type: ignore[arg-type]
        locator=locator,
        retrieval_query=retrieval_query,
        concept_id=concept_id,
    )


def _hint(
    action: str,
    message: str,
    *,
    target_path: str | None = None,
    example_patch: dict[str, Any] | None = None,
) -> RepairHint:
    return RepairHint(
        action=action,  # type: ignore[arg-type]
        message=message,
        target_path=target_path,
        example_patch=example_patch,
    )


GEOMETRY_GUIDE = _ref(
    "openmc.usersguide.geometry",
    "OpenMC geometry user guide",
    "openmc_docs",
    locator="OpenMC User Guide > Defining Geometry",
    retrieval_query="OpenMC pin cell geometry fuel radius pitch cladding",
    concept_id="openmc.geometry.pin_cell_radius",
)
MATERIALS_GUIDE = _ref(
    "openmc.usersguide.materials",
    "OpenMC materials user guide",
    "openmc_docs",
    locator="OpenMC User Guide > Defining Materials",
    retrieval_query="OpenMC material density composition macroscopic",
    concept_id="openmc.material.density_unit",
)
SETTINGS_GUIDE = _ref(
    "openmc.usersguide.settings",
    "OpenMC settings user guide",
    "openmc_docs",
    locator="OpenMC User Guide > Defining Settings",
    retrieval_query="OpenMC settings batches inactive particles energy mode",
    concept_id="openmc.settings.batches",
)
LATTICE_GUIDE = _ref(
    "openmc.usersguide.geometry",
    "OpenMC lattices and repeated geometry",
    "openmc_docs",
    locator="OpenMC User Guide > Defining Geometry > Lattices",
    retrieval_query="OpenMC RectLattice HexLattice universe repeated geometry",
    concept_id="openmc.geometry.lattice",
)
CROSS_SECTIONS_GUIDE = _ref(
    "openmc.usersguide.cross_sections",
    "OpenMC cross section data configuration",
    "openmc_docs",
    locator="OpenMC User Guide > Cross Section Configuration",
    retrieval_query="OpenMC cross_sections.xml OPENMC_CROSS_SECTIONS configuration",
    concept_id="openmc.data.cross_sections",
)
OPENMC_RUNTIME_GUIDE = _ref(
    "openmc.usersguide.troubleshoot",
    "OpenMC runtime diagnostics",
    "openmc_docs",
    locator="OpenMC User Guide > Troubleshooting",
    retrieval_query="OpenMC geometry overlap lost particle troubleshooting",
    concept_id="openmc.runtime.diagnostics",
)


# Type alias describing one catalog entry.  Kept as a plain dict so the catalog
# stays declarative and easy to audit/diff.
CatalogEntry = dict[str, Any]


ERROR_CATALOG: dict[str, CatalogEntry] = {
    # ---------------------------------------------------------------- geometry
    "geometry.fuel_radius.out_of_range": {
        "severity": "error",
        "message": "fuel_radius_cm is outside the supported pin-cell range (0, 2.0] cm",
        "schema_path": "model_spec.pin_cell.geometry.fuel_radius_cm",
        "rule_id": "rule.geometry.pin_cell.fuel_radius_supported_range",
        "concept_id": "openmc.geometry.pin_cell_radius",
        "knowledge_refs": [GEOMETRY_GUIDE],
        "repair_hints": [
            _hint(
                "edit_field",
                "Set fuel_radius_cm to a value in (0, 2.0] cm, typically 0.4-0.5 for LWR UO2.",
                target_path="model_spec.pin_cell.geometry.fuel_radius_cm",
            )
        ],
    },
    "geometry.pitch.out_of_range": {
        "severity": "error",
        "message": "pitch_cm is outside the supported range (0, 5.0] cm",
        "schema_path": "model_spec.pin_cell.geometry.pitch_cm",
        "rule_id": "rule.geometry.pin_cell.pitch_supported_range",
        "concept_id": "openmc.geometry.pin_cell_pitch",
        "knowledge_refs": [GEOMETRY_GUIDE],
        "repair_hints": [
            _hint(
                "edit_field",
                "Set pitch_cm to a value in (0, 5.0] cm consistent with the lattice layout.",
                target_path="model_spec.pin_cell.geometry.pitch_cm",
            )
        ],
    },
    "geometry.fuel_radius.too_large_for_pitch": {
        "severity": "error",
        "message": "fuel_radius_cm must be less than half of pitch_cm",
        "schema_path": "model_spec.pin_cell.geometry.fuel_radius_cm",
        "rule_id": "rule.geometry.pin_cell.fuel_radius_lt_half_pitch",
        "concept_id": "openmc.geometry.pin_cell_radius",
        "knowledge_refs": [GEOMETRY_GUIDE],
        "repair_hints": [
            _hint(
                "edit_field",
                "Reduce fuel_radius_cm or increase pitch_cm so fuel_radius_cm < pitch_cm / 2.",
                target_path="model_spec.pin_cell.geometry",
                example_patch={"fuel_radius_cm": "< pitch_cm / 2"},
            )
        ],
    },
    "geometry.cladding.radii_partial_missing": {
        "severity": "error",
        "message": "clad_inner_radius_cm and clad_outer_radius_cm must both be set",
        "schema_path": "model_spec.pin_cell.geometry",
        "rule_id": "rule.geometry.pin_cell.cladding_radii_paired",
        "concept_id": "openmc.geometry.cladding_radii",
        "knowledge_refs": [GEOMETRY_GUIDE],
        "repair_hints": [
            _hint(
                "add_missing_field",
                "Provide both clad_inner_radius_cm and clad_outer_radius_cm, or neither.",
                target_path="model_spec.pin_cell.geometry",
            )
        ],
    },
    "geometry.cladding.inner_not_greater_than_fuel": {
        "severity": "error",
        "message": "clad_inner_radius_cm must exceed fuel_radius_cm",
        "schema_path": "model_spec.pin_cell.geometry.clad_inner_radius_cm",
        "rule_id": "rule.geometry.pin_cell.clad_inner_gt_fuel",
        "concept_id": "openmc.geometry.cladding_radii",
        "knowledge_refs": [GEOMETRY_GUIDE],
        "repair_hints": [
            _hint(
                "edit_field",
                "Increase clad_inner_radius_cm to leave a fuel-cladding gap (e.g. fuel + gap).",
                target_path="model_spec.pin_cell.geometry.clad_inner_radius_cm",
            )
        ],
    },
    "geometry.cladding.outer_not_greater_than_inner": {
        "severity": "error",
        "message": "clad_outer_radius_cm must exceed clad_inner_radius_cm",
        "schema_path": "model_spec.pin_cell.geometry.clad_outer_radius_cm",
        "rule_id": "rule.geometry.pin_cell.clad_outer_gt_inner",
        "concept_id": "openmc.geometry.cladding_radii",
        "knowledge_refs": [GEOMETRY_GUIDE],
        "repair_hints": [
            _hint(
                "edit_field",
                "Increase clad_outer_radius_cm to model a cladding tube with positive thickness.",
                target_path="model_spec.pin_cell.geometry.clad_outer_radius_cm",
            )
        ],
    },
    "geometry.cladding.outer_too_large_for_pitch": {
        "severity": "error",
        "message": "clad_outer_radius_cm must be less than half of pitch_cm",
        "schema_path": "model_spec.pin_cell.geometry.clad_outer_radius_cm",
        "rule_id": "rule.geometry.pin_cell.clad_outer_lt_half_pitch",
        "concept_id": "openmc.geometry.cladding_radii",
        "knowledge_refs": [GEOMETRY_GUIDE],
        "repair_hints": [
            _hint(
                "edit_field",
                "Reduce clad_outer_radius_cm or increase pitch_cm so it stays inside the unit cell.",
                target_path="model_spec.pin_cell.geometry",
            )
        ],
    },
    "geometry.cladding.material_missing_for_radii": {
        "severity": "error",
        "message": "cladding radii are present but cladding material is missing",
        "schema_path": "model_spec.pin_cell.cladding",
        "rule_id": "rule.geometry.pin_cell.cladding_material_for_radii",
        "concept_id": "openmc.material.density_unit",
        "knowledge_refs": [GEOMETRY_GUIDE, MATERIALS_GUIDE],
        "repair_hints": [
            _hint(
                "add_missing_field",
                "Add a cladding MaterialSpec (e.g. Zircaloy) matching the cladding radii.",
                target_path="model_spec.pin_cell.cladding",
            )
        ],
    },
    "geometry.cladding.radii_missing_for_material": {
        "severity": "error",
        "message": "cladding material is present but cladding radii are missing",
        "schema_path": "model_spec.pin_cell.geometry",
        "rule_id": "rule.geometry.pin_cell.cladding_radii_for_material",
        "concept_id": "openmc.geometry.cladding_radii",
        "knowledge_refs": [GEOMETRY_GUIDE],
        "repair_hints": [
            _hint(
                "add_missing_field",
                "Add clad_inner_radius_cm and clad_outer_radius_cm for the cladding material.",
                target_path="model_spec.pin_cell.geometry",
            )
        ],
    },
    # --------------------------------------------------------------- settings
    "settings.inactive.not_less_than_batches": {
        "severity": "error",
        "message": "inactive must be less than batches",
        "schema_path": "settings.inactive",
        "rule_id": "rule.settings.inactive_lt_batches",
        "concept_id": "openmc.settings.inactive",
        "knowledge_refs": [SETTINGS_GUIDE],
        "repair_hints": [
            _hint(
                "edit_field",
                "Reduce inactive or increase batches so inactive < batches.",
                target_path="settings",
                example_patch={"inactive": "< batches"},
            )
        ],
    },
    # ------------------------------------------------------------ plan-level
    "plan.model.missing": {
        "severity": "error",
        "message": "SimulationPlan requires model_spec or complex_model",
        "schema_path": "model_spec",
        "rule_id": "rule.plan.requires_model",
        "concept_id": "openmc.ir.model_kind",
        "knowledge_refs": [
            _ref(
                "project.simulation_plan",
                "SimulationPlan structure",
                "project_rule",
                locator="openmc_agent.schemas.SimulationPlan",
            )
        ],
        "repair_hints": [
            _hint(
                "add_missing_field",
                "Provide a pin-cell model_spec or a complex_model IR.",
                target_path="model_spec",
            )
        ],
    },
    "plan.complex_model.non_executable": {
        "severity": "warning",
        "message": "Complex OpenMC IR was generated, but this executor version cannot render it yet.",
        "schema_path": "capability_report",
        "rule_id": "rule.plan.complex_model_not_renderable",
        "concept_id": "openmc_agent.renderability",
        "knowledge_refs": [LATTICE_GUIDE],
        "repair_hints": [
            _hint(
                "downgrade_renderability",
                "Review complex_model and capability_report before implementing a renderer for this subsystem.",
                target_path="capability_report",
            )
        ],
    },
    "plan.executable.unsupported_renderer": {
        "severity": "error",
        "message": "Executable plans require model_spec or supported_renderer='assembly'/'triso'/'core'",
        "schema_path": "capability_report.supported_renderer",
        "rule_id": "rule.plan.executable_needs_renderer",
        "concept_id": "openmc_agent.renderer_selection",
        "knowledge_refs": [
            _ref(
                "project.capability_report",
                "Capability report and renderer selection",
                "project_rule",
                locator="openmc_agent.schemas.RenderCapabilityReport",
            )
        ],
        "repair_hints": [
            _hint(
                "switch_renderer",
                "Add a model_spec for pin_cell, or set supported_renderer to assembly/triso/core with matching complex_model.kind.",
                target_path="capability_report.supported_renderer",
            )
        ],
    },
    # ----------------------------------------------------------- script-level
    "script.missing_structure": {
        "severity": "error",
        "message": "script missing required OpenMC structure",
        "schema_path": "script",
        "rule_id": "rule.script.required_structure",
        "concept_id": "openmc_agent.renderability",
        "knowledge_refs": [
            _ref(
                "openmc.usersguide.model",
                "OpenMC model composition (materials, geometry, settings, tallies)",
                "openmc_docs",
                locator="OpenMC User Guide > Model API",
                retrieval_query="OpenMC openmc.Model materials geometry settings tallies",
            )
        ],
        "repair_hints": [
            _hint(
                "add_missing_field",
                "Ensure the script builds materials, geometry, settings, tallies, and calls model.export_to_xml().",
                target_path="script",
            )
        ],
    },
    "script.material_not_referenced": {
        "severity": "error",
        "message": "material declared in spec is not referenced in script",
        "schema_path": "script",
        "rule_id": "rule.script.references_spec_materials",
        "concept_id": "openmc.material.density_unit",
        "knowledge_refs": [MATERIALS_GUIDE],
        "repair_hints": [
            _hint(
                "edit_field",
                "Assign the material to the matching cell so it appears in the generated geometry.",
                target_path="script",
            )
        ],
    },
    # ----------------------------------------------- schema-level model errors
    "material.definition.missing": {
        "severity": "error",
        "message": "complex material needs composition, chemical_formula, macroscopic, or requires_human_confirmation",
        "schema_path": "complex_model.materials",
        "rule_id": "rule.material.requires_definition",
        "concept_id": "openmc.material.composition",
        "knowledge_refs": [MATERIALS_GUIDE],
        "repair_hints": [
            _hint(
                "add_missing_field",
                "Add composition, chemical_formula, or macroscopic data; or flag the gap for human confirmation.",
                target_path="complex_model.materials",
            ),
            _hint(
                "mark_requires_human_confirmation",
                "If the source document omits the data, list the gap in requires_human_confirmation.",
            ),
        ],
    },
    "material.macroscopic.invalid_density_unit": {
        "severity": "error",
        "message": "macroscopic materials must use density_unit='macro' or omit density",
        "schema_path": "complex_model.materials.density_unit",
        "rule_id": "rule.material.macroscopic_density_unit",
        "concept_id": "openmc.material.macroscopic",
        "knowledge_refs": [MATERIALS_GUIDE],
        "repair_hints": [
            _hint(
                "edit_field",
                "Set density_unit='macro' (or null) for a macroscopic cross-section material.",
                target_path="complex_model.materials.density_unit",
                example_patch={"density_unit": "macro"},
            )
        ],
    },
    "material.density.partial_missing": {
        "severity": "error",
        "message": "density_unit and density_value must be provided together",
        "schema_path": "complex_model.materials",
        "rule_id": "rule.material.density_paired",
        "concept_id": "openmc.material.density_unit",
        "knowledge_refs": [MATERIALS_GUIDE],
        "repair_hints": [
            _hint(
                "add_missing_field",
                "Provide both density_unit and density_value, or flag the material for human confirmation.",
                target_path="complex_model.materials",
            )
        ],
    },
    "cell.fill_id.missing": {
        "severity": "error",
        "message": "fill_id is required unless fill_type is void",
        "schema_path": "complex_model.cells",
        "rule_id": "rule.cell.requires_fill_id",
        "concept_id": "openmc.geometry.cell_fill",
        "knowledge_refs": [GEOMETRY_GUIDE, LATTICE_GUIDE],
        "repair_hints": [
            _hint(
                "add_missing_field",
                "Set fill_id to a defined material, universe, or lattice id (or fill_type='void').",
                target_path="complex_model.cells",
            )
        ],
    },
    "lattice.rect.universe_pattern_missing": {
        "severity": "warning",
        "message": "rect lattice universe_pattern is missing",
        "schema_path": "complex_model.lattices",
        "rule_id": "rule.lattice.rect_requires_pattern",
        "concept_id": "openmc.geometry.rect_lattice",
        "knowledge_refs": [LATTICE_GUIDE],
        "requires_human_confirmation": True,
        "repair_hints": [
            _hint(
                "add_missing_field",
                "Provide universe_pattern as a rectangular 2D list of defined universe ids.",
                target_path="complex_model.lattices.universe_pattern",
            ),
            _hint(
                "mark_requires_human_confirmation",
                "If the loading pattern is unknown, record it in requires_human_confirmation.",
            ),
        ],
    },
    "lattice.hex.rings_missing": {
        "severity": "warning",
        "message": "hex lattice rings are missing",
        "schema_path": "complex_model.lattices",
        "rule_id": "rule.lattice.hex_requires_rings",
        "concept_id": "openmc.geometry.hex_lattice",
        "knowledge_refs": [LATTICE_GUIDE],
        "grep_patterns": ["LatticeSpec", "HexLattice", "rings", "outer_universe_id"],
        "requires_retrieval": True,
        "requires_human_confirmation": True,
        "route_hint": "ask_expert",
        "repair_hints": [
            _hint(
                "add_missing_field",
                "Provide rings as a list of hex rings (1, 6, 12, ... elements) of defined universe ids.",
                target_path="complex_model.lattices.rings",
            ),
            _hint(
                "mark_requires_human_confirmation",
                "If the loading pattern is unknown, record it in requires_human_confirmation.",
            ),
        ],
    },
    "triso.layers.not_strictly_increasing": {
        "severity": "error",
        "message": "TRISO layer outer_radius_cm values must be strictly increasing",
        "schema_path": "complex_model.trisos.layers",
        "rule_id": "rule.triso.layers_strictly_increasing",
        "concept_id": "openmc_agent.renderability",
        "knowledge_refs": [
            _ref(
                "openmc.usersguide.geometry",
                "OpenMC TRISO layered particle geometry",
                "openmc_docs",
                locator="OpenMC User Guide > TRISO Particles",
                retrieval_query="OpenMC TRISO particle layer radius ordering",
            )
        ],
        "repair_hints": [
            _hint(
                "edit_field",
                "Reorder layers so outer_radius_cm strictly increases from kernel outward.",
                target_path="complex_model.trisos.layers",
            )
        ],
    },
    "pebble.fuel_zone_radius.too_large": {
        "severity": "error",
        "message": "fuel_zone_radius_cm must be less than outer_radius_cm",
        "schema_path": "complex_model.pebbles",
        "rule_id": "rule.pebble.fuel_zone_inside_outer",
        "concept_id": "openmc_agent.renderability",
        "knowledge_refs": [GEOMETRY_GUIDE],
        "repair_hints": [
            _hint(
                "edit_field",
                "Reduce fuel_zone_radius_cm so the fuel zone fits inside the pebble outer radius.",
                target_path="complex_model.pebbles.fuel_zone_radius_cm",
            )
        ],
    },
    "plan.pin_cell.requires_model_spec": {
        "severity": "error",
        "message": "pin_cell executable plans require model_spec",
        "schema_path": "model_spec",
        "rule_id": "rule.plan.pin_cell_requires_model_spec",
        "concept_id": "openmc_agent.renderer_selection",
        "knowledge_refs": [GEOMETRY_GUIDE],
        "repair_hints": [
            _hint(
                "add_missing_field",
                "Provide a pin-cell model_spec, or switch the supported_renderer away from pin_cell.",
                target_path="model_spec",
            )
        ],
    },
    "plan.assembly.requires_complex_assembly": {
        "severity": "error",
        "message": "assembly renderer requires complex_model.kind='assembly'",
        "schema_path": "complex_model.kind",
        "rule_id": "rule.plan.assembly_requires_complex_assembly",
        "concept_id": "openmc_agent.renderer_selection",
        "knowledge_refs": [LATTICE_GUIDE],
        "repair_hints": [
            _hint(
                "edit_field",
                "Set complex_model.kind='assembly' with cells/universes/lattices describing the repeated geometry.",
                target_path="complex_model.kind",
                example_patch={"kind": "assembly"},
            )
        ],
    },
    "plan.triso.requires_complex_triso_or_pebble": {
        "severity": "error",
        "message": "triso renderer requires complex_model.kind='triso_compact' or 'pebble'",
        "schema_path": "complex_model.kind",
        "rule_id": "rule.plan.triso_requires_complex_triso",
        "concept_id": "openmc_agent.renderer_selection",
        "knowledge_refs": [GEOMETRY_GUIDE],
        "repair_hints": [
            _hint(
                "edit_field",
                "Set complex_model.kind to 'triso_compact' or 'pebble' and define the TRISO/pebble layers.",
                target_path="complex_model.kind",
            )
        ],
    },
    "plan.core.requires_complex_core": {
        "severity": "error",
        "message": "core renderer requires complex_model.kind='core'",
        "schema_path": "complex_model.kind",
        "rule_id": "rule.plan.core_requires_complex_core",
        "concept_id": "openmc_agent.renderer_selection",
        "knowledge_refs": [LATTICE_GUIDE],
        "repair_hints": [
            _hint(
                "edit_field",
                "Set complex_model.kind='core' and define the core lattice loading pattern.",
                target_path="complex_model.kind",
                example_patch={"kind": "core"},
            )
        ],
    },
    "plan.non_executable.renderer_must_be_none": {
        "severity": "error",
        "message": "non-executable complex-only plans must use supported_renderer='none'",
        "schema_path": "capability_report.supported_renderer",
        "rule_id": "rule.plan.non_executable_renderer_none",
        "concept_id": "openmc_agent.renderer_selection",
        "knowledge_refs": [
            _ref(
                "project.capability_report",
                "Capability report and renderer selection",
                "project_rule",
                locator="openmc_agent.schemas.RenderCapabilityReport",
            )
        ],
        "repair_hints": [
            _hint(
                "switch_renderer",
                "Set capability_report.supported_renderer='none' for non-executable complex-only plans.",
                target_path="capability_report.supported_renderer",
                example_patch={"supported_renderer": "none"},
            )
        ],
    },
    # ----------------------------------------------- repeated-geometry refs
    "lattice.universe_ref_missing": {
        "severity": "error",
        "message": "lattice universe_pattern references missing universes",
        "schema_path": "complex_model.lattices.universe_pattern",
        "rule_id": "rule.lattice.universe_ref_exists",
        "concept_id": "openmc.geometry.rect_lattice",
        "knowledge_refs": [LATTICE_GUIDE],
        "repair_hints": [
            _hint(
                "edit_field",
                "Fix the universe id typo or add the missing universe with that exact id.",
                target_path="complex_model.lattices.universe_pattern",
            ),
        ],
    },
    "lattice.shape_pattern_mismatch": {
        "severity": "error",
        "message": "lattice shape does not match universe_pattern dimensions",
        "schema_path": "complex_model.lattices",
        "rule_id": "rule.lattice.shape_matches_pattern",
        "concept_id": "openmc.geometry.rect_lattice",
        "knowledge_refs": [LATTICE_GUIDE],
        "repair_hints": [
            _hint(
                "edit_field",
                "Reconcile shape=(nx,ny) with the universe_pattern rows/cols count.",
                target_path="complex_model.lattices.shape",
            ),
        ],
    },
    "lattice.pattern_ragged_rows": {
        "severity": "error",
        "message": "lattice universe_pattern rows have unequal lengths",
        "schema_path": "complex_model.lattices.universe_pattern",
        "rule_id": "rule.lattice.rectangular_pattern",
        "concept_id": "openmc.geometry.rect_lattice",
        "knowledge_refs": [LATTICE_GUIDE],
        "repair_hints": [
            _hint(
                "edit_field",
                "Pad or trim rows so every universe_pattern row has the same length.",
                target_path="complex_model.lattices.universe_pattern",
            ),
        ],
    },
    "lattice.pin_count_mismatch": {
        "severity": "error",
        "message": "lattice pin counts do not match expected_counts",
        "schema_path": "complex_model.lattices",
        "rule_id": "rule.lattice.expected_counts",
        "concept_id": "openmc.geometry.rect_lattice",
        "knowledge_refs": [LATTICE_GUIDE],
        "repair_hints": [
            _hint(
                "edit_field",
                "Recompute fill_universe + overrides (row,col) positions so each universe count matches expected_counts.",
                target_path="complex_model.lattices.overrides",
            ),
        ],
    },
    "cell.material_ref_missing": {
        "severity": "error",
        "message": "cell references missing material",
        "schema_path": "complex_model.cells",
        "rule_id": "rule.cell.material_ref_exists",
        "concept_id": "openmc.geometry.cell_fill",
        "knowledge_refs": [MATERIALS_GUIDE],
        "repair_hints": [
            _hint(
                "edit_field",
                "Fix the material id typo or add the missing material.",
                target_path="complex_model.cells.fill_id",
            ),
        ],
    },
    "cell.region_ref_missing": {
        "severity": "error",
        "message": "cell references missing region",
        "schema_path": "complex_model.cells",
        "rule_id": "rule.cell.region_ref_exists",
        "concept_id": "openmc.geometry.region_boolean_expression",
        "knowledge_refs": [GEOMETRY_GUIDE],
        "repair_hints": [
            _hint(
                "edit_field",
                "Fix the region id typo or add the missing region.",
                target_path="complex_model.cells.region_id",
            ),
        ],
    },
    "cell.universe_ref_missing": {
        "severity": "error",
        "message": "cell references missing universe",
        "schema_path": "complex_model.cells",
        "rule_id": "rule.cell.universe_ref_exists",
        "concept_id": "openmc.geometry.cell_fill",
        "knowledge_refs": [LATTICE_GUIDE],
        "repair_hints": [
            _hint(
                "edit_field",
                "Fix the universe id typo or add the missing universe.",
                target_path="complex_model.cells.fill_id",
            ),
        ],
    },
    "cell.lattice_ref_missing": {
        "severity": "error",
        "message": "cell references missing lattice",
        "schema_path": "complex_model.cells",
        "rule_id": "rule.cell.lattice_ref_exists",
        "concept_id": "openmc.geometry.cell_fill",
        "knowledge_refs": [LATTICE_GUIDE],
        "repair_hints": [
            _hint(
                "edit_field",
                "Fix the lattice id typo or add the missing lattice.",
                target_path="complex_model.cells.fill_id",
            ),
        ],
    },
    "universe.cell_ref_missing": {
        "severity": "error",
        "message": "universe references missing cells",
        "schema_path": "complex_model.universes.cell_ids",
        "rule_id": "rule.universe.cell_ref_exists",
        "concept_id": "openmc.geometry.universe",
        "knowledge_refs": [LATTICE_GUIDE],
        "repair_hints": [
            _hint(
                "edit_field",
                "Fix the cell id typo or add the missing cell.",
                target_path="complex_model.universes.cell_ids",
            ),
        ],
    },
    "region.surface_ref_missing": {
        "severity": "error",
        "message": "region references missing surfaces",
        "schema_path": "complex_model.regions",
        "rule_id": "rule.region.surface_ref_exists",
        "concept_id": "openmc.geometry.region_surface_refs",
        "knowledge_refs": [GEOMETRY_GUIDE],
        "repair_hints": [
            _hint(
                "edit_field",
                "Fix the surface id typo or add the missing surface.",
                target_path="complex_model.regions.surface_ids",
            ),
        ],
    },
    "surface.cylinder_radius_invalid": {
        "severity": "error",
        "message": "cylinder surface radius is invalid",
        "schema_path": "complex_model.surfaces",
        "rule_id": "rule.surface.cylinder_radius",
        "concept_id": "openmc.geometry.surface_parameters",
        "knowledge_refs": [GEOMETRY_GUIDE],
        "repair_hints": [
            _hint(
                "edit_field",
                "Set a positive numeric radius < pitch/2.",
                target_path="complex_model.surfaces.parameters.r",
            ),
        ],
    },
    "axial_layer.fill_ref_missing": {
        "severity": "error",
        "message": "axial layer references missing fill",
        "schema_path": "complex_model.core.axial_layers.fill.id",
        "rule_id": "rule.axial_layer.fill_ref_exists",
        "concept_id": "openmc.geometry.cell_fill",
        "knowledge_refs": [LATTICE_GUIDE],
        "repair_hints": [
            _hint(
                "edit_field",
                "Fix the fill id typo or add the missing material/universe/lattice.",
                target_path="complex_model.core.axial_layers.fill.id",
            ),
        ],
    },
    "axial_layer.loading_ref_missing": {
        "severity": "error",
        "message": "axial layer references missing lattice loading",
        "schema_path": "complex_model.core.axial_layers.loading_id",
        "rule_id": "rule.axial_layer.loading_ref_exists",
        "concept_id": "openmc.geometry.lattice",
        "knowledge_refs": [LATTICE_GUIDE],
        "repair_hints": [
            _hint(
                "edit_field",
                "Fix loading_id or add the corresponding lattice_loadings entry.",
                target_path="complex_model.core.axial_layers.loading_id",
            ),
        ],
    },
    "lattice_loading.base_ref_missing": {
        "severity": "error",
        "message": "lattice loading references missing base lattice",
        "schema_path": "complex_model.lattice_loadings.base_lattice_id",
        "rule_id": "rule.lattice_loading.base_ref_exists",
        "concept_id": "openmc.geometry.rect_lattice",
        "knowledge_refs": [LATTICE_GUIDE],
        "repair_hints": [
            _hint(
                "edit_field",
                "Fix base_lattice_id or add the missing base lattice.",
                target_path="complex_model.lattice_loadings.base_lattice_id",
            ),
        ],
    },
    "lattice_loading.override_universe_ref_missing": {
        "severity": "error",
        "message": "lattice loading override references missing universe",
        "schema_path": "complex_model.lattice_loadings.overrides",
        "rule_id": "rule.lattice_loading.override_universe_ref_exists",
        "concept_id": "openmc.geometry.rect_lattice",
        "knowledge_refs": [LATTICE_GUIDE],
        "repair_hints": [
            _hint(
                "edit_field",
                "Fix the override universe id typo or add the missing universe.",
                target_path="complex_model.lattice_loadings.overrides",
            ),
        ],
    },
    "lattice_loading.override_position_oob": {
        "severity": "error",
        "message": "lattice loading override position is out of bounds",
        "schema_path": "complex_model.lattice_loadings.overrides",
        "rule_id": "rule.lattice_loading.override_position_bounds",
        "concept_id": "openmc.geometry.rect_lattice",
        "knowledge_refs": [LATTICE_GUIDE],
        "repair_hints": [
            _hint(
                "edit_field",
                "Move override positions inside the base lattice row/column bounds.",
                target_path="complex_model.lattice_loadings.overrides",
            ),
        ],
    },
    # -------------------------------------------------------------- runtime
    "runtime.cross_sections_missing": {
        "severity": "error",
        "message": "OpenMC cross section data is missing or not configured.",
        "schema_path": "runtime.cross_sections",
        "rule_id": "rule.runtime.cross_sections_missing",
        "concept_id": "openmc.data.cross_sections",
        "knowledge_refs": [CROSS_SECTIONS_GUIDE],
        "grep_patterns": ["cross_sections.xml", "OPENMC_CROSS_SECTIONS", "openmc_data"],
        "requires_human_confirmation": True,
        "route_hint": "ask_expert",
        "repair_hints": [
            _hint(
                "ask_human",
                "Ask the user to confirm the installed nuclear data library and OPENMC_CROSS_SECTIONS path; do not invent a path.",
                target_path="environment.OPENMC_CROSS_SECTIONS",
            )
        ],
    },
    "runtime.cross_sections_invalid": {
        "severity": "error",
        "message": "OpenMC cross section data path or XML file is invalid.",
        "schema_path": "runtime.cross_sections",
        "rule_id": "rule.runtime.cross_sections_invalid",
        "concept_id": "openmc.data.cross_sections",
        "knowledge_refs": [CROSS_SECTIONS_GUIDE],
        "grep_patterns": ["cross_sections.xml", "OPENMC_CROSS_SECTIONS", "not present in cross_sections.xml"],
        "requires_human_confirmation": True,
        "route_hint": "ask_expert",
        "repair_hints": [
            _hint(
                "ask_human",
                "Ask the user to confirm that the selected nuclides/materials exist in the configured cross section library.",
                target_path="environment.OPENMC_CROSS_SECTIONS",
            )
        ],
    },
    "runtime.geometry_overlap": {
        "severity": "error",
        "message": "OpenMC reported a possible geometry overlap.",
        "schema_path": "runtime.geometry",
        "rule_id": "rule.runtime.geometry_overlap",
        "concept_id": "openmc.geometry.overlap",
        "knowledge_refs": [GEOMETRY_GUIDE, OPENMC_RUNTIME_GUIDE],
        "grep_patterns": ["overlap", "check_overlaps", "Geometry", "Cell", "Surface"],
        "requires_retrieval": True,
        "route_hint": "reflect_plan",
        "repair_hints": [
            _hint(
                "retrieve_docs",
                "Review OpenMC geometry overlap diagnostics, then adjust deterministic cell/surface regions rather than changing material facts.",
                target_path="complex_model.regions",
            )
        ],
    },
    "runtime.lost_particle": {
        "severity": "error",
        "message": "OpenMC reported lost particles.",
        "schema_path": "runtime.geometry",
        "rule_id": "rule.runtime.lost_particle",
        "concept_id": "openmc.geometry.lost_particle",
        "knowledge_refs": [GEOMETRY_GUIDE, OPENMC_RUNTIME_GUIDE],
        "grep_patterns": ["lost particle", "lost particles", "boundary_type", "region"],
        "requires_retrieval": True,
        "route_hint": "reflect_plan",
        "repair_hints": [
            _hint(
                "retrieve_docs",
                "Check cell containment, boundary surfaces, and missing outer regions before changing physical parameters.",
                target_path="complex_model.cells",
            )
        ],
    },
    "runtime.material_missing_nuclide_data": {
        "severity": "error",
        "message": "OpenMC reported material nuclide data missing from the configured library.",
        "schema_path": "runtime.materials",
        "rule_id": "rule.runtime.material_missing_nuclide_data",
        "concept_id": "openmc.material.nuclide_data",
        "knowledge_refs": [MATERIALS_GUIDE, CROSS_SECTIONS_GUIDE],
        "grep_patterns": ["not present in cross_sections.xml", "Could not find nuclide", "add_nuclide"],
        "requires_human_confirmation": True,
        "route_hint": "ask_expert",
        "repair_hints": [
            _hint(
                "ask_human",
                "Ask the user to confirm the nuclide name and whether the configured nuclear data library contains it.",
                target_path="complex_model.materials.composition",
            )
        ],
    },
    "runtime.dagmc_or_geometry_load_failed": {
        "severity": "error",
        "message": "OpenMC failed to load DAGMC or geometry input.",
        "schema_path": "runtime.geometry",
        "rule_id": "rule.runtime.geometry_load_failed",
        "concept_id": "openmc.geometry.load",
        "knowledge_refs": [GEOMETRY_GUIDE, OPENMC_RUNTIME_GUIDE],
        "grep_patterns": ["DAGMC", "geometry.xml", "failed to load", "Geometry"],
        "requires_retrieval": True,
        "route_hint": "manual_review",
        "repair_hints": [
            _hint(
                "retrieve_docs",
                "Inspect geometry export/load errors and verify referenced geometry files or XML artifacts exist.",
                target_path="runtime.geometry",
            )
        ],
    },
    "runtime.openmc_unknown_error": {
        "severity": "error",
        "message": "OpenMC reported an unknown runtime error.",
        "schema_path": "runtime",
        "rule_id": "rule.runtime.unknown_error",
        "concept_id": "openmc.runtime.diagnostics",
        "knowledge_refs": [OPENMC_RUNTIME_GUIDE],
        "grep_patterns": ["ERROR", "OpenMC", "Traceback", "runtime"],
        "requires_retrieval": True,
        "requires_human_confirmation": True,
        "route_hint": "manual_review",
        "repair_hints": [
            _hint(
                "retrieve_docs",
                "Use the raw stderr/stdout summary and stable code to investigate the OpenMC failure before attempting a patch.",
                target_path="runtime",
            )
        ],
    },
    # ------------------------------------------------------------ export XML
    "export_xml.dangling_cell_fill": {
        "severity": "error",
        "message": "geometry.xml cell fill references an unexported universe or lattice.",
        "schema_path": "geometry.xml.cell.fill",
        "rule_id": "rule.export_xml.cell_fill_ref_exists",
        "concept_id": "openmc.geometry.cell_fill",
        "knowledge_refs": [LATTICE_GUIDE],
        "grep_patterns": ["cell", "fill", "universe", "lattice", "geometry.xml"],
        "route_hint": "reflect_plan",
        "repair_hints": [
            _hint("edit_field", "Fix the cell fill id typo or define the referenced universe/lattice before export.")
        ],
    },
    "export_xml.dangling_lattice_universe": {
        "severity": "error",
        "message": "geometry.xml lattice universes reference an unexported universe.",
        "schema_path": "geometry.xml.lattice.universes",
        "rule_id": "rule.export_xml.lattice_universe_ref_exists",
        "concept_id": "openmc.geometry.lattice",
        "knowledge_refs": [LATTICE_GUIDE],
        "grep_patterns": ["lattice", "universes", "RectLattice", "HexLattice", "geometry.xml"],
        "route_hint": "reflect_plan",
        "repair_hints": [
            _hint("edit_field", "Fix the lattice universe id typo or define the referenced universe before export.")
        ],
    },
    "export_xml.dangling_lattice_outer_universe": {
        "severity": "error",
        "message": "geometry.xml lattice outer universe references an unexported universe.",
        "schema_path": "geometry.xml.lattice.outer",
        "rule_id": "rule.export_xml.lattice_outer_ref_exists",
        "concept_id": "openmc.geometry.lattice_outer",
        "knowledge_refs": [LATTICE_GUIDE],
        "grep_patterns": ["outer", "outer_universe", "lattice", "geometry.xml"],
        "route_hint": "reflect_plan",
        "repair_hints": [
            _hint("edit_field", "Fix lattice.outer / outer_universe_id or define the referenced outer universe.")
        ],
    },
    "export_xml.dangling_region_surface": {
        "severity": "error",
        "message": "geometry.xml region references an unexported surface.",
        "schema_path": "geometry.xml.cell.region",
        "rule_id": "rule.export_xml.region_surface_ref_exists",
        "concept_id": "openmc.geometry.region_surface_refs",
        "knowledge_refs": [GEOMETRY_GUIDE],
        "grep_patterns": ["region", "surface", "geometry.xml"],
        "route_hint": "reflect_plan",
        "repair_hints": [
            _hint("edit_field", "Fix the region expression/surface id typo or export the referenced surface.")
        ],
    },
    "export_xml.dangling_material_ref": {
        "severity": "error",
        "message": "geometry.xml cell material references an unexported material.",
        "schema_path": "geometry.xml.cell.material",
        "rule_id": "rule.export_xml.material_ref_exists",
        "concept_id": "openmc.material",
        "knowledge_refs": [MATERIALS_GUIDE],
        "grep_patterns": ["material", "cell", "geometry.xml", "materials.xml"],
        "route_hint": "reflect_plan",
        "repair_hints": [
            _hint("edit_field", "Fix the cell material id typo or export the referenced material in materials.xml.")
        ],
    },
    "export_xml.dangling_universe_cell": {
        "severity": "error",
        "message": "geometry.xml universe references an unexported cell.",
        "schema_path": "geometry.xml.universe.cell",
        "rule_id": "rule.export_xml.universe_cell_ref_exists",
        "concept_id": "openmc.geometry.universe",
        "knowledge_refs": [LATTICE_GUIDE],
        "grep_patterns": ["universe", "cell", "geometry.xml"],
        "route_hint": "reflect_plan",
        "repair_hints": [
            _hint("edit_field", "Fix the universe cell id typo or export the referenced cell.")
        ],
    },
    "export_xml.geometry_reference_unknown": {
        "severity": "error",
        "message": "geometry.xml has an unknown dangling geometry reference.",
        "schema_path": "geometry.xml",
        "rule_id": "rule.export_xml.geometry_reference_unknown",
        "concept_id": "openmc.geometry",
        "knowledge_refs": [GEOMETRY_GUIDE],
        "grep_patterns": ["geometry.xml", "cell", "lattice", "universe", "surface"],
        "route_hint": "manual_review",
        "repair_hints": [
            _hint("retrieve_docs", "Inspect the XML artifact and source model to classify the dangling reference before patching.")
        ],
    },
    # ----------------------------------------------------------- hex lattice
    "lattice.hex.renderer_unsupported": {
        "severity": "warning",
        "message": "hex lattice renderer is not implemented; plan remains review-only skeleton.",
        "schema_path": "complex_model.lattices",
        "rule_id": "rule.lattice.hex_renderer_unsupported",
        "concept_id": "openmc.geometry.hex_lattice",
        "knowledge_refs": [LATTICE_GUIDE],
        "grep_patterns": ["HexLattice", "hexagonal_prism", "LatticeSpec", "rings", "outer_universe_id"],
        "requires_retrieval": True,
        "route_hint": "capability_downgrade",
        "repair_hints": [
            _hint(
                "downgrade_renderability",
                "Keep renderability at skeleton until a HexAssemblyRenderer is implemented.",
                target_path="capability_report.renderability",
            )
        ],
    },
    "lattice.hex.ring_shape_invalid": {
        "severity": "error",
        "message": "hex lattice ring lengths are invalid.",
        "schema_path": "complex_model.lattices.rings",
        "rule_id": "rule.lattice.hex_ring_shape",
        "concept_id": "openmc.geometry.hex_lattice",
        "knowledge_refs": [LATTICE_GUIDE],
        "grep_patterns": ["HexLattice", "rings", "1, 6, 12", "LatticeSpec"],
        "requires_retrieval": True,
        "route_hint": "reflect_plan",
        "repair_hints": [
            _hint("edit_field", "Use center ring length 1 and outer ring lengths 6*n for ring index n.")
        ],
    },
    "lattice.hex.outer_universe_missing": {
        "severity": "warning",
        "message": "hex lattice outer_universe_id is missing.",
        "schema_path": "complex_model.lattices.outer_universe_id",
        "rule_id": "rule.lattice.hex_outer_universe",
        "concept_id": "openmc.geometry.hex_lattice",
        "knowledge_refs": [LATTICE_GUIDE],
        "grep_patterns": ["HexLattice", "outer", "outer_universe_id", "LatticeSpec"],
        "requires_retrieval": True,
        "route_hint": "reflect_plan",
        "repair_hints": [
            _hint("add_missing_field", "Set outer_universe_id when particles can leave the defined hex lattice rings.")
        ],
    },
    "lattice.hex.orientation_unverified": {
        "severity": "info",
        "message": "hex lattice orientation, pitch convention, or ring ordering is unverified.",
        "schema_path": "complex_model.lattices",
        "rule_id": "rule.lattice.hex_orientation_unverified",
        "concept_id": "openmc.geometry.hex_lattice",
        "knowledge_refs": [LATTICE_GUIDE],
        "grep_patterns": ["HexLattice", "orientation", "pitch", "rings", "outer_universe_id"],
        "requires_retrieval": True,
        "route_hint": "retrieval",
        "repair_hints": [
            _hint(
                "retrieve_docs",
                "Retrieve OpenMC HexLattice documentation for rings, pitch, outer universe, and orientation before renderer work.",
                target_path="complex_model.lattices",
            )
        ],
    },
}


def issue_from_catalog(code: str, **overrides: Any) -> ValidationIssue:
    """Build a :class:`ValidationIssue` from the catalog, with overrides.

    Unknown codes degrade gracefully: a minimal issue is built from ``code`` and
    any provided ``message`` so new validators can emit structured issues before
    their catalog entry exists.

    Recognized overrides: ``message``, ``schema_path``, ``rule_id``,
    ``concept_id``, ``severity``, ``requires_retrieval``,
    ``requires_human_confirmation``, ``grep_patterns``, and ``route_hint``.
    ``knowledge_refs`` / ``repair_hints`` may be supplied as lists of dicts or
    model instances and are merged on top of the catalog defaults.
    """
    entry = ERROR_CATALOG.get(code, {})
    severity = overrides.pop("severity", entry.get("severity", "error"))
    message = overrides.pop("message", entry.get("message", code))
    schema_path = overrides.pop("schema_path", entry.get("schema_path"))
    rule_id = overrides.pop("rule_id", entry.get("rule_id"))
    concept_id = overrides.pop("concept_id", entry.get("concept_id"))
    requires_retrieval = overrides.pop(
        "requires_retrieval", entry.get("requires_retrieval", False)
    )
    requires_human_confirmation = overrides.pop(
        "requires_human_confirmation", entry.get("requires_human_confirmation", False)
    )
    route_hint = overrides.pop("route_hint", entry.get("route_hint"))
    grep_patterns = list(entry.get("grep_patterns", []))
    extra_grep_patterns = overrides.pop("grep_patterns", None)
    if extra_grep_patterns:
        grep_patterns.extend(str(pattern) for pattern in extra_grep_patterns)
    grep_patterns = list(dict.fromkeys(pattern for pattern in grep_patterns if pattern))

    knowledge_refs = list(entry.get("knowledge_refs", []))
    extra_refs = overrides.pop("knowledge_refs", None)
    if extra_refs:
        knowledge_refs.extend(extra_refs)

    repair_hints = list(entry.get("repair_hints", []))
    extra_hints = overrides.pop("repair_hints", None)
    if extra_hints:
        repair_hints.extend(extra_hints)

    if overrides:
        # Ignore unexpected keys silently rather than crashing callers, but keep
        # the most common typo surface visible during development via repr.
        unknown = ", ".join(sorted(overrides))
        raise TypeError(f"issue_from_catalog({code!r}) got unexpected overrides: {unknown}")

    return ValidationIssue(
        severity=severity,  # type: ignore[arg-type]
        code=code,
        message=message,
        schema_path=schema_path,
        rule_id=rule_id,
        concept_id=concept_id,
        knowledge_refs=knowledge_refs,
        repair_hints=repair_hints,
        grep_patterns=grep_patterns,
        requires_retrieval=requires_retrieval,
        requires_human_confirmation=requires_human_confirmation,
        route_hint=route_hint,
    )


def add_issue(
    issues: list[ValidationIssue],
    code: str,
    message: str | None = None,
    **overrides: Any,
) -> ValidationIssue:
    """Append a catalog-built issue to ``issues`` and return it."""
    if message is not None:
        overrides["message"] = message
    issue = issue_from_catalog(code, **overrides)
    issues.append(issue)
    return issue
