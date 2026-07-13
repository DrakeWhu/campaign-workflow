# Configuration contract

This document describes the configuration keys consumed by `campaign-workflow` `1.0.0`.

There are two main configuration files:

```text
campaign_root/campaign.json
optimization_root/optimization.json
```

`campaign.json` describes one simulation campaign. `optimization.json` describes how an optimization root advances between campaign iterations.

## campaign.json

### Required campaign identity and manifest fields

```json
{
  "schema_version": 1,
  "campaign_name": "example_campaign",
  "case_manifest": "cases.tsv",
  "case_manifest_format": "tsv",
  "case_id_column": "CASE_ID",
  "case_name_column": "CASE_NAME"
}
```

Requirements:

- `schema_version` must be `1`.
- `case_manifest_format` is currently `tsv`.
- `CASE_ID` values must be integer-like and unique.
- `CASE_NAME` values must be unique, safe, single relative directory names.

### Optional state layout

```json
"state": {
  "state_file": "state.json",
  "validation_file": "validation.json",
  "locks_dir": "locks",
  "manifests_dir": "manifests",
  "post_dir": "post",
  "logs_dir": "logs"
}
```

Defaults are the values shown above. These paths are case-relative.

### Simulation section

```json
"simulation": {
  "backend": "warpx_picmi",
  "scheduler": "slurm",
  "input_script": "input.py",
  "completion_marker": "post/sim_done.json",
  "failure_marker": "post/sim_failed.json"
}
```

The workflow uses this section for metadata, materialization defaults, and marker checks. It does not interpret the simulation physics.

### Case materialization section

```json
"case_materialization": {
  "input_template": "input_template.py",
  "input_name": "input.py",
  "env_name": "case.env",
  "env_columns": [
    {"column": "CASE_ID", "env": "CASE_ID", "required": true},
    {"column": "DENSITY_CM3", "env": "DENSITY_M3", "required": true, "scale": "1e6"}
  ],
  "env_constants": {
    "DIAG_PRESET": "fields"
  }
}
```

Fields:

- `input_template`: campaign-root-relative source file copied into each case.
- `input_name`: case-relative output filename. Defaults to `simulation.input_script` or `input.py`.
- `env_name`: case-relative environment file. Defaults to `case.env`.
- `env_columns`: list of mappings from TSV columns to shell environment variables.
- `env_columns[].required`: if true, missing or empty values fail materialization.
- `env_columns[].scale`: optional decimal multiplier applied before rendering.
- `env_constants`: object or list of `{ "env": ..., "value": ... }` entries.

If `env_columns` is absent, `1.0.0` uses the legacy capillary guiding `CAP_*` defaults required by the current production campaigns. Non-capillary campaigns should set this section explicitly.

If `env_constants` is absent, the legacy defaults are:

```text
CAP_DIAG_PRESET=guiding_rhoe
CAP_LONG_PROFILE=both
```

### Raw diagnostics

```json
"raw_diagnostics": [
  {
    "name": "fields_openpmd",
    "kind": "openpmd_hdf5",
    "glob": "diags/**/*.h5",
    "allowed_suffixes": [".h5", ".hdf5"],
    "min_files": 1,
    "min_age_seconds": 600,
    "required": true
  }
]
```

Supported `kind` values:

```text
fake
openpmd_hdf5
```

`glob` is case-relative and must stay inside the case directory. `path` may appear in example configs as descriptive metadata, but validation uses `glob`.

### Analysis section

```json
"analysis": {
  "name": "guiding",
  "kind": "command",
  "adapter": "command",
  "inputs": ["fields_openpmd"],
  "command": ["bash", "{campaign_root}/workflow/examples/capillary_guiding/run_guiding_case_analysis_sunrise.sh", "{case_dir}"],
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
}
```

Supported production adapter:

```text
command
```

Supported reduced output kind:

```text
csv
```

Available command placeholders include:

```text
{campaign_root}
{case_dir}
{case_id}
{case_name}
```

The command receives environment variables such as `CAMPAIGN_ROOT`, `CAMPAIGN_NAME`, `CASE_DIR`, `CASE_ID`, and `CASE_NAME` from the command adapter.

### Cleanup section

```json
"cleanup": {
  "raw_delete_globs": ["diags/**/*.h5", "diags/**/*.hdf5"],
  "require_raw_validated": true,
  "require_reduced_validated": true,
  "require_delete_manifest": true,
  "allow_directory_delete": false
}
```

Rules:

- `raw_delete_globs` are case-relative.
- cleanup deletes files only;
- `allow_directory_delete` must remain `false` for the supported workflow;
- execute mode requires a valid dry-run manifest.

### Storage section

```json
"storage": {
  "snapshot_dir": "snapshots",
  "reservation_dir": "reservations",
  "events_dir": "events"
}
```

`storage_snapshot` currently writes to `snapshots/storage_snapshot_latest.json` by default unless `--output` is provided.

## optimization.json

`optimization.json` lives at the optimization root.

### External optimizer command

```json
"optimizer": {
  "command": [
    "bash",
    "{optimization_root}/run_optimizer.sh",
    "--from-iteration", "{from_iteration}",
    "--next-iteration", "{next_iteration}",
    "--output-dir", "{optimizer_run_dir}/outputs"
  ],
  "working_directory": "{optimization_root}",
  "env_script": "~/apps/env/optimas_sunrise.sh"
}
```

The workflow renders placeholders and executes this command when proposing the next iteration. The command must produce the optimizer outputs described in `docs/OPTIMIZATION_MODULE_CONTRACT.md`.

### Campaign preparation

```json
"campaign_preparation": {
  "optimizer_runs_dir": "optimizer_runs",
  "iterations_dir": "iterations",
  "template_campaign_root": "iterations/iter_000",
  "campaign_name_template": "{optimization_name}_iter_{next_iteration:03d}",
  "materialize_after_prepare": true,
  "init_case_states_after_materialize": true
}
```

Defaults:

- `optimizer_runs_dir`: `optimizer_runs`
- `iterations_dir`: `iterations`
- `template_campaign_root`: source iteration campaign root
- `campaign_name_template`: `{optimization_name}_iter_{next_iteration:03d}`
- `materialize_after_prepare`: `true`
- `init_case_states_after_materialize`: `true`

### Policy

```json
"policy": {
  "max_iterations": 5,
  "max_total_materialized_cases": 1000,
  "max_total_submitted_cases": 1000,
  "max_cases_per_submit": 128,
  "max_unsubmitted_materialized_cases": 128,
  "require_all_materialized_cases_submitted": false
}
```

Policy keys are guardrails checked before materialization/submission where applicable.
When `require_all_materialized_cases_submitted` is true, a staged pilot cannot
advance the optimizer until every case in that materialized iteration has been
included in a registered submission.

### Guards

```json
"guards": {
  "enabled": true,
  "pause_file": "PAUSE_OPTIMIZATION",
  "campaign_size": {
    "enabled": true,
    "max_iterations": 5,
    "max_cases_per_submit": 128,
    "max_total_materialized_cases": 1000,
    "max_total_submitted_cases": 1000,
    "max_unsubmitted_materialized_cases": 128
  },
  "quota": {
    "enabled": true,
    "mode": "hard",
    "hard_used_fraction": 0.95,
    "soft_used_fraction": 0.90,
    "min_free_bytes": 100000000000
  },
  "walltime": {
    "enabled": true,
    "mode": "warn",
    "max_runtime_fraction": 0.85,
    "partition_time_limit_seconds": 21600
  }
}
```

The exact enabled guard subset is optional. Missing guard sections mean no corresponding guard is enforced.

### Stopping

```json
"stopping": {
  "enabled": true,
  "mode": "hard",
  "max_iterations": 5,
  "hypervolume_plateau": {
    "enabled": true,
    "min_delta": 0.001,
    "objectives": ["score_guiding", "score_beamlike"],
    "aggregation": "any"
  },
  "candidate_novelty": {
    "enabled": true,
    "min_median_nearest_known_scaled_dist": 0.05
  },
  "manual_review": {
    "enabled": true,
    "review_threshold": 0.85,
    "block": false
  },
  "weak_batch": {
    "enabled": true,
    "block_on_weak": false
  }
}
```

Only configured stopping criteria are evaluated. A `STOP_OPTIMIZATION` file at the optimization root is also recognized by the optimizer state logic.
