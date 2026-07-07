"""Generic 3D-assembly / axial-geometry workflow guard.

Purpose
-------
Prevent 3D axial assembly requirements from being silently collapsed into a
2D unit-height (z=-1..1) slab assembly that *exports* but is physically wrong.

The guard has two halves:

1. :func:`detect_assembly_3d_features` -- a requirement-level detector that
   scans the user/benchmark input text for generic axial-geometry signals
   (axial layers, spacer grids, explicit z ranges, nozzles/plena, control-rod
   insertion, ...).
2. :func:`validate_assembly3d_plan` -- a plan-level validator that turns those
   signals into structured :class:`~openmc_agent.schemas.ValidationIssue`
   entries so the workflow can reflect, downgrade, or ask a human before any
   renderer emits a misleading 2D export.

Design constraints
------------------
* **No benchmark facts.**  This module knows nothing about VERA, material
  densities, spacer dimensions, or any specific benchmark.  It only pattern-
  matches generic axial vocabulary and inspects the generic IR shape.
* **Spectrum of safe outcomes.**  When the IR cannot safely represent the 3D
  structure, the guard routes to ``reflect_plan`` (agent can add an axial
  representation) or ``capability_downgrade`` (drop to skeleton / human
  confirmation) -- it never silently lets a 2D assembly export go through.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from openmc_agent.error_catalog import issue_from_catalog
from openmc_agent.schemas import AxialLayerSpec, ComplexModelSpec, SimulationPlan, ValidationIssue


# ---------------------------------------------------------------------------
# Term registries -- generic axial vocabulary only.  No benchmark constants.
# ---------------------------------------------------------------------------

# Phrases that announce an axial-direction heterogeneous structure.  Matched
# case-insensitively as substrings (all entries are multi-word, so the false-
# positive rate on ordinary reactor text is low).
_AXIAL_GEOMETRY_TERMS: tuple[str, ...] = (
    "3d assembly",
    "3-d assembly",
    "three-dimensional assembly",
    "axial layer",
    "axial reflector",
    "axial reflect",
    "axial heterogen",
    "axial segment",
    "axial region",
    "axial zone",
    "axial division",
    "axial depletion",
    "axial height",
    "fuel stack height",
    "active fuel height",
    "assembly height",
    "active height",
    "guide tube axial",
    "instrument tube axial",
    "burnable absorber axial",
    "control rod insertion",
    "axial insertion",
    "axially varying",
    "axially heterogeneous",
    # Chinese
    "三维",
    "轴向反射",
    "轴向异质",
    "轴向分段",
    "控制棒插入",
)

# Spacer / support grid vocabulary.  These are 3D structural components that
# overlay the pin lattice; they must not be modeled as a full material slab.
_SPACER_GRID_TERMS: tuple[str, ...] = (
    "spacer grid",
    "grid strap",
    "support grid",
    "mixing vane",
    "mid-grid",
    "mid grid",
    "top grid",
    "bottom grid",
    "top nozzle grid",
    "spacer grid strap",
    # Chinese
    "定位格架",
    "格架",
    "支撑格架",
)

# Discrete axial components (non-fuel structural parts stacked along z).  Their
# presence implies the assembly has real axial extent beyond a single slab.
_AXIAL_COMPONENT_TERMS: tuple[str, ...] = (
    "top nozzle",
    "bottom nozzle",
    "top nozzle grid",
    "end plug",
    "end plugs",
    "plenum",
    "plena",
    "fuel stack",
    "axial reflector",
    "axial reflectors",
    "axial reflect",
    "guide tube axial",
    "instrument tube axial",
    # Chinese
    "轴向反射",
    "上管座",
    "下管座",
    "气腔",
)

# Regex patterns that signal an explicit axial z range.  These are stronger
# than keyword matches because they carry physical coordinates.
_EXPLICIT_Z_RANGE_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\bz[\s_-]?(?:min|max)\b", re.IGNORECASE),  # z_min, z-min, zmin, zmax
    re.compile(r"\bz\s*=\s*-?\d", re.IGNORECASE),  # z=10, z = -1
    re.compile(r"\bz[\s-]?range\b", re.IGNORECASE),  # z range, z-range
    re.compile(
        r"\bfrom\s+-?\d+(?:\.\d+)?\s*(?:cm|mm|m)\s+to\s+-?\d+(?:\.\d+)?\s*(?:cm|mm|m)\b",
        re.IGNORECASE,
    ),  # from 10.0 cm to 13.8 cm
    re.compile(
        r"\b(?:axial|height|elevation)\s+(?:region|layer|segment|position|level|extent|range|zone)s?\b",
        re.IGNORECASE,
    ),
    re.compile(r"\b(?:axial|height)\s*=\s*-?\d", re.IGNORECASE),  # axial=10, height=0
)


@dataclass
class Assembly3DFeatureFlags:
    """Requirement-level signals that the problem is genuinely 3D / axial.

    ``matched_terms`` keeps the lower-cased vocabulary hits so a downstream
    reflection prompt can show the model *why* the guard fired.
    """

    has_axial_geometry: bool = False
    has_spacer_grid: bool = False
    has_explicit_z_ranges: bool = False
    has_axial_components: bool = False
    matched_terms: list[str] = field(default_factory=list)


def _coerce_requirement_text(requirement: Any) -> str:
    """Normalize the detector input to a single text blob.

    Accepts a plain string, a ``GraphState``-like dict, or any object exposing
    a ``requirement``/``prompt``/``problem`` attribute.  Falls back to
    ``str(...)`` so the detector never crashes on an unfamiliar input type.
    """
    if requirement is None:
        return ""
    if isinstance(requirement, str):
        return requirement
    if isinstance(requirement, dict):
        parts: list[str] = []
        for key in (
            "requirement",
            "prompt",
            "input",
            "problem",
            "problem_statement",
            "user_request",
        ):
            value = requirement.get(key)
            if isinstance(value, str) and value.strip():
                parts.append(value)
        if not parts:
            for value in requirement.values():
                if isinstance(value, str) and value.strip():
                    parts.append(value)
        return "\n".join(parts)
    for attr in ("requirement", "prompt", "problem", "problem_statement"):
        value = getattr(requirement, attr, None)
        if isinstance(value, str) and value.strip():
            return value
    return str(requirement)


def _match_terms(text_lower: str, terms: tuple[str, ...]) -> list[str]:
    """Return the lower-cased terms that appear in ``text_lower``.

    Terms are matched as substrings; entries are multi-word phrases so this
    stays specific.  Order is preserved and duplicates removed.
    """
    matched: list[str] = []
    for term in terms:
        if term in text_lower and term not in matched:
            matched.append(term)
    return matched


def _match_regex(text: str, patterns: tuple[re.Pattern[str], ...]) -> list[str]:
    matched: list[str] = []
    for pattern in patterns:
        found = pattern.search(text)
        if found:
            token = found.group(0).strip().lower()
            if token and token not in matched:
                matched.append(token)
    return matched


def detect_assembly_3d_features(requirement: Any) -> Assembly3DFeatureFlags:
    """Scan requirement text for generic 3D-assembly / axial-geometry signals.

    The detector is deliberately conservative about *specificity*: a bare
    ``"17x17 assembly"`` triggers nothing, while ``"3D assembly with axial
    layers"`` or an explicit ``"from 10.0 cm to 13.8 cm"`` range does.  Any
    spacer-grid, axial-component, or explicit-z signal implies
    ``has_axial_geometry`` so the plan validator gets a single canonical flag.
    """
    text = _coerce_requirement_text(requirement)
    if not text.strip():
        return Assembly3DFeatureFlags()

    text_lower = text.lower()
    matched: list[str] = []

    axial_hits = _match_terms(text_lower, _AXIAL_GEOMETRY_TERMS)
    spacer_hits = _match_terms(text_lower, _SPACER_GRID_TERMS)
    component_hits = _match_terms(text_lower, _AXIAL_COMPONENT_TERMS)
    z_hits = _match_regex(text, _EXPLICIT_Z_RANGE_PATTERNS)

    matched.extend(axial_hits)
    matched.extend(spacer_hits)
    matched.extend(component_hits)
    matched.extend(z_hits)

    has_spacer_grid = bool(spacer_hits)
    has_axial_components = bool(component_hits)
    has_explicit_z_ranges = bool(z_hits)
    # Any explicit axial signal means the problem is 3D; spacer grids and
    # discrete axial components also imply real axial extent.
    has_axial_geometry = bool(
        axial_hits or has_explicit_z_ranges or has_spacer_grid or has_axial_components
    )

    return Assembly3DFeatureFlags(
        has_axial_geometry=has_axial_geometry,
        has_spacer_grid=has_spacer_grid,
        has_explicit_z_ranges=has_explicit_z_ranges,
        has_axial_components=has_axial_components,
        matched_terms=matched,
    )


__all__ = [
    "Assembly3DFeatureFlags",
    "assembly3d_grid_layer_issues",
    "detect_assembly_3d_features",
    "validate_assembly3d_plan",
]


# ---------------------------------------------------------------------------
# Plan-level validation
# ---------------------------------------------------------------------------

# Tokens that mark an axial layer as a spacer / support grid structure.  Used to
# decide whether the layer must preserve pin/tube through-paths.
_GRID_LAYER_TOKENS: tuple[str, ...] = ("spacer", "grid", "格架")


def _issue(code: str, message: str, *, schema_path: str | None = None) -> ValidationIssue:
    """Build a catalog-backed issue; unknown codes degrade gracefully."""
    return issue_from_catalog(code, message=message, schema_path=schema_path)


def _layer_text(layer: AxialLayerSpec) -> str:
    return " ".join([layer.id, layer.name, layer.purpose]).lower()


def _layer_looks_like_grid(layer: AxialLayerSpec) -> bool:
    """True when the layer's id/name/purpose names it as a spacer / support grid."""
    text = _layer_text(layer)
    return any(token in text for token in _GRID_LAYER_TOKENS)


def _layer_fill_is_material_slab(layer: AxialLayerSpec) -> bool:
    """A grid layer filled with a single material replaces the whole cross section."""
    return layer.fill.type == "material"


def _grid_layer_lacks_through_path(
    layer: AxialLayerSpec, model: ComplexModelSpec
) -> bool:
    """Conservatively detect a grid layer that drops pin/tube through-paths.

    Safe representations currently recognised:
    * ``fill.type == 'lattice'`` together with a ``loading_id`` that resolves to
      a :class:`~openmc_agent.schemas.LatticeLoadingSpec` (a derived lattice
      overlays grid material while keeping every pin/tube universe in place).

    Everything else (a material slab, a bare universe, or a lattice fill with no
      loading override) cannot be proven to preserve the through-path, so the
      guard fires and asks for confirmation.
    """
    if layer.fill.type in {"material", "universe"}:
        return True
    if layer.fill.type == "lattice":
        if layer.loading_id is None:
            return True
        loading_ids = {loading.id for loading in model.lattice_loadings}
        return layer.loading_id not in loading_ids
    # void / unknown fill types: do not claim a through-path defect here.
    return False


def _plan_has_axial_representation(plan: SimulationPlan) -> bool:
    """True when the plan carries a real axial structure, not a 2D slab.

    Today the only supported axial representation is a non-empty
    ``complex_model.core.axial_layers`` list (each layer contributes a real z
    slab and is rendered by the axial root path).  Other shapes (e.g. a future
    dedicated 3D-assembly root) should be added here as they become supported.
    """
    model = plan.complex_model
    if model is None:
        return False
    return model.core is not None and bool(model.core.axial_layers)


def assembly3d_grid_layer_issues(model: ComplexModelSpec) -> list[ValidationIssue]:
    """Plan-level spacer-grid layer guards (requirement-agnostic).

    Shared by plan validation and renderer ``can_render`` so a hand-edited plan
    that introduces a grid slab is caught at both stages.  Only the grid-layer
    slab / through-path checks live here; the requirement-vs-plan axial
    representation checks need the requirement text and stay in
    :func:`validate_assembly3d_plan`.
    """
    issues: list[ValidationIssue] = []
    if model.core is None:
        return issues
    for layer in model.core.axial_layers:
        if not _layer_looks_like_grid(layer):
            continue
        schema_path = f"complex_model.core.axial_layers.{layer.id}.fill"
        if _layer_fill_is_material_slab(layer):
            issues.append(
                _issue(
                    "assembly3d.spacer_grid_material_slab",
                    (
                        f"axial layer {layer.id!r} is a spacer/grid layer but its "
                        f"fill is a single material ({layer.fill.id!r}), which "
                        "replaces the whole assembly cross section and truncates "
                        "fuel pins, cladding, guide tubes and instrument tubes. "
                        "Model the grid as an overlay / derived lattice / "
                        "homogenized open-region treatment that preserves pin and "
                        "tube through-paths, not as a full material slab."
                    ),
                    schema_path=schema_path,
                )
            )
            # A material slab is also an extreme through-path loss.
            issues.append(
                _issue(
                    "assembly3d.pin_through_path_missing",
                    (
                        f"grid layer {layer.id!r} fill is a single material, so "
                        "fuel/tube universes do not continue through the grid "
                        "z-range; pin/tube through-path is missing."
                    ),
                    schema_path=schema_path,
                )
            )
        elif _grid_layer_lacks_through_path(layer, model):
            issues.append(
                _issue(
                    "assembly3d.pin_through_path_missing",
                    (
                        f"grid layer {layer.id!r} does not reference a pin/tube "
                        "lattice or a derived overlay lattice; fuel/tube "
                        "through-paths may be truncated across the grid z-range."
                    ),
                    schema_path=schema_path,
                )
            )
    return issues


def validate_assembly3d_plan(
    plan: SimulationPlan,
    *,
    requirement: Any = "",
) -> list[ValidationIssue]:
    """Validate ``plan`` against the requirement's 3D / axial signals.

    Returns a list of structured issues (empty when nothing is wrong).  The
    validator is the single source of truth for the four ``assembly3d.*`` codes
    and is called from both plan validation (early, with the requirement text)
    and renderer diagnostics (plan-only re-check, requirement-agnostic).

    Issue catalogue:

    * ``assembly3d.axial_layers_required`` -- the requirement is axial but the
      plan has no ``core.axial_layers`` and is still a 2D assembly root.
    * ``assembly3d.default_z_extent_for_axial_problem`` -- an explicit z range is
      requested yet the plan would render the default ``z=-1..1`` unit slab.
    * ``assembly3d.spacer_grid_material_slab`` -- a grid layer is filled with a
      single material, replacing the whole cross section.
    * ``assembly3d.pin_through_path_missing`` -- a grid layer cannot be shown to
      preserve fuel-pin / guide-tube / instrument-tube through-paths.
    """
    issues: list[ValidationIssue] = []
    model = plan.complex_model
    if model is None or model.kind != "assembly":
        return issues

    flags = detect_assembly_3d_features(requirement)
    has_axial_in_plan = _plan_has_axial_representation(plan)

    # Requirement is 3D but the plan collapsed to a 2D assembly root.
    if flags.has_axial_geometry and not has_axial_in_plan:
        issues.append(
            _issue(
                "assembly3d.axial_layers_required",
                (
                    "requirement describes 3D axial geometry "
                    f"({', '.join(flags.matched_terms) or 'axial signals'}), but the "
                    "plan has no core.axial_layers / axial root; a 2D assembly root "
                    "cannot represent axial heterogeneity. Add core.axial_layers or "
                    "mark the plan as a non-exportable skeleton."
                ),
                schema_path="complex_model.core.axial_layers",
            )
        )
        if flags.has_explicit_z_ranges:
            issues.append(
                _issue(
                    "assembly3d.default_z_extent_for_axial_problem",
                    (
                        "requirement gives explicit axial z ranges, but the plan "
                        "has no axial_layers so the renderer would emit the default "
                        "z=-1..1 unit slab; a 3D axial problem cannot be represented "
                        "by a default unit-height slab."
                    ),
                    schema_path="complex_model.core.axial_layers",
                )
            )

    # Plan-level grid-layer guards (slab + through-path). Requirement-agnostic
    # so the renderer can re-run the same checks from can_render.
    if has_axial_in_plan:
        issues.extend(assembly3d_grid_layer_issues(model))

    return issues
