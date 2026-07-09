from pywarpx import picmi
import numpy as np

# Physical constants
c = picmi.constants.c
q_e = picmi.constants.q_e

# Number of time steps
max_steps = 4000

# Number of cells
nx = 128
ny = 128
nz = 1024

# Physical domain
xmin = -20e-06
xmax = 20e-06
ymin = -20e-06
ymax = 20e-06
zmin = 0 
zmax = 41e-06

# Domain decomposition
max_grid_size = 64
blocking_factor = 32

# Plasma parameters
plasma_density = 1e+25
omega_p = np.sqrt(plasma_density * q_e ** 2 / picmi.constants.ep0 * picmi.constants.m_e)
lambda_p = 2 * np.pi * c / omega_p
plasma_xmin = -10e-06
plasma_xmax = 10e-06
plasma_ymin = -10e-06
plasma_ymax = 10e-06
plasma_zmin = 0
plasma_zmax = 0.0002513442739844322
cnt_radius = 2e-06
distance = 7.5e-07
D = 2 * cnt_radius + distance
h = np.sqrt(3) * D / 2


hexagonal_density_expression = '''
    n0 * if(
        (
            (x - (D * floor((x - D * 0.5 * (floor(y / h + 0.5) - 2 * floor(floor(y / h + 0.5) / 2))) / D + 0.5) + D * 0.5 * (floor(y / h + 0.5) - 2 * floor(floor(y / h + 0.5) / 2))))^2 +
            (y - (h * floor(y / h + 0.5)))^2
        ) <= r^2,
        1, 0
    )
'''

# Laser
wavelength = 0.8e-06
I_peak_Wcm2 = 5e+20
I_peak = I_peak_Wcm2 * 1e4
pulse_length = lambda_p
profile_t_peak = 30.0e-15
waist = 1e-06
e_max = np.sqrt(2*I_peak / (picmi.constants.ep0 * c))
omega_laser = 2 * np.pi * c / wavelength

foc_dist = 1 # Afocal
pulse_duration = pulse_length / c
position_z = zmax - pulse_length
centroid_position_z = -pulse_length

# Window setup

dz = (zmax - zmin) / nz
dt_estimated = 7.32214e-17
group_velocity = 0.95 * c * np.sqrt((1 - (omega_p / omega_laser) ** 2))

# Create grid
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
    pml_cells=[8, 8, 16]
)

# Plasma structure

analytic_distribution_electrons = picmi.AnalyticDistribution(
    density_expression=hexagonal_density_expression,
    n0=3*plasma_density,
    D=D,
    r=cnt_radius,
    h=h,
    fill_in = True
)

analytic_distribution_ions = picmi.AnalyticDistribution(
    density_expression=hexagonal_density_expression,
    n0=plasma_density,
    D=D,
    r=cnt_radius,
    h=h,
    fill_in = True
)

carbon_ions = picmi.Species(
    particle_type="C",
    name="carbon_ions",
    initial_distribution=analytic_distribution_ions,
    charge_state=3,
    warpx_do_not_push=True,
    warpx_add_int_attributes={"regionofinterest": f"(x*x + y*y < {waist**2})"}
)

electrons = picmi.Species(
    particle_type="electron",
    name="electrons",
    charge=-picmi.constants.q_e,
    initial_distribution=analytic_distribution_electrons,
    warpx_add_real_attributes={"thermal_energy": "ux*ux+uy*uy"}, # If it doesnt work use also uz
    warpx_add_int_attributes={"regionofinterest": f"(x*x + y*y < {waist**2})"}
)

field_ionization = picmi.FieldIonization(
    model="ADK",
    ionized_species=carbon_ions,
    product_species=electrons
)

#electron_ion_collision = picmi.CoulombCollisions(
#    name="electron_ion_collision",
#    species=[electrons, carbon_ions],
#    CoulombLog=None,
#    ndt=10
#)

# Particles: beam electrons
q_tot = 1e-12
x_m = 0.0
y_m = 0.0
x_rms = 1e-06
y_rms = 1e-06
z_rms = 0.5e-06
position_beam_z = zmin-2*z_rms
ux_m = 0.0
uy_m = 0.0
uz_m = 500.0
ux_th = 5.0
uy_th = 5.0
uz_th = 50.0
gaussian_bunch_distribution = picmi.GaussianBunchDistribution(
    n_physical_particles=q_tot / q_e,
    rms_bunch_size=[x_rms, y_rms, z_rms],
    rms_velocity=[c * ux_th, c * uy_th, c * uz_th],
    centroid_position=[x_m, y_m, position_beam_z],
    centroid_velocity=[c * ux_m, c * uy_m, c * uz_m],
)
beam = picmi.Species(
    particle_type="electron",
    name="beam",
    initial_distribution=gaussian_bunch_distribution
)

pml_thickness_z = grid.pml_cells[2] * ((zmax-zmin)/nz)

laser = picmi.GaussianLaser(
    wavelength=wavelength,
    waist=waist,
    duration=pulse_duration,
    focal_position=[0, 0, 45e-06],
    centroid_position=[0, 0, 45e-06],
    propagation_direction=[0, 0, 1],
    polarization_direction=[0, 1, 0],
    E0=e_max,
    fill_in=False
)
laser_antenna = picmi.LaserAntenna(
    position=[0.0, 0.0, 37e-06], normal_vector=[0, 0, 1]
)

# Electromagnetic solver
solver = picmi.ElectromagneticSolver(
    grid=grid, 
    method="Yee", 
    cfl=1.0, 
    divE_cleaning=0, 
    warpx_do_pml_in_domain=True,
    warpx_pml_has_particles = False,
    warpx_do_pml_j_damping=True
)

# Diagnostics
diag_field_list = ["B", "E", "J", "rho"]
particle_diag = picmi.ParticleDiagnostic(
    name="diag1",
    period=100,
    write_dir=".",
    warpx_file_prefix="3D",
    warpx_format="openpmd",
    warpx_openpmd_backend="h5"
)
field_diag = picmi.FieldDiagnostic(
    name="diag1",
    grid=grid,
    period=100,
    data_list=diag_field_list,
    write_dir=".",
    warpx_file_prefix="3D",
    warpx_format="openpmd",
    warpx_openpmd_backend="h5"
)

# Set up simulation
sim = picmi.Simulation(
    solver=solver,
    max_steps=max_steps,
    verbose=1,
    particle_shape="cubic",
    warpx_use_filter=1,
    warpx_serialize_initial_conditions=1,
    warpx_do_dynamic_scheduling=0
)

# Add plasma electrons
sim.add_species(
    electrons, layout=picmi.GriddedLayout(grid=grid, n_macroparticle_per_cell=[1,1,1])
)

sim.add_species(
    carbon_ions, layout=picmi.GriddedLayout(grid=grid, n_macroparticle_per_cell=[1,1,1])
)

# Add beam electrons
sim.add_species(beam, layout=picmi.PseudoRandomLayout(grid=grid, n_macroparticles=5000))

# Add laser
sim.add_laser(laser, injection_method=laser_antenna)

# Add diagnostics
sim.add_diagnostic(particle_diag)
sim.add_diagnostic(field_diag)

# Write input file that can be used to run with the compiled version
sim.write_input_file(file_name="inputs_3d_picmi")

# Initialize inputs and WarpX instance
sim.initialize_inputs()
sim.initialize_warpx()

# Advance simulation until last time step
sim.step(max_steps)