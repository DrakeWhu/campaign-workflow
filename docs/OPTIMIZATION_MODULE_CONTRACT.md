# Optimization module contract

## Purpose

This document defines the integration contract between `campaign-workflow` and an external optimization module.

The optimization module is intentionally external. It is not part of the workflow core, and the workflow core must not import optimizer-specific packages such as Optimas, Ax, BoTorch, scikit-learn, PyTorch, or future surrogate-model backends.

The contract is file-based, iteration-based, and compatible with the existing SUNRISE/SLURM production workflow.

## Scope

This contract covers how an external optimizer may consume validated reduced campaign outputs and propose new candidate cases.

It does not define:

* Bayesian optimization internals;
* acquisition functions;
* surrogate model architecture;
* physical objective choices;
* WarpX input physics;
* SLURM submission logic;
* raw diagnostic analysis algorithms.

Those belong to separate modules or later phases.

## Responsibility separation

### `campaign-workflow`

`campaign-workflow` owns campaign execution and safety:

* reading `campaign.json`;
* reading immutable campaign case manifests such as `cases.tsv`;
* creating case directories;
* materializing case-local runtime files such as `input.py` and `case.env`;
* tracking case states;
* recording validation evidence;
* coordinating external simulation wrappers;
* validating raw diagnostics;
* running external analysis commands;
* validating reduced outputs;
* generating cleanup manifests;
* deleting raw diagnostics only through manifest-driven cleanup;
* recording storage snapshots and cleanup evidence.

`campaign-workflow` does not own optimization.

It must not contain optimizer-specific logic.

It must not decide new candidate points.

It may, in a later phase, provide generic utilities that read a candidate batch manifest and prepare a new campaign, but the selection of candidates remains external.

### Simulation wrapper / WarpX input

The simulation layer owns:

* the WarpX/PyWarpX input script;
* PICMI simulation objects;
* case-specific environment variables;
* physics parameters;
* MPI/SLURM execution details;
* generation of raw diagnostics.

For the current production campaign, WarpX writes openPMD/HDF5 diagnostics under case-local diagnostic directories such as:

```text
CASE_DIR/diags/fields/*.h5
CASE_DIR/diags/plasma_electrons/*.h5
```

Those files are raw simulation outputs. They are large, expensive, and eligible for deletion after validation and reduction.

### `guiding-analysis`

The analysis module owns the physical reduction from raw diagnostics to lightweight metrics.

It may read raw WarpX/openPMD/HDF5 diagnostics only during the analysis phase.

It may write reduced outputs such as:

```text
CASE_DIR/guiding_metrics.csv
CASE_DIR/particle_analysis/particle_summary.csv
CASE_DIR/particle_analysis/particle_acceptance_curves.csv
```

The exact reduced outputs are campaign-specific and declared in `campaign.json` or in campaign documentation.

The analysis module must not:

* launch WarpX;
* submit SLURM jobs;
* update workflow state files;
* update validation evidence;
* update cleanup manifests;
* delete raw diagnostics;
* perform optimization.

### External `campaign-optimizer`

The optimizer owns:

* reading validated reduced outputs;
* assembling observation tables;
* building objective tables;
* fitting surrogates;
* ranking or proposing candidate points;
* writing optimizer iteration artifacts;
* writing recommended candidates;
* writing reviewed candidate batches.

The optimizer must not:

* launch WarpX;
* submit SLURM jobs;
* call `srun`, `sbatch`, `mpiexec`, or WarpX executables as part of optimization;
* read raw HDF5/openPMD files;
* re-run `guiding-analysis`;
* delete raw data;
* edit `state.json`;
* edit `validation.json`;
* edit cleanup manifests;
* edit `post/*.json` workflow markers;
* mutate an already launched campaign manifest.

The optimizer may be implemented with Optimas, Ax, BoTorch, MORBO, random search, evolutionary algorithms, grid search, or future ML models, as long as it respects this file contract.

## Allowed reads and writes

### `campaign-workflow`

May read:

```text
campaign.json
cases.tsv
candidate_batch.tsv               # future campaign preparation only
CASE_DIR/state.json
CASE_DIR/validation.json
CASE_DIR/post/*.json
CASE_DIR/manifests/*.json
CASE_DIR/logs/*
CASE_DIR/reduced outputs declared in campaign.json
CASE_DIR/raw diagnostics declared in campaign.json
```

May write:

```text
CASE_DIR/input.py
CASE_DIR/case.env
CASE_DIR/state.json
CASE_DIR/validation.json
CASE_DIR/post/*.json
CASE_DIR/manifests/*.json
CASE_DIR/logs/*
storage snapshots
cleanup reports
```

May delete only files explicitly listed in validated cleanup manifests.

### `guiding-analysis`

May read:

```text
CASE_DIR/diags/fields/*.h5
CASE_DIR/diags/plasma_electrons/*.h5
other raw diagnostics explicitly declared for the campaign
```

May write:

```text
CASE_DIR/guiding_metrics.csv
CASE_DIR/particle_analysis/particle_summary.csv
CASE_DIR/particle_analysis/particle_acceptance_curves.csv
optional plots or analysis side products
```

Must not write workflow state, validation evidence, cleanup manifests, or optimizer state.

### `campaign-optimizer`

May read:

```text
campaign.json
cases.tsv
CASE_DIR/state.json
CASE_DIR/validation.json
CASE_DIR/guiding_metrics.csv
CASE_DIR/particle_analysis/particle_summary.csv
CASE_DIR/particle_analysis/particle_acceptance_curves.csv
other validated reduced outputs
previous optimizer_runs/iter_XXX/ artifacts
```

May write:

```text
optimizer_runs/iter_XXX/inputs/observations.csv
optimizer_runs/iter_XXX/inputs/objective_table.csv
optimizer_runs/iter_XXX/outputs/recommended_candidates.tsv
optimizer_runs/iter_XXX/outputs/candidate_batch.tsv
optimizer_runs/iter_XXX/outputs/batch_campaign_plan.json
optimizer_runs/iter_XXX/optimizer_state.json
optimizer_runs/iter_XXX/logs/*
```

Must not write case-local workflow files.

Must not write raw diagnostics.

Must not delete any simulation or analysis output.

## Immutable artifacts

The following artifacts are immutable once the corresponding campaign or optimizer iteration starts:

```text
campaign.json                         # after campaign launch, except deliberate config migration
cases.tsv                             # after campaign launch
CASE_DIR/input.py                     # after case launch
CASE_DIR/case.env                     # after case launch
optimizer_runs/iter_XXX/inputs/*
optimizer_runs/iter_XXX/outputs/candidate_batch.tsv
optimizer_runs/iter_XXX/outputs/batch_campaign_plan.json
optimizer_runs/iter_XXX/optimizer_state.json
```

If an objective definition changes, do not overwrite old optimizer artifacts.

Create a new optimizer iteration or a new objective configuration.

## Validated reduced output

A reduced output is considered validated only when all of the following are true:

1. The relevant case has completed simulation successfully or has another explicitly accepted successful state.
2. Required raw diagnostics passed workflow validation before analysis.
3. The external analysis command completed successfully, or the reduced output already existed and was explicitly accepted by the workflow.
4. `campaign-workflow` validated the reduced output according to the configured contract.
5. The validation evidence is recorded in the case-local workflow evidence.

For CSV outputs, validation means at minimum:

* file exists;
* file is readable as CSV;
* file has at least the configured minimum number of rows;
* required columns exist;
* the output path is relative to the case directory and is not an unsafe path.

An optimizer must not treat an arbitrary CSV file as valid merely because it exists.

The optimizer must use workflow validation evidence and/or a workflow-produced observation table in later phases.

## Raw diagnostics policy

Raw WarpX/openPMD/HDF5 diagnostics are not optimizer input.

They are heavy transient data owned by the simulation and analysis stages.

The optimizer must not read files matching patterns such as:

```text
*.h5
*.hdf5
diags/fields/*
diags/plasma_electrons/*
```

The optimizer must not use openPMD APIs, HDF5 APIs, or WarpX diagnostic readers.

If a metric is missing from reduced outputs, the optimizer must represent it as missing or failed. It must not reconstruct it from raw diagnostics.

## Simulation launch policy

The optimizer must not launch simulations directly.

It must not run:

```text
WarpX
pywarpx
python input.py
srun
sbatch
mpiexec
mpirun
```

The optimizer may write a candidate batch manifest.

A human or a future explicit workflow preparation command may turn that candidate batch into a new campaign.

The actual execution remains owned by `campaign-workflow` and the existing SLURM scripts or wrappers.

## Objective and score versioning

Objectives are derived quantities. They are not raw metrics.

Every objective table must identify the objective definition using:

```text
objective_schema_version
objective_config_id
objective_config_hash
```

Each score column should include an explicit version suffix, for example:

```text
score_guiding_v1
score_beamlike_v1
score_transverse_v1
score_acceptance_v1
```

A change in any of the following requires a new objective version or config id:

* formula;
* normalization;
* clipping;
* weighting;
* failure policy;
* baseline handling;
* thresholds;
* sign convention;
* minimization/maximization direction;
* filtering rules;
* train/fit eligibility rules.

Scores must not silently change semantics while keeping the same name.

## Failures, NaNs, and missing data

Missing data must not be silently converted to zero.

Each observation or objective row must represent status explicitly.

Recommended status values:

```text
ok
not_applicable
missing_metric
missing_reduced_output
missing_baseline
simulation_failed
raw_validation_failed
analysis_failed
reduced_validation_failed
invalid_nan
invalid_infinite
filtered_out
manual_reject
```

Recommended failure categories:

```text
simulation
raw_validation
analysis
reduced_validation
objective_construction
baseline_matching
optimizer_fit
manual_review
```

NaNs are allowed only as missing-value representations paired with an explicit status.

A physical zero must be represented as numeric zero and must not be confused with missing data.

Examples:

* vacuum cases may have `particle_summary_status = not_applicable`;
* channel cases with no accelerated electrons may have a physical zero charge if the analysis defines it that way;
* a missing uniform baseline must be represented as `missing_baseline`, not as zero gain;
* a failed analysis must not enter objective fitting as a low score unless the objective version explicitly defines that penalty.

## Baseline and comparison policy

Some objectives require baselines, for example channel-vs-uniform or channel-vs-vacuum comparisons.

Baseline matching must be explicit and reproducible.

The objective table should include baseline identifiers when used:

```text
channel_case_id
uniform_case_id
vacuum_case_id
baseline_match_status
baseline_match_reason
```

If a baseline is missing, the affected comparison score is invalid for that objective.

The underlying observation row remains valid if its own reduced outputs are valid.

## Optimizer iteration model

Optimization proceeds in discrete iterations.

Each iteration reads previous validated reduced outputs and writes a new immutable optimizer run directory:

```text
optimizer_runs/iter_XXX/
```

The optimizer does not remain resident.

It performs one finite operation and exits.

A typical iteration is:

```text
collect validated reduced outputs
-> write observations.csv
-> write objective_table.csv
-> fit/update surrogate
-> write recommended_candidates.tsv
-> human or policy review
-> write candidate_batch.tsv
-> write batch_campaign_plan.json
-> exit
```

No daemon is required.

No parent process must occupy walltime while waiting for simulations.

## SUNRISE/SLURM compatibility

The contract is compatible with the current SUNRISE workflow:

* campaign execution uses SLURM arrays;
* each case is independent;
* state is stored on disk;
* validation precedes cleanup;
* cleanup uses manifests;
* global operations are short finite ticks.

An optimizer tick may be launched as a normal short job, but it must not submit or monitor simulation jobs itself.

## Future compatibility

The contract must remain valid for:

* Optimas/Ax/BoTorch;
* BoTorch-only implementations;
* MORBO or high-dimensional trust-region methods;
* random or grid baselines;
* non-Bayesian optimizers;
* other PIC codes besides WarpX;
* non-PIC simulation backends;
* experimental campaigns where the executor is not SLURM;
* future clusters besides SUNRISE.

Therefore the optimizer contract is based on reduced metrics and candidate manifests, not on WarpX internals.

## Current capillary optimizer prototype

The initial standalone Optimas scripts may use hard-coded paths and campaign-specific parsing while the integration is being designed.

Those scripts are prototypes, not the long-term contract.

The long-term optimizer must prefer explicit columns from observation and objective tables over parsing physics parameters from case names.

Case names are identifiers for humans and filesystem organization. They are not the canonical source of physics parameters.

## Non-goals for Phase 0

Phase 0 must not implement:

* candidate generation inside `campaign-workflow`;
* Optimas integration inside `campaign-workflow`;
* automatic campaign creation from optimizer output;
* automatic SLURM submission from optimizer output;
* raw HDF5 reading from optimizer code;
* objective formulas as workflow core logic;
* changes to WarpX input templates;
* changes to physics parameters;
* cleanup behavior changes;
* database storage;
* daemon orchestration.
