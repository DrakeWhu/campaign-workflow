# campaign-workflow

General file-based workflow for HPC simulation campaigns.

The project provides a small, auditable campaign layer around simulation jobs, raw diagnostic validation, reduced-data analysis, storage snapshots, and safe cleanup of heavy raw outputs.

The first production target is a WarpX/PyWarpX campaign on SUNRISE using SLURM arrays and openPMD/HDF5 diagnostics, but the workflow core must remain independent of a specific physics input, diagnostic format, or analysis module.

## Non-goals

- This repository does not define the physics of a simulation.
- This repository does not generate WarpX input templates unless explicitly extended to do so.
- This repository does not own Bayesian optimization in V1.
- This repository does not use SQL in V1.
- This repository must not delete raw data without redundant validation and explicit manifests.

## Design principle

Each case directory is the ownership boundary for:

- raw diagnostics,
- reduced outputs,
- state,
- validation reports,
- locks,
- manifests,
- logs,
- cleanup evidence.

Global files are allowed only for immutable manifests, configuration, read-only summaries, snapshots, and explicitly lock-protected operations.

## Deployment model

Development happens locally under Git.

SUNRISE receives the workflow through Git, preferably by checking out a tagged version inside a campaign directory:

```text
campaign_root/
├── campaign.json
├── cases.tsv
├── submit_campaign_array.sh
├── workflow/          # git clone of this repository
└── CASE_DIRS...
```

SLURM jobs call the workflow with:

```
export PYTHONPATH="${CAMPAIGN_ROOT}/workflow:${PYTHONPATH:-}"
python -m campaign_workflow.cli.<command> --campaign-root .
```

## First concrete example

`examples/capillary_guiding/` documents the current capillary guiding campaign:
- simulation backend: WarpX/PyWarpX;
- scheduler: SLURM array;
- raw diagnostic kind: openPMD/HDF5;
- reduced analysis: guiding metrics CSV;
- cleanup target: raw HDF5 files after validation.

This example is not the core architecture.

## Production lesson: cooperative maintenance without a daemon

A real SUNRISE WarpX/PyWarpX campaign showed that long campaigns need more than a
static SLURM array.

The workflow direction is now:

```text
case-local cycle
+ short lock-protected campaign maintenance ticks
```

Case-local tasks run simulation, validation, analysis, and cleanup for one case.

After finishing, a task may attempt a campaign-wide maintenance tick. If it gets
the campaign lock, it may inspect quota, walltime risk, stale running cases, and
rerun plans. If it does not get the lock, it exits normally.

This preserves the no-resident-daemon rule while allowing unattended campaigns to
self-regulate on HPC systems.

- [Optimization module contract](docs/OPTIMIZATION_MODULE_CONTRACT.md): file-based contract for external optimizers that consume validated reduced outputs and propose candidate batches without launching simulations or reading raw diagnostics.
- [Candidate batch contract](docs/CANDIDATE_BATCH_CONTRACT.md): artifact contract for optimizer iterations, recommended candidates, launchable candidate batches, and future campaign preparation.