from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


Renderability = Literal["none", "skeleton", "exportable", "runnable"]
"""How far a renderer can take a SimulationPlan.

- ``none``: cannot understand the IR, no model.py is produced.
- ``skeleton``: produces a review-only ``model.py`` skeleton, but cannot export XML.
- ``exportable``: produces ``model.py`` and exports XML, but does not run OpenMC.
- ``runnable``: produces ``model.py``, exports XML, and runs a low-cost smoke test.
"""

RENDERABILITY_RANK: dict[str, int] = {
    "none": 0,
    "skeleton": 1,
    "exportable": 2,
    "runnable": 3,
}


class AgentBaseModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class NuclideSpec(AgentBaseModel):
    name: str = Field(
        min_length=1,
        description="OpenMC nuclide or element name, such as U235, O16, H1, or Zr.",
    )
    percent: float = Field(gt=0, description="Component amount passed to OpenMC.")
    percent_type: Literal["ao", "wo"] = Field(
        default="ao",
        description="OpenMC composition basis: atom percent (ao) or weight percent (wo).",
    )
    kind: Literal["nuclide", "element"] = Field(
        default="nuclide",
        description="Use element for elemental composition and nuclide for isotope-specific composition.",
    )


class MaterialSpec(AgentBaseModel):
    name: str = Field(min_length=1, description="Human-readable material name.")
    density_unit: Literal["g/cm3", "kg/m3", "atom/b-cm"] = Field(
        description="Density unit accepted by openmc.Material.set_density."
    )
    density_value: float = Field(gt=0, description="Positive material density value.")
    composition: list[NuclideSpec] = Field(
        min_length=1,
        description="Nuclide or element composition entries for this material.",
    )
    temperature_k: float | None = Field(
        default=None,
        gt=0,
        description="Optional material temperature in kelvin.",
    )
    sab: list[str] = Field(
        default_factory=list,
        description="Optional thermal scattering names, such as c_H_in_H2O.",
    )
    chemical_formula: str | None = Field(
        default=None,
        description="Optional chemical formula when the model should use OpenMC material helpers.",
    )
    enrichment_percent: float | None = Field(
        default=None,
        gt=0,
        description="Optional enrichment percentage for enriched elemental materials.",
    )
    enrichment_target: str | None = Field(
        default=None,
        description="Optional enriched nuclide target, such as U235.",
    )
    depletable: bool = Field(
        default=False,
        description="Whether this material should be marked depletable in OpenMC.",
    )
    volume_cm3: float | None = Field(
        default=None,
        gt=0,
        description="Optional material volume used by depletion or normalization workflows.",
    )
    source: str | None = Field(
        default=None,
        description="Source of material data, such as user input, handbook, or placeholder.",
    )
    assumptions: list[str] = Field(
        default_factory=list,
        description="Assumptions attached to the material definition.",
    )
    requires_human_confirmation: list[str] = Field(
        default_factory=list,
        description="Material fields that must be confirmed by a human expert.",
    )


class ComplexMaterialSpec(AgentBaseModel):
    id: str = Field(min_length=1, description="Stable material identifier used by IR cells.")
    name: str = Field(min_length=1, description="Human-readable material name.")
    density_unit: Literal["g/cm3", "kg/m3", "atom/b-cm", "sum"] | None = Field(
        default=None,
        description="OpenMC density unit when known. Use null when the source did not specify it.",
    )
    density_value: float | None = Field(
        default=None,
        gt=0,
        description="Positive material density value when known.",
    )
    composition: list[NuclideSpec] = Field(
        default_factory=list,
        description="Nuclide or element composition entries when known.",
    )
    chemical_formula: str | None = Field(
        default=None,
        description="Chemical formula or shorthand, such as UO2, SiC, graphite, or H2O.",
    )
    enrichment_percent: float | None = Field(default=None, gt=0)
    enrichment_target: str | None = Field(default=None)
    enrichment_type: Literal["ao", "wo"] | None = Field(default=None)
    temperature_k: float | None = Field(default=None, gt=0)
    sab: list[str] = Field(default_factory=list)
    depletable: bool = False
    volume_cm3: float | None = Field(default=None, gt=0)
    source: str | None = None
    assumptions: list[str] = Field(default_factory=list)
    requires_human_confirmation: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_material_has_source_or_uncertainty(self) -> "ComplexMaterialSpec":
        has_material_definition = bool(self.composition or self.chemical_formula)
        if not has_material_definition and not self.requires_human_confirmation:
            raise ValueError(
                "complex material needs composition, chemical_formula, or requires_human_confirmation"
            )
        # A partial density (only one of unit/value) is allowed when the material
        # is explicitly pending human confirmation -- e.g. a candidate burnable-
        # poison material whose density the source document did not give. The LLM
        # often knows the unit (or an estimate) but not the value. The capability
        # layer decides whether the gap blocks based on whether the material is
        # actually used by the default model (reachability), not here.
        if (self.density_unit is None) != (self.density_value is None):
            if not self.requires_human_confirmation:
                raise ValueError("density_unit and density_value must be provided together")
        return self


class SurfaceSpec(AgentBaseModel):
    id: str = Field(min_length=1, description="Stable surface identifier.")
    kind: Literal[
        "xplane",
        "yplane",
        "zplane",
        "plane",
        "zcylinder",
        "ycylinder",
        "xcylinder",
        "sphere",
        "rectangular_prism",
        "hexagonal_prism",
    ] = Field(description="OpenMC surface or composite-surface family.")
    parameters: dict[str, Any] = Field(
        default_factory=dict,
        description="OpenMC constructor parameters, such as r, x0, y0, z0, pitch, or orientation.",
    )
    boundary_type: Literal["transmission", "vacuum", "reflective", "periodic", "white"] | None = Field(
        default=None,
        description="Optional OpenMC boundary_type for outer surfaces.",
    )
    purpose: str = Field(default="", description="Why this surface is needed.")


class RegionSpec(AgentBaseModel):
    id: str = Field(min_length=1, description="Stable region identifier.")
    expression: str = Field(
        min_length=1,
        description="OpenMC boolean region expression using surface ids, such as -fuel & +xmin.",
    )
    surface_ids: list[str] = Field(
        default_factory=list,
        description="Surface ids referenced by the expression for validation and traceability.",
    )
    purpose: str = Field(default="", description="Geometry role of this region.")


class CellSpec(AgentBaseModel):
    id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    region_id: str | None = Field(default=None)
    fill_type: Literal["material", "universe", "lattice", "void"] = Field(default="material")
    fill_id: str | None = Field(default=None, description="Referenced material, universe, or lattice id.")
    temperature_k: float | None = Field(default=None, gt=0)
    purpose: str = ""

    @model_validator(mode="after")
    def validate_fill_reference(self) -> "CellSpec":
        if self.fill_type != "void" and not self.fill_id:
            raise ValueError("fill_id is required unless fill_type is void")
        return self


class UniverseSpec(AgentBaseModel):
    id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    cell_ids: list[str] = Field(default_factory=list)
    purpose: str = ""


class LatticeSpec(AgentBaseModel):
    id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    kind: Literal["rect", "hex"] = Field(description="OpenMC RectLattice or HexLattice.")
    pitch_cm: tuple[float, ...] = Field(min_length=1)
    lower_left_cm: tuple[float, ...] | None = None
    center_cm: tuple[float, ...] | None = None
    shape: tuple[int, ...] | None = None
    outer_universe_id: str | None = None
    universe_pattern: list[list[str]] = Field(
        default_factory=list,
        description="Rectangular lattice universe ids by row/column.",
    )
    rings: list[list[str]] = Field(
        default_factory=list,
        description="Hexagonal lattice universe ids by ring.",
    )
    purpose: str = ""

    @field_validator("rings", "universe_pattern", mode="before")
    @classmethod
    def _coerce_none_to_empty_list(cls, value: Any) -> Any:
        """Tolerate LLMs that emit ``null`` for optional list fields."""
        if value is None:
            return []
        return value

    @model_validator(mode="after")
    def validate_lattice_definition(self) -> "LatticeSpec":
        if any(pitch <= 0 for pitch in self.pitch_cm):
            raise ValueError("lattice pitch_cm values must be positive")
        if self.kind == "rect" and not self.universe_pattern:
            raise ValueError("rect lattice requires universe_pattern")
        if self.kind == "hex" and not self.rings:
            raise ValueError("hex lattice requires rings")
        return self


class AssemblySpec(AgentBaseModel):
    id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    lattice_id: str | None = None
    pitch_cm: float | None = Field(default=None, gt=0)
    boundary: Literal["reflective", "vacuum", "transmission", "periodic"] | None = None
    purpose: str = ""


class ReflectorSpec(AgentBaseModel):
    id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    material_id: str
    region_id: str | None = None
    location: Literal["radial", "axial", "bottom", "top", "mixed"] = "radial"
    purpose: str = ""


class ControlRodSpec(AgentBaseModel):
    id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    absorber_material_id: str
    guide_tube_region_id: str | None = None
    insertion_depth_cm: float | None = Field(default=None, ge=0)
    position_ids: list[str] = Field(default_factory=list)
    state: Literal["inserted", "withdrawn", "partially_inserted", "unknown"] = "unknown"
    purpose: str = ""


class CoreSpec(AgentBaseModel):
    id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    lattice_id: str | None = None
    assembly_ids: list[str] = Field(default_factory=list)
    reflector_ids: list[str] = Field(default_factory=list)
    control_rod_ids: list[str] = Field(default_factory=list)
    boundary: Literal["vacuum", "reflective", "periodic", "mixed", "unknown"] = "unknown"
    symmetry: str | None = None
    purpose: str = ""


class TRISOLayerSpec(AgentBaseModel):
    name: str = Field(min_length=1)
    material_id: str = Field(min_length=1)
    outer_radius_cm: float = Field(gt=0)
    purpose: str = ""


class TRISOSpec(AgentBaseModel):
    id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    layers: list[TRISOLayerSpec] = Field(min_length=1)
    matrix_material_id: str | None = None
    packing_fraction: float | None = Field(default=None, gt=0, lt=1)
    container_region_id: str | None = None
    packing_algorithm: Literal["pack_spheres", "explicit_centers", "homogenized", "unknown"] = "unknown"
    assumptions: list[str] = Field(default_factory=list)
    requires_human_confirmation: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_layer_ordering(self) -> "TRISOSpec":
        radii = [layer.outer_radius_cm for layer in self.layers]
        if radii != sorted(radii) or len(set(radii)) != len(radii):
            raise ValueError("TRISO layer outer_radius_cm values must be strictly increasing")
        return self


class PackedSphereSpec(AgentBaseModel):
    id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    sphere_radius_cm: float = Field(gt=0)
    container_region_id: str
    material_id: str | None = None
    packing_fraction: float | None = Field(default=None, gt=0, lt=1)
    num_spheres: int | None = Field(default=None, gt=0)
    seed: int | None = Field(default=None, ge=1)
    purpose: str = ""


class PebbleSpec(AgentBaseModel):
    id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    outer_radius_cm: float = Field(gt=0)
    fuel_zone_radius_cm: float | None = Field(default=None, gt=0)
    matrix_material_id: str | None = None
    triso_spec_id: str | None = None
    moderator_material_id: str | None = None
    packing_fraction: float | None = Field(default=None, gt=0, lt=1)
    assumptions: list[str] = Field(default_factory=list)
    requires_human_confirmation: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_fuel_zone(self) -> "PebbleSpec":
        if self.fuel_zone_radius_cm is not None and self.fuel_zone_radius_cm >= self.outer_radius_cm:
            raise ValueError("fuel_zone_radius_cm must be less than outer_radius_cm")
        return self


class ValidationReport(AgentBaseModel):
    is_valid: bool
    errors: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    suggestions: list[str] = Field(default_factory=list)


class GeometrySpec(AgentBaseModel):
    fuel_radius_cm: float = Field(gt=0, le=2.0)
    pitch_cm: float = Field(gt=0, le=5.0)
    clad_inner_radius_cm: float | None = Field(default=None, gt=0, le=2.5)
    clad_outer_radius_cm: float | None = Field(default=None, gt=0, le=2.5)

    @model_validator(mode="after")
    def validate_radius_ordering(self) -> "GeometrySpec":
        if self.fuel_radius_cm >= self.pitch_cm / 2:
            raise ValueError("fuel_radius_cm must be less than half the pitch_cm")

        if self.clad_inner_radius_cm is None and self.clad_outer_radius_cm is None:
            return self

        if self.clad_inner_radius_cm is None or self.clad_outer_radius_cm is None:
            raise ValueError(
                "clad_inner_radius_cm and clad_outer_radius_cm must be provided together"
            )

        if self.clad_inner_radius_cm <= self.fuel_radius_cm:
            raise ValueError("clad_inner_radius_cm must exceed fuel_radius_cm")

        if self.clad_outer_radius_cm <= self.clad_inner_radius_cm:
            raise ValueError(
                "clad_outer_radius_cm must exceed clad_inner_radius_cm"
            )

        if self.clad_outer_radius_cm >= self.pitch_cm / 2:
            raise ValueError("clad_outer_radius_cm must be less than half the pitch_cm")

        return self


class PinCellSpec(AgentBaseModel):
    fuel: MaterialSpec
    moderator: MaterialSpec
    geometry: GeometrySpec
    cladding: MaterialSpec | None = None

    @model_validator(mode="after")
    def validate_cladding_matches_geometry(self) -> "PinCellSpec":
        has_cladding_geometry = self.geometry.clad_outer_radius_cm is not None
        if self.cladding is not None and not has_cladding_geometry:
            raise ValueError("cladding material requires cladding radii in geometry")
        if self.cladding is None and has_cladding_geometry:
            raise ValueError("cladding radii require a cladding material")
        return self


class RunSettingsSpec(AgentBaseModel):
    run_mode: Literal["eigenvalue"] = Field(
        default="eigenvalue",
        description="OpenMC run mode. This phase only supports eigenvalue models.",
    )
    batches: int = Field(
        default=50,
        ge=1,
        description="Number of OpenMC batches or generations used for this run.",
    )
    inactive: int = Field(
        default=10,
        ge=0,
        description="Inactive batches for eigenvalue source convergence.",
    )
    particles: int = Field(
        default=1000,
        ge=1,
        description="Particles per batch or generation.",
    )
    seed: int | None = Field(
        default=None,
        ge=1,
        description="Optional deterministic random seed for reproducible checks.",
    )

    @model_validator(mode="after")
    def validate_inactive_batches(self) -> "RunSettingsSpec":
        if self.inactive >= self.batches:
            raise ValueError("inactive must be less than batches")
        return self


class SettingsSpec(RunSettingsSpec):
    """Backward-compatible settings model used by the first implementation."""


class ComplexModelSpec(AgentBaseModel):
    name: str = Field(min_length=1)
    kind: Literal[
        "pin_cell",
        "assembly",
        "core",
        "reflector",
        "control_rod",
        "triso_compact",
        "pebble",
        "pebble_bed",
        "mixed",
    ] = "mixed"
    materials: list[ComplexMaterialSpec] = Field(default_factory=list)
    surfaces: list[SurfaceSpec] = Field(default_factory=list)
    regions: list[RegionSpec] = Field(default_factory=list)
    cells: list[CellSpec] = Field(default_factory=list)
    universes: list[UniverseSpec] = Field(default_factory=list)
    lattices: list[LatticeSpec] = Field(default_factory=list)
    assemblies: list[AssemblySpec] = Field(default_factory=list)
    core: CoreSpec | None = None
    reflectors: list[ReflectorSpec] = Field(default_factory=list)
    control_rods: list[ControlRodSpec] = Field(default_factory=list)
    trisos: list[TRISOSpec] = Field(default_factory=list)
    packed_spheres: list[PackedSphereSpec] = Field(default_factory=list)
    pebbles: list[PebbleSpec] = Field(default_factory=list)
    settings: RunSettingsSpec = Field(default_factory=RunSettingsSpec)
    assumptions: list[str] = Field(default_factory=list)
    requires_human_confirmation: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_complex_model_has_content(self) -> "ComplexModelSpec":
        content_count = sum(
            bool(value)
            for value in (
                self.materials,
                self.surfaces,
                self.regions,
                self.cells,
                self.universes,
                self.lattices,
                self.assemblies,
                self.core,
                self.reflectors,
                self.control_rods,
                self.trisos,
                self.packed_spheres,
                self.pebbles,
            )
        )
        if content_count == 0:
            raise ValueError("complex model needs at least one structured subsystem")
        return self

    @model_validator(mode="after")
    def infer_single_assembly_root(self) -> "ComplexModelSpec":
        """Recover a missing AssemblySpec when the root lattice is unambiguous."""
        if self.kind != "assembly" or self.assemblies:
            return self

        lattice_id = self._infer_assembly_lattice_id()
        if lattice_id is None:
            return self

        lattice = next(lattice for lattice in self.lattices if lattice.id == lattice_id)
        pitch_cm = (
            lattice.pitch_cm[0]
            if all(value == lattice.pitch_cm[0] for value in lattice.pitch_cm)
            else None
        )
        object.__setattr__(
            self,
            "assemblies",
            [
                AssemblySpec(
                    id="assembly",
                    name="assembly",
                    lattice_id=lattice_id,
                    pitch_cm=pitch_cm,
                    purpose="Inferred from the assembly lattice root.",
                )
            ],
        )
        return self

    def _infer_assembly_lattice_id(self) -> str | None:
        rect_lattice_ids = [
            lattice.id for lattice in self.lattices if lattice.kind == "rect"
        ]
        if not rect_lattice_ids:
            return None
        rect_lattice_id_set = set(rect_lattice_ids)

        root_fill_ids: list[str] = []
        all_fill_ids: list[str] = []
        for cell in self.cells:
            if cell.fill_type != "lattice" or cell.fill_id not in rect_lattice_id_set:
                continue
            all_fill_ids.append(cell.fill_id)
            marker = f"{cell.id} {cell.name} {cell.purpose}".lower()
            if "assembly" in marker or "root" in marker:
                root_fill_ids.append(cell.fill_id)

        unique_root_fill_ids = list(dict.fromkeys(root_fill_ids))
        if len(unique_root_fill_ids) == 1:
            return unique_root_fill_ids[0]

        unique_fill_ids = list(dict.fromkeys(all_fill_ids))
        if len(unique_fill_ids) == 1:
            return unique_fill_ids[0]

        if len(rect_lattice_ids) == 1:
            return rect_lattice_ids[0]
        return None


class RenderCapabilityReport(AgentBaseModel):
    renderability: Renderability = Field(
        default="none",
        description=(
            "Highest code-generation level a renderer can reach for this plan: "
            "'none', 'skeleton', 'exportable', or 'runnable'."
        ),
    )
    is_executable: bool = Field(
        default=True,
        description=(
            "Backward-compatible executable flag. True when renderability is "
            "'exportable' or 'runnable' (the renderer will emit XML)."
        ),
    )
    supported_renderer: Literal[
        "pin_cell", "assembly", "triso", "core", "skeleton", "none"
    ] = Field(
        default="pin_cell",
        description=(
            "Renderer selected for this plan. 'skeleton' is the review-only fallback; "
            "'none' means no renderer understands the IR."
        ),
    )
    executable_subsystems: list[str] = Field(default_factory=list)
    unsupported_subsystems: list[str] = Field(default_factory=list)
    reasons: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    required_human_confirmations: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def reconcile_executability(self) -> "RenderCapabilityReport":
        """Keep renderability and is_executable consistent.

        ``renderability`` is the source of truth when it was explicitly provided.
        Otherwise infer it from the legacy ``is_executable`` flag so existing
        callers and LLM-generated reports stay valid.
        """
        if "renderability" in self.model_fields_set:
            object.__setattr__(
                self,
                "is_executable",
                self.renderability in {"exportable", "runnable"},
            )
        elif self.is_executable:
            object.__setattr__(self, "renderability", "runnable")
        else:
            object.__setattr__(self, "renderability", "none")
        return self


class SimulationSpec(AgentBaseModel):
    name: str = Field(min_length=1, description="Name of the generated OpenMC model.")
    kind: Literal["pin_cell"] = Field(
        default="pin_cell",
        description="Supported geometry family for the current implementation.",
    )
    pin_cell: PinCellSpec
    settings: RunSettingsSpec = Field(default_factory=RunSettingsSpec)


class PlotSpec(AgentBaseModel):
    kind: Literal["slice"] = Field(
        default="slice",
        description="OpenMC plot kind. This phase supports 2-D slice plots.",
    )
    basis: Literal["xy", "xz", "yz"] = Field(
        description="Plane basis for the slice plot.",
        examples=["xy"],
    )
    origin: tuple[float, float, float] = Field(
        default=(0.0, 0.0, 0.0),
        description="Plot origin in centimeters.",
    )
    width_cm: tuple[float, float] = Field(
        description="Plot width in centimeters in the selected basis.",
    )
    pixels: tuple[int, int] = Field(
        default=(500, 500),
        description="Pixel dimensions for the plot image.",
    )
    color_by: Literal["material", "cell"] = Field(
        default="material",
        description="Whether OpenMC colors the plot by material or cell.",
    )
    filename: str = Field(
        min_length=1,
        description="Image filename, normally ending in .png.",
    )
    purpose: str = Field(
        default="Check the generated geometry definition.",
        description="Why this plot is useful for expert review.",
    )

    @model_validator(mode="after")
    def validate_plot_dimensions(self) -> "PlotSpec":
        if any(width <= 0 for width in self.width_cm):
            raise ValueError("plot width_cm values must be positive")
        if any(pixel <= 0 for pixel in self.pixels):
            raise ValueError("plot pixels must be positive")
        return self


class ExecutionCheckSpec(AgentBaseModel):
    enabled: bool = Field(
        default=True,
        description="Whether to run this execution check.",
    )
    settings: RunSettingsSpec = Field(
        default_factory=lambda: RunSettingsSpec(batches=5, inactive=1, particles=100),
        description="Low-cost OpenMC settings used for a smoke test.",
    )
    expected_checks: list[str] = Field(
        default_factory=list,
        description="Diagnostic conditions the smoke test should check.",
    )
    purpose: str = Field(
        default="Run a low-particle OpenMC smoke test to catch model definition errors.",
        description="Why this execution check is requested.",
    )


class ExpertFeedback(AgentBaseModel):
    text: str = Field(min_length=1, description="Natural-language expert feedback.")
    round_index: int = Field(default=0, ge=0, description="Workflow round that received it.")


class FeedbackDecision(AgentBaseModel):
    should_continue: bool = Field(
        default=True,
        description="Whether the workflow should continue after expert feedback.",
    )
    feedback: ExpertFeedback | None = None


class SimulationPlan(AgentBaseModel):
    schema_version: Literal["simulation_plan.v1", "simulation_plan.v2"] = Field(
        default="simulation_plan.v1",
        description="Version of the structured planning schema.",
    )
    model_spec: SimulationSpec | None = Field(
        default=None,
        description="Executable pin-cell OpenMC model description when this plan can be rendered.",
    )
    complex_model: ComplexModelSpec | None = Field(
        default=None,
        description="General OpenMC IR for assemblies, cores, reflectors, control rods, TRISO, and pebbles.",
    )
    capability_report: RenderCapabilityReport = Field(
        default_factory=RenderCapabilityReport,
        description="Executor capability assessment for this structured plan.",
    )
    plot_specs: list[PlotSpec] = Field(
        min_length=1,
        description="LLM-selected geometry plots for expert inspection.",
    )
    execution_check: ExecutionCheckSpec = Field(
        default_factory=ExecutionCheckSpec,
        description="LLM-selected low-cost execution check.",
    )
    expert_assumptions: list[str] = Field(
        default_factory=list,
        description="Modeling assumptions made because the user request was incomplete.",
    )
    expert_feedback: list[ExpertFeedback] = Field(
        default_factory=list,
        description="Natural-language expert feedback accumulated during the workflow.",
    )

    @model_validator(mode="after")
    def validate_plan_has_model(self) -> "SimulationPlan":
        if self.model_spec is None and self.complex_model is None:
            raise ValueError("SimulationPlan requires model_spec or complex_model")
        if (
            self.model_spec is None
            and self.capability_report.is_executable
            and self.capability_report.supported_renderer == "pin_cell"
        ):
            raise ValueError("pin_cell executable plans require model_spec")
        if (
            self.model_spec is None
            and self.capability_report.supported_renderer == "assembly"
            and (self.complex_model is None or self.complex_model.kind != "assembly")
        ):
            raise ValueError("assembly renderer requires complex_model.kind='assembly'")
        if (
            self.model_spec is None
            and self.capability_report.supported_renderer == "triso"
            and (
                self.complex_model is None
                or self.complex_model.kind not in {"triso_compact", "pebble"}
            )
        ):
            raise ValueError("triso renderer requires complex_model.kind='triso_compact' or 'pebble'")
        if (
            self.model_spec is None
            and self.capability_report.supported_renderer == "core"
            and (self.complex_model is None or self.complex_model.kind != "core")
        ):
            raise ValueError("core renderer requires complex_model.kind='core'")
        if (
            self.model_spec is None
            and not self.capability_report.is_executable
            and self.capability_report.supported_renderer != "none"
        ):
            raise ValueError("non-executable complex-only plans must use supported_renderer='none'")
        return self
