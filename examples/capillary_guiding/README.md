# Capillary guiding example

This directory documents the current production example for `campaign-workflow`: a WarpX/PyWarpX capillary guiding campaign on SUNRISE.

It is an example configuration and wrapper set, not the generic workflow core.

## What is generic

The reusable workflow concepts are:

```text
campaign.json
cases.tsv
case directories
state.json
validation.json
raw diagnostics
raw validation
external analysis
reduced outputs
reduced validation
cleanup manifests
storage snapshots
optimizer iterations
```

## What is capillary-specific

The capillary campaign uses domain-specific columns and environment variables such as:

```text
LASER_CASE
PLASMA_KIND
N0_CM3
PLATEAU_LENGTH_MM
RADIUS_UM
FOCUS_OFFSET_FROM_PLATEAU_START_MM
CAP_RMAX_UM
CAP_NR
CAP_* environment variables
```

Those defaults remain in the `1.0.0` materializer for compatibility with the proven production campaigns. They should be generalized in a later minor release, not during the 1.0 freeze.

## Files in this example

```text
campaign.json                              example campaign contract
resolve_field_diag_dir.py                  field diagnostic resolver helper
run_guiding_case_analysis_sunrise.sh       SUNRISE wrapper for guiding analysis
run_particle_analysis_if_available.py      optional particle analysis wrapper
```

Operational SUNRISE scripts live in:

```text
examples/sunrise/
```

Important scripts:

```text
submit_case_cycle_array.sh
run_warpx_case_sunrise.sh
submit_morbo_chain.py
run_optimizer_tick_materialize_only.sh
run_iteration_array.sh
```

## Expected campaign root

```text
capillaries_campaign/
├── campaign.json
├── cases.tsv
├── input_template.py
├── workflow/
├── array_logs/
└── CASE_DIRS...
```

Typical setup:

```bash
cd /gpfs/home/jrodriguez/warpx_runs/capillaries_campaign
export PYTHONPATH="$PWD/workflow:${PYTHONPATH:-}"

python -m campaign_workflow.cli.materialize_cases --campaign-root . --dry-run --verbose
python -m campaign_workflow.cli.materialize_cases --campaign-root .
python -m campaign_workflow.cli.init_case_states --campaign-root . --check
```

For production runs on SUNRISE, prefer the static scripts under `examples/sunrise/` rather than ad hoc launchers.
