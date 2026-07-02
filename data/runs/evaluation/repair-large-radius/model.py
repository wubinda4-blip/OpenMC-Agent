"""Generated OpenMC pin-cell model for uo2_pincell_corrected."""

import openmc


material_0 = openmc.Material(name='uo2_fuel')
material_0.set_density('g/cm3', 10.0)
material_0.add_nuclide('U235', 0.031, 'ao')
material_0.add_nuclide('U238', 0.969, 'ao')
material_0.add_nuclide('O16', 2.0, 'ao')

material_1 = openmc.Material(name='water')
material_1.set_density('g/cm3', 0.997)
material_1.add_nuclide('H1', 2.0, 'ao')
material_1.add_nuclide('O16', 1.0, 'ao')

material_2 = openmc.Material(name='zircaloy4')
material_2.set_density('g/cm3', 6.55)
material_2.add_element('Zr', 98.23, 'wo')
material_2.add_element('Sn', 1.5, 'wo')
material_2.add_element('Fe', 0.2, 'wo')
material_2.add_element('Cr', 0.07, 'wo')

materials = openmc.Materials([material_0, material_1, material_2])

fuel_radius = 0.4096
pitch = 1.26
half_pitch = pitch / 2.0

fuel_surface = openmc.ZCylinder(r=fuel_radius)

clad_inner_radius = 0.418
clad_outer_radius = 0.475
clad_inner_surface = openmc.ZCylinder(r=clad_inner_radius)
clad_outer_surface = openmc.ZCylinder(r=clad_outer_radius)

x_min = openmc.XPlane(x0=-half_pitch, boundary_type="reflective")
x_max = openmc.XPlane(x0=half_pitch, boundary_type="reflective")
y_min = openmc.YPlane(y0=-half_pitch, boundary_type="reflective")
y_max = openmc.YPlane(y0=half_pitch, boundary_type="reflective")
boundary_region = +x_min & -x_max & +y_min & -y_max

fuel_cell = openmc.Cell(name="fuel", fill=material_0, region=-fuel_surface)
gap_cell = openmc.Cell(name="gap", region=+fuel_surface & -clad_inner_surface)
clad_cell = openmc.Cell(
    name="cladding",
    fill=material_2,
    region=+clad_inner_surface & -clad_outer_surface,
)
moderator_region = +clad_outer_surface & boundary_region
moderator_cell = openmc.Cell(name="moderator", fill=material_1, region=moderator_region)
cells = [fuel_cell, gap_cell, clad_cell, moderator_cell]

root_universe = openmc.Universe(cells=cells)
geometry = openmc.Geometry(root_universe)

settings = openmc.Settings()
settings.run_mode = 'eigenvalue'
settings.batches = 50
settings.inactive = 10
settings.particles = 1000
settings.source = openmc.IndependentSource(
    space=openmc.stats.Box(
        (-half_pitch, -half_pitch, -1.0),
        (half_pitch, half_pitch, 1.0),
        only_fissionable=True,
    )
)

flux_tally = openmc.Tally(name="cell flux")
flux_tally.filters = [openmc.CellFilter(cells)]
flux_tally.scores = ["flux"]
tallies = openmc.Tallies([flux_tally])

model = openmc.Model(
    materials=materials,
    geometry=geometry,
    settings=settings,
    tallies=tallies,
)
model.export_to_xml()
