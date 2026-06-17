# Analysis module contract

## Purpose

`campaign-workflow` can execute campaign-specific analysis code without importing it directly.

The workflow owns:

* case state transitions
* validation evidence
* stdout/stderr capture
* reduced-output validation
* cleanup blocking
* storage and cleanup metadata

The external analysis module owns:

* physical interpretation of raw diagnostics
* numerical algorithms
* domain-specific reduced metrics
* plots or optional side products
* its own Python environment and dependencies

This separation is mandatory. The workflow core must remain generic.

## Minimal analysis module architecture

A compatible analysis module should provide at least one case-local command-line entrypoint:

```bash
analysis_command CASE_DIR
```

or an equivalent command declared in `campaign.json` using placeholders:

```json
"command": [
  "bash",
  "/path/to/run_analysis_case.sh",
  "{case_dir}"
]
```

The command must be able to run for one case directory independently.

It should not require the campaign workflow to import Python packages from the analysis module.

## Required input contract

The analysis command may assume that:

* `CASE_DIR` exists.
* required raw diagnostics have already been validated by `campaign-workflow`.
* raw diagnostics are located according to `campaign.json`.
* `campaign-workflow` will execute the command only after state/evidence checks pass.

The analysis command should receive all case-specific paths explicitly through arguments or environment variables.

It must not infer campaign layout from hard-coded global paths unless wrapped by a campaign-specific shell script.

## Required output contract

The analysis command must produce the reduced outputs declared in `campaign.json`:

```json
"outputs": [
  {
    "name": "guiding_metrics",
    "kind": "csv",
    "path": "guiding_metrics.csv",
    "min_rows": 1,
    "required_columns": ["iteration", "time_fs"],
    "required": true
  }
]
```

For CSV outputs:

* the file path must be relative to `CASE_DIR`;
* the file must be readable as CSV;
* the file must contain at least `min_rows` data rows;
* the file must include all configured `required_columns`;
* missing or invalid required outputs make the analysis fail.

The analysis module may write additional side products, but only configured reduced outputs are validated by the workflow.

## Exit-code contract

The analysis command must return:

* `0` on successful execution;
* non-zero on failure.

A zero exit code is not sufficient for workflow success. After the command exits, `campaign-workflow` still validates the configured reduced outputs.

A non-zero exit code makes the case transition to `Analysis_failed`.

## Logging contract

The analysis command should write useful progress information to stdout/stderr.

`campaign-workflow` captures these streams into:

```text
CASE_DIR/logs/analysis_<analysis_name>_stdout.txt
CASE_DIR/logs/analysis_<analysis_name>_stderr.txt
```

The analysis module does not need to create these log files itself.

## Environment contract

The external analysis module may use its own virtual environment.

For SUNRISE, the expected separation is:

```text
campaign-workflow-py310    -> workflow orchestration
guiding-analysis-py310     -> guiding/particle analysis
warpx-26.05-py314          -> PyWarpX/WarpX execution
```

The recommended pattern is a shell wrapper:

```bash
#!/usr/bin/env bash
set -euo pipefail

CASE_DIR="$1"

module purge
module load GCC/12.1.0
module load Python/3.10.12

source "$HOME/apps/venvs/guiding-analysis-py310/bin/activate"

cd "$HOME/apps/src/guiding_analysis_module"

python scripts/analyze_case.py \
  --diag "${CASE_DIR}/diags/diag1" \
  --outdir "${CASE_DIR}" \
  --overwrite \
  --no-plots
```

The workflow invokes this wrapper through the generic `command` adapter.

## State semantics

Normal analysis path:

```text
Raw_validated -> Analyzing -> Reduced_validated
```

Failure path:

```text
Raw_validated -> Analyzing -> Analysis_failed
```

Explicit reanalysis paths:

```text
Raw_delete_eligible -> Analyzing -> Reduced_validated
Reduced_validated   -> Analyzing -> Reduced_validated
Analysis_failed     -> Analyzing -> Reduced_validated
```

Reanalysis from final states must be explicit.

Reanalysis from `Raw_delete_eligible` or `Reduced_validated` blocks cleanup and requires a later eligibility phase before raw deletion can be reconsidered.

## Cleanup safety

An analysis module must never delete raw diagnostics.

An analysis module must not update cleanup manifests.

An analysis module must not transition workflow states itself.

Only `campaign-workflow` may update:

```text
state.json
validation.json
post/analysis_done.json
post/analysis_failed.json
manifests/
```

The analysis module writes reduced scientific outputs; the workflow validates and records them.

## Guiding example

For the current capillary guiding campaign, the production contract is:

```text
CASE_DIR/diags/diag1/*.h5
  -> guiding_analysis_module/scripts/analyze_case.py
  -> CASE_DIR/guiding_metrics.csv
```

The workflow command is:

```json
"analysis": {
  "name": "guiding",
  "kind": "command",
  "adapter": "command",
  "inputs": ["fields_openpmd"],
  "command": [
    "bash",
    "/HOME/jrodriguez/apps/src/campaign-workflow/examples/capillary_guiding/run_guiding_case_analysis_sunrise.sh",
    "{case_dir}"
  ],
  "outputs": [
    {
      "name": "guiding_metrics",
      "kind": "csv",
      "path": "guiding_metrics.csv",
      "min_rows": 1,
      "required_columns": [
        "iteration",
        "time_fs",
        "z_min_um",
        "z_max_um",
        "z_peak_um",
        "front_margin_um",
        "back_margin_um",
        "waist_um",
        "peak_I_proxy",
        "Eperp_peak_Vm",
        "a0_peak",
        "energy_proxy",
        "Ez_wake_max",
        "Ez_wake_min",
        "Ez_wake_absmax",
        "Ez_wake_rms",
        "z_Ez_absmax_um",
        "z_Ez_absmax_rel_um",
        "propagation_mm",
        "z_peak_relative_um"
      ],
      "required": true
    }
  ]
}
```

This is an example adapter stack, not a core workflow dependency.

## Particle-analysis extension in capillary guiding

The capillary guiding wrapper may also invoke an external particle-analysis
entrypoint after field/guiding analysis.

This remains an external analysis-module responsibility. `campaign-workflow`
does not parse particle HDF5 files or implement particle physics internally.

The production pattern is:

```text
CASE_DIR/diags/fields or CASE_DIR/diags/diag1
  -> field/guiding analysis
  -> CASE_DIR/guiding_metrics.csv

CASE_DIR/diags/plasma_electrons
  -> particle analysis
  -> CASE_DIR/particle_analysis/particle_summary.csv
  -> optional plots under CASE_DIR/particle_analysis/plots/

The wrapper may support:

CAMPAIGN_RUN_PARTICLE_ANALYSIS=auto|always|never

Recommended semantics:

auto   -> run particle analysis if CASE_DIR/diags/plasma_electrons exists
always -> fail if CASE_DIR/diags/plasma_electrons does not exist
never  -> skip particle analysis

For campaigns containing vacuum cases, particle_summary.csv should usually be
declared optional globally unless the workflow supports conditional reduced
outputs by case metadata.

Example reduced output contract:

{
  "name": "particle_summary",
  "kind": "csv",
  "path": "particle_analysis/particle_summary.csv",
  "min_rows": 1,
  "required_columns": [
    "iteration",
    "selection_mode",
    "selected_particle_iteration",
    "n_macroparticles_hot",
    "weight_hot",
    "charge_hot_pC",
    "Emax_MeV",
    "E95_MeV",
    "Emax_hot_MeV",
    "Emean_hot_MeV",
    "q_long_mean_hot_mm",
    "u_long_mean_hot"
  ],
  "required": false
}

Useful electron reduced metrics observed in production include:

n_macroparticles_hot
weight_hot
charge_hot_pC
Emax_hot_MeV
E95_hot_MeV
Emean_hot_MeV
q_long_mean_hot_mm
q_long_min_hot_mm
q_long_max_hot_mm
u_long_mean_hot
u_long_max_hot

The reduced CSV is enough for first-pass candidate selection. Richer diagnostics
such as transverse phase spaces, divergence, and emittance should be produced in
targeted reruns of promising cases, not necessarily for every campaign case.
