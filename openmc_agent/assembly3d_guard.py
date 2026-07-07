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
    "assembly3d_overlay_issues",
    "axial_overlay_issues",
    "detect_assembly_3d_features",
    "layer_is_spacer_grid_slab_candidate",
    "layer_mentions_grid",
    "validate_assembly3d_plan",
]


# ---------------------------------------------------------------------------
# Plan-level validation
# ---------------------------------------------------------------------------

# Generic single tokens that merely *mention* a grid somewhere in the layer
# text. A mention alone is weak evidence: "Fuel region with grids" only says
# grids live somewhere inside the region, not that the layer itself is a grid.
_GRID_MENTION_TOKENS: tuple[str, ...] = ("spacer", "grid", "格架")

# Multi-word phrases that explicitly name a layer as a spacer/support grid slab
# (the layer *is* the grid). These are strong evidence and only matched against
# id + name (purpose is too free-form: it routinely says "with embedded grids").
_GRID_SLAB_PHRASES: tuple[str, ...] = (
    "spacer grid",
    "support grid",
    "grid strap",
    "grid slab",
    "spacing grid",
    "grid layer",
    "定位格架",
    "格架",
)

# A real spacer/support grid is a thin structural band (typically a few cm or
# less). A layer thicker than this is almost certainly a fuel/active region that
# merely contains grids, not a grid slab. Conservative upper bound.
_MAX_GRID_SLAB_THICKNESS_CM: float = 5.0


def _issue(code: str, message: str, *, schema_path: str | None = None) -> ValidationIssue:
    """Build a catalog-backed issue; unknown codes degrade gracefully."""
    return issue_from_catalog(code, message=message, schema_path=schema_path)


def _layer_text(layer: AxialLayerSpec) -> str:
    return " ".join([layer.id, layer.name, layer.purpose]).lower()


def _layer_id_name(layer: AxialLayerSpec) -> str:
    """id + name only. ``purpose`` is excluded from slab-candidate detection
    because it is free-form and routinely mentions grids that live *inside* a
    fuel region (e.g. VERA3 'Active fuel region with embedded grid sub-layers').
    """
    return f"{layer.id} {layer.name}".lower()


def layer_mentions_grid(layer: AxialLayerSpec) -> bool:
    """True when any of the layer's text fields mention a grid/spacer.

    This is a weak, text-only signal. It does NOT imply the layer itself is a
    grid slab; use :func:`layer_is_spacer_grid_slab_candidate` for that.
    """
    text = _layer_text(layer)
    return any(token in text for token in _GRID_MENTION_TOKENS)


def layer_is_spacer_grid_slab_candidate(layer: AxialLayerSpec) -> bool:
    """True when the layer itself appears to be a spacer/support grid slab.

    Conservative detection that avoids the VERA3 false positive (a 365 cm
    'Fuel region with grids' lattice layer is NOT a grid slab). A layer is a
    candidate only when:

    * its id/name carry an explicit grid-slab phrase (``spacer grid``, ``support
      grid``, ``grid strap``, ...); or
    * it is a *thin* z-band (<= ``_MAX_GRID_SLAB_THICKNESS_CM``) whose id/name
      mention grid/spacer.

    ``purpose`` is deliberately ignored here: it routinely says things like
    'fuel region with embedded grids', which describes grids *inside* the layer,
    not the layer being a grid. A plain lattice fill also preserves pin
    through-paths on its own, so a tall lattice-filled 'fuel region' is never a
    grid-slab candidate.
    """
    id_name = _layer_id_name(layer)
    if any(phrase in id_name for phrase in _GRID_SLAB_PHRASES):
        return True
    thickness = layer.z_max_cm - layer.z_min_cm
    if thickness <= _MAX_GRID_SLAB_THICKNESS_CM and any(
        token in id_name for token in _GRID_MENTION_TOKENS
    ):
        return True
    return False


def _layer_fill_is_material_slab(layer: AxialLayerSpec) -> bool:
    """A grid layer filled with a single material replaces the whole cross section."""
    return layer.fill.type == "material"


def _grid_layer_lacks_through_path(
    layer: AxialLayerSpec, model: ComplexModelSpec
) -> bool:
    """Detect a grid-slab layer that drops pin/tube through-paths.

    A plain ``lattice`` fill already preserves through-paths (fuel/guide/
    instrument-tube universes continue through the lattice), so it is *not*
    flagged here even when ``loading_id`` is None. The ``loading_id`` overlay is
    only needed when additional grid material must be layered on top of the
    lattice; absent it, the layer is simply 'more lattice', which is safe.

    Flagged cases:

    * ``fill.type`` is ``material``/``universe`` (a replacement that truncates
      every pin/tube); or
    * ``fill.type == 'lattice'`` with a ``loading_id`` that does not resolve to
      a declared :class:`LatticeLoadingSpec` (a dangling derived-lattice ref).
    """
    if layer.fill.type in {"material", "universe"}:
        return True
    if layer.fill.type == "lattice":
        if layer.loading_id is None:
            # Plain lattice fill = pins continue through. Not a through-path loss.
            return False
        loading_ids = {loading.id for loading in model.lattice_loadings}
        return layer.loading_id not in loading_ids
    # void / unknown fill types: do not claim a through-path defect here.
    return False


def _plan_has_axial_representation(plan: SimulationPlan) -> bool:
    """True when the plan carries a real axial structure, not a 2D slab.

    Today the supported axial representations are a non-empty
    ``complex_model.core.axial_layers`` list or an ``axial_overlays`` list;
    each contributes real structure handled by the axial root path / overlay
    validation. Other shapes should be added here as they become supported.
    """
    model = plan.complex_model
    if model is None:
        return False
    return (
        model.core is not None
        and (bool(model.core.axial_layers) or bool(model.core.axial_overlays))
    )


def assembly3d_grid_layer_issues(model: ComplexModelSpec) -> list[ValidationIssue]:
    """Plan-level spacer-grid *slab* layer guards (requirement-agnostic).

    Shared by plan validation and renderer ``can_render`` so a hand-edited plan
    that introduces a grid slab is caught at both stages. Only genuine
    spacer-grid slab layers (see :func:`layer_is_spacer_grid_slab_candidate`)
    are examined; a tall fuel region that merely mentions grids is left alone.
    """
    issues: list[ValidationIssue] = []
    if model.core is None:
        return issues
    for layer in model.core.axial_layers:
        if not layer_is_spacer_grid_slab_candidate(layer):
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
                        "Model the grid as an overlay (core.axial_overlays) / "
                        "derived lattice that preserves pin and tube "
                        "through-paths, not as a full material slab."
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
                        f"grid layer {layer.id!r} references a derived loading "
                        f"({layer.loading_id!r}) that is not declared in "
                        "lattice_loadings; fuel/tube through-paths cannot be "
                        "confirmed across the grid z-range."
                    ),
                    schema_path=schema_path,
                )
            )
    return issues


# ---------------------------------------------------------------------------
# Axial overlay validation (spacer grids expressed as overlays, not slabs)
# ---------------------------------------------------------------------------

# Overlay geometry modes the current renderer can actually turn into geometry.
# Empty today: every non-skeleton overlay downgrades instead of producing fake
# geometry. Future Level 1+ renderers add modes here.
_RENDERER_SUPPORTED_OVERLAY_MODES: frozenset[str] = frozenset()


def _plan_has_spacer_grid_overlay(model: ComplexModelSpec) -> bool:
    return (
        model.core is not None
        and any(o.overlay_kind == "spacer_grid" for o in model.core.axial_overlays)
    )


def _assembly_axial_domain(model: ComplexModelSpec) -> tuple[float, float] | None:
    """Outer (z_min, z_max) spanned by core.axial_layers, or None if no layers."""
    if model.core is None or not model.core.axial_layers:
        return None
    z_mins = [layer.z_min_cm for layer in model.core.axial_layers]
    z_maxs = [layer.z_max_cm for layer in model.core.axial_layers]
    return (min(z_mins), max(z_maxs))


def _overlay_intersects_domain(
    z_min: float, z_max: float, domain: tuple[float, float]
) -> bool:
    """Two z-intervals overlap when each starts before the other ends."""
    return z_min < domain[1] and z_max > domain[0]


def axial_overlay_issues(
    model: ComplexModelSpec, flags: Assembly3DFeatureFlags
) -> list[ValidationIssue]:
    """Validate ``core.axial_overlays`` and the requirement's spacer-grid signal.

    Returns structured issues (empty when nothing is wrong). Combines the
    requirement-agnostic checks from :func:`assembly3d_overlay_issues` with the
    requirement-aware ``spacer_grid_overlay_required`` code.
    """
    issues: list[ValidationIssue] = []
    if model.core is None:
        return issues

    overlays = model.core.axial_overlays
    has_spacer_overlay = any(o.overlay_kind == "spacer_grid" for o in overlays)

    # Requirement names spacer grids but the plan has no overlay at all.
    # A safely-handled grid slab (loading_id resolves) also counts.
    if flags.has_spacer_grid and not has_spacer_overlay:
        safe_slab_present = any(
            layer_is_spacer_grid_slab_candidate(layer)
            and not _grid_layer_lacks_through_path(layer, model)
            for layer in model.core.axial_layers
        )
        if not safe_slab_present:
            issues.append(
                _issue(
                    "assembly3d.spacer_grid_overlay_required",
                    (
                        "requirement describes spacer/support grids but the plan "
                        "has no core.axial_overlays spacer_grid entry. A "
                        "fuel-region layer purpose comment is not a safe grid "
                        "representation. Add a spacer_grid overlay "
                        "(geometry_mode='skeleton' + requires_human_confirmation "
                        "when grid z-positions are unknown), or mark the plan "
                        "non-exportable."
                    ),
                    schema_path="complex_model.core.axial_overlays",
                )
            )

    issues.extend(assembly3d_overlay_issues(model))
    return issues


def assembly3d_overlay_issues(model: ComplexModelSpec) -> list[ValidationIssue]:
    """Requirement-agnostic axial-overlay guards.

    Shared by plan validation and renderer ``can_render`` so a hand-edited plan
    that introduces an overlay is caught at both stages. Codes:

    * ``assembly3d.axial_overlay_invalid_range`` -- an overlay z-range is
      missing (non-skeleton), inverted, or disjoint from the axial domain.
    * ``assembly3d.axial_overlay_missing_target`` -- a non-skeleton overlay
      lacks a ``target_lattice_id`` that resolves to a declared lattice.
    * ``assembly3d.axial_overlay_requires_renderer_support`` -- an overlay asks
      for a geometry_mode the current renderer cannot produce (or a skeleton
      overlay, which is a review-only downgrade).
    * ``assembly3d.pin_through_path_missing`` -- a non-skeleton spacer overlay
      lacks through-path evidence.
    """
    issues: list[ValidationIssue] = []
    if model.core is None:
        return issues

    lattice_ids = {lat.id for lat in model.lattices}
    domain = _assembly_axial_domain(model)

    for overlay in model.core.axial_overlays:
        base = f"complex_model.core.axial_overlays.{overlay.id}"

        # 1. Range validity (skip for skeleton overlays: z may be unknown).
        if overlay.geometry_mode != "skeleton":
            z_min = overlay.z_min_cm
            z_max = overlay.z_max_cm
            range_bad = False
            if z_min is None or z_max is None:
                range_bad = True
            elif z_min >= z_max:
                range_bad = True
            elif domain is not None and not _overlay_intersects_domain(
                z_min, z_max, domain
            ):
                range_bad = True
            if range_bad:
                issues.append(
                    _issue(
                        "assembly3d.axial_overlay_invalid_range",
                        (
                            f"axial overlay {overlay.id!r} has an invalid or "
                            "domain-disjoint z-range "
                            f"(z_min={z_min}, z_max={z_max}, "
                            f"assembly domain={domain}). Provide a valid "
                            "z_min_cm < z_max_cm intersecting the axial layers, "
                            "or set geometry_mode='skeleton'."
                        ),
                        schema_path=base,
                    )
                )

        # 2. Target lattice resolution for non-skeleton overlays that need one.
        grid_like = overlay.overlay_kind in {"spacer_grid", "support_plate"}
        if (
            grid_like
            and overlay.geometry_mode != "skeleton"
            and (not overlay.target_lattice_id or overlay.target_lattice_id not in lattice_ids)
        ):
            issues.append(
                _issue(
                    "assembly3d.axial_overlay_missing_target",
                    (
                        f"axial overlay {overlay.id!r} (geometry_mode="
                        f"{overlay.geometry_mode!r}) must reference an existing "
                        "target_lattice_id so the renderer knows which lattice's "
                        "pins/tubes continue through the overlay."
                    ),
                    schema_path=f"{base}.target_lattice_id",
                )
            )

        # 3. Renderer support. The current renderer implements no overlay
        #    geometry mode. A skeleton overlay is the planner's own declaration
        #    that no geometry is expected; document it as a review-only downgrade
        #    rather than a geometric error.
        if overlay.geometry_mode == "skeleton":
            issues.append(
                _issue(
                    "assembly3d.axial_overlay_requires_renderer_support",
                    (
                        f"spacer/overlay {overlay.id!r} is declared as "
                        "geometry_mode='skeleton' (review-only): the IR captures "
                        "the grid but no renderer turns it into geometry yet, so "
                        "the model stays a non-exportable skeleton pending human "
                        "confirmation."
                    ),
                    schema_path=base,
                )
            )
        elif overlay.geometry_mode not in _RENDERER_SUPPORTED_OVERLAY_MODES:
            issues.append(
                _issue(
                    "assembly3d.axial_overlay_requires_renderer_support",
                    (
                        f"axial overlay {overlay.id!r} requests geometry_mode="
                        f"{overlay.geometry_mode!r}, which the current renderer "
                        "cannot produce. Downgrade geometry_mode to 'skeleton' or "
                        "wait for the Level 1+ overlay renderer; do not emit a "
                        "material slab to fake it."
                    ),
                    schema_path=base,
                )
            )

        # 4. Through-path evidence for non-skeleton spacer grids.
        if (
            overlay.overlay_kind == "spacer_grid"
            and overlay.geometry_mode != "skeleton"
            and overlay.through_path_preserved is not True
            and not overlay.requires_human_confirmation
        ):
            issues.append(
                _issue(
                    "assembly3d.pin_through_path_missing",
                    (
                        f"spacer_grid overlay {overlay.id!r} does not declare "
                        "through_path_preserved=True (and no human confirmation), "
                        "so pin/tube through-paths across the grid z-range cannot "
                        "be confirmed."
                    ),
                    schema_path=f"{base}.through_path_preserved",
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
    validator is the single source of truth for the ``assembly3d.*`` codes
    and is called from both plan validation (early, with the requirement text)
    and renderer diagnostics (plan-only re-check, requirement-agnostic).

    Issue catalogue:

    * ``assembly3d.axial_layers_required`` -- the requirement is axial but the
      plan has no ``core.axial_layers`` / ``core.axial_overlays`` and is still a
      2D assembly root.
    * ``assembly3d.default_z_extent_for_axial_problem`` -- an explicit z range is
      requested yet the plan would render the default ``z=-1..1`` unit slab.
    * ``assembly3d.spacer_grid_material_slab`` -- a grid slab layer is filled
      with a single material, replacing the whole cross section.
    * ``assembly3d.pin_through_path_missing`` -- a grid slab layer cannot be
      shown to preserve fuel-pin / guide-tube / instrument-tube through-paths,
      or a non-skeleton spacer overlay lacks through-path evidence.
    * ``assembly3d.spacer_grid_overlay_required`` -- requirement names spacer
      grids but the plan has no overlay and no safe slab.
    * ``assembly3d.axial_overlay_invalid_range`` -- overlay z-range invalid or
      disjoint from the axial domain.
    * ``assembly3d.axial_overlay_missing_target`` -- non-skeleton overlay lacks
      a resolvable target lattice.
    * ``assembly3d.axial_overlay_requires_renderer_support`` -- overlay asks for
      a geometry mode the renderer cannot produce (or skeleton review-only).
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
                    "plan has no core.axial_layers / axial_overlays; a 2D assembly root "
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

    # Plan-level grid-layer slab guards (slab + through-path). Requirement-agnostic
    # so the renderer can re-run the same checks from can_render.
    issues.extend(assembly3d_grid_layer_issues(model))

    # Axial overlay validation (spacer grids as overlays, not slabs).
    issues.extend(axial_overlay_issues(model, flags))

    return issues
