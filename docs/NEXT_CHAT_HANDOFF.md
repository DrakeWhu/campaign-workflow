# Next chat handoff

## Project

General campaign workflow for simulation campaigns:

```text
simulation -> raw validation -> analysis -> reduced validation -> storage snapshot -> safe cleanup
```

Development is done locally under Git. SUNRISE receives the workflow through Git checkout/pull/tag, not rsync.

This workflow is being developed directly with ChatGPT in small auditable pieces. OpenCode is not used for this workflow.

The workflow is intended to be generic. Capillary guiding is the first real production example, not the architecture.

## Current repository

Local development path:

```text
C:\Users\juan.rodriguez-perez\OneDrive - ELI ERIC\Desktop\campaign-workflow
```

Repository initialized with Git.

Recent commits before Fase 2:

```text
b6eb066 Initialize campaign workflow documentation and example config
d548040 Add generic case state initialization
<latest> Normalize line endings for HPC workflow
```

After Fase 2 local, there should be a new commit similar to:

```text
<latest> Add generic raw validation workflow
```

The latest commit hash should always be obtained with:

```powershell
git log --oneline -5
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
* For WarpX/PyWarpX and SLURM submitters, do not invent scripts from scratch if working scripts already exist; ask for the current files unless explicitly told to build from zero.
* For this workflow, changes should be done directly with ChatGPT in small auditable chunks. Do not switch to OpenCode for implementation.

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
atomic JSON writes
safe transition rules
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

## Repository layout after Fase 0, Fase 1, and Fase 2a

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
│   │   ├── state.py
│   │   ├── path_safety.py
│   │   ├── manifests.py
│   │   └── transitions.py
│   ├── diagnostics/
│   │   ├── __init__.py
│   │   └── openpmd_hdf5.py
│   ├── analysis/
│   │   └── __init__.py
│   └── cli/
│       ├── __init__.py
│       ├── init_case_states.py
│       └── validate_raw_case.py
├── slurm/
└── tests/
    ├── test_raw_validation.py
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

Important correction:

```text
docs/CONFIG_CONTRAST.md
```

was renamed/fixed to:

```text
docs/CONFIG_CONTRACT.md
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

## Completed Fase 2a — Local generic raw validation against fake raw files

Fase 2a has now been implemented locally.

Purpose:

```text
Validate raw diagnostics according to campaign.json.
Implement generic raw diagnostic validation.
Implement first adapter stack with fake files and openpmd_hdf5 support prepared.
Write validation evidence into validation.json.
Write raw manifests atomically.
Transition Sim_done -> Raw_validated on success.
Transition Sim_done -> Validation_failed on failure.
Do not delete anything.
Do not analyze guiding metrics.
Do not depend on capillary-specific physics.
```

Implemented files:

```text
campaign_workflow/core/path_safety.py
campaign_workflow/core/manifests.py
campaign_workflow/core/transitions.py
campaign_workflow/diagnostics/openpmd_hdf5.py
campaign_workflow/cli/validate_raw_case.py
tests/test_raw_validation.py
```

Main command shape:

```powershell
python -m campaign_workflow.cli.validate_raw_case --campaign-root tests\fake_campaign --case-id 0 --dry-run --verbose
python -m campaign_workflow.cli.validate_raw_case --campaign-root tests\fake_campaign --case-id 0 --verbose
```

The CLI supports:

```text
--campaign-root
--case-id repeated multiple times
--dry-run
--verbose
```

The adapter supports diagnostic kinds:

```text
fake
openpmd_hdf5
```

For `fake`, allowed suffix defaults to:

```text
.fake
```

For `openpmd_hdf5`, allowed suffixes default to:

```text
.h5
.hdf5
```

The current `openpmd_hdf5` validation is intentionally minimal:

```text
path safety
glob resolution
file count
suffix check
regular file check
inside CASE_DIR check
symlink escape rejection
non-empty file check
minimum age check
HDF5 open/readability check using h5py
```

Strict openPMD semantic validation is not implemented yet. It can be added later using `openPMD-api` or more detailed metadata checks without changing the core workflow contract.

## Fase 2a safety contract

The raw validator rejects:

```text
absolute globs
globs containing ..
absolute config paths
paths containing ..
files outside CASE_DIR
symlink escapes outside CASE_DIR
directories instead of files
empty files
suffixes not allowed by diagnostic config
files younger than min_age_seconds
diagnostics with fewer files than min_files
unsupported diagnostic kinds
state-incompatible validation attempts
```

The raw validator does not:

```text
delete files
delete directories
run rm -rf
modify WarpX/PyWarpX inputs
run analysis
compute guiding metrics
mark cleanup as allowed
```

Important invariant:

```text
cleanup.cleanup_allowed = false
```

after raw validation, even if raw validation succeeds.

Reason:

```text
Raw validation alone does not authorize cleanup. Reduced validation is still required.
```

## Fase 2a state transitions

Raw validation is allowed from:

```text
Sim_done
Validation_failed
Raw_validated
```

Successful validation transitions to:

```text
Raw_validated
```

Failed validation transitions to:

```text
Validation_failed
```

Validation from `Created` is rejected without writing state/validation/manifests.

This is intentional: raw validation should only run after simulation completion or when revalidating/recovering a previous raw validation attempt.

## Fase 2a local test result

Command run:

```powershell
python -m unittest discover -s tests -p "test_*.py"
```

Observed result:

```text
Ran 5 tests in 0.230s

OK (skipped=1)
```

The skipped test is the symlink escape test. On Windows this can be skipped if the environment cannot create symlinks without special privileges. This is acceptable locally. On Linux/SUNRISE, this test should ideally run and pass because it protects against symlink escapes outside `CASE_DIR`.

Tests covered:

```text
successful fake raw validation writes manifest and state
dry-run does not write manifest or change state
missing required raw files marks Validation_failed
incompatible Created state is rejected without writing
symlink escape is rejected when symlink creation is available
```

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

## Immediate next decision

Fase 2a is locally implemented with fake raw diagnostics. The next decision is whether to do:

```text
Option A — Fase 1b SUNRISE dry-run/check now
```

or:

```text
Option B — Fase 2b local HDF5/openPMD smoke test first
```

Recommended order:

```text
1. Commit Fase 2a locally.
2. Run/verify latest git status and log.
3. Do Fase 2b local minimal HDF5 smoke test if h5py is available locally.
4. Then deploy to SUNRISE through Git for Fase 1b dry-run/check.
5. Then run validate_raw_case --dry-run on 1 real SUNRISE case.
```

Reason:

```text
Fase 2a already proves the generic safety machinery.
A tiny local HDF5 smoke test would verify that the openpmd_hdf5 branch works syntactically with h5py before touching real SUNRISE files.
SUNRISE should first be used in dry-run/check mode only.
```

## Commit commands after Fase 2a

Run:

```powershell
git status
git add campaign_workflow tests docs\NEXT_CHAT_HANDOFF.md
git commit -m "Add generic raw validation workflow"
git log --oneline -5
```

If `docs/NEXT_CHAT_HANDOFF.md` is updated after the Fase 2 commit, either amend the commit or make a second documentation commit:

```powershell
git add docs\NEXT_CHAT_HANDOFF.md
git commit -m "Update workflow handoff after raw validation phase"
```

## Fase 1b — SUNRISE dry-run/check

Operational bridge before touching real raw HDF5/openPMD data in write mode.

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

If the campaign already has case directories and a real `cases.tsv`, do not regenerate or reorder `cases.tsv`. It is immutable after campaign start.

## Fase 2b — Optional local HDF5/openPMD smoke test

Purpose:

```text
Verify that the openpmd_hdf5 diagnostic branch works with h5py locally.
Use tiny artificial HDF5 files.
Do not require strict openPMD metadata yet.
Do not use real WarpX data yet.
```

Expected possible test addition:

```text
tests/test_raw_validation_hdf5.py
```

or an extra method in:

```text
tests/test_raw_validation.py
```

Checks:

```text
valid .h5 file opens with h5py -> Raw_validated
invalid .h5 payload -> Validation_failed
.h5 suffix allowed
.hdf5 suffix allowed
non-HDF5 suffix rejected
```

Skip HDF5 tests cleanly if `h5py` is not installed locally.

## Fase 2c — SUNRISE real raw validation dry-run

Purpose:

```text
Run validate_raw_case against real SUNRISE case directories and real WarpX/openPMD HDF5 files in dry-run mode.
```

Expected command shape:

```bash
cd /gpfs/home/jrodriguez/warpx_runs/capillaries_bo_full_campaign
export PYTHONPATH="$PWD/workflow:${PYTHONPATH:-}"

python -m campaign_workflow.cli.validate_raw_case --campaign-root . --case-id 42 --dry-run --verbose
```

Only after inspecting dry-run output:

```bash
python -m campaign_workflow.cli.validate_raw_case --campaign-root . --case-id 42 --verbose
```

Do not run write mode over all cases until one or a few cases have been inspected manually.

Potential SUNRISE checks before using real HDF5 validation:

```bash
python - <<'PY'
import h5py
print("h5py", h5py.__version__)
PY
```

If `h5py` is missing on SUNRISE, either load the correct module/environment or keep openpmd_hdf5 validation dry-run disabled until the environment is fixed.

## Fase 3 — Analysis adapter framework + first guiding adapter

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

Analysis should produce reduced outputs defined by `campaign.json`, e.g.:

```text
post/guiding_metrics.csv
post/analysis_done.json
```

The core should only validate that configured outputs exist and satisfy the reduced-output contract.

Important architectural rule:

```text
The analysis adapter can be guiding-specific.
The core cannot be guiding-specific.
```

## Fase 4 — Reduced-output validation

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

Potential state flow:

```text
Analyzing -> Reduced_validated
Raw_validated -> Reduced_validated
```

The second transition may be useful if analysis was run outside the workflow but reduced outputs exist and pass the contract.

## Fase 5 — Storage snapshot

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

## Fase 6 — Cleanup dry-run

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
delete nothing
```

Cleanup dry-run must never decide based on state alone.

## Fase 7 — Cleanup execute

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

No deletion outside CASE_DIR.

No deletion of files not listed in the validated manifest.

## Fase 8 — SLURM wrappers

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

Before writing SLURM wrappers, ask for the existing working SUNRISE/SLURM files if any exist. Do not invent operational HPC submitters blindly.

## Fase 9 — Passive optimizer tick, not MORBO yet

Purpose:

```text
Read validated reduced outputs.
Build observations.tsv.
Compute objective/score.
Produce rankings and plots.
Do not launch jobs yet.
```

This is explicitly after the validation/cleanup base works.

Expected future concepts:

```text
observations.tsv
rankings
plots
score/objective calculation
multiobjective extension point
```

This phase should not launch simulations.

## Fase 10 — MORBO / recursive optimization

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

Future optimizer architecture must preserve:

```text
raw metrics in results/reduced outputs
objective/score as derived layer
explicit seeds
checkpoint/restart semantics
safe quota checks
no dependency on a resident daemon in V1
```

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
after raw validation
after reduced validation
after delete manifest dry-run
```

## Important implementation notes for future chats

* Use the ZIP/repo state as source of truth when code is provided.
* Do not assume uncommitted files exist; ask for ZIP or relevant file contents when necessary.
* Do not generate opaque patch files for operational HPC code.
* Prefer visible, auditable, directly copyable code and commands.
* Do not modify `lwfa_3d.py`, PyWarpX input templates, or physics parameters unless Juan explicitly asks.
* Keep all CLI tools separate by responsibility.
* Keep all cleanup behavior redundant and conservative.
* Use `unittest`, not pytest.
* Test command preferred by Juan:

```powershell
python -m unittest discover -s tests -p "test_*.py"
```

Do not suggest pytest unless Juan explicitly asks.

## Next recommended chat opening

Start the next chat with:

```text
Seguimos desde este handoff. Fase 2a local está implementada y los tests pasan. Quiero decidir si hacemos Fase 2b con un HDF5 fake local o si pasamos ya a Fase 1b en SUNRISE con dry-run/check.
```

Recommended next step:

```text
Commit Fase 2a, then either:
1. add a tiny local HDF5 smoke test for openpmd_hdf5, or
2. deploy to SUNRISE through Git and run only dry-run/check commands first.
```
