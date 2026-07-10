# Hexagonal electrons + ions baseline

3D Lynx/WarpX baseline derived from the legacy
`hexagonal_buenas/combination_1/lwfa_3d.py` input.

This is not a smoke test.

Purpose:

- simulate the hexagonal CNT / multichannel target in 3D
- use plasma electrons and fixed C3+ ions
- remove injected beam electrons
- remove field ionization for the first clean baseline
- analyze whether plasma electrons form a beam-like population
- produce `post/particle_summary.csv` with `multichannel-lfmetrics`

Main physics choices:

- grid: 128 x 128 x 1024
- steps: 4000
- target: hexagonal transverse density expression
- electrons: n0 = 3 * plasma_density
- carbon ions: n0 = plasma_density, charge_state = 3, fixed ions
- laser: legacy 800 nm Gaussian laser
- diagnostics:
  - particle openPMD/HDF5 every 100 steps, prefix `3D`
  - sparse field openPMD/HDF5 every 400 steps, prefix `fields3D`

Intentional exclusions:

- no injected `beam` species
- no `FieldIonization`
- no cleanup
- no optimizer scoring yet

Expected analysis output:

```text
post/particle_summary.csv
```
LFMetrics command used by the SLURM script:
```
lfmetrics analyze-case "$RUN_DIR" \
  --diagnostics-dir 3D \git push
  --species electrons \
  --energy-threshold-MeV 5 \
  --output "$RUN_DIR/post/particle_summary.csv"
```
Future questions:

- compare pre-ionized electrons vs `ionized_electrons`
- reintroduce ADK field ionization explicitly
- compare plasma electron beam vs injected beam
- add objective scoring for MORBO