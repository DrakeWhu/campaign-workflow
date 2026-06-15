# Next chat handoff — campaign-workflow

## Purpose

`campaign-workflow` is a generic campaign coordination layer for simulation campaigns:

```text
simulation -> raw validation -> analysis -> reduced validation -> storage snapshot -> cleanup eligibility -> cleanup dry-run manifest -> cleanup execute
```

The workflow is generic. Capillary guiding is the first real production example, not the architecture.

Development is done locally under Git and deployed to SUNRISE through Git checkout/pull. OpenCode is not used for this workflow.

## Non-negotiable rules

* Do not edit WarpX/PyWarpX physics inputs unless explicitly requested.
* Do not invent SLURM/PyWarpX submitters from scratch if working scripts exist; ask for the current files first.
* Do not generate opaque patches, ZIPs, or downloadable code artifacts.
* All operational scripts/commands must be visible, auditable, and copyable.
* Use `unittest`, not `pytest`.
* Standard local test command:

```bash
python -m unittest discover -s tests -p "test_*.py"
```

* Keep virtual environments separate:

```text
campaign-workflow-py310    -> workflow orchestration, states, validation, manifests, storage, cleanup
guiding-analysis-py310     -> guiding / particle analysis
warpx-26.05-py314          -> PyWarpX / WarpX simulation execution
```

* `cases.tsv` is immutable after campaign start.
* State alone is never sufficient permission to delete raw data.
* Cleanup must never delete directories.
* Cleanup must never use `rm -rf` on campaign data.
* Cleanup must never delete files outside `CASE_DIR`.
* Cleanup execute must delete only files listed in a validated dry-run manifest.

## Repository / deployment

Local development path:

```text
C:\Users\juan.rodriguez-perez\OneDrive - ELI ERIC\Desktop\campaign-workflow
```

GitHub remote:

```text
git@github.com:DrakeWhu/campaign-workflow.git
```

SUNRISE checkout:

```text
~/apps/src/campaign-workflow
```

SUNRISE checkout should track `master`. SUNRISE has an old Git, so prefer:

```bash
cd ~/apps/src/campaign-workflow
git fetch origin
git checkout master
git merge --ff-only origin/master
```

or, if supported:

```bash
git pull --ff-only origin master
```

## SUNRISE environment

System Python is too old:

```text
/usr/bin/python3 -> Python 3.6.8
```

Working workflow environment:

```bash
source ~/apps/env/campaign-workflow.sh
```

Equivalent stack:

```text
GCC/12.1.0 + Python/3.10.12 + ~/apps/venvs/campaign-workflow-py310
```

Important `h5py` note:

```bash
python -m pip uninstall -y h5py numpy
python -m pip install --only-binary=:all: "numpy<2" "h5py==3.10.0"
```

This was needed because a plain `pip install h5py` tried to build a newer h5py against an incompatible system HDF5.

## Core workflow concepts

Per campaign:

```text
campaign.json
cases.tsv
snapshots/
```

Per case:

```text
CASE_DIR/
  state.json
  validation.json
  locks/
  manifests/
  post/
  logs/
  raw diagnostics
  reduced outputs
```

The workflow core knows about:

```text
case manifests
case directories
states
validation documents
locks
safe path checks
atomic JSON writes
storage accounting
safe transition rules
cleanup manifests
```

The workflow core must not know about:

```text
capillary radius
laser case
plasma density
guiding physics
particle physics details
WarpX-specific physical parameters
```

Campaign-specific behavior belongs in `campaign.json`, wrappers, and external analysis/simulation modules.

## Current state machine

Main states:

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

Side/failure states:

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

Important naming decision: use generic states like `Raw_validated` and `Reduced_validated`, not format-specific states like `H5_validated` or `CSV_validated`.

## Completed capabilities

Implemented and tested:

```text
state initialization
simulation completion backfill
raw validation
minimal openpmd_hdf5/HDF5 readability validation
reduced CSV validation
legacy reduced-only validation
external analysis command adapter
analysis stdout/stderr capture
explicit analysis rerun from Raw_delete_eligible
explicit analysis rerun from Reduced_validated
storage snapshot
raw delete eligibility
cleanup dry-run manifest
cleanup execute local/tests
```

Cleanup execute exists in code/tests but has intentionally not been run on the real `top10_particles` campaign.

## Real SUNRISE campaign: top10_particles

Campaign root:

```text
/gpfs/home/jrodriguez/warpx_runs/capillaries_bo_top10_particles
```

This is the current small real preserved-raw corpus.

Current physical layout:

```text
diags/diag1                  # field diagnostics, legacy name
diags/electron_particles     # particle diagnostic
guiding_metrics.csv          # official reduced guiding output
```

Logical naming for future campaigns:

```text
fields              -> field diagnostics
plasma_electrons    -> pre-existing / plasma electron particle diagnostics
ionized_electrons   -> electrons created by ionization
```

Do not rename existing directories in `top10_particles`.

Current raw diagnostic contract:

```text
name: fields_openpmd
kind: openpmd_hdf5
glob: diags/diag1/*.h5
min_files: 1
min_age_seconds: 600
allowed_suffixes: .h5, .hdf5
```

Current analysis contract:

```text
analysis.name    = guiding
analysis.kind    = command
analysis.adapter = command
analysis.command = bash <workflow>/examples/capillary_guiding/run_guiding_case_analysis_sunrise.sh {case_dir}
```

Official reduced output:

```text
CASE_DIR/guiding_metrics.csv
```

No alternate `post/guiding_rerun/` output is used. Reanalysis overwrites the official reduced output in place.

## Real external guiding analysis integration

The workflow has successfully invoked the real guiding module as an external command adapter on SUNRISE.

No guiding package is imported by `campaign-workflow`.

Production path:

```text
CASE_DIR/diags/diag1/*.h5
  -> guiding_analysis_module/scripts/analyze_case.py
  -> CASE_DIR/guiding_metrics.csv
  -> campaign_workflow reduced CSV validation
```

SUNRISE wrapper:

```text
examples/capillary_guiding/run_guiding_case_analysis_sunrise.sh
```

First successful real case:

```text
case_id = 0
000_from_042_f20_chan_n4e18cm3_L10mm_d500um_focm5mm_rz
```

Successful command:

```bash
python -m campaign_workflow.cli.analyze_case \
  --campaign-root . \
  --case-id 0 \
  --allow-rerun-from-raw-delete-eligible \
  --verbose
```

Observed result:

```text
analysis_name=guiding
analysis_adapter=command
case_ok=True
target_state=Reduced_validated
adapter_ok=True
OUTPUT: guiding_metrics kind=csv ok=True rows=65 required=True
destructive_operations=0
```

The resulting validation evidence included:

```text
analysis.guiding.ok = True
analysis.guiding.return_code = 0
reduced.guiding_metrics.ok = True
reduced.guiding_metrics.path = guiding_metrics.csv
reduced.guiding_metrics.row_count = 65
cleanup.cleanup_allowed = False
cleanup.delete_manifest_ready = False
cleanup.previous_cleanup_evidence_invalidated = True
```

Note: in `validation.json`, the CSV row-count field is:

```text
row_count
```

not:

```text
rows
```

The CLI may print `rows=...`, but scripts should read `row_count`.

## Analysis rerun policy

Reanalysis from final-ish states must be explicit.

Supported explicit rerun flags:

```bash
--allow-rerun-from-raw-delete-eligible
--allow-rerun-from-reduced-validated
```

Reason: preserved raw diagnostics may need to be reanalyzed after external analysis-module changes.

Allowed successful rerun transitions:

```text
Raw_delete_eligible -> Analyzing -> Reduced_validated
Reduced_validated   -> Analyzing -> Reduced_validated
Analysis_failed     -> Analyzing -> Reduced_validated
```

Reanalysis must:

```text
verify raw validation evidence
verify preserved raw files still exist
reject legacy reduced-only cases
reject cases with raw_deleted evidence
write stdout/stderr logs
overwrite/update configured reduced outputs
revalidate reduced outputs
end in Reduced_validated on success
end in Analysis_failed on failure
keep cleanup blocked
invalidate previous cleanup eligibility / delete manifest readiness
```

If a case has been reanalyzed, raw cleanup must not use old eligibility/manifests. To clean later, rerun:

```bash
python -m campaign_workflow.cli.mark_raw_delete_eligible ...
python -m campaign_workflow.cli.cleanup_raw_case --dry-run ...
```

Do not execute cleanup on `top10_particles` for now.

## Analysis module contract

A stable contract document should exist:

```text
docs/ANALYSIS_MODULE_CONTRACT.md
```

Summary of the required architecture:

```text
An analysis module must provide a case-local CLI or wrapper command.
It must accept explicit case paths.
It must write configured reduced outputs under CASE_DIR.
It must return 0 on successful execution and non-zero on failure.
It must not modify workflow state files.
It must not delete raw diagnostics.
It must not manage cleanup manifests.
It should run in its own virtual environment.
campaign-workflow invokes it through the generic command adapter and then validates the outputs.
```

This contract is mandatory for future modules:

```text
guiding analysis
particle analysis
ionized-electron analysis
future non-guiding diagnostics
```

## Legacy full campaign

Legacy campaign root:

```text
/gpfs/home/jrodriguez/warpx_runs/capillaries_bo_full_campaign
```

This campaign has no preserved raw diagnostics but has reduced CSV outputs.

It was adopted with:

```bash
python -m campaign_workflow.cli.validate_reduced_case \
  --campaign-root . \
  --legacy-reduced-only
```

Final result:

```text
states: {'Reduced_validated': 351}
guiding_metrics reduced ok: 351
legacy reduced-only: 351
raw nonempty: 0
cleanup_allowed: 0
```

Meaning:

```text
reduced CSVs are valid
raw diagnostics are not validated
raw cleanup must remain impossible
```

Never convert a legacy reduced-only campaign into raw-validated state unless real raw diagnostics exist and pass raw validation.

## Storage / cleanup status

On `top10_particles`, storage snapshot previously found:

```text
case_count=10
validated_cases=10
submitted_cases=10
case_total_GB=39.808848
raw_live_GB=36.532526
safe_cleanup_candidate_GB=36.532526
avg_raw_per_case_GB=3.653253
```

Cleanup dry-run manifests were created previously:

```text
10/10 cases had manifests/raw_delete_manifest.json
0 raw files deleted
0 directories deleted
```

After analysis reruns, previous cleanup eligibility/manifests may be invalidated. Do not execute cleanup until eligibility and dry-run manifests are regenerated deliberately.

## Current immediate tasks

1. Finish/verify the SUNRISE test for:

```bash
python -m campaign_workflow.cli.analyze_case \
  --campaign-root . \
  --case-id 0 \
  --allow-rerun-from-reduced-validated \
  --verbose
```

Expected result:

```text
Reduced_validated -> Analyzing -> Reduced_validated
adapter_ok=True
guiding_metrics.csv overwritten/revalidated
cleanup_allowed=False
```

2. Finish/verify guiding reanalysis for remaining `top10_particles` cases.

Check final campaign state:

```bash
python - <<'PY'
import json
from pathlib import Path
from collections import Counter

states = Counter()
analysis_ok = Counter()
reduced_ok = Counter()
cleanup_allowed = Counter()

for case_dir in sorted(p for p in Path(".").iterdir() if p.is_dir() and (p / "state.json").exists()):
    state = json.loads((case_dir / "state.json").read_text(encoding="utf-8-sig"))
    validation = json.loads((case_dir / "validation.json").read_text(encoding="utf-8-sig"))

    states[state.get("state")] += 1
    analysis_ok[validation.get("analysis", {}).get("guiding", {}).get("ok")] += 1
    reduced_ok[validation.get("reduced", {}).get("guiding_metrics", {}).get("ok")] += 1
    cleanup_allowed[validation.get("cleanup", {}).get("cleanup_allowed")] += 1

print("states =", dict(states))
print("analysis.guiding.ok =", dict(analysis_ok))
print("reduced.guiding_metrics.ok =", dict(reduced_ok))
print("cleanup_allowed =", dict(cleanup_allowed))
PY
```

3. Commit the external-analysis/rerun/documentation work locally.

Suggested commit:

```bash
git status
python -m unittest discover -s tests -p "test_*.py"
git add campaign_workflow tests docs examples README.md
git commit -m "Document and extend external analysis workflow"
git log --oneline -5
```

4. Decide whether to add a thin SLURM wrapper for analysis arrays.

The SLURM wrapper should only call:

```bash
python -m campaign_workflow.cli.analyze_case ...
```

No core logic belongs in SLURM.

Before writing operational SLURM wrappers, ask for existing working SUNRISE SLURM files if needed.

5. After analysis integration is stable, define the simulation lifecycle evidence contract.

## Next major phase — simulation lifecycle contract

Do not jump to a new ionization campaign yet.

Before SLURM simulation wrappers, define generic simulation evidence:

```text
Submitted
Running
Sim_done
Failed
Retryable
post/sim_submitted.json
post/sim_running.json or logs/sim_runtime.json
post/sim_done.json
post/sim_failed.json
scheduler_job_id
submit_command
run_command
environment_name
started_at
finished_at
return_code
stdout/stderr log paths
```

The simulation backend should be external-command based at first:

```text
campaign_workflow runs or wraps a configured command
the PyWarpX/simulation module remains separate
SLURM remains an execution backend, not the whole architecture
```

Do not edit PyWarpX input templates unless explicitly requested.

## Later integration campaign

Only after analysis and simulation integration are stable, prepare a small ionization integration campaign:

```text
~20 cases
two gas-mixture families
fields diagnostic
plasma_electrons diagnostic
ionized_electrons diagnostic
possibly ion diagnostics if needed
limited dumps
full workflow validation
storage snapshot
cleanup dry-run
```

Goal: integration test, not optimization yet.
