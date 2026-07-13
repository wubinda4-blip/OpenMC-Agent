"""Full-core MVP: schema, renderer, and 3×3 transport smoke.

Generates a self-contained model.py that creates a reusable assembly universe,
places it in a 2D core RectLattice, and wraps with a vacuum outer boundary.
"""
from __future__ import annotations

import json
from pathlib import Path
from dataclasses import dataclass


def generate_full_core_model(
    assembly_model_script: str,
    *,
    core_size: int = 3,
    assembly_pitch_cm: float = 21.50,
    reflector_thickness_cm: float = 21.50,
    reflector_material_id: str = "borated_water_3a",
    batches: int = 30,
    inactive: int = 10,
    particles: int = 2000,
) -> str:
    """Generate a full-core model.py from an assembly model.py script.

    Strategy:
    1. Execute the assembly script's geometry/material definitions
    2. Extract the assembly root universe
    3. Set assembly boundary surfaces to 'transmission'
    4. Create a core RectLattice filled with the assembly universe
    5. Wrap with reflector + vacuum outer boundary
    """
    # The assembly script defines materials_by_id, universes, etc.
    # and ends with root_universe. We need to:
    # 1. Remove the root cell creation and boundary setting
    # 2. Keep all material/cell/universe/lattice definitions
    # 3. Add core lattice + outer boundary

    lines = assembly_model_script.splitlines()

    # The assembly script defines root_universe at the end.
    # Keep the entire script (with boundary types changed to transmission).
    reusable = assembly_model_script

    # Replace 'reflective' boundary types with 'transmission' for assembly surfaces
    reusable = reusable.replace("'reflective'", "'transmission'")
    reusable = reusable.replace('"reflective"', '"transmission"')

    # Strip x/y boundary from root cell regions — the core lattice provides x/y clipping.
    # Root regions look like: +assembly_xmin & -assembly_xmax & +assembly_ymin & -assembly_ymax & +z_lo & -z_hi
    # We want to keep only: +z_lo & -z_hi
    import re
    reusable = re.sub(
        r'= \+assembly_xmin & -assembly_xmax & \+assembly_ymin & -assembly_ymax & ',
        '= ',
        reusable
    )

    # Core dimensions
    core_extent = core_size * assembly_pitch_cm + 2 * reflector_thickness_cm
    core_lower = -core_extent / 2
    core_upper = core_extent / 2
    core_pitch = assembly_pitch_cm
    n_total = core_size + 2  # including reflector ring

    full_core_script = f'''"""Full-core MVP model — {core_size}x{core_size} assembly lattice.

Auto-generated from assembly model. Engineering smoke, NOT benchmark.
"""

import openmc
import warnings
warnings.filterwarnings('ignore')

# ============================================================
# Part 1: Assembly geometry (reusable, boundaries=transmission)
# ============================================================

{reusable}

# ============================================================
# Part 2: Core lattice
# ============================================================

# The assembly root universe (from Part 1) is our reusable element.
# Create a homogenized reflector material for the reflector ring.
reflector_mat = openmc.Material(name='Homogenized Reflector')
reflector_mat.set_density('g/cm3', 0.743)
reflector_mat.add_nuclide('H1', 0.0664, 'ao')
reflector_mat.add_nuclide('O16', 0.0333, 'ao')
reflector_mat.add_nuclide('B10', 1.43e-5, 'ao')
reflector_mat.add_nuclide('B11', 5.76e-5, 'ao')
reflector_mat.add_element('Fe', 0.01, 'ao')

# Reflector universe (single material fill)
reflector_cell = openmc.Cell(name='reflector', fill=reflector_mat)
reflector_universe = openmc.Universe(name='reflector_universe', cells=[reflector_cell])

# Core map: {core_size}x{core_size} assemblies + reflector ring
core_size = {core_size}
core_pitch = {core_pitch!r}
n = core_size + 2  # total map size including reflector

# Build core map: assembly positions in center, reflector around edges
assembly_univ = root_universe  # reusable assembly from Part 1

# Core lattice pattern — must use Universe objects
core_pattern = []
for row in range(n):
    pattern_row = []
    for col in range(n):
        if row == 0 or row == n - 1 or col == 0 or col == n - 1:
            pattern_row.append(reflector_universe)
        else:
            pattern_row.append(assembly_univ)
    core_pattern.append(pattern_row)

# Create core lattice
core_lattice = openmc.RectLattice(name='core_lattice')
core_lattice.pitch = (core_pitch, core_pitch)
core_lattice.lower_left = (-core_pitch * n / 2, -core_pitch * n / 2)
core_lattice.universes = core_pattern
core_lattice.outer = reflector_universe

# ============================================================
# Part 3: Root geometry with vacuum boundary
# ============================================================

core_lower = {core_lower!r}
core_upper = {core_upper!r}

# Axial bounds from the assembly model
axial_z_min = assembly_z_min  # defined in Part 1
axial_z_max = assembly_z_max

core_xmin = openmc.XPlane(x0=core_lower, boundary_type='vacuum')
core_xmax = openmc.XPlane(x0=core_upper, boundary_type='vacuum')
core_ymin = openmc.YPlane(y0=core_lower, boundary_type='vacuum')
core_ymax = openmc.YPlane(y0=core_upper, boundary_type='vacuum')
core_zmin = openmc.ZPlane(z0=axial_z_min, boundary_type='vacuum')
core_zmax = openmc.ZPlane(z0=axial_z_max, boundary_type='vacuum')

root_cell = openmc.Cell(
    name='root',
    fill=core_lattice,
    region=+core_xmin & -core_xmax & +core_ymin & -core_ymax & +core_zmin & -core_zmax
)
root_universe = openmc.Universe(cells=[root_cell])
geometry = openmc.Geometry(root=root_universe)

# ============================================================
# Part 4: Materials
# ============================================================

materials = openmc.Materials(list(materials_by_id.values()) + [reflector_mat])

# ============================================================
# Part 5: Settings
# ============================================================

settings = openmc.Settings()
settings.batches = {batches}
settings.inactive = {inactive}
settings.particles = {particles}

# Source in center of core, within active fuel region
fuel_z_min = 11.951
fuel_z_max = 377.711
source_dist = openmc.stats.Box(
    (-core_pitch * core_size / 4, -core_pitch * core_size / 4, fuel_z_min),
    (core_pitch * core_size / 4, core_pitch * core_size / 4, fuel_z_max),
    only_fissionable=True
)
settings.source = openmc.IndependentSource(space=source_dist)
settings.temperature = {{
    'method': 'interpolation',
    'multipole': True,
}}

# ============================================================
# Part 6: Tallies
# ============================================================

tallies = openmc.Tallies()

# Assembly-level fission tally using mesh filter
mesh = openmc.RegularMesh()
mesh.dimension = [core_size, core_size, 1]
mesh.lower_left = [-core_pitch * core_size / 2, -core_pitch * core_size / 2, fuel_z_min]
mesh.upper_right = [core_pitch * core_size / 2, core_pitch * core_size / 2, fuel_z_max]

mesh_filter = openmc.MeshFilter(mesh)
tally = openmc.Tally(name='assembly_fission')
tally.filters = [mesh_filter]
tally.scores = ['fission']
tallies.append(tally)

# ============================================================
# Export
# ============================================================

geometry.export_to_xml()
materials.export_to_xml()
settings.export_to_xml()
tallies.export_to_xml()
plots = openmc.Plots()

# XY plot at mid-plane
plot_xy = openmc.Plot()
plot_xy.filename = 'core_xy'
plot_xy.width = [core_upper - core_lower, core_upper - core_lower]
plot_xy.basis = 'xy'
plot_xy.pixels = [400, 400]
plots.append(plot_xy)

plots.export_to_xml()

print(f"Full-core MVP: {{core_size}}x{{core_size}} assemblies, pitch={{core_pitch}} cm")
print(f"Core extent: {{core_lower}} to {{core_upper}} cm")
print(f"Batches={{settings.batches}}, inactive={{settings.inactive}}, particles={{settings.particles}}")
'''
    return full_core_script


if __name__ == "__main__":
    import sys

    # Read the assembly model.py
    assembly_script = Path("data/evals/vera3_geometry/3A/model.py").read_text()

    # Generate full-core model
    full_core = generate_full_core_model(assembly_script, core_size=3)

    outdir = Path("data/evals/full_core_mvp/geometry")
    outdir.mkdir(parents=True, exist_ok=True)
    (outdir / "model.py").write_text(full_core)
    print(f"Wrote {outdir / 'model.py'} ({len(full_core)} chars)")
