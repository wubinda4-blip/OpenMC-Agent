"""Patch schemas for incremental plan building (Phase 2).

Each patch type is a self-contained Pydantic model that captures one
*component* of a future SimulationPlan (materials, universes, pin map, axial
layers, ...).  Patches are designed to be:

* **Small** — each patch is a few KB, not a 25 KB monolithic JSON.
* **Independently validatable** — a validator can check a patch in isolation
  (plus lightweight cross-references via :class:`PatchValidationContext`).
* **Assemblable** — a future deterministic assembler (Phase 3) will merge
  validated patches into a complete ``SimulationPlan``.

No OpenMC, no renderer, no LLM dependencies.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from openmc_agent.schemas import AgentBaseModel


PatchType = Literal[
    "facts",
    "materials",
    "universes",
    "pin_map",
    "axial_layers",
    "axial_overlays",
    "settings",
]


class _PatchBase(AgentBaseModel):
    """Common base for all patch models (extra='forbid', strip whitespace)."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


# ---------------------------------------------------------------------------
# FactsPatch
# ---------------------------------------------------------------------------


class FactsPatch(_PatchBase):
    """Extracted benchmark / geometry / variant facts.

    Does NOT generate any OpenMC plan content; only records what the
    requirement / benchmark document tells us about the problem shape.
    """

    patch_type: Literal["facts"] = "facts"
    benchmark_id: str | None = None
    selected_variant: str | None = None

    geometry_type: str | None = None
    lattice_size: tuple[int, int] | None = None
    pin_pitch_cm: float | None = None
    assembly_pitch_cm: float | None = None

    has_axial_geometry: bool = False
    has_spacer_grids: bool = False
    has_special_pin_map: bool = False

    active_fuel_region_cm: tuple[float, float] | None = None
    axial_domain_cm: tuple[float, float] | None = None

    expected_spacer_grid_count: int | None = None
    expected_pin_count: int | None = None
    expected_guide_tube_count: int | None = None
    expected_instrument_tube_count: int | None = None
    expected_pyrex_count: int | None = None
    expected_thimble_plug_count: int | None = None

    material_roles: list[str] = Field(default_factory=list)
    missing_facts: list[str] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)
    source_notes: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# MaterialsPatch
# ---------------------------------------------------------------------------


class MaterialSpecPatch(_PatchBase):
    """A single material entry in a MaterialsPatch."""

    material_id: str
    name: str
    role: str
    density_g_cm3: float | None = None
    temperature_K: float | None = None
    composition: dict[str, float] = Field(default_factory=dict)
    composition_basis: Literal["atom_frac", "weight_frac", "unknown"] = "unknown"
    composition_status: Literal[
        "confirmed",
        "approximate",
        "needs_library",
        "needs_confirmation",
        "placeholder",
    ] = "needs_confirmation"
    source_note: str | None = None
    warnings: list[str] = Field(default_factory=list)


class MaterialsPatch(_PatchBase):
    """Material catalog patch."""

    patch_type: Literal["materials"] = "materials"
    materials: list[MaterialSpecPatch]
    assumptions: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# UniversesPatch
# ---------------------------------------------------------------------------


class CellLayerPatch(_PatchBase):
    """A concentric cell layer inside a universe."""

    id: str
    role: str
    material_id: str | None = None
    region_kind: Literal[
        "cylinder",
        "annulus",
        "box",
        "background",
        "unknown",
    ] = "unknown"
    r_min_cm: float | None = None
    r_max_cm: float | None = None
    fill_universe_id: str | None = None
    protected_through_path: bool = False


class UniverseSpecPatch(_PatchBase):
    """A single universe definition (fuel pin, guide tube, pyrex rod, ...)."""

    universe_id: str
    kind: Literal[
        "fuel_pin",
        "guide_tube",
        "instrument_tube",
        "pyrex_rod",
        "thimble_plug",
        "water_cell",
        "custom",
    ]
    cells: list[CellLayerPatch]
    source_note: str | None = None
    assumptions: list[str] = Field(default_factory=list)


class UniversesPatch(_PatchBase):
    """Universe catalog patch."""

    patch_type: Literal["universes"] = "universes"
    universes: list[UniverseSpecPatch]


# ---------------------------------------------------------------------------
# PinMapPatch
# ---------------------------------------------------------------------------


class CoordinateConvention(_PatchBase):
    """Coordinate system convention for pin map positions."""

    index_base: Literal[0, 1] = 0
    row_origin: Literal["top", "bottom", "unknown"] = "top"
    col_origin: Literal["left", "right", "unknown"] = "left"
    ordering: Literal["row_col", "x_y", "unknown"] = "row_col"


class PinMapPatch(_PatchBase):
    """Pin map replacement rules (not a full expanded lattice).

    Instead of a 17×17 = 289-entry ``universe_pattern``, this patch only lists
    the *special* positions (guide tubes, pyrex rods, ...).  The default
    universe fills everything else.  The assembler (Phase 3) will expand this
    into the full lattice.
    """

    patch_type: Literal["pin_map"] = "pin_map"
    variant: str | None = None
    lattice_size: tuple[int, int]
    default_universe_id: str
    coordinate_convention: CoordinateConvention = Field(
        default_factory=CoordinateConvention
    )

    guide_tube_coords: list[tuple[int, int]] = Field(default_factory=list)
    instrument_tube_coords: list[tuple[int, int]] = Field(default_factory=list)
    pyrex_rod_coords: list[tuple[int, int]] = Field(default_factory=list)
    thimble_plug_coords: list[tuple[int, int]] = Field(default_factory=list)
    water_cell_coords: list[tuple[int, int]] = Field(default_factory=list)

    assumptions: list[str] = Field(default_factory=list)
    source_note: str | None = None


# ---------------------------------------------------------------------------
# AxialLayersPatch
# ---------------------------------------------------------------------------


class AxialLayerPatchItem(_PatchBase):
    """A single axial layer in an AxialLayersPatch."""

    layer_id: str
    role: Literal[
        "lower_nozzle",
        "lower_end_plug",
        "lower_plenum",
        "active_fuel",
        "gas_gap",
        "upper_plenum",
        "upper_end_plug",
        "upper_nozzle",
        "core_plate",
        "reflector",
        "custom",
    ] = "custom"
    z_min_cm: float | None = None
    z_max_cm: float | None = None
    fill_type: Literal["lattice", "material", "universe", "void", "unknown"] = "unknown"
    fill_id: str | None = None
    requires_human_confirmation: bool = False
    assumptions: list[str] = Field(default_factory=list)
    source_note: str | None = None


class AxialLayersPatch(_PatchBase):
    """Axial layer segmentation patch."""

    patch_type: Literal["axial_layers"] = "axial_layers"
    layers: list[AxialLayerPatchItem]
    axial_domain_cm: tuple[float, float] | None = None


# ---------------------------------------------------------------------------
# AxialOverlaysPatch
# ---------------------------------------------------------------------------


class AxialOverlayPatchItem(_PatchBase):
    """A single axial overlay (e.g. spacer grid) in an AxialOverlaysPatch."""

    overlay_id: str
    overlay_kind: Literal[
        "spacer_grid",
        "support_plate",
        "absorber_insert",
        "custom",
    ]
    z_min_cm: float | None = None
    z_max_cm: float | None = None
    target_lattice_id: str | None = None
    material_id: str | None = None
    geometry_mode: Literal[
        "skeleton",
        "homogenized_open_region",
        "annular_shell",
        "explicit_bars",
        "volume_fraction_calibrated",
    ] = "skeleton"
    through_path_preserved: bool | None = None
    volume_fraction: float | None = None
    effective_density_g_cm3: float | None = None
    requires_human_confirmation: bool = False
    assumptions: list[str] = Field(default_factory=list)
    source_note: str | None = None


class AxialOverlaysPatch(_PatchBase):
    """Axial overlays patch (spacer grids, support plates, ...)."""

    patch_type: Literal["axial_overlays"] = "axial_overlays"
    overlays: list[AxialOverlayPatchItem]


# ---------------------------------------------------------------------------
# SettingsPatch
# ---------------------------------------------------------------------------


class SettingsPatch(_PatchBase):
    """Execution settings strategy patch."""

    patch_type: Literal["settings"] = "settings"
    source_strategy: Literal[
        "active_fuel_box",
        "assembly_box",
        "manual",
        "unknown",
    ] = "active_fuel_box"
    source_requires_fissionable_constraint: bool = True
    plot_strategy: Literal[
        "full_assembly",
        "quarter_assembly",
        "manual",
        "none",
    ] = "full_assembly"
    cross_sections_runtime_required: bool = True
    tallies_required_for_smoke_test: bool = False
    assumptions: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Patch parsing helpers
# ---------------------------------------------------------------------------

_PATCH_MODELS: dict[str, type[BaseModel]] = {
    "facts": FactsPatch,
    "materials": MaterialsPatch,
    "universes": UniversesPatch,
    "pin_map": PinMapPatch,
    "axial_layers": AxialLayersPatch,
    "axial_overlays": AxialOverlaysPatch,
    "settings": SettingsPatch,
}


class PatchParseError(Exception):
    """Raised when a patch content dict cannot be parsed into its schema."""

    def __init__(self, patch_type: str, message: str, details: Any = None) -> None:
        self.patch_type = patch_type
        self.details = details
        super().__init__(f"[{patch_type}] {message}")


def parse_patch_content(patch_type: str, content: dict[str, Any]) -> BaseModel:
    """Parse a content dict into the appropriate patch model.

    Parameters
    ----------
    patch_type
        One of the :data:`PatchType` literal values.
    content
        Raw content dict (e.g. from ``PlanPatchEnvelope.content``).

    Returns
    -------
    BaseModel
        The parsed patch model.

    Raises
    ------
    PatchParseError
        If ``patch_type`` is unknown or the content fails schema validation.
    """
    model_cls = _PATCH_MODELS.get(patch_type)
    if model_cls is None:
        raise PatchParseError(patch_type, f"unknown patch_type {patch_type!r}")
    try:
        return model_cls.model_validate(content)
    except Exception as exc:
        raise PatchParseError(
            patch_type,
            f"content does not match {model_cls.__name__} schema: {exc}",
            details=str(exc),
        ) from exc


def parse_patch_envelope(envelope: Any) -> BaseModel:
    """Parse a :class:`~openmc_agent.plan_builder.state.PlanPatchEnvelope`-like object.

    Accepts a ``PlanPatchEnvelope`` instance or a dict with ``patch_type`` and
    ``content`` keys.
    """
    patch_type: str | None = None
    content: dict[str, Any] | None = None
    if hasattr(envelope, "patch_type") and hasattr(envelope, "content"):
        patch_type = envelope.patch_type
        content = envelope.content
    elif isinstance(envelope, dict):
        patch_type = envelope.get("patch_type")
        content = envelope.get("content")
    if patch_type is None or content is None:
        raise PatchParseError(
            str(patch_type or "unknown"),
            "envelope must have patch_type and content",
        )
    return parse_patch_content(patch_type, content)


def normalized_coords(
    coords: list[tuple[int, int]],
    convention: CoordinateConvention,
    lattice_size: tuple[int, int],
) -> list[tuple[int, int]]:
    """Normalize coordinates to 0-indexed (row, col) within lattice bounds.

    Does not validate bounds; callers should check the output against
    ``lattice_size`` separately.
    """
    nx, ny = lattice_size
    result: list[tuple[int, int]] = []
    for row, col in coords:
        r = row - convention.index_base
        c = col - convention.index_base
        result.append((r, c))
    return result


__all__ = [
    "PatchType",
    "FactsPatch",
    "MaterialSpecPatch",
    "MaterialsPatch",
    "CellLayerPatch",
    "UniverseSpecPatch",
    "UniversesPatch",
    "CoordinateConvention",
    "PinMapPatch",
    "AxialLayerPatchItem",
    "AxialLayersPatch",
    "AxialOverlayPatchItem",
    "AxialOverlaysPatch",
    "SettingsPatch",
    "PatchParseError",
    "parse_patch_content",
    "parse_patch_envelope",
    "normalized_coords",
]
