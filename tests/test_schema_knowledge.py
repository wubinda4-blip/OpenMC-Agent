"""Tests for schema knowledge metadata, structured validation issues, and the
schema knowledge index exporter.

These also exercise the repeated-geometry constructs (cells -> universes ->
lattices) heavily, because the next milestone targets a larger 3D full-core
model that must be built from repeated geometry.
"""

import pytest
from pydantic import BaseModel

from openmc_agent.error_catalog import ERROR_CATALOG, issue_from_catalog
from openmc_agent.schemas import (
    GeometrySpec,
    KnowledgeField,
    KnowledgeRef,
    LatticeSpec,
    MaterialSpec,
    NuclideSpec,
    PinCellSpec,
    RepairHint,
    RunSettingsSpec,
    SettingsSpec,
    SimulationPlan,
    SimulationSpec,
    ValidationIssue,
    ValidationReport,
)
from openmc_agent.schema_knowledge import export_schema_knowledge_index
from openmc_agent.validator import validate_simulation_plan, validate_simulation_spec


def _pin_cell_spec() -> SimulationSpec:
    fuel = MaterialSpec(
        name="UO2 fuel",
        density_unit="g/cm3",
        density_value=10.4,
        composition=[
            NuclideSpec(name="U235", percent=4.95),
            NuclideSpec(name="U238", percent=95.05),
            NuclideSpec(name="O16", percent=200.0),
        ],
    )
    moderator = MaterialSpec(
        name="Water moderator",
        density_unit="g/cm3",
        density_value=1.0,
        composition=[
            NuclideSpec(name="H1", percent=2.0),
            NuclideSpec(name="O16", percent=1.0),
        ],
    )
    return SimulationSpec(
        name="UO2 pin-cell criticality",
        pin_cell=PinCellSpec(
            fuel=fuel,
            moderator=moderator,
            geometry=GeometrySpec(fuel_radius_cm=0.41, pitch_cm=1.26),
        ),
        settings=SettingsSpec(batches=50, inactive=10, particles=1000),
    )


# --------------------------------------------------------------------- task 1
def test_validation_report_legacy_construction_still_works() -> None:
    """Existing callers passing errors/warnings directly must keep working."""
    report = ValidationReport(is_valid=False, errors=["boom"], warnings=["w"])

    assert report.is_valid is False
    assert report.errors == ["boom"]
    assert report.warnings == ["w"]
    assert report.issues == []


def test_validation_report_from_issues_derives_legacy_fields() -> None:
    issue = issue_from_catalog("geometry.fuel_radius.too_large_for_pitch")
    report = ValidationReport.from_issues([issue])

    assert report.is_valid is False
    # legacy error text is derived from the issue message
    assert report.errors == [issue.message]
    # suggestions are derived from repair-hint messages
    assert report.suggestions == [hint.message for hint in issue.repair_hints]
    assert report.issues == [issue]


def test_validation_report_warning_does_not_invalidate() -> None:
    issue = issue_from_catalog("plan.complex_model.non_executable")
    assert issue.severity == "warning"
    report = ValidationReport.from_issues([issue])

    assert report.is_valid is True
    assert report.errors == []
    assert report.warnings == [issue.message]


# --------------------------------------------------------------------- task 2
def test_knowledge_field_writes_concept_id_into_json_schema() -> None:
    class Sample(BaseModel):
        radius: float = KnowledgeField(
            default=0.0,
            gt=0,
            concept_id="openmc.geometry.pin_cell_radius",
            doc_refs=["openmc.usersguide.geometry"],
            retrieval_queries=["OpenMC pin cell radius"],
            common_errors=["radius >= pitch/2"],
        )

    extra = Sample.model_fields["radius"].json_schema_extra
    assert isinstance(extra, dict)
    assert extra["concept_id"] == "openmc.geometry.pin_cell_radius"
    assert extra["doc_refs"] == ["openmc.usersguide.geometry"]

    schema = Sample.model_json_schema()
    assert schema["properties"]["radius"]["concept_id"] == "openmc.geometry.pin_cell_radius"


def test_knowledge_field_merges_existing_json_schema_extra() -> None:
    class Sample(BaseModel):
        radius: float = KnowledgeField(
            default=0.0,
            concept_id="openmc.geometry.pin_cell_radius",
            json_schema_extra={"units": "cm"},
        )

    extra = Sample.model_fields["radius"].json_schema_extra
    assert isinstance(extra, dict)
    assert extra["concept_id"] == "openmc.geometry.pin_cell_radius"
    assert extra["units"] == "cm"  # caller-provided extra preserved, not overwritten


# --------------------------------------------------------------------- task 5
def test_validate_simulation_spec_valid_pin_cell_has_no_issues() -> None:
    report = validate_simulation_spec(_pin_cell_spec())

    assert report.is_valid is True
    assert report.errors == []
    assert report.issues == []


def test_validate_simulation_spec_returns_structured_issue_for_fuel_radius() -> None:
    spec = _pin_cell_spec()
    spec.pin_cell.geometry = GeometrySpec.model_construct(
        fuel_radius_cm=10.0,
        pitch_cm=1.26,
        clad_inner_radius_cm=None,
        clad_outer_radius_cm=None,
    )

    report = validate_simulation_spec(spec)

    assert report.is_valid is False
    # legacy message text preserved for backward compatibility
    assert any("fuel_radius_cm" in error and "10.0" in error for error in report.errors)

    codes = [issue.code for issue in report.issues]
    assert "geometry.fuel_radius.too_large_for_pitch" in codes

    issue = next(
        issue for issue in report.issues if issue.code == "geometry.fuel_radius.too_large_for_pitch"
    )
    assert issue.severity == "error"
    assert issue.concept_id == "openmc.geometry.pin_cell_radius"
    assert issue.rule_id == "rule.geometry.pin_cell.fuel_radius_lt_half_pitch"
    assert len(issue.repair_hints) >= 1
    # knowledge refs give the future retrieval layer a handle
    assert issue.knowledge_refs
    assert any(ref.source_type == "openmc_docs" for ref in issue.knowledge_refs)


def test_validate_simulation_spec_inactive_issue_has_doc_refs_and_queries() -> None:
    spec = _pin_cell_spec()
    # Bypass the schema's own model validator (as a repair loop would) so the
    # cross-field check is exercised here.
    spec.settings = RunSettingsSpec.model_construct(batches=5, inactive=5, particles=100)

    report = validate_simulation_spec(spec)

    assert report.is_valid is False
    issue = next(
        issue for issue in report.issues if issue.code == "settings.inactive.not_less_than_batches"
    )
    assert issue.concept_id == "openmc.settings.inactive"
    assert issue.repair_hints
    queries = [ref.retrieval_query for ref in issue.knowledge_refs if ref.retrieval_query]
    assert queries  # something for a future retrieval step to search on


# --------------------------------------------------------------------- task 6
def test_export_schema_knowledge_index_covers_key_fields() -> None:
    index = export_schema_knowledge_index(SimulationPlan)

    fuel = index["SimulationPlan.model_spec.pin_cell.geometry.fuel_radius_cm"]
    assert fuel["concept_id"] == "openmc.geometry.pin_cell_radius"
    for key in ("doc_refs", "retrieval_queries", "common_errors"):
        assert fuel[key], f"{key} should be populated for fuel_radius_cm"

    density = index["SimulationPlan.model_spec.pin_cell.fuel.density_unit"]
    assert density["concept_id"] == "openmc.material.density_unit"

    batches_paths = [path for path in index if path.endswith(".settings.batches")]
    assert batches_paths  # RunSettingsSpec is reachable under multiple parents
    for path in batches_paths:
        assert index[path]["concept_id"] == "openmc.settings.batches"


# --------------------------------------------- repeated-geometry emphasis
def test_export_index_covers_repeated_geometry_concepts() -> None:
    """The next milestone builds a 3D full core from repeated geometry, so the
    lattice/universe/cell concepts must be present and addressable in the index."""
    index = export_schema_knowledge_index(SimulationPlan)
    concepts = {entry.get("concept_id") for entry in index.values()}

    assert "openmc.geometry.lattice" in concepts
    assert "openmc.geometry.rect_lattice" in concepts
    assert "openmc.geometry.hex_lattice" in concepts
    assert "openmc.geometry.lattice_pitch" in concepts
    assert "openmc.geometry.cell_fill" in concepts
    assert "openmc.geometry.universe" in concepts

    # Lattice fields must carry actionable doc pointers and retrieval queries.
    rect_paths = [path for path in index if path.endswith(".lattices.universe_pattern")]
    assert rect_paths
    rect = index[rect_paths[0]]
    assert rect["concept_id"] == "openmc.geometry.rect_lattice"
    assert rect["doc_refs"]
    assert rect["retrieval_queries"]


def test_lattice_spec_metadata_round_trips_through_json_schema() -> None:
    schema = LatticeSpec.model_json_schema()
    kind = schema["properties"]["kind"]
    assert kind["concept_id"] == "openmc.geometry.lattice"
    pattern = schema["properties"]["universe_pattern"]
    assert pattern["concept_id"] == "openmc.geometry.rect_lattice"
    assert pattern["common_errors"]


def test_rect_lattice_repeated_geometry_validates_cleanly() -> None:
    """A minimal pin -> universe -> rect-lattice IR must validate without errors."""
    from openmc_agent.schemas import (
        AssemblySpec,
        CellSpec,
        ComplexMaterialSpec,
        ComplexModelSpec,
        PlotSpec,
        RenderCapabilityReport,
        UniverseSpec,
    )

    plan = SimulationPlan(
        schema_version="simulation_plan.v2",
        model_spec=None,
        complex_model=ComplexModelSpec(
            name="2x2 repeated lattice",
            kind="assembly",
            materials=[
                ComplexMaterialSpec(
                    id="fuel",
                    name="fuel",
                    chemical_formula="UO2",
                    requires_human_confirmation=["density"],
                )
            ],
            cells=[
                CellSpec(id="fuel_cell", name="fuel", fill_type="material", fill_id="fuel"),
            ],
            universes=[UniverseSpec(id="pin", name="pin", cell_ids=["fuel_cell"])],
            lattices=[
                LatticeSpec(
                    id="assembly_lattice",
                    name="2x2 lattice",
                    kind="rect",
                    pitch_cm=(1.26, 1.26),
                    universe_pattern=[["pin", "pin"], ["pin", "pin"]],
                )
            ],
            assemblies=[
                AssemblySpec(id="assembly", name="assembly", lattice_id="assembly_lattice"),
            ],
        ),
        capability_report=RenderCapabilityReport(
            is_executable=False,
            supported_renderer="none",
        ),
        plot_specs=[PlotSpec(basis="xy", width_cm=(2.52, 2.52), filename="assembly_xy.png")],
    )

    report = validate_simulation_plan(plan)

    assert report.is_valid is True
    assert [issue for issue in report.issues if issue.severity == "error"] == []


def test_three_level_repeated_geometry_core_ir_validates() -> None:
    """Lock in the pin -> universe -> rect lattice -> core-root-cell fill chain
    that a 3D full-core model relies on."""
    from openmc_agent.schemas import (
        ComplexMaterialSpec,
        ComplexModelSpec,
        CoreSpec,
        CellSpec,
        PlotSpec,
        RenderCapabilityReport,
        UniverseSpec,
    )

    plan = SimulationPlan(
        schema_version="simulation_plan.v2",
        model_spec=None,
        complex_model=ComplexModelSpec(
            name="mini core",
            kind="core",
            materials=[
                ComplexMaterialSpec(
                    id="fuel",
                    name="fuel",
                    chemical_formula="UO2",
                    requires_human_confirmation=["density"],
                )
            ],
            cells=[
                CellSpec(id="pin_cell", name="pin", fill_type="material", fill_id="fuel"),
                # Root cell fills the assembly lattice -- the core fill entry point.
                CellSpec(id="root", name="root", fill_type="lattice", fill_id="assembly_lattice"),
            ],
            universes=[UniverseSpec(id="pin_u", name="pin", cell_ids=["pin_cell"])],
            lattices=[
                LatticeSpec(
                    id="assembly_lattice",
                    name="3x3",
                    kind="rect",
                    pitch_cm=(1.26, 1.26),
                    universe_pattern=[["pin_u"] * 3 for _ in range(3)],
                )
            ],
            core=CoreSpec(id="core", name="core", lattice_id="assembly_lattice"),
        ),
        capability_report=RenderCapabilityReport(
            is_executable=False,
            supported_renderer="none",
            unsupported_subsystems=["core"],
        ),
        plot_specs=[PlotSpec(basis="xy", width_cm=(4.0, 4.0), filename="core_xy.png")],
    )

    report = validate_simulation_plan(plan)

    assert report.is_valid is True
    assert [issue for issue in report.issues if issue.severity == "error"] == []


def test_hex_lattice_repeated_geometry_ir_validates() -> None:
    """HexLattice rings (1, 6, 12, ...) are the HTGR/prismatic core layout."""
    lattice = LatticeSpec(
        id="hex",
        name="hex",
        kind="hex",
        pitch_cm=(1.5, 1.5),
        rings=[["center"], ["a", "b", "c", "d", "e", "f"]],
    )
    assert [len(ring) for ring in lattice.rings] == [1, 6]

    # concept id preserved through the schema for the hex path
    schema = LatticeSpec.model_json_schema()
    assert schema["properties"]["rings"]["concept_id"] == "openmc.geometry.hex_lattice"
    assert schema["properties"]["kind"]["concept_id"] == "openmc.geometry.lattice"


def test_unknown_catalog_code_falls_back_to_minimal_issue() -> None:
    """New validators can emit issues before a catalog entry exists."""
    issue = issue_from_catalog("future.not_yet_cataloged", message="placeholder")

    assert issue.code == "future.not_yet_cataloged"
    assert issue.message == "placeholder"
    assert issue.severity == "error"


def test_catalog_routes_core_axial_and_loading_refs_to_agent_repair() -> None:
    """Reference-missing issues are agent-fixable (auto_repair when a
    deterministic id fix exists, reflect_plan when the LLM must add a missing
    definition). None should require human confirmation."""
    # Deterministic id-typo fixes -> auto_repair.
    for code in (
        "core.lattice_ref_missing",
        "axial_layer.fill_ref_missing",
        "axial_layer.loading_ref_missing",
        "lattice_loading.base_ref_missing",
        "lattice_loading.override_universe_ref_missing",
    ):
        issue = issue_from_catalog(code)
        assert issue.route_hint == "auto_repair", code
        assert issue.requires_human_confirmation is False, code
    # A lattice referencing an undefined universe needs the LLM to emit the
    # missing UniverseSpec (cell grouping cannot be auto-invented) -> reflect_plan.
    issue = issue_from_catalog("lattice.universe_ref_missing")
    assert issue.route_hint == "reflect_plan"
    assert issue.requires_human_confirmation is False


def test_catalog_covers_all_required_validator_codes() -> None:
    """Guardrail: every code the validator emits must resolve in the catalog."""
    required = {
        "geometry.fuel_radius.out_of_range",
        "geometry.pitch.out_of_range",
        "geometry.fuel_radius.too_large_for_pitch",
        "geometry.cladding.radii_partial_missing",
        "geometry.cladding.inner_not_greater_than_fuel",
        "geometry.cladding.outer_not_greater_than_inner",
        "geometry.cladding.outer_too_large_for_pitch",
        "geometry.cladding.material_missing_for_radii",
        "geometry.cladding.radii_missing_for_material",
        "settings.inactive.not_less_than_batches",
        "plan.model.missing",
        "plan.complex_model.non_executable",
        "plan.executable.unsupported_renderer",
        "script.missing_structure",
        "script.material_not_referenced",
    }
    missing = required - set(ERROR_CATALOG)
    assert not missing, f"catalog missing codes: {sorted(missing)}"


def test_catalog_entries_have_knowledge_refs_and_repair_hints() -> None:
    """Every catalog entry should carry at least one doc ref and one repair hint
    so the future retrieval + self-repair loop has stable hooks."""
    for code, entry in ERROR_CATALOG.items():
        assert entry["knowledge_refs"], f"{code} has no knowledge_refs"
        assert entry["repair_hints"], f"{code} has no repair_hints"
        for ref in entry["knowledge_refs"]:
            assert isinstance(ref, KnowledgeRef)
        for hint in entry["repair_hints"]:
            assert isinstance(hint, RepairHint)
