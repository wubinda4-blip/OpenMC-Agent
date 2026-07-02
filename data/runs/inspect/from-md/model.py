"""Generated OpenMC pin-cell model for single_pin_cell_case1."""

import openmc


material_0 = openmc.Material(name='fuel_uo2_placeholder')
material_0.set_density('g/cm3', 10.0)
material_0.add_nuclide('U235', 3.0, 'ao')
material_0.add_nuclide('U238', 97.0, 'ao')
material_0.add_nuclide('O16', 200.0, 'ao')

material_1 = openmc.Material(name='water_coolant_placeholder')
material_1.set_density('g/cm3', 1.0)
material_1.add_nuclide('H1', 2.0, 'ao')
material_1.add_nuclide('O16', 1.0, 'ao')

material_2 = openmc.Material(name='cladding_zr_placeholder')
material_2.set_density('g/cm3', 6.5)
material_2.add_element('Zr', 1.0, 'ao')

materials = openmc.Materials([material_0, material_1, material_2])

fuel_radius = 0.4215
pitch = 1.33
half_pitch = pitch / 2.0

fuel_surface = openmc.ZCylinder(r=fuel_radius)

clad_inner_radius = 0.43
clad_outer_radius = 0.5
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
settings.batches = 15
settings.inactive = 5
settings.particles = 500
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
