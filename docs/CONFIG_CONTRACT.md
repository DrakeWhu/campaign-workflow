# Campaign configuration contract

## Purpose

`campaign.json` defines how the generic workflow should interpret one concrete campaign.

It maps generic workflow concepts to campaign-specific files, diagnostics, analysis modules, and cleanup rules.

## Required top-level fields

```json
{
  "schema_version": 1,
  "campaign_name": "example_campaign",
  "case_manifest": "cases.tsv",
  "case_id_column": "CASE_ID",
  "case_name_column": "CASE_NAME",
  "simulation": {},
  "raw_diagnostics": [],
  "analysis": {},
  "cleanup": {}
}
```

## Case manifest

The case manifest is usually a TSV file.

Required concepts:

- case ID
- case directory name

Example:
```json
{
  "case_manifest": "cases.tsv",
  "case_id_column": "CASE_ID",
  "case_name_column": "CASE_NAME"
}
```

The manifest is immutable after campaign start.

If a future optimizer proposes new candidates, it must write them to a new batch manifest rather than modifying the original manifest in place.

## Simulation section

Example:
```json
{
  "simulation": {
    "backend": "warpx_picmi",
    "scheduler": "slurm",
    "input_script": "input.py",
    "completion_marker": "post/sim_done.json",
    "failure_marker": "post/sim_failed.json"
  }
}
```
The core workflow does not run simulations directly in V1. It only records and validates evidence produced by simulation jobs.

Raw diagnostics section

Example:
```json
{
  "raw_diagnostics": [
    {
      "name": "fields_openpmd",
      "kind": "openpmd_hdf5",
      "path": "diags",
      "glob": "diags/**/*.h5",
      "min_files": 1,
      "min_age_seconds": 600,
      "required": true
    }
  ]
}
```
`kind` selects a validator adapter.

Initial planned kinds:
```
openpmd_hdf5
```
Future possible kinds:
```
openpmd_adios
sdf
image_stack
custom_directory
```

## Analysis section

Example:
```json
{
  "analysis": {
    "name": "guiding",
    "kind": "python_module",
    "adapter": "guiding",
    "inputs": ["fields_openpmd"],
    "outputs": [
      {
        "name": "guiding_metrics",
        "kind": "csv",
        "path": "post/guiding_metrics.csv",
        "required_columns": []
      }
    ]
  }
}
```

The analysis adapter may call a campaign-specific module, but the workflow core should only care about:

- inputs
- outputs
- exit status
- validation contract

## Reduced outputs

Reduced outputs are small, persistent scientific products.

Examples:
```json
{
  "outputs": [
    {
      "name": "metrics",
      "kind": "csv",
      "path": "post/metrics.csv",
      "min_rows": 1,
      "required_columns": ["iteration"]
    }
  ]
}
```

## Cleanup section

Example:
```json
{
  "cleanup": {
    "raw_delete_globs": ["diags/**/*.h5"],
    "require_raw_validated": true,
    "require_reduced_validated": true,
    "require_delete_manifest": true,
    "allow_directory_delete": false
  }
}
```
Hard rules:

- `allow_directory_delete` must remain false in V1
- cleanup may delete explicit files only
- cleanup may not delete case directories
- cleanup may not delete paths outside the case directory
- cleanup may not follow symlink escapes

## Optional maintenance section

Future versions may support a campaign maintenance section.

This section is not required for V1. If absent, no automatic maintenance tick,
walltime guard, quota guard, or rerun launcher is enabled.

Example:

```json
{
  "maintenance": {
    "enabled": false,
    "lock_name": "campaign_maintenance",
    "walltime_guard": {
      "enabled": false,
      "safety_margin_minutes": 30,
      "risk_threshold": "TIMEOUT_RISK",
      "failure_kind": "walltime_insufficient",
      "partition_escalation": {
        "T6H": {
          "partition": "T12H",
          "time": "12:00:00"
        },
        "T12H": {
          "partition": "T24H",
          "time": "24:00:00"
        }
      }
    },
    "quota_guard": {
      "enabled": false,
      "command": ["lfs", "quota", "-h", "-u", "{user}", "{campaign_root}"],
      "soft_used_fraction": 0.80,
      "hard_used_fraction": 0.90
    },
    "rerun_launcher": {
      "enabled": false,
      "max_cases_per_tick": 50,
      "submit_command_template": [
        "sbatch",
        "--partition={partition}",
        "--time={time}",
        "--array={array_spec}",
        "{case_cycle_script}"
      ]
    }
  }
}

Maintenance configuration must remain generic.

It must not contain capillary physics assumptions. It may contain scheduler policy,
quota policy, walltime policy, and rerun policy.

## Git reproducibility metadata

Every job should eventually record the workflow commit:
```json
{
  "workflow_git": {
    "commit": "...",
    "tag": "...",
    "dirty": false
  }
}
```

This is not required for phase 0, but the file formats should leave room for it


