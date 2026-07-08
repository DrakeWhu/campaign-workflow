# campaign-workflow

`campaign-workflow` is a small, file-based orchestration layer for HPC simulation campaigns.

Its production target is many independent simulation cases running under SLURM, producing heavy raw diagnostics, being reduced by external analysis code, and being cleaned up safely once reduced outputs have been validated.

The first production backend is WarpX/PyWarpX on SUNRISE with openPMD/HDF5 diagnostics, but the core workflow is intentionally not a WarpX input generator and does not own physics logic.

## What this package owns

- campaign and case layout interpretation;
- per-case state and validation evidence;
- simulation lifecycle marker CLIs;
- raw diagnostic validation;
- command-based external analysis execution;
- reduced-output validation;
- storage snapshots;
- manifest-driven raw cleanup;
- file-based optimization iteration bookkeeping;
- daemon-free SLURM-compatible iteration orchestration.

## What this package does not own

- WarpX physics inputs or PIC parameters;
- the scientific analysis algorithms;
- the Bayesian optimizer model internals;
- Optimas, Ax, BoTorch, Torch, or campaign-specific ML dependencies;
- SQL-backed state;
- resident daemon orchestration;
- directory-level deletion of raw data.

External modules communicate with the workflow through files, commands, and explicit contracts.

## Why daemon-free

HPC campaigns should not depend on a resident parent process surviving for days. The workflow state is stored on disk, and each action is a finite command:

```text
case-local SLURM jobs
+ explicit optimizer ticks
+ optional finite pre-submitted chains
```

A case-local job can run simulation, raw validation, analysis, reduced validation, cleanup eligibility, cleanup dry-run, and cleanup execute for one case. Optimization-level ticks inspect iteration state, reconcile completed iterations, call an external optimizer command, prepare/materialize the next campaign, and optionally submit the next SLURM array.

## Repository layout

```text
campaign_workflow/          Python package and CLI implementations
docs/                       normative contracts and workflow documentation
examples/capillary_guiding/ capillary guiding campaign example config/wrappers
examples/sunrise/           static SUNRISE SLURM scripts
tests/                      unittest suite
```

## Minimal campaign layout

```text
campaign_root/
├── campaign.json
├── cases.tsv
├── input_template.py        # required by materialize_cases
├── workflow/                # git checkout of this repo, or PYTHONPATH equivalent
└── CASE_DIRS...
```

The expected per-case layout is:

```text
CASE_DIR/
├── input.py                 # generated from input_template.py
├── case.env                 # generated from cases.tsv
├── state.json
├── validation.json
├── diags/                   # raw simulation diagnostics
├── post/                    # markers and lifecycle evidence
├── manifests/               # raw and cleanup manifests
└── logs/                    # phase logs
```

The exact filenames are configurable through `campaign.json` where supported.

## Basic usage

From a campaign root:

```bash
export PYTHONPATH="$PWD/workflow:${PYTHONPATH:-}"

python -m campaign_workflow.cli.materialize_cases --campaign-root . --dry-run --verbose
python -m campaign_workflow.cli.materialize_cases --campaign-root .

python -m campaign_workflow.cli.init_case_states --campaign-root . --check
```

For an existing campaign where simulations were already run outside the workflow, use the marker CLIs or backfill path deliberately, then validate:

```bash
python -m campaign_workflow.cli.validate_raw_case --campaign-root . --dry-run --verbose
python -m campaign_workflow.cli.validate_raw_case --campaign-root .

python -m campaign_workflow.cli.analyze_case --campaign-root . --dry-run --verbose
python -m campaign_workflow.cli.analyze_case --campaign-root .

python -m campaign_workflow.cli.validate_reduced_case --campaign-root . --dry-run --verbose
python -m campaign_workflow.cli.validate_reduced_case --campaign-root .
```

Cleanup is always explicit and two-step:

```bash
python -m campaign_workflow.cli.mark_raw_delete_eligible --campaign-root . --dry-run
python -m campaign_workflow.cli.mark_raw_delete_eligible --campaign-root .

python -m campaign_workflow.cli.cleanup_raw_case --campaign-root . --dry-run --verbose
python -m campaign_workflow.cli.cleanup_raw_case --campaign-root . --execute --verbose
```

`--dry-run` cleanup writes or previews a deletion manifest. `--execute` deletes only files listed in a validated manifest. It never deletes directories.

## SUNRISE execution model

Static, versioned SUNRISE scripts live under `examples/sunrise/`.

Important entrypoints:

```text
submit_case_cycle_array.sh              case-local simulation -> validation -> analysis -> cleanup
run_warpx_case_sunrise.sh               WarpX/PyWarpX case runner wrapper
submit_morbo_chain.py                   finite pre-submitted optimizer chain
run_optimizer_tick_materialize_only.sh  optimizer tick without nested sbatch
run_iteration_array.sh                  iteration array runner
```

For SUNRISE, the proven pattern is to submit a finite chain from the login node instead of relying on nested `sbatch` from compute nodes.

## Optimization model

`campaign-workflow` does not implement the optimizer model. It orchestrates optimizer iterations through files:

```text
optimization_root/
├── optimization.json
├── optimization_state.json
├── iterations/
│   ├── iter_000/
│   └── iter_001/
└── optimizer_runs/
    └── iter_001/
        └── outputs/
            ├── candidate_batch.tsv
            └── batch_campaign_plan.json
```

The external optimizer command reads validated reduced outputs from completed iterations and writes `candidate_batch.tsv` plus `batch_campaign_plan.json`. The workflow then prepares and materializes the next iteration campaign root.

See:

- `docs/CAMPAIGN_WORKFLOW.md`
- `docs/CONFIG_CONTRACT.md`
- `docs/OPTIMIZATION_MODULE_CONTRACT.md`
- `docs/CANDIDATE_BATCH_CONTRACT.md`

## Capillary guiding example

`examples/capillary_guiding/` documents the current WarpX/PyWarpX capillary guiding campaign shape. It is an example, not the core architecture.

The current materialization defaults are still the capillary `CAP_*` environment mapping used by the production campaign. This is intentionally preserved for `1.0.0`. A later `1.1` cleanup can move those defaults into explicit campaign configuration without changing the proven runtime behavior.

## Tests

Run the permanent local suite with `unittest`:

```bash
python -m unittest discover -s tests -p "test_*.py"
```

Do not use `pytest` as the project test command.

## Version

Current frozen functional version: `1.0.0`.
