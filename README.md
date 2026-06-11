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