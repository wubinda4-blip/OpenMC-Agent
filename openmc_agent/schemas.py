from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


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
    schema_version: Literal["simulation_plan.v1"] = Field(
        default="simulation_plan.v1",
        description="Version of the structured planning schema.",
    )
    model_spec: SimulationSpec = Field(
        description="Structured OpenMC model description.",
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
