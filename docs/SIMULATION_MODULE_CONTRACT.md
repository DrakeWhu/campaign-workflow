# Simulation module contract

This document defines the boundary between `campaign-workflow` and a simulation runner.

## Core rule

The simulation module owns execution of the physical simulation. The workflow owns evidence around that execution.

For the current production campaigns, the simulation module is a WarpX/PyWarpX runner called from a static SUNRISE SLURM script. The workflow does not edit the physics input unless explicitly asked.

## Inputs provided by the workflow

A simulation wrapper may assume:

```text
CASE_DIR/
├── input.py
├── case.env
├── state.json
└── validation.json
```

The wrapper should receive explicit paths through arguments or environment variables. The static SUNRISE scripts use case-local environment such as:

```text
CAMPAIGN_ROOT
CAMPAIGN_NAME
CASE_DIR
CASE_ID
CASE_NAME
```

The case-local `case.env` file is produced by `materialize_cases` and may contain campaign-specific variables such as the current capillary `CAP_*` mapping.

## Expected simulation behavior

A successful simulation should:

1. run the case-local input;
2. write raw diagnostics declared in `campaign.json`;
3. return exit code `0`;
4. allow the workflow to mark `Sim_done`.

A failed simulation should return non-zero or allow the wrapper to call `mark_sim_failed` with explicit failure metadata.

## Lifecycle markers

The workflow provides CLIs for explicit markers:

```text
campaign_workflow.cli.mark_sim_submitted
campaign_workflow.cli.mark_sim_running
campaign_workflow.cli.mark_sim_done
campaign_workflow.cli.mark_sim_failed
```

Default marker paths:

```text
post/sim_submitted.json
post/sim_running.json
post/sim_done.json
post/sim_failed.json
```

These markers are evidence. They do not replace raw validation.

## Raw outputs

A successful simulation must produce the raw diagnostics configured under `raw_diagnostics`. Example:

```json
{
  "name": "fields_openpmd",
  "kind": "openpmd_hdf5",
  "glob": "diags/**/*.h5",
  "min_files": 1,
  "required": true
}
```

The workflow validates these files later with `validate_raw_case`. The simulation runner should not write workflow validation evidence itself.

## Managed case-local cycle

The production SUNRISE case-cycle script may execute several workflow phases in one SLURM job:

```text
mark_sim_submitted
mark_sim_running
run_external_simulation
mark_sim_done or mark_sim_failed
validate_raw_case
analyze_case
mark_raw_delete_eligible
cleanup_raw_case --dry-run
cleanup_raw_case --execute
```

The phases remain explicit even when run inside one job: each phase has separate logs, evidence, and failure behavior.

## What the simulation module must not do

The simulation module must not:

- delete raw diagnostics;
- write cleanup manifests;
- modify reduced-output validation evidence;
- modify optimizer state;
- silently retry failed cases without explicit workflow evidence.
