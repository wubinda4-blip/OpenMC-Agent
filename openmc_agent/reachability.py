"""Active-graph reachability analysis for complex-model plans.

The capability / renderer layer must judge executability from the objects the
*default* model actually uses, not from every object declared in the IR. A
candidate subsystem that is defined but never inserted into the default lattice
(for example a burnable-poison universe kept around for later use) must not turn
its incomplete materials into blocking errors.

:meth:`collect_active_dependencies` walks the default-model entry points
(assembly/core lattice id, then ``lattice.universe_pattern`` and ``rings``,
``outer_universe_id``, universes, cells, regions, surfaces, and material /
universe / lattice fills) and partitions declared objects into *active* and
*inactive* sets. Renderers consult the active sets before deciding whether a
missing density or composition is blocking.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from openmc_agent.schemas import ComplexModelSpec, SimulationPlan


@dataclass
class ActiveDependencies:
    """Objects reachable from the default model, plus the leftover inactive sets.

    ``*_ids`` fields hold the ids reached from the default entry points.
    ``inactive_*_ids`` hold ids that exist in the IR but were not reached, i.e.
    candidate / optional subsystems whose gaps must not block the default model.
    """

    lattice_ids: set[str] = field(default_factory=set)
    universe_ids: set[str] = field(default_factory=set)
    cell_ids: set[str] = field(default_factory=set)
    region_ids: set[str] = field(default_factory=set)
    surface_ids: set[str] = field(default_factory=set)
    material_ids: set[str] = field(default_factory=set)
    inactive_universe_ids: set[str] = field(default_factory=set)
    inactive_material_ids: set[str] = field(default_factory=set)


def collect_active_dependencies(plan: SimulationPlan) -> ActiveDependencies:
    """Walk the default-model graph and partition declared objects by reachability.

    Returns an empty :class:`ActiveDependencies` when the plan has no
    ``complex_model`` (e.g. pin-cell plans), so callers can treat the result
    uniformly.
    """
    model = plan.complex_model
    if model is None:
        return ActiveDependencies()
    return collect_active_dependencies_from_model(model)


def collect_active_dependencies_from_model(model: ComplexModelSpec) -> ActiveDependencies:
    """Same as :func:`collect_active_dependencies` but from a bare complex model.

    Shared with executor helpers that hold a ``ComplexModelSpec`` and not a full
    plan.
    """
    deps = ActiveDependencies()

    cells_by_id = {cell.id: cell for cell in model.cells}
    universes_by_id = {universe.id: universe for universe in model.universes}
    lattices_by_id = {lattice.id: lattice for lattice in model.lattices}
    regions_by_id = {region.id: region for region in model.regions}

    visited_lattices: set[str] = set()
    visited_universes: set[str] = set()
    visited_cells: set[str] = set()

    def visit_lattice(lattice_id: str) -> None:
        if lattice_id in visited_lattices:
            return
        visited_lattices.add(lattice_id)
        lattice = lattices_by_id.get(lattice_id)
        if lattice is None:
            return
        deps.lattice_ids.add(lattice.id)
        # Rect lattices lay universes out by row/column; hex lattices by ring.
        for row in lattice.universe_pattern:
            for uid in row:
                visit_universe(uid)
        for ring in lattice.rings:
            for uid in ring:
                visit_universe(uid)
        if lattice.outer_universe_id:
            visit_universe(lattice.outer_universe_id)

    def visit_universe(universe_id: str) -> None:
        if universe_id in visited_universes:
            return
        visited_universes.add(universe_id)
        universe = universes_by_id.get(universe_id)
        if universe is None:
            # Referenced but not declared; the structural check reports this.
            return
        deps.universe_ids.add(universe.id)
        for cell_id in universe.cell_ids:
            visit_cell(cell_id)

    def visit_cell(cell_id: str) -> None:
        if cell_id in visited_cells:
            return
        visited_cells.add(cell_id)
        cell = cells_by_id.get(cell_id)
        if cell is None:
            return
        deps.cell_ids.add(cell.id)
        if cell.region_id and cell.region_id in regions_by_id:
            deps.region_ids.add(cell.region_id)
            deps.surface_ids.update(regions_by_id[cell.region_id].surface_ids)
        if cell.fill_type == "material" and cell.fill_id:
            deps.material_ids.add(cell.fill_id)
        elif cell.fill_type == "universe" and cell.fill_id:
            visit_universe(cell.fill_id)
        elif cell.fill_type == "lattice" and cell.fill_id:
            visit_lattice(cell.fill_id)

    # Default-model entry points: the assembly / core lattice.
    seed_lattices: list[str] = []
    for assembly in model.assemblies:
        if assembly.lattice_id:
            seed_lattices.append(assembly.lattice_id)
    if model.core is not None and model.core.lattice_id:
        seed_lattices.append(model.core.lattice_id)
    # Fall back to declared lattices when no root container points at one, so a
    # bare lattice-only model still resolves an active graph.
    if not seed_lattices:
        seed_lattices.extend(lattices_by_id.keys())

    for lattice_id in seed_lattices:
        visit_lattice(lattice_id)

    # Reflectors and control rods are part of the default assembly model (they
    # are not candidate / optional subsystems), so their materials and regions
    # are active regardless of lattice reachability.
    for reflector in model.reflectors:
        if reflector.material_id:
            deps.material_ids.add(reflector.material_id)
        if reflector.region_id and reflector.region_id in regions_by_id:
            deps.region_ids.add(reflector.region_id)
            deps.surface_ids.update(regions_by_id[reflector.region_id].surface_ids)
    for control_rod in model.control_rods:
        if control_rod.absorber_material_id:
            deps.material_ids.add(control_rod.absorber_material_id)
        if (
            control_rod.guide_tube_region_id is not None
            and control_rod.guide_tube_region_id in regions_by_id
        ):
            deps.region_ids.add(control_rod.guide_tube_region_id)
            deps.surface_ids.update(
                regions_by_id[control_rod.guide_tube_region_id].surface_ids
            )

    all_material_ids = {material.id for material in model.materials}
    all_universe_ids = {universe.id for universe in model.universes}
    deps.inactive_universe_ids = all_universe_ids - deps.universe_ids
    deps.inactive_material_ids = all_material_ids - deps.material_ids
    return deps
