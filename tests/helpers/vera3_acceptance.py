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
    NuclideSpec,
    PlotSpec,
    RenderCapabilityReport,
    RunSettingsSpec,
    SimulationPlan,
    UniverseSpec,
)

REFERENCE_PATH = Path("tests/fixtures/vera3_reference.json")


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

    # 1c. active fuel height
    fuel_layer = next((L for L in layers if L.fill.type == "lattice"), None)
    exp_fuel = meta["active_fuel_region_cm"]
    if fuel_layer is None:
        issues.append(BenchmarkIssue("vera3.active_fuel_height_mismatch", "error",
                                     "no lattice-filled active fuel layer found"))
    else:
        if abs(fuel_layer.z_min_cm - exp_fuel[0]) > tol or abs(fuel_layer.z_max_cm - exp_fuel[1]) > tol:
            issues.append(BenchmarkIssue("vera3.active_fuel_height_mismatch", "error",
                                         "active fuel region z-range mismatch",
                                         expected=list(exp_fuel),
                                         actual=[fuel_layer.z_min_cm, fuel_layer.z_max_cm]))

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
        for letter, exp_count in pm["counts"].items():
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
        # pyrex (3B)
        for pos in pm["pyrex_positions_1based"]:
            _check_coord("P", pos, "pyrex rod", "vera3.pyrex_coordinate_mismatch")
        # plugs (3B)
        for pos in pm["plug_positions_1based"]:
            _check_coord("T", pos, "thimble plug", "vera3.plug_coordinate_mismatch")

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
    """
    cells: list[CellSpec] = []
    universes: list[UniverseSpec] = []

    def _pin(uid: str, uname: str, solid_cell_id: str, solid_mat: str) -> None:
        coolant_id = f"{solid_cell_id}_coolant"
        cells.append(CellSpec(id=solid_cell_id, name=f"{uname} solid",
                              fill_type="material", fill_id=solid_mat))
        cells.append(CellSpec(id=coolant_id, name=f"{uname} coolant",
                              fill_type="material", fill_id="water"))
        universes.append(UniverseSpec(id=uid, name=uname, cell_ids=[solid_cell_id, coolant_id]))

    _pin("fuel_pin", "fuel pin", "fuel_pellet", "fuel")
    if pure_water_guide:
        cells.append(CellSpec(id="guide_water", name="guide water",
                              fill_type="material", fill_id="water"))
        universes.append(UniverseSpec(id="guide_tube", name="guide tube", cell_ids=["guide_water"]))
    else:
        _pin("guide_tube", "guide tube", "guide_tube_wall", "clad")
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
        "P": pm["pyrex_positions_1based"],
        "T": pm["plug_positions_1based"],
    }
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
    materials = _vera3_materials(variant)
    cells, universes = _vera3_pin_universes(variant, pure_water_guide=pure_water_guide)

    pattern = _vera3_lattice_pattern(reference, variant)
    if mutate_pin is not None:
        r0, c0 = mutate_pin  # 0-indexed; replace whatever is there with a fuel pin
        pattern[r0][c0] = "fuel_pin"
    if wrong_pyrex is not None and variant == "3B":
        # Relocate a pyrex rod: clear its first documented position (-> fuel) and
        # place a pyrex at the given wrong 0-indexed (fuel) coordinate.
        first_pyrex_0 = to_0_indexed(reference["pin_maps"]["3B"]["pyrex_positions_1based"][0])
        pattern[first_pyrex_0[0]][first_pyrex_0[1]] = "fuel_pin"
        r0, c0 = wrong_pyrex
        pattern[r0][c0] = "pyrex_pin"

    lattices = [LatticeSpec(
        id="assembly_lattice", name="VERA3 17x17 assembly", kind="rect",
        pitch_cm=(meta["pin_pitch_cm"], meta["pin_pitch_cm"]),
        universe_pattern=pattern,
    )]

    # axial layers (transcribed from reference)
    ref_layers = reference["axial_layers"]
    if default_z:
        layers = [AxialLayerSpec(id="fuel", name="fuel", z_min_cm=-1.0, z_max_cm=1.0,
                                 fill={"type": "lattice", "id": "assembly_lattice"})]
    else:
        layers = []
        for rl in ref_layers:
            if use_material_slab_grid and rl["id"] == "active_fuel":
                fill = {"type": "material", "id": "grid_inconel"}
            elif rl["id"] == "active_fuel":
                fill = {"type": "lattice", "id": "assembly_lattice"}
            else:
                mat_map = {"borated_water": "water", "zircaloy4": "clad", "helium": "helium",
                           "ss304_coolant_50_50": "ss304", "ss304_coolant_nozzle": "ss304",
                           "uo2_fuel_lattice": "assembly_lattice"}
                fill = {"type": "material", "id": mat_map.get(rl["material"], "water")}
            layers.append(AxialLayerSpec(
                id=rl["id"], name=rl["id"], z_min_cm=rl["z_min_cm"], z_max_cm=rl["z_max_cm"],
                fill=fill,
                purpose=("Active fuel with spacer grid sub-layers" if rl["id"] == "active_fuel" else ""),
            ))

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
        core=CoreSpec(
            id="vera3_core", name="VERA3 core", lattice_id="assembly_lattice",
            boundary="mixed",
            boundary_conditions=None,
            axial_layers=layers, axial_overlays=overlays,
        ),
        settings=RunSettingsSpec(batches=6, inactive=2, particles=50),
    )
    return SimulationPlan(
        schema_version="simulation_plan.v2", complex_model=model,
        capability_report=RenderCapabilityReport(is_executable=False, supported_renderer="none"),
        plot_specs=[PlotSpec(basis="xy", width_cm=(21.5, 21.5), filename="vera3.png")],
        execution_check=ExecutionCheckSpec(settings=RunSettingsSpec(batches=4, inactive=1, particles=20)),
    )


__all__ = [
    "BenchmarkIssue",
    "REFERENCE_PATH",
    "build_vera3_like_plan",
    "load_vera3_reference",
    "to_0_indexed",
    "validate_vera3_plan_structure",
]
