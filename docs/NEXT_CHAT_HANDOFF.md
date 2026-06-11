# Next chat handoff

## Project

General campaign workflow for simulation campaigns:

```text
simulation -> raw validation -> analysis -> reduced validation -> storage snapshot -> safe cleanup
```

Development is done locally under Git. SUNRISE receives the workflow through Git checkout/pull/tag, not rsync.

This workflow is being developed directly with ChatGPT in small auditable pieces. OpenCode is not used for this workflow.

## Current repository

Local development path:

```text
C:\Users\juan.rodriguez-perez\OneDrive - ELI ERIC\Desktop\campaign-workflow
```

Repository initialized with Git.

Recent commits:

```text
b6eb066 Initialize campaign workflow documentation and example config
d548040 Add generic case state initialization
<latest> Normalize line endings for HPC workflow
```

The latest commit hash should be obtained with:

```powershell
git log --oneline -3
```

## Hard design constraints

* Do not edit WarpX/PyWarpX physics inputs unless explicitly requested.
* Do not center the workflow on capillaries or guiding.
* Capillary guiding is the first production example, not the architecture.
* The core workflow must remain general.
* `cases.tsv` is immutable after campaign start.
* No SQL database in V1.
* No resident parent orchestrator daemon in V1.
* Simulation, raw validation, analysis, reduced validation, storage snapshots, cleanup, and future optimization are separate jobs.
* Case directories are the concurrency boundary.
* Destructive operations require redundant validation.
* Cleanup is two-phase: dry-run manifest, then execute.
* Cleanup must never delete directories.
* Cleanup must never use `rm -rf` on case directories or campaign roots.
* Cleanup must never delete paths outside a case directory.
* State alone is never sufficient permission to delete raw data.
* Scripts must be visible, auditable, and runnable directly on SUNRISE.
* Deployment to SUNRISE should happen through Git, preferably tags or pinned commits.

## Core abstraction

The reusable workflow concepts are:

```text
campaign.json
cases.tsv
CASE_DIR
state.json
validation.json
locks/
manifests/
post/
logs/
raw diagnostics
reduced outputs
storage snapshots
safe cleanup
```

The workflow core should know about:

```text
case manifests
case directories
states
validation documents
locks
manifests
storage accounting
safe path checks
```

The workflow core should not know about:

```text
capillary radius
laser case
plasma density
guiding metrics
uniform/vacuum/channel triplets
WarpX-specific physics
```

Campaign-specific behavior belongs in config and adapters.

## Current example campaign

The first production example is:

```text
examples/capillary_guiding/
```

It describes a SUNRISE WarpX/PyWarpX campaign with:

```text
simulation backend: warpx_picmi
scheduler: slurm
raw diagnostic kind: openpmd_hdf5
analysis adapter: guiding
reduced output: guiding_metrics.csv
cleanup target: diags/**/*.h5 and diags/**/*.hdf5
```

This is only the first real adapter stack.

Future campaigns should be able to change:

```text
input_template.py / input.py
simulation backend
diagnostic contract
analysis module
reduced output contract
cleanup globs
```

without rewriting the core workflow.

## Repository layout after Fase 0 and Fase 1

Expected tree:

```text
campaign-workflow/
├── README.md
├── .gitignore
├── .gitattributes
├── docs/
│   ├── CAMPAIGN_WORKFLOW.md
│   ├── STATE_MACHINE.md
│   ├── CONFIG_CONTRACT.md
│   ├── CLEANUP_SAFETY.md
│   └── NEXT_CHAT_HANDOFF.md
├── examples/
│   └── capillary_guiding/
│       ├── campaign.json
│       └── README.md
├── campaign_workflow/
│   ├── __init__.py
│   ├── core/
│   │   ├── __init__.py
│   │   ├── atomic_io.py
│   │   ├── tsv_cases.py
│   │   └── state.py
│   ├── diagnostics/
│   │   └── __init__.py
│   ├── analysis/
│   │   └── __init__.py
│   └── cli/
│       ├── __init__.py
│       └── init_case_states.py
├── slurm/
└── tests/
    └── fake_campaign/
        ├── campaign.json
        ├── cases.tsv
        ├── 000_fake_case/.gitkeep
        ├── 001_fake_case/.gitkeep
        └── 002_fake_case/.gitkeep
```

Runtime files such as `state.json`, `validation.json`, `logs/`, `post/`, `locks/`, and `manifests/` are intentionally ignored by Git.

## Completed Fase 0

Fase 0 created documentation and the first example config.

Files:

```text
README.md
.gitignore
docs/CAMPAIGN_WORKFLOW.md
docs/STATE_MACHINE.md
docs/CONFIG_CONTRACT.md
docs/CLEANUP_SAFETY.md
docs/NEXT_CHAT_HANDOFF.md
examples/capillary_guiding/campaign.json
examples/capillary_guiding/README.md
```

Fase 0 commit:

```text
b6eb066 Initialize campaign workflow documentation and example config
```

## Completed Fase 1

Fase 1 implemented generic state initialization.

Implemented files:

```text
campaign_workflow/__init__.py
campaign_workflow/core/__init__.py
campaign_workflow/core/atomic_io.py
campaign_workflow/core/tsv_cases.py
campaign_workflow/core/state.py
campaign_workflow/cli/__init__.py
campaign_workflow/cli/init_case_states.py
campaign_workflow/diagnostics/__init__.py
campaign_workflow/analysis/__init__.py
tests/fake_campaign/campaign.json
tests/fake_campaign/cases.tsv
tests/fake_campaign/000_fake_case/.gitkeep
tests/fake_campaign/001_fake_case/.gitkeep
tests/fake_campaign/002_fake_case/.gitkeep
```

Fase 1 commit:

```text
d548040 Add generic case state initialization
```

Validated commands:

```powershell
python -m campaign_workflow.cli.init_case_states --campaign-root tests\fake_campaign --dry-run --verbose
python -m campaign_workflow.cli.init_case_states --campaign-root tests\fake_campaign --verbose
python -m campaign_workflow.cli.init_case_states --campaign-root tests\fake_campaign --check --verbose
python -m campaign_workflow.cli.init_case_states --campaign-root tests\fake_campaign --case-id 1 --check --verbose
```

Observed result:

```text
cases_processed=3
cases_with_errors=0
errors=0
destructive_operations=0
```

Example initialized state:

```json
{
  "schema_version": 1,
  "case_id": 0,
  "case_name": "000_fake_case",
  "state": "Created",
  "history": [
    {
      "from": null,
      "to": "Created",
      "operation": "init_case_states",
      "reason": "initialized from case manifest"
    }
  ]
}
```

Example initialized validation:

```json
{
  "schema_version": 1,
  "case_id": 0,
  "case_name": "000_fake_case",
  "raw": {},
  "reduced": {},
  "cleanup": {
    "cleanup_allowed": false,
    "reason": "No raw/reduced validation has been performed yet."
  }
}
```

Implementation notes:

* JSON loaders were changed to use `utf-8-sig`, because PowerShell may write UTF-8 files with BOM.
* `.gitattributes` was added to force LF line endings for Python, shell, JSON, Markdown, TSV, YAML, etc.
* LF normalization is important because future SLURM/bash scripts must run correctly on Linux/SUNRISE.

## Current state machine

Main generic states:

```text
Created
Submitted
Running
Sim_done
Raw_validated
Analyzing
Reduced_validated
Raw_delete_eligible
Raw_deleted
```

Side states:

```text
Failed
Retryable
Stale
Quarantined
Validation_failed
Analysis_failed
Cleanup_failed
Disk_wait
```

Important naming decision:

Use generic states:

```text
Raw_validated
Reduced_validated
Raw_delete_eligible
Raw_deleted
```

not format-specific states like:

```text
H5_validated
CSV_validated
```

The concrete meaning of raw/reduced is defined by `campaign.json`.

## Intended full roadmap

### Fase 0 — Documentation and config contract

Completed.

Purpose:

```text
Define architecture, safety contract, config contract, and example campaign.
```

### Fase 1 — Generic state initialization

Completed locally.

Purpose:

```text
Read campaign.json and cases.tsv.
Create/check per-case state.json and validation.json.
Create per-case runtime directories.
No raw validation yet.
No analysis yet.
No cleanup yet.
```

### Fase 1b — SUNRISE dry-run/check

Next operational bridge before Fase 2 on real data.

Purpose:

```text
Deploy repo to SUNRISE through Git.
Place workflow checkout inside real campaign root.
Copy/adapt examples/capillary_guiding/campaign.json to campaign root.
Run init_case_states --dry-run and --check on the real 351-case campaign.
```

Expected SUNRISE commands, conceptually:

```bash
cd /gpfs/home/jrodriguez/warpx_runs/capillaries_bo_full_campaign

git clone <repo-url> workflow
# or git fetch / git checkout <tag> if already cloned

cp workflow/examples/capillary_guiding/campaign.json ./campaign.json

export PYTHONPATH="$PWD/workflow:${PYTHONPATH:-}"

python -m campaign_workflow.cli.init_case_states --campaign-root . --dry-run
python -m campaign_workflow.cli.init_case_states --campaign-root . --check
```

Do not execute write mode on SUNRISE until dry-run output is inspected.

### Fase 2 — Generic raw validation + openPMD/HDF5 adapter

Next development phase.

Purpose:

```text
Validate raw diagnostics according to campaign.json.
Implement first diagnostic adapter: openpmd_hdf5.
Write validation evidence into validation.json.
Write raw manifests.
Transition Sim_done -> Raw_validated, or -> Validation_failed.
```

Expected new files:

```text
campaign_workflow/core/path_safety.py
campaign_workflow/core/manifests.py
campaign_workflow/core/transitions.py
campaign_workflow/diagnostics/openpmd_hdf5.py
campaign_workflow/cli/validate_raw_case.py
```

Expected command shape:

```powershell
python -m campaign_workflow.cli.validate_raw_case --campaign-root tests\fake_campaign --case-id 0 --dry-run
```

For real SUNRISE campaign later:

```bash
python -m campaign_workflow.cli.validate_raw_case --campaign-root . --case-id 42 --dry-run
python -m campaign_workflow.cli.validate_raw_case --campaign-root . --case-id 42
```

Raw validation checks should include:

```text
case directory exists
state is compatible
configured raw diagnostic exists
glob resolves files
file count >= min_files
files have allowed suffixes
files are regular files
files are inside CASE_DIR
files are not symlink escapes
files are non-empty
files have minimum age
HDF5 files open with h5py
openPMD metadata/series validation if available
manifest written atomically
validation.json updated atomically
```

Fase 2 must not delete anything.

Fase 2 must not analyze guiding metrics.

Fase 2 must not depend on capillary-specific physics.

### Fase 3 — Analysis adapter framework + first guiding adapter

Purpose:

```text
Run case-local analysis after Raw_validated.
Keep analysis replaceable.
First adapter uses guiding analysis module.
```

Expected new files:

```text
campaign_workflow/analysis/base.py
campaign_workflow/analysis/guiding.py
campaign_workflow/cli/analyze_case.py
```

Expected state flow:

```text
Raw_validated -> Analyzing -> Reduced_validated
```

or:

```text
Raw_validated -> Analyzing -> Analysis_failed
```

Analysis should produce reduced outputs defined by campaign.json, e.g.:

```text
post/guiding_metrics.csv
post/analysis_done.json
```

The core should only validate that configured outputs exist and satisfy the reduced-output contract.

### Fase 4 — Reduced-output validation

Purpose:

```text
Validate reduced outputs independently of analysis execution.
```

Expected new files:

```text
campaign_workflow/cli/validate_reduced_case.py
campaign_workflow/analysis/csv_contract.py
```

Checks:

```text
configured reduced outputs exist
CSV is readable
CSV has min_rows
required columns exist
optional critical columns may be checked
validation.json updated
state moves to Reduced_validated
```

This should remain generic enough for non-guiding CSV outputs.

### Fase 5 — Storage snapshot

Purpose:

```text
Compute campaign-level storage accounting without modifying raw data.
```

Expected new files:

```text
campaign_workflow/cli/storage_snapshot.py
campaign_workflow/core/storage.py
```

Expected output:

```text
snapshots/storage_snapshot_latest.json
```

Expected fields:

```text
raw_live_GB
raw_delete_eligible_GB
raw_deleted_GB
validated_cases
submitted_cases
avg_raw_per_case_GB
safe_quota_GB
reserved_quota_GB
cases_by_state
```

This will later support quota decisions before launching more simulations.

### Fase 6 — Cleanup dry-run

Purpose:

```text
Resolve cleanup candidates and write delete manifests without deleting files.
```

Expected new files:

```text
campaign_workflow/cli/cleanup_raw_case.py
```

First implemented mode:

```bash
python -m campaign_workflow.cli.cleanup_raw_case --campaign-root . --case-id 42 --dry-run
```

Dry-run must:

```text
verify Raw_delete_eligible or equivalent eligibility
verify raw validation evidence
verify reduced validation evidence
verify case is not running if possible
resolve cleanup globs
validate every candidate path
write manifest
write cleanup validation evidence
delete nothing
```

### Fase 7 — Cleanup execute

Purpose:

```text
Delete only files listed in a validated dry-run manifest.
```

Command shape:

```bash
python -m campaign_workflow.cli.cleanup_raw_case --campaign-root . --case-id 42 --execute
```

Execute must:

```text
require existing manifest
revalidate state
revalidate validation.json
revalidate paths
delete only manifest-listed files
write post/raw_deleted.json
update validation.json
transition Raw_delete_eligible -> Raw_deleted
```

No directory deletion in V1.

No reinterpretation of globs during execute.

### Fase 8 — SLURM wrappers

Purpose:

```text
Add thin SLURM scripts that call CLI commands.
```

Expected files:

```text
slurm/submit_validate_raw_array.sh
slurm/submit_analysis_array.sh
slurm/submit_validate_reduced_array.sh
slurm/submit_cleanup_array.sh
```

The SLURM scripts should be thin wrappers:

```text
set strict bash mode
cd campaign root
load environment if needed
export PYTHONPATH
call python -m campaign_workflow.cli.<command>
write logs
```

Do not put core logic in SLURM scripts.

### Fase 9 — Passive optimizer tick, not MORBO yet

Purpose:

```text
Read validated reduced outputs.
Build observations.tsv.
Compute objective/score.
Produce rankings and plots.
Do not launch jobs yet.
```

This is explicitly after the validation/cleanup base works.

### Fase 10 — MORBO / recursive optimization

Future phase.

Purpose:

```text
optimizer_tick reads observations
decides if enough new data exists
proposes new candidates
writes candidate batch manifests
submits simulation jobs if quota allows
```

This is not part of V1.

## Cleanup safety summary

Absolute prohibitions:

```text
Never delete campaign root.
Never delete case directories.
Never run rm -rf on user data.
Never delete files outside CASE_DIR.
Never delete files not listed in a manifest.
Never delete raw diagnostics before reduced validation.
Never delete while case is running.
Never use state.json alone as deletion permission.
```

Allowed deletion in V1:

```text
explicit regular files
inside CASE_DIR
matching configured raw_delete_globs
listed in validated manifest
```

## Next recommended chat opening

Start the next chat with:

```text
Seguimos desde este handoff. Vamos con Fase 2, pero antes quiero decidir si hacemos Fase 1b en SUNRISE o si implementamos Fase 2 local con fake raw files primero.
```

Recommended next step:

```text
Implement Fase 2 locally against fake raw diagnostics first, then deploy to SUNRISE for dry-run against real HDF5/openPMD files.
```
