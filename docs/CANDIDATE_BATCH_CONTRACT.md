# Candidate batch contract

## Purpose

This document defines the file artifacts used to pass information between a completed campaign, an external optimizer, and a future campaign batch.

The contract is intentionally file-based and optimizer-agnostic.

The main flow is:

```text
validated reduced campaign outputs
-> observations.csv
-> objective_table.csv
-> recommended_candidates.tsv
-> candidate_batch.tsv
-> batch_campaign_plan.json
-> future campaign cases.tsv
```

`candidate_batch.tsv` is the bridge between optimization and campaign execution.

It is not a job launcher.

It is not allowed to submit simulations.

It is not allowed to modify an existing launched campaign.

## Optimizer run directory

Every optimizer iteration must write to a dedicated immutable directory:

```text
optimizer_runs/
└── iter_XXX/
    ├── inputs/
    │   ├── observations.csv
    │   └── objective_table.csv
    ├── outputs/
    │   ├── recommended_candidates.tsv
    │   ├── candidate_batch.tsv
    │   └── batch_campaign_plan.json
    ├── optimizer_state.json
    └── logs/
```

`XXX` is a zero-padded integer:

```text
iter_000
iter_001
iter_002
```

After a candidate batch has been reviewed or used to create a campaign, files inside the iteration directory must not be overwritten.

If the optimizer is rerun with different objective definitions, filters, parameter bounds, acquisition functions, or seeds, create a new iteration or a new objective configuration.

## `observations.csv`

### Purpose

`observations.csv` is the normalized table of campaign observations available to the optimizer.

It is built from validated reduced outputs and lightweight workflow metadata.

It must not be built by reading raw HDF5/openPMD diagnostics.

### Required semantics

One row represents one observed case or one observed comparison unit, depending on the objective design.

For the current capillary campaign, one row will usually represent one channel case, optionally linked to uniform and/or vacuum baselines.

### Required columns

Minimum required columns:

```text
observation_id
source_campaign_name
source_campaign_root
source_case_id
source_case_name
case_state
simulation_status
raw_validation_status
analysis_status
reduced_validation_status
failure_kind
failure_reason
```

Recommended campaign-parameter columns for the current capillary campaign:

```text
laser_case
plasma_kind
n0_cm3
plateau_length_mm
diameter_um
radius_um
focus_offset_from_plateau_start_mm
cap_rmax_um
cap_nr
```

Recommended baseline columns:

```text
channel_case_id
channel_case_name
uniform_case_id
uniform_case_name
vacuum_case_id
vacuum_case_name
baseline_match_status
baseline_match_reason
```

Recommended reduced-output status columns:

```text
guiding_metrics_status
particle_summary_status
acceptance_curves_status
comparison_metrics_status
```

Metric columns should use explicit prefixes:

```text
metric_guiding_*
metric_particle_*
metric_acceptance_*
metric_comparison_*
```

Examples:

```text
metric_guiding_final_score
metric_guiding_peakI_norm_exit
metric_particle_E95_hot_MeV
metric_particle_charge_hot_pC
metric_particle_theta_rms_mrad
metric_particle_beam_transverse_quality_score
metric_acceptance_Q_Ege100MeV_theta10mrad_pC
metric_comparison_guiding_gain_vs_uniform
```

### Missing values

Missing values must be represented as empty fields or NaN-compatible CSV fields, but only with explicit status columns.

A missing metric must not be converted to zero.

A physical zero must be written as numeric zero.

Examples:

```text
particle_summary_status=not_applicable
failure_kind=
failure_reason=
```

for a vacuum case.

```text
particle_summary_status=ok
metric_particle_charge_hot_pC=0.0
```

for a valid plasma case with zero hot charge, if the analysis defines this as a physical zero.

## `objective_table.csv`

### Purpose

`objective_table.csv` contains objective values derived from observations.

Objectives are versioned derived quantities.

They are not raw metrics.

### Required columns

```text
observation_id
objective_schema_version
objective_config_id
objective_config_hash
fit_eligible
objective_status
objective_failure_reason
```

Recommended objective columns:

```text
score_guiding_v1
score_beamlike_v1
score_transverse_v1
score_acceptance_v1
```

For every score, include status and direction metadata either as columns or in a sidecar config:

```text
score_guiding_v1_status
score_guiding_v1_direction
score_beamlike_v1_status
score_beamlike_v1_direction
score_transverse_v1_status
score_transverse_v1_direction
score_acceptance_v1_status
score_acceptance_v1_direction
```

Direction values:

```text
maximize
minimize
```

Recommended status values:

```text
ok
not_applicable
missing_metric
missing_baseline
invalid_nan
invalid_infinite
simulation_failed
analysis_failed
reduced_validation_failed
filtered_out
manual_reject
```

### Fit eligibility

`fit_eligible` is a boolean-like field:

```text
true
false
```

A row can be present but not fit-eligible.

Examples:

* reduced outputs valid but baseline missing;
* metric valid but outside trusted physical domain;
* analysis succeeded but objective formula not applicable;
* manually rejected point.

The optimizer must respect `fit_eligible=false`.

## `recommended_candidates.tsv`

### Purpose

`recommended_candidates.tsv` is the optimizer's ranked proposal table.

It may contain more candidates than will actually be launched.

It is not necessarily a launchable manifest.

It is intended for inspection, ranking, filtering, and review.

### Required columns

```text
recommendation_id
optimizer_iteration
candidate_id
rank
recommendation_status
candidate_source
ranking_source
acquisition_value
```

Recommended parameter columns:

```text
f_number
n0_1e18cm3
plateau_mm_num
diameter_um_num
focus_mm_num
```

Recommended predicted-objective columns:

```text
pred_score_guiding_mean
pred_score_guiding_sem
pred_score_beamlike_mean
pred_score_beamlike_sem
pred_score_transverse_mean
pred_score_transverse_sem
pred_score_acceptance_mean
pred_score_acceptance_sem
```

Recommended acquisition/ranking columns:

```text
score_balanced_conservative
score_balanced_exploratory
score_beamlike_guarded
nearest_known_scaled_dist
```

`recommended_candidates.tsv` may include optimizer-internal columns, but they must not be required by `campaign-workflow`.

## `candidate_batch.tsv`

### Purpose

`candidate_batch.tsv` is the reviewed, launchable candidate manifest.

It must be convertible into a new campaign `cases.tsv`.

For the current capillary WarpX campaign, it should use the same physical columns expected by `campaign.json` case materialization.

### Required columns for the current capillary campaign

```text
CASE_ID
CASE_NAME
LASER_CASE
PLASMA_KIND
N0_CM3
PLATEAU_LENGTH_MM
DIAMETER_UM
RADIUS_UM
FOCUS_OFFSET_FROM_PLATEAU_START_MM
CAP_RMAX_UM
CAP_NR
```

### Optional optimizer provenance columns

```text
OPT_ITERATION
OPT_CANDIDATE_ID
OPT_RECOMMENDATION_ID
OPT_OBJECTIVE_CONFIG_ID
OPT_SOURCE_OBSERVATION_ID
OPT_RANKING_SOURCE
OPT_ACQUISITION_VALUE
```

These columns are metadata.

They may be preserved in `cases.tsv`, but `campaign-workflow` should ignore them unless they are explicitly mapped in `campaign.json`.

### Column semantics

`CASE_ID`

Integer or integer-like unique identifier inside the new candidate batch.

`CASE_NAME`

Relative case directory name.

Must not be absolute.

Must not contain `..`.

Must be unique inside the batch.

`LASER_CASE`

Current allowed values are campaign-specific. For the capillary campaign:

```text
f20
f32
f40
```

`PLASMA_KIND`

Current allowed values are campaign-specific. For optimizer-proposed physical candidates this will usually be:

```text
chan
```

Uniform or vacuum baselines may be added by a later batch expansion step if required.

`N0_CM3`

Electron density in cm^-3.

`PLATEAU_LENGTH_MM`

Plateau length in millimeters.

`DIAMETER_UM`

Capillary diameter in micrometers.

`RADIUS_UM`

Capillary radius in micrometers.

For this campaign:

```text
RADIUS_UM = DIAMETER_UM / 2
```

`FOCUS_OFFSET_FROM_PLATEAU_START_MM`

Laser focus offset from plateau start, in millimeters.

`CAP_RMAX_UM`

Simulation radial domain size in micrometers.

This is numerical/campaign setup metadata, not an optimizer objective.

`CAP_NR`

Number of radial cells.

### Launchability rules

A `candidate_batch.tsv` is launchable only if:

* all required columns are present;
* all case ids are unique;
* all case names are unique;
* all case names are safe relative paths;
* values are within the reviewed campaign parameter bounds;
* unit columns use the declared units;
* no row requires the optimizer to edit `input_template.py`;
* no row requires the optimizer to submit a job.

### Relationship to `cases.tsv`

A reviewed `candidate_batch.tsv` may become the `cases.tsv` of a new campaign.

The new campaign must have its own campaign root.

Do not append candidates directly to an already launched production campaign unless there is an explicit append-safe workflow phase.

The safe default is:

```text
old_campaign/
new_campaign_from_optimizer_iter_XXX/
```

The new campaign root should contain:

```text
campaign.json
cases.tsv              # copied or derived from candidate_batch.tsv
input_template.py
workflow/
submit scripts
```

Then existing `campaign-workflow` phases can be used normally.

## `batch_campaign_plan.json`

### Purpose

`batch_campaign_plan.json` records how the candidate batch is intended to become a campaign.

It is declarative metadata.

It is not executable orchestration.

It must not submit jobs.

### Minimal schema

```json
{
  "schema_version": 1,
  "plan_type": "optimizer_candidate_batch",
  "created_at": "ISO-8601 timestamp",
  "optimizer_iteration": 0,
  "objective_config_id": "string",
  "candidate_batch": "outputs/candidate_batch.tsv",
  "recommended_candidates": "outputs/recommended_candidates.tsv",
  "source_campaigns": [
    {
      "campaign_name": "string",
      "campaign_root": "string",
      "cases_tsv": "string",
      "campaign_json": "string"
    }
  ],
  "campaign_template": {
    "campaign_json": "campaign.json",
    "input_template": "input_template.py"
  },
  "expected_workflow": [
    "create campaign root",
    "copy or derive cases.tsv from candidate_batch.tsv",
    "copy campaign.json",
    "copy input_template.py",
    "materialize cases",
    "initialize case states",
    "submit SLURM array",
    "validate raw diagnostics",
    "run external analysis",
    "validate reduced outputs",
    "cleanup raw diagnostics through manifests"
  ],
  "non_goals": [
    "does not submit jobs",
    "does not edit physics input",
    "does not read raw HDF5/openPMD",
    "does not delete data",
    "does not mutate previous campaigns"
  ]
}
```

Additional fields are allowed, but consumers must ignore unknown fields unless explicitly versioned.

## `optimizer_state.json`

### Purpose

`optimizer_state.json` records optimizer-side state and provenance.

It is not workflow state.

It must not duplicate or replace `CASE_DIR/state.json`.

### Minimal schema

```json
{
  "schema_version": 1,
  "optimizer_backend": "optimas",
  "optimizer_backend_version": "0.8.1",
  "surrogate_backend": "ax/botorch",
  "objective_schema_version": 1,
  "objective_config_id": "string",
  "objective_config_hash": "string",
  "parameter_space_version": 1,
  "random_seed": 12345,
  "latest_iteration": 0,
  "history": [
    {
      "iteration": 0,
      "observations": "inputs/observations.csv",
      "objectives": "inputs/objective_table.csv",
      "recommendations": "outputs/recommended_candidates.tsv",
      "candidate_batch": "outputs/candidate_batch.tsv",
      "batch_campaign_plan": "outputs/batch_campaign_plan.json"
    }
  ]
}
```

The optimizer may store additional backend-specific state, but file paths must remain relative to the optimizer iteration directory when possible.

## Review policy

`recommended_candidates.tsv` and `candidate_batch.tsv` are separate on purpose.

`recommended_candidates.tsv` is optimizer output.

`candidate_batch.tsv` is reviewed launch intent.

A future automated review policy may produce `candidate_batch.tsv`, but Phase 0 assumes human review is valid and expected.

## Baseline expansion policy

For comparison objectives, a candidate channel case may require uniform or vacuum baselines.

This contract does not require the optimizer to generate those baselines in Phase 0.

A later phase may introduce a batch expansion step:

```text
channel candidate_batch.tsv
-> expanded campaign cases.tsv with chan/uni/vac cases
```

That expansion step must be explicit and documented.

It must not be hidden inside the optimizer fit loop.

## Non-goals

This contract does not implement:

* optimizer fitting;
* acquisition functions;
* candidate generation;
* baseline expansion;
* SLURM submission;
* campaign creation;
* raw HDF5/openPMD reading;
* analysis reruns;
* cleanup;
* database storage;
* daemon orchestration.
