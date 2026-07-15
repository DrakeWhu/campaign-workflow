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
export OPT_ROOT="${HOME}/warpx_runs/clpu_capillary_guiding_bo_004_corrected_n2_soft50"

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

## Release policy

Do not pre-submit the full 27-iteration chain at bootstrap. Run the two Sobol
iterations, audit the soft50 curves and simulation reliability, then freeze the
objective configuration. Only then use the existing finite
`submit_morbo_chain.py` login-node submitter for 20 MORBO batches. The optional
last five batches require a new review of convergence and quota. `sbatch` is
never called from inside a job, and raw HDF5 cleanup remains validation- and
manifest-gated.
