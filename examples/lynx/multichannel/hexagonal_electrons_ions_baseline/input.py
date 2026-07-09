from pywarpx import picmi
import numpy as np

# ---------------------------------------------------------------------------
# Hexagonal CNT / multichannel baseline for Lynx.
#
# Purpose:
#   - 3D physical baseline derived from legacy hexagonal_buenas/combination_1
#   - plasma electrons + fixed C3+ ions
#   - no injected beam
#   - no field ionization yet
#   - analyze final electron beam-like behavior with multichannel-lfmetrics
#
# This is a real baseline run, not a smoke test.
# ---------------------------------------------------------------------------

# Physical constants
c = picmi.constants.c
q_e = picmi.constants.q_e
eps0 = picmi.constants.ep0
m_e = picmi.constants.m_e

# Number of time steps
max_steps = 4000

# Number of cells: legacy good-resolution setup
nx = 128
ny = 128
nz = 1024

# Physical domain
xmin = -20e-6
xmax = 20e-6
ymin = -20e-6
ymax = 20e-6
zmin = 0.0
zmax = 41e-6

# Domain decomposition
max_grid_size = 64
blocking_factor = 32

# Plasma parameters
plasma_density = 1.0e25

# Correct electron plasma frequency:
# omega_p = sqrt(n e^2 / (eps0 m_e))
omega_p = np.sqrt(plasma_density * q_e**2 / (eps0 * m_e))
lambda_p = 2.0 * np.pi * c / omega_p

cnt_radius = 2e-6
distance = 7.5e-7
D = 2.0 * cnt_radius + distance
h = np.sqrt(3.0) * D / 2.0

hexagonal_density_expression = """
    n0 * if(
        (
            (x - (D * floor((x - D * 0.5 * (floor(y / h + 0.5) - 2 * floor(floor(y / h + 0.5) / 2))) / D + 0.5) + D * 0.5 * (floor(y / h + 0.5) - 2 * floor(floor(y / h + 0.5) / 2))))^2 +
            (y - (h * floor(y / h + 0.5)))^2
        ) <= r^2,
        1, 0
    )
"""

# Laser
wavelength = 0.8e-6
I_peak_Wcm2 = 5.0e20
I_peak = I_peak_Wcm2 * 1.0e4

pulse_length = lambda_p
pulse_duration = pulse_length / c

profile_t_peak = 30.0e-15
waist = 1.0e-6

e_max = np.sqrt(2.0 * I_peak / (eps0 * c))
omega_laser = 2.0 * np.pi * c / wavelength

# Moving window
group_velocity = 0.95 * c * np.sqrt(1.0 - (omega_p / omega_laser) ** 2)

grid = picmi.Cartesian3DGrid(
    number_of_cells=[nx, ny, nz],
    lower_bound=[xmin, ymin, zmin],
    upper_bound=[xmax, ymax, zmax],
    lower_boundary_conditions=["open", "open", "open"],
    upper_boundary_conditions=["open", "open", "open"],
    lower_boundary_conditions_particles=["open", "open", "open"],
    upper_boundary_conditions_particles=["open", "open", "open"],
    moving_window_velocity=[0.0, 0.0, group_velocity],
    warpx_start_moving_window_step=0,
    warpx_max_grid_size=max_grid_size,
    warpx_blocking_factor=blocking_factor,
    pml_cells=[8, 8, 16],
)

# Plasma structure: neutral pre-ionized C3+ target.
# electrons n0 = 3 * plasma_density balances carbon_ions charge_state = +3.
analytic_distribution_electrons = picmi.AnalyticDistribution(
    density_expression=hexagonal_density_expression,
    n0=3.0 * plasma_density,
    D=D,
    r=cnt_radius,
    h=h,
    fill_in=True,
)

analytic_distribution_ions = picmi.AnalyticDistribution(
    density_expression=hexagonal_density_expression,
    n0=plasma_density,
    D=D,
    r=cnt_radius,
    h=h,
    fill_in=True,
)

electrons = picmi.Species(
    particle_type="electron",
    name="electrons",
    charge=-q_e,
    initial_distribution=analytic_distribution_electrons,
    warpx_add_real_attributes={
        "thermal_energy": "ux*ux+uy*uy",
    },
    warpx_add_int_attributes={
        "regionofinterest": f"(x*x + y*y < {waist**2})",
    },
)

carbon_ions = picmi.Species(
    particle_type="C",
    name="carbon_ions",
    initial_distribution=analytic_distribution_ions,
    charge_state=3,
    warpx_do_not_push=True,
    warpx_add_int_attributes={
        "regionofinterest": f"(x*x + y*y < {waist**2})",
    },
)

# No FieldIonization in this baseline.
# Future campaign question:
#   - pre-ionized electrons vs ionized_electrons product species
#   - whether the resulting beam is distinguishable or physically equivalent
#
# WarpX supports PICMI FieldIonization with ADK, but we intentionally leave it
# out here to keep this baseline interpretable.
# See future extension:
#   field_ionization = picmi.FieldIonization(...)

laser = picmi.GaussianLaser(
    wavelength=wavelength,
    waist=waist,
    duration=pulse_duration,
    focal_position=[0.0, 0.0, 45e-6],
    centroid_position=[0.0, 0.0, 45e-6],
    propagation_direction=[0.0, 0.0, 1.0],
    polarization_direction=[0.0, 1.0, 0.0],
    E0=e_max,
    fill_in=False,
)

laser_antenna = picmi.LaserAntenna(
    position=[0.0, 0.0, 37e-6],
    normal_vector=[0.0, 0.0, 1.0],
)

solver = picmi.ElectromagneticSolver(
    grid=grid,
    method="Yee",
    cfl=1.0,
    divE_cleaning=0,
    warpx_do_pml_in_domain=True,
    warpx_pml_has_particles=False,
    warpx_do_pml_j_damping=True,
)

# Particle diagnostic.
# LFMetrics v0.1 reads electrons from this openPMD/HDF5 output.
particle_diag = picmi.ParticleDiagnostic(
    name="diag1",
    period=max_steps,
    write_dir=".",
    warpx_file_prefix="3D",
    warpx_format="openpmd",
    warpx_openpmd_backend="h5",
)

# Sparse field diagnostic for physical auditing.
# This is not required by LFMetrics but helps inspect the run if needed.
field_diag = picmi.FieldDiagnostic(
    name="diag_fields",
    grid=grid,
    period=max_steps,
    data_list=["B", "E", "rho"],
    write_dir=".",
    warpx_file_prefix="fields3D",
    warpx_format="openpmd",
    warpx_openpmd_backend="h5",
)

sim = picmi.Simulation(
    solver=solver,
    max_steps=max_steps,
    verbose=1,
    particle_shape="cubic",
    warpx_use_filter=1,
    warpx_serialize_initial_conditions=1,
    warpx_do_dynamic_scheduling=0,
)

sim.add_species(
    electrons,
    layout=picmi.GriddedLayout(
        grid=grid,
        n_macroparticle_per_cell=[1, 1, 1],
    ),
)

sim.add_species(
    carbon_ions,
    layout=picmi.GriddedLayout(
        grid=grid,
        n_macroparticle_per_cell=[1, 1, 1],
    ),
)

sim.add_laser(laser, injection_method=laser_antenna)

sim.add_diagnostic(particle_diag)
sim.add_diagnostic(field_diag)

# Keep a generated inputs file for auditing/debugging if PICMI supports it.
sim.write_input_file(file_name="inputs_3d_picmi")

# Run WarpX through the normal PICMI Python path.
sim.step(max_steps)
