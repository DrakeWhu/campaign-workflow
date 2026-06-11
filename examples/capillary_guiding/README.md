# Capillary guiding example

This directory documents the first concrete production example for the generic campaign workflow.

It is based on a WarpX/PyWarpX campaign where:

- `cases.tsv` defines the case matrix;
- each `CASE_ID` maps to a `CASE_NAME`;
- each case directory contains an `input.py` driven by environment variables;
- WarpX writes openPMD/HDF5 diagnostics;
- a guiding analysis module reduces raw diagnostics to a metrics CSV;
- raw HDF5 files may become cleanup-eligible after validation.

## Why this is an example

The campaign workflow must not be designed around capillary guiding specifically.

The reusable workflow concepts are:

```text
case manifest
case directory
raw diagnostics
raw validation
reduced outputs
reduced validation
safe cleanup
state machine
locks
manifests
storage snapshots
```

The capillary-specific concepts are:

```
laser case
plasma kind
capillary radius
plateau length
focus offset
guiding metrics
uniform/vacuum/channel triplets
```

The second group must not leak into the workflow core.

## Expected real campaign layout on SUNRISE

```
capillaries_bo_full_campaign/
├── campaign.json
├── cases.tsv
├── submit_campaign_array.sh
├── workflow/
└── CASE_DIRS...
```

The `campaign.json` in this directory is a template/example. The real campaign should copy or adapt it at campaign root.

## Deployment idea

On SUNRISE:
```bash 
cd /gpfs/home/jrodriguez/warpx_runs/capillaries_bo_full_campaign
git clone <repo-url> workflow
cp workflow/examples/capillary_guiding/campaign.json ./campaign.json
```

Then workflow commands can be called with:

```bash
export PYTHONPATH="$PWD/workflow:${PYTHONPATH:-}"
python -m campaign_workflow.cli.init_case_states --campaign-root . --dry-run
```

Operational scripts are not implemented in phase 0
