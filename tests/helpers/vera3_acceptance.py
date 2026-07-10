"""VERA3 benchmark acceptance helpers (TEST-ONLY).

Nothing in this module is imported by production code. It lives under
``tests/helpers/`` so VERA3-specific facts (pin coordinates, grid z-positions,
axial layer ranges) stay out of the planner/renderer/guard. The numbers all
come from ``tests/fixtures/vera3_reference.json``, which is transcribed from
``Input/VERA3_problem.md``.

Three responsibilities:

1. :func:`load_vera3_reference` -- load the reference fixture.
2. :func:`validate_vera3_plan_structure` -- structural acceptance check that
   returns benchmark-specific :class:`BenchmarkIssue` entries (kept separate
   from the generic ``assembly3d.*`` taxonomy).
3. :func:`build_vera3_like_plan` -- a deterministic, renderable VERA3-like
   :class:`SimulationPlan` built purely from the reference, used by the
   deterministic tests (no LLM call).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from openmc_agent.assembly3d_guard import validate_assembly3d_plan
from openmc_agent.axial_overlay import classify_material_role
from openmc_agent.schemas import (
    AxialLayerSpec,
    AxialOverlaySpec,
    CellSpec,
    ComplexMaterialSpec,
    ComplexModelSpec,
    CoreSpec,
    ExecutionCheckSpec,
    LatticeSpec,
    LatticeLoadingSpec,
    NuclideSpec,
    PlotSpec,
    RegionSpec,
    RenderCapabilityReport,
    RunSettingsSpec,
    SimulationPlan,
    SurfaceSpec,
    UniverseSpec,
)

REFERENCE_PATH = Path("tests/fixtures/vera3_reference.json")
CONTRACT_PATH = Path("tests/fixtures/vera3_geometry_contract.json")


# ---------------------------------------------------------------------------
# Benchmark issue type
# ---------------------------------------------------------------------------


@dataclass
class BenchmarkIssue:
    code: str
    severity: Literal["error", "warning", "info"]
    message: str
    expected: Any = None
    actual: Any = None
    path: str | None = None

    def __str__(self) -> str:
        return f"{self.code}: {self.message}"


# ---------------------------------------------------------------------------
# Reference loading + coordinate helpers
# ---------------------------------------------------------------------------


def load_vera3_reference(path: Path | str = REFERENCE_PATH) -> dict[str, Any]:
    """Load the VERA3 reference fixture (test-only)."""
    return json.loads(Path(path).read_text(encoding="utf-8"))


def load_vera3_geometry_contract(path: Path | str = CONTRACT_PATH) -> dict[str, Any]:
    """Load the canonical VERA3 geometry contract (test-only)."""
    return json.loads(Path(path).read_text(encoding="utf-8"))


def to_0_indexed(pos_1based: list[int] | tuple[int, int]) -> tuple[int, int]:
    """Convert a document 1-based (row, col) to internal 0-indexed (row, col).

    Document convention (per VERA3 input): row 1 is the top, so 0-indexed row
    ``r-1`` of the lattice ``universe_pattern`` corresponds to document row ``r``.
    """
    r, c = pos_1based[0], pos_1based[1]
    return (r - 1, c - 1)


def _pin_kind(universe_id: str, universe_name: str = "") -> str:
    """Heuristically classify a universe id/name into a pin kind.

    Used only to map a plan's lattice back to pin-type coordinates for the
    benchmark acceptance check. Order matters: 'instrument' before 'guide'
    (instrument tubes are also tube-like), and 'plug' before generic checks.
    """
    text = f"{universe_id} {universe_name}".lower()
    if "pyrex" in text or "burnable" in text or "_ba_" in text or text.endswith("_ba"):
        return "P"
    if "plug" in text or "thimble_plug" in text:
        return "T"
    if "instrument" in text or "_inst" in text or text.endswith("inst"):
        return "I"
    if "guide" in text or "_gt" in text or text.endswith("gt"):
        return "G"
    return "F"


def collect_base_lattice_counts(plan: SimulationPlan | dict[str, Any]) -> dict[str, int]:
    """Count base-lattice universe ids without applying axial insert loadings."""
    model = plan.complex_model if isinstance(plan, SimulationPlan) else plan.get("complex_model", plan)
    lattices = model.lattices if isinstance(model, ComplexModelSpec) else model.get("lattices", [])
    if not lattices:
        return {}
    lattice = lattices[0]
    pattern = lattice.universe_pattern if isinstance(lattice, LatticeSpec) else lattice.get("universe_pattern", [])
    counts: dict[str, int] = {}
    for row in pattern:
        for universe_id in row:
            counts[universe_id] = counts.get(universe_id, 0) + 1
    return counts


def collect_loading_override_counts(
    plan: SimulationPlan | dict[str, Any], loading_id: str | None = None
) -> dict[str, int]:
    """Count override coordinates in one loading or across all loadings."""
    model = plan.complex_model if isinstance(plan, SimulationPlan) else plan.get("complex_model", plan)
    loadings = model.lattice_loadings if isinstance(model, ComplexModelSpec) else model.get("lattice_loadings", [])
    counts: dict[str, int] = {}
    for loading in loadings:
        current_id = loading.id if isinstance(loading, LatticeLoadingSpec) else loading.get("id")
        if loading_id is not None and current_id != loading_id:
            continue
        overrides = loading.overrides if isinstance(loading, LatticeLoadingSpec) else loading.get("overrides", {})
        for universe_id, coordinates in overrides.items():
            counts[universe_id] = counts.get(universe_id, 0) + len(coordinates)
    return counts


def collect_nested_operation_coord_counts(
    plan: SimulationPlan | dict[str, Any], loading_id: str | None = None
) -> dict[str, int]:
    """Count target_coordinates in nested_component_override operations.

    Maps replacement_universe_id → total coordinate count.
    """
    model = plan.complex_model if isinstance(plan, SimulationPlan) else plan.get("complex_model", plan)
    loadings = model.lattice_loadings if isinstance(model, ComplexModelSpec) else model.get("lattice_loadings", [])
    counts: dict[str, int] = {}
    for loading in loadings:
        current_id = loading.id if isinstance(loading, LatticeLoadingSpec) else loading.get("id")
        if loading_id is not None and current_id != loading_id:
            continue
        transforms = loading.transformations if isinstance(loading, LatticeLoadingSpec) else loading.get("transformations", [])
        for t in transforms:
            kind = t.operation_kind if hasattr(t, "operation_kind") else t.get("operation_kind")
            if kind != "nested_component_override":
                continue
            repl = t.replacement_universe_id if hasattr(t, "replacement_universe_id") else t.get("replacement_universe_id")
            coords = t.target_coordinates if hasattr(t, "target_coordinates") else t.get("target_coordinates", [])
            counts[repl] = counts.get(repl, 0) + len(coords)
    return counts


def collect_active_lattice_union(plan: SimulationPlan) -> tuple[float, float] | None:
    """Return the contiguous union of all active-fuel lattice layer bounds."""
    model = plan.complex_model
    if model is None or model.core is None:
        return None
    layers = sorted(
        (layer for layer in model.core.axial_layers
         if layer.fill.type == "lattice" and "active_fuel" in layer.id),
        key=lambda layer: layer.z_min_cm,
    )
    if not layers:
        return None
    return (layers[0].z_min_cm, layers[-1].z_max_cm)


def validate_axial_layer_continuity(
    plan: SimulationPlan, tolerance_cm: float = 1e-6
) -> list[BenchmarkIssue]:
    """Return deterministic gaps/overlaps in the complete axial layer partition."""
    model = plan.complex_model
    if model is None or model.core is None:
        return [BenchmarkIssue("vera3.active_lattice_coverage_gap", "error", "plan has no axial layers")]
    layers = sorted(model.core.axial_layers, key=lambda layer: layer.z_min_cm)
    issues: list[BenchmarkIssue] = []
    for previous, current in zip(layers, layers[1:]):
        delta = current.z_min_cm - previous.z_max_cm
        if delta > tolerance_cm:
            issues.append(BenchmarkIssue("vera3.active_lattice_coverage_gap", "error", "axial layer gap", actual=delta))
        elif delta < -tolerance_cm:
            issues.append(BenchmarkIssue("vera3.active_lattice_coverage_overlap", "error", "axial layer overlap", actual=-delta))
    return issues


def diagnose_vera3_component_geometry(
    plan_or_patch_fixture: SimulationPlan | dict[str, Any],
    contract: dict[str, Any],
    *,
    variant: str,
) -> list[BenchmarkIssue]:
    """Diagnose known VERA3 component/profile errors without changing the plan.

    This is a deliberately test-only oracle. It recognizes legacy whole-layer
    fuel-pin internals, finite inserts incorrectly placed in the base lattice,
    and radial/axial insert omissions from either an assembled plan or raw
    patch fixture.
    """
    raw = plan_or_patch_fixture.model_dump(mode="json") if isinstance(plan_or_patch_fixture, SimulationPlan) else plan_or_patch_fixture
    model = raw.get("complex_model", raw)
    core = model.get("core", {})
    layers = core.get("axial_layers", [])
    if not layers and "patches" in raw:
        axial = next((patch for patch in raw["patches"] if patch.get("patch_type") == "axial_layers"), {})
        layers = axial.get("layers", [])
        model = {**model, "lattice_loadings": axial.get("lattice_loadings", [])}
    issues: list[BenchmarkIssue] = []
    fuel_segments = contract["component_profiles"]["fuel_pin"]["axial_segments"]
    for segment in fuel_segments:
        segment_id = segment["id"]
        if segment_id not in {"lower_end_plug", "upper_end_plug", "upper_plenum"}:
            continue
        matching = next(
            (layer for layer in layers if layer.get("id", layer.get("layer_id")) == segment_id),
            None,
        )
        fill = (matching or {}).get("fill", matching or {})
        fill_type = fill.get("type", fill.get("fill_type"))
        fill_id = fill.get("id", fill.get("fill_id"))
        expected_material = segment["internal_material"]
        if fill_type == "material" and fill_id == expected_material:
            issues.append(BenchmarkIssue(
                "vera3.component_material_slab", "error",
                f"{segment_id} is a whole-assembly {expected_material} slab instead of fuel-pin internal content",
                path=f"core.axial_layers.{segment_id}",
            ))
            issues.append(BenchmarkIssue(
                "vera3.fuel_pin_profile_missing", "error",
                f"{segment_id} lacks a fuel-pin component profile",
                path=f"core.axial_layers.{segment_id}",
            ))

    if variant == "3B":
        universes = {u.get("universe_id", u.get("id")): u for u in raw.get("patches", [{}])[0:0]}
        if "patches" in raw:
            universe_patch = next((p for p in raw["patches"] if p.get("patch_type") == "universes"), {})
            universes = {u["universe_id"]: u for u in universe_patch.get("universes", [])}
        elif model.get("universes"):
            cells = {cell.get("id"): cell for cell in model.get("cells", [])}
            universes = {
                universe.get("id"): {"cells": [cells.get(cell_id, {}) for cell_id in universe.get("cell_ids", [])]}
                for universe in model.get("universes", [])
            }
        pyrex = universes.get("pyrex_rod") or universes.get("pyrex_pin") or universes.get("pyrex_inner_profile")
        if pyrex:
            if "pyrex_inner_profile" in universes:
                # New inner-profile universe: check only that it contains
                # a poison layer and gas gaps with helium.
                cells = pyrex.get("cells", [])
                roles = {c.get("role") or c.get("component_role") for c in cells}
                if "poison" not in roles:
                    issues.append(BenchmarkIssue("vera3.pyrex_radial_stack_mismatch", "error", "Pyrex inner profile omits the poison layer"))
                gas_gap_mats = {
                    c.get("material_id", c.get("fill_id"))
                    for c in cells
                    if (c.get("role") or c.get("component_role")) == "gas_gap"
                }
                if gas_gap_mats and gas_gap_mats != {"helium"}:
                    issues.append(BenchmarkIssue("vera3.pyrex_gap_material_mismatch", "error", "Pyrex gas gaps must be helium"))
            else:
                expected = {item["id"]: item["material"] for item in contract["component_profiles"]["pyrex_rod"]["radial_layers"]}
                cells = pyrex.get("cells", [])
                actual = {cell.get("id"): cell.get("material_id", cell.get("fill_id")) for cell in cells}
                if any(actual.get(key) != material for key, material in expected.items() if key in {"gap_1", "gap_2"}):
                    issues.append(BenchmarkIssue("vera3.pyrex_gap_material_mismatch", "error", "Pyrex internal gaps do not match the helium contract"))
                expected_ids = set(expected)
                actual_ids = set(actual)
                radii = [(cell.get("r_min_cm", 0.0), cell.get("r_max_cm")) for cell in cells if cell.get("r_max_cm") is not None]
                if expected_ids - actual_ids or (radii and any(right <= left for left, right in radii)):
                    issues.append(BenchmarkIssue("vera3.pyrex_radial_stack_mismatch", "error", "Pyrex radial stack omits or invalidates contract layers"))

        base_counts = collect_base_lattice_counts(raw)
        if base_counts.get("pyrex_rod", 0) or base_counts.get("pyrex_pin", 0) or base_counts.get("pyrex_inner_profile", 0) or base_counts.get("thimble_plug", 0) or base_counts.get("thimble_inner_profile", 0) or base_counts.get("plug_pin", 0):
            issues.append(BenchmarkIssue("vera3.base_lattice_contains_finite_insert", "error", "finite Pyrex or thimble insert appears in base lattice"))

        # Check thimble loading via both legacy overrides and nested operations
        loading_counts = collect_loading_override_counts(raw)
        nested_counts = collect_nested_operation_coord_counts(raw)
        thimble_total = (
            loading_counts.get("thimble_plug", 0)
            + loading_counts.get("plug_pin", 0)
            + nested_counts.get("thimble_inner_profile", 0)
            + nested_counts.get("thimble_plug", 0)
        )
        if thimble_total != 8:
            issues.append(BenchmarkIssue("vera3.thimble_loading_missing", "error", "3B requires an 8-coordinate finite thimble loading"))
        pyrex_profile = contract["component_profiles"]["pyrex_rod"]["axial_profile"]
        nominal_top = pyrex_profile["nominal_plenum_top_cm"]["value"]
        nozzle_start = next(zone for zone in contract["assembly_level_zones"] if zone["id"] == "upper_nozzle")["z_min_cm"]["value"]
        if nominal_top > nozzle_start:
            issues.append(BenchmarkIssue("vera3.pyrex_axial_profile_conflict", "warning", "nominal Pyrex plenum intersects homogenized upper nozzle"))

    if isinstance(plan_or_patch_fixture, SimulationPlan):
        issues.extend(validate_axial_layer_continuity(plan_or_patch_fixture))
        active = collect_active_lattice_union(plan_or_patch_fixture)
        expected = contract["derived_breakpoints"]["active_fuel_region_cm"]["value"]
        if active is None or abs(active[0] - expected[0]) > 1e-6 or abs(active[1] - expected[1]) > 1e-6:
            issues.append(BenchmarkIssue("vera3.active_lattice_coverage_gap", "error", "active lattice union does not cover active fuel"))
    return issues


# ---------------------------------------------------------------------------
# Plan validator
# ---------------------------------------------------------------------------


def _lattice_pin_kinds(plan: SimulationPlan) -> list[list[str]] | None:
    """Return the plan's lattice as a 2D grid of pin-kind letters, or None."""
    model = plan.complex_model
    if model is None or not model.lattices:
        return None
    lattice = model.lattices[0]
    names = {u.id: u.name for u in model.universes}
    return [
        [_pin_kind(uid, names.get(uid, "")) for uid in row]
        for row in lattice.universe_pattern
    ]


def _material_ids(model: ComplexModelSpec) -> set[str]:
    return {m.id for m in model.materials}


def validate_vera3_plan_structure(
    plan: SimulationPlan,
    reference: dict[str, Any],
    *,
    variant: str = "3A",
) -> list[BenchmarkIssue]:
    """Structural acceptance check for a VERA3-like plan.

    Returns benchmark-specific :class:`BenchmarkIssue` entries (empty list =
    acceptance pass). This deliberately does NOT reuse the generic
    ``assembly3d.*`` codes: those flag generic geometry faults, while these
    flag VERA3-specific structural expectations (pin counts, grid count,
    coordinates) that only a benchmark fixture can define.
    """
    issues: list[BenchmarkIssue] = []
    tol = reference.get("tolerance_cm", 1.0e-3)
    meta = reference["assembly_metadata"]
    pm = reference["pin_maps"][variant]
    model = plan.complex_model

    if model is None or model.kind != "assembly":
        issues.append(BenchmarkIssue("vera3.not_3d_assembly", "error",
                                     "plan must be a 3D assembly (complex_model.kind='assembly')",
                                     actual=getattr(model, "kind", None)))
        return issues
    if model.core is None or not model.core.axial_layers:
        issues.append(BenchmarkIssue("vera3.not_3d_assembly", "error",
                                     "plan has no core.axial_layers"))
        return issues

    layers = model.core.axial_layers
    z_mins = [L.z_min_cm for L in layers]
    z_maxs = [L.z_max_cm for L in layers]
    domain = (min(z_mins), max(z_maxs))
    exp_domain = tuple(meta["axial_domain_cm"])

    # 1a. default z=-1..1 unit slab
    if abs(domain[0] - (-1.0)) < tol and abs(domain[1] - 1.0) < tol:
        issues.append(BenchmarkIssue("vera3.default_z_extent", "error",
                                     "axial domain is the default z=-1..1 unit slab",
                                     expected=exp_domain, actual=domain))

    # 1b. axial domain coverage
    if abs(domain[0] - exp_domain[0]) > tol or abs(domain[1] - exp_domain[1]) > tol:
        issues.append(BenchmarkIssue("vera3.axial_domain_mismatch", "error",
                                     "axial domain does not cover the expected range",
                                     expected=list(exp_domain), actual=list(domain)))

    # 1c. Active fuel can be split by finite insert loading boundaries.
    fuel_union = collect_active_lattice_union(plan)
    exp_fuel = meta["active_fuel_region_cm"]
    if fuel_union is None:
        issues.append(BenchmarkIssue("vera3.active_fuel_height_mismatch", "error",
                                      "no lattice-filled active fuel layer found"))
    else:
        if abs(fuel_union[0] - exp_fuel[0]) > tol or abs(fuel_union[1] - exp_fuel[1]) > tol:
            issues.append(BenchmarkIssue("vera3.active_fuel_height_mismatch", "error",
                                          "active fuel region z-range mismatch",
                                          expected=list(exp_fuel),
                                          actual=list(fuel_union)))

    # 2. spacer grids as overlays
    ref_grids = reference["spacer_grids"]
    expected_grid_count = ref_grids["count"]
    overlays = model.core.axial_overlays
    spacer_overlays = [o for o in overlays if o.overlay_kind == "spacer_grid"]

    # 2a. grid only mentioned in a layer purpose but no overlay?
    mentions_grid = any(
        ("grid" in (L.purpose or "").lower() or "grid" in (L.name or "").lower())
        and L.fill.type == "lattice"
        for L in layers
    )
    if expected_grid_count > 0 and not spacer_overlays:
        code = "vera3.spacer_grid_missing_overlay" if mentions_grid else "vera3.spacer_grid_count_mismatch"
        issues.append(BenchmarkIssue(code, "error",
                                     "spacer grids expected but no spacer_grid overlay present",
                                     expected=expected_grid_count, actual=0))

    # 2b. grid count
    if len(spacer_overlays) != expected_grid_count and spacer_overlays:
        issues.append(BenchmarkIssue("vera3.spacer_grid_count_mismatch", "error",
                                     "spacer_grid overlay count mismatch",
                                     expected=expected_grid_count, actual=len(spacer_overlays)))

    # 2c. each overlay mode/target/through-path
    exp_mode = reference["expected_overlay_geometry_mode"]
    exp_target = reference["expected_overlay_target_lattice_id"]
    for o in spacer_overlays:
        if o.geometry_mode != exp_mode and o.geometry_mode != "skeleton":
            issues.append(BenchmarkIssue("vera3.spacer_grid_wrong_mode", "error",
                                         f"overlay {o.id!r} geometry_mode is {o.geometry_mode!r}",
                                         expected=exp_mode, actual=o.geometry_mode,
                                         path=f"core.axial_overlays.{o.id}"))
        if o.target_lattice_id != exp_target:
            issues.append(BenchmarkIssue("vera3.spacer_grid_wrong_mode", "error",
                                         f"overlay {o.id!r} target_lattice_id is {o.target_lattice_id!r}",
                                         expected=exp_target, actual=o.target_lattice_id))
        if o.through_path_preserved is not True:
            issues.append(BenchmarkIssue("vera3.spacer_grid_wrong_mode", "error",
                                         f"overlay {o.id!r} through_path_preserved is not True",
                                         expected=True, actual=o.through_path_preserved))

    # 2d. no axial layer fill is a grid material slab
    mat_ids = _material_ids(model)
    grid_mat_ids = {m.id for m in model.materials if classify_material_role(m) == "protected"
                    and any(t in (m.id + m.name).lower() for t in ("grid", "inconel"))}
    for L in layers:
        if L.fill.type == "material" and L.fill.id in grid_mat_ids:
            issues.append(BenchmarkIssue("vera3.material_slab_grid", "error",
                                         f"axial layer {L.id!r} fill is a grid material slab",
                                         path=f"core.axial_layers.{L.id}.fill"))

    # 3. pin map structure
    pm = reference["pin_maps"][variant]
    grid = _lattice_pin_kinds(plan)
    exp_size = meta["lattice_size"]
    if grid is None or len(grid) != exp_size[0] or any(len(r) != exp_size[1] for r in grid):
        issues.append(BenchmarkIssue("vera3.pin_map_size_mismatch", "error",
                                     "lattice is not 17x17",
                                     expected=list(exp_size),
                                     actual=[len(grid) if grid else 0,
                                             len(grid[0]) if grid else 0]))
    else:
        flat = [k for row in grid for k in row]
        # map plan kinds to reference letters; instrument/guide/pyrex/plug already letters
        actual_counts: dict[str, int] = {}
        for k in flat:
            actual_counts[k] = actual_counts.get(k, 0) + 1
        expected_base_counts = pm["counts"]
        if variant == "3B":
            expected_base_counts = {"F": 264, "G": 24, "I": 1, "P": 0, "T": 0}
        for letter, exp_count in expected_base_counts.items():
            act = actual_counts.get(letter, 0)
            if act != exp_count:
                issues.append(BenchmarkIssue("vera3.pin_count_mismatch", "error",
                                             f"pin count mismatch for {letter}",
                                             expected=exp_count, actual=act))

        # 3b. coordinate checks (document 1-based -> 0-indexed pattern)
        def _check_coord(letter: str, pos_1based: list[int], label: str, code: str) -> None:
            r0, c0 = to_0_indexed(pos_1based)
            if 0 <= r0 < len(grid) and 0 <= c0 < len(grid[0]):
                if grid[r0][c0] != letter:
                    issues.append(BenchmarkIssue(code, "error",
                                                 f"{label} at doc {pos_1based} is {grid[r0][c0]!r}",
                                                 expected=letter, actual=grid[r0][c0],
                                                 path=f"lattice[{r0}][{c0}]"))

        # instrument tube
        _check_coord("I", pm["instrument_tube_position_1based"], "instrument tube",
                     "vera3.instrument_tube_coordinate_mismatch")
        if variant == "3B":
            for pos in pm["guide_tube_positions_1based"]:
                _check_coord("G", pos, "base guide tube", "vera3.guide_tube_coordinate_mismatch")
            # Pyrex loading: check both legacy overrides and nested operations
            loading_counts = collect_loading_override_counts(plan)
            nested_counts = collect_nested_operation_coord_counts(plan)
            pyrex_total = (
                loading_counts.get("pyrex_rod", 0)
                + loading_counts.get("pyrex_pin", 0)
                + nested_counts.get("pyrex_inner_profile", 0)
            )
            if pyrex_total != 16:
                issues.append(BenchmarkIssue("vera3.pyrex_loading_count_mismatch", "error", "Pyrex loading must cover 16 coordinates", expected=16, actual=pyrex_total))
            thimble_total = (
                loading_counts.get("thimble_plug", 0)
                + loading_counts.get("plug_pin", 0)
                + nested_counts.get("thimble_inner_profile", 0)
                + nested_counts.get("thimble_plug", 0)
            )
            if thimble_total != 8:
                issues.append(BenchmarkIssue("vera3.thimble_loading_missing", "error", "thimble loading must cover 8 coordinates", expected=8, actual=thimble_total))

            # Pyrex operation kind check
            pyrex_loading = next((item for item in model.lattice_loadings if item.id == "pyrex_active_loading"), None)
            if pyrex_loading is not None:
                has_nested = any(t.operation_kind == "nested_component_override" for t in pyrex_loading.transformations)
                if not has_nested and not pyrex_loading.overrides:
                    issues.append(BenchmarkIssue("vera3.pyrex_not_nested_in_guide", "error", "Pyrex loading should use nested_component_override"))
                # Coordinate check
                nested_op = next((t for t in pyrex_loading.transformations if t.operation_kind == "nested_component_override"), None)
                if nested_op is not None:
                    actual_coords = set(tuple(c) for c in nested_op.target_coordinates)
                    expected_coords = {to_0_indexed(pos) for pos in pm["pyrex_positions_1based"]}
                    if actual_coords != expected_coords:
                        issues.append(BenchmarkIssue("vera3.pyrex_coordinate_mismatch", "error", "Pyrex loading coordinates mismatch", expected=sorted(expected_coords), actual=sorted(actual_coords)))
                elif pyrex_loading.overrides:
                    actual_coords = set(
                        tuple(c) for c in (pyrex_loading.overrides.get("pyrex_pin") or pyrex_loading.overrides.get("pyrex_rod") or [])
                    )
                    expected_coords = {to_0_indexed(pos) for pos in pm["pyrex_positions_1based"]}
                    if actual_coords != expected_coords:
                        issues.append(BenchmarkIssue("vera3.pyrex_coordinate_mismatch", "error", "Pyrex loading coordinates mismatch", expected=sorted(expected_coords), actual=sorted(actual_coords)))

    # 4. material references resolve
    cell_material_ids = {c.fill_id for c in model.cells if c.fill_type == "material"}
    missing = cell_material_ids - mat_ids
    if missing:
        issues.append(BenchmarkIssue("vera3.material_missing", "error",
                                     f"cells reference undefined materials {sorted(missing)!r}",
                                     actual=sorted(missing)))
    for o in spacer_overlays:
        if o.material_id and o.material_id not in mat_ids:
            issues.append(BenchmarkIssue("vera3.overlay_material_unresolved", "error",
                                         f"overlay {o.id!r} material_id {o.material_id!r} not defined",
                                         path=f"core.axial_overlays.{o.id}.material_id"))
    # pyrex material required for 3B
    if variant == "3B":
        has_pyrex = any("pyrex" in (m.id + m.name).lower() for m in model.materials)
        if not has_pyrex:
            issues.append(BenchmarkIssue("vera3.material_missing", "error",
                                         "3B requires a Pyrex material", expected="pyrex"))

    # 4b. guide tubes must keep a Zircaloy/clad wall, not be pure-water cells.
    materials_by_id = {m.id: m for m in model.materials}
    for u in model.universes:
        if "guide" not in (u.id + u.name).lower():
            continue
        u_cells = [c for c in model.cells if c.id in u.cell_ids]
        has_wall = any(
            c.fill_type == "material"
            and c.fill_id in materials_by_id
            and classify_material_role(materials_by_id[c.fill_id]) == "protected"
            and c.fill_id != "fuel"
            for c in u_cells
        )
        if not has_wall:
            issues.append(BenchmarkIssue("vera3.guide_tube_wall_missing", "warning",
                                         f"guide tube universe {u.id!r} has no Zircaloy/clad "
                                         "wall cell (only coolant); the tube wall must be preserved",
                                         path=f"complex_model.universes.{u.id}"))

    # 4c. Tube radii checks (guide tube 0.561/0.602, instrument tube 0.559/0.605).
    _check_tube_radii(model, issues)

    # 4d. Nested operation and finite-insert geometry checks for 3B.
    if variant == "3B":
        _check_3b_nested_geometry(model, issues)

    # 5. renderer compatibility -- no blocking generic assembly3d.* errors
    generic = validate_assembly3d_plan(plan, requirement="VERA 3D HZP assembly with spacer grids")
    blocking = [i for i in generic if i.severity == "error" and i.code.startswith("assembly3d.")]
    for i in blocking:
        issues.append(BenchmarkIssue("vera3.unresolved_reference", "error",
                                     f"blocking generic issue: {i.code} -- {i.message}",
                                     path=i.schema_path))

    # 6. full-assembly / source / plot bounds consistency (Step 6).
    from openmc_agent.geometry_bounds import (
        compute_geometry_bounds,
        infer_symmetry_policy,
        validate_bounds_consistency,
    )
    from openmc_agent.source_settings import source_bounds_for_plan

    gb = compute_geometry_bounds(model)
    policy = infer_symmetry_policy(model, gb)
    if policy.mode == "quarter":
        issues.append(BenchmarkIssue("vera3.quarter_geometry_unexpected", "error",
                                     "VERA3 reference uses a full 17x17 pin map; quarter "
                                     "symmetry is unexpected"))
    src = source_bounds_for_plan(model)
    if src is not None and gb is not None:
        src_tuple = (src.x_min, src.x_max, src.y_min, src.y_max, src.z_min, src.z_max)
        bounds_issues = validate_bounds_consistency(model, source_bounds=src_tuple)
        for bi in bounds_issues:
            if "source_xy_outside" in bi.code or "source_xy_too_small" in bi.code:
                issues.append(BenchmarkIssue("vera3.source_bounds_mismatch", "error",
                                             bi.message, path=bi.schema_path))
            elif "plot_bounds" in bi.code:
                issues.append(BenchmarkIssue("vera3.plot_quarter_assembly", "warning",
                                             bi.message, path=bi.schema_path))
        # source z must overlap active fuel.
        if gb.active_fuel_z is not None:
            af0, af1 = gb.active_fuel_z
            if not (src.z_min < af1 - 1e-6 and src.z_max > af0 + 1e-6):
                issues.append(BenchmarkIssue("vera3.source_not_active_fuel", "error",
                                             f"source z [{src.z_min},{src.z_max}] does not "
                                             f"overlap active fuel [{af0},{af1}]"))

    return issues


# ---------------------------------------------------------------------------
# Deterministic VERA3-like plan builder (test fixture, no LLM)
# ---------------------------------------------------------------------------


def _inner_profile_universes(variant: str) -> tuple[list[CellSpec], list[UniverseSpec]]:
    """Build pyrex_inner_profile and thimble_inner_profile universes for 3B tests."""
    cells: list[CellSpec] = []
    universes: list[UniverseSpec] = []
    if variant == "3B":
        cells.extend([
            CellSpec(id="pyrex_inner_solid", name="pyrex poison",
                     fill_type="material", fill_id="pyrex", component_role="poison"),
            CellSpec(id="pyrex_inner_bg", name="pyrex background water",
                     fill_type="material", fill_id="water", component_role="inner_flow_background"),
        ])
        universes.append(UniverseSpec(
            id="pyrex_inner_profile", name="pyrex inner profile",
            cell_ids=["pyrex_inner_solid", "pyrex_inner_bg"],
        ))
        cells.extend([
            CellSpec(id="thimble_plug_solid", name="thimble plug",
                     fill_type="material", fill_id="ss304", component_role="plug"),
            CellSpec(id="thimble_plug_bg", name="thimble plug water gap",
                     fill_type="material", fill_id="water", component_role="inner_flow_background"),
        ])
        universes.append(UniverseSpec(
            id="thimble_inner_profile", name="thimble inner profile",
            cell_ids=["thimble_plug_solid", "thimble_plug_bg"],
        ))
    return cells, universes


def _material(mid: str, name: str, density: float, nuclide: str = "U235") -> ComplexMaterialSpec:
    return ComplexMaterialSpec(
        id=mid, name=name, density_unit="g/cm3", density_value=density,
        composition=[NuclideSpec(name=nuclide, percent=1.0)],
    )


def _vera3_materials(variant: str) -> list[ComplexMaterialSpec]:
    mats = [
        _material("fuel", "UO2 fuel", 10.257),
        ComplexMaterialSpec(id="water", name="borated water coolant", density_unit="g/cm3",
                            density_value=0.743, chemical_formula="H2O"),
        _material("clad", "Zircaloy-4 clad", 6.56, "Zr90"),
        _material("grid_inconel", "Inconel-718 end grid", 8.19, "Fe56"),
        _material("grid_zircaloy", "Zircaloy-4 middle grid", 6.56, "Zr90"),
        _material("helium", "helium gas", 0.0001, "He4"),
        _material("ss304", "SS304 structural", 8.00, "Fe56"),
    ]
    if variant == "3B":
        mats.append(_material("pyrex", "Pyrex borosilicate glass", 2.25, "B10"))
    return mats


def _vera3_pin_universes(variant: str, *, pure_water_guide: bool = False) -> tuple[list[CellSpec], list[UniverseSpec]]:
    """Each pin universe = a solid cell + a single open coolant cell.

    Each universe therefore has exactly one open cell, so a homogenized
    overlay can derive an overlay universe (coolant -> grid material) at every
    lattice position while the solid (fuel/clad/pyrex/plug) is preserved.
    Set ``pure_water_guide=True`` to build a (broken) guide tube with no
    Zircaloy wall -- only coolant -- for the guide-tube-wall acceptance test.

    Fuel-pin variant universes (end-plug, plenum) are added so component-
    profile layers can use replace_universe_family instead of material slabs.
    """
    cells: list[CellSpec] = []
    universes: list[UniverseSpec] = []

    def _pin(uid: str, uname: str, solid_cell_id: str, solid_mat: str) -> None:
        coolant_id = f"{solid_cell_id}_coolant"
        cells.append(CellSpec(id=solid_cell_id, name=f"{uname} solid",
                              fill_type="material", fill_id=solid_mat, region_id="solid_region"))
        cells.append(CellSpec(id=coolant_id, name=f"{uname} coolant",
                              fill_type="material", fill_id="water", region_id="coolant_region"))
        universes.append(UniverseSpec(id=uid, name=uname, cell_ids=[solid_cell_id, coolant_id]))

    _pin("fuel_pin", "fuel pin", "fuel_pellet", "fuel")
    # Fuel-pin component-profile variants for axial segments where only the
    # pin-internal material changes (end plug = Zircaloy, plenum = helium).
    _pin("fuel_pin_end_plug", "fuel pin end plug", "end_plug_solid", "clad")
    _pin("fuel_pin_plenum", "fuel pin plenum", "plenum_solid", "helium")
    if pure_water_guide:
        cells.append(CellSpec(id="guide_water", name="guide water",
                              fill_type="material", fill_id="water", region_id="coolant_region"))
        universes.append(UniverseSpec(id="guide_tube", name="guide tube", cell_ids=["guide_water"]))
    else:
        # Guide tube with component roles and regions for nested override
        cells.extend([
            CellSpec(id="gt_inner_water", name="guide inner water",
                     fill_type="material", fill_id="water", region_id="gt_inner_region",
                     component_role="inner_flow"),
            CellSpec(id="gt_wall", name="guide tube wall",
                     fill_type="material", fill_id="clad", region_id="gt_wall_region",
                     component_role="tube_wall", protected_through_path=True),
            CellSpec(id="gt_outer_water", name="guide outer moderator",
                     fill_type="material", fill_id="water", region_id="gt_outer_region",
                     component_role="outer_moderator"),
        ])
        universes.append(UniverseSpec(
            id="guide_tube", name="guide tube",
            cell_ids=["gt_inner_water", "gt_wall", "gt_outer_water"],
        ))
    _pin("instrument_tube", "instrument tube", "inst_tube_wall", "clad")
    if variant == "3B":
        _pin("pyrex_pin", "pyrex burnable absorber rod", "pyrex_solid", "pyrex")
        _pin("plug_pin", "thimble plug", "plug_solid", "ss304")
    return cells, universes


def _vera3_lattice_pattern(reference: dict, variant: str) -> list[list[str]]:
    """Build the 17x17 universe_pattern from the reference pin coordinates."""
    size = reference["assembly_metadata"]["lattice_size"][0]
    pm = reference["pin_maps"][variant]
    pattern = [["fuel_pin" for _ in range(size)] for _ in range(size)]
    uid_for = {"G": "guide_tube", "I": "instrument_tube", "P": "pyrex_pin", "T": "plug_pin"}
    placements = {
        "G": pm["guide_tube_positions_1based"],
        "I": [pm["instrument_tube_position_1based"]],
    }
    if variant != "3B":
        placements.update({"P": pm["pyrex_positions_1based"], "T": pm["plug_positions_1based"]})
    for letter, positions in placements.items():
        uid = uid_for.get(letter)
        if uid is None:
            continue
        for pos in positions:
            r0, c0 = to_0_indexed(pos)
            pattern[r0][c0] = uid
    return pattern


def build_vera3_like_plan(
    reference: dict,
    *,
    variant: str = "3A",
    drop_overlays: bool = False,
    grid_count: int | None = None,
    use_material_slab_grid: bool = False,
    mutate_pin: tuple[int, int] | None = None,
    wrong_pyrex: tuple[int, int] | None = None,
    default_z: bool = False,
    pure_water_guide: bool = False,
) -> SimulationPlan:
    """Build a deterministic VERA3-like SimulationPlan from the reference.

    Mutation flags let the acceptance tests construct intentionally-broken
    plans (missing overlays, wrong grid count, material slab, wrong pin
    placement, default z, pure-water guide tube). No LLM is involved.
    """
    meta = reference["assembly_metadata"]
    pm = reference["pin_maps"][variant]
    materials = _vera3_materials(variant)
    cells, universes = _vera3_pin_universes(variant, pure_water_guide=pure_water_guide)

    pattern = _vera3_lattice_pattern(reference, variant)
    if mutate_pin is not None:
        r0, c0 = mutate_pin  # 0-indexed; replace whatever is there with a fuel pin
        pattern[r0][c0] = "fuel_pin"

    lattices = [LatticeSpec(
        id="assembly_lattice", name="VERA3 17x17 assembly", kind="rect",
        pitch_cm=(meta["pin_pitch_cm"], meta["pin_pitch_cm"]),
        universe_pattern=pattern,
    )]

    lattice_loadings: list[LatticeLoadingSpec] = []
    # Component-profile loadings: replace fuel-pin family with variant universe
    # so end-plug/plenum layers are lattice fills, not material slabs.
    from openmc_agent.schemas import LatticeTransformationOperation
    lattice_loadings.append(LatticeLoadingSpec(
        id="end_plug_loading", base_lattice_id="assembly_lattice",
        derived_lattice_id="assembly_lattice_end_plug",
        transformations=[LatticeTransformationOperation(
            operation_id="family_end_plug",
            operation_kind="replace_universe_family",
            replacement_universe_id="fuel_pin_end_plug",
            source_universe_id="fuel_pin",
            purpose="Fuel-pin end-plug profile",
        )],
    ))
    lattice_loadings.append(LatticeLoadingSpec(
        id="plenum_loading", base_lattice_id="assembly_lattice",
        derived_lattice_id="assembly_lattice_plenum",
        transformations=[LatticeTransformationOperation(
            operation_id="family_plenum",
            operation_kind="replace_universe_family",
            replacement_universe_id="fuel_pin_plenum",
            source_universe_id="fuel_pin",
            purpose="Fuel-pin plenum profile",
        )],
    ))

    if variant == "3B":
        pyrex_coords = [to_0_indexed(pos) for pos in pm["pyrex_positions_1based"]]
        if wrong_pyrex is not None:
            pyrex_coords[0] = wrong_pyrex
        lattice_loadings.extend([
            LatticeLoadingSpec(
                id="pyrex_active_loading", base_lattice_id="assembly_lattice",
                transformations=[LatticeTransformationOperation(
                    operation_id="insert_pyrex_inside_guide",
                    operation_kind="nested_component_override",
                    replacement_universe_id="pyrex_inner_profile",
                    target_coordinates=pyrex_coords,
                    component_role="inner_flow",
                    preserve_component_roles=["tube_wall", "outer_moderator"],
                    purpose="Insert Pyrex inner profile inside guide tubes",
                )],
            ),
            LatticeLoadingSpec(
                id="thimble_plug_loading", base_lattice_id="assembly_lattice",
                transformations=[LatticeTransformationOperation(
                    operation_id="insert_thimble_inside_guide",
                    operation_kind="nested_component_override",
                    replacement_universe_id="thimble_inner_profile",
                    target_coordinates=[to_0_indexed(pos) for pos in pm["plug_positions_1based"]],
                    component_role="inner_flow",
                    preserve_component_roles=["tube_wall", "outer_moderator"],
                    purpose="Insert thimble plug inside guide tubes",
                )],
            ),
        ])
        # Add inner-profile universes for the test plan
        cells_inner, univs_inner = _inner_profile_universes(variant)
        cells.extend(cells_inner)
        universes.extend(univs_inner)

    # Axial layers: component-profile segments (end-plug, plenum) use lattice
    # fill with family replacement; only homogenized regions use material fill.
    ref_layers = reference["axial_layers"]
    _component_profile_loading = {
        "lower_end_plug": "end_plug_loading",
        "upper_end_plug": "end_plug_loading",
        "upper_plenum": "plenum_loading",
    }
    if default_z:
        layers = [AxialLayerSpec(id="fuel", name="fuel", z_min_cm=-1.0, z_max_cm=1.0,
                                 fill={"type": "lattice", "id": "assembly_lattice"})]
    else:
        layers = []
        for rl in ref_layers:
            layer_id = rl["id"]
            loading_id_for_profile = _component_profile_loading.get(layer_id)
            if use_material_slab_grid and layer_id == "active_fuel":
                fill = {"type": "material", "id": "grid_inconel"}
            elif layer_id == "active_fuel":
                fill = {"type": "lattice", "id": "assembly_lattice"}
            elif loading_id_for_profile is not None:
                fill = {"type": "lattice", "id": "assembly_lattice"}
            else:
                mat_map = {"borated_water": "water", "zircaloy4": "clad", "helium": "helium",
                           "ss304_coolant_50_50": "ss304", "ss304_coolant_nozzle": "ss304",
                           "uo2_fuel_lattice": "assembly_lattice"}
                fill = {"type": "material", "id": mat_map.get(rl["material"], "water")}
            layer_kwargs: dict[str, Any] = {
                "id": layer_id, "name": layer_id,
                "z_min_cm": rl["z_min_cm"], "z_max_cm": rl["z_max_cm"],
                "fill": fill,
                "purpose": ("Active fuel with spacer grid sub-layers" if layer_id == "active_fuel" else ""),
            }
            if loading_id_for_profile is not None:
                layer_kwargs["loading_id"] = loading_id_for_profile
            layers.append(AxialLayerSpec(**layer_kwargs))

    # overlays
    overlays: list[AxialOverlaySpec] = []
    if not drop_overlays and not default_z:
        ref_grids = reference["spacer_grids"]["grids"]
        n = grid_count if grid_count is not None else len(ref_grids)
        for i, g in enumerate(ref_grids[:n]):
            material_id = "grid_inconel" if g["material"] == "inconel718" else "grid_zircaloy"
            overlays.append(AxialOverlaySpec(
                id=f"spacer_grid_{i}", overlay_kind="spacer_grid",
                z_min_cm=g["z_min_cm"], z_max_cm=g["z_max_cm"],
                target_lattice_id="assembly_lattice", material_id=material_id,
                geometry_mode="homogenized_open_region", through_path_preserved=True,
            ))

    model = ComplexModelSpec(
        name=f"VERA3-{variant}", kind="assembly", materials=materials,
        cells=cells, universes=universes, lattices=lattices,
        surfaces=[
            SurfaceSpec(id="solid_r", kind="zcylinder",
                        parameters={"x0": 0.0, "y0": 0.0, "r": 0.4}),
            SurfaceSpec(id="pin_box", kind="rectangular_prism",
                        parameters={"xmin": -0.63, "xmax": 0.63, "ymin": -0.63, "ymax": 0.63}),
            SurfaceSpec(id="gt_inner_r", kind="zcylinder", parameters={"r": 0.561}),
            SurfaceSpec(id="gt_wall_r", kind="zcylinder", parameters={"r": 0.602}),
        ],
        regions=[
            RegionSpec(id="solid_region", expression="-solid_r", surface_ids=["solid_r"]),
            RegionSpec(id="coolant_region", expression="+solid_r & pin_box",
                       surface_ids=["solid_r", "pin_box"]),
            RegionSpec(id="gt_inner_region", expression="-gt_inner_r", surface_ids=["gt_inner_r"]),
            RegionSpec(id="gt_wall_region", expression="+gt_inner_r -gt_wall_r",
                       surface_ids=["gt_inner_r", "gt_wall_r"]),
            RegionSpec(id="gt_outer_region", expression="+gt_wall_r & pin_box",
                       surface_ids=["gt_wall_r", "pin_box"]),
        ],
        core=CoreSpec(
            id="vera3_core", name="VERA3 core", lattice_id="assembly_lattice",
            boundary="mixed",
            boundary_conditions=None,
            axial_layers=layers, axial_overlays=overlays,
        ),
        lattice_loadings=lattice_loadings,
        settings=RunSettingsSpec(batches=6, inactive=2, particles=50),
    )
    return SimulationPlan(
        schema_version="simulation_plan.v2", complex_model=model,
        capability_report=RenderCapabilityReport(is_executable=False, supported_renderer="none"),
        plot_specs=[PlotSpec(basis="xy", width_cm=(21.5, 21.5), filename="vera3.png")],
        execution_check=ExecutionCheckSpec(settings=RunSettingsSpec(batches=4, inactive=1, particles=20)),
    )


def _check_tube_radii(model: ComplexModelSpec, issues: list[BenchmarkIssue]) -> None:
    """Check guide/instrument tube outer radii against the contract.

    Only the outermost cylinder surface radius is checked; the inner radius
    is a different geometric boundary and is not an error.
    """
    expected = {
        "guide": 0.602,
        "instrument": 0.605,
    }
    for u in model.universes:
        uid_lower = u.id.lower()
        tube_type = None
        if "guide" in uid_lower or "gt" in uid_lower:
            tube_type = "guide"
        elif "instrument" in uid_lower or "inst" in uid_lower:
            tube_type = "instrument"
        if tube_type is None:
            continue
        u_cells = [c for c in model.cells if c.id in u.cell_ids]
        for cell in u_cells:
            if cell.component_role not in ("tube_wall", "cladding"):
                continue
            region = next((r for r in model.regions if r.id == cell.region_id), None) if cell.region_id else None
            if region is None:
                continue
            # Find the maximum cylinder radius in this wall region = outer radius
            outer_r = None
            for sid in region.surface_ids:
                surf = next((s for s in model.surfaces if s.id == sid), None)
                if surf and surf.kind == "zcylinder":
                    r = surf.parameters.get("r")
                    if r is not None:
                        if outer_r is None or r > outer_r:
                            outer_r = r
            if outer_r is not None and abs(outer_r - expected[tube_type]) > 0.001:
                code = "vera3.guide_tube_radius_mismatch" if tube_type == "guide" else "vera3.instrument_tube_radius_mismatch"
                issues.append(BenchmarkIssue(
                    code, "error",
                    f"{tube_type} tube outer radius should be {expected[tube_type]}, got {outer_r}",
                    expected=expected[tube_type], actual=outer_r,
                    path=f"complex_model.universes.{u.id}",
                ))


def _check_3b_nested_geometry(model: ComplexModelSpec, issues: list[BenchmarkIssue]) -> None:
    """Check 3B-specific nested geometry: operation kinds, coordinate counts,
    guide wall preservation, upper plenum multi-loading."""
    loadings_by_id = {l.id: l for l in model.lattice_loadings}

    # Pyrex must use nested_component_override
    pyrex_loading = loadings_by_id.get("pyrex_active_loading")
    if pyrex_loading is not None:
        nested_ops = [t for t in pyrex_loading.transformations if t.operation_kind == "nested_component_override"]
        if not nested_ops:
            issues.append(BenchmarkIssue(
                "vera3.pyrex_not_nested_in_guide", "error",
                "Pyrex loading should use nested_component_override operation kind",
            ))

    # Thimble must use nested_component_override with 8 coordinates
    thimble_loading = loadings_by_id.get("thimble_plug_loading")
    if thimble_loading is None:
        issues.append(BenchmarkIssue(
            "vera3.thimble_loading_missing", "error",
            "3B requires a thimble_plug_loading",
        ))
    else:
        nested_ops = [t for t in thimble_loading.transformations if t.operation_kind == "nested_component_override"]
        if not nested_ops:
            issues.append(BenchmarkIssue(
                "vera3.thimble_loading_missing", "error",
                "Thimble loading should use nested_component_override operation kind",
            ))
        elif len(nested_ops[0].target_coordinates) != 8:
            issues.append(BenchmarkIssue(
                "vera3.thimble_loading_missing", "error",
                "Thimble nested operation must target 8 coordinates",
                expected=8, actual=len(nested_ops[0].target_coordinates),
            ))

    # Upper plenum middle layer must have loading_ids = [plenum, thimble]
    if model.core is not None:
        for layer in model.core.axial_layers:
            if "upper_plenum_middle" in layer.id or "thimble" in layer.id.lower():
                if layer.loading_ids and len(layer.loading_ids) >= 2:
                    has_plenum = "plenum_loading" in layer.loading_ids
                    has_thimble = any("thimble" in lid for lid in layer.loading_ids)
                    if not (has_plenum and has_thimble):
                        issues.append(BenchmarkIssue(
                            "vera3.upper_plenum_loading_composition_mismatch", "error",
                            f"Upper plenum middle layer loading_ids must include plenum + thimble, got {layer.loading_ids}",
                            expected=["plenum_loading", "thimble_plug_loading"], actual=layer.loading_ids,
                        ))

__all__ = [
    "BenchmarkIssue",
    "CONTRACT_PATH",
    "REFERENCE_PATH",
    "build_vera3_like_plan",
    "collect_active_lattice_union",
    "collect_base_lattice_counts",
    "collect_loading_override_counts",
    "collect_nested_operation_coord_counts",
    "diagnose_vera3_component_geometry",
    "load_vera3_geometry_contract",
    "load_vera3_reference",
    "to_0_indexed",
    "validate_axial_layer_continuity",
    "validate_vera3_plan_structure",
]
