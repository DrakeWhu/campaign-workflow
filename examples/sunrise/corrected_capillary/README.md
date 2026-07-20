# Corrected CLPU capillary campaign on SUNRISE

This template fixes the physical ambiguity found after the first CLPU
optimization. It keeps the proven `campaign-workflow` lifecycle and changes
only the campaign-specific input, reduced products and optimizer contract.

## Laser conventions and pulse-duration scan

Carlos's document labels 26/42/52 um as focal 1/e2 intensity **diameters**.
The template therefore implements:

```text
PICMI waist radius = documented spot / 2
PICMI duration = (intensity FWHM) / sqrt(2 ln 2)
```

The campaign records the explicit interpretation token:

```json
"CAP_INPUT_CONVENTIONS_ACK": "clpu_spot_diameter_and_30fs_intensity_fwhm_v1"
```

Pulse duration is a continuous optimizer variable represented by
`PULSE_RESONANCE_FACTOR = tau_FWHM / (pi/omega_p)`, sampled in [0.8, 1.4].
The materialized TSV also contains the resulting `LASER_DURATION_FWHM_FS`.
The documented 0.6 J pulse energy remains fixed by scaling
`a0 = a0_30fs sqrt(30 fs / tau_FWHM)`.

## Corrected channel and mixture

The input now uses Carlos's direct quasi-parabolic expansion, with `r0` equal
to the geometric capillary radius, **only inside the plateau**:

```text
front ramp: n_e(r,z) = linear_up(z) n0
plateau:    n_e(r,z) = n0 [1 + 0.33 (r/r0)^2 + 0.4 (r/r0)^4]
back ramp:  n_e(r,z) = linear_down(z) n0
```

The on-axis quadratic coefficient is `0.33 n0/r0^2`, so both radius and density
change curvature. At the capillary wall the truncated profile is `1.73 n0`.
The 40.5 um `W_M` reference is treated as a matched **spot diameter** and
recorded only as a diagnostic; it does not reconstruct the density profile.
The entrance and exit ramps are radially homogeneous inside the capillary
aperture, as requested by Carlos; the radial multiplier is never applied there.

`CAP_NR` is fixed at 192. The radial domain follows `rmax = r0 + 30 um`, giving
0.55--1.46 um/cell across D=150--500 um without increasing the radial cell
count. Cases above 1.5 um/cell are rejected.

The number of steps is derived separately for every materialized grid from
WarpX's multimode cylindrical-Yee CFL expression. For the reference grid
(`rmax=180 um`, `nr=192`, `nz=1536`, two azimuthal modes, `cfl=1`) this gives
`c dt = 0.1640085 um`, about 30,487 steps per 5 mm. The old empirical value of
64,000 steps per 5 mm is rejected explicitly: it placed the particle dump
well downstream of the plateau exit and propagated the moving window about
twice as far as intended. Both `max_steps` and the single particle-diagnostic
iteration now use the same grid-derived `c dt` recorded in
`resolved_parameters.json`.

`NITROGEN_DOPANT_FRACTION` is the fraction of atomic nuclei that are nitrogen;
because both gases are diatomic, it is also the N2 molecular fraction in an
H2/N2 mixture. The initial free-electron profile is held fixed. Hydrogen starts
as H+ and nitrogen as N5+:

```text
n_nuclei = n_e_initial / (1 + 4 f_N)
n_H+     = (1 - f_N) n_nuclei
n_N5+    = f_N n_nuclei
```

This is initially charge neutral. The initial free electrons are stored as
`preionized_background_electrons`. That species is deliberately not called
"hydrogen electrons": it includes the electrons initially supplied for charge
neutrality by H+ and by the five already removed electrons per N5+ ion. WarpX
26.05 `FieldIonization(model="ADK")` releases the last two nitrogen electrons
into the traceable `nitrogen_ionized_electrons` species.

Particle analysis writes one combined `all_electrons` row first (the only scope
consumed by the optimizer), followed by separate rows for the preionized
background and the ADK-ionized nitrogen electrons. Energy spectra and all
longitudinal/transverse phase-space figures are also generated for all three
scopes, with the scope embedded in both filename and plot title. A campaign
validator decodes all 21 PNGs, checks the three CSV scope sets and refuses
cleanup if any product is absent or inconsistent.

The particle diagnostic is deliberately **not** a final-timestep dump. For
each case the input maps the physical plateau exit (front ramp plus plateau)
to the nearest regular field-diagnostic iteration and writes exactly that one
particle iteration. WarpX filters the dump in situ to forward electrons with
kinetic energy at least 5 MeV, for both electron species, and
`dump_last_timestep` is disabled. The resolved target, aligned iteration,
interval, filter and alignment error are persisted in
`resolved_parameters.json`.

The analysis independently derives the plateau exit from those resolved
longitudinal boundaries and requires zero iteration mismatch for this
campaign. Reduced validation also requires a single available particle
iteration equal to the resolved one. A genuinely empty, correctly timed dump
is valid physics; a distant or extra final dump is invalid provenance and
blocks cleanup.

## Sampling and objective schedule

The supplied optimizer configuration uses:

```text
iteration 0: 3 exact N2 controls + 32 Sobol points = 35 cases
iteration 1: 32 continuing Sobol points           = 32 cases
iterations 2..21: 20 MORBO batches x 6            = 120 cases
optional iterations 22..26: 5 MORBO batches x 6   = 30 cases
```

Nominal totals are 187 cases after 20 MORBO batches and 217 at the hard
25-batch ceiling. The exact controls use the same D=300 um, n0=4e18 cm^-3,
f/32, 5 mm plateau and zero focus offset, with N2 = 0, 0.5 and 1 percent.

MORBO fits three maximization objectives: guiding, smoothly weighted charge,
and energy quality. Energy quality is driven by the soft p90 energy and carries
only a 20 percent bounded preference for lower relative RMS energy spread, so
spread cannot overtake energy. Angular divergence and emittance remain
persisted diagnostics rather than equal Pareto objectives. Review the first 64
Sobol results and lock the soft-energy and spread references before iteration 2.

Every case must produce the standard waist/a0/guiding-summary PNGs, two decoded
MP4 animations (`E_perp^2` and `Ez`), a per-frame metrics CSV and a validation
JSON. These are required reduced outputs; failed generation or frame decoding
prevents reduced validation and therefore prevents raw HDF5 cleanup.
Animations remain enabled for the campaign. If validated raw cleanup is still
insufficient for quota, they are the first reduced product to reconsider; they
are not silently deleted by the current cleanup contract.

## Bootstrap without submitting jobs

Expected checkouts:

```bash
export WORKFLOW_ROOT="${HOME}/apps/src/campaign-workflow"
export OPTIMIZER_ROOT="${HOME}/apps/src/campaign-optimizer"
export OPT_ROOT="${HOME}/warpx_runs/clpu_capillary_guiding_bo_004_corrected_n2_soft50_v3"

mkdir -p "${OPT_ROOT}/template_campaign" \
         "${OPT_ROOT}/iterations" \
         "${OPT_ROOT}/optimizer_runs" \
         "${OPT_ROOT}/loop_logs"

cp "${WORKFLOW_ROOT}/examples/sunrise/corrected_capillary/campaign.json" \
   "${OPT_ROOT}/template_campaign/campaign.json"
cp "${WORKFLOW_ROOT}/examples/sunrise/corrected_capillary/input_template.py" \
   "${OPT_ROOT}/template_campaign/input_template.py"
cp "${WORKFLOW_ROOT}/examples/sunrise/corrected_capillary/optimization.json" \
   "${OPT_ROOT}/optimization.json"
cp "${OPTIMIZER_ROOT}/examples/optimizer_clpu_corrected_soft50_sunrise.json" \
   "${OPT_ROOT}/optimizer.json"
```

Build the first reviewed batch:

```bash
source "${HOME}/apps/env/campaign-optimizer.sh"
cd "${OPT_ROOT}"

python -m campaign_optimizer.cli.run_iteration \
    --config "${OPT_ROOT}/optimizer.json" \
    --iteration 0 \
    --build-candidate-batch \
    --build-report
```

The `_v2` root is an immutable audit artifact containing the two canaries that
exposed the erroneous empirical step conversion. It must not be reset, reused
or cleaned by the v3 bootstrap. Rebuilding iteration 0 with the unchanged seed
must reproduce the three controls and the same 32 Sobol parameter points in a
fresh root before any canary is submitted.

Before materialization, audit these invariants:

```bash
python - <<'PY'
import pandas as pd

path = "optimizer_runs/iter_000/outputs/candidate_batch.tsv"
df = pd.read_csv(path, sep="\t")
assert len(df) == 35
assert set(df.OPT_SAMPLE_SOURCE) == {"reference", "sobol"}
assert set(df.loc[df.OPT_SAMPLE_SOURCE == "reference", "NITROGEN_DOPANT_FRACTION"]) == {0, 0.005, 0.01}
assert (df.CAP_RMAX_UM - df.RADIUS_UM - 30.0).abs().max() < 1e-9
assert (df.CAP_NR == 192).all()
assert df.PULSE_RESONANCE_FACTOR.between(0.8, 1.4).all()
assert df.LASER_DURATION_FWHM_FS.gt(0).all()
print(df[["CASE_ID", "CASE_NAME", "RADIUS_UM", "NITROGEN_DOPANT_FRACTION", "PULSE_RESONANCE_FACTOR", "LASER_DURATION_FWHM_FS", "CAP_RMAX_UM", "CAP_NR"]].to_string(index=False))
PY
```

Use the existing `prepare_batch_campaign`, `materialize_cases` and
`init_case_states` commands exactly as in the multichannel SUNRISE example,
with this template and output root. None of those commands submits a job.

On SUNRISE, after checking out the reviewed workflow commit in the isolated
ADK worktree, `rebuild_v3_after_cfl_fix_sunrise.sh` performs this complete
rebuild. It updates the other two isolated ADK worktrees to their reviewed
commits, runs all three test suites, proves that the iteration-0 batch is
byte-identical to the previous root, materializes all 35 cases, and runs PICMI
serialization preflights for the first two controls. The script contains no
SLURM submission and finishes with `READY_FOR_CANARY=1` only after confirming
that no HDF5 files or submitted cases exist.

If the rebuild has already completed materialization but stops during the
PICMI serialization gate, do not delete or recreate the root.
`resume_v3_preflight_after_materialization_sunrise.sh` first proves that the
batch, 35 `Created` states, optimization state and absence of HDF5/submissions
match that exact safe checkpoint. It then runs only the corrected PICMI test
and the two materialized control preflights, leaving optimization state
byte-for-byte unchanged.

Once either preflight path finishes with `READY_FOR_CANARY=1`, run the
versioned two-case launcher from the login node:

```bash
bash --noprofile --norc \
    "${HOME}/apps/src/campaign-workflow-clpu-adk/examples/sunrise/corrected_capillary/launch_v3_canary_sunrise.sh"
```

The launcher revalidates the ready checkpoint, creates an exact T12H copy of
the stock case-cycle submit script, audits `optimizer_tick submit_iteration`
in dry-run mode, and then submits only case IDs 0 and 1 with array spec
`0-1` with no array throttle. Both particle provenance outputs and both MP4 animations are required
reduced artifacts. Raw HDF5 cleanup is requested, but it can execute only
after raw and reduced validation plus the delete-manifest gate succeed. The
launcher does not submit any later Sobol or MORBO work.

After both canary tasks finish, audit their complete lifecycle before releasing
anything else:

```bash
bash --noprofile --norc \
    "${HOME}/apps/src/campaign-workflow-clpu-adk/examples/sunrise/corrected_capillary/audit_v3_canary_results_sunrise.sh"
```

This audit is read-only apart from its timestamped audit log. It requires both
controls to reach `Raw_deleted`, verifies every required guiding, particle and
animation artifact, checks that the particle snapshot is exactly the aligned
plateau-exit iteration, confirms the three provenance scopes, hashes the two
decoded MP4 files, and proves that raw HDF5 cleanup occurred only after the
validation gates. It finishes with `READY_FOR_REST_AND_CHAIN=1` and prints a
compact species-separated particle summary.

Only after reviewing that output, submit the remaining iteration-0 cases and
the reviewed finite chain:

```bash
bash --noprofile --norc \
    "${HOME}/apps/src/campaign-workflow-clpu-adk/examples/sunrise/corrected_capillary/launch_v3_rest_and_chain_sunrise.sh"
```

The resume launcher never resubmits control IDs 0 and 1. It submits IDs 2--34,
then an `afterok` tick for iteration 0, followed by the second Sobol batch and
20 MORBO batches. Every array/tick transition is `afterok`; arrays have no
concurrency throttle. The launcher ends at iteration 21, so its last tick may
materialize iteration 22 for inspection but never submits optional iterations
22--26. All simulation arrays use T12H, optimizer ticks use T1H, and cleanup
remains gated by required-output validation.

## Release policy

Do not pre-submit the full 27-iteration chain at bootstrap. Run the two Sobol
iterations, audit the soft50 curves and simulation reliability, then freeze the
objective configuration. Only then use the existing finite
`submit_morbo_chain.py` login-node submitter for 20 MORBO batches. The optional
last five batches require a new review of convergence and quota. `sbatch` is
never called from inside a job, and raw HDF5 cleanup remains validation- and
manifest-gated.
