# Campaign workflow contract

This document is normative for `campaign-workflow` `1.0.0`.

## Purpose

`campaign-workflow` orchestrates simulation campaigns as explicit file-based transactions. It is designed for HPC runs where raw outputs are large, analysis is external, and cleanup must be conservative.

The workflow is daemon-free: every operation is a finite CLI invocation that reads configuration and evidence from disk, writes explicit state/evidence files, and exits.

## Boundaries

The workflow core owns:

- campaign configuration loading;
- case manifest loading;
- case directory creation and materialization;
- state transitions;
- validation evidence;
- lifecycle markers;
- raw diagnostic validation;
- reduced-output validation;
- command-based analysis invocation;
- storage accounting;
- cleanup manifests and raw file deletion;
- optimization iteration bookkeeping and finite tick execution.

The workflow core does not own:

- physical simulation parameters;
- WarpX/PICMI input semantics;
- scientific metric definitions;
- optimizer model internals;
- scheduler policy beyond static script interfaces;
- a resident parent daemon.

## Campaign root

A campaign root contains at least:

```text
campaign_root/
├── campaign.json
├── cases.tsv
└── CASE_DIRS...
```

For materialized simulation campaigns it normally also contains:

```text
input_template.py
workflow/                  # git checkout or equivalent PYTHONPATH source
array_logs/
snapshots/
```

`campaign.json` defines the workflow contract. `cases.tsv` defines the case manifest and is treated as stable once production execution begins.

## Case identity

Cases are loaded from the configured manifest:

```json
{
  "case_manifest": "cases.tsv",
  "case_manifest_format": "tsv",
  "case_id_column": "CASE_ID",
  "case_name_column": "CASE_NAME"
}
```

Each case must have a unique integer `CASE_ID` and a safe single-directory `CASE_NAME`.

## Case-local layout

By default each case directory contains:

```text
CASE_DIR/
├── state.json
├── validation.json
├── locks/
├── manifests/
├── post/
└── logs/
```

The filenames and subdirectories are controlled by the optional `state` section in `campaign.json`.

## Materialization

`materialize_cases` creates the case-local simulation files:

```text
CASE_DIR/input.py
CASE_DIR/case.env
```

It copies `input_template.py` and renders `case.env` from `cases.tsv` using `case_materialization` rules.

The `1.0.0` default materialization profile is the proven capillary guiding `CAP_*` mapping. This remains for backward compatibility with the production SUNRISE campaigns. New non-capillary campaigns should declare explicit `case_materialization.env_columns` and `env_constants` in `campaign.json`.

`materialize_cases` refuses to overwrite existing `input.py` or `case.env` unless `--overwrite` is passed.

## Simulation lifecycle

The workflow records simulation lifecycle evidence through marker CLIs:

```text
mark_sim_submitted
mark_sim_running
mark_sim_done
mark_sim_failed
```

Markers are written under `CASE_DIR/post/` by default:

```text
post/sim_submitted.json
post/sim_running.json
post/sim_done.json
post/sim_failed.json
```

The external simulation runner owns the actual code execution. For WarpX/PyWarpX this is normally a shell wrapper that loads modules, activates the correct environment, sources `case.env`, and runs the case-local `input.py`.

## Raw validation

`validate_raw_case` validates configured raw diagnostics after simulation completion.

Supported raw diagnostic kinds in `1.0.0`:

```text
fake
openpmd_hdf5
```

For `openpmd_hdf5`, the validator checks path safety, glob resolution inside the case directory, file count, suffix, non-empty files, minimum age, and HDF5 readability. It does not interpret the physics content.

Successful raw validation writes validation evidence and raw manifests. Failed required diagnostics prevent the case from advancing.

## Analysis

`analyze_case` invokes the configured external analysis adapter. The production adapter type is command-based:

```json
"analysis": {
  "adapter": "command",
  "command": ["bash", "path/to/wrapper.sh", "{case_dir}"],
  "outputs": [...]
}
```

The workflow captures stdout/stderr into case-local logs, records analysis markers, and validates configured reduced outputs after the command returns.

Scientific interpretation belongs to the external analysis module.

## Reduced validation

`validate_reduced_case` validates `analysis.outputs`. The supported reduced output kind in `1.0.0` is CSV.

For CSV outputs the validator checks:

- path safety inside `CASE_DIR`;
- readability as CSV;
- minimum row count;
- required columns;
- required/optional output semantics.

`--legacy-reduced-only` exists for adopted campaigns where raw diagnostics are no longer available. It does not create raw validation evidence and never authorizes cleanup.

## Cleanup

Cleanup is conservative and manifest-driven.

The normal path is:

```text
Reduced_validated
-> Raw_delete_eligible
-> cleanup dry-run manifest
-> cleanup execute
-> Raw_deleted
```

`cleanup_raw_case --dry-run` creates or updates a manifest of exact files eligible for deletion. It does not delete data.

`cleanup_raw_case --execute` deletes only files listed in an existing validated manifest. It never deletes directories and must not follow paths outside the case directory.

## Storage snapshots

`storage_snapshot` computes campaign-level and case-level storage accounting without modifying raw data. The default output is:

```text
snapshots/storage_snapshot_latest.json
```

Snapshots are informational. They do not authorize deletion by themselves.

## Optimization orchestration

Optimization is represented by an `optimization_root` containing iteration campaign roots and optimizer run outputs:

```text
optimization_root/
├── optimization.json
├── optimization_state.json
├── iterations/
│   └── iter_000/
└── optimizer_runs/
    └── iter_001/
        └── outputs/
```

`optimizer_tick` can:

- audit finite iteration state;
- initialize or write `optimization_state.json`;
- evaluate configured guards;
- evaluate stopping conditions;
- submit an iteration through a static SLURM script;
- reconcile completed iteration state;
- call an external optimizer command;
- verify optimizer outputs;
- prepare and materialize the next iteration campaign;
- execute one finite loop step.

The optimizer model itself is external. The workflow consumes its files.

## SUNRISE finite-chain model

On SUNRISE, the production-safe model is a finite pre-submitted chain from the login node. It avoids assuming that nested `sbatch` from compute nodes is available.

The static scripts under `examples/sunrise/` are part of the operational example and should be versioned with campaign changes.

## Test contract

The project test command is:

```bash
python -m unittest discover -s tests -p "test_*.py"
```

A documentation-only release should still pass the full suite.
