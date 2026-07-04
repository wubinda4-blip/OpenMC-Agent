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


def KnowledgeField(
    *args: Any,
    concept_id: str,
    doc_refs: list[str] | None = None,
    retrieval_queries: list[str] | None = None,
    common_errors: list[str] | None = None,
    **kwargs: Any,
) -> Any:
    """Return a Pydantic ``Field`` enriched with stable OpenMC knowledge metadata.

    Short, stable knowledge (concept id, doc pointers, retrieval hints, common
    mistakes) lives in the schema as ``json_schema_extra``; long-form manual
    content stays out and is reached indirectly through ``doc_refs`` /
    ``retrieval_queries``. Any caller-provided ``json_schema_extra`` dict is
    merged rather than overwritten, and the helper adds no behaviour beyond
    what ``Field`` already does.
    """
    existing = kwargs.pop("json_schema_extra", None)
    extra: dict[str, Any] = dict(existing) if isinstance(existing, dict) else {}
    extra["concept_id"] = concept_id
    if doc_refs is not None:
        extra["doc_refs"] = doc_refs
    if retrieval_queries is not None:
        extra["retrieval_queries"] = retrieval_queries
    if common_errors is not None:
        extra["common_errors"] = common_errors
    kwargs["json_schema_extra"] = extra
    return Field(*args, **kwargs)


class NuclideSpec(AgentBaseModel):
    name: str = KnowledgeField(
        min_length=1,
        description="OpenMC nuclide or element name, such as U235, O16, H1, or Zr.",
        concept_id="openmc.material.nuclide_name",
        doc_refs=["openmc.usersguide.materials", "openmc.api.Material.add_nuclide"],
        retrieval_queries=[
            "OpenMC nuclide naming convention U235 O16",
            "OpenMC Material add_nuclide accepted names",
        ],
        common_errors=[
            "Using lowercase or hyphenated nuclide names like 'u235' or 'U-235'",
            "Mixing element names with kind='nuclide'",
        ],
    )
    percent: float = KnowledgeField(
        gt=0,
        description="Component amount passed to OpenMC.",
        concept_id="openmc.material.composition_fraction",
        doc_refs=["openmc.usersguide.materials"],
        retrieval_queries=["OpenMC material composition percent normalization"],
        common_errors=[
            "Negative or zero composition fraction",
            "Forgetting that OpenMC normalizes fractions internally",
        ],
    )
    percent_type: Literal["ao", "wo"] = KnowledgeField(
        default="ao",
        description="OpenMC composition basis: atom percent (ao) or weight percent (wo).",
        concept_id="openmc.material.percent_type",
        doc_refs=["openmc.usersguide.materials"],
        retrieval_queries=["OpenMC percent_type ao wo atom weight percent"],
        common_errors=["Confusing atom percent (ao) with weight percent (wo)"],
    )
    kind: Literal["nuclide", "element"] = KnowledgeField(
        default="nuclide",
        description="Use element for elemental composition and nuclide for isotope-specific composition.",
        concept_id="openmc.material.nuclide_kind",
        doc_refs=["openmc.usersguide.materials", "openmc.api.Material.add_nuclide"],
        retrieval_queries=["OpenMC add_nuclide vs add_element kind"],
        common_errors=[
            "Setting kind='element' while providing an isotope name like U235",
            "Setting kind='nuclide' while providing an element name like Zr",
        ],
    )


class MaterialSpec(AgentBaseModel):
    name: str = Field(min_length=1, description="Human-readable material name.")
    density_unit: Literal["g/cm3", "kg/m3", "atom/b-cm"] = KnowledgeField(
        description="Density unit accepted by openmc.Material.set_density.",
        concept_id="openmc.material.density_unit",
        doc_refs=["openmc.usersguide.materials", "openmc.api.Material.set_density"],
        retrieval_queries=[
            "OpenMC Material.set_density density units",
            "OpenMC material density atom/b-cm g/cm3 kg/m3",
        ],
        common_errors=[
            "Using unsupported density units such as 'g/cc' or 'kg/cm3'",
            "Providing density_value without density_unit",
        ],
    )
    density_value: float = KnowledgeField(
        gt=0,
        description="Positive material density value.",
        concept_id="openmc.material.density_value",
        doc_refs=["openmc.usersguide.materials", "openmc.api.Material.set_density"],
        retrieval_queries=["OpenMC Material.set_density density value meaning"],
        common_errors=[
            "Zero or negative density",
            "Pairing a g/cm3 value with atom/b-cm unit",
        ],
    )
    composition: list[NuclideSpec] = KnowledgeField(
        min_length=1,
        description="Nuclide or element composition entries for this material.",
        concept_id="openmc.material.composition",
        doc_refs=["openmc.usersguide.materials"],
        retrieval_queries=["OpenMC material composition nuclide list"],
        common_errors=["Empty composition list"],
    )
    temperature_k: float | None = Field(
        default=None,
        gt=0,
        description="Optional material temperature in kelvin.",
    )
    sab: list[str] = KnowledgeField(
        default_factory=list,
        description="Optional thermal scattering names, such as c_H_in_H2O.",
        concept_id="openmc.material.thermal_scattering",
        doc_refs=["openmc.usersguide.materials", "openmc.api.Material.add_s_alpha_beta"],
        retrieval_queries=[
            "OpenMC thermal scattering S(alpha,beta) library names",
            "OpenMC Material add_s_alpha_beta c_H_in_H2O",
        ],
        common_errors=[
            "Referencing a thermal scattering name without the matching lib80x library",
            "Adding S(alpha,beta) to a material whose temperature has no data",
        ],
    )
    chemical_formula: str | None = KnowledgeField(
        default=None,
        description="Optional chemical formula when the model should use OpenMC material helpers.",
        concept_id="openmc.material.chemical_formula",
        doc_refs=["openmc.usersguide.materials"],
        retrieval_queries=["OpenMC material chemical formula UO2 H2O"],
        common_errors=["Using an ambiguous formula without nuclide-level composition"],
    )
    enrichment_percent: float | None = KnowledgeField(
        default=None,
        gt=0,
        description="Optional enrichment percentage for enriched elemental materials.",
        concept_id="openmc.material.enrichment",
        doc_refs=["openmc.usersguide.materials", "openmc.api.Material.add_element"],
        retrieval_queries=["OpenMC add_element enrichment percent U235"],
        common_errors=[
            "Giving enrichment for a nuclide instead of an element",
            "Omitting enrichment_target while setting enrichment_percent",
        ],
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
    density_unit: Literal["g/cm3", "kg/m3", "atom/b-cm", "sum", "macro"] | None = KnowledgeField(
        default=None,
        description="OpenMC density unit when known. Use null when the source did not specify it.",
        concept_id="openmc.material.density_unit",
        doc_refs=["openmc.usersguide.materials", "openmc.api.Material.set_density"],
        retrieval_queries=[
            "OpenMC Material.set_density density units",
            "OpenMC multi-group macroscopic density_unit macro",
        ],
        common_errors=[
            "Mixing macroscopic cross-section data with a non-'macro' density unit",
            "Providing only one of density_unit / density_value",
        ],
    )
    density_value: float | None = KnowledgeField(
        default=None,
        gt=0,
        description="Positive material density value when known.",
        concept_id="openmc.material.density_value",
        doc_refs=["openmc.usersguide.materials", "openmc.api.Material.set_density"],
        retrieval_queries=["OpenMC material density value"],
        common_errors=["Zero or negative density", "Leaving density value unknown for a used material"],
    )
    composition: list[NuclideSpec] = KnowledgeField(
        default_factory=list,
        description="Nuclide or element composition entries when known.",
        concept_id="openmc.material.composition",
        doc_refs=["openmc.usersguide.materials"],
        retrieval_queries=["OpenMC material composition nuclide list"],
        common_errors=["Empty composition for a continuous-energy material"],
    )
    chemical_formula: str | None = Field(
        default=None,
        description="Chemical formula or shorthand, such as UO2, SiC, graphite, or H2O.",
    )
    macroscopic: str | None = KnowledgeField(
        default=None,
        description=(
            "OpenMC macroscopic cross-section dataset name for multi-group materials, "
            "for example 'uo2' or 'water' in a C5G7 MGXS library."
        ),
        concept_id="openmc.material.macroscopic",
        doc_refs=["openmc.usersguide.materials", "openmc.usersguide.mgxs"],
        retrieval_queries=[
            "OpenMC macroscopic cross-section MGXS library name",
            "OpenMC multi-group material macroscopic c5g7",
        ],
        common_errors=[
            "Using a macroscopic dataset without energy_mode='multi-group'",
            "Naming a macroscopic dataset that is not present in the MGXS HDF5 file",
        ],
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
        has_material_definition = bool(self.macroscopic or self.composition or self.chemical_formula)
        if not has_material_definition and not self.requires_human_confirmation:
            raise ValueError(
                "complex material needs composition, chemical_formula, macroscopic, "
                "or requires_human_confirmation"
            )
        if self.macroscopic and self.density_unit not in {None, "macro"}:
            raise ValueError("macroscopic materials must use density_unit='macro' or omit density")
        # A partial density (only one of unit/value) is allowed when the material
        # is explicitly pending human confirmation -- e.g. a candidate burnable-
        # poison material whose density the source document did not give. The LLM
        # often knows the unit (or an estimate) but not the value. The capability
        # layer decides whether the gap blocks based on whether the material is
        # actually used by the default model (reachability), not here.
        if not self.macroscopic and (self.density_unit is None) != (self.density_value is None):
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
    ] = KnowledgeField(
        description="OpenMC surface or composite-surface family.",
        concept_id="openmc.geometry.surface",
        doc_refs=["openmc.usersguide.geometry", "openmc.api.Surface"],
        retrieval_queries=["OpenMC surface kinds zcylinder sphere plane"],
        common_errors=[
            "Choosing a cylinder oriented along the wrong axis",
            "Confusing rectangular_prism and hexagonal_prism parameters",
        ],
    )
    parameters: dict[str, Any] = KnowledgeField(
        default_factory=dict,
        description="OpenMC constructor parameters, such as r, x0, y0, z0, pitch, or orientation.",
        concept_id="openmc.geometry.surface_parameters",
        doc_refs=["openmc.usersguide.geometry", "openmc.api.Surface"],
        retrieval_queries=["OpenMC ZCylinder r x0 y0 parameters", "OpenMC RectangularPrism pitch parameters"],
        common_errors=[
            "Passing radius as 'r' for a plane surface",
            "Mixing cylinder axis parameters across x/y/z cylinders",
        ],
    )
    boundary_type: Literal["transmission", "vacuum", "reflective", "periodic", "white"] | None = KnowledgeField(
        default=None,
        description="Optional OpenMC boundary_type for outer surfaces.",
        concept_id="openmc.geometry.boundary_type",
        doc_refs=["openmc.usersguide.geometry", "openmc.api.Surface.boundary_type"],
        retrieval_queries=["OpenMC boundary_type reflective vacuum periodic"],
        common_errors=[
            "Using 'reflective' on the outer boundary of a finite core",
            "Forgetting to set vacuum on the outermost boundary",
        ],
    )
    purpose: str = Field(default="", description="Why this surface is needed.")


class RegionSpec(AgentBaseModel):
    id: str = Field(min_length=1, description="Stable region identifier.")
    expression: str = KnowledgeField(
        min_length=1,
        description="OpenMC boolean region expression using surface ids, such as -fuel & +xmin.",
        concept_id="openmc.geometry.region_boolean_expression",
        doc_refs=["openmc.usersguide.geometry", "openmc.api.Cell"],
        retrieval_queries=["OpenMC region boolean expression surface sense operator"],
        common_errors=[
            "Using the wrong surface sense sign for the desired half-space",
            "Referencing a surface id that is not defined in surfaces",
        ],
    )
    surface_ids: list[str] = KnowledgeField(
        default_factory=list,
        description="Surface ids referenced by the expression for validation and traceability.",
        concept_id="openmc.geometry.region_surface_refs",
        doc_refs=["openmc.usersguide.geometry"],
        retrieval_queries=["OpenMC region referenced surfaces"],
        common_errors=[
            "Listing surface ids inconsistent with the boolean expression",
            "Forgetting a surface used inside the expression",
        ],
    )
    purpose: str = Field(default="", description="Geometry role of this region.")


class CellSpec(AgentBaseModel):
    id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    region_id: str | None = Field(default=None)
    fill_type: Literal["material", "universe", "lattice", "void"] = KnowledgeField(
        default="material",
        description=(
            "What this cell is filled with. Use 'universe' or 'lattice' to build "
            "repeated/hierarchical geometry (pin → assembly → core)."
        ),
        concept_id="openmc.geometry.cell_fill",
        doc_refs=["openmc.usersguide.geometry", "openmc.api.Cell.fill"],
        retrieval_queries=[
            "OpenMC Cell fill universe lattice material",
            "OpenMC repeated geometry fill hierarchy",
        ],
        common_errors=[
            "Using fill_type='lattice' with a fill_id that is not a lattice",
            "Forgetting fill_id for a non-void fill",
        ],
    )
    fill_id: str | None = KnowledgeField(
        default=None,
        description="Referenced material, universe, or lattice id.",
        concept_id="openmc.geometry.cell_fill",
        doc_refs=["openmc.usersguide.geometry", "openmc.api.Cell.fill"],
        retrieval_queries=["OpenMC Cell fill material/universe/lattice id reference"],
        common_errors=[
            "Referencing a universe/lattice id that does not exist",
            "Leaving fill_id empty for a material/universe/lattice fill",
        ],
    )
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
    cell_ids: list[str] = KnowledgeField(
        default_factory=list,
        description=(
            "Cells grouped into this universe. Universes are the reusable unit of "
            "repeated geometry: a universe is filled into many lattice positions."
        ),
        concept_id="openmc.geometry.universe",
        doc_refs=["openmc.usersguide.geometry", "openmc.api.Universe"],
        retrieval_queries=[
            "OpenMC Universe cell grouping repeated geometry",
            "OpenMC lattice fill universe",
        ],
        common_errors=[
            "Referencing cell ids that do not exist",
            "Building a universe with no cells and then filling it into a lattice",
        ],
    )
    purpose: str = ""


class LatticeSpec(AgentBaseModel):
    id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    kind: Literal["rect", "hex"] = KnowledgeField(
        description="OpenMC RectLattice or HexLattice.",
        concept_id="openmc.geometry.lattice",
        doc_refs=["openmc.usersguide.geometry", "openmc.api.RectLattice", "openmc.api.HexLattice"],
        retrieval_queries=[
            "OpenMC RectLattice HexLattice repeated geometry",
            "OpenMC lattice full core assembly",
        ],
        common_errors=[
            "Choosing 'hex' when the core uses a Cartesian layout",
            "Mixing rect universe_pattern with a hex lattice",
        ],
    )
    pitch_cm: tuple[float, ...] = KnowledgeField(
        min_length=1,
        description=(
            "Center-to-center spacing in cm. Two components (x,y) for a RectLattice, "
            "two (pitch, pitch) for a HexLattice in OpenMC's convention."
        ),
        concept_id="openmc.geometry.lattice_pitch",
        doc_refs=["openmc.usersguide.geometry", "openmc.api.RectLattice", "openmc.api.HexLattice"],
        retrieval_queries=["OpenMC lattice pitch units cm RectLattice HexLattice"],
        common_errors=[
            "Using a single pitch value where OpenMC expects a 2-tuple",
            "Swapping HexLattice pitch orientation between flat-to-flat and point-to-point",
        ],
    )
    lower_left_cm: tuple[float, ...] | None = None
    center_cm: tuple[float, ...] | None = None
    shape: tuple[int, ...] | None = None
    outer_universe_id: str | None = None
    universe_pattern: list[list[str]] = KnowledgeField(
        default_factory=list,
        description=(
            "Rectangular lattice universe ids by row/column. Each inner list is a row; "
            "rows must have equal length and every id should reference a defined universe."
        ),
        concept_id="openmc.geometry.rect_lattice",
        doc_refs=["openmc.usersguide.geometry", "openmc.api.RectLattice"],
        retrieval_queries=[
            "OpenMC RectLattice universes 2D array",
            "OpenMC rectangular lattice universe pattern",
        ],
        common_errors=[
            "Ragged rows (inner lists of different length)",
            "Filling lattice positions with undefined universe ids",
            "Forgetting the outer universe for positions outside the active core",
        ],
    )
    rings: list[list[str]] = KnowledgeField(
        default_factory=list,
        description=(
            "Hexagonal lattice universe ids by ring, from center outward. OpenMC "
            "expects num_rings rings with 1, 6, 12, 18, ... elements."
        ),
        concept_id="openmc.geometry.hex_lattice",
        doc_refs=["openmc.usersguide.geometry", "openmc.api.HexLattice"],
        retrieval_queries=["OpenMC HexLattice rings universe indices", "OpenMC hexagonal lattice ring counts"],
        common_errors=[
            "Wrong number of elements per hex ring (must be 6*n for n>=1)",
            "Confusing ring ordering (center-first vs outer-first)",
        ],
    )
    fill_universe: str | None = KnowledgeField(
        default=None,
        description=(
            "Compact rect-lattice template: universe id used to fill every position "
            "before overrides are applied. Provide with shape + overrides instead of "
            "hand-enumerating universe_pattern; the schema expands it deterministically "
            "so large regular maps need not be written by hand."
        ),
        concept_id="openmc.geometry.rect_lattice",
        doc_refs=["openmc.usersguide.geometry", "openmc.api.RectLattice"],
        retrieval_queries=["OpenMC RectLattice fill universe repeated geometry compact"],
        common_errors=["Setting fill_universe without shape"],
    )
    overrides: dict[str, list[tuple[int, int]]] = KnowledgeField(
        default_factory=dict,
        description=(
            "Compact rect-lattice overrides: universe_id -> [(row, col), ...] with "
            "row 0 = top, col 0 = left (0-indexed, matching the engineering "
            "top-to-bottom / left-to-right pin description). Applied on top of "
            "fill_universe to place guide tubes, fission chambers, MOX zones, water."
        ),
        concept_id="openmc.geometry.rect_lattice",
        doc_refs=["openmc.usersguide.geometry", "openmc.api.RectLattice"],
        retrieval_queries=["OpenMC RectLattice universe position override pin map"],
        common_errors=[
            "Using physical (x, y) cm instead of (row, col) indices",
            "Forgetting that row 0 is the top row of the drawing",
        ],
    )
    expected_counts: dict[str, int] | None = KnowledgeField(
        default=None,
        description=(
            "Optional hard pin-count check: universe_id -> expected occurrences in the "
            "expanded pattern. Enforces benchmark counts (e.g. 264 fuel + 24 guide "
            "tubes + 1 chamber = 289); a mismatch fails validation."
        ),
        concept_id="openmc.geometry.rect_lattice",
        doc_refs=["openmc.usersguide.geometry"],
        retrieval_queries=["OpenMC lattice pin count validation benchmark"],
        common_errors=["Counts whose sum is not rows*cols"],
    )
    requires_human_confirmation: list[str] = Field(default_factory=list)
    purpose: str = ""

    @field_validator("rings", "universe_pattern", mode="before")
    @classmethod
    def _coerce_none_to_empty_list(cls, value: Any) -> Any:
        """Tolerate LLMs that emit ``null`` for optional list fields."""
        if value is None:
            return []
        return value

    @field_validator("overrides", mode="before")
    @classmethod
    def _coerce_none_to_empty_dict(cls, value: Any) -> Any:
        """Tolerate LLMs that emit ``null`` for the overrides map."""
        if value is None:
            return {}
        return value

    def _rect_shape(self) -> tuple[int, int] | None:
        """Return ``(rows, cols)`` for a rect lattice, consistent with the renderer.

        ``shape`` follows OpenMC's ``(num_x, num_y)`` convention and
        ``universe_pattern`` is ``[num_y rows][num_x cols]``; this mirrors
        ``_shape_to_rows_cols`` in the assembly renderer so expansion and the
        renderer agree on dimensions.
        """
        shape = self.shape
        if not shape:
            return None
        if len(shape) == 2 and all(isinstance(n, int) and n > 0 for n in shape):
            nx, ny = int(shape[0]), int(shape[1])
            return ny, nx
        if len(shape) == 1 and isinstance(shape[0], int) and shape[0] > 0:
            n = int(shape[0])
            return n, n
        return None

    @model_validator(mode="after")
    def _expand_compact_pattern(self) -> "LatticeSpec":
        """Expand a rect ``fill_universe`` + ``overrides`` template into
        ``universe_pattern`` deterministically.

        Coordinate convention: ``(row, col)`` is 0-indexed with row 0 = top and
        col 0 = left, matching ``universe_pattern[row][col]`` and the engineering
        top-to-bottom / left-to-right description. An explicit ``universe_pattern``
        always wins; the template only fills when it is empty. This keeps the LLM
        from hand-enumerating large regular maps (the error-prone part) while the
        expansion stays deterministic and unit-testable.
        """
        if self.kind != "rect" or not self.fill_universe:
            return self
        if self.universe_pattern:
            return self  # explicit enumeration wins; do not clobber it
        shape = self._rect_shape()
        if shape is None:
            raise ValueError(
                "compact rect lattice template requires shape=(nx, ny) "
                "together with fill_universe"
            )
        rows, cols = shape
        grid: list[list[str]] = [[self.fill_universe] * cols for _ in range(rows)]
        for universe_id, positions in self.overrides.items():
            for position in positions:
                row, col = position
                if not (0 <= row < rows and 0 <= col < cols):
                    raise ValueError(
                        f"override position {(row, col)} for universe "
                        f"{universe_id!r} is out of bounds for {(rows, cols)} "
                        f"(rows, cols)"
                    )
                grid[row][col] = universe_id
        object.__setattr__(self, "universe_pattern", grid)
        return self

    @model_validator(mode="after")
    def _validate_expected_counts(self) -> "LatticeSpec":
        """Flag benchmark pin-count mismatches without hard-failing construction.

        A mismatch is recorded in requires_human_confirmation with the precise diff
        so the expert / repair loop gets actionable feedback and the workflow can
        still emit a reviewable skeleton instead of dying mid-construction. The
        assembly renderer independently blocks XML export on a mismatch, so a wrong
        pin map can never silently become a runnable model.
        """
        if self.kind != "rect" or not self.expected_counts:
            return self
        pattern = self.universe_pattern
        if not pattern:
            return self  # missing-pattern confirmation handled elsewhere
        actual: dict[str, int] = {}
        for row in pattern:
            for universe_id in row:
                actual[universe_id] = actual.get(universe_id, 0) + 1
        mismatches: list[str] = [
            f"{universe_id}: expected {expected}, got {actual.get(universe_id, 0)}"
            for universe_id, expected in self.expected_counts.items()
            if actual.get(universe_id, 0) != expected
        ]
        if mismatches:
            message = "pin count mismatch vs expected_counts: " + "; ".join(mismatches)
            confirmations = list(self.requires_human_confirmation)
            if message not in confirmations:
                confirmations.append(message)
            object.__setattr__(self, "requires_human_confirmation", confirmations)
        return self

    @model_validator(mode="after")
    def validate_lattice_definition(self) -> "LatticeSpec":
        if any(pitch <= 0 for pitch in self.pitch_cm):
            raise ValueError("lattice pitch_cm values must be positive")
        if self.kind == "rect" and not self.universe_pattern:
            confirmations = list(self.requires_human_confirmation)
            message = "rect lattice universe_pattern is missing"
            if message not in confirmations:
                confirmations.append(message)
            object.__setattr__(self, "requires_human_confirmation", confirmations)
        if self.kind == "hex" and not self.rings:
            confirmations = list(self.requires_human_confirmation)
            message = "hex lattice rings are missing"
            if message not in confirmations:
                confirmations.append(message)
            object.__setattr__(self, "requires_human_confirmation", confirmations)
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


BoundaryType = Literal["transmission", "vacuum", "reflective", "periodic", "white"]


class CoreBoundarySpec(AgentBaseModel):
    xmin: BoundaryType | None = None
    xmax: BoundaryType | None = None
    ymin: BoundaryType | None = None
    ymax: BoundaryType | None = None
    zmin: BoundaryType | None = None
    zmax: BoundaryType | None = None


class FillRefSpec(AgentBaseModel):
    type: Literal["material", "universe", "lattice", "void"] = Field(
        description="OpenMC object kind used as a fill."
    )
    id: str | None = Field(
        default=None,
        description="Referenced material, universe, or lattice id. Omit for void.",
    )

    @model_validator(mode="after")
    def validate_fill_id(self) -> "FillRefSpec":
        if self.type == "void":
            object.__setattr__(self, "id", None)
            return self
        if not self.id:
            raise ValueError("fill.id is required unless fill.type is void")
        return self


class AxialLayerSpec(AgentBaseModel):
    id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    z_min_cm: float
    z_max_cm: float
    fill: FillRefSpec = Field(
        description="Final OpenMC object filled into this axial root cell."
    )
    loading_id: str | None = None
    purpose: str = ""

    @model_validator(mode="after")
    def validate_layer(self) -> "AxialLayerSpec":
        if self.z_max_cm <= self.z_min_cm:
            raise ValueError("axial layer z_max_cm must exceed z_min_cm")
        return self


class LatticeLoadingSpec(AgentBaseModel):
    id: str = Field(min_length=1)
    base_lattice_id: str = Field(
        min_length=1,
        description="Existing LatticeSpec id used as the base loading.",
    )
    derived_lattice_id: str | None = Field(
        default=None,
        description="Optional id for the renderer-generated derived lattice.",
    )
    overrides: dict[str, list[tuple[int, int]]] = Field(
        default_factory=dict,
        description="Universe id -> [(row, col)] overrides applied to base_lattice_id.",
    )
    purpose: str = ""

    @field_validator("overrides", mode="before")
    @classmethod
    def _coerce_none_to_empty_dict(cls, value: Any) -> Any:
        if value is None:
            return {}
        return value


class CoreSpec(AgentBaseModel):
    id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    lattice_id: str | None = None
    assembly_ids: list[str] = Field(default_factory=list)
    reflector_ids: list[str] = Field(default_factory=list)
    control_rod_ids: list[str] = Field(default_factory=list)
    boundary: Literal["vacuum", "reflective", "periodic", "mixed", "unknown"] = "unknown"
    boundary_conditions: CoreBoundarySpec | None = None
    axial_layers: list[AxialLayerSpec] = Field(default_factory=list)
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


class KnowledgeRef(AgentBaseModel):
    ref_id: str = Field(description="Stable identifier for the referenced knowledge artifact.")
    title: str = Field(description="Human-readable title of the referenced artifact.")
    source_type: Literal[
        "schema",
        "openmc_docs",
        "openmc_developer_docs",
        "example",
        "kg_node",
        "retrieval_query",
        "project_rule",
    ] = Field(description="Category of the referenced knowledge source.")
    locator: str | None = Field(
        default=None,
        description="Human-readable location, e.g. a manual section heading or API path.",
    )
    retrieval_query: str | None = Field(
        default=None,
        description="Suggested search query for future document retrieval.",
    )
    concept_id: str | None = Field(
        default=None,
        description="OpenMC concept this reference explains.",
    )


class RepairHint(AgentBaseModel):
    action: Literal[
        "edit_field",
        "add_missing_field",
        "remove_field",
        "retrieve_docs",
        "ask_human",
        "downgrade_renderability",
        "switch_renderer",
        "mark_requires_human_confirmation",
    ] = Field(description="Structured repair action an agent or human can take.")
    message: str = Field(description="Concrete description of the repair step.")
    target_path: str | None = Field(
        default=None,
        description="Dotted path in the IR/plan that the action targets.",
    )
    example_patch: dict[str, Any] | None = Field(
        default=None,
        description="Optional illustrative patch an agent can adapt.",
    )


class ValidationIssue(AgentBaseModel):
    severity: Literal["error", "warning", "info"] = Field(description="Issue severity level.")
    code: str = Field(
        description="Stable error code, e.g. 'geometry.fuel_radius.too_large_for_pitch'.",
    )
    message: str = Field(
        description="Human-readable message; also surfaces in legacy errors/warnings.",
    )
    schema_path: str | None = Field(
        default=None,
        description="Dotted path to the offending field in the IR/plan.",
    )
    rule_id: str | None = Field(
        default=None,
        description="Stable identifier of the validation rule that fired.",
    )
    concept_id: str | None = Field(
        default=None,
        description="OpenMC concept related to this issue.",
    )
    knowledge_refs: list[KnowledgeRef] = Field(default_factory=list)
    repair_hints: list[RepairHint] = Field(default_factory=list)
    grep_patterns: list[str] = Field(
        default_factory=list,
        description="Stable code/search tokens for later grep, graph, RAG, or repair routing.",
    )
    requires_retrieval: bool = Field(
        default=False,
        description="Whether resolving this issue needs document retrieval.",
    )
    requires_human_confirmation: bool = Field(
        default=False,
        description="Whether a human must confirm the proposed fix.",
    )
    route_hint: Literal[
        "auto_repair",
        "reflect_plan",
        "ask_expert",
        "retrieval",
        "capability_downgrade",
        "manual_review",
    ] | None = Field(
        default=None,
        description="Preferred deterministic route for this issue.",
    )

    def __str__(self) -> str:
        return self.message

    def __contains__(self, text: object) -> bool:
        return isinstance(text, str) and text in self.message


class ValidationReport(AgentBaseModel):
    is_valid: bool
    errors: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    suggestions: list[str] = Field(default_factory=list)
    issues: list[ValidationIssue] = Field(
        default_factory=list,
        description="Structured validation issues with stable codes and knowledge references.",
    )

    @classmethod
    def from_issues(
        cls,
        issues: "list[ValidationIssue]",
        *,
        is_valid: bool | None = None,
    ) -> "ValidationReport":
        """Build a report whose legacy string fields are derived from issues.

        ``errors`` collects every error-severity message, ``warnings`` every
        warning-severity message, and ``suggestions`` the repair-hint messages.
        Callers that construct ``ValidationReport`` directly with explicit
        ``errors``/``warnings`` are unaffected.
        """
        errors: list[str] = []
        warnings: list[str] = []
        suggestions: list[str] = []
        for issue in issues:
            if issue.severity == "error":
                errors.append(issue.message)
            elif issue.severity == "warning":
                warnings.append(issue.message)
            for hint in issue.repair_hints:
                suggestions.append(hint.message)
        if is_valid is None:
            is_valid = not errors
        return cls(
            is_valid=is_valid,
            errors=errors,
            warnings=warnings,
            suggestions=suggestions,
            issues=issues,
        )


class GeometrySpec(AgentBaseModel):
    fuel_radius_cm: float = KnowledgeField(
        gt=0,
        le=2.0,
        description="Fuel pellet outer radius in cm. Must be less than half the pitch.",
        concept_id="openmc.geometry.pin_cell_radius",
        doc_refs=["openmc.usersguide.geometry"],
        retrieval_queries=["OpenMC pin cell fuel radius pitch reflective boundary"],
        common_errors=[
            "Setting fuel_radius_cm >= pitch_cm / 2 so pellets overlap",
            "Confusing radius with diameter",
        ],
    )
    pitch_cm: float = KnowledgeField(
        gt=0,
        le=5.0,
        description="Pin pitch (center-to-center spacing) in cm.",
        concept_id="openmc.geometry.pin_cell_pitch",
        doc_refs=["openmc.usersguide.geometry"],
        retrieval_queries=["OpenMC pin cell pitch units cm"],
        common_errors=["Using diameter instead of pitch", "Pitch not matching the lattice pitch"],
    )
    clad_inner_radius_cm: float | None = KnowledgeField(
        default=None,
        gt=0,
        le=2.5,
        description="Cladding inner radius in cm; must be provided together with the outer radius.",
        concept_id="openmc.geometry.cladding_radii",
        doc_refs=["openmc.usersguide.geometry"],
        retrieval_queries=["OpenMC pin cell cladding inner outer radius gap"],
        common_errors=[
            "Providing only one of inner/outer cladding radius",
            "Cladding inner radius not exceeding the fuel radius",
        ],
    )
    clad_outer_radius_cm: float | None = KnowledgeField(
        default=None,
        gt=0,
        le=2.5,
        description="Cladding outer radius in cm; must be less than half the pitch.",
        concept_id="openmc.geometry.cladding_radii",
        doc_refs=["openmc.usersguide.geometry"],
        retrieval_queries=["OpenMC pin cell cladding outer radius pitch"],
        common_errors=[
            "Cladding outer radius not exceeding the inner radius",
            "Cladding outer radius >= pitch_cm / 2",
        ],
    )

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
    batches: int = KnowledgeField(
        default=50,
        ge=1,
        description="Number of OpenMC batches or generations used for this run.",
        concept_id="openmc.settings.batches",
        doc_refs=["openmc.usersguide.settings", "openmc.api.Settings.batches"],
        retrieval_queries=["OpenMC Settings batches generations eigenvalue"],
        common_errors=[
            "Too few batches for a converged eigenvalue",
            "Setting inactive >= batches",
        ],
    )
    inactive: int = KnowledgeField(
        default=10,
        ge=0,
        description="Inactive batches for eigenvalue source convergence.",
        concept_id="openmc.settings.inactive",
        doc_refs=["openmc.usersguide.settings", "openmc.api.Settings.inactive"],
        retrieval_queries=["OpenMC inactive batches source convergence"],
        common_errors=["inactive >= batches", "Too few inactive batches for source convergence"],
    )
    particles: int = KnowledgeField(
        default=1000,
        ge=1,
        description="Particles per batch or generation.",
        concept_id="openmc.settings.particles",
        doc_refs=["openmc.usersguide.settings", "openmc.api.Settings.particles"],
        retrieval_queries=["OpenMC particles per batch statistics"],
        common_errors=[
            "Too few particles for acceptable statistical uncertainty",
            "Using the smoke-test particle count for a real run",
        ],
    )
    energy_mode: Literal["continuous-energy", "multi-group"] | None = KnowledgeField(
        default=None,
        description=(
            "Optional OpenMC energy mode. Use 'multi-group' for macroscopic "
            "MGXS models such as C5G7."
        ),
        concept_id="openmc.settings.energy_mode",
        doc_refs=["openmc.usersguide.settings", "openmc.usersguide.mgxs"],
        retrieval_queries=["OpenMC energy_mode continuous-energy multi-group MGXS"],
        common_errors=[
            "Selecting multi-group without a macroscopic cross-section library",
            "Leaving energy_mode implicit when using macroscopic materials",
        ],
    )
    seed: int | None = KnowledgeField(
        default=None,
        ge=1,
        description="Optional deterministic random seed for reproducible checks.",
        concept_id="openmc.settings.seed",
        doc_refs=["openmc.usersguide.settings", "openmc.api.Settings.seed"],
        retrieval_queries=["OpenMC Settings seed reproducible random"],
        common_errors=["Using seed=0 or a negative value"],
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
    ] = KnowledgeField(
        default="mixed",
        description=(
            "Top-level model family. Drives renderer selection and the repeated-"
            "geometry hierarchy (cells → universes → lattices → assemblies → core)."
        ),
        concept_id="openmc.ir.model_kind",
        doc_refs=["openmc.usersguide.geometry"],
        retrieval_queries=["OpenMC model kind assembly core repeated geometry"],
        common_errors=[
            "Declaring kind='core' without a core loading pattern",
            "Leaving kind='mixed' when a specific renderer is expected",
        ],
    )
    materials: list[ComplexMaterialSpec] = KnowledgeField(
        default_factory=list,
        description="Material library referenced by cells and TRISO/pebble layers.",
        concept_id="openmc.ir.complex_model",
        doc_refs=["openmc.usersguide.materials"],
        retrieval_queries=["OpenMC complex model material library"],
        common_errors=["Referencing material ids in cells that are not defined here"],
    )
    surfaces: list[SurfaceSpec] = KnowledgeField(
        default_factory=list,
        description="Surface library used by region boolean expressions.",
        concept_id="openmc.ir.complex_model",
        doc_refs=["openmc.usersguide.geometry"],
        retrieval_queries=["OpenMC complex model surface library"],
        common_errors=[
            "Defining duplicate surface ids",
            "Referencing undefined surfaces in region expressions",
        ],
    )
    regions: list[RegionSpec] = KnowledgeField(
        default_factory=list,
        description="Region library assigned to cells.",
        concept_id="openmc.ir.complex_model",
        doc_refs=["openmc.usersguide.geometry"],
        retrieval_queries=["OpenMC cell region boolean expression library"],
        common_errors=["Region expressions referencing missing surface ids"],
    )
    cells: list[CellSpec] = KnowledgeField(
        default_factory=list,
        description=(
            "Cell library. Cells fill materials, universes, or lattices and are "
            "grouped into universes for repeated geometry."
        ),
        concept_id="openmc.ir.complex_model",
        doc_refs=["openmc.usersguide.geometry", "openmc.api.Cell"],
        retrieval_queries=["OpenMC complex model cells universes hierarchy"],
        common_errors=[
            "Cells referencing missing region or fill ids",
            "Forgetting to group cells into a universe before lattice fill",
        ],
    )
    universes: list[UniverseSpec] = Field(default_factory=list)
    lattices: list[LatticeSpec] = KnowledgeField(
        default_factory=list,
        description=(
            "Lattice library. Lattices repeat universe-filled cells across a 2D "
            "pattern, the backbone of assembly and full-core geometry."
        ),
        concept_id="openmc.ir.complex_model",
        doc_refs=["openmc.usersguide.geometry", "openmc.api.RectLattice", "openmc.api.HexLattice"],
        retrieval_queries=["OpenMC complex model lattices repeated geometry full core"],
        common_errors=[
            "Lattice positions referencing undefined universes",
            "Mismatched lattice pitch and the pitch implied by the pin pattern",
        ],
    )
    lattice_loadings: list[LatticeLoadingSpec] = Field(default_factory=list)
    assemblies: list[AssemblySpec] = Field(default_factory=list)
    core: CoreSpec | None = None
    reflectors: list[ReflectorSpec] = Field(default_factory=list)
    control_rods: list[ControlRodSpec] = Field(default_factory=list)
    trisos: list[TRISOSpec] = Field(default_factory=list)
    packed_spheres: list[PackedSphereSpec] = Field(default_factory=list)
    pebbles: list[PebbleSpec] = Field(default_factory=list)
    settings: RunSettingsSpec = Field(default_factory=RunSettingsSpec)
    mg_cross_sections_file: str | None = Field(
        default=None,
        description=(
            "Path to an OpenMC multi-group cross-section HDF5 file. If omitted, "
            "OpenMC may use OPENMC_MG_CROSS_SECTIONS."
        ),
    )
    standard_mgxs_library: Literal["c5g7"] | None = Field(
        default=None,
        description=(
            "Built-in benchmark MGXS library to export before running. Use 'c5g7' "
            "for OECD/NEA C5G7 seven-group macroscopic data."
        ),
    )
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
                self.lattice_loadings,
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
    renderability: Renderability = KnowledgeField(
        default="none",
        description=(
            "Highest code-generation level a renderer can reach for this plan: "
            "'none', 'skeleton', 'exportable', or 'runnable'."
        ),
        concept_id="openmc_agent.renderability",
        doc_refs=["project.capability_report"],
        retrieval_queries=["openmc-agent renderability skeleton exportable runnable"],
        common_errors=[
            "Claiming 'runnable' when a subsystem is unsupported",
            "Leaving renderability inconsistent with is_executable",
        ],
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
    ] = KnowledgeField(
        default="pin_cell",
        description=(
            "Renderer selected for this plan. 'skeleton' is the review-only fallback; "
            "'none' means no renderer understands the IR."
        ),
        concept_id="openmc_agent.renderer_selection",
        doc_refs=["project.capability_report"],
        retrieval_queries=["openmc-agent supported_renderer selection assembly core triso"],
        common_errors=[
            "Selecting a renderer whose model kind is not present",
            "Using a concrete renderer for a non-executable plan instead of 'none'",
        ],
    )
    executable_subsystems: list[str] = Field(default_factory=list)
    unsupported_subsystems: list[str] = KnowledgeField(
        default_factory=list,
        description="Model subsystems this executor version cannot render yet.",
        concept_id="openmc_agent.unsupported_subsystem",
        doc_refs=["project.capability_report"],
        retrieval_queries=["openmc-agent unsupported subsystem capability"],
        common_errors=[
            "Listing a subsystem as unsupported while still claiming runnable",
            "Omitting the reason a subsystem is unsupported",
        ],
    )
    reasons: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    required_human_confirmations: list[str] = KnowledgeField(
        default_factory=list,
        description="Open questions that must be confirmed by a human expert before execution.",
        concept_id="openmc_agent.human_confirmation",
        doc_refs=["project.capability_report"],
        retrieval_queries=["openmc-agent human confirmation required fields"],
        common_errors=[
            "Executing a plan that still has open human confirmations",
            "Vague confirmation entries that cannot be acted on",
        ],
    )
    issues: list[ValidationIssue] = Field(
        default_factory=list,
        description=(
            "Structured renderer diagnostics with stable error codes, schema "
            "paths, and repair hints. ``reasons`` stays the human-readable "
            "summary; this field carries the code-level detail that "
            "deterministic self-repair (auto_repair) and code-based routing "
            "rely on. Pure addition: absent on legacy reports."
        ),
    )

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
    settings: RunSettingsSpec = KnowledgeField(
        default_factory=lambda: RunSettingsSpec(batches=5, inactive=1, particles=100),
        description="Low-cost OpenMC settings used for a smoke test.",
        concept_id="openmc.execution.smoke_test",
        doc_refs=["openmc.usersguide.settings"],
        retrieval_queries=["OpenMC low-cost smoke test batches inactive particles"],
        common_errors=[
            "Using full-run particle counts for the smoke test",
            "inactive >= batches in the smoke-test settings",
        ],
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


class ResolvedExpertItem(AgentBaseModel):
    question: str = Field(default="", description="Expert question that was answered or deferred.")
    answer: str = Field(default="", description="Expert answer associated with the question.")
    kind: Literal[
        "confirmation",
        "assumption",
        "capability_reason",
        "capability_warning",
        "unknown",
    ] = "unknown"
    status: Literal["resolved", "declined", "unknown"] = "unknown"
    source_round: int = Field(default=0, ge=0)
    semantic_keys: list[str] = Field(default_factory=list)
    reason: str | None = None


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
